"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Building2, Castle, ChevronDown, Church, Coins, Crown, Flag, Landmark, LoaderCircle, Shield, Swords, Users, Wand2 } from "lucide-react";
import { useState } from "react";
import type { Character, Faction } from "@/lib/api";
import { Group, KIND_LABEL, Stance } from "@/lib/people";

const KIND_ICON: Record<string, typeof Flag> = {
  faction: Flag, nation: Crown, city: Building2, guild: Coins, religion: Church, house: Castle, military: Swords,
  government: Landmark, crew: Users,
};

/** People grouped by faction, nation, guild, faith and so on; each group opens to its members. */
export function FactionGroups({ groups, selectedId, onSelect, stanceOf, onRegroup, regrouping, compact = false }: {
  groups: Group[]; selectedId?: string | null; onSelect: (id: string) => void; stanceOf: (person: Character) => Stance;
  onRegroup?: () => void; regrouping?: boolean; compact?: boolean;
}) {
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const named = groups.filter((group) => group.faction);
  return <div className={`faction-groups${compact ? " is-compact" : ""}`}>
    {onRegroup && <div className="faction-groups-head">
      <span>{named.length ? `${named.length} group${named.length === 1 ? "" : "s"}` : "No groups yet"}</span>
      <button type="button" className="btn btn--ghost btn--sm" onClick={onRegroup} disabled={regrouping}
        title="Read the story again and regroup people into factions, nations, guilds and crews">
        {regrouping ? <LoaderCircle size={13} className="spin" /> : <Wand2 size={13} />}{regrouping ? "Regrouping…" : "Regroup from story"}</button>
    </div>}
    {!named.length && <p className="empty-card">No one has a known faction, nation, guild or faith yet. Regroup from the story, or add groups under Edit character details.</p>}
    {groups.map((group) => {
      const id = group.faction?.id ?? "unaffiliated";
      const expanded = open[id] ?? (compact ? false : groups.length <= 3);
      const Icon = group.faction ? KIND_ICON[group.faction.kind] ?? Shield : Users;
      const leaders = group.members.filter((member) => member.status === "leader").map((member) => member.person.name);
      return <section key={id} className={`faction-card${group.faction ? "" : " is-loose"}`} data-kind={group.faction?.kind ?? "none"}>
        <button type="button" className="faction-card-head" aria-expanded={expanded} onClick={() => setOpen({ ...open, [id]: !expanded })}>
          <span className="faction-icon" aria-hidden="true"><Icon size={15} /></span>
          <span className="faction-title"><strong>{group.faction?.name ?? "No known group"}</strong>
            <small>{group.faction ? KIND_LABEL[group.faction.kind] ?? "Group" : "Unaffiliated"} · {group.members.length}
              {leaders.length ? ` · led by ${leaders.join(", ")}` : ""}</small></span>
          <motion.span className="faction-chevron" animate={{ rotate: expanded ? 180 : 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }}><ChevronDown size={15} /></motion.span>
        </button>
        <AnimatePresence initial={false}>
          {expanded && <motion.div key="body" className="faction-card-body" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }} transition={{ duration: reduceMotion ? 0 : 0.2 }}>
            {group.faction && <FactionInfo faction={group.faction} />}
            <ul className="faction-members">
              {group.members.map(({ person, role, status }) => {
                const stance = stanceOf(person);
                return <li key={person.id}><button type="button" aria-pressed={selectedId === person.id} onClick={() => onSelect(person.id)}
                  className={status === "former" ? "is-former" : undefined}>
                  <span><strong>{person.name}</strong><small>{role || person.role || "Role unknown"}{status === "former" ? " · former" : ""}</small></span>
                  {status === "leader" && <em className="member-tag">Leader</em>}
                  {stance.kind && <em className={`stance-tag stance-tag--${stance.kind}`} title={stance.reason}>{stance.kind === "ally" ? "Ally" : "Enemy"}</em>}
                </button></li>;
              })}
            </ul>
          </motion.div>}
        </AnimatePresence>
      </section>;
    })}
  </div>;
}

function FactionInfo({ faction }: { faction: Faction }) {
  if (!faction.description && !faction.relations.length) return null;
  return <div className="faction-info">
    {faction.description && <p>{faction.description}</p>}
    {!!faction.relations.length && <div className="chip-row">{faction.relations.slice(0, 6).map((relation) =>
      <span key={`${relation.to}-${relation.relation}`} className="chip chip--static" data-tone={/hostile|war|rival|enem/i.test(relation.relation) ? "enemy" : /all|friend|pact|vassal|trade/i.test(relation.relation) ? "ally" : "neutral"}>
        {relation.relation} · {relation.to}</span>)}</div>}
  </div>;
}
