export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "-";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  const h = Math.floor(s / 3600);
  return `${h}h ${Math.floor((s % 3600) / 60)}m`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "-";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 0) return "just now";
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const num = (n: number | null | undefined, digits = 0) =>
  n === null || n === undefined ? "-" : n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const usd = (n: number | null | undefined) => (n === null || n === undefined ? "-" : `$${n.toFixed(n < 1 ? 4 : 2)}`);

export function toCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => {
    const s = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

export function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}
