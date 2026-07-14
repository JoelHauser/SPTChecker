from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_dt(ts_str):
    """Parse an ISO or RFC 2822 timestamp (RSS vs API formats) into an aware datetime."""
    if not ts_str:
        return None
    try:
        try:
            dt = parsedate_to_datetime(ts_str)
        except Exception:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
