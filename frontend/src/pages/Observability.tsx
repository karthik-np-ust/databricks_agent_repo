import { Badge, Card, CardHeader, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, Tooltip } from "@fluentui/react-components";
import { useLlmCalls, useLlmCosts } from "../api/queries";
import { Empty, ErrorState, Grid, Kpi, Loading, PageHeader } from "../components/common";
import { dateTime, duration, num, usd } from "../components/format";

export function Observability() {
  const calls = useLlmCalls();
  const stats = useLlmCosts(14);
  if (calls.isLoading || stats.isLoading) return <Loading />;
  if (calls.error || stats.error) return <ErrorState error={calls.error ?? stats.error} />;
  const all = calls.data ?? [];
  const failed = all.filter((c) => !c.success).length;
  const redactions = all.reduce((s, c) => s + (c.redactions ?? 0), 0);
  return (
    <>
      <PageHeader eyebrow="Agent observability" title="How the agent's AI is behaving" subtitle="Every LLM call: model, tokens, latency, redactions and errors (prompt content is never shown)" />
      <Grid min={200}>
        <Kpi label="LLM calls (recent 200)" value={all.length} hint={`${failed} failed`} />
        <Kpi label="Failure rate" value={all.length ? `${Math.round((100 * failed) / all.length)}%` : "-"} hint="provider errors, timeouts, rate limits" />
        <Kpi label="Secrets redacted" value={redactions} hint="masked before any text left the agent" />
        <Kpi label="Models used" value={stats.data?.by_model.length ?? 0} hint="fallbacks included" />
      </Grid>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Models (14 days)</Text>} description="Fallback order is in config/llm.yaml" />
        <Table size="small" aria-label="Model statistics">
          <TableHeader><TableRow><TableHeaderCell>Model</TableHeaderCell><TableHeaderCell>Calls</TableHeaderCell><TableHeaderCell>Failed</TableHeaderCell><TableHeaderCell>Tokens</TableHeaderCell><TableHeaderCell>Avg latency</TableHeaderCell><TableHeaderCell>Cost</TableHeaderCell></TableRow></TableHeader>
          <TableBody>
            {(stats.data?.by_model ?? []).map((m) => (
              <TableRow key={m.model}>
                <TableCell>{m.model}</TableCell><TableCell>{m.calls}</TableCell>
                <TableCell>{m.failed ? <Badge color="danger" appearance="tint">{m.failed}</Badge> : 0}</TableCell>
                <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{num(m.tokens)}</TableCell>
                <TableCell>{m.avg_latency_ms ? duration(m.avg_latency_ms / 1000) : "-"}</TableCell><TableCell>{usd(m.cost_usd)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Recent LLM calls</Text>} />
        {all.length === 0 ? <Empty title="No LLM calls yet" /> : (
          <div style={{ maxHeight: 520, overflow: "auto" }}>
            <Table size="small" aria-label="Recent LLM calls">
              <TableHeader><TableRow>
                <TableHeaderCell>When</TableHeaderCell><TableHeaderCell>Incident</TableHeaderCell><TableHeaderCell>Purpose</TableHeaderCell>
                <TableHeaderCell>Model</TableHeaderCell><TableHeaderCell>Prompt</TableHeaderCell><TableHeaderCell>Completion</TableHeaderCell>
                <TableHeaderCell>Latency</TableHeaderCell><TableHeaderCell>Redactions</TableHeaderCell><TableHeaderCell>Result</TableHeaderCell>
              </TableRow></TableHeader>
              <TableBody>
                {all.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell style={{ whiteSpace: "nowrap" }}>{dateTime(c.ts)}</TableCell>
                    <TableCell>{c.incident_id ? `#${c.incident_id}` : "-"}</TableCell>
                    <TableCell>{c.purpose}</TableCell>
                    <TableCell><Text size={200}>{c.model}</Text></TableCell>
                    <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{num(c.prompt_tokens)}</TableCell>
                    <TableCell style={{ fontVariantNumeric: "tabular-nums" }}>{num(c.completion_tokens)}</TableCell>
                    <TableCell>{duration(c.latency_ms / 1000)}</TableCell>
                    <TableCell>{c.redactions ?? 0}</TableCell>
                    <TableCell>
                      {c.success ? <Badge color="success" appearance="tint">ok</Badge> : (
                        <Tooltip content={c.error ?? "error"} relationship="description"><Badge color="danger" appearance="tint">error</Badge></Tooltip>
                      )}
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
