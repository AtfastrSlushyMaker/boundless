import re

from app.schemas import CampaignConstitution, ThemeProfile


def infer_title(prompt: str) -> str:
    match = re.search(r"\bI am\s+(?:called\s+|named\s+)?([A-Z][\w'-]+)", prompt)
    if match:
        return f"{match.group(1)}'s world"
    first = re.split(r"[.!?\n]", prompt.strip(), maxsplit=1)[0]
    return (first[:74].rstrip(" ,;:") + ("…" if len(first) > 74 else "")) or "Untitled world"


def infer_player(prompt: str) -> str:
    match = re.search(r"\bI am\s+(?:called\s+|named\s+)?([A-Z][\w'-]+)", prompt)
    return match.group(1) if match else "You"


def _sentences(prompt: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", prompt) if part.strip()]


def derive_constitution(prompt: str) -> CampaignConstitution:
    text = prompt.strip()
    lower = text.casefold()
    genre = "Open world"
    for key, value in (
        ("cyberpunk", "Cyberpunk"), ("science fiction", "Science fiction"), ("sci-fi", "Science fiction"),
        ("high fantasy", "High fantasy"), ("fantasy", "Fantasy"), ("zombie", "Survival horror"),
        ("horror", "Horror"), ("mystery", "Mystery"), ("romance", "Romance"),
        ("political", "Political intrigue"), ("war", "War"),
    ):
        if key in lower:
            genre = value
            break

    hard_invariants: list[dict] = []
    mortality_rules: list[str] = []
    if re.search(r"\bimmortal\b|cannot (?:be )?(?:permanently )?killed|nothing can permanently kill", lower):
        exceptions: list[str] = []
        if "no exception" not in lower and "no known or unknown exception" not in lower:
            exception_match = re.search(r"\b(?:except|unless)\s+([^.!?]+)", text, re.IGNORECASE)
            if exception_match:
                exceptions = [exception_match.group(1).strip()]
        hard_invariants.append({
            "type": "PLAYER_CANNOT_DIE",
            "strength": "HARD",
            "exceptions": exceptions,
            "source": "campaign_setup",
        })
        mortality_rules.append("The player cannot permanently die." if not exceptions else f"The player cannot die except: {exceptions[0]}")
    elif re.search(r"\b(can die|may die|mortal|normal human|no plot armor)\b", lower):
        mortality_rules.append("The player is mortal; serious consequences, including death, are possible.")

    hidden_permissions: list[str] = []
    if re.search(r"(?:may|might|could) be (?:a |one |exactly one )?(?:hidden )?(?:way|method|weakness)|i believe there may be", lower):
        hidden_permissions.append("A hidden weakness or exception may exist if established before it becomes relevant.")

    abilities: list[str] = []
    power_words = ("power", "ability", "magic", "immortal", "strongest", "can control", "can summon", "can create")
    for sentence in _sentences(text):
        if any(word in sentence.casefold() for word in power_words):
            abilities.append(sentence)

    tone = ""
    for candidate in ("dark", "hopeful", "wholesome", "gritty", "realistic", "comedic", "tragic", "cozy"):
        if candidate in lower:
            tone = candidate
            break

    return CampaignConstitution(
        premise=text[:1200], genre=genre, tone=tone, player_identity=infer_player(text),
        abilities=abilities[:20], powers=abilities[:20], mortality_rules=mortality_rules,
        hard_invariants=hard_invariants, hidden_canon_permissions=hidden_permissions,
        narrative_preferences=[s for s in _sentences(text) if any(
            p in s.casefold() for p in ("do not railroad", "don't railroad", "consequences", "npc", "agency", "tone")
        )][:20],
        original_prompt=text,
    )


def derive_theme(prompt: str, genre: str, tone: str = "") -> ThemeProfile:
    text = f"{prompt} {genre} {tone}".casefold()
    if "cyberpunk" in text or "neon" in text:
        return ThemeProfile(family="cyberpunk", mood="electric and watchful", accent_family="cold teal", surface_style="carbon", typography_style="modern", motion_style="quick", background_effect="signal-map")
    if any(word in text for word in ("zombie", "outbreak", "survival", "post-apocalyptic")):
        return ThemeProfile(family="survival", mood="tense and weathered", accent_family="signal rust", surface_style="field-paper", typography_style="utilitarian", motion_style="restrained", background_effect="weathered-chart", contrast="high")
    if any(word in text for word in ("cozy", "wholesome", "slice-of-life")):
        return ThemeProfile(family="cozy", mood="warm and lived-in", accent_family="brass", surface_style="walnut", typography_style="soft-serif", motion_style="gentle", background_effect="woodcut-landscape")
    if any(word in text for word in ("fantasy", "demon", "magic", "medieval")):
        return ThemeProfile(family="dark_fantasy", mood="old, watchful, and political", accent_family="iron red", surface_style="obsidian", typography_style="old-world", motion_style="measured", background_effect="engraved-map")
    if "mystery" in text or "detective" in text:
        return ThemeProfile(family="mystery", mood="quietly suspicious", accent_family="oxidized green", surface_style="graphite", typography_style="editorial", motion_style="precise", background_effect="case-notes")
    return ThemeProfile(mood="open and expectant", accent_family="warm copper", surface_style="ink", typography_style="old-world", background_effect="engraved-map")
