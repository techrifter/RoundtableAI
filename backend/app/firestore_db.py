"""
Firestore persistence layer for RoundtableAI.

Manages persistent storage in Google Cloud Firestore for:
1. 'waitlist': Early access Pro waitlist signups (doc ID = uid)
2. 'users': Candidate metadata and daily rate-limiting session epochs (doc ID = uid)
3. 'profiles': Parsed JD and resume candidate profiles (doc ID = profile_id)
4. 'sessions': Interview session lifecycle, stage tracking, transcripts, and evaluation reports (doc ID = channel_name)
"""

import asyncio
import logging
import time
from typing import Any

from fastapi import HTTPException
import firebase_admin
from firebase_admin import firestore
from google.api_core.exceptions import AlreadyExists

from app.models.interview_profile import InterviewProfile

logger = logging.getLogger("roundtable.firestore")

_db: Any | None = None
_session_owner_cache: dict[str, str] = {}

FREE_TIER_DAILY_LIMIT = 2
_WINDOW_SECONDS = 86_400  # 24 hours
ACCOUNT_DELETION_LEASE_SECONDS = 15 * 60


def _recent_session_epochs(session_epochs: Any, cutoff: float) -> list[float]:
    if not isinstance(session_epochs, list):
        return []
    return [
        timestamp
        for timestamp in session_epochs
        if isinstance(timestamp, (int, float)) and timestamp > cutoff
    ]


def _user_limits(used: int, has_joined_waitlist: bool) -> dict:
    remaining = max(0, FREE_TIER_DAILY_LIMIT - used)
    return {
        "daily_limit": FREE_TIER_DAILY_LIMIT,
        "used_today": used,
        "remaining": remaining,
        "can_start": remaining > 0,
        "has_joined_waitlist": has_joined_waitlist,
    }


