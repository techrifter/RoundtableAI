import logging

from fastapi import APIRouter, Depends, status

from app.auth import verify_user_claims
from app.firestore_db import enqueue_account_deletion

router = APIRouter(prefix="/user")
logger = logging.getLogger("roundtable.account")


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
