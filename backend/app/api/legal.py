from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@router.get("/privacy", include_in_schema=False)
async def privacy_policy():
    return FileResponse(_STATIC_DIR / "privacy-policy.html", media_type="text/html")


@router.get("/account-deletion", include_in_schema=False)
async def account_deletion_page():
    return FileResponse(_STATIC_DIR / "account-deletion.html", media_type="text/html")
