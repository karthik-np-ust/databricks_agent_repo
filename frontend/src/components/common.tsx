import {
  Button, Card, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, MessageBar,
  MessageBarBody, MessageBarTitle, Spinner, Text, Textarea, makeStyles, tokens,
} from "@fluentui/react-components";
import { useState, type ReactNode } from "react";
import { ApiError } from "../api/client";

const useStyles = makeStyles({
  header: { display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "16px", marginBottom: "20px", flexWrap: "wrap" },
  eyebrow: { color: "#0097AC", letterSpacing: "0.08em", fontWeight: 700, fontSize: "11px", textTransform: "uppercase" },
  kpi: { padding: "16px", display: "flex", flexDirection: "column", gap: "4px", minWidth: 0 },
  kpiValue: { fontSize: "28px", lineHeight: "36px", fontWeight: 600, color: tokens.colorNeutralForeground1 },
  grid: { display: "grid", gap: "16px" },
  code: {
    fontFamily: "Consolas, 'Cascadia Mono', monospace", fontSize: "12px", whiteSpace: "pre-wrap", wordBreak: "break-word",
    background: tokens.colorNeutralBackground3, padding: "12px", borderRadius: tokens.borderRadiusMedium, margin: 0,
    maxHeight: "360px", overflow: "auto",
  },
});

export function PageHeader({ eyebrow, title, subtitle, actions }: { eyebrow: string; title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  const s = useStyles();
  return (
    <div className={s.header}>
      <div>
        <div className={s.eyebrow}>{eyebrow}</div>
        <Text as="h1" size={800} weight="regular" style={{ display: "block", margin: "4px 0" }}>{title}</Text>
        {subtitle && <Text size={300} style={{ color: tokens.colorNeutralForeground3 }}>{subtitle}</Text>}
      </div>
      {actions && <div style={{ display: "flex", gap: 8 }}>{actions}</div>}
    </div>
  );
}

export function Kpi({ label, value, hint, accent }: { label: string; value: ReactNode; hint?: ReactNode; accent?: ReactNode }) {
  const s = useStyles();
  return (
    <Card className={s.kpi}>
      <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>{label}</Text>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span className={s.kpiValue}>{value}</span>
        {accent}
      </div>
      {hint && <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>{hint}</Text>}
    </Card>
  );
}

export function Grid({ min = 220, children }: { min?: number; children: ReactNode }) {
  const s = useStyles();
  return <div className={s.grid} style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${min}px, 1fr))` }}>{children}</div>;
}

export function Code({ children }: { children: ReactNode }) {
  return <pre className={useStyles().code}>{children}</pre>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <div style={{ padding: 48, display: "grid", placeItems: "center" }}><Spinner label={label} /></div>;
}

export function ErrorState({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? `${error.status}: ${error.message}` : String(error);
  return (
    <MessageBar intent="error">
      <MessageBarBody><MessageBarTitle>Could not load data</MessageBarTitle>{msg}</MessageBarBody>
    </MessageBar>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div style={{ padding: 32, textAlign: "center", color: tokens.colorNeutralForeground3 }}>
      <Text weight="semibold" block>{title}</Text>
      {children && <Text size={200}>{children}</Text>}
    </div>
  );
}

/** Confirm dialog that requires a reason: every human action is audited with it. */
export function ReasonDialog({ trigger, title, description, confirmLabel, onConfirm, danger }: {
  trigger: (open: () => void) => ReactNode; title: string; description: ReactNode; confirmLabel: string;
  onConfirm: (reason: string) => Promise<unknown>; danger?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      setOpen(false);
      setReason("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {trigger(() => setOpen(true))}
      <Dialog open={open} onOpenChange={(_, d) => setOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>{title}</DialogTitle>
            <DialogContent style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Text>{description}</Text>
              <Field label="Reason (recorded in the audit log)" required validationMessage={error ?? undefined}>
                <Textarea value={reason} onChange={(_, d) => setReason(d.value)} resize="vertical" />
              </Field>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => setOpen(false)} disabled={busy}>Cancel</Button>
              <Button
                appearance="primary"
                onClick={submit}
                disabled={busy || reason.trim().length < 3}
                style={danger ? { background: tokens.colorPaletteRedBackground3 } : undefined}
              >
                {busy ? "Working…" : confirmLabel}
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </>
  );
}
