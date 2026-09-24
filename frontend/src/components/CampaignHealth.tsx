"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { CheckCircle2, LoaderCircle, ShieldCheck, Wrench } from "lucide-react";
import { useState } from "react";
import { api, CampaignDetail, RepairFinding } from "@/lib/api";

const LABELS: Record<string, string> = {
  DUPLICATE_CHARACTER: "Same person, several records", AMBIGUOUS_CHARACTERS: "Unclear identities",
  RECOVER_CHARACTER_HISTORY: "Lost facts and roles", DUPLICATE_OBJECTIVE: "Repeated objective",
  STALE_OBJECTIVE: "Objective already done", DUPLICATE_ITEM: "Repeated item", NOISE_RELATIONSHIP: "Empty relationships",
  DUPLICATE_MEMORY: "Repeated memories", STALE_SUMMARY: "Summary behind", TRUNCATED_STATUS: "Cut-off status",
  WORLD_TIME: "World time stuck", ABILITY_CATALOGUE: "Ability catalogue", FAILED_ATTEMPTS: "Failed attempts",
};

/** Check an existing campaign for bookkeeping damage and repair it, with a report before anything changes. */
export function CampaignHealth({ campaign, onRepaired }: { campaign: CampaignDetail; onRepaired: (detail: CampaignDetail) => void }) {
  const reduceMotion = useReducedMotion();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [started, setStarted] = useState(false);
  const report = useQuery({
    queryKey: ["repair", campaign.id, campaign.branch.id],
    queryFn: () => api.repairReport(campaign.id, campaign.branch.id),
    enabled: started, staleTime: 0,
  });
  const apply = useMutation({
    mutationFn: () => api.applyRepair(campaign.id, campaign.branch.id, [...checked], true),
    onSuccess: (result) => { onRepaired(result.campaign); setChecked(new Set()); void report.refetch(); },
  });
  const findings = report.data?.findings ?? [];
  const actionable = findings.filter((finding) => finding.type !== "AMBIGUOUS_CHARACTERS" && finding.type !== "FAILED_ATTEMPTS" || finding.auto);
  const toggle = (finding: RepairFinding) => setChecked((current) => {
    const next = new Set(current);
    if (next.has(finding.id)) next.delete(finding.id); else next.add(finding.id);
    return next;
  });
  return <section className="campaign-health">
    <div className="campaign-health-head"><ShieldCheck size={16} /><h3>Record health</h3></div>
    <p className="lore-copy">Look for duplicate people, stale objectives, and lost facts. You see the report before anything changes.</p>
    {!started && <button type="button" className="health-button" onClick={() => setStarted(true)}><Wrench size={14} />Check this campaign</button>}
    {report.isFetching && <p className="health-loading"><LoaderCircle size={14} className="spin" />Reading the whole record…</p>}
    {report.isError && <p className="portrait-error">{report.error.message}</p>}
    {report.data && !report.isFetching && <>
      {!findings.length && <p className="health-clean"><CheckCircle2 size={14} />Nothing needs repair.</p>}
      <ul className="health-findings">
        <AnimatePresence initial={false}>
          {actionable.map((finding, index) => <motion.li key={finding.id} className={`health-finding health-finding--${finding.confidence.toLowerCase()}`}
            initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }}
            transition={{ delay: reduceMotion ? 0 : index * 0.04, duration: 0.22 }}>
            <label>
              {finding.auto ? <span className="health-auto" title="Applied automatically: high confidence">Auto</span>
                : <input type="checkbox" checked={checked.has(finding.id)} onChange={() => toggle(finding)} disabled={finding.confidence === "AMBIGUOUS"} />}
              <span className="health-kind">{LABELS[finding.type] ?? finding.type}</span>
            </label>
            <p>{finding.summary}</p>
            {finding.evidence.length > 0 && <details><summary>Evidence</summary><ul>{finding.evidence.map((line) => <li key={line}>{line}</li>)}</ul></details>}
          </motion.li>)}
        </AnimatePresence>
      </ul>
      {findings.length > 0 && <button type="button" className="health-button health-button--primary" disabled={apply.isPending} onClick={() => apply.mutate()}>
        {apply.isPending ? <LoaderCircle size={14} className="spin" /> : <Wrench size={14} />}
        {apply.isPending ? "Repairing…" : `Repair ${findings.filter((finding) => finding.auto).length + checked.size} findings`}
      </button>}
      {apply.isSuccess && <p className="health-clean"><CheckCircle2 size={14} />Repaired {apply.data.result.applied.length} findings.</p>}
      {apply.isError && <p className="portrait-error">{apply.error.message}</p>}
    </>}
  </section>;
}
