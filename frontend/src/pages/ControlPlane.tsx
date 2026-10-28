// Redesign views: human approvals (L3), signed requests + execution ledger, Known Error DB, agents & autonomy policy,
// and the per-incident panel (graph state, Databricks test runs, evidence, signed requests).
import {
  Badge, Button, Card, CardHeader, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle, Link, Tab, TabList,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, tokens,
} from "@fluentui/react-components";
import { Checkmark16Regular, Dismiss16Regular, Open16Regular } from "@fluentui/react-icons";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useActionApprovals, useAgents, useApprovals, useCurateKedb, useDecideApproval, useEvidence, useKedb, useLedgerSummary, usePolicy,
  useRequests,
} from "../api/queries";
import type { ActionApproval, IncidentAgentView, SignedRequest } from "../api/types";
import { useAuth } from "../auth/auth";
import { Code, Empty, ErrorState, Grid, Kpi, Loading, PageHeader, ReasonDialog } from "../components/common";
import { ago, dateTime } from "../components/format";
import { FixTable } from "./Fixes";

const LEVEL_TEXT: Record<number, string> = {
  0: "L0 manual", 1: "L1 evidence only", 2: "L2 recommend", 3: "L3 human approves", 4: "L4 pre-approved", 5: "L5 closed loop",
};

function Level({ level }: { level: number | null }) {
  if (level === null || level === undefined) return <>-</>;
  const color = level >= 4 ? "success" : level === 3 ? "warning" : "informative";
  return <Badge appearance="tint" color={color}>{LEVEL_TEXT[level] ?? `L${level}`}</Badge>;
}

function Outcome({ value }: { value: string | null | undefined }) {
  if (!value) return <Text size={200}>-</Text>;
  const color = ({ success: "success", SUCCESS: "success", approved: "success", done: "success", resolved: "success",
    failed: "danger", FAILED: "danger", rejected: "danger", escalated: "danger", taken_over: "warning",
    UNKNOWN: "warning", unknown: "warning", pending: "warning", waiting: "warning", dry_run: "informative" } as const)[value] ?? "subtle";
  return <Badge appearance="tint" color={color}>{value}</Badge>;
}

