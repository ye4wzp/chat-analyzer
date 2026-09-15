from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    period: str = "weekly"  # 'weekly' | 'monthly' | 'yearly'
    year: int | None = None  # yearly only: calendar year to cover (default: trailing 365 days)
