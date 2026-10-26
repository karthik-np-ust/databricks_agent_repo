import { Button, Card, Dropdown, Field, Input, MessageBar, MessageBarBody, Option, Text, tokens } from "@fluentui/react-components";
import { DataBarVertical24Regular } from "@fluentui/react-icons";
import { useState } from "react";
import { post } from "../api/client";
import type { Role } from "../api/types";
import { useAuth } from "../auth/auth";

const ROLES: { value: Role; text: string }[] = [
  { value: "viewer", text: "Viewer: read everything" },
  { value: "operator", text: "Operator: cancel / rerun, re-diagnose, take over" },
  { value: "approver", text: "Approver: start agent fixes" },
  { value: "admin", text: "Admin: kill switch, dry run" },
];

export function Login() {
  const { signIn } = useAuth();
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("operator");
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setError(null);
    try {
      const r = await post<{ token: string; name: string; role: Role }>("/auth/demo-login", { name, role });
      signIn(r.token, { name: r.name, role: r.role });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "linear-gradient(135deg, #006E74 0%, #003C51 100%)" }}>
      <Card style={{ width: 420, padding: 28, gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, color: "#006E74" }}>
          <DataBarVertical24Regular />
          <Text size={600} weight="semibold">Pipeline Ops Console</Text>
        </div>
        <Text style={{ color: tokens.colorNeutralForeground3 }}>
          Production support agent for Databricks and other data platforms.
        </Text>
        <form onSubmit={(e) => { e.preventDefault(); void submit(); }} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <Field label="Name" required>
            <Input value={name} onChange={(_, d) => setName(d.value)} placeholder="e.g. Karthik" autoFocus />
          </Field>
          <Field label="Role" hint="The API enforces the role on every action.">
            <Dropdown value={ROLES.find((r) => r.value === role)?.text} selectedOptions={[role]} onOptionSelect={(_, d) => setRole(d.optionValue as Role)}>
              {ROLES.map((r) => <Option key={r.value} value={r.value}>{r.text}</Option>)}
            </Dropdown>
          </Field>
          {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
          <Button appearance="primary" type="submit" disabled={name.trim().length < 2}>Sign in</Button>
        </form>
        <MessageBar intent="warning">
          <MessageBarBody>
            Demo sign-in: anyone can pick any role. The pilot uses Entra ID single sign-on with roles from Entra groups.
          </MessageBarBody>
        </MessageBar>
      </Card>
    </div>
  );
}
