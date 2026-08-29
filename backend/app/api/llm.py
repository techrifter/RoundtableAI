import json
import logging
import time

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.api.jd import get_profile_for_channel
from app.config import settings
from app.agents.style import SPOKEN_TONE_RULES
from app.evaluation.report import has_report_started, start_report_generation
from app.orchestration.orchestrator import InterviewStage, build_stage_system_prompt, get_stage_voice_config
from app.services.http_client import get_http_client

router = APIRouter()
logger = logging.getLogger("roundtable.turns")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Current persona, polled by the Android client to show "who's asking".
_current_stage_by_channel: dict[str, str] = {}

# Channels that already got their intro line. Tracked explicitly rather than
# inferred from the message history — what Agora puts in that history isn't what
# we assumed, and inferring it caused a live regression.
_intro_given_by_channel: set[str] = set()

# Latest history as Agora last sent it; api/session.py's /end scores from this when
# the candidate hangs up before the interview reaches its scripted close.
_last_messages_by_channel: dict[str, list[dict]] = {}

# One wall-clock stamp per candidate answer, so the orchestrator can budget a round
# in minutes rather than turns. Appended as answers arrive rather than stored with
# the message. Lost on restart, which the stage machine tolerates by falling back to its turn bounds.
_answer_times_by_channel: dict[str, list[float]] = {}


def _record_answer_times(channel: str, answer_count: int) -> list[float]:
    times = _answer_times_by_channel.setdefault(channel, [])
    now = time.time()
    while len(times) < answer_count:
        times.append(now)
    return times


def get_current_stage(channel: str) -> str | None:
    return _current_stage_by_channel.get(channel)


def get_last_messages(channel: str) -> list[dict]:
    return _last_messages_by_channel.get(channel, [])

FALLBACK_SYSTEM_PROMPT = (
    "You are a technical interviewer. This only applies if the interview profile "
    "hasn't been parsed yet; this only applies for direct/manual testing."
)

