import secrets

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.interview_profile import InterviewProfile
from app.services.document_parser import extract_text_from_upload
from app.services.jd_extractor import extract_interview_profile

router = APIRouter(prefix="/jd")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB — a resume/JD

# Profiles from /jd/upload, keyed by the profile_id handed to the client. Keyed by
# profile rather than channel because a profile exists before any channel does.
_profile_store: dict[str, InterviewProfile] = {}


def get_stored_profile(profile_id: str) -> InterviewProfile | None:
    return _profile_store.get(profile_id)


# Which profile a live channel is using, bound once at POST /session/start and read
# on every turn by api/llm.py. Must stay per-channel: a single shared "last parsed
# profile" would leak one candidate's profile into another's session.
_profile_by_channel: dict[str, InterviewProfile] = {}


def bind_profile_to_channel(channel: str, profile: InterviewProfile) -> None:
    _profile_by_channel[channel] = profile


def get_profile_for_channel(channel: str) -> InterviewProfile | None:
    return _profile_by_channel.get(channel)


@router.post("/upload")
async def upload_jd(
    jd_file: UploadFile | None = File(None),
    resume_file: UploadFile | None = File(None),
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
    return {"profile_id": profile_id, "profile": profile}