// ------------------------------------------------------------------------------------ approvals
function ApprovalRow({ a }: { a: ActionApproval }) {
  const decide = useDecideApproval();
  const { can } = useAuth();
  const nav = useNavigate();
  const button = (approve: boolean) => (
    <ReasonDialog
      danger={!approve}
      title={approve ? "Approve this action?" : "Reject this action?"}
      description={approve
        ? `The control plane signs ${a.steps.join(" then ")} and the data plane executes it after re-checking policy and live state.`
        : "The incident is escalated to a human; nothing is executed."}
      confirmLabel={approve ? "Approve" : "Reject"}
      onConfirm={(reason) => decide.mutateAsync({ id: a.id, approve, reason })}
      trigger={(open) => (
        <Button size="small" appearance={approve ? "primary" : "secondary"} icon={approve ? <Checkmark16Regular /> : <Dismiss16Regular />}
          onClick={open} disabled={!can("approver")}>{approve ? "Approve" : "Reject"}</Button>
      )}
    />
  );
  return (
    <TableRow>
      <TableCell>#{a.id}</TableCell>
      <TableCell>{a.incident_id ? <Link onClick={() => nav(`/incidents/${a.incident_id}`)}>#{a.incident_id}</Link> : "-"}</TableCell>
      <TableCell><Text size={200}>{a.summary}</Text></TableCell>
      <TableCell>{a.steps.map((s) => <Badge key={s} appearance="outline" style={{ marginRight: 4 }}>{s}</Badge>)}</TableCell>
      <TableCell><Level level={a.level} /></TableCell>
      <TableCell>{ago(a.created_at)}</TableCell>
      <TableCell>{a.status === "pending"
        ? <span style={{ display: "inline-flex", gap: 6 }}>{button(true)}{button(false)}</span>
        : <span><Outcome value={a.status} /> <Text size={200}>{a.decided_by} · {a.comment}</Text></span>}
      </TableCell>
    </TableRow>
  );
}

export function Approvals() {
  const [show, setShow] = useState<"pending" | "all">("pending");
  const actions = useActionApprovals(show);
  const prs = useApprovals();
  const { can } = useAuth();
  return (
    <>
      <PageHeader
        eyebrow="Human in the loop"
        title="Approvals"
        subtitle="L3 actions wait here for an approver; prod code waits for your merge in GitHub (it enforces reviews and records who approved)."
      />
      <Card>
        <CardHeader
          header={<Text weight="semibold" size={400}>Runtime actions (console approval)</Text>}
          description={can("approver") ? "One approval covers the whole plan shown in Steps." : "Sign in as approver to decide."}
          action={<TabList size="small" selectedValue={show} onTabSelect={(_, d) => setShow(d.value as "pending" | "all")}>
            <Tab value="pending">Pending</Tab><Tab value="all">History</Tab></TabList>}
        />
        {actions.isLoading ? <Loading /> : actions.error ? <ErrorState error={actions.error} /> : !actions.data?.length
          ? <Empty title={show === "pending" ? "Nothing is waiting for you" : "No approvals yet"} /> : (
            <Table aria-label="Action approvals">
              <TableHeader><TableRow>
                <TableHeaderCell>#</TableHeaderCell><TableHeaderCell>Incident</TableHeaderCell><TableHeaderCell style={{ width: "36%" }}>Plan</TableHeaderCell>
                <TableHeaderCell>Steps</TableHeaderCell><TableHeaderCell>Autonomy</TableHeaderCell><TableHeaderCell>Asked</TableHeaderCell><TableHeaderCell>Decision</TableHeaderCell>
              </TableRow></TableHeader>
              <TableBody>{actions.data.map((a) => <ApprovalRow key={a.id} a={a} />)}</TableBody>
            </Table>
          )}
      </Card>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Code fixes waiting for your merge (GitHub)</Text>}
          description="Tested in Databricks and running in dev and qa. Merging the prod PR approves the prod sync and the repair of the failed run." />
        {prs.isLoading ? <Loading /> : prs.error ? <ErrorState error={prs.error} /> : <FixTable fixes={prs.data ?? []} empty="No PRs waiting" />}
      </Card>
    </>
  );
}

