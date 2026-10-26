import { Text, Tooltip, makeStyles, tokens } from "@fluentui/react-components";
import { CheckmarkCircle20Filled, Circle20Regular, DismissCircle20Filled, ArrowSync20Regular, PersonClock20Filled } from "@fluentui/react-icons";
import type { AuditEntry, Fix, IncidentSummary } from "../api/types";
import { dateTime } from "./format";

type StepState = "done" | "current" | "waiting_human" | "failed" | "pending";
interface Step { label: string; state: StepState; at?: string; detail?: string }

const useStyles = makeStyles({
  row: { display: "flex", alignItems: "flex-start", gap: "0", overflowX: "auto", paddingBottom: "4px" },
  step: { display: "flex", flexDirection: "column", alignItems: "center", gap: "4px", minWidth: "108px", flex: 1, textAlign: "center" },
  line: { height: "2px", flex: 1, minWidth: "16px", marginTop: "10px" },
});

function at(audit: AuditEntry[], action: string, branch?: string) {
  return audit.find((a) => a.action === action && (!branch || a.details.branch === branch))?.ts;
}

/** Derives the incident's story from the audit trail: what happened, what's next, who's needed. */
export function buildSteps(inc: IncidentSummary, fix: Fix | null, audit: AuditEntry[]): Step[] {
  const steps: Step[] = [{ label: "Detected", state: "done", at: inc.created_at }];
  const diagnosed = at(audit, "diagnosed");
  steps.push({ label: "Diagnosed", state: diagnosed ? "done" : "pending", at: diagnosed });
  if (!inc.fix_eligible && !fix) {
    steps.push({ label: inc.status === "resolved" ? "Resolved" : "Escalated to a human", state: inc.status === "resolved" ? "done" : "waiting_human", at: inc.updated_at });
    return steps;
  }
  const [first, second, last] = fix?.promotion ?? ["dev", "qa", "main"];
  const plan: [string, string | undefined][] = [
    ["Fix written", at(audit, "fix_pr_opened", first)],
    [`Merged to ${first}`, at(audit, "fix_pr_merged", first)],
    [`Deployed ${first}`, at(audit, "fix_deployed", first)],
    [`Merged to ${second}`, at(audit, "fix_pr_merged", second)],
    [`Deployed ${second}`, at(audit, "fix_deployed", second)],
    [`Approved → ${last}`, at(audit, "fix_pr_merged", last)],
    ["Deployed prod", at(audit, "fix_deployed", last)],
    ["Verified in prod", at(audit, "fix_resolved")],
  ];
  let currentSet = false;
  for (const [label, ts] of plan) {
    if (ts) {
      steps.push({ label, state: "done", at: ts });
      continue;
    }
    if (!currentSet && fix) {
      currentSet = true;
      const terminal = fix.state === "failed" || fix.state === "rejected";
      steps.push({
        label, state: terminal ? "failed" : fix.awaiting_human ? "waiting_human" : "current",
        detail: terminal ? fix.error ?? fix.state : fix.awaiting_human ? "waiting for you" : fix.repairs ? `repair attempt ${fix.repairs}` : undefined,
      });
      continue;
    }
    steps.push({ label, state: "pending" });
  }
  return steps;
}

const ICON: Record<StepState, JSX.Element> = {
  done: <CheckmarkCircle20Filled color={tokens.colorPaletteGreenForeground1} />,
  current: <ArrowSync20Regular color={tokens.colorBrandForeground1} />,
  waiting_human: <PersonClock20Filled color={tokens.colorPaletteMarigoldForeground1} />,
  failed: <DismissCircle20Filled color={tokens.colorPaletteRedForeground1} />,
  pending: <Circle20Regular color={tokens.colorNeutralForeground4} />,
};
const LABEL: Record<StepState, string> = { done: "done", current: "in progress", waiting_human: "needs a human", failed: "failed", pending: "not started" };

export function Lifecycle({ steps }: { steps: Step[] }) {
  const s = useStyles();
  return (
    <div className={s.row} role="list" aria-label="Incident lifecycle">
      {steps.map((st, i) => (
        <div key={st.label} style={{ display: "contents" }}>
          {i > 0 && <div className={s.line} style={{ background: st.state === "pending" ? tokens.colorNeutralStroke2 : tokens.colorBrandStroke1 }} />}
          <Tooltip relationship="description" content={`${LABEL[st.state]}${st.at ? ` · ${dateTime(st.at)}` : ""}${st.detail ? ` · ${st.detail}` : ""}`}>
            <div className={s.step} role="listitem" aria-label={`${st.label}: ${LABEL[st.state]}`} tabIndex={0}>
              {ICON[st.state]}
              <Text size={200} weight={st.state === "pending" ? "regular" : "semibold"}>{st.label}</Text>
              <Text size={100} style={{ color: tokens.colorNeutralForeground3 }}>{st.at ? dateTime(st.at) : st.detail ?? ""}</Text>
            </div>
          </Tooltip>
        </div>
      ))}
    </div>
  );
}
