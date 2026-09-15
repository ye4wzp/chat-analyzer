from fastapi import APIRouter, HTTPException

from app.models.ask import AskRequest
from app.services.budget import TokenBudgetExceeded

router = APIRouter()


@router.post("/api/ask")
async def ask(body: AskRequest):
    from app.services.ask import answer_question

    question = body.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    try:
        return await answer_question(question, [t.model_dump() for t in body.history])
    except TokenBudgetExceeded as e:
        raise HTTPException(429, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