def get_db() -> Any | None:
    """Returns the Firestore client, or None when persistent storage is unavailable."""
    global _db
    if _db is not None:
        return _db
    try:
        if not firebase_admin._apps:
            from app.auth import init_firebase
            init_firebase()
        _db = firestore.client()
        return _db
    except Exception as exc:
        logger.warning("Firestore client is unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Waitlist Collection ('waitlist')
# ---------------------------------------------------------------------------

async def save_waitlist_user(uid: str, email: str, name: str, source: str = "android_pro_dialog") -> dict:
    """Creates a waitlist record in Firestore, idempotently by Firebase UID."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Waitlist storage is temporarily unavailable.")

    def _sync_save():
        doc_ref = db.collection("waitlist").document(uid)
        try:
            doc_ref.create(
                {
                    "uid": uid,
                    "email": email,
                    "name": name,
                    "source": source,
                    "joined_at": firestore.SERVER_TIMESTAMP,
                }
            )
            logger.info("Saved waitlist candidate to Firestore: uid=%s", uid)
            return {"status": "ok", "message": "Added to waitlist", "already_joined": False}
        except AlreadyExists:
            return {"status": "ok", "message": "Already on waitlist", "already_joined": True}

    try:
        return await asyncio.to_thread(_sync_save)
    except Exception as exc:
        logger.exception("Firestore save_waitlist_user failed for uid=%s", uid)
        raise HTTPException(status_code=503, detail="Waitlist storage is temporarily unavailable.") from exc


# ---------------------------------------------------------------------------
# Rate Limiting & User Sessions ('users' and 'sessions')
# ---------------------------------------------------------------------------

async def get_user_rate_limits(uid: str) -> dict:
    """Reads Firestore session history and calculates the rolling 24-hour quota."""
    now = time.time()
    cutoff = now - _WINDOW_SECONDS
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Interview availability is temporarily unavailable.")

    def _sync_get():
        try:
            has_joined_waitlist = db.collection("waitlist").document(uid).get().exists
            doc_ref = db.collection("users").document(uid)
            doc = doc_ref.get()
            if not doc.exists:
                return _user_limits(0, has_joined_waitlist)
            data = doc.to_dict() or {}
            recent = _recent_session_epochs(data.get("recent_sessions", []), cutoff)
            return _user_limits(len(recent), has_joined_waitlist)
        except Exception as exc:
            logger.exception("Firestore get_user_rate_limits failed for uid=%s", uid)
            raise HTTPException(
                status_code=503,
                detail="Interview availability is temporarily unavailable.",
            ) from exc

    try:
        return await asyncio.to_thread(_sync_get)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not read interview limits for uid=%s", uid)
        raise HTTPException(
            status_code=503,
            detail="Interview availability is temporarily unavailable.",
        ) from exc


async def check_and_record_session(
    uid: str,
    channel_name: str,
    profile_id: str,
    mode: str = "full",
    target_skill: str | None = None,
) -> None:
    """Atomically enforces the free-session limit and persists the session."""
    now = time.time()
    cutoff = now - _WINDOW_SECONDS
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Unable to safely start a session. Please try again.")

    def _sync_record():
        try:
            user_ref = db.collection("users").document(uid)
            session_ref = db.collection("sessions").document(channel_name)
            transaction = db.transaction()

            @firestore.transactional
            def _record(transaction):
                user_doc = user_ref.get(transaction=transaction)
                data = user_doc.to_dict() or {} if user_doc.exists else {}
                recent = _recent_session_epochs(data.get("recent_sessions", []), cutoff)

                # Only refund a prior attempt that failed before the agent started.
                # Live sessions must not open the grace window to additional starts.
                is_retryable_failure = False
                last_channel = data.get("last_channel")
                if last_channel and recent and now - recent[-1] < 900:
                    last_sess_ref = db.collection("sessions").document(last_channel)
                    last_sess_doc = last_sess_ref.get(transaction=transaction)
                    if last_sess_doc.exists:
                        sess_data = last_sess_doc.to_dict() or {}
                        is_retryable_failure = sess_data.get("status") == "failed"

                if len(recent) >= FREE_TIER_DAILY_LIMIT and not is_retryable_failure:
                    raise HTTPException(
                        status_code=429,
                        detail=f"You've used your {FREE_TIER_DAILY_LIMIT} free interviews in the last 24 hours. Upgrade to Roundtable Pro for unlimited mock interviews, or try again later.",
                    )

                if is_retryable_failure and recent:
                    recent[-1] = now
                else:
                    recent.append(now)

                session_data = {
                    "channel_name": channel_name,
                    "uid": uid,
                    "profile_id": profile_id,
                    "mode": mode,
                    "status": "active",
                    "stage": "TECHNICAL" if mode != "focused" else "FOCUSED",
                    "report_status": "not_started",
                    "created_at": firestore.SERVER_TIMESTAMP,
                }
                if target_skill:
                    session_data["target_skill"] = target_skill

                transaction.set(
                    user_ref,
                    {
                        "uid": uid,
                        "last_session_at": firestore.SERVER_TIMESTAMP,
                        "last_channel": channel_name,
                        "recent_sessions": recent,
                    },
                    merge=True,
                )
                transaction.set(session_ref, session_data)

            _record(transaction)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Firestore session creation failed for uid=%s", uid)
            raise HTTPException(
                status_code=503,
                detail="Unable to safely start a session. Please try again.",
            ) from exc

    await asyncio.to_thread(_sync_record)
    _session_owner_cache[channel_name] = uid


async def get_user_sessions_for_deletion(uid: str) -> list[dict[str, str]]:
    """Returns the user's stored sessions and their Agora agent IDs for cleanup."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account data is temporarily unavailable.")

    def _sync_get() -> list[dict[str, str]]:
        documents = db.collection("sessions").where("uid", "==", uid).stream()
        sessions = []
        for doc in documents:
            data = doc.to_dict() or {}
            agent_id = data.get("agent_id")
            sessions.append({
                "channel_name": doc.id,
                "agent_id": agent_id if isinstance(agent_id, str) else "",
            })
        return sessions

    try:
        return await asyncio.to_thread(_sync_get)
    except Exception as exc:
        logger.exception("Could not read sessions for account deletion")
        raise HTTPException(status_code=503, detail="Account data is temporarily unavailable.") from exc


async def delete_user_data(uid: str) -> tuple[list[str], list[str]]:
    """Deletes account-owned Firestore records and returns deleted profile/session IDs."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account data is temporarily unavailable.")

    def _sync_delete() -> tuple[list[str], list[str]]:
        profile_docs = list(db.collection("profiles").where("uid", "==", uid).stream())
        session_docs = list(db.collection("sessions").where("uid", "==", uid).stream())
        legal_hold_ref = db.collection("legal_holds").document(uid)
        hold_doc = legal_hold_ref.get()
        retained_profiles: set[str] = set()
        retained_sessions: set[str] = set()
        remove_expired_hold = False
        if hold_doc.exists:
            hold = hold_doc.to_dict() or {}
            reason = hold.get("reason")
            expires_at = hold.get("expires_at_epoch")
            profile_ids_to_retain = hold.get("profile_ids", [])
            session_ids_to_retain = hold.get("session_ids", [])
            if (
                not isinstance(reason, str)
                or not reason.strip()
                or not isinstance(expires_at, (int, float))
                or not isinstance(profile_ids_to_retain, list)
                or not all(isinstance(item, str) for item in profile_ids_to_retain)
                or not isinstance(session_ids_to_retain, list)
                or not all(isinstance(item, str) for item in session_ids_to_retain)
            ):
                raise ValueError("Invalid legal hold record.")
            if expires_at > time.time():
                retained_profiles = set(profile_ids_to_retain)
                retained_sessions = set(session_ids_to_retain)
            else:
                remove_expired_hold = True

        profiles_to_delete = [doc for doc in profile_docs if doc.id not in retained_profiles]
        sessions_to_delete = [doc for doc in session_docs if doc.id not in retained_sessions]
        profile_ids = [doc.id for doc in profiles_to_delete]
        channel_names = [doc.id for doc in sessions_to_delete]
        references = [
            doc.reference for doc in profiles_to_delete + sessions_to_delete
        ] + [
            db.collection("users").document(uid),
            db.collection("waitlist").document(uid),
        ]
        if remove_expired_hold:
            references.append(legal_hold_ref)

        batch = db.batch()
        pending = 0
        for reference in references:
            batch.delete(reference)
            pending += 1
            if pending == 400:
                batch.commit()
                batch = db.batch()
                pending = 0
        if pending:
            batch.commit()
        return profile_ids, channel_names

    try:
        profile_ids, channel_names = await asyncio.to_thread(_sync_delete)
    except Exception as exc:
        logger.exception("Firestore account data deletion failed")
        raise HTTPException(status_code=503, detail="Could not delete all account data. Please retry.") from exc

    for channel_name in channel_names:
        _session_owner_cache.pop(channel_name, None)
    return profile_ids, channel_names


async def enqueue_account_deletion(uid: str) -> bool:
    """Persist an account deletion request for the background worker."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account deletion is temporarily unavailable.")

    requested_at = time.time()

    def _sync_enqueue() -> bool:
        reference = db.collection("account_deletion_requests").document(uid)
        transaction = db.transaction()

        @firestore.transactional
        def _enqueue(transaction) -> bool:
            snapshot = reference.get(transaction=transaction)
            if snapshot.exists:
                status_value = (snapshot.to_dict() or {}).get("status")
                if status_value in {"pending", "processing"}:
                    return False
            transaction.set(reference, {
                "uid": uid,
                "status": "pending",
                "attempts": 0,
                "requested_at_epoch": requested_at,
                "next_attempt_at_epoch": requested_at,
                "lease_until_epoch": 0,
                "delete_by_epoch": requested_at + 48 * 60 * 60,
            })
            return True

        return _enqueue(transaction)

    try:
        return await asyncio.to_thread(_sync_enqueue)
    except Exception as exc:
        logger.exception("Could not enqueue account deletion for uid=%s", uid)
        raise HTTPException(status_code=503, detail="Account deletion is temporarily unavailable.") from exc


