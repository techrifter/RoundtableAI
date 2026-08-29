import base64

import httpx

from app.config import settings

# This module handles the lifecycle of the Agora Conversational AI agent, 
# including starting it in a channel, fetching its conversation history, and stopping it.
# Reference: https://docs.agora.io/en/api-reference/api-ref/conversational-ai

# 0 does not mean "join as uid 0" — Agora reads it as "generate a random uid for
# the agent". That is why the agent's RTC token must be a channel-wide wildcard:
# the real uid does not exist until join time. Used literally for agent_rtm_uid.
AGENT_UID = 0

# agent_id per channel, needed to call the history endpoint after the interview.
# Same in-memory, per-process pattern as the profile and report stores.
_agent_id_by_channel: dict[str, str] = {}


def remember_agent_id(channel: str, agent_id: str) -> None:
    if agent_id:
        _agent_id_by_channel[channel] = agent_id


def get_agent_id(channel: str) -> str | None:
    return _agent_id_by_channel.get(channel)


def _basic_auth_header() -> str:
    raw = f"{settings.agora_customer_id}:{settings.agora_customer_secret}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


async def start_agent(channel_name: str, agent_token: str, candidate_uid: int, system_prompt: str) -> dict:
    """Starts an Agora Conversational AI agent in the given channel, wired to our
    custom-LLM endpoint (this backend's /chat/completions, reached via PUBLIC_BASE_URL).
    """
    url = f"https://api.agora.io/api/conversational-ai-agent/v2/projects/{settings.agora_app_id}/join"

    payload = {
        "name": f"roundtable-{channel_name}",
        "properties": {
            "channel": channel_name,
            "token": agent_token,
            "agent_rtc_uid": str(AGENT_UID),
            "remote_rtc_uids": [str(candidate_uid)],
            "enable_string_uid": False,
            "idle_timeout": 120,
            "agent_rtm_uid": str(AGENT_UID),
            # Powers the live transcript: Agora sends transcript, agent-state and
            # per-turn latency events over RTM. `token` above is reused for RTM
            # login, so it needs both privileges (see build_agent_rtc_rtm_token).
            "advanced_features": {
                "enable_rtm": True,
            },
            # The agent's turn-detection, ASR, LLM and TTS are all configured here. The
            # agent is a single process, so these are not per-channel but per-agent.
            "turn_detection": {
                "mode": "default",
                "config": {
                    "speech_threshold": 0.55,
                    "start_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "interrupt_duration_ms": 200,
                            "speaking_interrupt_duration_ms": 350,
                            "prefix_padding_ms": 800,
                        },
                    },
                    "end_of_speech": {
                        "mode": "semantic",
                        "semantic_config": {
                            # Duration of silence that triggers end-of-turn. 550ms is a sweet spot
                            "silence_duration_ms": 550,
                            "max_wait_ms": 6000,
                        },
                    },
                },
            },
            "asr": {
                "credential_mode": "managed",
                "vendor": "deepgram",
                "language": "en-US",
                "params": {
                    "url": "wss://api.deepgram.com/v1/listen",
                    "model": "nova-3",
                },
            },
            "llm": {
                # channel query param lets /chat/completions record which persona
                # is currently active per session, so the client can poll and show
                # "who's asking" — the candidate's actual point of interest, not an
                # engineering metric.
                "url": f"{settings.public_base_url}/chat/completions?channel={channel_name}",
                "style": "openai",
                "system_messages": [{"role": "system", "content": system_prompt}],
                # Played directly via TTS on join, before any LLM call — this is what
                # makes the interviewer speak first, unconditionally, every time.
                "greeting_message": "Hi, hey! Thanks for joining today's interview panel. Let's get started.",
                "failure_message": "Sorry, I'm having trouble responding right now.",
                "max_history": 32,
                # Without this, the agent may only emit audio for its own turns and
                # never publish the text events that become "assistant" transcript messages.
                "output_modalities": ["text", "audio"],
            },
            "tts": {
                "credential_mode": "managed",
                "vendor": "minimax",
                "params": {
                    "url": "wss://api.minimax.io/ws/v1/t2a_v2",
                    "model": "speech-2.8-turbo",
                    "voice_setting": {
                        "voice_id": "English_Persuasive_Man",
                        "speed": 1.0,
                    },
                    # 44.1kHz is a media-standard rate, not a typical RTC voice rate;
                    # forcing Agora to resample non-native rates into its RTC audio
                    # pipeline was a suspected source of crackling/dropout artifacts.
                    "audio_setting": {"sample_rate": 24000},
                },
            },
            # advanced_features.enable_rtm only turns Signaling on; transcripts
            # specifically need this second flag telling the agent to route them
            # over RTM rather than the default RTC data channel.
            "parameters": {
                "data_channel": "rtm",
                # Powers onTurnFinished's per-turn ASR/LLM/TTS latency breakdown on
                # the client.
                "enable_metrics": True,
            },
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": _basic_auth_header()},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def fetch_agent_history(agent_id: str) -> list[dict]:
    """The agent's full conversation, as role/content messages.

    Worth a network call only because it is not subject to the `max_history` cap we
    set on the agent: what Agora forwards to /chat/completions is truncated to the
    last 20 messages, so on a long interview the Technical round has already fallen
    out by the time the report is built. This returns everything since agent start.

    Only callable while the agent is still running, which holds here — nothing calls
    stop_agent(), so the agent lives until its idle_timeout.
    """
    url = (
        f"https://api.agora.io/api/conversational-ai-agent/v2/projects/"
        f"{settings.agora_app_id}/agents/{agent_id}/history"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers={"Authorization": _basic_auth_header()})
        response.raise_for_status()
        contents = response.json().get("contents") or []

    return [
        {"role": c["role"], "content": c["content"]}
        for c in contents
        if c.get("role") in ("user", "assistant") and (c.get("content") or "").strip()
    ]


async def stop_agent(agent_id: str) -> None:
    url = (
        f"https://api.agora.io/api/conversational-ai-agent/v2/projects/"
        f"{settings.agora_app_id}/agents/{agent_id}/leave"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers={"Authorization": _basic_auth_header()})
        response.raise_for_status()
