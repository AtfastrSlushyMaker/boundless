import type { Affiliation, CampaignDetail, Character, Faction, Relationship } from "@/lib/api";

export type Stance = { kind: "ally" | "enemy" | null; reason: string };

const ENEMY_WORDS = /\b(?:enem(?:y|ies)|hostile|nemesis|rival|foe|hunt(?:s|ing|er)?|betray\w*|at war|vendetta|hates?|sworn enemy|wants? (?:him|her|them|you) dead)\b/i;
const ALLY_WORDS = /\b(?:all(?:y|ies|ied)|friend(?:s|ly)?|companion|trusted|loyal|lover|partner|protector|sworn to|confidant|comrade)\b/i;
const FACTION_ENEMY = /\b(?:hostile|enem(?:y|ies)|rival|at war|war|feud|oppos\w*)\b/i;
const FACTION_ALLY = /\b(?:all(?:y|ied|iance)|friend\w*|vassal|trade partners?|pact|treaty)\b/i;

function score(relation: Relationship, axis: string): number | null {
  const value = relation.dimensions?.[axis];
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
}

export function affiliationsOf(person: Character | undefined): Affiliation[] {
  const raw = person?.attributes?.affiliations;
  return Array.isArray(raw) ? raw.filter((entry): entry is Affiliation => Boolean(entry && typeof entry === "object" && typeof (entry as Affiliation).name === "string")) : [];
}

const key = (value: string) => value.trim().toLocaleLowerCase();

/**
 * Whether someone is the player's ally or enemy, and why. Looks at both directions of the
 * relationship (how they see the player and how the player sees them), an explicit status,
 * and — when there is no direct relationship — whether their groups are allied with or
 * hostile to the player's groups.
 */
export function stanceFor(person: Character, campaign: Pick<CampaignDetail, "protagonist_name" | "relationships" | "factions" | "characters">): Stance {
  const player = campaign.protagonist_name;
  if (person.name === player) return { kind: null, reason: "" };
  const relations = campaign.relationships.filter((relation) =>
    (relation.from === person.name && relation.to === player) || (relation.from === player && relation.to === person.name));
  const highest = (axis: string) => Math.max(-1, ...relations.map((relation) => score(relation, axis) ?? -1));
  const hostility = highest("hostility"), trust = highest("trust"), loyalty = highest("loyalty"), affection = highest("affection");
  const status = relations.map((relation) => String(relation.dimensions?.status ?? "")).join(" ");
  if (ENEMY_WORDS.test(status)) return { kind: "enemy", reason: status.trim() };
  if (hostility >= 55) return { kind: "enemy", reason: `Hostility ${hostility}` };
  if (ALLY_WORDS.test(status) && hostility < 40) return { kind: "ally", reason: status.trim() };
  if (person.importance === "COMPANION" && hostility < 40) return { kind: "ally", reason: "Companion" };
  const warmth = Math.max(trust, loyalty, affection);
  if (warmth >= 65 && hostility < 35) {
    const axis = warmth === trust ? "Trust" : warmth === loyalty ? "Loyalty" : "Affection";
    return { kind: "ally", reason: `${axis} ${warmth}` };
  }
  if (relations.some((relation) => AXIS_KEYS.some((axis) => score(relation, axis) !== null))) return { kind: null, reason: "" };

  // No direct feelings recorded: fall back to how their groups stand toward the player's groups.
  const protagonist = campaign.characters.find((row) => row.name === player);
  const mine = new Set(affiliationsOf(protagonist).filter((entry) => entry.status !== "former").map((entry) => key(entry.name)));
  if (!mine.size) return { kind: null, reason: "" };
  const theirs = affiliationsOf(person).filter((entry) => entry.status !== "former");
  const shared = theirs.find((entry) => mine.has(key(entry.name)));
  if (shared) return { kind: "ally", reason: `Fellow member of ${shared.name}` };
  for (const entry of theirs) {
    const group = campaign.factions.find((row) => key(row.name) === key(entry.name));
    for (const relation of group?.relations ?? []) {
      if (!mine.has(key(relation.to))) continue;
      if (FACTION_ENEMY.test(relation.relation)) return { kind: "enemy", reason: `${entry.name}: ${relation.relation} with ${relation.to}` };
      if (FACTION_ALLY.test(relation.relation)) return { kind: "ally", reason: `${entry.name}: ${relation.relation} with ${relation.to}` };
    }
  }
  return { kind: null, reason: "" };
}

const AXIS_KEYS = ["trust", "affection", "fear", "respect", "loyalty", "hostility", "attraction", "debt", "dependence"];

export type Group = { faction: Faction | null; members: Array<{ person: Character; role: string; status: string }> };

/** People grouped by faction (largest first), then everyone without a known group. */
export function groupPeople(people: Character[], factions: Faction[]): Group[] {
  const byId = new Map(people.map((person) => [person.id, person]));
  const grouped = new Set<string>();
  const groups: Group[] = factions.map((faction) => {
    const members = faction.members.flatMap((member) => {
      const person = byId.get(member.id);
      if (!person) return [];
      grouped.add(person.id);
      return [{ person, role: member.role, status: member.status }];
    }).sort((left, right) => (left.status === "leader" ? -1 : 0) - (right.status === "leader" ? -1 : 0)
      || (left.status === "former" ? 1 : 0) - (right.status === "former" ? 1 : 0));
    return { faction, members };
  }).filter((group) => group.members.length).sort((left, right) => right.members.length - left.members.length);
  const loose = people.filter((person) => !grouped.has(person.id));
  return loose.length ? [...groups, { faction: null, members: loose.map((person) => ({ person, role: "", status: "" })) }] : groups;
}

export const KIND_LABEL: Record<string, string> = {
  faction: "Faction", nation: "Nation", city: "City", guild: "Guild", religion: "Faith", house: "House",
  military: "Military", government: "Government", crew: "Crew",
};