async def get_due_account_deletions(now: float | None = None) -> list[str]:
    """Find queued requests that are ready or have an expired worker lease."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.")
    current_time = now if now is not None else time.time()

    def _sync_get() -> list[str]:
        due_uids = []
        for document in db.collection("account_deletion_requests").stream():
            data = document.to_dict() or {}
            status_value = data.get("status")
            if (
                status_value == "pending"
                and data.get("next_attempt_at_epoch", 0) <= current_time
            ) or (
                status_value == "processing"
                and data.get("lease_until_epoch", 0) <= current_time
            ):
                due_uids.append(document.id)
        return due_uids

    try:
        return await asyncio.to_thread(_sync_get)
    except Exception as exc:
        logger.exception("Could not read account deletion queue")
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.") from exc


async def claim_account_deletion(uid: str, now: float | None = None) -> int | None:
    """Atomically lease a due deletion request; return its updated attempt count."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.")
    current_time = now if now is not None else time.time()

    def _sync_claim() -> int | None:
        reference = db.collection("account_deletion_requests").document(uid)
        transaction = db.transaction()

        @firestore.transactional
        def _claim(transaction) -> int | None:
            snapshot = reference.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict() or {}
            status_value = data.get("status")
            due = (
                status_value == "pending"
                and data.get("next_attempt_at_epoch", 0) <= current_time
            ) or (
                status_value == "processing"
                and data.get("lease_until_epoch", 0) <= current_time
            )
            if not due:
                return None
            attempts = int(data.get("attempts", 0)) + 1
            transaction.update(reference, {
                "status": "processing",
                "attempts": attempts,
                "lease_until_epoch": current_time + ACCOUNT_DELETION_LEASE_SECONDS,
            })
            return attempts

        return _claim(transaction)

    try:
        return await asyncio.to_thread(_sync_claim)
    except Exception as exc:
        logger.exception("Could not claim account deletion for uid=%s", uid)
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.") from exc


