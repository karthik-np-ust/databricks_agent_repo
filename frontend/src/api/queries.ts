import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, post, put } from "./client";
import type {
  ActiveRun, AuditEntry, ComputeCosts, Fix, FixDiff, IncidentDetail, IncidentSummary, LlmCall, LlmCosts, Overview,
  PipelineDetail, PipelineRow, RunRow, SettingsView,
} from "./types";

// Live data refreshes every 15s; previous data stays on screen while refetching (no skeleton flash).
const live = { refetchInterval: 15_000, placeholderData: keepPreviousData } as const;
const slow = { refetchInterval: 60_000, placeholderData: keepPreviousData } as const;

export const useOverview = () => useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/overview"), ...live });
export const usePipelines = () => useQuery({ queryKey: ["pipelines"], queryFn: () => api<PipelineRow[]>("/pipelines"), ...live });
export const usePipeline = (platform: string, id: string) =>
  useQuery({ queryKey: ["pipeline", platform, id], queryFn: () => api<PipelineDetail>(`/pipelines/${platform}/${id}`), ...live });
export const useRuns = (hours: number) => useQuery({ queryKey: ["runs", hours], queryFn: () => api<RunRow[]>(`/runs?hours=${hours}`), ...live });
export const useActiveRuns = () =>
  useQuery({ queryKey: ["runs-active"], queryFn: () => api<ActiveRun[]>("/runs/active"), refetchInterval: 10_000, placeholderData: keepPreviousData });
export const useIncidents = () => useQuery({ queryKey: ["incidents"], queryFn: () => api<IncidentSummary[]>("/incidents"), ...live });
export const useIncident = (id: number) => useQuery({ queryKey: ["incident", id], queryFn: () => api<IncidentDetail>(`/incidents/${id}`), ...live });
export const useFixes = () => useQuery({ queryKey: ["fixes"], queryFn: () => api<Fix[]>("/fixes"), ...live });
export const useFixDiff = (id: number | undefined) =>
  useQuery({ queryKey: ["fix-diff", id], queryFn: () => api<FixDiff>(`/fixes/${id}/diff`), enabled: id !== undefined, ...slow });
export const useApprovals = () => useQuery({ queryKey: ["approvals"], queryFn: () => api<Fix[]>("/approvals"), ...live });
export const useComputeCosts = (days: number) =>
  useQuery({ queryKey: ["costs-compute", days], queryFn: () => api<ComputeCosts>(`/costs/compute?days=${days}`), ...slow });
export const useLlmCosts = (days: number) => useQuery({ queryKey: ["costs-llm", days], queryFn: () => api<LlmCosts>(`/costs/llm?days=${days}`), ...live });
export const useLlmCalls = () => useQuery({ queryKey: ["llm-calls"], queryFn: () => api<LlmCall[]>("/llm/calls?limit=200"), ...live });
export const useAudit = (f: { actor?: string; action?: string; q?: string }) => {
  const params = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]);
  params.set("limit", "500");
  return useQuery({ queryKey: ["audit", f], queryFn: () => api<AuditEntry[]>(`/audit?${params}`), ...live });
};
export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => api<SettingsView>("/settings"), ...slow });

/** Mutations invalidate everything: the dashboard should reflect an action immediately. */
function useAction<TBody>(fn: (body: TBody) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries() });
}

export const useCancelRun = () =>
  useAction((b: { platform_id: string; pipeline_id: string; run_id: string; reason: string }) => post("/runs/cancel", b));
export const useRerun = () => useAction((b: { platform_id: string; pipeline_id: string; reason: string }) => post("/pipelines/rerun", b));
export const useRetriage = () => useAction((b: { id: number }) => post(`/incidents/${b.id}/triage`, {}));
export const useStartFix = () => useAction((b: { id: number; reason: string }) => post(`/incidents/${b.id}/fix`, { reason: b.reason }));
export const useTakeover = () => useAction((b: { id: number; reason: string }) => post(`/incidents/${b.id}/takeover`, { reason: b.reason }));
export const useSetControls = () => useAction((b: { enabled?: boolean; dry_run?: boolean; reason: string }) => put("/controls", b));
