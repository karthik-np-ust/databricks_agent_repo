import {
  Avatar, Badge, Button, CounterBadge, Menu, MenuItem, MenuList, MenuPopover, MenuTrigger, Tab, TabList, Text,
  Tooltip, makeStyles, tokens,
} from "@fluentui/react-components";
import {
  Board24Regular, DataBarVertical24Regular, DataFunnel24Regular, Alert24Regular, Wrench24Regular, CheckmarkStarburst24Regular,
  Money24Regular, BrainCircuit24Regular, History24Regular, Settings24Regular, WeatherMoon20Regular, WeatherSunny20Regular,
  SignOut20Regular, Play24Regular,
} from "@fluentui/react-icons";
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useApprovals, useOverview } from "../api/queries";
import { useAuth } from "../auth/auth";
import { ago } from "./format";

const useStyles = makeStyles({
  root: { display: "grid", gridTemplateColumns: "232px 1fr", gridTemplateRows: "56px 1fr", height: "100vh" },
  top: {
    gridColumn: "1 / 3", display: "flex", alignItems: "center", gap: "16px", padding: "0 20px",
    background: "#006E74", color: "#ffffff",
  },
  brand: { display: "flex", alignItems: "center", gap: "10px", fontWeight: 600, fontSize: "16px" },
  spacer: { flex: 1 },
  nav: {
    borderRight: `1px solid ${tokens.colorNeutralStroke2}`, padding: "12px 8px", background: tokens.colorNeutralBackground1,
    overflowY: "auto",
  },
  main: { overflow: "auto", padding: "24px 32px 48px", background: tokens.colorNeutralBackground2 },
  status: { display: "flex", alignItems: "center", gap: "8px" },
  topButton: { color: "#ffffff" },
});

const NAV = [
  { path: "/", label: "Command Center", icon: <Board24Regular /> },
  { path: "/pipelines", label: "Pipelines", icon: <DataFunnel24Regular /> },
  { path: "/runs", label: "Live runs", icon: <Play24Regular /> },
  { path: "/incidents", label: "Incidents", icon: <Alert24Regular /> },
  { path: "/fixes", label: "Agent fixes", icon: <Wrench24Regular /> },
  { path: "/approvals", label: "Approvals", icon: <CheckmarkStarburst24Regular /> },
  { path: "/costs", label: "Cost & FinOps", icon: <Money24Regular /> },
  { path: "/observability", label: "Agent observability", icon: <BrainCircuit24Regular /> },
  { path: "/audit", label: "Audit log", icon: <History24Regular /> },
  { path: "/settings", label: "Settings & controls", icon: <Settings24Regular /> },
];

function AgentStatus() {
  const { data } = useOverview();
  if (!data) return null;
  const a = data.agent;
  const stale = a.last_tick ? (Date.now() - new Date(a.last_tick).getTime()) / 1000 > a.poll_interval_s * 3 : true;
  const state = !a.enabled ? { color: "danger" as const, label: "Agent stopped (kill switch)" }
    : stale ? { color: "warning" as const, label: "Agent not polling" }
    : a.dry_run ? { color: "informative" as const, label: "Dry run" }
    : { color: "success" as const, label: "Agent active" };
  return (
    <Tooltip relationship="description" content={`Last poll ${ago(a.last_tick)} · LLM ${a.llm_enabled ? "on" : "off"} · fixes ${a.fix_enabled ? "on" : "off"}`}>
      <Badge appearance="filled" color={state.color} size="large">{state.label}</Badge>
    </Tooltip>
  );
}

export function AppShell({ children, dark, toggleDark }: { children: ReactNode; dark: boolean; toggleDark: () => void }) {
  const s = useStyles();
  const nav = useNavigate();
  const loc = useLocation();
  const { user, signOut } = useAuth();
  const approvals = useApprovals();
  const current = NAV.slice().reverse().find((n) => (n.path === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.path)))?.path ?? "/";
  return (
    <div className={s.root}>
      <header className={s.top}>
        <div className={s.brand}>
          <DataBarVertical24Regular />
          <span>Pipeline Ops Console</span>
        </div>
        <div className={s.spacer} />
        <div className={s.status}><AgentStatus /></div>
        <Tooltip content={dark ? "Light theme" : "Dark theme"} relationship="label">
          <Button appearance="transparent" className={s.topButton} icon={dark ? <WeatherSunny20Regular /> : <WeatherMoon20Regular />} onClick={toggleDark} />
        </Tooltip>
        <Menu>
          <MenuTrigger disableButtonEnhancement>
            <Button appearance="transparent" className={s.topButton} icon={<Avatar name={user?.name} size={24} color="neutral" />}>
              <Text style={{ color: "#fff" }}>{user?.name} · {user?.role}</Text>
            </Button>
          </MenuTrigger>
          <MenuPopover>
            <MenuList>
              <MenuItem icon={<SignOut20Regular />} onClick={signOut}>Sign out</MenuItem>
            </MenuList>
          </MenuPopover>
        </Menu>
      </header>
      <nav className={s.nav} aria-label="Main navigation">
        <TabList vertical selectedValue={current} onTabSelect={(_, d) => nav(String(d.value))} size="large">
          {NAV.map((n) => (
            <Tab key={n.path} value={n.path} icon={n.icon}>
              <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
                {n.label}
                {n.path === "/approvals" && (approvals.data?.length ?? 0) > 0 && (
                  <CounterBadge count={approvals.data!.length} color="danger" size="small" />
                )}
              </span>
            </Tab>
          ))}
        </TabList>
      </nav>
      <main className={s.main}>{children}</main>
    </div>
  );
}
