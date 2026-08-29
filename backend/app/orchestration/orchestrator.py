from dataclasses import dataclass
from enum import Enum

from app.agents.behavioral import build_behavioral_persona_prompt
from app.agents.system_design import build_system_design_persona_prompt
from app.agents.technical import build_technical_persona_prompt
from app.models.interview_profile import InterviewProfile
from app.orchestration.signals import analyze_answer

CLOSING_MESSAGE = (
    "That's everything from the panel. Thanks for your time today, we'll get back to you soon with feedback."
)


class InterviewStage(str, Enum):
    TECHNICAL = "technical"
    SYSTEM_DESIGN = "system_design"
    BEHAVIORAL = "behavioral"
    FINAL_EVAL = "final_eval"


STAGE_ORDER = [InterviewStage.TECHNICAL, InterviewStage.SYSTEM_DESIGN, InterviewStage.BEHAVIORAL]

# Turns per stage adapt to the candidate — bail early on weak answers, linger on
# strong ones — bounded so total interview length stays demo-safe.
STAGE_FLOOR_TURNS = 2
# Keep low: a higher ceiling lets one stage drill a single narrow detail for its
# whole budget, since topic-coverage matching only catches literal JD keywords.
# Works with TOPIC_BREADTH_RULE (agents/style.py), which caps same-detail follow-ups.
STAGE_CEILING_TURNS = 4
COVERAGE_TARGET_TOPICS = 3  # distinct JD topics touched before a stage is "enough"
LOW_ENGAGEMENT_STREAK_TO_ADVANCE = 2  # consecutive weak answers before bailing early

# Distinct MiniMax voice per persona so the panel sounds like different people.
STAGE_VOICE_CONFIG = {
    InterviewStage.TECHNICAL: {"voice_id": "English_Persuasive_Man", "speed": 1.0},
    InterviewStage.SYSTEM_DESIGN: {"voice_id": "English_Insightful_Speaker", "speed": 1.0},
    InterviewStage.BEHAVIORAL: {"voice_id": "English_radiant_girl", "speed": 1.15},
    InterviewStage.FINAL_EVAL: {"voice_id": "English_Persuasive_Man", "speed": 1.0},
}


def get_stage_voice_config(stage: InterviewStage) -> dict:
    return STAGE_VOICE_CONFIG[stage]


def _topic_pool_for(stage: InterviewStage, profile: InterviewProfile) -> list[str]:
    if stage == InterviewStage.TECHNICAL:
        return profile.skills
    if stage == InterviewStage.SYSTEM_DESIGN:
        return profile.system_design_topics
    if stage == InterviewStage.BEHAVIORAL:
        return profile.responsibilities
    return []


@dataclass
class StageState:
    stage: InterviewStage
    stage_start_turn: int  # index into the ordered list of candidate answers
    weak_topics: list[str]  # topics the candidate signaled low engagement on
    strong_topics: list[str]  # topics with a real, substantive answer


