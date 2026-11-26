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
from app.orchestration.signals import THIN_ANSWER_WORD_COUNT, UNCERTAIN_PHRASES
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
    try:
        from app.api.llm import clear_channel_state as clear_llm_state
        clear_llm_state(channel)
    except Exception:
        pass


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


_SCORING_RUBRIC = """MANDATORY CALIBRATED SCORING RUBRIC (Anchor all scores strictly to this scale):
- 0.0 - 2.0 (Non-Participatory / Unanswered): Candidate gave no substantive answers, said "I don't know" / "pass" repeatedly, stayed mostly silent, or hung up with almost nothing said.
- 2.1 - 4.5 (Major Gaps / Inadequate): Attempted answers but demonstrated fundamental conceptual misconceptions, gave 1-line surface non-answers, or failed to answer core questions.
- 4.6 - 6.9 (Developing): Partial understanding; covers basic concepts but lacks depth or struggles on edge cases and failure modes.
- 7.0 - 8.9 (Good / Strong Competence): Clear, accurate, and solid answers. Explains technical decisions and trade-offs well. A candidate who answers well and thoroughly should score in this rewarding 7.0 - 8.9 range.
- 9.0 - 10.0 (Exceptional / Deep Mastery): Truly deep technical mastery of internals, proactive consideration of edge cases, production-grade scale trade-offs, and nuanced reasoning.

CRITICAL RULES FOR HONEST EVALUATION:
1. FAIR & REWARDING SCORING: If the candidate answered clearly, correctly, and with good substance, reward them fairly in the 7.0 to 8.9 range. Reserve 9.0+ for truly deep mastery and thorough edge-case analysis.
2. DO NOT INFLATE NON-ANSWERS: If the candidate gave no substantive answers, passed repeatedly, or stayed silent, score strictly in the 0.0 - 3.0 range and leave strengths as [].
3. DO NOT HALLUCINATE STRENGTHS: Do not praise admissions of ignorance (such as "Honest about not knowing") as technical strengths. Every listed strength must cite real technical substance the candidate explained in the transcript."""


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
            f"{_SCORING_RUBRIC}\n\n"
            f"Score the candidate specifically on their performance in this {target_skill} drill. "
            "Do NOT penalize them for omitting unrelated interview stages (such as system design or behavioral rounds that were not part of this drill). "
            "For 'overall', score their overall competence on this skill (0-10) using the rubric above. "
            "For 'technical', score their technical accuracy on this skill (0-10). "
            "For 'system_design' and 'behavioral', score them proportionally to their overall performance or relevant aspects demonstrated. "
            "For 'communication', score their clarity and articulateness (0-10). "
            f"Provide 1 to 3 concise strengths citing their answers about {target_skill} (or [] if no substantive knowledge was demonstrated). "
            f"Provide 2 to 3 targeted, actionable coaching tips/improvements for them to master {target_skill}. "
            f"In 'summary', provide an honest 2-3 sentence coaching verdict on their readiness with {target_skill}.\n\n"
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
        f"{_SCORING_RUBRIC}\n\n"
        "Score the candidate honestly, as a real interview panel would. "
        "Every strength and improvement you list must cite something the candidate actually said in the transcript. "
        "If a candidate answered weakly or gave non-answers, score strictly in the 0.0 - 4.0 range. Do not inflate.\n\n"
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
        "If the candidate provided no substantive technical answers, leave strengths as [] rather than inventing praise. "
        "If a section of the interview barely happened, score it lower and say so rather than guessing high."
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
        from app.firestore_db import get_db, get_agora_credentials
        from app.config import settings
        db = get_db()
        app_id = settings.agora_app_id
        customer_id = settings.agora_customer_id
        customer_secret = settings.agora_customer_secret

        if db:
            doc = db.collection("sessions").document(channel).get()
            if doc.exists:
                data = doc.to_dict() or {}
                if data.get("quota_type") == "BYOK":
                    uid = data.get("uid")
                    creds = await get_agora_credentials(uid)
                    if creds:
                        app_id = creds["app_id"]
                        customer_id = creds["customer_id"]
                        customer_secret = creds["customer_secret"]

        history = await fetch_agent_history(app_id, customer_id, customer_secret, agent_id)
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
            report = await _call_groq_for_report(profile, messages, budget, mode, target_skill, channel=channel)
            report_dict = report.model_dump()
            report_dict["mode"] = mode
            report_dict["target_skill"] = target_skill
            _report_state_by_channel[channel] = {"status": "ready", "report": report_dict}
            logger.info("[%s] REPORT ready: overall=%.1f (mode=%s skill=%s)", channel, report.overall, mode, target_skill)
            asyncio.create_task(update_session_report(channel, "ready", report_dict))
            try:
                from app.api.llm import clear_channel_state as clear_llm_state
                clear_llm_state(channel)
            except Exception:
                pass
            return
        except Exception as exc:
            last_error = exc
            logger.warning("[%s] REPORT attempt %d failed: %s", channel, attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(3)

    logger.error("[%s] REPORT generation failed after retry", channel, exc_info=last_error)
    _report_state_by_channel[channel] = {"status": "failed", "report": None}
    asyncio.create_task(update_session_report(channel, "failed"))
    try:
        from app.api.llm import clear_channel_state as clear_llm_state
        clear_llm_state(channel)
    except Exception:
        pass


def _evaluate_substantive_answers(messages: list[dict]) -> tuple[int, int]:
    """Counts (substantive_answers, low_engagement_answers) from the candidate."""
    substantive = 0
    low_engagement = 0
    for m in messages:
        if m.get("role") != "user":
            continue
        text = (m.get("content") or "").strip()
        if not text:
            continue
        words = text.split()
        lowered = text.lower()
        is_uncertain = any(phrase in lowered for phrase in UNCERTAIN_PHRASES)
        is_thin = len(words) <= THIN_ANSWER_WORD_COUNT
        if is_uncertain or is_thin:
            low_engagement += 1
        else:
            substantive += 1
    return substantive, low_engagement


def _apply_sanity_guardrails(
    report: InterviewReport,
    messages: list[dict],
) -> InterviewReport:
    """Enforces mathematical reality checks on the LLM's scores based on actual participation.
    Guarantees that a candidate who spoke very little, said 'I don't know', or passed
    never receives an unearned passing or high score.
    """
    substantive, low_eng = _evaluate_substantive_answers(messages)

    # Case 1: Zero substantive answers (said nothing, silence, or only 'I don't know' / 'pass')
    if substantive == 0:
        return InterviewReport(
            mode=report.mode,
            target_skill=report.target_skill,
            technical=min(report.technical, 1.0),
            system_design=min(report.system_design, 1.0),
            behavioral=min(report.behavioral, 1.0),
            communication=min(report.communication, 2.5),
            overall=min(report.overall, 1.0),
            strengths=[],
            improvements=[
                EvidencePoint(
                    point="Technical Engagement",
                    evidence="No substantive technical answers were provided. Talk through your reasoning and attempt solutions rather than passing.",
                )
            ],
            summary="The candidate did not provide substantive technical answers during this session. To receive an actionable evaluation, engage with the questions and talk through your thinking.",
        )

    # Case 2: Only 1 substantive answer across the entire session
    if substantive == 1:
        return InterviewReport(
            mode=report.mode,
            target_skill=report.target_skill,
            technical=min(report.technical, 3.5),
            system_design=min(report.system_design, 3.5),
            behavioral=min(report.behavioral, 3.5),
            communication=min(report.communication, 5.0),
            overall=min(report.overall, 3.0),
            strengths=report.strengths[:1],
            improvements=report.improvements or [
                EvidencePoint(
                    point="Interview Breadth & Depth",
                    evidence="Only a single question was substantively answered. Ensure you elaborate on technical details across all topics.",
                )
            ],
            summary=f"The candidate attempted one question but session participation was too limited to demonstrate comprehensive competence. Overall score capped at {min(report.overall, 3.0):.1f}.",
        )

    # Case 3: Very low ratio of substantive answers (majority are 'I don't know' / thin)
    if substantive <= 3 and low_eng >= substantive * 2:
        return InterviewReport(
            mode=report.mode,
            target_skill=report.target_skill,
            technical=min(report.technical, 4.5),
            system_design=min(report.system_design, 4.5),
            behavioral=min(report.behavioral, 4.5),
            communication=min(report.communication, 6.0),
            overall=min(report.overall, 4.5),
            strengths=report.strengths[:2],
            improvements=report.improvements,
            summary=report.summary,
        )

    return report


async def _call_groq_for_report(
    profile: InterviewProfile,
    messages: list[dict],
    budget: "_Budget",
    mode: str = "full",
    target_skill: str | None = None,
    channel: str = "",
) -> InterviewReport:
    api_key = (settings.groq_api_key or "").strip()
    if channel:
        try:
            from app.firestore_db import get_db, get_agora_credentials
            db = get_db()
            if db:
                doc = db.collection("sessions").document(channel).get()
                if doc.exists:
                    data = doc.to_dict() or {}
                    if data.get("quota_type") == "BYOK":
                        uid = data.get("uid")
                        creds = await get_agora_credentials(uid)
                        if creds and creds.get("groq_api_key"):
                            api_key = creds["groq_api_key"].strip()
        except Exception:
            pass

    if not api_key:
        raise ValueError("Groq API Key is missing.")

    client = get_http_client()
    req_json = {
        "model": settings.groq_model,
        "messages": [
            {"role": "user", "content": _build_report_prompt(profile, messages, mode, target_skill)}
        ],
        "stream": False,
        "max_completion_tokens": budget.max_completion_tokens,
        "response_format": {"type": "json_object"},
    }
    if "gpt-oss" in settings.groq_model:
        req_json["reasoning_effort"] = budget.reasoning_effort

    response = await client.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=req_json,
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
        raw_report = InterviewReport.model_validate(parsed)
        return _apply_sanity_guardrails(raw_report, messages)
    except ValidationError as exc:
        raise ValueError(f"Report JSON didn't match expected shape: {parsed!r}") from exc
