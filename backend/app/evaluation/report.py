import asyncio
import json
import logging
from typing import NamedTuple

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.firestore_db import get_session_report_from_firestore, update_session_report
from app.services.agora_agent import fetch_agent_history, get_agent_id
from app.models.interview_profile import InterviewProfile
from app.orchestration.orchestrator import collect_interview_evidence
from app.services.http_client import get_http_client

logger = logging.getLogger("roundtable.evaluation")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class EvidencePoint(BaseModel):
    point: str
    evidence: str


class InterviewReport(BaseModel):
    mode: str = "full"
    target_skill: str | None = None
    technical: float
    system_design: float
    behavioral: float
    communication: float
    overall: float
    strengths: list[EvidencePoint]
    improvements: list[EvidencePoint]
    summary: str


class _Budget(NamedTuple):
    reasoning_effort: str
    max_completion_tokens: int


# gpt-oss is a reasoning model: max_completion_tokens caps reasoning *and* output
# together, so too low a ceiling starves the JSON and Groq 400s. The retry
# escalates rather than repeating — a long interview needs the extra room.
_TOKEN_BUDGETS: tuple[_Budget, ...] = (
    _Budget(reasoning_effort="low", max_completion_tokens=4000),
    _Budget(reasoning_effort="low", max_completion_tokens=8000),
)

# In-memory report state per channel — consistent with the rest of the backend's
# "no session store, small dicts keyed by channel" pattern. {"status": ..., "report": ...}
_report_state_by_channel: dict[str, dict] = {}

# Guards against starting a second generation for the same channel: once the
# interview reaches FINAL_EVAL it *stays* there, so the candidate could keep
# talking and trigger /chat/completions again before the first report finishes.
_started_channels: set[str] = set()


def clear_channel_state(channel: str) -> None:
    _report_state_by_channel.pop(channel, None)
    _started_channels.discard(channel)


def has_report_started(channel: str) -> bool:
    return channel in _started_channels


async def get_report_status_async(channel: str) -> dict:
    if channel in _report_state_by_channel:
        return _report_state_by_channel[channel]
    res = await get_session_report_from_firestore(channel)
    if res is not None:
        _report_state_by_channel[channel] = res
        return res
    return {"status": "not_started", "report": None}


def get_report_status(channel: str) -> dict:
    return _report_state_by_channel.get(channel, {"status": "not_started", "report": None})


def mark_not_applicable(channel: str) -> None:
    """For a channel that ended with nothing to score (candidate hung up before
    saying anything) — distinct from "failed" (which implies scoring was
    attempted and errored), so the client can show an honest, specific message.
    """
    _started_channels.add(channel)
    _report_state_by_channel[channel] = {"status": "not_applicable", "report": None}
    asyncio.create_task(update_session_report(channel, "not_applicable"))


def start_report_generation(
    channel: str,
    profile: InterviewProfile,
    messages: list[dict],
    mode: str = "full",
    target_skill: str | None = None,
) -> None:
    """Fire-and-forget: kicks off report generation as a background task instead of
    awaiting it inline. This deliberately keeps evaluation off the conversational
    critical path (CLAUDE.md's own rule) — the candidate hears the goodbye line
    immediately, the report finishes a few seconds later and is fetched by polling.
    """
    _started_channels.add(channel)
    _report_state_by_channel[channel] = {"status": "pending", "report": None}
    asyncio.create_task(_generate_and_store(channel, profile, list(messages), mode, target_skill))


