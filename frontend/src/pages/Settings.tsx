import {
  Accordion, AccordionHeader, AccordionItem, AccordionPanel, Badge, Button, Card, CardHeader, MessageBar, MessageBarBody,
  Switch, Table, TableBody, TableCell, TableRow, Text, tokens,
} from "@fluentui/react-components";
import { useOverview, useSetControls, useSettings } from "../api/queries";
import { useAuth } from "../auth/auth";
import { Code, ErrorState, Loading, PageHeader, ReasonDialog } from "../components/common";
import { dateTime } from "../components/format";

function KeyValues({ data }: { data: Record<string, unknown> }) {
  return (
    <Table size="small">
      <TableBody>
        {Object.entries(data).map(([k, v]) => (
          <TableRow key={k}>
            <TableCell style={{ width: "40%" }}><Text size={200} weight="semibold">{k}</Text></TableCell>
            <TableCell><Text size={200} style={{ fontFamily: "Consolas, monospace" }}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</Text></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function Controls() {
  const { data } = useOverview();
  const set = useSetControls();
  const { can } = useAuth();
  if (!data) return null;
  const a = data.agent;
  const toggle = (key: "enabled" | "dry_run", value: boolean, label: string, danger = false) => (
    <ReasonDialog
      danger={danger}
      title={label}
      description="Applied by the running agent on its next poll and recorded in the audit log."
      confirmLabel="Apply"
      onConfirm={(reason) => set.mutateAsync({ [key]: value, reason })}
      trigger={(open) => <Button size="small" onClick={open} disabled={!can("admin")}>Change</Button>}
    />
  );
  return (
    <Card>
      <CardHeader header={<Text weight="semibold" size={400}>Runtime controls</Text>} description="Admin only · overrides .env without restarting the agent" />
      {!can("admin") && <MessageBar intent="info"><MessageBarBody>Sign in as admin to change these.</MessageBarBody></MessageBar>}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <Switch checked={a.enabled} label={a.enabled ? "Agent enabled" : "Agent STOPPED (kill switch)"} readOnly />
        {toggle("enabled", !a.enabled, a.enabled ? "Stop the agent (kill switch)?" : "Re-enable the agent?", a.enabled)}
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <Switch checked={a.dry_run} label={a.dry_run ? "Dry run: decisions only, no cancel/rerun" : "Live: agent cancels and reruns"} readOnly />
        {toggle("dry_run", !a.dry_run, a.dry_run ? "Switch to live actions?" : "Switch to dry run?")}
      </div>
      <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
        The kill switch can only stop the agent; if .env sets AGENT_ENABLED=false it stays off.
      </Text>
    </Card>
  );
}

export function Settings() {
  const { data, error, isLoading } = useSettings();
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorState error={error} />;
  return (
    <>
      <PageHeader eyebrow="Administration" title="Settings & controls" subtitle="Read-only view of the agent's configuration (secrets are shown as references, never values)" />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16 }}>
        <Controls />
        <Card>
          <CardHeader header={<Text weight="semibold" size={400}>Agent identity & process settings</Text>} />
          <KeyValues data={{ github_identity: data.github_identity ?? "not configured", ...data.env_settings }} />
        </Card>
      </div>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Platforms</Text>} description="config/platforms.yaml" />
        {data.platforms.map((p) => (
          <div key={p.id} style={{ padding: "8px 0", borderBottom: `1px solid ${tokens.colorNeutralStroke3}` }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <Text weight="semibold">{p.id}</Text><Badge appearance="outline">{p.plugin}</Badge>
              <Badge appearance="tint" color={p.enabled ? "success" : "subtle"}>{p.enabled ? "enabled" : "disabled"}</Badge>
              {Object.entries(p.secrets).map(([k, v]) => <Badge key={k} appearance="outline" color="informative">{k}: {v}</Badge>)}
            </div>
            <Code>{JSON.stringify(p.config, null, 2)}</Code>
          </div>
        ))}
      </Card>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Installed plugins</Text>} description="Discovered through Python entry points. Add a tool by installing a plugin package." />
        <Accordion collapsible multiple>
          {data.plugins.map((p) => (
            <AccordionItem key={p.name} value={p.name}>
              <AccordionHeader>
                <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <Text weight="semibold">{p.display_name}</Text> v{p.version}
                  {p.capabilities.map((c) => <Badge key={c} appearance="outline" size="small">{c}</Badge>)}
                </span>
              </AccordionHeader>
              <AccordionPanel><Text size={200}>Config schema (drives the onboarding form):</Text><Code>{JSON.stringify(p.config_schema, null, 2)}</Code></AccordionPanel>
            </AccordionItem>
          ))}
        </Accordion>
      </Card>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16, marginTop: 16 }}>
        <Card><CardHeader header={<Text weight="semibold" size={400}>Policy defaults</Text>} description="config/pipelines.yaml" /><KeyValues data={data.defaults} />
          <Text weight="semibold">Per-pipeline overrides</Text><Code>{JSON.stringify(data.policies, null, 2)}</Code>
          <Text weight="semibold">Circuit breaker</Text><KeyValues data={data.circuit_breaker} /></Card>
        <Card><CardHeader header={<Text weight="semibold" size={400}>LLM & budgets</Text>} description="config/llm.yaml · config/budgets.yaml" />
          <KeyValues data={data.budgets} /><KeyValues data={data.llm} /></Card>
      </div>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Fix flow</Text>} description="config/fix.yaml" />
        <KeyValues data={data.fix} />
      </Card>
      <Card style={{ marginTop: 16 }}>
        <CardHeader header={<Text weight="semibold" size={400}>Control history</Text>} />
        <Table size="small"><TableBody>
          {data.controls.map((c) => (
            <TableRow key={c.key}><TableCell><Text weight="semibold" size={200}>{c.key}</Text></TableCell><TableCell>{String(c.value)}</TableCell>
              <TableCell>{c.updated_by}</TableCell><TableCell>{dateTime(c.updated_at)}</TableCell></TableRow>
          ))}
        </TableBody></Table>
      </Card>
    </>
  );
}
