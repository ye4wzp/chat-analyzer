from pydantic import BaseModel


class SyncRequest(BaseModel):
    new_only: bool = False


class ImportRequest(BaseModel):
    path: str
