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
    tag_enabled: Optional[bool] = None
    tag_interval_minutes: Optional[int] = None
    tag_max_contacts: Optional[int] = None
    todo_remind_enabled: Optional[bool] = None
    todo_remind_interval_minutes: Optional[int] = None
    todo_remind_min_urgency: Optional[int] = None
    report_weekly_enabled: Optional[bool] = None
    report_monthly_enabled: Optional[bool] = None
