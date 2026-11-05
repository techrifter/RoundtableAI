import logging

from fastapi import APIRouter, Depends, status

from app.auth import verify_user_claims
from app.firestore_db import enqueue_account_deletion, save_waitlist_user

router = APIRouter(prefix="/user")
logger = logging.getLogger("roundtable.account")


@router.post("/waitlist")
async def join_waitlist(user: dict = Depends(verify_user_claims)):
    """Saves the candidate's UID and email to the Pro early-access waitlist in Firestore."""
    uid = user["uid"]
    email = user.get("email") or "no-email-provided"
    name = user.get("name") or ""

    return await save_waitlist_user(uid=uid, email=email, name=name, source="android_pro_dialog")


@router.delete("/account", status_code=status.HTTP_202_ACCEPTED)
async def delete_account(user: dict = Depends(verify_user_claims)):
    """Queue account deletion and return without waiting for provider cleanup."""
    uid = user["uid"]
    created = await enqueue_account_deletion(uid)
    logger.info("Account deletion request accepted for uid=%s (new=%s)", uid, created)
    return {
        "status": "accepted",
        "message": "Your deletion request is queued. Your account and associated data will be deleted within 48 hours.",
        "already_queued": not created,
    }
