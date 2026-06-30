from datetime import datetime, timedelta, timezone
import re

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


def normalize_timestamp(value: object) -> str | None:
    """Normalize message timestamps to local naive ISO for string sorting."""
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        return _from_epoch(float(value))

    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return _from_epoch(float(s))

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s

    if dt.tzinfo is not None:
        dt = dt.astimezone(_LOCAL_TZ)
    return dt.replace(tzinfo=None).isoformat(timespec="seconds")


def add_time_filters(
    conditions: list[str],
    params: list,
    column: str,
    since: str | None,
    until: str | None,
) -> None:
    if since:
        conditions.append(f"{column} >= ?")
        params.append(_start_bound(since))
    if until:
        if _DATE_ONLY_RE.match(until):
            conditions.append(f"{column} < ?")
            params.append(_next_day_bound(until))
        else:
            conditions.append(f"{column} <= ?")
            params.append(normalize_timestamp(until) or until)


def _from_epoch(ts: float) -> str:
    if ts > 1e12:
        ts /= 1000
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .astimezone(_LOCAL_TZ)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )


def _start_bound(value: str) -> str:
    if _DATE_ONLY_RE.match(value):
        return f"{value}T00:00:00"
    return normalize_timestamp(value) or value


def _next_day_bound(value: str) -> str:
    day = datetime.fromisoformat(value)
    return (day + timedelta(days=1)).isoformat(timespec="seconds")
