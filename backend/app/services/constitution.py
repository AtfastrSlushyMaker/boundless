import re

from app.schemas import CampaignConstitution, ThemeFamily, ThemeProfile


def infer_title(prompt: str) -> str:
    match = re.search(r"\b(?:my name is|I am called|I am named|I'm called|I'm named)\s+([\w'-]+)", prompt, re.IGNORECASE)
    if not match:
        match = re.search(r"\bI am\s+([A-Z][\w'-]+)", prompt)
    if match:
        return f"{match.group(1)}'s world"
    first = re.split(r"[.!?\n]", prompt.strip(), maxsplit=1)[0]
    return (first[:74].rstrip(" ,;:") + ("…" if len(first) > 74 else "")) or "Untitled world"


def infer_player(prompt: str) -> str:
    match = re.search(r"\b(?:my name is|I am called|I am named|I'm called|I'm named)\s+([\w'-]+)", prompt, re.IGNORECASE)
    if not match:
        match = re.search(r"\bI am\s+([A-Z][\w'-]+)", prompt)
    return match.group(1) if match else "You"


def _sentences(prompt: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", prompt) if part.strip()]


def _first_person(sentence: str) -> bool:
    return bool(re.search(r"\b(?:I|I'm|I've|my|mine)\b", sentence, re.IGNORECASE))


def _collect(sentences: list[str], pattern: str, limit: int = 12) -> list[str]:
    expression = re.compile(pattern, re.IGNORECASE)
    return [sentence[:500] for sentence in sentences if expression.search(sentence)][:limit]


def _explicit_identity(text: str) -> dict[str, str]:
    identity: dict[str, str] = {}
    sex = re.search(r"\b(?:my sex is|sex:?|I am|I'm)\s*(male|female|intersex)\b", text, re.IGNORECASE)
    gender = re.search(r"\b(?:I am|I'm|my gender is|gender:?)\s*(?:a\s+)?(man|woman|nonbinary|non-binary)\b", text, re.IGNORECASE)
    pronouns = re.search(r"\b(?:my pronouns are|I use)\s*(he/him|she/her|they/them)\b", text, re.IGNORECASE)
    if sex:
        identity["sex"] = sex.group(1).casefold()
    if gender:
        identity["gender"] = gender.group(1).casefold().replace("non-binary", "nonbinary")
    if pronouns:
        identity["pronouns"] = pronouns.group(1).casefold()
    return identity


def _explicit_money(text: str) -> dict[str, str | int]:
    match = re.search(r"\b(?:I (?:have|carry|start with)|my purse holds)\s+(\d{1,9})\s+(gold|silver|copper|coins?|credits?|dollars?)\b", text, re.IGNORECASE)
    return {"amount": int(match.group(1)), "currency": match.group(2).casefold()} if match else {}


def derive_constitution(prompt: str) -> CampaignConstitution:
    text = prompt.strip()
    lower = text.casefold()
    sentences = _sentences(text)
    genre = "Open world"
    for key, value in (
        ("cyberpunk", "Cyberpunk"), ("science fiction", "Science fiction"), ("sci-fi", "Science fiction"),
        ("high fantasy", "High fantasy"), ("fantasy", "Fantasy"), ("war", "War"), ("magic", "Fantasy"),
        ("demon", "Fantasy"), ("priestess", "Fantasy"), ("zombie", "Survival horror"),
        ("horror", "Horror"), ("mystery", "Mystery"), ("romance", "Romance"),
        ("political", "Political intrigue"),
    ):
        if re.search(r"\b" + re.escape(key) + r"\b", lower):
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

    abilities = [sentence[:500] for sentence in sentences if _first_person(sentence) and re.search(
        r"\b(?:I can|I'm able to|I am able to|my (?:power|ability|magic) is|I (?:command|control|summon|transform|cast))\b",
        sentence, re.IGNORECASE)][:20]
    powers = [sentence for sentence in abilities if re.search(r"\b(?:magic|power|spell|summon|transform|immortal|control)\b", sentence, re.IGNORECASE)]
    traits = _collect(sentences, r"\b(?:(?:I am|I'm)\s+(?:(?:a |an )?(?:man|woman|nonbinary|non-binary|human|demon|elf|thief|king|queen|priest|merchant|soldier|courier|mortal|immortal)|neither\b)|I just\b|people call me\b)", 12)
    goals = _collect(sentences, r"\b(?:my goal is|I (?:want|intend|plan|aim|seek) to|I will)\b", 12)
    history = _collect(sentences, r"\b(?:I (?:was|used to|have been|once|grew up)|my (?:past|history|sister|brother|family)|I've (?:seen|lost|survived))\b", 12)
    relationships = _collect(sentences, r"\b(?:my (?:sister|brother|mother|father|wife|husband|partner|friend)|I (?:know|serve|follow|oppose)\s+(?:the\s+)?[A-Z])", 12)
    world_rules = _collect(sentences, r"\b(?:in this world|in this city|the world (?:is|has)|magic (?:works|requires|cannot)|there (?:is|are) no)\b", 16)
    factions = _collect(sentences, r"\b(?:High Priestess|Iron Guard|guild|order|kingdom|empire|faction|council)\b", 12)
    opening = " ".join(sentences[:4])
    if len(opening) > 1200:
        opening = opening[:1199].rsplit(" ", 1)[0] + "…"
    starting_state: dict = {"identity": _explicit_identity(text)}
    money = _explicit_money(text)
    if money:
        starting_state["money"] = money

    tone = ""
    for candidate in ("dark", "hopeful", "wholesome", "gritty", "realistic", "comedic", "tragic", "cozy"):
        if candidate in lower:
            tone = candidate
            break

    return CampaignConstitution(
        premise=opening, genre=genre, tone=tone, player_identity=infer_player(text),
        traits=traits, history=history, known_history=history, abilities=abilities, powers=powers,
        important_relationships=relationships, world_rules=world_rules, important_factions=factions,
        starting_state=starting_state, mortality_rules=mortality_rules,
        hard_invariants=hard_invariants, hidden_canon_permissions=hidden_permissions,
        narrative_preferences=[s for s in sentences if any(
            p in s.casefold() for p in ("do not railroad", "don't railroad", "consequences", "npc", "agency", "tone")
        )][:20],
        preferences=goals,
        original_prompt=text,
    )


THEME_DETAILS: dict[str, dict[str, str]] = {
    "dark_fantasy": {"mood": "ominous", "accent_family": "iron red", "surface_style": "obsidian", "typography_style": "old-world", "motion_style": "measured", "background_effect": "engraved-map"},
    "cyberpunk": {"mood": "electric", "accent_family": "cold teal", "surface_style": "carbon", "typography_style": "modern", "motion_style": "quick", "background_effect": "signal-map"},
    "survival": {"mood": "weathered", "accent_family": "signal rust", "surface_style": "field-paper", "typography_style": "utilitarian", "motion_style": "restrained", "background_effect": "weathered-chart"},
    "cozy": {"mood": "warm", "accent_family": "brass", "surface_style": "walnut", "typography_style": "soft-serif", "motion_style": "gentle", "background_effect": "woodcut-landscape"},
    "mystery": {"mood": "suspicious", "accent_family": "oxidized green", "surface_style": "graphite", "typography_style": "editorial", "motion_style": "precise", "background_effect": "case-notes"},
    "horror": {"mood": "dread", "accent_family": "ash red", "surface_style": "soot", "typography_style": "stark", "motion_style": "uneasy", "background_effect": "worn-plate"},
    "romance": {"mood": "intimate", "accent_family": "rosewood", "surface_style": "velvet", "typography_style": "literary", "motion_style": "gentle", "background_effect": "soft-etching"},
    "sci_fi": {"mood": "expansive", "accent_family": "pale cyan", "surface_style": "alloy", "typography_style": "modern", "motion_style": "precise", "background_effect": "star-chart"},
    "modern": {"mood": "grounded", "accent_family": "stone", "surface_style": "slate", "typography_style": "plain", "motion_style": "measured", "background_effect": "city-chart"},
    "neutral": {"mood": "open", "accent_family": "warm copper", "surface_style": "ink", "typography_style": "old-world", "motion_style": "measured", "background_effect": "engraved-map"},
}


def derive_theme(prompt: str, genre: str, tone: str = "", family: ThemeFamily | None = None) -> ThemeProfile:
    text = f"{prompt} {genre} {tone}".casefold()
    if family is None:
        if re.search(r"\b(cyberpunk|neon|hacker)\b", text):
            family = "cyberpunk"
        elif re.search(r"\b(horror|haunted|terrifying|eldritch)\b", text):
            family = "horror"
        elif re.search(r"\b(zombie|outbreak|survival|post-apocalyptic)\b", text):
            family = "survival"
        elif re.search(r"\b(cozy|wholesome|slice-of-life)\b", text):
            family = "cozy"
        elif re.search(r"\b(mystery|detective|investigation)\b", text):
            family = "mystery"
        elif re.search(r"\b(romance|romantic|love story)\b", text):
            family = "romance"
        elif re.search(r"\b(science fiction|sci-fi|spaceship|starship|interstellar)\b", text):
            family = "sci_fi"
        elif re.search(r"\b(fantasy|demon|magic|medieval|priestess)\b", text):
            family = "dark_fantasy"
        elif re.search(r"\b(modern|contemporary|present day)\b", text):
            family = "modern"
        else:
            family = "neutral"
    details = THEME_DETAILS[family]
    return ThemeProfile(family=family, **details)
