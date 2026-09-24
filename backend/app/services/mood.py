"""Scene mood for the current story point: drives the adaptive presentation, never the canon.

The interpreter may suggest a mood; a deterministic reading of the narration (dialogue
excluded) always produces one, so weak models still get a living interface.
"""

import re
from typing import Any

MOODS = ("calm", "tense", "danger", "combat", "mystery", "grief", "romance", "triumph", "eerie", "wonder")
CUES: dict[str, tuple[str, ...]] = {
    "combat": ("blade", "sword", "strike", "struck", "slash", "punch", "fight", "parry", "arrow", "swing", "lunge", "attack",
               "chain", "blood sprays", "clash"),
    "danger": ("blood", "fire", "burning", "flame", "scream", "threat", "knife", "trap", "chase", "boots on", "run",
               "falls", "wound", "pain", "alarm", "bell", "hunt", "legs", "dead"),
    "tense": ("silence", "stiff", "careful", "watching", "guard", "hand on", "narrow", "wait", "stare", "demand",
              "lie", "suspicious", "cold", "leash", "price"),
    "mystery": ("shadow", "hood", "whisper", "secret", "mask", "unknown", "ledger", "veiled", "strange", "hidden",
                "fog", "riddle", "rumor"),
    "eerie": ("chant", "hollow", "ghost", "wrong", "stillness", "unnatural", "cold light", "bones", "dark water"),
    "grief": ("tears", "mourn", "grave", "loss", "weep", "sorrow", "funeral", "gone forever"),
    "romance": ("kiss", "warm hand", "blush", "embrace", "tender", "heartbeat", "close to you"),
    "triumph": ("victory", "cheer", "you did it", "free at last", "triumph", "the crowd roars", "laugh"),
    "wonder": ("glitter", "vast", "stars", "light spills", "beautiful", "shimmer", "ancient", "gold"),
    "calm": ("quiet", "rest", "soft", "breathe", "peace", "gentle", "tea", "sleep", "warmth"),
}
QUOTED = re.compile(r"[\"“][^\"”]*[\"”]")


def read_mood(narration: str, *, suggested: Any = None, time_of_day: str = "") -> dict[str, Any]:
    text = QUOTED.sub(" ", narration or "").casefold()
    scores = {mood: sum(len(re.findall(rf"\b{re.escape(cue)}", text)) for cue in cues) for mood, cues in CUES.items()}
    scores["combat"] *= 1.3
    scores["danger"] *= 1.1
    best = max(scores, key=lambda mood: scores[mood])
    total = sum(scores.values()) or 1
    mood = best if scores[best] > 0 else "calm"
    intensity = round(min(1.0, 0.25 + scores[best] / max(total, 4) + scores[best] / 12), 2) if scores[best] else 0.2
    source = "narration"
    if isinstance(suggested, str) and suggested.strip().casefold() in MOODS:
        mood, source = suggested.strip().casefold(), "interpreter"
        intensity = max(intensity, 0.45)
    return {"mood": mood, "intensity": intensity, "time_of_day": time_of_day or "", "source": source}
