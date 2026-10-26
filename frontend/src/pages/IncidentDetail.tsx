import {
  Badge, Button, Card, CardHeader, Field, Link, MessageBar, MessageBarBody, MessageBarTitle, ProgressBar, Tab, TabList,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, tokens,
} from "@fluentui/react-components";
import { ArrowClockwise20Regular, Open16Regular, PersonArrowLeft20Regular, Wrench20Regular } from "@fluentui/react-icons";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useFixDiff, useIncident, useRetriage, useStartFix, useTakeover } from "../api/queries";
import type { IncidentDetail as Detail } from "../api/types";
import { useAuth } from "../auth/auth";
import { DiffView } from "../components/DiffView";
import { Code, Empty, ErrorState, Loading, PageHeader, ReasonDialog } from "../components/common";
import { ago, dateTime, duration, num, usd } from "../components/format";
import { Lifecycle, buildSteps } from "../components/Lifecycle";
import { EnvBadge, ErrorClass, FixStatus, IncidentStatus, SuspiciousBadge } from "../components/Status";

function DiagnosisCard({ d }: { d: Detail["diagnosis"] }) {
  if (!d) return <Card><CardHeader header={<Text weight="semibold" size={400}>Diagnosis</Text>} /><Empty title="No diagnosis yet" /></Card>;
  return (
    <Card>
      <CardHeader header={<Text weight="semibold" size={400}>LLM diagnosis</Text>} description={`${d.model} · ${d.prompt_version}`} />
      <Text weight="semibold">{d.summary}</Text>
      <Text>{d.root_cause}</Text>
      <Field label={`Confidence ${Math.round(d.confidence * 100)}%`}>
        <ProgressBar value={d.confidence} color={d.confidence >= 0.8 ? "success" : d.confidence >= 0.5 ? "warning" : "error"} aria-label="Diagnosis confidence" />
      </Field>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Badge appearance="outline">class: {d.error_class}</Badge>
        <Badge appearance="outline">fix type: {d.fix_type}</Badge>
        {d.failing_component && <Badge appearance="outline">{d.failing_component}</Badge>}
      </div>
      <Text weight="semibold">Suggested fix</Text>
      <Text>{d.suggested_fix}</Text>
      {d.evidence?.length > 0 && <><Text weight="semibold">Evidence</Text><Code>{d.evidence.join("\n\n")}</Code></>}
    </Card>
  );
}

