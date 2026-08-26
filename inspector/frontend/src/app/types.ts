// API DTOs — one interface per `api.*` response, mirroring inspector/backend
// payloads.py. Fields the backend may omit (RP deep links, optional blocks) are
// optional here; views must render an honest fallback rather than assume them.

// --------------------------------------------------------------------------- //
// ReportPortal name block (attached to every label-bearing payload)
// --------------------------------------------------------------------------- //

export interface DefectInfo {
  name?: string | null;
  short_name?: string | null;
  color?: string | null;
}

export type DefectMap = Record<string, DefectInfo>;

export interface RpStatus {
  configured: boolean;
  reachable: boolean;
  note: string;
}

export interface RpBlock {
  project_id: number;
  project_name?: string | null;
  status: RpStatus;
  defects: DefectMap;
}

export type RpResponse = RpBlock;

// --------------------------------------------------------------------------- //
// Projects / launches / items
// --------------------------------------------------------------------------- //

export interface Project {
  project_id: number;
  project_name?: string | null;
  item_count: number;
  launch_count: number;
}

export interface ProjectsResponse {
  projects: Project[];
  rp_status?: RpStatus;
}

export interface Launch {
  launch_id: number;
  launch_name?: string | null;
  launch_number?: number | null;
  item_count: number;
  labeled_count: number;
  last_start?: string | null;
  ui_url?: string | null;
}

export interface LaunchesResponse {
  launches: Launch[];
}

export interface Item {
  item_id: number;
  item_name?: string | null;
  issue_type?: string | null;
  label_group?: string | null;
  is_auto_analyzed?: boolean | null;
  log_count?: number | null;
  exc_text?: string | null;
  has_emb?: boolean | null;
  ui_url?: string | null;
}

export interface ItemsResponse {
  items: Item[];
}

export interface SummaryResponse {
  project_id: number;
  rp: RpBlock;
  suggestions: number;
  accepted: number;
  corrected: number;
  ignored: number;
  abstains: number;
  pending: number;
  llm_used: number;
  items: number;
  signatures: number;
  embedded: number;
  templates: number;
  groups: number;
  modes: number;
  label_events: number;
}

// --------------------------------------------------------------------------- //
// Drain3 templates
// --------------------------------------------------------------------------- //

export interface Template {
  template_id: number;
  pattern?: string | null;
  token_count?: number | null;
  match_count?: number | null;
  last_seen?: string | null;
  missing?: boolean;
}

export interface TemplatesResponse {
  templates: Template[];
  count: number;
}

// --------------------------------------------------------------------------- //
// Item journey
// --------------------------------------------------------------------------- //

export interface JourneyItem {
  item_id: number;
  item_name?: string | null;
  issue_type?: string | null;
  label_group?: string | null;
  is_auto_analyzed?: boolean | null;
  ui_url?: string | null;
  launch_id?: number | null;
  launch_name?: string | null;
  launch_number?: number | null;
  launch_url?: string | null;
  log_count?: number | null;
  test_case_hash?: string | number | null;
  unique_id?: string | null;
  start_time?: string | null;
}

export interface JourneySignature {
  exc_text?: string | null;
  msg_text?: string | null;
  top_frames?: string[] | null;
  template_ids?: number[] | null;
  status_codes?: string[] | null;
  exception_fp?: string | null;
  error_hash?: string | null;
  emb_model_ver?: string | null;
  has_emb?: boolean | null;
}

export interface JourneyGroupMember {
  item_id: number;
  label_group?: string | null;
  issue_type?: string | null;
  is_self?: boolean;
}

export interface JourneyGrouping {
  group_id?: number | null;
  fingerprint?: string | null;
  member_count?: number | null;
  dominant?: boolean | null;
  si_prior?: number | null;
  launch_failed_count?: number | null;
  members?: JourneyGroupMember[] | null;
}

export interface MatchedMode {
  mode_id?: number | null;
  label?: string | null;
  label_group?: string | null;
  title?: string | null;
  purity?: number | null;
  support?: number | null;
  status?: string | null;
  seed_key?: string | null;
}

export interface JourneyMatching {
  has_suggestion?: boolean | null;
  stage?: string | null;
  stage_label?: string | null;
  stage_note?: string | null;
  matched_item_id?: number | null;
  matched_item_url?: string | null;
  matched_mode_id?: number | null;
  matched_mode?: MatchedMode | null;
  suggestion_id?: number | null;
}

export interface RetrievalCandidate {
  item_id: number;
  ui_url?: string | null;
  issue_type?: string | null;
  label_source?: string | null;
  sparse_rank?: number | null;
  dense_rank?: number | null;
  cosine?: number | null;
  jaccard_templates?: number | null;
  rrf_score?: number | null;
  is_self?: boolean | null;
  exception_fp?: string | null;
  error_hash?: string | null;
  launch_number?: number | null;
  mode_id?: number | null;
}

