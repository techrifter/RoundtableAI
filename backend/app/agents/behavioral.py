from app.agents.style import (
    SPOKEN_STYLE_RULES,
    format_prior_notes,
)
from app.models.interview_profile import InterviewProfile

# Job responsibilities are mostly technical tasks ("Implement lending workflows"), so
# this round uses the topic only as the setting for a question about how the candidate
# acted, never as the thing to ask about.
BEHAVIORAL_FOLLOW_UP_RULE = (
    "Stay on this situation until you are moved off it. Each follow-up should go one level deeper into "
    "what they did, why they did it, what happened, or what they would do differently. Never ask how "
    "something is built, designed, or implemented, and never ask about tools, code, or architecture, "
    "even when you refer back to something they said earlier. The other interviewers already covered that."
)


def _behavioral_focus(topic: str | None) -> str:
    if not topic:
        return ""
    return (
        f"Right now, set your question in this part of the job: {topic}. Ask about a real situation they "
        "faced there, like owning a problem, a disagreement, a tight deadline, or a mistake, not about how "
        "the work was built."
    )


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
        "working under pressure. Ask one question at a time, listen to their spoken answer, and ask a "
        "natural follow-up about what they just said. "
        f"{_behavioral_focus(focus_topic)} "
        f"{BEHAVIORAL_FOLLOW_UP_RULE} "
        f"{SPOKEN_STYLE_RULES} "
        f"{format_prior_notes(prior_notes or [], strong_topics or [])}"
    )
