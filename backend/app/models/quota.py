from pydantic import BaseModel

class UserMonthlyQuota(BaseModel):
    period: str  # Format: "YYYY-MM"
    limit: int = 2
    used: int = 0
