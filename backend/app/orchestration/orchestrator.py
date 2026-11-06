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


@dataclass(frozen=True)
class StagePlan:
    """How long a round should run, and how hard to push on one topic.

    Time is the primary budget because this is an interview: the
    round should last as long as the role warrants, not end early because the
    candidate happened to be efficient. Turn bounds are guardrails around it, since
    elapsed time alone would let a silent candidate skip a round in seconds or a
    very talkative one run indefinitely.
    """

    min_seconds: int
    max_seconds: int
    min_turns: int
    max_turns: int
    topic_depth: int  # follow-ups allowed on a topic the candidate is strong in


# Scales every time budget below without touching the topic logic, for shortening a
# run locally. Turn bounds are deliberately unscaled, so the panel still works
# through real topics at any setting. Committed value is always 1.0.
TIME_SCALE = 1.0

# Targets: technical 15-20 min, system design 10-15, behavioral 5-7.
# Turn bounds assume roughly a minute per exchange (question, answer, turnaround).
STAGE_PLANS: dict[InterviewStage, StagePlan] = {
    InterviewStage.TECHNICAL: StagePlan(
        min_seconds=15 * 60, max_seconds=20 * 60, min_turns=8, max_turns=22, topic_depth=3
    ),
    InterviewStage.SYSTEM_DESIGN: StagePlan(
        min_seconds=10 * 60, max_seconds=15 * 60, min_turns=6, max_turns=16, topic_depth=3
    ),
    InterviewStage.BEHAVIORAL: StagePlan(
        min_seconds=5 * 60, max_seconds=7 * 60, min_turns=4, max_turns=9, topic_depth=2
    ),
}

# A weak answer ends the current topic immediately, but it takes this many in a row
# to end the whole round early. Drilling something the candidate plainly doesn't
# know wastes the round; one weak answer somewhere is just a gap, not a verdict.
LOW_ENGAGEMENT_STREAK_TO_ADVANCE = 3

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
    focus_topic: str | None  # topic to keep drilling, or None to pick a fresh one
    covered_topics: list[str]  # topics already worked through in the current round


def _candidate_answers(messages: list[dict]) -> list[str]:
    """Real answers only. The greeting turn arrives with empty ASR content, and an
    empty string would otherwise burn a turn and score as low engagement on the
    word-count check. Timestamps in api/llm.py are counted the same way, so the two
    lists stay index-aligned.
    """
    return [
        text
        for m in messages
        if m.get("role") == "user" and (text := (m.get("content") or "").strip())
    ]


def _next_topic(topic_pool: list[str], done: set[str]) -> str | None:
    return next((t for t in topic_pool if t not in done), None)