export interface JourneyReconstruction {
  hybrid_retrieval_version?: number | string | null;
  note?: string | null;
  query?: {
    salient_terms?: string[] | null;
    template_ids?: number[] | null;
    emb_model_ver?: string | null;
    dense_active?: boolean | null;
  } | null;
  candidates?: RetrievalCandidate[] | null;
}

export interface DecisionFeature {
  key: string;
  label?: string | null;
  value: number;
  group?: string | null;
  definition?: string | null;
  range?: string | null;
  default?: number | string | null;
}

export interface JourneyDecision {
  band?: string | null;
  method?: string | null;
  predicted_label?: string | null;
  predicted_group?: string | null;
  confidence?: number | null;
  tau_suggest?: number | null;
  tau_auto?: number | null;
  explanation?: string | null;
  model_ver?: string | null;
  llm_used?: boolean | null;
  outcome?: string | null;
  abstain_reason?: string | null;
  judge?: { choice?: string | null; chosen_item_id?: number | null } | null;
  features?: DecisionFeature[] | null;
  feature_count?: number | null;
  feature_total?: number | null;
  extra_snapshot?: Record<string, number> | null;
  coldstart_provisional?: boolean | null;
  coldstart?: { rule?: string | null } | null;
  classical?: JourneyDecision | null;
}

export interface LlmEventOutput {
  reason?: string | null;
  rubric_rule_matched?: string | null;
  failing_layer?: string | null;
  error_class?: string | null;
  root_exception?: string | null;
  [key: string]: unknown;
}

export interface JourneyLlmEvent {
  role?: string | null;
  outcome?: string | null;
  model?: string | null;
  cache_hit?: boolean | null;
  latency_ms?: number | null;
  created_at?: string | null;
  prompt_hash?: string | null;
  output?: LlmEventOutput | null;
}

export interface JourneyLlm {
  events?: JourneyLlmEvent[] | null;
  role_state?: Record<string, LlmRoleState> | null;
}

export interface FeedbackEvent {
  event_id: number;
  old_label?: string | null;
  old_group?: string | null;
  new_label?: string | null;
  new_group?: string | null;
  source?: string | null;
  suggestion_id?: number | null;
  ts?: string | null;
}

export interface JourneyResponse {
  rp: RpBlock;
  item: JourneyItem;
  signature?: JourneySignature | null;
  templates?: Template[] | null;
  grouping?: JourneyGrouping | null;
  matching?: JourneyMatching | null;
  reconstruction?: JourneyReconstruction | null;
  decision?: JourneyDecision | null;
  llm?: JourneyLlm | null;
  feedback?: FeedbackEvent[] | null;
}

// --------------------------------------------------------------------------- //
// Cold-start rubric
// --------------------------------------------------------------------------- //

export interface RubricRule {
  rule: string;
  order: number;
  name: string;
  when: string;
  label?: string | null;
  label_name?: string | null;
  confidence?: string | null;
}

export interface RubricResponse {
  rules: RubricRule[];
}

// --------------------------------------------------------------------------- //
// Modes map 3D
// --------------------------------------------------------------------------- //

export interface ModePoint {
  kind: 'item' | 'mode';
  x: number;
  y: number;
  z?: number | null;
  label?: string | null;
  label_group?: string | null;
  item_id?: number | null;
  name?: string | null;
  ui_url?: string | null;
  is_auto_analyzed?: boolean | null;
  purity?: number | null;
  support?: number | null;
  status?: string | null;
}

export interface Modes3dResponse {
  available: boolean;
  reason?: string | null;
  diagnostics?: {
    total_signatures?: number | null;
    embedded_signatures?: number | null;
    modes_with_centroid?: number | null;
  } | null;
  rp: RpBlock;
  n_items?: number | null;
  n_modes?: number | null;
  dimensions?: number | null;
  explained_variance_ratio?: number[] | null;
  points?: ModePoint[] | null;
}

// --------------------------------------------------------------------------- //
// Launch groups
// --------------------------------------------------------------------------- //

export interface LaunchGroup {
  group_id: number;
  launch_id?: number | null;
  launch_url?: string | null;
  dominant?: boolean | null;
  si_prior?: number | null;
  member_count?: number | null;
  fingerprint?: string | null;
}

export interface GroupNode {
  item_id: number;
  group_id?: number | null;
  issue_type?: string | null;
  label_group?: string | null;
  is_auto_analyzed?: boolean | null;
  name?: string | null;
  ui_url?: string | null;
}

export interface GroupsResponse {
  rp: RpBlock;
  groups: LaunchGroup[];
  nodes: GroupNode[];
}

// --------------------------------------------------------------------------- //
// Learning loop timeline
// --------------------------------------------------------------------------- //