async def retry_account_deletion(uid: str, attempts: int, error_type: str) -> None:
    """Release a failed request back to the durable queue with bounded backoff."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.")
    now = time.time()
    delay = min(60 * (2 ** min(max(attempts - 1, 0), 4)), 900)

    def _sync_retry() -> None:
        reference = db.collection("account_deletion_requests").document(uid)
        snapshot = reference.get()
        data = snapshot.to_dict() or {} if snapshot.exists else {}
        delete_by = data.get("delete_by_epoch")
        next_attempt = now + delay
        if isinstance(delete_by, (int, float)) and delete_by > now:
            next_attempt = min(next_attempt, delete_by)
        elif isinstance(delete_by, (int, float)):
            logger.critical("Account deletion exceeded its 48-hour target for uid=%s", uid)
        reference.update({
            "status": "pending",
            "next_attempt_at_epoch": next_attempt,
            "lease_until_epoch": 0,
            "last_error_type": error_type,
            "last_attempt_at_epoch": now,
        })

    try:
        await asyncio.to_thread(_sync_retry)
    except Exception as exc:
        logger.exception("Could not reschedule account deletion for uid=%s", uid)
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.") from exc


async def complete_account_deletion(uid: str) -> None:
    """Remove the queue record after deletion has succeeded."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.")

    def _sync_complete() -> None:
        db.collection("account_deletion_requests").document(uid).delete()

    try:
        await asyncio.to_thread(_sync_complete)
    except Exception as exc:
        logger.exception("Could not finalize account deletion request for uid=%s", uid)
        raise HTTPException(status_code=503, detail="Account deletion queue is unavailable.") from exc


async def update_session_agent_id(channel_name: str, agent_id: str) -> None:
    """Persists the Agora agent ID so account deletion can stop an active agent."""
    db = get_db()
    if db is None:
        return

    def _sync_update() -> None:
        db.collection("sessions").document(channel_name).set({"agent_id": agent_id}, merge=True)

    try:
        await asyncio.to_thread(_sync_update)
    except Exception:
        logger.exception("Could not persist agent ID for channel=%s", channel_name)


async def mark_session_start_failed(channel_name: str) -> None:
    """Marks a failed agent start so the candidate can retry within the grace window."""
    db = get_db()
    if db is None:
        return

    def _sync_mark() -> None:
        db.collection("sessions").document(channel_name).update(
            {"status": "failed", "updated_at": firestore.SERVER_TIMESTAMP}
        )

    try:
        await asyncio.to_thread(_sync_mark)
    except Exception:
        logger.exception("Could not mark session start as failed for channel=%s", channel_name)


async def require_session_owner(channel_name: str, uid: str) -> None:
    """Reject access unless the authenticated Firebase UID owns this session."""
    owner = _session_owner_cache.get(channel_name)
    if owner is not None:
        if owner != uid:
            raise HTTPException(status_code=404, detail="Session not found.")
        return

    db = get_db()
    if db is not None:
        def _sync_get_owner() -> str | None:
            doc = db.collection("sessions").document(channel_name).get()
            if not doc.exists:
                return None
            return (doc.to_dict() or {}).get("uid")

        try:
            owner = await asyncio.to_thread(_sync_get_owner) or owner
        except Exception as exc:
            logger.exception("Could not verify session ownership for channel=%s", channel_name)
            raise HTTPException(status_code=503, detail="Unable to verify session access.") from exc

    if owner != uid:
        raise HTTPException(status_code=404, detail="Session not found.")


