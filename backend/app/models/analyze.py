from typing import Optional

from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    chat: Optional[str] = None
    chats: Optional[list[str]] = None
    platform: Optional[str] = None
    chat_id: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = 100
    full: bool = False  # if True, ignore incremental and analyze all


class ConfirmRequest(BaseModel):
    ids: list[int]  # indices into pending results to save


class ExtendRequest(BaseModel):
    id: int
