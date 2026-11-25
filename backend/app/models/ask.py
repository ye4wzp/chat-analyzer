from datetime import datetime
from pydantic import BaseModel, field_validator


class AskTurn(BaseModel):
    question: str
    answer: str


class AskRequest(BaseModel):
    question: str
    history: list[AskTurn] = []  # recent Q/A pairs for follow-up context
    platform: str | None = None
    chat_id: str | None = None
    since: str | None = None
    until: str | None = None

    @field_validator("since", "until")
    @classmethod
    def valid_date(cls, value):
        if value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