function FixCard({ data }: { data: Detail }) {
  const fix = data.fix!;
  const diff = useFixDiff(fix.id);
  const [tab, setTab] = useState<"diff" | "comments" | "history">("diff");
  return (
    <Card>
      <CardHeader
        header={<Text weight="semibold" size={400}>Agent fix #{fix.id}</Text>}
        description={<span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><FixStatus state={fix.state} target={fix.target} awaiting={fix.awaiting_human} /> branch <code>{fix.branch}</code> · {fix.repairs} repair(s)</span>}
      />
      {fix.awaiting_human && (
        <MessageBar intent="warning">
          <MessageBarBody>
            <MessageBarTitle>Waiting for you</MessageBarTitle>
            Review the PR into <b>{fix.target}</b> in GitHub, approve and merge it. The agent never merges into {fix.promotion[fix.promotion.length - 1]}.
          </MessageBarBody>
        </MessageBar>
      )}
      {fix.error && <MessageBar intent="error"><MessageBarBody><MessageBarTitle>Stopped</MessageBarTitle>{fix.error}</MessageBarBody></MessageBar>}
      {fix.proposal?.summary && <Text><b>Change:</b> {fix.proposal.summary}</Text>}
      {fix.proposal?.last_repair && <Text><b>Last repair:</b> {fix.proposal.last_repair}</Text>}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {Object.entries(fix.prs).map(([branch, url]) => (
          <Link key={branch} href={url} target="_blank">PR → {branch} <Open16Regular /></Link>
        ))}
        {fix.proposal?.model && <Badge appearance="outline">model: {fix.proposal.model}</Badge>}
        {fix.proposal?.risk && <Badge appearance="outline">risk (model's view): {fix.proposal.risk}</Badge>}
      </div>
      <TabList selectedValue={tab} onTabSelect={(_, d) => setTab(d.value as typeof tab)} size="small">
        <Tab value="diff">Code change</Tab>
        <Tab value="comments">PR comments {diff.data?.comments.length ? `(${diff.data.comments.length})` : ""}</Tab>
        <Tab value="history">Steps ({fix.history.length})</Tab>
      </TabList>
      {tab === "diff" && (diff.isLoading ? <Loading label="Loading diff from GitHub" /> : diff.data?.available ? <DiffView diff={diff.data} /> : <Empty title="Diff not available">{diff.data?.reason}</Empty>)}
      {tab === "comments" && (
        diff.data?.comments.length ? diff.data.comments.map((c, i) => (
          <div key={i} style={{ borderLeft: `3px solid ${tokens.colorBrandStroke1}`, paddingLeft: 10, marginBottom: 10 }}>
            <Text size={200} weight="semibold">{c.author}</Text> <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>{ago(c.created_at)}</Text>
            <Code>{c.body}</Code>
          </div>
        )) : <Empty title="No PR comments" />
      )}
      {tab === "history" && (
        <Table size="small" aria-label="Fix steps">
          <TableBody>
            {fix.history.map((h, i) => (
              <TableRow key={i}><TableCell style={{ whiteSpace: "nowrap" }}>{dateTime(h.ts)}</TableCell><TableCell><FixStatus state={h.state} /></TableCell><TableCell>{h.note}</TableCell></TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

export function IncidentDetail() {
  const id = Number(useParams().id);
  const { data, error, isLoading } = useIncident(id);
  const { can } = useAuth();
  const retriage = useRetriage();
  const startFix = useStartFix();
  const takeover = useTakeover();
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorState error={error} />;
  const inc = data.incident;
  const active = data.fix && ["planned", "pr_open", "deploying", "verifying"].includes(data.fix.state);
  return (
    <>
      <PageHeader
        eyebrow={`Incident #${inc.id} · ${inc.platform_id}`}
        title={inc.pipeline_name}
        subtitle={
          <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <IncidentStatus status={inc.status} /><EnvBadge env={inc.env} /><ErrorClass value={inc.error_class} rule={inc.matched_rule} />
            {inc.suspicious_content && <SuspiciousBadge />}
            opened {ago(inc.created_at)} {inc.url && <Link href={inc.url} target="_blank">failed run <Open16Regular /></Link>}
          </span>
        }
        actions={
          <>
            {can("operator") && (
              <Button icon={<ArrowClockwise20Regular />} disabled={retriage.isPending} onClick={() => retriage.mutate({ id })}>
                {retriage.isPending ? "Diagnosing…" : "Re-run diagnosis"}
              </Button>
            )}
            {can("approver") && !active && inc.fix_eligible && inc.status !== "resolved" && (
              <ReasonDialog
                title="Start an agent fix?"
                description="The agent writes a fix, opens a PR to dev and promotes it through qa. Prod still needs your approval in GitHub."
                confirmLabel="Start fix"
                onConfirm={(reason) => startFix.mutateAsync({ id, reason })}
                trigger={(open) => <Button appearance="primary" icon={<Wrench20Regular />} onClick={open}>Start agent fix</Button>}
              />
            )}
            {can("operator") && inc.status !== "resolved" && (
              <ReasonDialog
                danger
                title="Take over this incident?"
                description="The agent stops working on it (an active fix is stopped; open PRs stay for you) and the incident is marked escalated."
                confirmLabel="Take over"
                onConfirm={(reason) => takeover.mutateAsync({ id, reason })}
                trigger={(open) => <Button icon={<PersonArrowLeft20Regular />} onClick={open}>Take over</Button>}
              />
            )}
          </>
        }
      />
      {retriage.error && <MessageBar intent="error" style={{ marginBottom: 12 }}><MessageBarBody>{String(retriage.error.message)}</MessageBarBody></MessageBar>}

      <Card style={{ marginBottom: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Lifecycle</Text>} description="Built from the audit trail. Hover a step for details." />
        <Lifecycle steps={buildSteps(inc, data.fix, data.audit)} />
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16 }}>
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Error</Text>} description="Redacted and condensed (secrets masked, stack frames trimmed)" />
          <Code>{data.error_text}</Code>
        </Card>
        <DiagnosisCard d={data.diagnosis} />
      </div>

      {data.fix && <div style={{ marginTop: 16 }}><FixCard data={data} /></div>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16, marginTop: 16 }}>
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>LLM calls</Text>} description={`${num(inc.llm_tokens)} tokens · ${usd(inc.llm_cost_usd)}`} />
          {data.llm_calls.length === 0 ? <Empty title="No LLM calls" /> : (
            <Table size="small" aria-label="LLM calls for this incident">
              <TableHeader><TableRow><TableHeaderCell>When</TableHeaderCell><TableHeaderCell>Purpose</TableHeaderCell><TableHeaderCell>Model</TableHeaderCell><TableHeaderCell>Tokens</TableHeaderCell><TableHeaderCell>Latency</TableHeaderCell><TableHeaderCell>Result</TableHeaderCell></TableRow></TableHeader>
              <TableBody>
                {data.llm_calls.map((l, i) => (
                  <TableRow key={i}>
                    <TableCell>{dateTime(l.ts)}</TableCell><TableCell>{l.purpose}</TableCell>
                    <TableCell><Text size={200} truncate wrap={false} style={{ maxWidth: 180, display: "block" }}>{l.model}</Text></TableCell>
                    <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{num(l.prompt_tokens + l.completion_tokens)}</TableCell>
                    <TableCell>{duration(l.latency_ms / 1000)}</TableCell>
                    <TableCell>{l.success ? <Badge color="success" appearance="tint">ok</Badge> : <Badge color="danger" appearance="tint" title={l.error ?? ""}>error</Badge>}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Audit trail</Text>} description="Every agent and human action on this incident" />
          <div style={{ maxHeight: 320, overflow: "auto" }}>
            <Table size="small" aria-label="Incident audit trail">
              <TableBody>
                {data.audit.map((a, i) => (
                  <TableRow key={i}>
                    <TableCell style={{ whiteSpace: "nowrap" }}>{dateTime(a.ts)}</TableCell>
                    <TableCell><Text weight="semibold" size={200}>{a.action.replace(/_/g, " ")}</Text></TableCell>
                    <TableCell><Text size={200}>{a.actor}</Text></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
      </div>
    </>
  );
}
