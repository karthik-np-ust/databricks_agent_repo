import { Card, Dropdown, Option, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, tokens } from "@fluentui/react-components";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIncidents } from "../api/queries";
import { Empty, ErrorState, Loading, PageHeader } from "../components/common";
import { ago, num } from "../components/format";
import { EnvBadge, ErrorClass, FixStatus, IncidentStatus, SuspiciousBadge } from "../components/Status";

const STATUSES = ["all", "open", "fixing", "escalated", "resolved"];

export function Incidents() {
  const { data, error, isLoading } = useIncidents();
  const [status, setStatus] = useState("all");
  const nav = useNavigate();
  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  const rows = (data ?? []).filter((i) => status === "all" || i.status === status);
  return (
    <>
      <PageHeader eyebrow="Incidents" title="Failures the agent handled or escalated" subtitle="Click an incident for its full story: error, diagnosis, fix, approvals and audit." />
      <div style={{ marginBottom: 12 }}>
        <Dropdown value={status === "all" ? "All statuses" : status} selectedOptions={[status]} onOptionSelect={(_, d) => setStatus(String(d.optionValue))}>
          {STATUSES.map((s) => <Option key={s} value={s}>{s === "all" ? "All statuses" : s}</Option>)}
        </Dropdown>
      </div>
      <Card>
        {rows.length === 0 ? <Empty title="No incidents">Failures appear here as soon as the agent detects them.</Empty> : (
          <Table aria-label="Incidents">
            <TableHeader><TableRow>
              <TableHeaderCell>#</TableHeaderCell><TableHeaderCell>Pipeline</TableHeaderCell><TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Class</TableHeaderCell><TableHeaderCell style={{ width: "34%" }}>What happened</TableHeaderCell>
              <TableHeaderCell>Agent fix</TableHeaderCell><TableHeaderCell>LLM tokens</TableHeaderCell><TableHeaderCell>Opened</TableHeaderCell>
            </TableRow></TableHeader>
            <TableBody>
              {rows.map((i) => (
                <TableRow key={i.id} onClick={() => nav(`/incidents/${i.id}`)} style={{ cursor: "pointer" }}>
                  <TableCell>{i.id}</TableCell>
                  <TableCell><span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Text weight="semibold">{i.pipeline_name}</Text><EnvBadge env={i.env} /></span></TableCell>
                  <TableCell><IncidentStatus status={i.status} /></TableCell>
                  <TableCell><ErrorClass value={i.error_class} rule={i.matched_rule} /></TableCell>
                  <TableCell>
                    <Text size={200} block truncate wrap={false} style={{ maxWidth: 420 }}>{i.summary ?? i.headline}</Text>
                    {i.suspicious_content && <SuspiciousBadge />}
                    {!i.summary && <Text size={100} style={{ color: tokens.colorNeutralForeground3 }}>no diagnosis yet</Text>}
                  </TableCell>
                  <TableCell><FixStatus state={i.fix_state} /></TableCell>
                  <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{num(i.llm_tokens)}</TableCell>
                  <TableCell>{ago(i.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
