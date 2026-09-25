"use client";

import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useIsPresent, useReducedMotion } from "motion/react";
import { ArrowLeft, ArrowRight, ArrowUpRight, Feather, ImagePlus, ListChecks, LoaderCircle, PenLine, Sparkles, Wand2, X } from "lucide-react";
import { KeyboardEvent, ReactNode, useEffect, useState } from "react";
import { z } from "zod";
import { plateUrl } from "@/components/Art";
import { api } from "@/lib/api";
import type { CharacterSetup, GameMode, ThemeFamily } from "@/lib/api";

const promptSchema = z.string().trim().min(12, "Give the world a little more to begin with.").max(30_000, "Keep the opening brief under 30,000 characters.");

type Props = {
  open: boolean;
  busy: boolean;
  error?: string;
  onClose: () => void;
  onCreate: (prompt: string, gameMode: GameMode, details: CharacterSetup & { theme_family?: ThemeFamily }) => void;
  onEnhance: (prompt: string, direction: string) => Promise<string>;
};

type Step = "world" | "you" | "tone";
const STEPS: Array<{ id: Step; label: string }> = [{ id: "world", label: "The world" }, { id: "you", label: "Your character" }, { id: "tone", label: "Tone & play" }];

const MOODS: Array<{ id: ThemeFamily | ""; label: string; line: string }> = [
  { id: "", label: "Match the premise", line: "Chosen from what you write" },
  { id: "dark_fantasy", label: "Dark fantasy", line: "Candlelight, ash, old oaths" },
  { id: "horror", label: "Horror", line: "Dread that takes its time" },
  { id: "mystery", label: "Mystery", line: "Fog, clues, quiet lies" },
  { id: "romance", label: "Romance", line: "Warmth, want, and tension" },
  { id: "cozy", label: "Cozy", line: "Hearths and small kindnesses" },
  { id: "survival", label: "Survival", line: "Scarcity and hard choices" },
  { id: "cyberpunk", label: "Cyberpunk", line: "Neon, debt, chrome" },
  { id: "sci_fi", label: "Science fiction", line: "Distance and discovery" },
  { id: "modern", label: "Modern", line: "Streets you know" },
];

const SPARKS: Array<{ id: string; title: string; prompt: string; mood: ThemeFamily }> = [
  { id: "thief-who-steals-magic", title: "The thief who steals magic", mood: "dark_fantasy",
    prompt: "My name is Wren. I was born able to steal magic: when I touch a mage's skin I take one of their spells, and they never get it back. The Archive of Velen hunts people like me. Tonight I am standing outside its gates with a forged invitation." },
  { id: "last-lighthouse", title: "The last lighthouse", mood: "horror",
    prompt: "I keep the last lighthouse on a coast where the sea stopped making sound three weeks ago. Ships still come. None of them have crews. Tonight one runs aground below the rocks, and a lantern is moving on its deck." },
  { id: "masquerade-murder", title: "A murder at the masquerade", mood: "mystery",
    prompt: "I am Inspector Hale of the city watch. The Duke's masquerade ended with his steward dead in the orangery and forty guests who all wore masks. The Duke wants it solved before dawn, quietly, and he wants it to be anyone but his daughter." },
  { id: "debts-in-the-undercity", title: "Debts in the undercity", mood: "cyberpunk",
    prompt: "My name is Kai Mori, a courier with a cracked neural jack and a debt to the Saito syndicate I cannot pay. Tonight a stranger offers to erase it if I deliver one sealed case across the flooded districts before sunrise." },
  { id: "winter-at-the-inn", title: "Winter at the inn", mood: "cozy",
    prompt: "I have just inherited a failing inn at a mountain crossroads, three days before the first snow closes the pass. The staff are strange, the regulars are stranger, and something lives in the cellar that the old owner used to feed." },
  { id: "crown-of-a-dead-king", title: "Crown of a dead king", mood: "dark_fantasy",
    prompt: "I am Malek, a human king, and we are at war with the demons of the Ashen Reach. My father died holding the pass. Tonight the demon envoy arrives under a white banner and asks to speak to me alone." },
];

