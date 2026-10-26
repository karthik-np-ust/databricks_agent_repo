import {
  Badge, Button, Card, CardHeader, Link, MessageBar, MessageBarActions, MessageBarBody, MessageBarTitle, ProgressBar,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, tokens,
} from "@fluentui/react-components";
import type { EChartsOption } from "echarts";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useOverview, useRuns } from "../api/queries";
import { ChartCard } from "../components/ChartCard";
import { EChart } from "../components/EChart";
import { Empty, ErrorState, Grid, Kpi, Loading, PageHeader } from "../components/common";
import { ago, duration, num, usd } from "../components/format";
import { EnvBadge, ErrorClass, FixStatus, IncidentStatus } from "../components/Status";
import { useMode } from "../mode";
import { BAR_RADIUS, STATUS, baseOption } from "../viz";

function RunsChart() {
  const mode = useMode();
  const { data } = useRuns(24 * 14);
  const days = useMemo(() => {
    const m = new Map<string, { ok: number; failed: number }>();
    for (let i = 13; i >= 0; i--) m.set(new Date(Date.now() - i * 86400_000).toISOString().slice(0, 10), { ok: 0, failed: 0 });
    for (const r of data ?? []) {
      const d = r.start_time?.slice(0, 10);
      if (d && m.has(d)) {
        if (r.status === "succeeded") m.get(d)!.ok++;
        if (r.status === "failed") m.get(d)!.failed++;
      }
    }
    return [...m.entries()];
  }, [data]);
  const b = baseOption(mode);
  const option: EChartsOption = {
    ...b,
    xAxis: { ...b.xAxis, type: "category", data: days.map(([d]) => d.slice(5)) },
    yAxis: { ...b.yAxis, type: "value", minInterval: 1 },
    series: [
      { name: "Succeeded", type: "bar", stack: "runs", data: days.map(([, v]) => v.ok), itemStyle: { color: STATUS.good }, barMaxWidth: 28 },
      // 2px surface gap between stacked segments, rounded data end on top.
      { name: "Failed", type: "bar", stack: "runs", data: days.map(([, v]) => v.failed), barMaxWidth: 28,
        itemStyle: { color: STATUS.critical, borderRadius: BAR_RADIUS as [number, number, number, number], borderColor: mode === "dark" ? "#292929" : "#ffffff", borderWidth: 2 } },
    ],
  } as EChartsOption;
  return (
    <ChartCard
      title="Pipeline runs, last 14 days"
      subtitle="All platforms and environments · succeeded vs failed"
      chart={<EChart option={option} label="Stacked bar chart of succeeded and failed runs per day" />}
      table={{ columns: ["Day", "Succeeded", "Failed"], rows: days.map(([d, v]) => [d, v.ok, v.failed]) }}
    />
  );
}

