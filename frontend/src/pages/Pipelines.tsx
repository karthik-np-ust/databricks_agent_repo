import {
  Badge, Button, Card, Dropdown, Input, Option, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, Tooltip,
} from "@fluentui/react-components";
import { Search20Regular } from "@fluentui/react-icons";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePipelines } from "../api/queries";
import { Empty, ErrorState, Loading, PageHeader } from "../components/common";
import { ago, duration } from "../components/format";
import { EnvBadge, RunStatus } from "../components/Status";

export function Pipelines() {
  const { data, error, isLoading } = usePipelines();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [env, setEnv] = useState("all");
  const rows = useMemo(
    () => (data ?? []).filter((p) => (env === "all" || p.env === env) && p.name.toLowerCase().includes(q.toLowerCase())),
    [data, q, env],
  );
  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader eyebrow="Inventory" title="Pipelines" subtitle="Every pipeline the agent monitors, across platforms and environments" />
      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        <Input contentBefore={<Search20Regular />} placeholder="Filter by name" value={q} onChange={(_, d) => setQ(d.value)} />
        <Dropdown value={env === "all" ? "All environments" : env.toUpperCase()} selectedOptions={[env]} onOptionSelect={(_, d) => setEnv(String(d.optionValue))}>
          <Option value="all">All environments</Option><Option value="prod">PROD</Option><Option value="qa">QA</Option><Option value="dev">DEV</Option>
        </Dropdown>
      </div>
      <Card>
        {rows.length === 0 ? <Empty title="No pipelines match" /> : (
          <Table aria-label="Pipelines" size="medium">
            <TableHeader><TableRow>
              <TableHeaderCell>Pipeline</TableHeaderCell><TableHeaderCell>Env</TableHeaderCell><TableHeaderCell>Platform</TableHeaderCell>
              <TableHeaderCell>Last run</TableHeaderCell><TableHeaderCell>Success (14d)</TableHeaderCell><TableHeaderCell>Avg duration</TableHeaderCell>
              <TableHeaderCell>Threshold</TableHeaderCell><TableHeaderCell>Agent</TableHeaderCell><TableHeaderCell>Incidents</TableHeaderCell><TableHeaderCell />
            </TableRow></TableHeader>
            <TableBody>
              {rows.map((p) => (
                <TableRow key={`${p.platform_id}:${p.pipeline_id}`}>
                  <TableCell><Text weight="semibold">{p.name}</Text></TableCell>
                  <TableCell><EnvBadge env={p.env} /></TableCell>
                  <TableCell>{p.platform_id}</TableCell>
                  <TableCell><span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><RunStatus status={p.last_status} /> {ago(p.last_run_at)}</span></TableCell>
                  <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{p.success_rate === null ? "-" : `${p.success_rate}% of ${p.runs_14d}`}</TableCell>
                  <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{duration(p.avg_duration_s)}</TableCell>
                  <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{p.threshold_s ? duration(p.threshold_s) : <Tooltip content="Baseline: p95 of recent successful runs x multiplier" relationship="description"><span>baseline</span></Tooltip>}</TableCell>
                  <TableCell>
                    <span style={{ display: "inline-flex", gap: 4 }}>
                      {p.acts_in_env ? <Badge appearance="tint" color="brand">acts</Badge> : <Badge appearance="outline">notify only</Badge>}
                      {p.auto_fix && <Badge appearance="tint" color="important">auto-fix</Badge>}
                    </span>
                  </TableCell>
                  <TableCell>{p.open_incidents ? <Badge color="danger">{p.open_incidents} open</Badge> : "-"}</TableCell>
                  <TableCell><Button size="small" onClick={() => nav(`/pipelines/${p.platform_id}/${p.pipeline_id}`)}>Details</Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
