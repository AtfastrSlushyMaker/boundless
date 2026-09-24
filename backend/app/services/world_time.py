"""Relative in-world time: elapsed seconds plus a readable day and time of day."""

import re
from typing import Any

PERIODS = [  # (start minute, label)
    (0, "late night"), (4 * 60, "before dawn"), (5 * 60 + 30, "dawn"), (7 * 60, "morning"),
    (11 * 60 + 30, "midday"), (13 * 60 + 30, "afternoon"), (17 * 60 + 30, "evening"),
    (20 * 60 + 30, "night"),
]
# Phrases that state the time of day outright. Weak hints ("night air") only anchor a clock
# that has never been set; they never move an established clock.
STRONG_CUES = [
    (r"\b(?:dawn breaks|at dawn|first light|the sun rises|sunrise)\b", 6 * 60),
    (r"\b(?:morning comes|this morning|in the morning|by morning)\b", 8 * 60),
    (r"\b(?:at noon|by noon|midday sun|high noon)\b", 12 * 60),
    (r"\b(?:the sun sets|sunset|at dusk|dusk falls)\b", 19 * 60),
    (r"\b(?:night falls|nightfall)\b", 21 * 60),
    (r"\b(?:at midnight|midnight)\b", 0),
]
WEAK_CUES = [
    (r"\b(?:dawn|sunrise)\b", 6 * 60), (r"\bmorning\b", 8 * 60), (r"\b(?:noon|midday)\b", 12 * 60),
    (r"\bafternoon\b", 15 * 60), (r"\b(?:dusk|sunset|evening)\b", 19 * 60),
    (r"\b(?:night|torchlight|moonlight|moon|stars|lantern)\b", 22 * 60),
]


def period_for(minute: int) -> str:
    label = PERIODS[0][1]
    for start, name in PERIODS:
        if minute >= start:
            label = name
    return label


def _label(clock: dict[str, Any]) -> str:
    period = clock.get("time_of_day") or "time unknown"
    return f"{period[:1].upper()}{period[1:]}, Day {clock.get('day', 1)}"


def initial_clock() -> dict[str, Any]:
    return {"day": 1, "minute_of_day": None, "time_of_day": "", "clock": None, "elapsed_seconds": 0,
            "label": "Day 1"}


def advance(state: dict[str, Any], seconds: int, narration: str = "", explicit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a new world_clock after ``seconds`` pass and the narration's time cues apply."""
    clock = dict(state.get("world_clock") or initial_clock())
    seconds = max(0, min(int(seconds or 0), 31_536_000))
    clock["elapsed_seconds"] = int(clock.get("elapsed_seconds") or state.get("elapsed_seconds") or 0) + seconds
    minute = clock.get("minute_of_day")
    day = int(clock.get("day") or 1)
    if isinstance(minute, int):
        total = minute + seconds // 60
        day += total // 1440
        minute = total % 1440
    # Time cues come from narration, not dialogue: "you'll be dead by morning" is not a clock.
    text = re.sub(r"[\"“][^\"”]*[\"”]", " ", narration).casefold()
    strong = next((value for pattern, value in STRONG_CUES if re.search(pattern, text)), None)
    if strong is not None:
        if minute is None:
            minute = strong
        else:
            forward = (strong - minute) % 1440
            # A big jump needs the turn to have taken real time; a short beat can only nudge the clock.
            if forward <= (14 * 60 if seconds >= 1800 else 3 * 60):
                day += 1 if strong < minute and forward else 0
                minute = strong
    elif minute is None:
        weak = next((value for pattern, value in WEAK_CUES if re.search(pattern, text)), None)
        if weak is not None:
            minute = weak
    explicit = explicit or {}
    if isinstance(explicit.get("day"), int) and explicit["day"] >= day:
        day = explicit["day"]
    if isinstance(explicit.get("time_of_day"), str) and explicit["time_of_day"].strip():
        wanted = explicit["time_of_day"].strip().casefold()
        match = next((start for start, name in PERIODS if name == wanted), None)
        if match is not None:
            minute = match
    clock.update({"day": day, "minute_of_day": minute,
                  "time_of_day": period_for(minute) if isinstance(minute, int) else clock.get("time_of_day", ""),
                  "clock": f"{minute // 60:02d}:{minute % 60:02d}" if isinstance(minute, int) and explicit.get("exact_clock") else None})
    label = explicit.get("label")
    clock["label"] = str(label).strip()[:120] if isinstance(label, str) and label.strip() and not label.strip().casefold() == "opening" else _label(clock)
    return clock
