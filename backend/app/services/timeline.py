from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Branch, Checkpoint, Turn
from app.services.context_builder import history_for_branch
from app.services.state_service import restore_snapshot


async def _checkpoint_for(session: AsyncSession, branch_id: UUID, turn_id: UUID | None, turn_index: int | None = None):
    query = select(Checkpoint).where(Checkpoint.branch_id == branch_id)
    if turn_id is None:
        query = query.where(Checkpoint.turn_index == 0)
    else:
        query = query.where(Checkpoint.turn_id == turn_id)
    if turn_index is not None:
        query = query.where(Checkpoint.turn_index == turn_index)
    return await session.scalar(query.order_by(Checkpoint.created_at.desc()))


async def rewind_branch(session: AsyncSession, campaign_id: UUID, branch_id: UUID,
                        turn_id: UUID | None = None, turn_index: int | None = None) -> Branch:
    branch = await session.get(Branch, branch_id)
    if not branch or branch.campaign_id != campaign_id:
        raise ValueError("Timeline not found in this campaign.")
    history = await history_for_branch(session, branch.head_turn_id, limit=5000)
    if turn_id:
        selected = next((turn for turn in history if turn.id == turn_id), None)
        if not selected:
            raise ValueError("That turn is not part of this timeline.")
        chosen_index = selected.turn_index
    elif turn_index is not None:
        selected = next((turn for turn in history if turn.turn_index == turn_index), None)
        if turn_index and not selected:
            raise ValueError("That turn is not part of this timeline.")
        chosen_index = turn_index
    else:
        raise ValueError("Choose a turn or turn number to rewind to.")
    checkpoint = await _checkpoint_for(session, branch.id, selected.id if selected else None, chosen_index)
    if not checkpoint:
        raise ValueError("The requested world snapshot is missing.")
    await restore_snapshot(session, branch, checkpoint.state_snapshot)
    branch.head_turn_id = selected.id if selected else None
    await session.commit()
    await session.refresh(branch)
    return branch


async def fork_branch(session: AsyncSession, campaign_id: UUID, source_branch_id: UUID,
                      name: str, turn_id: UUID | None = None) -> Branch:
    source = await session.get(Branch, source_branch_id)
    if not source or source.campaign_id != campaign_id:
        raise ValueError("Timeline not found in this campaign.")
    fork_turn = await session.get(Turn, turn_id) if turn_id else None
    if fork_turn and fork_turn.campaign_id != campaign_id:
        raise ValueError("That turn belongs to another campaign.")
    if not fork_turn and source.head_turn_id:
        fork_turn = await session.get(Turn, source.head_turn_id)
    if fork_turn and fork_turn.id not in {turn.id for turn in await history_for_branch(session, source.head_turn_id, limit=5000)}:
        raise ValueError("That turn is not part of the selected timeline.")
    fork_index = fork_turn.turn_index if fork_turn else 0
    checkpoint = await _checkpoint_for(session, source.id, fork_turn.id if fork_turn else None, fork_index)
    if not checkpoint:
        raise ValueError("The selected point has no restorable checkpoint.")
    snapshot = deepcopy(checkpoint.state_snapshot)
    child = Branch(campaign_id=campaign_id, name=name.strip(), parent_branch_id=source.id,
                   forked_from_turn_id=fork_turn.id if fork_turn else None,
                   head_turn_id=fork_turn.id if fork_turn else None,
                   current_state=deepcopy(snapshot.get("current_state", {})))
    session.add(child)
    await session.flush()

    # Reserve identities from every historical snapshot, including entities since removed.
    id_map: dict[str, str] = {}
    historical_checkpoints = (await session.scalars(select(Checkpoint).where(
        Checkpoint.branch_id == source.id, Checkpoint.turn_index <= fork_index,
    ))).all()
    for saved_checkpoint in historical_checkpoints:
        for rows in saved_checkpoint.state_snapshot.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if row.get("id"):
                    id_map.setdefault(str(row["id"]), str(uuid4()))
    for rows in snapshot.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get("id"):
                id_map.setdefault(str(row["id"]), str(uuid4()))
    for table, rows in snapshot.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get("id"):
                row["id"] = id_map[str(row["id"])]
            if row.get("branch_id"):
                row["branch_id"] = str(child.id)
            for key in ("from_character_id", "to_character_id"):
                if row.get(key) and str(row[key]) in id_map:
                    row[key] = id_map[str(row[key])]
    await restore_snapshot(session, child, snapshot)
    # Copy checkpoints for the shared ancestry so rewinding works before the fork point too.
    ancestry = await history_for_branch(session, fork_turn.id if fork_turn else None, limit=5000)
    for turn in ancestry:
        prior = await session.scalar(select(Checkpoint).where(
            Checkpoint.branch_id == source.id, Checkpoint.turn_id == turn.id,
            Checkpoint.turn_index == turn.turn_index,
        ).order_by(Checkpoint.created_at.desc()))
        if prior:
            copied = deepcopy(prior.state_snapshot)
            for table, rows in copied.items():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    original_id = str(row.get("id", ""))
                    if original_id in id_map:
                        row["id"] = id_map[original_id]
                    if row.get("branch_id"):
                        row["branch_id"] = str(child.id)
                    for key in ("from_character_id", "to_character_id"):
                        if row.get(key) and str(row[key]) in id_map:
                            row[key] = id_map[str(row[key])]
            session.add(Checkpoint(campaign_id=campaign_id, branch_id=child.id, turn_id=turn.id,
                                   turn_index=turn.turn_index, state_snapshot=copied))
    source_initial = await session.scalar(select(Checkpoint).where(
        Checkpoint.branch_id == source.id, Checkpoint.turn_index == 0,
        Checkpoint.turn_id.is_(None),
    ).order_by(Checkpoint.created_at.desc()))
    if source_initial:
        initial_snapshot = deepcopy(source_initial.state_snapshot)
        for rows in initial_snapshot.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if row.get("id"):
                    row["id"] = id_map[str(row["id"])]
                if row.get("branch_id"):
                    row["branch_id"] = str(child.id)
                for key in ("from_character_id", "to_character_id"):
                    if row.get(key) and str(row[key]) in id_map:
                        row[key] = id_map[str(row[key])]
        session.add(Checkpoint(campaign_id=campaign_id, branch_id=child.id,
                               turn_id=None, turn_index=0, state_snapshot=initial_snapshot))
    session.add(Checkpoint(campaign_id=campaign_id, branch_id=child.id,
                           turn_id=fork_turn.id if fork_turn else None,
                           turn_index=fork_index, state_snapshot=snapshot))
    await session.commit()
    await session.refresh(child)
    return child
