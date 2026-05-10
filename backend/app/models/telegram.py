from typing import Optional

from pydantic import BaseModel


class TelegramLoginStart(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class TelegramLoginConfirm(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None
