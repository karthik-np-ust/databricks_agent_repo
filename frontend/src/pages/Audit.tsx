import { Button, Card, Input, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, tokens } from "@fluentui/react-components";
import { ArrowDownload20Regular, Search20Regular } from "@fluentui/react-icons";
import { useState } from "react";
import { useAudit } from "../api/queries";
import { Empty, ErrorState, Loading, PageHeader } from "../components/common";
import { dateTime, download, toCsv } from "../components/format";

export function Audit() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [q, setQ] = useState("");
  const { data, error, isLoading } = useAudit({ actor, action, q });
  return (
    <>
      <PageHeader
        eyebrow="Compliance"
        title="Audit log"
        subtitle="Append-only record of every agent and human action (values are redacted before they are stored)"
        actions={
          <Button icon={<ArrowDownload20Regular />} disabled={!data?.length}
            onClick={() => download(`audit-${new Date().toISOString().slice(0, 19)}.csv`, toCsv((data ?? []) as unknown as Record<string, unknown>[]))}>
            Export CSV
          </Button>
        }
      />
      <div style={{ display: "flex", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <Input placeholder="Actor (exact, e.g. agent)" value={actor} onChange={(_, d) => setActor(d.value)} />
        <Input placeholder="Action (exact, e.g. fix_pr_opened)" value={action} onChange={(_, d) => setAction(d.value)} />
        <Input contentBefore={<Search20Regular />} placeholder="Target contains…" value={q} onChange={(_, d) => setQ(d.value)} />
      </div>
      <Card>
        {isLoading ? <Loading /> : error ? <ErrorState error={error} /> : !data?.length ? <Empty title="No matching entries" /> : (
          <div style={{ maxHeight: "70vh", overflow: "auto" }}>
            <Table size="small" aria-label="Audit log">
              <TableHeader><TableRow>
                <TableHeaderCell>When</TableHeaderCell><TableHeaderCell>Actor</TableHeaderCell><TableHeaderCell>Action</TableHeaderCell>
                <TableHeaderCell>Target</TableHeaderCell><TableHeaderCell style={{ width: "40%" }}>Details</TableHeaderCell>
              </TableRow></TableHeader>
              <TableBody>
                {data.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell style={{ whiteSpace: "nowrap" }}>{dateTime(a.ts)}</TableCell>
                    <TableCell><Text size={200} weight={a.actor === "agent" ? "regular" : "semibold"}>{a.actor}</Text></TableCell>
                    <TableCell><Text size={200} weight="semibold">{a.action}</Text></TableCell>
                    <TableCell><Text size={200} style={{ wordBreak: "break-all" }}>{a.target}</Text></TableCell>
                    <TableCell>
                      <Text size={100} style={{ fontFamily: "Consolas, monospace", color: tokens.colorNeutralForeground3, wordBreak: "break-word" }}>
                        {JSON.stringify(a.details)}
                      </Text>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>
    </>
  );
}
