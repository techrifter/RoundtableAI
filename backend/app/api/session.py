import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.technical import build_technical_persona_prompt
from app.api.jd import bind_profile_to_channel, get_profile_for_channel, get_stored_profile
from app.api.llm import get_current_stage, get_last_messages
from app.config import settings
from app.evaluation.report import get_report_status, has_report_started, mark_not_applicable, start_report_generation
from app.services.agora_agent import AGENT_UID, remember_agent_id, start_agent
from app.services.agora_token import build_agent_rtc_rtm_token, build_rtc_token, build_rtm_token

router = APIRouter(prefix="/session")

CANDIDATE_UID = 1001


class StartSessionRequest(BaseModel):
    profile_id: str


class StartSessionResponse(BaseModel):
    app_id: str
    channel_name: str
    candidate_uid: int
    candidate_token: str
    rtm_token: str
    agent_id: str


@router.post("/start", response_model=StartSessionResponse)
async def start_session(request: StartSessionRequest):
    """Create a channel, generate tokens for the candidate and the agent, and start
    the Agora Conversational AI agent wired to our custom-LLM endpoint, using a
    Technical Interviewer persona built from the profile the candidate's JD/resume
    upload produced (POST /jd/upload). The profile is bound to this channel so
    every later turn (api/llm.py) and the end-of-call report use the same one —
    not a single global profile shared across every session.
    """
    profile = get_stored_profile(request.profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown profile_id — call POST /jd/upload first.")

    channel_name = f"roundtable-{secrets.token_hex(4)}"
    candidate_token = build_rtc_token(channel_name, CANDIDATE_UID)
    agent_token = build_agent_rtc_rtm_token(channel_name, AGENT_UID, str(AGENT_UID))
    rtm_token = build_rtm_token(str(CANDIDATE_UID))

    bind_profile_to_channel(channel_name, profile)
    system_prompt = build_technical_persona_prompt(profile)

    try:
        agent_response = await start_agent(
            channel_name=channel_name,
            agent_token=agent_token,
            candidate_uid=CANDIDATE_UID,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to start Agora agent: {exc}") from exc

    agent_id = agent_response.get("agent_id", "")
    remember_agent_id(channel_name, agent_id)

    return StartSessionResponse(
        app_id=settings.agora_app_id,
        channel_name=channel_name,
        candidate_uid=CANDIDATE_UID,
        candidate_token=candidate_token,
        rtm_token=rtm_token,
        agent_id=agent_id,
    )


@router.get("/{channel_name}/stage")
async def get_session_stage(channel_name: str):
    """Polled by the client during a live call to show which persona is
    currently interviewing — the one piece of orchestrator state that's
    actually useful for the candidate to see, not an engineering detail."""
    return {"stage": get_current_stage(channel_name)}


@router.get("/{channel_name}/report")
async def get_session_report(channel_name: str):
    """Polled by the client after the interview ends. Report generation runs as a
    background task started once the interview reaches FINAL_EVAL (see api/llm.py),
    so this is typically already "pending" or "ready" by the time the candidate
    disconnects — status is "not_started" | "pending" | "ready" | "failed" | "not_applicable"."""
    return get_report_status(channel_name)


@router.post("/{channel_name}/end")
async def end_session(channel_name: str):
    """Called when the candidate taps End, at any stage.

    Ending early still produces a report, scored on whatever was covered. No-op if
    the interview reached FINAL_EVAL and api/llm.py already started generation.
    """
    if has_report_started(channel_name):
        return get_report_status(channel_name)

    messages = get_last_messages(channel_name)
    has_any_answer = any((m.get("role") == "user" and (m.get("content") or "").strip()) for m in messages)
    if not has_any_answer:
        mark_not_applicable(channel_name)
        return get_report_status(channel_name)

    profile = get_profile_for_channel(channel_name)
    if profile is None:
        # Only reachable if the backend restarted mid-interview and lost the
        # binding. Scoring this transcript against some other profile would be
        # worse than admitting we can't score it.
        mark_not_applicable(channel_name)
        return get_report_status(channel_name)

    start_report_generation(channel_name, profile, messages)
    return get_report_status(channel_name)
