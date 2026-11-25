from pydantic import BaseModel
from typing import Literal


class SyncRequest(BaseModel):
    new_only: bool = False
    mode: Literal["incremental", "full", "repair"] | None = None


class QQSyncRequest(SyncRequest):
    chat_ids: list[str] | None = None


class ImportRequest(BaseModel):
    path: str
