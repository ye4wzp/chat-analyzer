from pydantic import BaseModel


class AskTurn(BaseModel):
    question: str
    answer: str


class AskRequest(BaseModel):
    question: str
    history: list[AskTurn] = []  # recent Q/A pairs for follow-up context
