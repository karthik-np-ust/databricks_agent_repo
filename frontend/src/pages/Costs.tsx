import { Card, CardHeader, MessageBar, MessageBarBody, ProgressBar, Tab, TabList, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from "@fluentui/react-components";
import type { EChartsOption } from "echarts";
import { useState } from "react";
import { useComputeCosts, useLlmCosts } from "../api/queries";
import { ChartCard } from "../components/ChartCard";
import { EChart } from "../components/EChart";
import { Empty, ErrorState, Grid, Kpi, Loading, PageHeader } from "../components/common";
import { num, usd } from "../components/format";
import { useMode } from "../mode";
import { BAR_RADIUS, SERIES, baseOption, envColor } from "../viz";

const ENVS = ["prod", "qa", "dev"] as const;

export function Costs() {
  const [days, setDays] = useState(14);
  const compute = useComputeCosts(days);
  const llm = useLlmCosts(days);
  const mode = useMode();
  const gap = mode === "dark" ? "#292929" : "#ffffff";

  const header = (
    <PageHeader
      eyebrow="FinOps"
      title="Cost & FinOps"
      subtitle="Compute usage per pipeline and environment, and the agent's own LLM spend against its budgets"
      actions={
        // One filter row scopes every chart on the page.
        <TabList selectedValue={String(days)} onTabSelect={(_, d) => setDays(Number(d.value))}>
          <Tab value="7">7 days</Tab><Tab value="14">14 days</Tab><Tab value="30">30 days</Tab>
        </TabList>
      }
    />
  );
  if (compute.isLoading || llm.isLoading) return <>{header}<Loading label="Querying billing data" /></>;
  if (llm.error) return <>{header}<ErrorState error={llm.error} /></>;
  const c = compute.data;
  const l = llm.data!;
  const b = baseOption(mode);
  const presentEnvs = ENVS.filter((e) => c?.by_day.some((d) => d[e]));

  const dbuOption: EChartsOption = {
    ...b,
    legend: { ...b.legend, show: presentEnvs.length >= 2 },
    xAxis: { ...b.xAxis, type: "category", data: c?.by_day.map((d) => d.date.slice(5)) ?? [] },
    yAxis: { ...b.yAxis, type: "value", name: c?.unit, nameTextStyle: { color: b.yAxis.axisLabel.color } },
    series: presentEnvs.map((e, i) => ({
      name: e.toUpperCase(), type: "bar", stack: "dbu", barMaxWidth: 28,
      data: c?.by_day.map((d) => d[e] ?? 0) ?? [],
      itemStyle: { color: envColor(e, mode), borderColor: gap, borderWidth: 2, ...(i === presentEnvs.length - 1 ? { borderRadius: BAR_RADIUS } : {}) },
    })),
  } as EChartsOption;

  const tokensOption: EChartsOption = {
    ...b,
    legend: { show: false },
    xAxis: { ...b.xAxis, type: "category", data: l.by_day.map((d) => d.date.slice(5)) },
    yAxis: { ...b.yAxis, type: "value" },
    series: [{ name: "Tokens", type: "bar", barMaxWidth: 28, data: l.by_day.map((d) => d.tokens), itemStyle: { color: SERIES[mode][0], borderRadius: BAR_RADIUS } }],
  } as EChartsOption;

  const pipeOption: EChartsOption = {
    ...b,
    legend: { show: false },
    tooltip: { ...b.tooltip, axisPointer: { type: "shadow" } },
    grid: { ...b.grid, top: 8 },
    xAxis: { ...b.yAxis, type: "value" },
    yAxis: { ...b.xAxis, type: "category", data: c?.by_pipeline.slice(0, 10).map((p) => p.pipeline).reverse() ?? [] },
    series: [{ type: "bar", barMaxWidth: 18, data: c?.by_pipeline.slice(0, 10).map((p) => p.quantity).reverse() ?? [],
      itemStyle: { color: SERIES[mode][0], borderRadius: [0, 4, 4, 0] } }],
  } as EChartsOption;

  const reqPct = l.budgets.per_day_requests ? l.today.calls / l.budgets.per_day_requests : 0;
  const costPct = l.budgets.per_day_usd ? l.today.cost_usd / l.budgets.per_day_usd : 0;
  return (
    <>
      {header}
      {c?.is_estimate && <MessageBar intent="info" style={{ marginBottom: 12 }}><MessageBarBody>Compute figures are estimates from run duration (billing table not enabled for at least one platform).</MessageBarBody></MessageBar>}
      {c?.errors.map((e) => <MessageBar key={e} intent="warning" style={{ marginBottom: 12 }}><MessageBarBody>Billing query failed: {e}</MessageBarBody></MessageBar>)}
      <Grid min={200}>
        <Kpi label={`Compute (${days}d)`} value={`${num(c?.total, 2)} ${c?.unit ?? "DBU"}`} hint="all monitored pipelines" />
        <Kpi label="LLM requests today" value={`${l.today.calls} / ${l.budgets.per_day_requests}`}
          accent={<ProgressBar value={Math.min(1, reqPct)} color={reqPct > 0.8 ? "warning" : "brand"} style={{ width: 60 }} aria-label="Request budget used" />} />
        <Kpi label="LLM cost today" value={usd(l.today.cost_usd)} hint={`budget ${usd(l.budgets.per_day_usd)}/day`}
          accent={<ProgressBar value={Math.min(1, costPct)} color={costPct > 0.8 ? "warning" : "brand"} style={{ width: 60 }} aria-label="Cost budget used" />} />
        <Kpi label="LLM tokens today" value={num(l.today.tokens)} hint={`per incident cap ${num(l.budgets.per_incident_tokens)}`} />
      </Grid>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16, marginTop: 16 }}>
        <ChartCard
          title={`Compute usage by environment (${c?.unit ?? "DBU"})`}
          subtitle="Stacked per day · prod teal, qa orange, dev violet"
          chart={c?.by_day.length ? <EChart option={dbuOption} label="Stacked bar chart of compute usage per day by environment" /> : <Empty title="No compute records" />}
          table={{ columns: ["Day", ...presentEnvs.map((e) => e.toUpperCase())], rows: (c?.by_day ?? []).map((d) => [d.date, ...presentEnvs.map((e) => num(d[e] ?? 0, 3))]) }}
        />
        <ChartCard
          title="Top pipelines by compute"
          subtitle={`Last ${days} days`}
          chart={c?.by_pipeline.length ? <EChart option={pipeOption} label="Bar chart of compute by pipeline" /> : <Empty title="No compute records" />}
          table={{ columns: ["Pipeline", c?.unit ?? "DBU"], rows: (c?.by_pipeline ?? []).map((p) => [p.pipeline, num(p.quantity, 3)]) }}
        />
        <ChartCard
          title="Agent LLM tokens per day"
          subtitle="Triage and fix calls"
          chart={l.by_day.length ? <EChart option={tokensOption} label="Bar chart of LLM tokens per day" /> : <Empty title="No LLM calls yet" />}
          table={{ columns: ["Day", "Calls", "Tokens", "Failed", "Cost"], rows: l.by_day.map((d) => [d.date, d.calls, num(d.tokens), d.failed, usd(d.cost_usd)]) }}
        />
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>LLM spend per incident</Text>} />
          {l.per_incident.length === 0 ? <Empty title="No incidents with LLM usage" /> : (
            <Table size="small" aria-label="LLM spend per incident">
              <TableHeader><TableRow><TableHeaderCell>Incident</TableHeaderCell><TableHeaderCell>Pipeline</TableHeaderCell><TableHeaderCell>Tokens</TableHeaderCell><TableHeaderCell>Cost</TableHeaderCell></TableRow></TableHeader>
              <TableBody>
                {l.per_incident.map((p) => (
                  <TableRow key={p.incident_id}><TableCell>#{p.incident_id}</TableCell><TableCell>{p.pipeline}</TableCell>
                    <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{num(p.tokens)}</TableCell><TableCell>{usd(p.cost_usd)}</TableCell></TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