INTRO_SYSTEM_PROMPT = (
    "You are the interviewer, opening a live interview call with a candidate you have "
    "never spoken to before. A single curt line like 'hello, thanks for joining' is not "
    "enough, that reads as a bot, not a human running an interview. Say two short "
    "sentences: a plain hello and thanks for joining, then a brief, natural line "
    "setting expectations the way a real interviewer would, for example mentioning "
    "this is the technical round, or telling them to take their time and just talk "
    "through their thinking, or that it's a casual conversation, not an exam. Sound "
    "like someone actually talking, not reciting a script. Keep it professional and "
    "neutral, not warm or personal, since you do not know this person yet. Never say "
    "things like 'glad to have you here' or 'excited to have you', that is how a host "
    "greets a guest, not how an interviewer opens an interview. Never thank the "
    "candidate for having you or for the opportunity either, that is something the "
    "candidate would say, not you. Do not ask a question yet, that starts on your "
    "next turn. "
    f"{SPOKEN_TONE_RULES}"
)


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible endpoint Agora's Conversational AI Engine calls as the
    agent's custom LLM. Streams SSE chat.completion.chunk frames.

    The orchestrator picks the active persona deterministically from the assistant
    turn count — never by asking the LLM — and rewrites the system message. Stateless:
    Agora resends full history each call, so stage is recomputed from it.
    """
    body = await request.json()
    messages = body.get("messages", [])
    channel = request.query_params.get("channel")
    intro_only = bool(channel) and channel not in _intro_given_by_channel

    if channel:
        _last_messages_by_channel[channel] = messages

    if intro_only:
        stage, system_prompt = InterviewStage.TECHNICAL, INTRO_SYSTEM_PROMPT
    else:
        # A live session always has a profile bound by POST /session/start; None
        # here means the backend restarted mid-interview. Keep the conversation
        # going with a generic interviewer rather than dropping the call.
        profile = get_profile_for_channel(channel) if channel else None
        if profile is None:
            stage, system_prompt = "unparsed", FALLBACK_SYSTEM_PROMPT
        else:
            answer_count = sum(
                1 for m in messages if m.get("role") == "user" and (m.get("content") or "").strip()
            )
            answer_times = _record_answer_times(channel, answer_count) if channel else None
            try:
                stage, system_prompt = build_stage_system_prompt(profile, messages, answer_times)
            except Exception:
                stage, system_prompt = "unparsed", FALLBACK_SYSTEM_PROMPT

    if channel:
        _current_stage_by_channel[channel] = (
            stage.value if isinstance(stage, InterviewStage) else stage
        )

    if (
        channel
        and stage == InterviewStage.FINAL_EVAL
        and not intro_only
        and not has_report_started(channel)
    ):
        # Kicked off here (not awaited) so the goodbye line streams to TTS
        # immediately — evaluation runs in the background and is picked up by
        # the client polling GET /session/{channel}/report.
        start_report_generation(channel, profile, messages)

    if messages and messages[0].get("role") == "system":
        messages = [{"role": "system", "content": system_prompt}, *messages[1:]]
    else:
        messages = [{"role": "system", "content": system_prompt}, *messages]

    turn_id = f"{time.time():.3f}"
    logger.info("[%s] STAGE: %s", turn_id, stage)
    user_text = next(
        (m.get("content") for m in reversed(messages) if m.get("role") == "user"), ""
    )
    logger.info("[%s] USER (ASR): %r", turn_id, user_text)

    QUESTION_MARK_LIMIT = 3

    async def event_stream():
        generated = []
        completed_normally = False
        qmark_count = 0
        try:
            if isinstance(stage, InterviewStage):
                # Only the very first chunk of a response may carry object:
                # chat.completion.custom_metadata (Agora ignores it on any later
                # chunk), so it's sent once here before any real content — used to
                # switch TTS voice per persona and, for the closing line, to stop
                # the candidate's speech from cutting off the goodbye mid-sentence.
                metadata = {"tts_params": {"params": {"voice_setting": get_stage_voice_config(stage)}}}
                if stage == InterviewStage.FINAL_EVAL:
                    metadata["interruptable"] = False
                yield {
                    "data": json.dumps(
                        {
                            "id": f"meta-{turn_id}",
                            "object": "chat.completion.custom_metadata",
                            "choices": [],
                            "metadata": metadata,
                        }
                    )
                }

            client = get_http_client()
            async with client.stream(
                "POST",
                GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_model,
                    "messages": messages,
                    "stream": True,
                    # Persona prompt now hard-caps replies to one sentence, so
                    # "low" is enough for quality and cuts LLM latency.
                    "reasoning_effort": "low",
                    # Sized for up to ~3 short questions' worth of content.
                    "max_completion_tokens": 200,
                },
            ) as upstream:
                if upstream.status_code != 200:
                    # httpx doesn't raise on non-2xx by itself, and a Groq error body
                    # isn't SSE-shaped, so the "data: " line filter below would
                    # silently swallow it entirely — dead air with no exception and
                    # no log. A spoken fallback beats silence on a live call.
                    error_body = (await upstream.aread())[:500]
                    logger.error("[%s] GROQ ERROR %s: %r", turn_id, upstream.status_code, error_body)
                    fallback = "Sorry, could you say that again?"
                    generated.append(fallback)
                    completed_normally = True
                    yield {
                        "data": json.dumps(
                            {
                                "id": turn_id,
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {"role": "assistant", "content": fallback}, "finish_reason": None}],
                            }
                        )
                    }
                    yield {
                        "data": json.dumps(
                            {
                                "id": turn_id,
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            }
                        )
                    }
                    yield {"data": "[DONE]"}
                    return

                if intro_only:
                    # The model ignores "don't ask a question yet" most of the time,
                    # so buffer the whole greeting and strip any question in code.
                    buffered = []
                    async for line in upstream.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line[len("data: "):]
                        if payload.strip() == "[DONE]":
                            break
                        chunk = json.loads(payload)
                        forwarded = _strip_reasoning(chunk)
                        if forwarded is None:
                            continue
                        content = forwarded.get("choices", [{}])[0].get("delta", {}).get("content")
                        if content:
                            buffered.append(content)

                    # Fallback only fires if the model's entire reply was a question
                    # with no preceding greeting, which is unlikely but possible.
                    cleaned = _strip_questions(_sanitize_for_tts("".join(buffered))) or "Hi, thanks for joining today."
                    generated.append(cleaned)
                    completed_normally = True
                    if channel:
                        _intro_given_by_channel.add(channel)
                    yield {
                        "data": json.dumps(
                            {
                                "id": turn_id,
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {"role": "assistant", "content": cleaned}, "finish_reason": None}],
                            }
                        )
                    }
                    yield {
                        "data": json.dumps(
                            {
                                "id": turn_id,
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            }
                        )
                    }
                    yield {"data": "[DONE]"}
                    return

                # The rest of the interview is streamed chunk-by-chunk, with a small budget of
                # question marks before the turn is cut off and the candidate gets to speak.
                async for line in upstream.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        completed_normally = True
                        yield {"data": "[DONE]"}
                        break

                    chunk = json.loads(payload)
                    forwarded = _strip_reasoning(chunk)
                    if forwarded is None:
                        continue

                    delta = forwarded.get("choices", [{}])[0].get("delta", {})
                    limit_hit = False
                    content = delta.get("content")
                    if content:
                        content = _sanitize_for_tts(content)
                        cutoff_idx = None
                        for i, ch in enumerate(content):
                            if ch == "?":
                                qmark_count += 1
                                if qmark_count >= QUESTION_MARK_LIMIT:
                                    cutoff_idx = i
                                    break
                        if cutoff_idx is not None:
                            content = content[: cutoff_idx + 1]
                            limit_hit = True
                        delta["content"] = content
                        generated.append(content)

                    yield {"data": json.dumps(forwarded)}

                    if limit_hit:
                        # Personas are told to ask one question per turn; the model
                        # sometimes reels off several. Cut the turn once it burns a
                        # small budget of question marks — two is a legitimate
                        # lead-in, five is not.
                        completed_normally = True
                        yield {
                            "data": json.dumps(
                                {
                                    "id": forwarded.get("id", turn_id),
                                    "object": "chat.completion.chunk",
                                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                }
                            )
                        }
                        yield {"data": "[DONE]"}
                        break
        finally:
            full_text = "".join(generated)
            # If Agora barge-in interrupts the agent, it stops reading this stream
            # early and our generator is cancelled before completed_normally is set —
            # so a False value here means the spoken audio was cut short mid-response.
            status = "COMPLETED" if completed_normally else "CUT OFF BY AGORA (likely interrupted)"
            logger.info("[%s] LLM RESPONSE (%s): %r", turn_id, status, full_text)

    return EventSourceResponse(event_stream())


_TTS_UNSAFE_CHARS = {
    "—": ", ",  # em dash
    "–": ", ",  # en dash
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "…": "...",  # ellipsis
    "‑": "-",   # non-breaking hyphen
}


def _sanitize_for_tts(text: str) -> str:
    """LLM output commonly uses em dashes, curly quotes, and ellipsis characters
    that some TTS engines mishandle, producing stutters/glitches right at those
    characters. Replace them with plain ASCII punctuation before it's spoken.
    """
    for unsafe, replacement in _TTS_UNSAFE_CHARS.items():
        text = text.replace(unsafe, replacement)
    return text


def _strip_questions(text: str) -> str:
    """Drops any question and everything after it — used only for the interview's
    opening line, which must be a plain greeting no matter what the model generates.
    Walks back to the previous sentence-ending punctuation before the first "?" so
    the result doesn't end on a dangling lead-in fragment.
    """
    qmark_idx = text.find("?")
    if qmark_idx == -1:
        return text.strip()
    boundary = max(text.rfind(".", 0, qmark_idx), text.rfind("!", 0, qmark_idx))
    return text[: boundary + 1].strip() if boundary != -1 else ""


def _strip_reasoning(chunk: dict) -> dict | None:
    """Reasoning-capable Groq models (e.g. gpt-oss) emit extra 'reasoning'/'channel'
    fields on delta before the real answer. Agora's custom-LLM contract expects plain
    OpenAI chat.completion.chunk shape, so drop reasoning-only chunks and strip the
    non-standard keys from the rest before forwarding.
    """
    choices = chunk.get("choices") or []
    if not choices:
        return chunk

    delta = choices[0].get("delta", {})
    has_content = "content" in delta or "role" in delta
    has_finish = choices[0].get("finish_reason") is not None
    if not has_content and not has_finish:
        return None

    delta.pop("reasoning", None)
    delta.pop("channel", None)
    return chunk
