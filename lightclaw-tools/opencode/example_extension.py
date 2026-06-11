"""Example lightclaw extension: date and time utilities.

Copy this file to ~/.config/lightclaw/extensions/datetime_tools.py
to install it, then call lightclaw_extension_load('datetime_tools.py').
"""

from __future__ import annotations

import datetime

from lightclaw.tools import tool


@tool(description=(
    "Return the current local date and time. "
    "format: strftime pattern, default is ISO 8601 with timezone."
))
def datetime_now(format: str = "%Y-%m-%dT%H:%M:%S %Z") -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().strftime(format)


@tool(description=(
    "Calculate the number of days between two ISO 8601 dates (YYYY-MM-DD). "
    "Returns a plain-English summary."
))
def datetime_days_between(date1: str, date2: str) -> str:
    try:
        d1 = datetime.date.fromisoformat(date1)
        d2 = datetime.date.fromisoformat(date2)
    except ValueError as exc:
        return f"Error parsing dates: {exc}"
    delta = (d2 - d1).days
    if delta == 0:
        return f"{date1} and {date2} are the same day."
    direction = "after" if delta > 0 else "before"
    return f"{date2} is {abs(delta)} day{'s' if abs(delta) != 1 else ''} {direction} {date1}."
