"""
Firestore persistence layer for RoundtableAI.

Manages persistent storage in Google Cloud Firestore for:
1. 'users': Candidate metadata and daily rate-limiting session epochs (doc ID = uid)
3. 'profiles': Parsed JD and resume candidate profiles (doc ID = profile_id)
4. 'sessions': Interview session lifecycle, stage tracking, transcripts, and evaluation reports (doc ID = channel_name)
"""

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any

from fastapi import HTTPException
import firebase_admin
from firebase_admin import firestore

from app.models.interview_profile import InterviewProfile
from app.services.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger("roundtable.firestore")

_db: Any | None = None
_session_owner_cache: dict[str, str] = {}

FREE_TIER_MONTHLY_LIMIT = 2
ACCOUNT_DELETION_LEASE_SECONDS = 15 * 60

def _get_current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")

def _get_resets_at() -> str:
    now = datetime.now(timezone.utc)
    year = now.year
    month = now.month + 1
    if month > 12:
        month = 1
        year += 1
    resets_at = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    return resets_at.isoformat().replace("+00:00", "Z")

def _monthly_quota_response(used: int) -> dict:
    remaining = max(0, FREE_TIER_MONTHLY_LIMIT - used)
    return {
        "limit": FREE_TIER_MONTHLY_LIMIT,
        "used": used,
        "remaining": remaining,
        "available": remaining > 0,
        "period": _get_current_period(),
        "resets_at": _get_resets_at(),
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
# Rate Limiting & User Sessions ('users' and 'sessions')
# ---------------------------------------------------------------------------

async def get_global_quota_status(uid: str) -> dict:
    """Reads the user's monthly quota from users/{uid} in Firestore."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Interview availability is temporarily unavailable.")
    
    period = _get_current_period()

    def _sync_get():
        try:
            doc_ref = db.collection("users").document(uid)
            doc = doc_ref.get()
            if not doc.exists:
                return _monthly_quota_response(0)
            data = doc.to_dict() or {}
            saved_period = data.get("quota_period", "")
            used = data.get("quota_used", 0) if saved_period == period else 0
            return _monthly_quota_response(used)
        except Exception as exc:
            logger.exception("Firestore get_global_quota_status failed for uid=%s", uid)
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


async def reserve_monthly_quota(
    uid: str,
    channel_name: str,
    profile_id: str,
    mode: str = "full",
    target_skill: str | None = None,
) -> None:
    """Atomically enforces the candidate's 2-session per month quota on users/{uid}."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Unable to safely start a session. Please try again.")

    period = _get_current_period()

    def _sync_record():
        try:
            user_ref = db.collection("users").document(uid)
            session_ref = db.collection("sessions").document(channel_name)
            transaction = db.transaction()

            @firestore.transactional
            def _record(transaction):
                user_doc = user_ref.get(transaction=transaction)
                user_data = user_doc.to_dict() or {} if user_doc.exists else {}
                
                saved_period = user_data.get("quota_period", "")
                used = user_data.get("quota_used", 0) if saved_period == period else 0

                if used >= FREE_TIER_MONTHLY_LIMIT:
                    raise HTTPException(
                        status_code=429,
                        detail="Monthly free interview limit reached."
                    )
                
                new_used = used + 1

                # Update User Quota directly on users/{uid}
                transaction.set(
                    user_ref,
                    {
                        "uid": uid,
                        "quota_period": period,
                        "quota_used": new_used,
                        "last_session_at": firestore.SERVER_TIMESTAMP,
                        "last_channel": channel_name,
                    },
                    merge=True,
                )

                session_data = {
                    "channel_name": channel_name,
                    "uid": uid,
                    "profile_id": profile_id,
                    "mode": mode,
                    "status": "active",
                    "stage": "TECHNICAL" if mode != "focused" else "FOCUSED",
                    "report_status": "not_started",
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "quota_type": "MANAGED"
                }
                if target_skill:
                    session_data["target_skill"] = target_skill

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


async def reserve_byok_session(
    uid: str,
    channel_name: str,
    profile_id: str,
    mode: str = "full",
    target_skill: str | None = None,
) -> None:
    """Records a session without consuming the global monthly quota."""
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
                session_data = {
                    "channel_name": channel_name,
                    "uid": uid,
                    "profile_id": profile_id,
                    "mode": mode,
                    "status": "active",
                    "stage": "TECHNICAL" if mode != "focused" else "FOCUSED",
                    "report_status": "not_started",
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "quota_type": "BYOK"
                }
                if target_skill:
                    session_data["target_skill"] = target_skill

                transaction.set(session_ref, session_data)
                
                transaction.set(
                    user_ref,
                    {
                        "uid": uid,
                        "last_session_at": firestore.SERVER_TIMESTAMP,
                        "last_channel": channel_name,
                    },
                    merge=True,
                )

            _record(transaction)
        except Exception as exc:
            logger.exception("Firestore BYOK session creation failed for uid=%s", uid)
            raise HTTPException(
                status_code=503,
                detail="Unable to safely start a session. Please try again.",
            ) from exc

    await asyncio.to_thread(_sync_record)
    _session_owner_cache[channel_name] = uid


async def release_quota_reservation(channel_name: str) -> None:
    """Decrements candidate's monthly quota on users/{uid} if the session start failed."""
    db = get_db()
    if db is None:
        return

    period = _get_current_period()

    def _sync_release():
        try:
            session_ref = db.collection("sessions").document(channel_name)
            transaction = db.transaction()

            @firestore.transactional
            def _release(transaction):
                sess_doc = session_ref.get(transaction=transaction)
                if not sess_doc.exists:
                    return
                sess_data = sess_doc.to_dict() or {}
                if sess_data.get("quota_type") != "MANAGED" or sess_data.get("quota_refunded"):
                    return
                
                uid = sess_data.get("uid")
                if not uid:
                    return

                user_ref = db.collection("users").document(uid)
                user_doc = user_ref.get(transaction=transaction)
                if user_doc.exists:
                    u_data = user_doc.to_dict() or {}
                    saved_period = u_data.get("quota_period", "")
                    used = u_data.get("quota_used", 0) if saved_period == period else 0
                    if used > 0:
                        transaction.update(user_ref, {"quota_used": used - 1})

                transaction.update(session_ref, {
                    "quota_refunded": True,
                    "status": "cancelled",
                    "updated_at": firestore.SERVER_TIMESTAMP
                })

            _release(transaction)
        except Exception as exc:
            logger.exception("Failed to release quota reservation for channel=%s", channel_name)

    await asyncio.to_thread(_sync_release)


mark_session_start_failed = release_quota_reservation


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
            db.collection("agora_credentials").document(uid),
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
        await release_quota_reservation(channel_name)
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
# Bring Your Own Key (BYOK) Credentials ('agora_credentials')
# ---------------------------------------------------------------------------

async def save_agora_credentials(
    uid: str,
    app_id: str,
    app_certificate: str,
    customer_id: str,
    customer_secret: str,
    groq_api_key: str = "",
    connection_status: str = "Connected"
) -> None:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Storage temporarily unavailable.")

    enc_app_id = encrypt_secret(app_id)
    enc_cert = encrypt_secret(app_certificate)
    enc_cust_id = encrypt_secret(customer_id)
    enc_cust_secret = encrypt_secret(customer_secret)
    enc_groq_key = encrypt_secret(groq_api_key)

    def _sync_save():
        try:
            doc_ref = db.collection("agora_credentials").document(uid)
            doc_ref.set({
                "uid": uid,
                "encrypted_app_id": enc_app_id,
                "encrypted_app_certificate": enc_cert,
                "encrypted_customer_id": enc_cust_id,
                "encrypted_customer_secret": enc_cust_secret,
                "encrypted_groq_api_key": enc_groq_key,
                "connection_status": connection_status,
                "created_at_timestamp": time.time(),
                "updated_at_timestamp": time.time(),
            })
        except Exception as exc:
            logger.exception("Failed to save credentials")
            raise HTTPException(status_code=503, detail="Failed to save credentials") from exc

    await asyncio.to_thread(_sync_save)

async def get_agora_credentials(uid: str) -> dict | None:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Storage temporarily unavailable.")

    def _sync_get():
        doc_ref = db.collection("agora_credentials").document(uid)
        doc = doc_ref.get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        
        try:
            return {
                "app_id": decrypt_secret(data.get("encrypted_app_id", "")),
                "app_certificate": decrypt_secret(data.get("encrypted_app_certificate", "")),
                "customer_id": decrypt_secret(data.get("encrypted_customer_id", "")),
                "customer_secret": decrypt_secret(data.get("encrypted_customer_secret", "")),
                "groq_api_key": decrypt_secret(data.get("encrypted_groq_api_key", "")),
                "connection_status": data.get("connection_status", "Connected")
            }
        except Exception as exc:
            logger.exception("Failed to decrypt credentials")
            return None

    return await asyncio.to_thread(_sync_get)

# Aliases for backward compatibility
save_byok_credentials = save_agora_credentials
get_byok_credentials = get_agora_credentials

async def get_agora_connection_status(uid: str) -> dict | None:
    """Returns safe connection status and masked credentials without exposing plaintext secrets."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Storage temporarily unavailable.")

    def _sync_get():
        doc_ref = db.collection("agora_credentials").document(uid)
        doc = doc_ref.get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        
        app_id = ""
        app_cert_masked = ""
        cust_id_masked = ""
        cust_secret_masked = ""
        groq_key_masked = ""
        try:
            raw_app_id = decrypt_secret(data.get("encrypted_app_id", ""))
            raw_cert = decrypt_secret(data.get("encrypted_app_certificate", ""))
            raw_cust_id = decrypt_secret(data.get("encrypted_customer_id", ""))
            raw_cust_secret = decrypt_secret(data.get("encrypted_customer_secret", ""))
            raw_groq = decrypt_secret(data.get("encrypted_groq_api_key", ""))
            
            app_id = raw_app_id
            if raw_cert:
                app_cert_masked = "••••••••••••"
            if raw_cust_secret:
                cust_secret_masked = "••••••••••••"
            if len(raw_cust_id) > 4:
                cust_id_masked = "••••" + raw_cust_id[-4:]
            elif raw_cust_id:
                cust_id_masked = "••••"
            if len(raw_groq) > 4:
                groq_key_masked = "••••" + raw_groq[-4:]
            elif raw_groq:
                groq_key_masked = "••••••••••••"
        except Exception:
            pass

        return {
            "connection_status": data.get("connection_status", "Not configured"),
            "app_id": app_id,
            "app_certificate": app_cert_masked,
            "customer_id": cust_id_masked,
            "customer_secret": cust_secret_masked,
            "groq_api_key": groq_key_masked,
        }

    return await asyncio.to_thread(_sync_get)

async def delete_agora_credentials(uid: str) -> None:
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Storage temporarily unavailable.")

    def _sync_delete():
        db.collection("agora_credentials").document(uid).delete()

    await asyncio.to_thread(_sync_delete)

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