# ---------------------------------------------------------------------------
# Profiles Collection ('profiles')
# ---------------------------------------------------------------------------

async def save_profile_to_firestore(profile_id: str, uid: str, profile: InterviewProfile) -> None:
    """Stores parsed interview profile in Firestore."""
    db = get_db()
    if db is None:
        return

    def _sync_save():
        try:
            doc_ref = db.collection("profiles").document(profile_id)
            doc_ref.set(
                {
                    "profile_id": profile_id,
                    "uid": uid,
                    "role": profile.role,
                    "skills": profile.skills,
                    "system_design_topics": profile.system_design_topics,
                    "responsibilities": profile.responsibilities,
                    "candidate_highlights": profile.candidate_highlights,
                    "created_at": firestore.SERVER_TIMESTAMP,
                }
            )
            logger.info("Saved profile to Firestore: profile_id=%s uid=%s", profile_id, uid)
        except Exception as exc:
            logger.warning("Firestore save profile warning: %s", exc)

    try:
        await asyncio.to_thread(_sync_save)
    except Exception as exc:
        logger.warning("Error saving profile to Firestore: %s", exc)


async def get_profile_from_firestore(profile_id: str, uid: str) -> InterviewProfile | None:
    """Retrieves a profile only when it belongs to the authenticated user."""
    db = get_db()
    if db is None:
        return None

    def _sync_get():
        try:
            doc_ref = db.collection("profiles").document(profile_id)
            doc = doc_ref.get()
            if not doc.exists:
                return None
            data = doc.to_dict() or {}
            if data.get("uid") != uid:
                return None
            return InterviewProfile(
                role=data.get("role", ""),
                skills=data.get("skills", []),
                system_design_topics=data.get("system_design_topics", []),
                responsibilities=data.get("responsibilities", []),
                candidate_highlights=data.get("candidate_highlights", []),
            )
        except Exception as exc:
            logger.warning("Firestore get profile warning: %s", exc)
            return None

    try:
        return await asyncio.to_thread(_sync_get)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sessions & Reports Collection ('sessions')
# ---------------------------------------------------------------------------

async def update_session_stage_in_firestore(channel_name: str, stage: str) -> None:
    """Updates the active interview stage in Firestore."""
    db = get_db()
    if db is None:
        return

    def _sync_update():
        try:
            doc_ref = db.collection("sessions").document(channel_name)
            doc_ref.update({"stage": stage, "updated_at": firestore.SERVER_TIMESTAMP})
        except Exception as exc:
            logger.warning("Firestore stage update warning: %s", exc)

    try:
        await asyncio.to_thread(_sync_update)
    except Exception:
        pass


async def update_session_report(channel_name: str, status: str, report: dict | None = None) -> None:
    """Persists the interview report and status to the Firestore session document."""
    db = get_db()
    if db is None:
        return

    def _sync_update():
        try:
            doc_ref = db.collection("sessions").document(channel_name)
            update_data: dict[str, Any] = {
                "report_status": status,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
            if report is not None:
                update_data["report"] = report
                update_data["status"] = "completed"
                update_data["ended_at"] = firestore.SERVER_TIMESTAMP
            elif status in ("failed", "not_applicable"):
                update_data["status"] = "ended"
                update_data["ended_at"] = firestore.SERVER_TIMESTAMP

            doc_ref.update(update_data)
            logger.info("Saved report to Firestore session: channel=%s status=%s", channel_name, status)
        except Exception as exc:
            logger.warning("Firestore report update warning: %s", exc)

    try:
        await asyncio.to_thread(_sync_update)
    except Exception:
        pass


async def get_session_report_from_firestore(channel_name: str) -> dict | None:
    """Retrieves session report and status from Firestore."""
    db = get_db()
    if db is None:
        return None

    def _sync_get():
        try:
            doc_ref = db.collection("sessions").document(channel_name)
            doc = doc_ref.get()
            if not doc.exists:
                return None
            data = doc.to_dict() or {}
            return {
                "status": data.get("report_status", "not_started"),
                "report": data.get("report"),
            }
        except Exception as exc:
            logger.warning("Firestore report fetch warning: %s", exc)
            return None

    try:
        return await asyncio.to_thread(_sync_get)
    except Exception:
        return None
