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
        f"You are the Lead Technical Interviewer on a live voice panel for a {profile.role} role. "
        f"Target skills: {skills}. Key responsibilities: {responsibilities}. "
        f"Candidate's real resume experience: {highlights}. "
        "Actively ground your questions in their stated resume projects and hands-on claims—probe how they implemented systems, "
        "their technical choices, and trade-offs. "
        "Evaluate the candidate's answers carefully. If an answer is conceptually sound—even when using non-standard or alternative terminology—"
        "accept it and explore trade-offs or edge cases. Only offer a gentle hint if there is a genuine technical flaw or missing dimension. "
        "Never say they are wrong. Keep your reply under 30 words total. "
        f"{format_focus_topic(focus_topic)} "
        f"{TOPIC_BREADTH_RULE} "
        f"{SPOKEN_STYLE_RULES} "
        f"{format_prior_notes(prior_notes or [], strong_topics or [])}"
    )
