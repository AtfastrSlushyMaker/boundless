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


NOMINAL = {
    "steal": "theft", "copy": "copying", "control": "control", "read": "reading", "heal": "healing",
    "summon": "summoning", "teleport": "teleportation", "fly": "flight", "see": "sight", "speak": "speech",
    "become": "transformation", "transform": "transformation", "command": "command", "absorb": "absorption",
    "drain": "draining", "take": "taking", "sense": "sensing", "manipulate": "manipulation", "bend": "bending",
    "create": "creation", "shape": "shaping", "mimic": "mimicry", "borrow": "borrowing", "devour": "devouring",
}
ABILITY_PHRASE = re.compile(
    r"\b(?:ability|power|gift|talent|curse) to (?P<verb>[a-z]+)(?:\s+(?P<object>(?!and\b|when\b|by\b|if\b)[a-z'-]+(?:\s+(?!and\b|when\b|by\b|if\b|to\b)[a-z'-]+){0,2}))?",
    re.IGNORECASE)
POWER_VERB = re.compile(
    r"\bI (?:can|am able to)\s+(?P<verb>cast|summon|control|command|transform|heal|teleport|fly|read|shapeshift|"
    r"become|absorb|drain|steal|copy)\s+(?P<object>[a-z'-]+(?:\s+[a-z'-]+){0,2})", re.IGNORECASE)
LIMITATION = re.compile(r"\b(?:I (?:cannot|can't|can not)|only (?:when|if|while)|my (?:power|magic|ability) (?:cannot|can't|only|fails)|"
                        r"costs? me|takes a toll|at a price|must (?:touch|see|be))\b", re.IGNORECASE)


def _ability_name(verb: str, obj: str) -> str:
    verb = verb.casefold()
    noun = NOMINAL.get(verb, f"{verb}ing" if not verb.endswith("e") else f"{verb[:-1]}ing")
    obj = re.sub(r"\b(?:their|his|her|my|the|a|an|other|others'?)\b", " ", obj or "", flags=re.IGNORECASE)
    obj = " ".join(obj.split())
    return (f"{obj} {noun}" if obj else noun).strip().capitalize()[:120]


def extract_abilities(prompt: str) -> list[dict]:
    """Deterministic ability catalogue entries from explicit first-person setup text."""
    sentences = _sentences(prompt)
    found: list[dict] = []
    for index, sentence in enumerate(sentences):
        for pattern in (ABILITY_PHRASE, POWER_VERB):
            for match in pattern.finditer(sentence):
                if pattern is ABILITY_PHRASE and not re.search(r"\b(?:I|my|me)\b", sentence, re.IGNORECASE):
                    continue
                verb, obj = match.group("verb"), match.group("object") or ""
                name = _ability_name(verb, obj)
                if any(entry["name"].casefold() == name.casefold() for entry in found):
                    continue
                keywords = {verb.casefold(), *(word.casefold() for word in obj.split() if len(word) > 3)}
                detail = [sentence]
                for follow in sentences[index + 1:index + 9]:
                    related = any(word in follow.casefold() for word in keywords) or \
                        re.search(r"\b(?:spell|magic|power|touch)\b", follow, re.IGNORECASE)
                    if not related and len(follow) > 45:
                        break
                    detail.append(follow)
                    if len(detail) >= 8:
                        break
                limits = [line[:300] for line in detail if LIMITATION.search(line)]
                source = "innate" if re.search(r"\bborn with|since birth|innate|always had\b", sentence, re.IGNORECASE) else "campaign_setup"
                found.append({"name": name, "description": " ".join(detail)[:1200], "source": source,
                              "limitations": limits, "strength": "HARD", "source_text": sentence[:500]})
    return found[:12]


def constitution_rules(constitution: CampaignConstitution) -> list[dict]:
    """Auditable rule list: every important setup rule keeps its type, strength, and source text."""
    rules: list[dict] = []
    for invariant in constitution.hard_invariants:
        rules.append({"statement": "The player cannot permanently die." if invariant.get("type") == "PLAYER_CANNOT_DIE"
                      else str(invariant.get("statement", invariant.get("type", ""))),
                      "type": invariant.get("type", "HARD_RULE"), "strength": invariant.get("strength", "HARD"),
                      "source": invariant.get("source", "campaign_setup"), "exceptions": invariant.get("exceptions", [])})
    for entry in constitution.ability_catalogue:
        rules.append({"statement": f"{constitution.player_identity} has the ability: {entry['name']}.", "type": "ABILITY",
                      "strength": "HARD", "source": "player_setup", "source_text": entry.get("source_text", "")})
    for kind, values, strength in (("LIMITATION", constitution.limitations, "HARD"),
                                   ("MORTALITY", constitution.mortality_rules, "HARD"),
                                   ("WORLD_RULE", constitution.world_rules, "SOFT"),
                                   ("MAGIC_RULE", constitution.magic_rules, "SOFT"),
                                   ("GOAL", constitution.goals, "SOFT")):
        for value in values:
            rules.append({"statement": value, "type": kind, "strength": strength, "source": "player_setup",
                          "source_text": value})
    return rules


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

    catalogue = extract_abilities(text)
    abilities = [f"{entry['name']}: {entry['description']}"[:500] for entry in catalogue]
    abilities += [sentence[:500] for sentence in sentences if _first_person(sentence) and re.search(
        r"\b(?:I'm able to|I am able to|my (?:power|ability|magic) is|I (?:command|control|summon|transform|cast))\b",
        sentence, re.IGNORECASE) and not any(sentence in entry["description"] for entry in catalogue)][:8]
    powers = [sentence for sentence in abilities if re.search(r"\b(?:magic|power|spell|summon|transform|immortal|control|theft|steal)\b", sentence, re.IGNORECASE)]
    limitations = _collect(sentences, r"\b(?:I (?:cannot|can't|can not)|my (?:power|magic|ability) (?:cannot|can't|only|fails)|"
                                      r"takes a (?:huge )?toll|costs? me)\b", 12)
    magic_rules = _collect(sentences, r"\b(?:magic (?:is|works|requires|cannot|can't|comes|bleeds)|spells? (?:require|cost|cannot)|mages?\b.*\bmust)\b", 12)
    traits = _collect(sentences, r"\b(?:(?:I am|I'm)\s+(?:(?:a |an )?(?:man|woman|nonbinary|non-binary|human|demon|elf|thief|king|queen|priest|merchant|soldier|courier|mortal|immortal)|neither\b)|I just\b|people call me\b)", 12)
    goals = _collect(sentences, r"\b(?:my goal is|I (?:want|intend|plan|aim|seek) to|I want (?:the|her|his|their|every)|I will)\b", 12)
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

    constitution = CampaignConstitution(
        premise=opening, genre=genre, tone=tone, player_identity=infer_player(text),
        traits=traits, history=history, known_history=history, abilities=abilities, powers=powers,
        limitations=limitations, magic_rules=magic_rules, ability_catalogue=catalogue, goals=goals,
        important_relationships=relationships, world_rules=world_rules, important_factions=factions,
        starting_state=starting_state, mortality_rules=mortality_rules,
        hard_invariants=hard_invariants, hidden_canon_permissions=hidden_permissions,
        narrative_preferences=[s for s in sentences if any(
            p in s.casefold() for p in ("do not railroad", "don't railroad", "consequences", "npc", "agency", "tone")
        )][:20],
        preferences=goals[:5],
        original_prompt=text,
    )
    constitution.rules = constitution_rules(constitution)
    return constitution


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