def _build_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if not content or role not in ("user", "assistant"):
            continue
        speaker = "Candidate" if role == "user" else "Interviewer"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _build_report_prompt(
    profile: InterviewProfile,
    messages: list[dict],
    mode: str = "full",
    target_skill: str | None = None,
) -> str:
    transcript = _build_transcript(messages)
    weak_topics, strong_topics = collect_interview_evidence(
        profile, messages, mode=mode, target_skill=target_skill
    )

    if mode == "focused" and target_skill:
        return (
            f"You are scoring a completed 5-to-15 minute FOCUSED PRACTICE DRILL for a {profile.role} role. "
            f"This session was specifically dedicated to testing the candidate's mastery of: {target_skill}.\n\n"
            f"TRANSCRIPT:\n{transcript}\n\n"
            "During the drill, topic-tracking flagged these signals: "
            f"topics answered weakly: {', '.join(weak_topics) or 'none flagged'}; "
            f"topics answered with substance: {', '.join(strong_topics) or 'none flagged'}.\n\n"
            f"Score the candidate specifically on their performance in this {target_skill} drill. "
            "Do NOT penalize them for omitting unrelated interview stages (such as system design or behavioral rounds that were not part of this drill). "
            "For 'overall', score their overall competence on this skill (0-10). "
            "For 'technical', score their technical accuracy on this skill (0-10). "
            "For 'system_design' and 'behavioral', score them proportionally to their overall performance or relevant aspects demonstrated. "
            "For 'communication', score their clarity and articulateness (0-10). "
            f"Provide 2 to 3 concise, concrete strengths specifically citing their answers about {target_skill}. "
            f"Provide 2 to 3 targeted, actionable coaching tips/improvements for them to master {target_skill}. "
            f"In 'summary', provide a 2-3 sentence coaching verdict on their readiness with {target_skill}.\n\n"
            "Respond with ONLY a JSON object in this exact shape:\n"
            "{\n"
            '  "technical": <0-10 float>,\n'
            '  "system_design": <0-10 float>,\n'
            '  "behavioral": <0-10 float>,\n'
            '  "communication": <0-10 float>,\n'
            '  "overall": <0-10 float>,\n'
            '  "strengths": [{"point": "<short label>", "evidence": "<what the candidate said>"}, ...],\n'
            '  "improvements": [{"point": "<short label>", "evidence": "<actionable drill takeaway>"}, ...],\n'
            '  "summary": "<2-3 sentence coaching summary>"\n'
            "}\n"
        )

    return (
        f"You are scoring a completed live voice interview for a {profile.role} role. "
        f"The role required these skills: {', '.join(profile.skills)}. "
        f"System design topics relevant to the role: {', '.join(profile.system_design_topics)}. "
        "Below is the full interview transcript, interviewer and candidate turns in order.\n\n"
        f"TRANSCRIPT:\n{transcript}\n\n"
        "During the interview, deterministic topic-tracking (not you) flagged these signals — use them "
        "as grounding, but judge primarily from the transcript itself: "
        f"topics the candidate answered weakly or uncertainly on: {', '.join(weak_topics) or 'none flagged'}; "
        f"topics the candidate answered with real substance: {', '.join(strong_topics) or 'none flagged'}.\n\n"
        "Score the candidate honestly, as a real interview panel would — most candidates are not perfect, "
        "so avoid clustering every score near 8-9 out of politeness. Every strength and improvement you list "
        "must cite something the candidate actually said in the transcript, not a generic observation. "
        "Do not claim false precision: these are informed estimates from one conversation, not a lab measurement.\n\n"
        "Respond with ONLY a JSON object, no markdown fences, no extra text, in exactly this shape:\n"
        "{\n"
        '  "technical": <0-10 float>,\n'
        '  "system_design": <0-10 float>,\n'
        '  "behavioral": <0-10 float>,\n'
        '  "communication": <0-10 float>,\n'
        '  "overall": <0-10 float>,\n'
        '  "strengths": [{"point": "<short label>", "evidence": "<what the candidate said, briefly>"}, ...],\n'
        '  "improvements": [{"point": "<short label>", "evidence": "<what was weak or missing>"}, ...],\n'
        '  "summary": "<2-3 sentence overall take, in plain spoken language>"\n'
        "}\n"
        "Include 2 to 4 items each in strengths and improvements. If a section of the interview (technical, "
        "system design, or behavioral) barely happened, score it lower and say so rather than guessing high."
    )


