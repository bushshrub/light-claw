"""Productivity tools: calendar events, todo list, weather."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite

from lightclaw.config import get_config
from lightclaw.tools.registry import get_default_registry

_reg = get_default_registry()

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS calendar_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    start_time  TEXT    NOT NULL,
    end_time    TEXT,
    description TEXT    NOT NULL DEFAULT '',
    ts          REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT    NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    priority    TEXT    NOT NULL DEFAULT 'normal',
    due         TEXT,
    ts          REAL    NOT NULL
);
"""


@asynccontextmanager
async def _db():
    path = get_config().db_path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(_SCHEMA)
        await db.commit()
        yield db


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@_reg.tool(description=(
    "Add a calendar event. start_time and end_time are ISO 8601 strings "
    "(e.g. '2025-06-10T14:00'). end_time is optional."
))
async def calendar_add(
    title: str,
    start_time: str,
    end_time: str = "",
    description: str = "",
) -> str:
    async with _db() as db:
        cur = await db.execute(
            "INSERT INTO calendar_events(title, start_time, end_time, description, ts) VALUES (?,?,?,?,?)",
            (title, start_time, end_time or None, description, time.time()),
        )
        await db.commit()
        return f"Event #{cur.lastrowid} created: {title} @ {start_time}"


@_reg.tool(description=(
    "List calendar events. Optionally filter by from_date/to_date (YYYY-MM-DD or ISO 8601). "
    "Defaults to upcoming events from now."
))
async def calendar_list(from_date: str = "", to_date: str = "") -> list[dict]:
    async with _db() as db:
        if from_date and to_date:
            end = to_date + "T23:59:59" if len(to_date) == 10 else to_date
            cur = await db.execute(
                "SELECT id, title, start_time, end_time, description FROM calendar_events "
                "WHERE start_time >= ? AND start_time <= ? ORDER BY start_time LIMIT 50",
                (from_date, end),
            )
        elif from_date:
            cur = await db.execute(
                "SELECT id, title, start_time, end_time, description FROM calendar_events "
                "WHERE start_time >= ? ORDER BY start_time LIMIT 50",
                (from_date,),
            )
        else:
            now = datetime.now().isoformat(timespec="minutes")
            cur = await db.execute(
                "SELECT id, title, start_time, end_time, description FROM calendar_events "
                "WHERE start_time >= ? ORDER BY start_time LIMIT 50",
                (now,),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


@_reg.tool(description="Delete a calendar event by id.")
async def calendar_delete(id: int) -> str:
    async with _db() as db:
        cur = await db.execute("DELETE FROM calendar_events WHERE id=?", (id,))
        await db.commit()
        return f"Deleted event #{id}" if cur.rowcount else f"Event #{id} not found"


@_reg.tool(description="Update a calendar event by id. Only provided (non-empty) fields are changed.")
async def calendar_update(
    id: int,
    title: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
) -> str:
    fields = [(f, v) for f, v in [
        ("title", title), ("start_time", start_time),
        ("end_time", end_time), ("description", description),
    ] if v]
    if not fields:
        return "No fields to update"
    set_clause = ", ".join(f"{f}=?" for f, _ in fields)
    values = [v for _, v in fields] + [id]
    async with _db() as db:
        cur = await db.execute(
            f"UPDATE calendar_events SET {set_clause} WHERE id=?", values
        )
        await db.commit()
        return f"Updated event #{id}" if cur.rowcount else f"Event #{id} not found"


# ---------------------------------------------------------------------------
# Todo list
# ---------------------------------------------------------------------------

@_reg.tool(description=(
    "Add a todo item. priority is 'low', 'normal', or 'high'. "
    "due is an optional ISO date string (e.g. '2025-06-15')."
))
async def todo_add(text: str, priority: str = "normal", due: str = "") -> str:
    if priority not in ("low", "normal", "high"):
        return "priority must be 'low', 'normal', or 'high'"
    async with _db() as db:
        cur = await db.execute(
            "INSERT INTO todos(text, done, priority, due, ts) VALUES (?,0,?,?,?)",
            (text, priority, due or None, time.time()),
        )
        await db.commit()
        return f"Todo #{cur.lastrowid} added: {text}"


@_reg.tool(description=(
    "List todo items. filter: 'pending' (default), 'done', or 'all'. "
    "Pending items sorted by priority (high first). "
    "Returns id, text, done, priority, due."
))
async def todo_list(filter: str = "pending") -> list[dict]:
    async with _db() as db:
        if filter == "done":
            cur = await db.execute(
                "SELECT id, text, done, priority, due FROM todos WHERE done=1 ORDER BY ts DESC LIMIT 100"
            )
        elif filter == "all":
            cur = await db.execute(
                "SELECT id, text, done, priority, due FROM todos ORDER BY ts DESC LIMIT 100"
            )
        else:
            cur = await db.execute(
                "SELECT id, text, done, priority, due FROM todos WHERE done=0 "
                "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, ts LIMIT 100"
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


@_reg.tool(description="Mark a todo item as done by id.")
async def todo_complete(id: int) -> str:
    async with _db() as db:
        cur = await db.execute("UPDATE todos SET done=1 WHERE id=?", (id,))
        await db.commit()
        return f"Todo #{id} marked done" if cur.rowcount else f"Todo #{id} not found"


@_reg.tool(description="Delete a todo item by id.")
async def todo_delete(id: int) -> str:
    async with _db() as db:
        cur = await db.execute("DELETE FROM todos WHERE id=?", (id,))
        await db.commit()
        return f"Deleted todo #{id}" if cur.rowcount else f"Todo #{id} not found"


@_reg.tool(description="Update a todo item by id. Only provided (non-empty) fields are changed.")
async def todo_update(
    id: int,
    text: str = "",
    priority: str = "",
    due: str = "",
) -> str:
    if priority and priority not in ("low", "normal", "high"):
        return "priority must be 'low', 'normal', or 'high'"
    fields = [(f, v) for f, v in [
        ("text", text), ("priority", priority), ("due", due),
    ] if v]
    if not fields:
        return "No fields to update"
    set_clause = ", ".join(f"{f}=?" for f, _ in fields)
    values = [v for _, v in fields] + [id]
    async with _db() as db:
        cur = await db.execute(f"UPDATE todos SET {set_clause} WHERE id=?", values)
        await db.commit()
        return f"Updated todo #{id}" if cur.rowcount else f"Todo #{id} not found"


# ---------------------------------------------------------------------------
# Weather  (wttr.in, no API key required)
# ---------------------------------------------------------------------------

@_reg.tool(description=(
    "Get current weather and a 3-day forecast for a location. "
    "location can be a city name, zip code, or 'lat,lon' coordinates. "
    "No API key required."
))
async def weather(location: str) -> str:
    try:
        import httpx
    except ImportError:
        return "httpx not installed — run: uv add httpx"

    url = f"https://wttr.in/{location}?format=j1"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "lightclaw/0.1"})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return f"Weather API error: HTTP {exc.response.status_code}"
    except Exception as exc:
        return f"Weather fetch error: {exc}"

    try:
        current = data["current_condition"][0]
        area = data["nearest_area"][0]
        area_name = area["areaName"][0]["value"]
        country = area["country"][0]["value"]

        lines = [
            f"**{area_name}, {country}**",
            (
                f"Now: {current['weatherDesc'][0]['value']}, "
                f"{current['temp_C']}°C / {current['temp_F']}°F "
                f"(feels like {current['FeelsLikeC']}°C / {current['FeelsLikeF']}°F)"
            ),
            f"Humidity: {current['humidity']}%  Wind: {current['windspeedKmph']} km/h {current['winddir16Point']}",
            "",
            "**3-day forecast:**",
        ]
        for day in data.get("weather", []):
            midday_desc = day["hourly"][4]["weatherDesc"][0]["value"] if day.get("hourly") else "N/A"
            lines.append(
                f"  {day['date']}: {midday_desc}, "
                f"{day['mintempC']}–{day['maxtempC']}°C / {day['mintempF']}–{day['maxtempF']}°F"
            )
        return "\n".join(lines)
    except (KeyError, IndexError) as exc:
        return f"Failed to parse weather response: {exc}"
