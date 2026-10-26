import { Button, Card, Link, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text } from "@fluentui/react-components";
import { Open16Regular } from "@fluentui/react-icons";
import { useNavigate } from "react-router-dom";
import { useApprovals, useFixes } from "../api/queries";
import type { Fix } from "../api/types";
import { Empty, ErrorState, Loading, PageHeader } from "../components/common";
import { ago } from "../components/format";
import { FixStatus } from "../components/Status";

function FixTable({ fixes, empty }: { fixes: Fix[]; empty: string }) {
  const nav = useNavigate();
  if (!fixes.length) return <Empty title={empty} />;
  return (
    <Table aria-label="Agent fixes">
      <TableHeader><TableRow>
        <TableHeaderCell>Fix</TableHeaderCell><TableHeaderCell>Incident</TableHeaderCell><TableHeaderCell>State</TableHeaderCell>
        <TableHeaderCell style={{ width: "34%" }}>Change</TableHeaderCell><TableHeaderCell>Pull requests</TableHeaderCell>
        <TableHeaderCell>Repairs</TableHeaderCell><TableHeaderCell>Last step</TableHeaderCell><TableHeaderCell />
      </TableRow></TableHeader>
      <TableBody>
        {fixes.map((f) => (
          <TableRow key={f.id}>
            <TableCell>#{f.id}</TableCell>
            <TableCell>#{f.incident_id} {f.pipeline_name}</TableCell>
            <TableCell><FixStatus state={f.state} target={f.target} awaiting={f.awaiting_human} /></TableCell>
            <TableCell><Text size={200} block truncate wrap={false} style={{ maxWidth: 380 }}>{f.proposal?.summary ?? f.error ?? "-"}</Text></TableCell>
            <TableCell>
              <span style={{ display: "inline-flex", gap: 8 }}>
                {Object.entries(f.prs).map(([b, u]) => <Link key={b} href={u} target="_blank">{b} <Open16Regular /></Link>)}
              </span>
            </TableCell>
            <TableCell>{f.repairs}</TableCell>
            <TableCell>{ago(f.updated_at)}</TableCell>
            <TableCell><Button size="small" onClick={() => nav(`/incidents/${f.incident_id}`)}>Open</Button></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function Fixes() {
  const { data, error, isLoading } = useFixes();
  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader eyebrow="Self-healing" title="Agent fixes" subtitle="Every fix the agent wrote, and where it is in dev → qa → your approval → prod." />
      <Card><FixTable fixes={data ?? []} empty="The agent hasn't written any fixes yet" /></Card>
    </>
  );
}

export function Approvals() {
  const { data, error, isLoading } = useApprovals();
  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader
        eyebrow="Human in the loop"
        title="Waiting for your approval"
        subtitle="Approvals happen in GitHub (it enforces reviews and records who approved). These are the PRs the agent can't merge itself."
      />
      <Card><FixTable fixes={data ?? []} empty="Nothing is waiting for you" /></Card>
    </>
  );
}
