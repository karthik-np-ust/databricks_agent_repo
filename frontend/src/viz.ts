// Chart tokens. Validated with the dataviz skill's validate_palette.js (2026-10-26):
//   light  #0097ac, #eb6834, #6250d6 on #ffffff  -> ALL CHECKS PASS (--pairs all)
//   dark   #0097ac, #d95926, #9085e9 on #292929  -> ALL CHECKS PASS (--pairs all)
// Slot 1 is UST Light Teal. UST Dark Teal #006E74 fails the chroma floor at chart
// scale (reads gray), so it stays on the app chrome only.
// Color follows the entity: prod is always slot 1, qa slot 2, dev slot 3.

export type Mode = "light" | "dark";

export const SERIES: Record<Mode, string[]> = {
  light: ["#0097ac", "#eb6834", "#6250d6"],
  dark: ["#0097ac", "#d95926", "#9085e9"],
};

export const ENV_SLOT: Record<string, number> = { prod: 0, qa: 1, dev: 2 };

export function envColor(env: string, mode: Mode): string {
  return SERIES[mode][ENV_SLOT[env] ?? 0];
}

// Status palette: reserved for state, always shown with an icon + label.
export const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };

export const CHROME: Record<Mode, { surface: string; text: string; text2: string; muted: string; grid: string; axis: string }> = {
  light: { surface: "#ffffff", text: "#231F20", text2: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7" },
  dark: { surface: "#292929", text: "#f0efec", text2: "#c3c2b7", muted: "#898781", grid: "#3a3a38", axis: "#4a4a47" },
};

/** Shared ECharts base: recessive solid hairline grid, thin marks, hover tooltip by default. */
export function baseOption(mode: Mode) {
  const c = CHROME[mode];
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: '"Aptos","Segoe UI",system-ui,sans-serif', color: c.text2 },
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: c.axis, width: 1 } },
      backgroundColor: mode === "dark" ? "#1f1f1f" : "#ffffff",
      borderColor: c.grid,
      textStyle: { color: c.text, fontSize: 12 },
      confine: true,
    },
    legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 8, textStyle: { color: c.text2 } },
    xAxis: {
      axisLine: { lineStyle: { color: c.axis } },
      axisTick: { show: false },
      axisLabel: { color: c.muted },
      splitLine: { show: false },
    },
    yAxis: {
      axisLine: { show: false },
      axisLabel: { color: c.muted },
      splitLine: { lineStyle: { color: c.grid, type: "solid", width: 1 } },
    },
    aria: { enabled: true },
  };
}

export const BAR_RADIUS = [4, 4, 0, 0];
