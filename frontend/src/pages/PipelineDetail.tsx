import {
  Button, Card, CardHeader, Link, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text,
} from "@fluentui/react-components";
import { ArrowRepeatAll20Regular, Open16Regular } from "@fluentui/react-icons";
import type { EChartsOption } from "echarts";
import { useNavigate, useParams } from "react-router-dom";
import { usePipeline, useRerun } from "../api/queries";
import { useAuth } from "../auth/auth";
import { ChartCard } from "../components/ChartCard";
import { EChart } from "../components/EChart";
import { Code, Empty, ErrorState, Grid, Kpi, Loading, PageHeader, ReasonDialog } from "../components/common";
import { ago, dateTime, duration } from "../components/format";
import { EnvBadge, IncidentStatus, RunStatus } from "../components/Status";
import { useMode } from "../mode";
import { BAR_RADIUS, CHROME, STATUS, baseOption } from "../viz";

export function PipelineDetail() {
  const { platform = "", id = "" } = useParams();
  const { data, error, isLoading } = usePipeline(platform, id);
  const rerun = useRerun();
  const { can } = useAuth();
  const mode = useMode();
  const nav = useNavigate();
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorState error={error} />;
  const p = data.pipeline;
  const runs = [...data.runs].reverse().filter((r) => r.duration_s !== null);
  const b = baseOption(mode);
  const option: EChartsOption = {
    ...b,
    legend: { show: false }, // one series: the title names it; status is in the bar color + tooltip + table
    tooltip: { ...b.tooltip, trigger: "item", formatter: (x: any) => `${x.name}<br/>${x.data.status}: ${duration(x.value)}` },
    xAxis: { ...b.xAxis, type: "category", data: runs.map((r) => dateTime(r.start_time)), axisLabel: { ...b.xAxis.axisLabel, hideOverlap: true } },
    yAxis: { ...b.yAxis, type: "value", axisLabel: { ...b.yAxis.axisLabel, formatter: (v: number) => duration(v) } },
    series: [{
      type: "bar", barMaxWidth: 22,
      data: runs.map((r) => ({ value: r.duration_s, status: r.status,
        itemStyle: { color: r.status === "failed" ? STATUS.critical : r.status === "succeeded" ? STATUS.good : CHROME[mode].muted, borderRadius: BAR_RADIUS } })),
      markLine: p.threshold_s ? {
        symbol: "none", silent: true,
        lineStyle: { color: STATUS.serious, type: "solid", width: 1.5 },
        label: { formatter: `threshold ${duration(p.threshold_s)}`, color: CHROME[mode].text2, position: "insideEndTop" },
        data: [{ yAxis: p.threshold_s }],
      } : undefined,
    }],
  } as EChartsOption;
  return (
    <>
      <PageHeader
        eyebrow={`${p.platform_id} · pipeline`}
        title={p.name}
        subtitle={<span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><EnvBadge env={p.env} /> job {p.pipeline_id} {p.url && <Link href={p.url} target="_blank">open in platform <Open16Regular /></Link>}</span>}
        actions={can("operator") ? (
          <ReasonDialog
            title={`Rerun ${p.name} (${p.env})?`}
            description="Queued as your request: policy is checked, the request is signed and the data plane starts a new run (job defaults) within a poll. Track it under Requests & ledger."
            confirmLabel="Rerun"
            onConfirm={(reason) => rerun.mutateAsync({ platform_id: p.platform_id, pipeline_id: p.pipeline_id, reason })}
            trigger={(open) => <Button icon={<ArrowRepeatAll20Regular />} onClick={open}>Rerun now</Button>}
          />
        ) : undefined}
      />
      <Grid min={180}>
        <Kpi label="Last run" value={<RunStatus status={p.last_status} />} hint={ago(p.last_run_at)} />
        <Kpi label="Success rate (14d)" value={p.success_rate === null ? "-" : `${p.success_rate}%`} hint={`${p.runs_14d} runs`} />
        <Kpi label="Average duration" value={duration(p.avg_duration_s)} hint="successful runs" />
        <Kpi label="Threshold" value={p.threshold_s ? duration(p.threshold_s) : "baseline"} hint={p.acts_in_env ? "agent cancels and reruns" : "notify only in this env"} />
      </Grid>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)", gap: 16, marginTop: 16 }}>
        <ChartCard
          title="Run duration"
          subtitle="Last runs · green succeeded, red failed · line = threshold"
          chart={runs.length ? <EChart option={option} label="Bar chart of run durations with threshold line" /> : <Empty title="No completed runs yet" />}
          table={{ columns: ["Start", "Status", "Duration"], rows: runs.map((r) => [dateTime(r.start_time), r.status, duration(r.duration_s)]) }}
        />
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Policy</Text>} description="config/pipelines.yaml (defaults merged)" />
          <Code>{JSON.stringify(data.policy, null, 2)}</Code>
        </Card>
      </div>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Incidents</Text>} />
        {data.incidents.length === 0 ? <Empty title="No incidents for this pipeline" /> : (
          <Table size="small" aria-label="Pipeline incidents">
            <TableHeader><TableRow><TableHeaderCell>#</TableHeaderCell><TableHeaderCell>Status</TableHeaderCell><TableHeaderCell>Summary</TableHeaderCell><TableHeaderCell>Opened</TableHeaderCell></TableRow></TableHeader>
            <TableBody>
              {data.incidents.map((i) => (
                <TableRow key={i.id} style={{ cursor: "pointer" }} onClick={() => nav(`/incidents/${i.id}`)}>
                  <TableCell>{i.id}</TableCell><TableCell><IncidentStatus status={i.status} /></TableCell>
                  <TableCell>{i.summary ?? i.headline}</TableCell><TableCell>{ago(i.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