export function CommandCenter() {
  const { data, error, isLoading } = useOverview();
  const nav = useNavigate();
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorState error={error} />;
  const k = data.kpis;
  const reqPct = k.llm_requests_budget ? k.llm_calls_today / k.llm_requests_budget : 0;
  return (
    <>
      <PageHeader
        eyebrow="Command center"
        title="Production support at a glance"
        subtitle={`${data.agent.platforms.length} platform(s) · ${k.pipelines} pipelines · agent v${data.version}`}
      />

      {data.attention.map((f) => (
        <MessageBar key={f.id} intent="warning" style={{ marginBottom: 12 }}>
          <MessageBarBody>
            <MessageBarTitle>Your approval is needed</MessageBarTitle>
            Agent fix for incident #{f.incident_id} ({f.pipeline_name ?? "pipeline"}) is waiting at <b>{f.target}</b>.
          </MessageBarBody>
          <MessageBarActions>
            <Button onClick={() => nav(`/incidents/${f.incident_id}`)}>Open incident</Button>
            {f.prs[f.target] && <Button appearance="primary" as="a" href={f.prs[f.target]} target="_blank">Review in GitHub</Button>}
          </MessageBarActions>
        </MessageBar>
      ))}

      <Grid min={190}>
        <Kpi label="Pipelines monitored" value={k.pipelines} hint={`prod ${k.pipelines_by_env.prod ?? 0} · qa ${k.pipelines_by_env.qa ?? 0} · dev ${k.pipelines_by_env.dev ?? 0}`} />
        <Kpi label="Running now" value={k.running_now} hint="live, from the platforms" />
        <Kpi label="Runs (24h)" value={k.runs_24h} hint={`${k.runs_24h_failed} failed · ${k.runs_24h_succeeded} succeeded`} />
        <Kpi label="Open incidents" value={k.incidents_open} accent={k.incidents_escalated ? <Badge color="danger">{k.incidents_escalated} escalated</Badge> : undefined} hint={`${k.incidents_total} total`} />
        <Kpi label="Fixed by the agent" value={k.auto_resolved_pct === null ? "-" : `${k.auto_resolved_pct}%`} hint={`${k.incidents_resolved} resolved · MTTR ${duration(k.mttr_s)}`} />
        <Kpi label="Agent fixes in flight" value={k.fixes_active} accent={k.awaiting_human ? <Badge color="warning">{k.awaiting_human} need you</Badge> : undefined} />
        <Kpi
          label="LLM requests today"
          value={`${k.llm_calls_today} / ${k.llm_requests_budget}`}
          hint={<span>{num(k.llm_tokens_today)} tokens · {usd(k.llm_cost_today)} of {usd(k.llm_cost_budget)}</span>}
          accent={<ProgressBar value={Math.min(1, reqPct)} color={reqPct > 0.8 ? "warning" : "brand"} style={{ width: 60 }} aria-label="LLM request budget used" />}
        />
      </Grid>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", gap: 16, marginTop: 16 }}>
        <RunsChart />
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Platforms</Text>} />
          {data.agent.platforms.map((p) => (
            <div key={p.id} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: `1px solid ${tokens.colorNeutralStroke3}` }}>
              <div>
                <Text weight="semibold">{p.display_name}</Text> <Text size={200}>({p.id})</Text>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                  {p.capabilities.map((c) => <Badge key={c} appearance="outline" size="small">{c}</Badge>)}
                </div>
              </div>
              <Text size={200} style={{ color: tokens.colorNeutralForeground3, whiteSpace: "nowrap" }}>polled {ago(p.last_poll)}</Text>
            </div>
          ))}
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 3fr) minmax(0, 2fr)", gap: 16, marginTop: 16 }}>
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Recent incidents</Text>} action={<Link onClick={() => nav("/incidents")}>All incidents</Link>} />
          {data.recent_incidents.length === 0 ? <Empty title="No incidents yet" /> : (
            <Table size="small" aria-label="Recent incidents">
              <TableHeader><TableRow>
                <TableHeaderCell>#</TableHeaderCell><TableHeaderCell>Pipeline</TableHeaderCell><TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Class</TableHeaderCell><TableHeaderCell>Fix</TableHeaderCell><TableHeaderCell>Opened</TableHeaderCell>
              </TableRow></TableHeader>
              <TableBody>
                {data.recent_incidents.map((i) => (
                  <TableRow key={i.id} onClick={() => nav(`/incidents/${i.id}`)} style={{ cursor: "pointer" }}>
                    <TableCell>{i.id}</TableCell>
                    <TableCell><span style={{ display: "inline-flex", gap: 6 }}>{i.pipeline_name} <EnvBadge env={i.env} /></span></TableCell>
                    <TableCell><IncidentStatus status={i.status} /></TableCell>
                    <TableCell><ErrorClass value={i.error_class} rule={i.matched_rule} /></TableCell>
                    <TableCell><FixStatus state={i.fix_state} /></TableCell>
                    <TableCell>{ago(i.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Agent activity</Text>} action={<Link onClick={() => nav("/audit")}>Audit log</Link>} />
          <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 340, overflow: "auto" }}>
            {data.activity.map((a, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "70px 1fr", gap: 8 }}>
                <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>{ago(a.ts)}</Text>
                <div>
                  <Text size={200} weight="semibold">{a.action.replace(/_/g, " ")}</Text>{" "}
                  <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>{a.actor === "agent" ? "" : `by ${a.actor}`}</Text>
                  <Text size={200} block truncate wrap={false} style={{ color: tokens.colorNeutralForeground3 }}>{a.target}</Text>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