const DEVELOP = ["Add a rival", "Deepen the magic or technology", "Give me a secret", "Sharpen the opening scene", "Name the powers that be", "Raise the stakes"];

export function CreateWorldDialog({ open, busy, error, onClose, onCreate, onEnhance }: Props) {
  const reduceMotion = useReducedMotion();
  const [step, setStep] = useState<Step>("world");
  const [prompt, setPrompt] = useState("");
  const [direction, setDirection] = useState("");
  const [gameMode, setGameMode] = useState<GameMode>("freeform");
  const [themeFamily, setThemeFamily] = useState<ThemeFamily | "">("");
  const [characterName, setCharacterName] = useState("");
  const [sex, setSex] = useState("");
  const [gender, setGender] = useState("");
  const [pronouns, setPronouns] = useState("");
  const [appearance, setAppearance] = useState("");
  const [groups, setGroups] = useState("");
  const [money, setMoney] = useState("");
  const [currency, setCurrency] = useState("gold");
  const [portrait, setPortrait] = useState(true);
  const [validation, setValidation] = useState("");
  const [enhanceError, setEnhanceError] = useState("");
  const [enhancing, setEnhancing] = useState(false);
  const [previous, setPrevious] = useState<string | null>(null);
  const images = useQuery({ queryKey: ["image-settings"], queryFn: api.imageSettings, enabled: open, retry: false });
  const roles = useQuery({ queryKey: ["model-roles"], queryFn: api.modelRoles, enabled: open, retry: false });
  const canPortrait = Boolean(images.data?.enabled && (images.data.provider === "comfyui" || images.data.provider === "ai_horde"));
  const matureWriter = roles.data?.roles.find((role) => role.role === "mature" && !role.inherit);
  const locked = busy || enhancing;
  const premiseOk = promptSchema.safeParse(prompt).success;
  const index = STEPS.findIndex((entry) => entry.id === step);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape" && !locked) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, locked, onClose]);

  const guessedName = characterName.trim() || prompt.match(/\b(?:[Mm]y name is|I am|I'm)\s+([A-Z][\w'’-]+)/)?.[1] || "";
  const groupList = groups.split(",").map((entry) => entry.trim()).filter(Boolean).slice(0, 8);
  const mood = MOODS.find((entry) => entry.id === themeFamily) ?? MOODS[0];

  const create = () => {
    const result = promptSchema.safeParse(prompt);
    if (!result.success) { setValidation(result.error.issues[0].message); setStep("world"); return; }
    setValidation("");
    onCreate(result.data, gameMode, {
      ...(themeFamily ? { theme_family: themeFamily } : {}),
      ...(characterName.trim() ? { character_name: characterName.trim() } : {}),
      ...(sex ? { character_sex: sex as CharacterSetup["character_sex"] } : {}),
      ...(gender ? { character_gender: gender as CharacterSetup["character_gender"] } : {}),
      ...(pronouns ? { character_pronouns: pronouns as CharacterSetup["character_pronouns"] } : {}),
      ...(money ? { starting_money: Number(money), money_currency: currency } : {}),
      ...(appearance.trim() ? { character_appearance: appearance.trim() } : {}),
      ...(groupList.length ? { character_affiliations: groupList } : {}),
      ...(canPortrait && portrait ? { generate_portrait: true } : {}),
    });
  };
  const next = () => {
    if (step === "world" && !premiseOk) { setValidation(promptSchema.safeParse(prompt).error?.issues[0].message ?? ""); return; }
    setValidation("");
    if (index < STEPS.length - 1) setStep(STEPS[index + 1].id); else create();
  };
  const enhance = async (request: string) => {
    if (!premiseOk) { setValidation("Write a line or two first, then develop it."); return; }
    if (!request.trim()) { setEnhanceError("Pick a direction or describe one."); return; }
    setValidation(""); setEnhanceError(""); setEnhancing(true);
    try {
      const developed = await onEnhance(prompt.trim(), request.trim());
      setPrevious(prompt);
      setPrompt(developed);
      setDirection("");
    } catch (cause) {
      setEnhanceError(cause instanceof Error ? cause.message : "The world could not be developed.");
    } finally { setEnhancing(false); }
  };
  const onPromptKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); next(); }
  };

  return <AnimatePresence>
    {open && <Scrim onDismiss={() => { if (!locked) onClose(); }} fast={Boolean(reduceMotion)}>
      <motion.section className={`create-dialog composer${themeFamily ? ` theme-${themeFamily}` : ""}`} role="dialog" aria-modal="true" aria-labelledby="create-title"
        initial={{ y: reduceMotion ? 0 : 14, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: reduceMotion ? 0 : 8, opacity: 0 }}
        transition={{ duration: reduceMotion ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}>
        <header className="composer-head">
          <div><p className="section-overline">A NEW WORLD</p><h2 id="create-title">{step === "world" ? "What is true here?" : step === "you" ? "Who are you?" : "How should it feel?"}</h2></div>
          <button className="icon-button" aria-label="Close" onClick={onClose} disabled={locked}><X size={18} /></button>
        </header>
        <nav className="composer-steps" aria-label="Steps">
          {STEPS.map((entry, position) => <button type="button" key={entry.id} aria-current={step === entry.id ? "step" : undefined}
            className={position < index ? "is-done" : undefined} disabled={locked || (position > 0 && !premiseOk)}
            onClick={() => setStep(entry.id)}><span>{position + 1}</span>{entry.label}</button>)}
        </nav>

        <div className="composer-body">
          <div className="composer-main">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={step} initial={{ opacity: 0, x: reduceMotion ? 0 : 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: reduceMotion ? 0 : -12 }}
                transition={{ duration: reduceMotion ? 0 : 0.18 }}>
                {step === "world" && <div className="composer-pane">
                  <label className="composer-label" htmlFor="world-prompt">Your premise <span>{prompt.length.toLocaleString()} / 30,000</span></label>
                  <textarea id="world-prompt" className="composer-premise" autoFocus value={prompt} onKeyDown={onPromptKey}
                    onChange={(event) => { setPrompt(event.target.value); setValidation(""); }} maxLength={30_000} disabled={locked}
                    placeholder="Who you are, what this world allows, and the moment everything changes. A sentence is enough; a page is welcome." />
                  {!prompt.trim() && <div className="spark-list" aria-label="Story sparks">
                    <p className="composer-hint"><Feather size={13} />Or start from a spark</p>
                    <div className="spark-grid">{SPARKS.map((spark) => <button type="button" key={spark.id} className="spark"
                      style={{ backgroundImage: `url(/art/sparks/${spark.id}.webp)` }}
                      onClick={() => { setPrompt(spark.prompt); setThemeFamily(spark.mood); }}>
                      <span className="spark-shade" aria-hidden="true" /><strong>{spark.title}</strong>
                      <em>{MOODS.find((mood) => mood.id === spark.mood)?.label}</em></button>)}</div>
                  </div>}
                  {!!prompt.trim() && <div className="develop">
                    <p className="composer-hint"><Wand2 size={13} />Develop it <span>The model expands your premise; you can undo.</span></p>
                    <div className="chip-row">{DEVELOP.map((entry) => <button type="button" key={entry} className="chip" disabled={locked} onClick={() => void enhance(entry)}>{entry}</button>)}</div>
                    <div className="develop-custom">
                      <input value={direction} onChange={(event) => { setDirection(event.target.value); setEnhanceError(""); }} maxLength={2000} disabled={locked}
                        placeholder="Or ask for something specific…" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void enhance(direction); } }} />
                      <button type="button" className="btn btn--secondary btn--sm" onClick={() => void enhance(direction)} disabled={locked || !direction.trim()}>
                        {enhancing ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{enhancing ? "Developing…" : "Develop"}</button>
                      {previous !== null && !enhancing && <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setPrompt(previous); setPrevious(null); }}>Undo</button>}
                    </div>
                  </div>}
                </div>}

                {step === "you" && <div className="composer-pane">
                  <p className="composer-note">Everything here is optional. Leave a field blank and the story decides from your premise.</p>
                  <div className="composer-grid">
                    <Field label="Name" wide><input value={characterName} onChange={(event) => setCharacterName(event.target.value)} maxLength={120} disabled={locked}
                      placeholder={guessedName ? `${guessedName} (from your premise)` : "From your premise"} /></Field>
                    <Field label="Sex"><select value={sex} onChange={(event) => setSex(event.target.value)} disabled={locked}><option value="">Unspecified</option><option value="male">Male</option><option value="female">Female</option><option value="intersex">Intersex</option><option value="other">Another</option></select></Field>
                    <Field label="Gender"><select value={gender} onChange={(event) => setGender(event.target.value)} disabled={locked}><option value="">Unspecified</option><option value="man">Man</option><option value="woman">Woman</option><option value="nonbinary">Nonbinary</option><option value="other">Another</option></select></Field>
                    <Field label="Pronouns"><select value={pronouns} onChange={(event) => setPronouns(event.target.value)} disabled={locked}><option value="">Unspecified</option><option value="he/him">He / him</option><option value="she/her">She / her</option><option value="they/them">They / them</option></select></Field>
                    <Field label="Appearance" hint="Used for your portrait and how others describe you" wide>
                      <textarea rows={3} value={appearance} onChange={(event) => setAppearance(event.target.value)} maxLength={1000} disabled={locked}
                        placeholder="Tall and weathered, a scar through one eyebrow, a courier's satchel…" /></Field>
                    <Field label="Groups you belong to" hint="Factions, nation, guild, faith… separated by commas" wide>
                      <input value={groups} onChange={(event) => setGroups(event.target.value)} maxLength={600} disabled={locked} placeholder="Couriers' Guild, Kingdom of Ost" /></Field>
                    <Field label="Starting money"><input type="number" min="0" max="1000000000" step="1" value={money} onChange={(event) => setMoney(event.target.value)} disabled={locked} placeholder="Unspecified" /></Field>
                    <Field label="Currency"><select value={currency} onChange={(event) => setCurrency(event.target.value)} disabled={locked}><option value="gold">Gold</option><option value="silver">Silver</option><option value="copper">Copper</option><option value="coins">Coins</option><option value="credits">Credits</option></select></Field>
                  </div>
                  <label className={`composer-toggle${canPortrait ? "" : " is-off"}`}>
                    <input type="checkbox" checked={canPortrait && portrait} disabled={!canPortrait || locked} onChange={(event) => setPortrait(event.target.checked)} />
                    <ImagePlus size={16} /><span><strong>Paint my portrait</strong>
                      <small>{canPortrait ? "Starts in the background as soon as the world opens." : "Turn on a portrait provider in Settings to use this."}</small></span>
                  </label>
                </div>}

                {step === "tone" && <div className="composer-pane">
                  <p className="composer-label">World mood <span>Sets colours, light and prose tone. Change it any time.</span></p>
                  <div className="mood-grid" role="radiogroup" aria-label="World mood">
                    {MOODS.map((entry) => <button type="button" role="radio" aria-checked={themeFamily === entry.id} key={entry.id || "auto"}
                      className={`mood-swatch${entry.id ? ` theme-${entry.id}` : " is-auto"}`} onClick={() => setThemeFamily(entry.id)} disabled={locked}>
                      <span className="mood-swatch-art" aria-hidden="true" style={entry.id ? { backgroundImage: `url(${plateUrl(entry.id)})` } : undefined} /><strong>{entry.label}</strong><small>{entry.line}</small></button>)}
                  </div>
                  <p className="composer-label">How you play</p>
                  <div className="play-grid" role="radiogroup" aria-label="Play style">
                    <button type="button" role="radio" aria-checked={gameMode === "freeform"} onClick={() => setGameMode("freeform")} disabled={locked}>
                      <PenLine size={18} /><strong>Write every action</strong><small>You type what you do. Nothing is off the table.</small></button>
                    <button type="button" role="radio" aria-checked={gameMode === "guided"} onClick={() => setGameMode("guided")} disabled={locked}>
                      <ListChecks size={18} /><strong>Offer me choices</strong><small>Three suggestions after each scene; you can still write your own.</small></button>
                  </div>
                  {matureWriter && <p className="composer-note">Explicit scenes between adults (sex, graphic violence, gore) are written by <strong>{matureWriter.model.split("/").pop()}</strong>, as set in AI roles.</p>}
                </div>}
              </motion.div>
            </AnimatePresence>
          </div>

          <aside className={`composer-summary${themeFamily ? ` theme-${themeFamily}` : ""}`} aria-label="Your world so far">
            <span className="composer-summary-art" aria-hidden="true" style={{ backgroundImage: `url(${plateUrl(themeFamily || "neutral")})` }} />
            <p className="section-overline">YOUR WORLD</p>
            <h3>{guessedName ? `${guessedName}'s world` : "Untitled world"}</h3>
            <p className="composer-summary-premise">{prompt.trim() ? prompt.trim().slice(0, 220) + (prompt.trim().length > 220 ? "…" : "") : "Your premise will appear here."}</p>
            <dl>
              <div><dt>Mood</dt><dd>{mood.label}</dd></div>
              <div><dt>Play</dt><dd>{gameMode === "guided" ? "Choices offered" : "Free writing"}</dd></div>
              {(pronouns || gender) && <div><dt>You</dt><dd>{[gender, pronouns].filter(Boolean).join(" · ")}</dd></div>}
              {!!groupList.length && <div><dt>Groups</dt><dd>{groupList.join(", ")}</dd></div>}
              {money && <div><dt>Purse</dt><dd>{Number(money).toLocaleString()} {currency}</dd></div>}
              {canPortrait && portrait && <div><dt>Portrait</dt><dd>Painted on arrival</dd></div>}
            </dl>
          </aside>
        </div>

        {(validation || enhanceError || error) && <p className="form-message form-message--error composer-error" role="alert">{validation || enhanceError || error}</p>}
        <footer className="composer-foot">
          {index > 0 ? <button type="button" className="btn btn--ghost" onClick={() => setStep(STEPS[index - 1].id)} disabled={locked}><ArrowLeft size={15} />Back</button>
            : <button type="button" className="btn btn--ghost" onClick={onClose} disabled={locked}>Cancel</button>}
          <span className="composer-foot-hint">{step === "world" ? <><kbd>⌘</kbd><kbd>↵</kbd> next</> : null}</span>
          {index < STEPS.length - 1 && <button type="button" className="btn btn--secondary" onClick={create} disabled={locked || !premiseOk}
            title="Skip the optional steps">Start now</button>}
          <button type="button" className="btn btn--primary" onClick={next} disabled={locked || (step === "world" && !prompt.trim())}>
            {busy ? <LoaderCircle className="spin" size={16} /> : index < STEPS.length - 1 ? <ArrowRight size={16} /> : <ArrowUpRight size={16} />}
            {busy ? "Setting the world…" : index < STEPS.length - 1 ? "Next" : "Enter the world"}</button>
        </footer>
      </motion.section>
    </Scrim>}
  </AnimatePresence>;
}

function Field({ label, hint, wide, children }: { label: string; hint?: string; wide?: boolean; children: ReactNode }) {
  return <label className={`composer-field${wide ? " is-wide" : ""}`}><span>{label}{hint && <small>{hint}</small>}</span>{children}</label>;
}

function Scrim({ children, onDismiss, fast }: { children: ReactNode; onDismiss: () => void; fast: boolean }) {
  const present = useIsPresent();
  return <motion.div className="dialog-scrim" style={{ pointerEvents: present ? "auto" : "none" }}
    onMouseDown={(event) => { if (present && event.target === event.currentTarget) onDismiss(); }}
    initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: fast ? 0 : 0.16 }}>{children}</motion.div>;
}