// ------------------------------------------------------------------------- requests + ledger
function RequestsTable({ rows, compact }: { rows: SignedRequest[]; compact?: boolean }) {
  const nav = useNavigate();
  if (!rows.length) return <Empty title="No signed requests" />;
  return (
    <Table size="small" aria-label="Signed requests">
      <TableHeader><TableRow>
        <TableHeaderCell>When</TableHeaderCell>{!compact && <TableHeaderCell>Incident</TableHeaderCell>}
        <TableHeaderCell>Agent</TableHeaderCell><TableHeaderCell>Capability</TableHeaderCell><TableHeaderCell>Env</TableHeaderCell>
        <TableHeaderCell>Autonomy</TableHeaderCell><TableHeaderCell>Approval</TableHeaderCell><TableHeaderCell>Result</TableHeaderCell>
        <TableHeaderCell>Ledger</TableHeaderCell>{!compact && <TableHeaderCell>Key</TableHeaderCell>}
      </TableRow></TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id}>
            <TableCell style={{ whiteSpace: "nowrap" }}>{dateTime(r.created_at)}</TableCell>
            {!compact && <TableCell>{r.incident_id ? <Link onClick={() => nav(`/incidents/${r.incident_id}`)}>#{r.incident_id}</Link> : "manual"}</TableCell>}
            <TableCell>{r.agent}</TableCell>
            <TableCell><Text size={200} weight="semibold">{r.capability}</Text></TableCell>
            <TableCell>{r.env}</TableCell>
            <TableCell><Level level={r.level} /></TableCell>
            <TableCell><Text size={200}>{r.approval ? `${r.approval.kind}${r.approval.by ? ` (${r.approval.by})` : ""}` : "-"}</Text></TableCell>
            <TableCell title={r.result?.error ?? ""}><Outcome value={r.result?.status ?? r.status} />
              {r.result?.error && <Text size={100} block style={{ color: tokens.colorPaletteRedForeground1, maxWidth: 280 }} truncate wrap={false}>{r.result.error}</Text>}
            </TableCell>
            <TableCell><Outcome value={r.ledger?.state} /></TableCell>
            {!compact && <TableCell><Text size={100} style={{ fontFamily: "Consolas, monospace" }}>{r.idempotency_key}</Text></TableCell>}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function Requests() {
  const { data, error, isLoading } = useRequests();
  const summary = useLedgerSummary();
  const s = summary.data;
  return (
    <>
      <PageHeader eyebrow="Trust boundary" title="Signed requests & execution ledger"
        subtitle="Every platform change: requested by an agent, decided by policy, signed by the control plane, verified and executed once by the data plane." />
      <Grid min={180}>
        <Kpi label="Executions" value={s?.total ?? "-"} />
        <Kpi label="Succeeded" value={s?.SUCCESS ?? "-"} />
        <Kpi label="Failed" value={s?.FAILED ?? "-"} />
        <Kpi label="Unknown (check platform)" value={s?.UNKNOWN ?? "-"} hint="Never retried automatically" />
      </Grid>
      <Card style={{ marginTop: 16 }}>
        {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : <div style={{ maxHeight: "65vh", overflow: "auto" }}><RequestsTable rows={data ?? []} /></div>}
      </Card>
    </>
  );
}

// ------------------------------------------------------------------------------------- KEDB
export function KnownErrors() {
  const [status, setStatus] = useState<string>("approved");
  const { data, error, isLoading } = useKedb(status);
  const curate = useCurateKedb();
  const { can } = useAuth();
  return (
    <>
      <PageHeader eyebrow="Knowledge" title="Known Error DB"
        subtitle="Curated failure signatures and their runbooks. Triage checks these before any LLM call; agents can only propose entries, an admin approves them." />
      <TabList selectedValue={status} onTabSelect={(_, d) => setStatus(String(d.value))} style={{ marginBottom: 12 }}>
        <Tab value="approved">Approved</Tab><Tab value="proposed">Proposed by agents</Tab><Tab value="retired">Retired</Tab>
      </TabList>
      <Card>
        {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.length ? <Empty title="No entries" /> : (
          <Table size="small" aria-label="Known errors">
            <TableHeader><TableRow>
              <TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Platform</TableHeaderCell><TableHeaderCell>Class</TableHeaderCell>
              <TableHeaderCell>Runbook</TableHeaderCell><TableHeaderCell style={{ width: "28%" }}>Signature</TableHeaderCell>
              <TableHeaderCell style={{ width: "24%" }}>Fix hint</TableHeaderCell><TableHeaderCell>Provenance</TableHeaderCell><TableHeaderCell />
            </TableRow></TableHeader>
            <TableBody>
              {data.map((e) => (
                <TableRow key={e.id}>
                  <TableCell><Text weight="semibold" size={200}>{e.name}</Text></TableCell>
                  <TableCell>{e.platform}</TableCell>
                  <TableCell><Badge appearance="outline">{e.error_class}</Badge></TableCell>
                  <TableCell><Badge appearance="tint">{e.runbook}</Badge></TableCell>
                  <TableCell><Text size={100} style={{ fontFamily: "Consolas, monospace", wordBreak: "break-all" }}>{e.signature}</Text></TableCell>
                  <TableCell><Text size={200}>{e.fix_hint || e.diagnostics || "-"}</Text></TableCell>
                  <TableCell><Text size={200}>{e.provenance}</Text></TableCell>
                  <TableCell>
                    {e.status !== "retired" && (
                      <ReasonDialog
                        title={e.status === "proposed" ? "Approve this entry?" : "Retire this entry?"}
                        description={e.status === "proposed" ? "Triage will match it from now on." : "Triage stops matching it."}
                        confirmLabel={e.status === "proposed" ? "Approve" : "Retire"}
                        onConfirm={(reason) => curate.mutateAsync({ id: e.id, action: e.status === "proposed" ? "approve" : "retire", reason })}
                        trigger={(open) => <Button size="small" onClick={open} disabled={!can("admin")}>{e.status === "proposed" ? "Approve" : "Retire"}</Button>}
                      />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}

// -------------------------------------------------------------------------- agents + policy
export function AgentsAndPolicy() {
  const agents = useAgents();
  const policy = usePolicy();
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16, marginTop: 16 }}>
      <Card>
        <CardHeader header={<Text weight="semibold" size={400}>Agent registry</Text>} description="config/agents/*.yaml · an agent can only request what its manifest lists" />
        {agents.isLoading ? <Loading /> : agents.error ? <ErrorState error={agents.error} /> : (agents.data ?? []).map((a) => (
          <div key={a.name} style={{ padding: "8px 0", borderBottom: `1px solid ${tokens.colorNeutralStroke3}` }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <Text weight="semibold">{a.name}</Text><Badge appearance="outline">{a["x-pipeline-agent"].kind}</Badge>
              {a["x-pipeline-agent"].model_alias && <Badge appearance="tint" color="informative">model: {a["x-pipeline-agent"].model_alias}</Badge>}
              <Badge appearance="tint" color={a["x-pipeline-agent"].risk_class === "high" ? "danger" : "subtle"}>risk {a["x-pipeline-agent"].risk_class}</Badge>
            </div>
            <Text size={200} block>{a.description}</Text>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
              {a["x-pipeline-agent"].requests.map((c) => <Badge key={c} size="small" appearance="outline" color="brand">{c}</Badge>)}
            </div>
          </div>
        ))}
      </Card>
      <Card>
        <CardHeader header={<Text weight="semibold" size={400}>Autonomy policy</Text>}
          description={policy.data ? `config/policy/autonomy.yaml · bundle ${policy.data.sha256.slice(0, 12)} · first matching rule wins` : "config/policy/autonomy.yaml"} />
        {policy.isLoading ? <Loading /> : policy.error ? <ErrorState error={policy.error} /> : (
          <Table size="small" aria-label="Autonomy rules">
            <TableHeader><TableRow>
              <TableHeaderCell>Rule</TableHeaderCell><TableHeaderCell>When</TableHeaderCell><TableHeaderCell>Level</TableHeaderCell>
            </TableRow></TableHeader>
            <TableBody>
              {(policy.data?.rules ?? []).map((r) => {
                const when = (["agent", "capability", "env", "pattern", "rule"] as const)
                  .filter((k) => r[k] !== null && r[k] !== undefined).map((k) => `${k}=${Array.isArray(r[k]) ? (r[k] as string[]).join("|") : String(r[k])}`);
                return (
                  <TableRow key={r.name}>
                    <TableCell><Text size={200} weight="semibold">{r.name}</Text></TableCell>
                    <TableCell><Text size={100} style={{ fontFamily: "Consolas, monospace" }}>{when.join(" · ") || "any"}</Text></TableCell>
                    <TableCell><Level level={r.level} />{r.level === 3 && <Text size={100} block>by {r.approval_from.join(" / ")}</Text>}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        {policy.data && <Text size={200}>Limits: {Object.entries(policy.data.limits).map(([k, v]) => `${k} ${v}`).join(" · ")}</Text>}
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------- incident: agents panel
function EvidenceDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, isLoading, error } = useEvidence(id);
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface style={{ maxWidth: "70vw" }}>
        <DialogBody>
          <DialogTitle>{data ? `${data.kind} · ${data.source} · ${data.redactions} redaction(s)` : "Evidence"}</DialogTitle>
          <DialogContent>
            {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : (
              <>
                <Text size={200} block>Untrusted data: shown as stored, after redaction. {data?.intact ? "Integrity check passed." : "Integrity check FAILED."}</Text>
                <Code>{data?.text}</Code>
              </>
            )}
          </DialogContent>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

export function IncidentAgentPanel({ data }: { data: IncidentAgentView }) {
  const [tab, setTab] = useState("tests");
  const [evidence, setEvidence] = useState<string | null>(null);
  const item = data.work_items[data.work_items.length - 1];
  return (
    <Card style={{ marginBottom: 16 }}>
      <CardHeader
        header={<Text weight="semibold" size={400}>Agents on this incident</Text>}
        description={item ? <span>Graph {item.kind} · <Outcome value={item.status} />{item.waiting_for && <> waiting for <b>{item.waiting_for.replace("_", " ")}</b></>}
          {item.reason && <> · {item.reason}</>}</span> : "Handled before the redesign"}
      />
      <TabList size="small" selectedValue={tab} onTabSelect={(_, d) => setTab(String(d.value))}>
        <Tab value="tests">Databricks tests ({data.tests.length})</Tab>
        <Tab value="requests">Signed requests ({data.requests.length})</Tab>
        <Tab value="evidence">Evidence ({data.evidence.length})</Tab>
        <Tab value="approvals">Approvals ({data.approvals.length})</Tab>
      </TabList>
      {tab === "tests" && (!data.tests.length ? <Empty title="No test runs (only code fixes are tested in Databricks)" /> : (
        <Table size="small" aria-label="Test runs">
          <TableHeader><TableRow><TableHeaderCell>When</TableHeaderCell><TableHeaderCell>Branch</TableHeaderCell><TableHeaderCell>Notebooks</TableHeaderCell>
            <TableHeaderCell>Submitted</TableHeaderCell><TableHeaderCell>Run</TableHeaderCell></TableRow></TableHeader>
          <TableBody>{data.tests.map((t) => (
            <TableRow key={t.id}>
              <TableCell>{dateTime(t.created_at)}</TableCell>
              <TableCell><Text size={200}>{String(t.params.branch ?? "-")}</Text></TableCell>
              <TableCell><Text size={200}>{((t.params.notebooks as string[]) ?? []).join(", ")}</Text></TableCell>
              <TableCell><Outcome value={t.result?.status ?? t.status} /></TableCell>
              <TableCell>{t.result?.output?.run_id ? <Text size={200}>run {String(t.result.output.run_id)}</Text> : "-"}</TableCell>
            </TableRow>
          ))}</TableBody>
        </Table>
      ))}
      {tab === "requests" && <RequestsTable rows={data.requests} compact />}
      {tab === "evidence" && (!data.evidence.length ? <Empty title="No evidence stored" /> : (
        <Table size="small" aria-label="Evidence">
          <TableHeader><TableRow><TableHeaderCell>Kind</TableHeaderCell><TableHeaderCell>Source</TableHeaderCell><TableHeaderCell>Size</TableHeaderCell>
            <TableHeaderCell>Redactions</TableHeaderCell><TableHeaderCell>When</TableHeaderCell><TableHeaderCell /></TableRow></TableHeader>
          <TableBody>{data.evidence.map((e) => (
            <TableRow key={e.id}>
              <TableCell><Badge appearance="outline">{e.kind}</Badge></TableCell><TableCell>{e.source}</TableCell>
              <TableCell>{e.size} B</TableCell><TableCell>{e.redactions}</TableCell><TableCell>{dateTime(e.created_at)}</TableCell>
              <TableCell><Button size="small" icon={<Open16Regular />} onClick={() => setEvidence(e.id)}>View</Button></TableCell>
            </TableRow>
          ))}</TableBody>
        </Table>
      ))}
      {tab === "approvals" && (!data.approvals.length ? <Empty title="No approvals on this incident" /> : (
        <Table size="small" aria-label="Incident approvals"><TableBody>{data.approvals.map((a) => <ApprovalRow key={a.id} a={a} />)}</TableBody></Table>
      ))}
      {evidence && <EvidenceDialog id={evidence} onClose={() => setEvidence(null)} />}
    </Card>
  );
}
