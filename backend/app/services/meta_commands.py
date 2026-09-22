from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, CanonRule


async def apply_meta_command(session: AsyncSession, campaign: Campaign, raw_action: str) -> tuple[str, str | None]:
    value = raw_action.strip()
    for command, source in (("/canon", "player_meta"), ("/retcon", "retcon_command")):
        prefix = command + " "
        if value.casefold().startswith(prefix):
            statement = value[len(prefix):].strip()
            if not statement:
                return raw_action, None
            rule_type = "PLAYER_META"
            if any(term in statement.casefold() for term in ("immortal", "cannot die", "can die")):
                rule_type = "PLAYER_CANNOT_DIE" if any(term in statement.casefold() for term in ("immortal", "cannot die")) else "PLAYER_MORTALITY"
            session.add(CanonRule(campaign_id=campaign.id, rule_type=rule_type, statement=statement,
                strength="HARD", exceptions=[], visibility="PLAYER_KNOWN", source=source))
            await session.commit()
            return f"[Out-of-character canon update from the player: {statement}]", statement
    if value.casefold().startswith("/ooc "):
        return f"[Out-of-character player note: {value[5:].strip()}]", value[5:].strip()
    return raw_action, None
