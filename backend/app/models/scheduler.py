from typing import Optional

from pydantic import BaseModel


class SchedulerUpdate(BaseModel):
    sync_enabled: Optional[bool] = None
    sync_interval_minutes: Optional[int] = None
    analyze_enabled: Optional[bool] = None
    analyze_interval_minutes: Optional[int] = None
    qq_enabled: Optional[bool] = None
    qq_interval_minutes: Optional[int] = None
    telegram_enabled: Optional[bool] = None
    telegram_interval_minutes: Optional[int] = None