export interface LabelEvent {
  item_id: number;
  ui_url?: string | null;
  ts?: string | null;
  old_label?: string | null;
  old_group?: string | null;
  new_label?: string | null;
  new_group?: string | null;
  source?: string | null;
}

export interface ModelArtifact {
  kind: string;
  version?: string | null;
  scope?: string | null;
  is_active?: boolean | null;
  trained_at?: string | null;
  metrics?: Record<string, number> | null;
  feature_schema_ver?: string | number | null;
  n_events?: number | null;
}

export interface MetricsDay {
  day: string;
  accepted: number;
  corrected: number;
  abstained: number;
  ignored: number;
}

export interface Maturity {
  stage?: string | null;
  labeled_items?: number | null;
  gbm_min_events?: number | null;
  calib_min_events?: number | null;
  label_events?: number | null;
  human_events?: number | null;
  modes_confirmed?: number | null;
  modes_candidate?: number | null;
  embedded?: number | null;
  signatures?: number | null;
  embedded_pct?: number | null;
  install_gbm?: string | null;
  install_gbm_events?: number | null;
  project_calibrator?: string | null;
  project_calibrator_events?: number | null;
}

export interface TimelineResponse {
  rp: RpBlock;
  label_events: LabelEvent[];
  model_artifacts: ModelArtifact[];
  metrics_daily: MetricsDay[];
  maturity?: Maturity | null;
}

// --------------------------------------------------------------------------- //
// Signatures explorer
// --------------------------------------------------------------------------- //

export interface SignatureLabelCount {
  locator?: string | null;
  group?: string | null;
  count: number;
}

export interface SignatureRow {
  error_hash: string;
  member_count: number;
  labels?: SignatureLabelCount[] | null;
  exc_classes?: string[] | null;
  status_codes?: string[] | null;
  first_seen?: string | null;
  last_seen?: string | null;
  is_conflict?: boolean | null;
}

export interface SignaturesSummary {
  distinct_error_hash: number;
  distinct_exception_fp: number;
  items_with_signatures: number;
  embedded: number;
  conflict_hashes: number;
}

export interface SignaturesResponse {
  rp: RpBlock;
  summary: SignaturesSummary;
  rows: SignatureRow[];
  count: number;
  offset: number;
  limit: number;
}

export interface SignatureMember {
  item_id: number;
  ui_url?: string | null;
  issue_type?: string | null;
  label_group?: string | null;
  item_name?: string | null;
  is_auto_analyzed?: boolean | null;
  launch_id?: number | null;
  launch_name?: string | null;
  launch_url?: string | null;
  indexed_at?: string | null;
}

export interface SignatureHashResponse {
  rp: RpBlock;
  error_hash: string;
  exception_fp?: string | null;
  representative_item_id?: number | null;
  is_conflict?: boolean | null;
  labels?: SignatureLabelCount[] | null;
  member_total?: number | null;
  member_shown?: number | null;
  signature?: JourneySignature | null;
  templates?: Template[] | null;
  members?: SignatureMember[] | null;
}

// --------------------------------------------------------------------------- //
// Analyzer health probe
// --------------------------------------------------------------------------- //

export type AnalyzerHealthResponse =
  | { configured: false }
  | { configured: true; reachable: true; health: Record<string, unknown> }
  | { configured: true; reachable: false; error: string };

// --------------------------------------------------------------------------- //
// LLM sidecar bookkeeping
// --------------------------------------------------------------------------- //

export interface LlmRoleState {
  enabled?: boolean | null;
  reason?: string | null;
  decided_at?: string | null;
}

export interface LlmLatency {
  p50?: number | null;
  p90?: number | null;
  max?: number | null;
  n?: number | null;
}

export interface LlmRoleSummary {
  role: string;
  event_count: number;
  ok: number;
  outcomes?: Record<string, number> | null;
  from_cache?: number | null;
  last_event?: string | null;
  latency?: LlmLatency | null;
  state?: LlmRoleState | null;
}

export interface LlmSummaryResponse {
  model?: string | null;
  event_total: number;
  roles: LlmRoleSummary[];
}

export interface LlmEvent {
  role?: string | null;
  outcome?: string | null;
  model?: string | null;
  cache_hit?: boolean | null;
  item_id?: number | null;
  launch_id?: number | null;
  latency_ms?: number | null;
  created_at?: string | null;
  output?: LlmEventOutput | null;
}

export interface LlmEventsResponse {
  events: LlmEvent[];
  count: number;
  limit: number;
}

export interface LlmCacheRow {
  template_hash?: string | null;
  model?: string | null;
  hits?: number | null;
  fresh?: boolean | null;
  created_at?: string | null;
  last_hit_at?: string | null;
  output?: LlmEventOutput | null;
  negative?: boolean | null;
}

export interface LlmCacheResponse {
  entries: number;
  total_hits: number;
  rows: LlmCacheRow[];
}