def _advance_stage_machine(
    profile: InterviewProfile,
    candidate_answers: list[str],
    answer_times: list[float] | None = None,
) -> StageState:
    """Replays the candidate's answers in order to compute the current stage and
    which topic the panel should be on.

    Two levers per round. Depth: a strong answer on a topic earns follow-ups on that
    same topic, up to the plan's topic_depth, then the round moves to the next topic
    in the pool. A weak answer ends that topic immediately, since drilling something
    the candidate doesn't know teaches nobody anything. Breadth: the round ends once
    its time budget is spent, or the topic pool is exhausted past the minimum.

    `answer_times` is one wall-clock timestamp per answer. When absent (backend
    restarted mid-interview, so the timestamps are gone) the turn bounds still hold
    and the round runs to max_turns instead of a clock.
    """
    times = answer_times or []

    stage_idx = 0
    turns_in_stage = 0
    stage_start_turn = 0
    low_engagement_streak = 0
    weak_topics: list[str] = []
    strong_topics: list[str] = []

    focus_topic: str | None = None
    turns_on_focus = 0
    covered: set[str] = set()

    for turn_index, answer in enumerate(candidate_answers):
        if stage_idx >= len(STAGE_ORDER):
            break  # already in FINAL_EVAL, nothing left to advance through

        stage = STAGE_ORDER[stage_idx]
        plan = STAGE_PLANS[stage]
        topic_pool = _topic_pool_for(stage, profile)
        signal = analyze_answer(answer, topic_pool)

        turns_in_stage += 1

        # Whatever they actually talked about counts as covered, even if the panel
        # had aimed somewhere else — candidates lead the conversation sometimes.
        covered |= signal.matched_topics
        if focus_topic is None:
            focus_topic = next(iter(signal.matched_topics), None) or _next_topic(topic_pool, covered)
            turns_on_focus = 0
        if focus_topic is not None:
            covered.add(focus_topic)

        turns_on_focus += 1

        if signal.is_low_engagement:
            low_engagement_streak += 1
            weak_topics.extend(signal.matched_topics or [focus_topic or f"{stage.value} (turn {turns_in_stage})"])
            focus_topic = None  # they don't know this one; stop drilling it
        else:
            low_engagement_streak = 0
            strong_topics.extend(signal.matched_topics or ([focus_topic] if focus_topic else []))
            if turns_on_focus >= plan.topic_depth:
                focus_topic = None  # drilled deep enough, take a fresh topic

        if focus_topic is None:
            focus_topic = _next_topic(topic_pool, covered)
            turns_on_focus = 0

        elapsed = (times[turn_index] - times[stage_start_turn]) if turn_index < len(times) else None

        out_of_time = elapsed is not None and elapsed >= plan.max_seconds * TIME_SCALE
        out_of_turns = turns_in_stage >= plan.max_turns
        past_minimum = turns_in_stage >= plan.min_turns and (
            elapsed is None or elapsed >= plan.min_seconds * TIME_SCALE
        )
        nothing_left = focus_topic is None and not _next_topic(topic_pool, covered)

        # Sustained silence ends the round regardless of its time budget.
        disengaged = low_engagement_streak >= LOW_ENGAGEMENT_STREAK_TO_ADVANCE

        should_advance = out_of_time or out_of_turns or disengaged or (past_minimum and nothing_left)

        if should_advance:
            stage_idx += 1
            turns_in_stage = 0
            stage_start_turn = turn_index + 1
            low_engagement_streak = 0
            focus_topic = None
            turns_on_focus = 0
            covered = set()

    if stage_idx >= len(STAGE_ORDER):
        return StageState(InterviewStage.FINAL_EVAL, stage_start_turn, weak_topics, strong_topics, None, [])

    stage = STAGE_ORDER[stage_idx]
    if focus_topic is None:
        focus_topic = _next_topic(_topic_pool_for(stage, profile), covered)
    return StageState(stage, stage_start_turn, weak_topics, strong_topics, focus_topic, sorted(covered))


def resolve_focused_stage(target_skill: str | None, profile: InterviewProfile) -> InterviewStage:
    """Maps a focused skill to the technical or system-design interviewer."""
    if not target_skill:
        return InterviewStage.TECHNICAL

    target_lower = target_skill.lower().strip()

    # Check system design topics or keywords
    if any(topic.lower() in target_lower or target_lower in topic.lower() for topic in profile.system_design_topics):
        return InterviewStage.SYSTEM_DESIGN
    if any(kw in target_lower for kw in ["system design", "architecture", "distributed", "scalability", "caching", "database design", "microservices"]):
        return InterviewStage.SYSTEM_DESIGN

    return InterviewStage.TECHNICAL


def _advance_focused_machine(
    profile: InterviewProfile,
    candidate_answers: list[str],
    target_skill: str | None,
    answer_times: list[float] | None = None,
) -> StageState:
    """State machine for a 5-15 minute single-skill Focused Practice drill.
    Runs for 3-5 substantive turns on the chosen target skill, providing instant
    coaching follow-ups before transitioning to FINAL_EVAL.
    """
    stage = resolve_focused_stage(target_skill, profile)
    focus = target_skill or (_topic_pool_for(stage, profile)[0] if _topic_pool_for(stage, profile) else "General")

    weak_topics: list[str] = []
    strong_topics: list[str] = []
    low_engagement_streak = 0

    for answer in candidate_answers:
        signal = analyze_answer(answer, [focus])
        if signal.is_low_engagement:
            low_engagement_streak += 1
            weak_topics.append(focus)
        else:
            low_engagement_streak = 0
            strong_topics.append(focus)

    turns = len(candidate_answers)
    times = answer_times or []
    elapsed = (times[-1] - times[0]) if len(times) >= 2 else 0

    # End drill when 5 questions are completed or 10 minutes elapsed
    # or candidate disengaged repeatedly
    if turns >= 5 or (turns >= 3 and elapsed >= 10 * 60) or low_engagement_streak >= 2:
        return StageState(InterviewStage.FINAL_EVAL, 0, weak_topics, strong_topics, focus, [focus])

    return StageState(stage, 0, weak_topics, strong_topics, focus, [focus])


