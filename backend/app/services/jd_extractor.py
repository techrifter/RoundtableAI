import json

from app.config import settings
from app.models.interview_profile import InterviewProfile
from app.services.http_client import get_http_client

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

EXTRACTION_SYSTEM_PROMPT = """You turn a job description and/or a candidate's resume into a structured \
interview profile. Respond with a single JSON object only, no prose, matching exactly this shape:

{
  "role": "string, the job title",
  "skills": ["string", ...],            // technical skills required by the JD
  "system_design_topics": ["string", ...],  // architecture/system-design areas relevant to this role
  "responsibilities": ["string", ...],  // key responsibilities from the JD
  "candidate_highlights": ["string", ...]   // notable claims/experience from the RESUME worth probing in an interview
}

Keep each list to at most 8 concise items. Base skills/responsibilities/system_design_topics on the \
JOB DESCRIPTION, and candidate_highlights on the RESUME, when each is provided. If the job description is \
missing, infer a realistic role, skills, responsibilities, and system design topics from the resume itself, \
as if writing the JD for the role this candidate is best suited to interview for next. If the resume is \
missing, leave candidate_highlights as an empty list rather than inventing claims about someone you have no \
information on."""


async def extract_interview_profile(jd_text: str | None, resume_text: str | None) -> InterviewProfile:
    if not (jd_text or "").strip() and not (resume_text or "").strip():
        raise ValueError("Need at least a job description or a resume to build an interview profile.")

    jd_section = f"JOB DESCRIPTION:\n{jd_text}" if jd_text and jd_text.strip() else "JOB DESCRIPTION: (not provided)"
    resume_section = (
        f"RESUME:\n{resume_text}" if resume_text and resume_text.strip() else "RESUME: (not provided)"
    )

    client = get_http_client()
    response = await client.post(
        GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"{jd_section}\n\n{resume_section}"},
            ],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "low",
        },
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return InterviewProfile.model_validate(json.loads(content))
