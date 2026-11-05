import secrets
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import verify_token
from app.firestore_db import get_profile_from_firestore, save_profile_to_firestore
from app.models.interview_profile import InterviewProfile
from app.services.document_parser import extract_text_from_upload
from app.services.jd_extractor import extract_interview_profile

router = APIRouter(prefix="/jd")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB — a resume/JD

# Profiles from /jd/upload, keyed by the profile_id handed to the client. Keyed by
# profile rather than channel because a profile exists before any channel does.
_profile_store: dict[str, InterviewProfile] = {}
_profile_owner_by_id: dict[str, str] = {}


def get_stored_profile(profile_id: str) -> InterviewProfile | None:
    return _profile_store.get(profile_id)


async def get_stored_profile_async(profile_id: str, uid: str) -> InterviewProfile | None:
    """Retrieves profile from memory cache, falling back to Firestore."""
    if profile_id in _profile_store:
        if _profile_owner_by_id.get(profile_id) != uid:
            return None
        return _profile_store[profile_id]
    profile = await get_profile_from_firestore(profile_id, uid)
    if profile is not None:
        _profile_store[profile_id] = profile
        _profile_owner_by_id[profile_id] = uid
    return profile


@dataclass
class ChannelSessionMeta:
    profile: InterviewProfile
    mode: str = "full"  # "full" | "focused"
    target_skill: str | None = None


# Which profile and session metadata a live channel is using, bound once at POST /session/start
# and read on every turn by api/llm.py. Must stay per-channel: a single shared "last parsed
# profile" would leak one candidate's profile into another's session.
_profile_by_channel: dict[str, InterviewProfile] = {}
_meta_by_channel: dict[str, ChannelSessionMeta] = {}


def bind_profile_to_channel(
    channel: str,
    profile: InterviewProfile,
    mode: str = "full",
    target_skill: str | None = None,
) -> None:
    _profile_by_channel[channel] = profile
    _meta_by_channel[channel] = ChannelSessionMeta(
        profile=profile,
        mode=mode,
        target_skill=target_skill,
    )


def get_profile_for_channel(channel: str) -> InterviewProfile | None:
    return _profile_by_channel.get(channel)


def get_channel_session_meta(channel: str) -> ChannelSessionMeta | None:
    return _meta_by_channel.get(channel)


def clear_user_profile_cache(uid: str, profile_ids: set[str], channel_names: set[str]) -> None:
    for profile_id, owner_uid in list(_profile_owner_by_id.items()):
        if owner_uid == uid:
            profile_ids.add(profile_id)
    for profile_id in profile_ids:
        _profile_store.pop(profile_id, None)
        _profile_owner_by_id.pop(profile_id, None)
    for channel in channel_names:
        _profile_by_channel.pop(channel, None)
        _meta_by_channel.pop(channel, None)


@router.post("/upload")
async def upload_jd(
    jd_file: UploadFile | None = File(None),
    resume_file: UploadFile | None = File(None),
    uid: str = Depends(verify_token),
):
    """Parses a JD and/or resume into a structured interview profile.

    Either file alone is enough. Returns a profile_id for POST /session/start —
    parsing happens here, once, so the app can show what the panel extracted
    before the candidate joins.
    """
    if jd_file is None and resume_file is None:
        raise HTTPException(status_code=400, detail="Upload a job description or a resume to start.")

    try:
        jd_text = None
        if jd_file is not None:
            jd_bytes = await jd_file.read()
            if len(jd_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="The job description file must be under 5MB.")
            jd_text = extract_text_from_upload(jd_file.filename or "job_description", jd_bytes)

        resume_text = None
        if resume_file is not None:
            resume_bytes = await resume_file.read()
            if len(resume_bytes) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="The resume file must be under 5MB.")
            resume_text = extract_text_from_upload(resume_file.filename or "resume", resume_bytes)

        profile = await extract_interview_profile(jd_text, resume_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to parse JD/resume: {exc}") from exc

    profile_id = secrets.token_hex(8)
    _profile_store[profile_id] = profile
    _profile_owner_by_id[profile_id] = uid
    await save_profile_to_firestore(profile_id, uid, profile)
    return {"profile_id": profile_id, "profile": profile}
