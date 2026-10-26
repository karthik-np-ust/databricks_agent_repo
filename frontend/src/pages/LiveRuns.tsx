import {
  Button, Card, Link, ProgressBar, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text, tokens,
} from "@fluentui/react-components";
import { Stop20Regular, Warning16Filled } from "@fluentui/react-icons";
import { useActiveRuns, useCancelRun } from "../api/queries";
import { useAuth } from "../auth/auth";
import { Empty, ErrorState, Loading, PageHeader, ReasonDialog } from "../components/common";
import { dateTime, duration } from "../components/format";
import { EnvBadge, RunStatus } from "../components/Status";

export function LiveRuns() {
  const { data, error, isLoading } = useActiveRuns();
  const cancel = useCancelRun();
  const { can } = useAuth();
  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader eyebrow="Live" title="Running now" subtitle="Live from the platforms, refreshed every 10 seconds. Sorted by how close each run is to its threshold." />
      <Card>
        {!data?.length ? <Empty title="Nothing is running right now" /> : (
          <Table aria-label="Active runs">
            <TableHeader><TableRow>
              <TableHeaderCell>Pipeline</TableHeaderCell><TableHeaderCell>Env</TableHeaderCell><TableHeaderCell>Status</TableHeaderCell>
              <TableHeaderCell>Started</TableHeaderCell><TableHeaderCell style={{ width: "30%" }}>Elapsed vs threshold</TableHeaderCell><TableHeaderCell />
            </TableRow></TableHeader>
            <TableBody>
              {data.map((r) => {
                const pct = r.pct_of_threshold;
                const over = pct !== null && pct >= 100;
                const near = pct !== null && pct >= 80;
                return (
                  <TableRow key={r.run_id}>
                    <TableCell><Text weight="semibold">{r.pipeline_name}</Text> {r.url && <Link href={r.url} target="_blank">run {r.run_id}</Link>}</TableCell>
                    <TableCell><EnvBadge env={r.env} /></TableCell>
                    <TableCell><RunStatus status={r.status} /></TableCell>
                    <TableCell>{dateTime(r.start_time)}</TableCell>
                    <TableCell>
                      <ProgressBar value={pct === null ? undefined : Math.min(1, pct / 100)} color={over ? "error" : near ? "warning" : "brand"} thickness="large" aria-label="Elapsed share of threshold" />
                      <Text size={200} style={{ color: over ? tokens.colorPaletteRedForeground1 : tokens.colorNeutralForeground3, display: "inline-flex", gap: 4, alignItems: "center" }}>
                        {over && <Warning16Filled />}
                        {duration(r.elapsed_s)} {r.threshold_s ? `of ${duration(r.threshold_s)} (${pct}%)` : "(no threshold yet)"}
                      </Text>
                    </TableCell>
                    <TableCell>
                      {can("operator") && r.can_cancel && (
                        <ReasonDialog
                          danger
                          title={`Cancel ${r.pipeline_name} run ${r.run_id}?`}
                          description="The run is cancelled on the platform. The agent will not rerun it automatically."
                          confirmLabel="Cancel run"
                          onConfirm={(reason) => cancel.mutateAsync({ platform_id: r.platform_id, pipeline_id: r.pipeline_id, run_id: r.run_id, reason })}
                          trigger={(open) => <Button size="small" icon={<Stop20Regular />} onClick={open}>Cancel</Button>}
                        />
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
