from typing import Optional

from pydantic import BaseModel


class AliasCreate(BaseModel):
    canonical_name: str
    platform: str
    platform_id: str
    platform_name: Optional[str] = None