def collect_interview_evidence(
    profile: InterviewProfile,
    messages: list[dict],
    mode: str = "full",
    target_skill: str | None = None,
) -> tuple[list[str], list[str]]:
    """Weak/strong topics accumulated across the interview, for the final report to cite."""
    answers = _candidate_answers(messages)
    if mode == "focused":
        state = _advance_focused_machine(profile, answers, target_skill)
    else:
        state = _advance_stage_machine(profile, answers)
    return state.weak_topics, state.strong_topics


PRIOR_NOTES_LIMIT = 3  # cap, not "all of it" — see docstring


def extract_prior_notes(candidate_answers: list[str], stage_start_turn: int) -> list[str]:
    """A few candidate utterances from before this stage, so the next persona can
    reference something said earlier.

    Capped deliberately: passing every earlier answer would hand the next persona a
    list dominated by whatever the last stage over-focused on.

    Sampled evenly across the earlier answers rather than taking the most recent
    few. A tail slice drops the opening answers, and the claim worth calling back
    to ("we ran Kafka in production") is usually made early — by the Behavioral
    round a tail slice contains nothing but System Design.
    """
    earlier = [text for text in candidate_answers[:stage_start_turn] if text and text.strip()]
    if len(earlier) <= PRIOR_NOTES_LIMIT:
        return earlier

    step = (len(earlier) - 1) / (PRIOR_NOTES_LIMIT - 1)
    return [earlier[round(i * step)] for i in range(PRIOR_NOTES_LIMIT)]


def build_stage_system_prompt(
    profile: InterviewProfile,
    candidate_answers: list[str],
    answer_times: list[float] | None = None,
    mode: str = "full",
    target_skill: str | None = None,
) -> tuple[InterviewStage, str]:
    if mode == "focused":
        state = _advance_focused_machine(profile, candidate_answers, target_skill, answer_times)
        if state.stage == InterviewStage.FINAL_EVAL:
            skill_name = target_skill or "this skill"
            return state.stage, (
                f"The focused drill on {skill_name} is now complete. "
                "Say this exact goodbye sentence, with no additional words: "
                f'"That wraps up our focused practice drill on {skill_name}! Great practice—check your report for targeted feedback and takeaways."'
            )

        focus = state.focus_topic
        strong = state.strong_topics

        if state.stage == InterviewStage.SYSTEM_DESIGN:
            base_prompt = build_system_design_persona_prompt(profile, [], strong, focus)
        elif state.stage == InterviewStage.BEHAVIORAL:
            base_prompt = build_behavioral_persona_prompt(profile, [], strong, focus)
        else:
            base_prompt = build_technical_persona_prompt(profile, [], strong, focus)

        drill_directive = (
            f"\n\n[FOCUSED PRACTICE DRILL ON: {focus}]\n"
            "This is a high-yield, 5-to-15 minute Focused Practice session. "
            f"Focus all questions directly on {focus}. "
            "After the candidate answers, include a 1-sentence real-time spoken coaching reaction "
            "(e.g. 'Good distinction on immutability' or 'Keep in mind edge cases around network timeouts') "
            "and immediately ask your next question or a targeted follow-up. Keep the momentum high."
        )
        return state.stage, f"{base_prompt} {drill_directive}"

    state = _advance_stage_machine(profile, candidate_answers, answer_times)
    prior_notes = extract_prior_notes(candidate_answers, state.stage_start_turn)

    # strong_topics is already accumulated across every earlier stage by the stage
    # machine; handing it to the persona turns "spot the connection in this raw text"
    # into an explicit list of what the candidate has actually demonstrated.
    strong = state.strong_topics

    focus = state.focus_topic

    if state.stage == InterviewStage.TECHNICAL:
        return state.stage, build_technical_persona_prompt(profile, prior_notes, strong, focus)
    if state.stage == InterviewStage.SYSTEM_DESIGN:
        return state.stage, build_system_design_persona_prompt(profile, prior_notes, strong, focus)
    if state.stage == InterviewStage.BEHAVIORAL:
        return state.stage, build_behavioral_persona_prompt(profile, prior_notes, strong, focus)
    return state.stage, (
        "The interview is now over. Say this exact closing sentence, with no additional words: "
        f'"{CLOSING_MESSAGE}"'
    )
