import { Card, CardHeader, Tab, TabList, Text, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow } from "@fluentui/react-components";
import { useState, type ReactNode } from "react";

export interface TableView {
  columns: string[];
  rows: (string | number | null)[][];
}

/** Every chart has a table-view twin (values reachable without hover or color). */
export function ChartCard({ title, subtitle, chart, table, actions }: {
  title: string; subtitle?: string; chart: ReactNode; table: TableView; actions?: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Card style={{ height: "100%" }}>
      <CardHeader
        header={<Text weight="semibold" size={400}>{title}</Text>}
        description={subtitle ? <Text size={200} style={{ opacity: 0.8 }}>{subtitle}</Text> : undefined}
        action={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {actions}
            <TabList size="small" selectedValue={view} onTabSelect={(_, d) => setView(d.value as "chart" | "table")}>
              <Tab value="chart">Chart</Tab>
              <Tab value="table">Table</Tab>
            </TabList>
          </div>
        }
      />
      {view === "chart" ? (
        chart
      ) : (
        <div style={{ maxHeight: 280, overflow: "auto" }}>
          <Table size="small" aria-label={`${title} (table view)`}>
            <TableHeader>
              <TableRow>{table.columns.map((c) => <TableHeaderCell key={c}>{c}</TableHeaderCell>)}</TableRow>
            </TableHeader>
            <TableBody>
              {table.rows.map((r, i) => (
                <TableRow key={i}>
                  {r.map((v, j) => (
                    <TableCell key={j} style={{ fontVariantNumeric: "tabular-nums" }}>{v ?? "-"}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Card>
  );
}