async def _resolve_transcript(channel: str, messages: list[dict]) -> list[dict]:
    """Prefer Agora's full history over the messages we were handed.

    `messages` is whatever Agora last forwarded to /chat/completions, so it is
    capped at max_history (20) — a long interview loses its earliest turns, which
    is exactly the Technical round the report needs to score. Best-effort: if the
    call fails we score the truncated transcript rather than no transcript.
    """
    agent_id = get_agent_id(channel)
    if not agent_id:
        return messages
    try:
        history = await fetch_agent_history(agent_id)
    except Exception as exc:
        logger.warning("[%s] REPORT history fetch failed, using forwarded messages: %s", channel, exc)
        return messages

    if len(history) < len(messages):
        # Shouldn't happen, but never trade a longer transcript for a shorter one.
        logger.warning("[%s] REPORT history shorter than forwarded messages, keeping the latter", channel)
        return messages

    logger.info("[%s] REPORT transcript: %d messages from history (forwarded had %d)",
                channel, len(history), len(messages))
    return history


async def _generate_and_store(
    channel: str,
    profile: InterviewProfile,
    messages: list[dict],
    mode: str = "full",
    target_skill: str | None = None,
) -> None:
    messages = await _resolve_transcript(channel, messages)

    # One retry: this call is off the conversational critical path, so a couple
    # extra seconds costs nothing, and it turns a transient Groq hiccup (a 400/429
    # that clears itself moments later) into a normal "ready" report instead of
    # sending the candidate straight to the failure screen on the first blip.
    last_error: Exception | None = None
    for attempt, budget in enumerate(_TOKEN_BUDGETS):
        try:
            report = await _call_groq_for_report(profile, messages, budget, mode, target_skill)
            report_dict = report.model_dump()
            report_dict["mode"] = mode
            report_dict["target_skill"] = target_skill
            _report_state_by_channel[channel] = {"status": "ready", "report": report_dict}
            logger.info("[%s] REPORT ready: overall=%.1f (mode=%s skill=%s)", channel, report.overall, mode, target_skill)
            asyncio.create_task(update_session_report(channel, "ready", report_dict))
            return
        except Exception as exc:
            last_error = exc
            logger.warning("[%s] REPORT attempt %d failed: %s", channel, attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(3)

    logger.error("[%s] REPORT generation failed after retry", channel, exc_info=last_error)
    _report_state_by_channel[channel] = {"status": "failed", "report": None}
    asyncio.create_task(update_session_report(channel, "failed"))


async def _call_groq_for_report(
    profile: InterviewProfile,
    messages: list[dict],
    budget: "_Budget",
    mode: str = "full",
    target_skill: str | None = None,
) -> InterviewReport:
    client = get_http_client()
    response = await client.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": settings.groq_model,
            "messages": [
                {"role": "user", "content": _build_report_prompt(profile, messages, mode, target_skill)}
            ],
            "stream": False,
            "reasoning_effort": budget.reasoning_effort,
            "max_completion_tokens": budget.max_completion_tokens,
            "response_format": {"type": "json_object"},
        },
        timeout=60.0,
    )
    if response.status_code != 200:
        raise ValueError(f"Groq report call failed {response.status_code}: {response.text[:800]!r}")
    body = response.json()
    usage = body.get("usage") or {}
    logger.info(
        "REPORT usage: completion=%s (reasoning=%s) prompt=%s ceiling=%s",
        usage.get("completion_tokens"),
        (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        usage.get("prompt_tokens"),
        budget.max_completion_tokens,
    )
    content = body["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Report response was not valid JSON: {content[:300]!r}") from exc

    try:
        parsed["mode"] = mode
        parsed["target_skill"] = target_skill
        return InterviewReport.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"Report JSON didn't match expected shape: {parsed!r}") from exc
