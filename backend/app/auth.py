"""
Firebase Auth token verification and per-UID rate limiting.

Every protected endpoint calls `Depends(verify_token)` to get the caller's
Firebase UID. The UID is used for rate limiting and to bind sessions to users.

Rate limiting is intentionally in-memory for v1 (no Redis dependency). It
resets on server restart, which is acceptable for the free tier. Upgrade to
a persistent store when adding paid tiers.
"""

import logging
from pathlib import Path

import firebase_admin
from fastapi import Depends, HTTPException, Header
from firebase_admin import auth as firebase_auth, credentials

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firebase Admin initialisation — called once from main.py on startup.
# ---------------------------------------------------------------------------

def init_firebase() -> None:
    """Initialise the Firebase Admin SDK from a key file or ADC.
    Safe to call multiple times — skips re-initialisation if already done."""
    if firebase_admin._apps:
        return

    credential_source = "configured Firebase credentials"
    try:
        key_path_value = settings.firebase_service_account_path.strip()
        if key_path_value:
            key_path = Path(key_path_value)
            if not key_path.is_absolute() and not key_path.exists():
                # Fallback to backend root if launched from project root
                backend_root = Path(__file__).resolve().parent.parent
                alt_path = backend_root / key_path
                if alt_path.exists():
                    key_path = alt_path
            cred = credentials.Certificate(str(key_path))
            credential_source = str(key_path)
        else:
            cred = credentials.ApplicationDefault()
            credential_source = "Application Default Credentials"
        firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialised successfully using: %s", credential_source)
    except Exception as exc:
        logger.error("Failed to initialise Firebase Admin SDK using %s: %s", credential_source, exc)
        raise


# ---------------------------------------------------------------------------
# Token verification dependencies
# ---------------------------------------------------------------------------

async def verify_token(authorization: str = Header(...)) -> str:
    """FastAPI dependency. Extracts and verifies the Firebase ID token from the
    Authorization: Bearer <token> header. Returns the verified Firebase UID.

    Raises 401 if the token is missing, malformed, expired, or revoked.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be 'Bearer <token>'.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = firebase_auth.verify_id_token(token, check_revoked=True)
        return decoded["uid"]
    except firebase_auth.RevokedIdTokenError:
        raise HTTPException(status_code=401, detail="Session has been revoked. Please sign in again.")
    except firebase_auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except firebase_auth.InvalidIdTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    except Exception as exc:
        logger.error("Token verification error: %s", exc)
        raise HTTPException(status_code=401, detail="Could not verify credentials.")


async def verify_user_claims(authorization: str = Header(...)) -> dict:
    """Extracts and verifies the Firebase ID token and returns the full claims dict
    including 'uid', 'email', and 'name'.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header must be 'Bearer <token>'.")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        decoded = firebase_auth.verify_id_token(token, check_revoked=True)
        return {
            "uid": decoded["uid"],
            "email": decoded.get("email"),
            "name": decoded.get("name"),
        }
    except firebase_auth.RevokedIdTokenError:
        raise HTTPException(status_code=401, detail="Session has been revoked. Please sign in again.")
    except firebase_auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except firebase_auth.InvalidIdTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    except Exception as exc:
        logger.error("Token verification error: %s", exc)
        raise HTTPException(status_code=401, detail="Could not verify credentials.")

