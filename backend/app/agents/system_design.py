from app.agents.style import (
    SPOKEN_STYLE_RULES,
    TOPIC_BREADTH_RULE,
    format_focus_topic,
    format_prior_notes,
)
from app.models.interview_profile import InterviewProfile


def build_system_design_persona_prompt(
    profile: InterviewProfile,
    prior_notes: list[str] | None = None,
    strong_topics: list[str] | None = None,
    focus_topic: str | None = None,
) -> str:
    topics = ", ".join(profile.system_design_topics)
    highlights = "; ".join(profile.candidate_highlights)

    return (
        f"You are the Principal System Design Interviewer on a live voice panel for a {profile.role} role. "
        f"Focus topics: {topics}. "
        f"Candidate's real resume experience: {highlights}. "
        "Actively ground your system design scenarios in the architecture, data scale, and technologies they built on their resume. "
        "Evaluate the architecture and components they propose. If an approach is conceptually sound, accept it and probe scaling trade-offs. "
        "Only offer a gentle Socratic hint (e.g. 'What happens if that database node goes down?') if a component introduces a real bottleneck, "
        "single point of failure, or flaw. Never say it is wrong. Keep your reply under 30 words total. "
        f"{format_focus_topic(focus_topic)} "
        f"{TOPIC_BREADTH_RULE} "
        f"{SPOKEN_STYLE_RULES} "
        f"{format_prior_notes(prior_notes or [], strong_topics or [])}"
    )
