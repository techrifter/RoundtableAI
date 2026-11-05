import asyncio
import logging

from firebase_admin import auth as firebase_auth

from app.api import jd, llm
from app.evaluation import report
from app.firestore_db import (
    _session_owner_cache,
    claim_account_deletion,
    complete_account_deletion,
    delete_user_data,
    get_due_account_deletions,
    get_user_sessions_for_deletion,
    retry_account_deletion,
)
from app.services import agora_agent

logger = logging.getLogger("roundtable.account_deletion")
WORKER_INTERVAL_SECONDS = 60 * 60


async def _delete_account_data(uid: str) -> None:
    try:
        await asyncio.to_thread(firebase_auth.update_user, uid, disabled=True)
    except firebase_auth.UserNotFoundError:
        pass

    stored_sessions = await get_user_sessions_for_deletion(uid)
    channels_by_name = {
        session["channel_name"]: session.get("agent_id", "")
        for session in stored_sessions
    }
    for channel_name, owner_uid in list(_session_owner_cache.items()):
        if owner_uid == uid:
            channels_by_name.setdefault(channel_name, "")

    for channel_name, stored_agent_id in channels_by_name.items():
        agent_id = stored_agent_id or agora_agent.get_agent_id(channel_name)
        if agent_id:
            await agora_agent.stop_agent(agent_id)

    profile_ids, deleted_channels = await delete_user_data(uid)
    all_channels = set(channels_by_name) | set(deleted_channels)
    jd.clear_user_profile_cache(uid, set(profile_ids), all_channels)
    for channel_name in all_channels:
        _session_owner_cache.pop(channel_name, None)
        llm.clear_channel_state(channel_name)
        report.clear_channel_state(channel_name)
        agora_agent.forget_agent_id(channel_name)

    try:
        await asyncio.to_thread(firebase_auth.delete_user, uid)
    except firebase_auth.UserNotFoundError:
        pass


async def process_due_account_deletions() -> None:
    for uid in await get_due_account_deletions():
        attempts = await claim_account_deletion(uid)
        if attempts is None:
            continue
        try:
            await _delete_account_data(uid)
            await complete_account_deletion(uid)
            logger.info("Completed queued account deletion for uid=%s", uid)
        except Exception as exc:
            logger.exception("Queued account deletion failed for uid=%s", uid)
            try:
                await retry_account_deletion(uid, attempts, type(exc).__name__)
            except Exception:
                logger.exception("Could not reschedule failed account deletion for uid=%s", uid)


async def account_deletion_worker() -> None:
    """Poll the durable deletion queue while the API process is running."""
    while True:
        try:
            await process_due_account_deletions()
        except Exception:
            logger.exception("Account deletion worker iteration failed")
        await asyncio.sleep(WORKER_INTERVAL_SECONDS)
