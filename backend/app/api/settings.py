import base64
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import httpx

from app.auth import verify_token
from app.firestore_db import (
    save_agora_credentials,
    get_agora_credentials,
    delete_agora_credentials
)

router = APIRouter(prefix="/settings/agora")

class BYOKCredentialsRequest(BaseModel):
    app_id: str = ""
    app_certificate: str = ""
    customer_id: str = ""
    customer_secret: str = ""
    groq_api_key: str = ""

# Alias for backward compatibility
AgoraCredentialsRequest = BYOKCredentialsRequest

class ConnectionStatusResponse(BaseModel):
    connection_status: str
    app_id: str = ""
    app_certificate: str = ""
    customer_id: str = ""
    customer_secret: str = ""
    groq_api_key: str = ""

@router.get("/status", response_model=ConnectionStatusResponse)
async def get_status(uid: str = Depends(verify_token)):
    creds = await get_agora_credentials(uid)
    if not creds:
        return ConnectionStatusResponse(connection_status="Not configured")

    app_id = creds.get("app_id", "").strip()
    app_cert = creds.get("app_certificate", "").strip()
    cust_id = creds.get("customer_id", "").strip()
    cust_secret = creds.get("customer_secret", "").strip()
    groq_key = creds.get("groq_api_key", "").strip()

    has_agora = bool(app_id and app_cert and cust_id and cust_secret)
    has_groq = bool(groq_key)

    raw_status = creds.get("connection_status", "")
    if raw_status == "Invalid credentials":
        final_status = "Invalid credentials"
    elif has_agora:
        final_status = "Connected"
    elif has_groq:
        final_status = "Incomplete (Agora keys missing)"
    else:
        final_status = "Not configured"

    return ConnectionStatusResponse(
        connection_status=final_status,
        app_id=app_id,
        app_certificate=app_cert,
        customer_id=cust_id,
        customer_secret=cust_secret,
        groq_api_key=groq_key
    )

@router.post("/credentials")
@router.put("/credentials")
async def update_credentials(request: BYOKCredentialsRequest, uid: str = Depends(verify_token)):
    # Check if user sent masked placeholders, and retain existing stored credentials if so
    if (
        request.app_certificate == "••••••••••••"
        or request.customer_secret == "••••••••••••"
        or request.customer_id.startswith("••••")
        or request.groq_api_key.startswith("••••")
        or request.groq_api_key == "••••••••••••"
    ):
        existing = await get_agora_credentials(uid)
        if existing:
            if request.app_certificate == "••••••••••••":
                request.app_certificate = existing.get("app_certificate", "")
            if request.customer_secret == "••••••••••••":
                request.customer_secret = existing.get("customer_secret", "")
            if request.customer_id.startswith("••••") or not request.customer_id:
                request.customer_id = existing.get("customer_id", "")
            if not request.app_id:
                request.app_id = existing.get("app_id", "")
            if request.groq_api_key.startswith("••••") or request.groq_api_key == "••••••••••••":
                request.groq_api_key = existing.get("groq_api_key", "")

    app_id = request.app_id.strip()
    app_cert = request.app_certificate.strip()
    cust_id = request.customer_id.strip()
    cust_secret = request.customer_secret.strip()
    groq_key = request.groq_api_key.strip()

    # Check if all required Agora keys are provided
    has_agora = bool(app_id and app_cert and cust_id and cust_secret)

    # Validate Agora credentials basic auth if provided
    if cust_id and cust_secret:
        validate_url = "https://api.agora.io/dev/v1/projects"
        raw = f"{cust_id}:{cust_secret}".encode()
        auth_header = f"Basic {base64.b64encode(raw).decode()}"
        
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(validate_url, headers={"Authorization": auth_header})
                if response.status_code in (401, 403):
                    await save_agora_credentials(
                        uid, app_id, app_cert, cust_id, cust_secret, groq_key, "Invalid credentials"
                    )
                    raise HTTPException(status_code=400, detail="Invalid Agora credentials")
        except httpx.RequestError:
            pass

    # Validate Groq API key if provided
    if groq_key:
        groq_url = "https://api.groq.com/openai/v1/models"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(groq_url, headers={"Authorization": f"Bearer {groq_key}"})
                if response.status_code in (401, 403):
                    await save_agora_credentials(
                        uid, app_id, app_cert, cust_id, cust_secret, groq_key, "Invalid credentials"
                    )
                    raise HTTPException(status_code=400, detail="Invalid Groq API Key")
        except httpx.RequestError:
            pass

    conn_status = "Connected" if has_agora else ("Incomplete (Agora keys missing)" if groq_key else "Not configured")

    # Save
    await save_agora_credentials(
        uid,
        app_id=app_id,
        app_certificate=app_cert,
        customer_id=cust_id,
        customer_secret=cust_secret,
        groq_api_key=groq_key,
        connection_status=conn_status
    )
    
    return {"status": "ok"}

@router.delete("/credentials")
async def remove_credentials(uid: str = Depends(verify_token)):
    await delete_agora_credentials(uid)
    return {"status": "ok"}
