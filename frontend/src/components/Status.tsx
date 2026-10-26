import { Badge, Tooltip } from "@fluentui/react-components";
import {
  CheckmarkCircle16Filled, DismissCircle16Filled, ErrorCircle16Filled, Pause16Filled, ArrowSync16Regular,
  Warning16Filled, Info16Regular, Wrench16Regular, ShieldError16Filled, Clock16Regular,
} from "@fluentui/react-icons";
import type { ReactElement } from "react";

// Status is never color alone: every badge has an icon and a text label.
type Tone = "success" | "danger" | "warning" | "informative" | "brand" | "subtle" | "important";

const RUN: Record<string, { tone: Tone; icon: ReactElement; label: string }> = {
  succeeded: { tone: "success", icon: <CheckmarkCircle16Filled />, label: "Succeeded" },
  failed: { tone: "danger", icon: <DismissCircle16Filled />, label: "Failed" },
  running: { tone: "brand", icon: <ArrowSync16Regular />, label: "Running" },
  queued: { tone: "informative", icon: <Clock16Regular />, label: "Queued" },
  cancelled: { tone: "subtle", icon: <Pause16Filled />, label: "Cancelled" },
  skipped: { tone: "subtle", icon: <Info16Regular />, label: "Skipped" },
};

const INCIDENT: Record<string, { tone: Tone; icon: ReactElement; label: string }> = {
  open: { tone: "warning", icon: <Warning16Filled />, label: "Open" },
  fixing: { tone: "brand", icon: <Wrench16Regular />, label: "Agent fixing" },
  escalated: { tone: "danger", icon: <ErrorCircle16Filled />, label: "Escalated" },
  resolved: { tone: "success", icon: <CheckmarkCircle16Filled />, label: "Resolved" },
};

const FIX: Record<string, { tone: Tone; label: string }> = {
  planned: { tone: "informative", label: "Writing fix" },
  pr_open: { tone: "brand", label: "PR open" },
  deploying: { tone: "brand", label: "Deploying" },
  verifying: { tone: "brand", label: "Verifying in prod" },
  resolved: { tone: "success", label: "Resolved" },
  failed: { tone: "danger", label: "Failed" },
  rejected: { tone: "subtle", label: "Rejected" },
};

export function RunStatus({ status }: { status: string | null }) {
  if (!status) return <Badge appearance="outline" color="subtle">No runs</Badge>;
  const s = RUN[status] ?? { tone: "subtle" as Tone, icon: <Info16Regular />, label: status };
  return <Badge appearance="tint" color={s.tone} icon={s.icon}>{s.label}</Badge>;
}

export function IncidentStatus({ status }: { status: string }) {
  const s = INCIDENT[status] ?? { tone: "subtle" as Tone, icon: <Info16Regular />, label: status };
  return <Badge appearance="tint" color={s.tone} icon={s.icon}>{s.label}</Badge>;
}

export function FixStatus({ state, target, awaiting }: { state: string | null; target?: string; awaiting?: boolean }) {
  if (!state) return <span>-</span>;
  if (awaiting) return <Badge appearance="filled" color="warning" icon={<Warning16Filled />}>Needs you · {target}</Badge>;
  const s = FIX[state] ?? { tone: "subtle" as Tone, label: state };
  const where = target && (state === "pr_open" || state === "deploying") ? ` → ${target}` : "";
  return <Badge appearance="tint" color={s.tone}>{s.label}{where}</Badge>;
}

const CLASS_TONE: Record<string, Tone> = { code: "important", infra: "danger", transient: "informative", unknown: "subtle" };

export function ErrorClass({ value, rule }: { value: string; rule?: string | null }) {
  return (
    <Tooltip content={rule ? `matched rule: ${rule}` : "no rule matched"} relationship="description">
      <Badge appearance="outline" color={CLASS_TONE[value] ?? "subtle"}>{value}</Badge>
    </Tooltip>
  );
}

export function EnvBadge({ env }: { env: string }) {
  const tone: Tone = env === "prod" ? "brand" : env === "qa" ? "warning" : "informative";
  return <Badge appearance={env === "prod" ? "filled" : "tint"} color={tone} shape="rounded">{env.toUpperCase()}</Badge>;
}

export function SuspiciousBadge() {
  return (
    <Tooltip content="Failure output contained text that looks like a prompt-injection attempt. Review with extra care." relationship="description">
      <Badge appearance="filled" color="danger" icon={<ShieldError16Filled />}>Suspicious content</Badge>
    </Tooltip>
  );
}
