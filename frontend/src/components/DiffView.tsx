import { Badge, Text, makeStyles, tokens } from "@fluentui/react-components";
import type { FixDiff } from "../api/types";

const useStyles = makeStyles({
  file: { border: `1px solid ${tokens.colorNeutralStroke2}`, borderRadius: tokens.borderRadiusMedium, marginBottom: "12px", overflow: "hidden" },
  head: { display: "flex", gap: "8px", alignItems: "center", padding: "6px 10px", background: tokens.colorNeutralBackground3 },
  pre: { margin: 0, fontFamily: "Consolas, 'Cascadia Mono', monospace", fontSize: "12px", lineHeight: "18px", overflowX: "auto", maxHeight: "420px" },
  add: { background: tokens.colorPaletteGreenBackground1, display: "block", padding: "0 10px" },
  del: { background: tokens.colorPaletteRedBackground1, display: "block", padding: "0 10px" },
  hunk: { color: tokens.colorNeutralForeground3, background: tokens.colorNeutralBackground2, display: "block", padding: "0 10px" },
  ctx: { display: "block", padding: "0 10px" },
});

/** Unified diff of the agent's PR, as GitHub reports it (read-only). */
export function DiffView({ diff }: { diff: FixDiff }) {
  const s = useStyles();
  return (
    <div>
      {diff.files.map((f) => (
        <div key={f.filename} className={s.file}>
          <div className={s.head}>
            <Text weight="semibold" size={200} style={{ fontFamily: "Consolas, monospace" }}>{f.filename}</Text>
            <Badge size="small" appearance="tint" color={f.status === "added" ? "success" : "informative"}>{f.status}</Badge>
            <Text size={200} style={{ color: tokens.colorPaletteGreenForeground1 }}>+{f.additions}</Text>
            <Text size={200} style={{ color: tokens.colorPaletteRedForeground1 }}>-{f.deletions}</Text>
          </div>
          <pre className={s.pre} aria-label={`Diff of ${f.filename}`}>
            {(f.patch || "(binary or too large to show)").split("\n").map((line, i) => (
              <span key={i} className={line.startsWith("@@") ? s.hunk : line.startsWith("+") ? s.add : line.startsWith("-") ? s.del : s.ctx}>
                {line || " "}
              </span>
            ))}
          </pre>
        </div>
      ))}
    </div>
  );
}
