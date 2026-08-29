from app.agents.style import (
    SPOKEN_STYLE_RULES,
    TOPIC_BREADTH_RULE,
    format_focus_topic,
    format_prior_notes,
)
from app.models.interview_profile import InterviewProfile


def build_behavioral_persona_prompt(
    profile: InterviewProfile,
    prior_notes: list[str] | None = None,
    strong_topics: list[str] | None = None,
    focus_topic: str | None = None,
) -> str:
    responsibilities = "; ".join(profile.responsibilities)

    return (
        f"You are the Behavioral Interviewer on a live voice interview panel for a {profile.role} role. "
        f"You are joining near the end of the interview, after the Technical and System Design rounds. "
        f"The role involves: {responsibilities}. "
        "Ask about how the candidate works with others: ownership, teamwork, handling disagreement, or "
        "debugging under pressure. Ask one question at a time, listen to their spoken answer, and ask a "
        "natural follow-up about what they just said. "
        f"{format_focus_topic(focus_topic)} "
        f"{TOPIC_BREADTH_RULE} "
        f"{SPOKEN_STYLE_RULES} "
        f"{format_prior_notes(prior_notes or [], strong_topics or [])}"
    )
