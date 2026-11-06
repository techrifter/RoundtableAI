import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents.system_design import build_system_design_persona_prompt
from app.agents.technical import build_technical_persona_prompt
from app.api.jd import (
    bind_profile_to_channel,
    get_channel_session_meta,
    get_profile_for_channel,
    get_stored_profile_async,
)
from app.api.llm import get_current_stage, get_last_messages
from app.auth import verify_token
from app.config import settings
from app.evaluation.report import (
    get_report_status,
    get_report_status_async,
    has_report_started,
    mark_not_applicable,
    start_report_generation,
)
from app.firestore_db import (
    check_and_record_session,
    get_user_rate_limits,
    mark_session_start_failed,
    require_session_owner,
    update_session_agent_id,
    update_session_stage_in_firestore,
)
from app.orchestration.orchestrator import InterviewStage, resolve_focused_stage
from app.services.agora_agent import AGENT_UID, remember_agent_id, start_agent
from app.services.agora_token import build_agent_rtc_rtm_token, build_rtc_token, build_rtm_token

router = APIRouter(prefix="/session")

CANDIDATE_UID = 1001


class StartSessionRequest(BaseModel):
    profile_id: str
    mode: str = "full"  # "full" | "focused"
    target_skill: str | None = None


class StartSessionResponse(BaseModel):
    app_id: str
    channel_name: str
    candidate_uid: int
    candidate_token: str
    rtm_token: str
    agent_id: str


@router.get("/limits")
async def get_session_limits(uid: str = Depends(verify_token)):
    """Returns the user's free tier limits and remaining interviews in the rolling 24-hour window."""
    return await get_user_rate_limits(uid)


@router.post("/start", response_model=StartSessionResponse)
async def start_session(request: StartSessionRequest, uid: str = Depends(verify_token)):
    """Create a channel, generate tokens for the candidate and the agent, and start
    the Agora Conversational AI agent wired to our custom-LLM endpoint.
    Supports two modes:
    - 'full': 30-40 min multi-stage simulation with all 3 personas.
    - 'focused': 5-15 min targeted practice drill on a single chosen skill.
    """
    profile = await get_stored_profile_async(request.profile_id, uid)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown profile_id — call POST /jd/upload first.")

    channel_name = f"roundtable-{secrets.token_hex(16)}"
    await check_and_record_session(
        uid,
        channel_name,
        request.profile_id,
        mode=request.mode,
        target_skill=request.target_skill,
    )

    candidate_token = build_rtc_token(channel_name, CANDIDATE_UID)
    agent_token = build_agent_rtc_rtm_token(channel_name, AGENT_UID, str(AGENT_UID))
    rtm_token = build_rtm_token(str(CANDIDATE_UID))

    bind_profile_to_channel(
        channel_name,
        profile,
        mode=request.mode,
        target_skill=request.target_skill,
    )

    if request.mode == "focused":
        stage = resolve_focused_stage(request.target_skill, profile)
        if stage == InterviewStage.SYSTEM_DESIGN:
            system_prompt = build_system_design_persona_prompt(profile, focus_topic=request.target_skill)
        else:
            system_prompt = build_technical_persona_prompt(profile, focus_topic=request.target_skill)
    else:
        system_prompt = build_technical_persona_prompt(profile)

    try:
        agent_response = await start_agent(
            channel_name=channel_name,
            agent_token=agent_token,
            candidate_uid=CANDIDATE_UID,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        await mark_session_start_failed(channel_name)
        raise HTTPException(status_code=502, detail=f"Failed to start Agora agent: {exc}") from exc

    agent_id = agent_response.get("agent_id", "")
    remember_agent_id(channel_name, agent_id)
    await update_session_agent_id(channel_name, agent_id)

    return StartSessionResponse(
        app_id=settings.agora_app_id,
        channel_name=channel_name,
        candidate_uid=CANDIDATE_UID,
        candidate_token=candidate_token,
        rtm_token=rtm_token,
        agent_id=agent_id,
    )


@router.get("/{channel_name}/stage")
async def get_session_stage(channel_name: str, uid: str = Depends(verify_token)):
    """Polled by the client during a live call to show which persona is
    currently interviewing — the one piece of orchestrator state that's
    actually useful for the candidate to see, not an engineering detail."""
    await require_session_owner(channel_name, uid)
    stage = get_current_stage(channel_name)
    if stage:
        asyncio.create_task(update_session_stage_in_firestore(channel_name, stage))
    return {"stage": stage}


@router.get("/{channel_name}/report")
async def get_session_report(channel_name: str, uid: str = Depends(verify_token)):
    """Polled by the client after the interview ends."""
    await require_session_owner(channel_name, uid)
    return await get_report_status_async(channel_name)


@router.post("/{channel_name}/end")
async def end_session(channel_name: str, uid: str = Depends(verify_token)):
    """Called when the candidate taps End, at any stage.
    Ending early still produces a report, scored on whatever was covered.
    """
    await require_session_owner(channel_name, uid)
    if has_report_started(channel_name):
        return get_report_status(channel_name)

    messages = get_last_messages(channel_name)
    has_any_answer = any((m.get("role") == "user" and (m.get("content") or "").strip()) for m in messages)
    if not has_any_answer:
        mark_not_applicable(channel_name)
        return get_report_status(channel_name)

    profile = get_profile_for_channel(channel_name)
    if profile is None:
        mark_not_applicable(channel_name)
        return get_report_status(channel_name)

    meta = get_channel_session_meta(channel_name)
    mode = meta.mode if meta else "full"
    target_skill = meta.target_skill if meta else None

    start_report_generation(
        channel_name,
        profile,
        messages,
        mode=mode,
        target_skill=target_skill,
    )
    return get_report_status(channel_name)
