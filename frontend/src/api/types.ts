// Response shapes of the console API (/api/v1). Kept in sync with src/pipeline_agent/api/views.py.

export type Role = "viewer" | "operator" | "approver" | "admin";
export type Env = "prod" | "qa" | "dev";

export interface Me { name: string; role: Role }

export interface AuditEntry { id?: number; ts: string; actor: string; action: string; target: string; details: Record<string, unknown> }

export interface PlatformStatus { id: string; plugin: string; display_name: string; capabilities: string[]; last_poll: string | null }

export interface IncidentSummary {
  id: number; status: string; error_class: string; matched_rule: string | null; platform_id: string;
  pipeline_id: string; pipeline_name: string; env: Env; run_key: string; url: string | null;
  created_at: string; updated_at: string; fix_eligible: boolean; suspicious_content: boolean;
  llm_tokens: number; llm_cost_usd: number; summary: string | null; confidence: number | null;
  fix_state: string | null; headline: string;
}

export interface FixHistoryItem { ts: string; state: string; note: string }

export interface Fix {
  id: number; incident_id: number; state: string; stage: number; target: string; promotion: string[];
  branch: string | null; commit_sha: string | null; pr_number: number | null; prs: Record<string, string>;
  proposal: { summary?: string; files?: string[]; risk?: string; test_plan?: string; model?: string; repairs?: number; last_repair?: string } | null;
  error: string | null; history: FixHistoryItem[]; created_at: string; updated_at: string;
  awaiting_human: boolean; repairs: number; pipeline_name?: string | null;
}

export interface Overview {
  version: string;
  agent: { enabled: boolean; dry_run: boolean; llm_enabled: boolean; fix_enabled: boolean; last_tick: string | null; poll_interval_s: number; platforms: PlatformStatus[] };
  kpis: {
    pipelines: number; pipelines_by_env: Record<Env, number>; runs_24h: number; runs_24h_failed: number; runs_24h_succeeded: number;
    running_now: number; incidents_open: number; incidents_escalated: number; incidents_resolved: number; incidents_total: number;
    auto_resolved_pct: number | null; mttr_s: number | null; fixes_active: number; awaiting_human: number; approvals_pending: number;
    llm_calls_today: number; llm_tokens_today: number; llm_cost_today: number; llm_requests_budget: number; llm_cost_budget: number;
  };
  attention: Fix[];
  recent_incidents: IncidentSummary[];
  activity: AuditEntry[];
}

export interface PipelineRow {
  platform_id: string; plugin: string; pipeline_id: string; name: string; env: Env; url: string | null; tags: Record<string, string>;
  last_status: string | null; last_run_at: string | null; runs_14d: number; success_rate: number | null; avg_duration_s: number | null;
  threshold_s: number | null; auto_fix: boolean; acts_in_env: boolean; open_incidents: number;
}

export interface RunRow {
  key: string; platform_id: string; pipeline_id: string; pipeline_name: string; run_id: string; env: Env; status: string;
  start_time: string | null; end_time: string | null; duration_s: number | null; triggered_by: string | null; url: string | null;
}

export interface PipelineDetail { pipeline: PipelineRow; policy: Record<string, unknown>; runs: RunRow[]; incidents: IncidentSummary[] }

export interface ActiveRun {
  platform_id: string; pipeline_id: string; pipeline_name: string; run_id: string; env: Env; status: string; start_time: string | null;
  elapsed_s: number | null; threshold_s: number | null; pct_of_threshold: number | null; url: string | null;
  parameters: Record<string, string>; can_cancel: boolean;
}

export interface Diagnosis {
  summary: string; root_cause: string; error_class: string; confidence: number; failing_component?: string | null;
  fix_type: string; suggested_fix: string; evidence: string[]; suspicious_content: boolean; model: string; prompt_version: string;
}

export interface LlmCall {
  id?: number; ts: string; incident_id?: number | null; purpose: string; model: string; prompt_tokens: number; completion_tokens: number;
  cost_usd: number; latency_ms: number; redactions?: number; success: boolean; error: string | null;
}

export interface IncidentDetail extends IncidentAgentView { incident: IncidentSummary; error_text: string; diagnosis: Diagnosis | null; fix: Fix | null; audit: AuditEntry[]; llm_calls: LlmCall[] }

