import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";

// Only what the console uses is bundled (tree-shaken ECharts).
echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, AriaComponent, SVGRenderer]);

/** Minimal ECharts host: init once, update on option change, resize with its container. */
export function EChart({ option, height = 260, label }: { option: EChartsOption; height?: number; label: string }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    chart.current = echarts.init(el.current, undefined, { renderer: "svg" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  // Height includes the axis band, so labels never get clipped into a nested scroll.
  return <div ref={el} role="img" aria-label={label} style={{ width: "100%", height }} />;
}
