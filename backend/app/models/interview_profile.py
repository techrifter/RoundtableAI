from pydantic import BaseModel


class InterviewProfile(BaseModel):
    role: str
    skills: list[str]
    system_design_topics: list[str]
    responsibilities: list[str]
    candidate_highlights: list[str]