export interface FixDiff {
  available: boolean; reason?: string; pr_number?: number; pr_url?: string;
  files: { filename: string; status: string; additions: number; deletions: number; patch: string }[];
  comments: { author: string; created_at: string; body: string }[];
}

export interface ComputeCosts {
  unit: string; is_estimate: boolean; total: number; errors: string[];
  by_day: ({ date: string } & Partial<Record<Env, number>>)[];
  by_pipeline: { pipeline: string; quantity: number }[];
}

export interface LlmCosts {
  budgets: { per_incident_usd: number; per_day_usd: number; per_incident_tokens: number; per_day_requests: number };
  today: { calls: number; tokens: number; cost_usd: number };
  by_day: { date: string; calls: number; tokens: number; cost_usd: number; failed: number }[];
  by_model: { model: string; calls: number; tokens: number; cost_usd: number; failed: number; avg_latency_ms: number | null }[];
  by_purpose: { purpose: string; tokens: number }[];
  per_incident: { incident_id: number; pipeline: string; tokens: number; cost_usd: number }[];
}

export interface SettingsView {
  controls: { key: string; value: unknown; updated_at: string; updated_by: string }[];
  env_settings: Record<string, unknown>;
  platforms: { id: string; plugin: string; enabled: boolean; config: Record<string, unknown>; secrets: Record<string, string> }[];
  plugins: { name: string; version: string; display_name: string; capabilities: string[]; config_schema: Record<string, unknown> }[];
  defaults: Record<string, unknown>; policies: Record<string, unknown>[]; circuit_breaker: Record<string, unknown>;
  budgets: Record<string, unknown>; llm: Record<string, unknown>; fix: Record<string, unknown>; github_identity: string | null;
}

// --- redesign: control plane, trust boundary, KEDB (src/pipeline_agent/api/control.py) ---

export interface ActionApproval {
  id: number; created_at: string; incident_id: number | null; capability: string; summary: string; steps: string[];
  reasons: string[]; level: number | null; status: "pending" | "approved" | "rejected"; decided_by: string | null;
  decided_at: string | null; comment: string | null;
}

export interface SignedRequest {
  id: string; created_at: string; incident_id: number | null; agent: string; capability: string; platform_id: string; env: string;
  level: number; status: string; key_id: string; idempotency_key: string; target: Record<string, unknown>;
  params: Record<string, unknown>; approval: { kind: string; ref: string; by?: string | null } | null; reason: string | null;
  policy_bundle: string;
  result: { status: string; error: string | null; output: Record<string, unknown>; evidence_ids: string[]; ts: string } | null;
  ledger: { state: "RESERVED" | "SUCCESS" | "FAILED" | "UNKNOWN"; reserved_at: string; finished_at: string | null } | null;
}

export interface LedgerSummary { total: number; RESERVED: number; SUCCESS: number; FAILED: number; UNKNOWN: number }

export interface KnownError {
  id: number; name: string; signature: string; platform: string; error_class: string; runbook: string; diagnostics: string;
  fix_hint: string; risk: string; rollback: string; provenance: string; status: "proposed" | "approved" | "retired";
}

export interface AgentCard {
  name: string; description: string; version: string; skills: { id: string; description: string }[];
  "x-pipeline-agent": { kind: string; model_alias: string | null; requests: string[]; tools: string[]; risk_class: string };
}

export interface PolicyView {
  sha256: string; version: number; defaults: Record<string, unknown>; limits: Record<string, number>;
  rules: { name: string; level: number; agent: unknown; capability: unknown; env: unknown; pattern: unknown; rule: unknown; approval_from: string[] }[];
}

export interface EvidenceDoc {
  id: string; kind: string; source: string; media_type: string; size: number; redactions: number; created_at: string; intact: boolean; text: string;
}

export interface IncidentAgentView {
  evidence: { id: string; kind: string; source: string; size: number; redactions: number; created_at: string }[];
  requests: SignedRequest[];
  tests: SignedRequest[];
  work_items: { id: number; kind: string; status: string; waiting_for: string | null; outcome: string | null; reason: string | null }[];
  approvals: ActionApproval[];
}