def _advance_stage_machine(profile: InterviewProfile, candidate_answers: list[str]) -> StageState:
    """Replays the candidate's answers in order against the adaptive floor/ceiling/
    coverage/engagement rules, to deterministically compute the current stage. This
    is re-derived from scratch on every request (no session store) since Agora
    resends full history each call — cheap here since interviews are short.
    """
    stage_idx = 0
    turns_in_stage = 0
    stage_start_turn = 0
    topics_hit: set[str] = set()
    low_engagement_streak = 0
    weak_topics: list[str] = []
    strong_topics: list[str] = []

    for turn_index, answer in enumerate(candidate_answers):
        if stage_idx >= len(STAGE_ORDER):
            break  # already in FINAL_EVAL, nothing left to advance through

        stage = STAGE_ORDER[stage_idx]
        topic_pool = _topic_pool_for(stage, profile)
        signal = analyze_answer(answer, topic_pool)

        turns_in_stage += 1
        topics_hit |= signal.matched_topics
        if signal.is_low_engagement:
            low_engagement_streak += 1
            weak_topics.extend(signal.matched_topics or [f"{stage.value} (turn {turns_in_stage})"])
        else:
            low_engagement_streak = 0
            strong_topics.extend(signal.matched_topics)

        should_advance = False
        if turns_in_stage >= STAGE_FLOOR_TURNS:
            if turns_in_stage >= STAGE_CEILING_TURNS:
                should_advance = True
            elif low_engagement_streak >= LOW_ENGAGEMENT_STREAK_TO_ADVANCE:
                should_advance = True  # candidate isn't engaging here, stop drilling
            elif len(topics_hit) >= min(COVERAGE_TARGET_TOPICS, len(topic_pool)):
                should_advance = True  # good coverage already, no need to linger

        if should_advance:
            stage_idx += 1
            turns_in_stage = 0
            stage_start_turn = turn_index + 1
            topics_hit = set()
            low_engagement_streak = 0

    if stage_idx >= len(STAGE_ORDER):
        return StageState(InterviewStage.FINAL_EVAL, stage_start_turn, weak_topics, strong_topics)
    return StageState(STAGE_ORDER[stage_idx], stage_start_turn, weak_topics, strong_topics)


def collect_interview_evidence(profile: InterviewProfile, messages: list[dict]) -> tuple[list[str], list[str]]:
    """Weak/strong topics accumulated across the *whole* interview, for the final
    report to cite as evidence. Reuses the stage machine's own bookkeeping (it
    never resets weak_topics/strong_topics between stages, only topics_hit) rather
    than a second analysis pass, so the report stays consistent with what actually
    drove stage routing instead of judging the transcript from scratch.
    """
    candidate_answers = [m.get("content", "") for m in messages if m.get("role") == "user"]
    state = _advance_stage_machine(profile, candidate_answers)
    return state.weak_topics, state.strong_topics


PRIOR_NOTES_LIMIT = 3  # cap, not "all of it" — see docstring


def extract_prior_notes(messages: list[dict], stage_start_turn: int) -> list[str]:
    """A few candidate utterances from before this stage, so the next persona can
    reference something said earlier.

    Capped deliberately: passing every earlier answer would hand the next persona a
    list dominated by whatever the last stage over-focused on.

    Sampled evenly across the earlier answers rather than taking the most recent
    few. A tail slice drops the opening answers, and the claim worth calling back
    to ("we ran Kafka in production") is usually made early — by the Behavioral
    round a tail slice contains nothing but System Design.
    """
    user_messages = [m.get("content", "") for m in messages if m.get("role") == "user"]
    earlier = [text for text in user_messages[:stage_start_turn] if text and text.strip()]
    if len(earlier) <= PRIOR_NOTES_LIMIT:
        return earlier

    step = (len(earlier) - 1) / (PRIOR_NOTES_LIMIT - 1)
    return [earlier[round(i * step)] for i in range(PRIOR_NOTES_LIMIT)]


def build_stage_system_prompt(profile: InterviewProfile, messages: list[dict]) -> tuple[InterviewStage, str]:
    candidate_answers = [m.get("content", "") for m in messages if m.get("role") == "user"]
    state = _advance_stage_machine(profile, candidate_answers)
    prior_notes = extract_prior_notes(messages, state.stage_start_turn)

    # strong_topics is already accumulated across every earlier stage by the stage
    # machine; handing it to the persona turns "spot the connection in this raw text"
    # into an explicit list of what the candidate has actually demonstrated.
    strong = state.strong_topics

    if state.stage == InterviewStage.TECHNICAL:
        return state.stage, build_technical_persona_prompt(profile, prior_notes, strong)
    if state.stage == InterviewStage.SYSTEM_DESIGN:
        return state.stage, build_system_design_persona_prompt(profile, prior_notes, strong)
    if state.stage == InterviewStage.BEHAVIORAL:
        return state.stage, build_behavioral_persona_prompt(profile, prior_notes, strong)
    return state.stage, (
        "The interview is now over. Say goodbye to the candidate in one short, warm sentence. "
        f'Say something close to: "{CLOSING_MESSAGE}"'
    )
