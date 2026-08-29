from app.agents.style import (
    SPOKEN_STYLE_RULES,
    TOPIC_BREADTH_RULE,
    format_focus_topic,
    format_prior_notes,
)
from app.models.interview_profile import InterviewProfile


def build_technical_persona_prompt(
    profile: InterviewProfile,
    prior_notes: list[str] | None = None,
    strong_topics: list[str] | None = None,
    focus_topic: str | None = None,
) -> str:
    skills = ", ".join(profile.skills)
    responsibilities = "; ".join(profile.responsibilities)
    highlights = "; ".join(profile.candidate_highlights)

    return (
        f"You are the Technical Interviewer on a live voice interview panel for a {profile.role} role. "
        f"The job needs these skills: {skills}. Key responsibilities: {responsibilities}. "
        f"The candidate's resume highlights: {highlights}. "
        "Ask one focused technical question at a time, listen to their spoken answer, and ask a natural "
        "follow-up tied to what they just said or to a resume highlight. "
        f"{format_focus_topic(focus_topic)} "
        f"{TOPIC_BREADTH_RULE} "
        f"{SPOKEN_STYLE_RULES} "
        f"{format_prior_notes(prior_notes or [], strong_topics or [])}"
    )
