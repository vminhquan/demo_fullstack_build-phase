export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type Role = "ADMIN" | "MEMBER";
export type Responsibility = "TESTCASE_CREATE" | "TESTCASE_REVIEW" | "TESTCASE_SELF_REVIEW";
export type AccountStatus = "PENDING_REGISTRATION" | "ACTIVE" | "SUSPENDED";
export type ProjectStatus = "ACTIVE" | "SUSPENDED" | "ARCHIVED";
/** PENDING: waiting for a reviewer · REJECTED: a reviewer declined it · DISCARDED: its creator removed it before review. */
export type TestCaseStatus = "PENDING" | "APPROVED" | "REJECTED" | "DISCARDED";
export type Decision = "APPROVED" | "REJECTED";
/** What the caller may do with a case right now (computed by the Backend). */
export type TestCaseAction = "edit" | "discard" | "restore" | "decide";
export type DangerLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type RunStatus = "QUEUED" | "CLAIMED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type RunVerdict = "PASS" | "FAIL" | "ERROR";

export type User = { id: number; email: string; display_name: string | null; account_status: AccountStatus };
export type ProjectSummary = {
  id: number; code: string; name: string; status: ProjectStatus;
  role: Role; responsibilities: Responsibility[]; is_owner: boolean; permissions: string[];
};
export type Project = ProjectSummary & { description: string | null; created_by: number; created_at: string; updated_at: string };
export type ProjectUser = User & { role: Role; responsibilities: Responsibility[]; is_owner: boolean; created_at: string };
export type Session = {
  access_token: string;
  refresh_token: string;
  user: User;
  projects: ProjectSummary[];
  active_project: ProjectSummary | null;
};
export type CurrentSession = Pick<Session, "user" | "projects" | "active_project">;

/** The five metadata fields + tags a person can edit on a test case. */
export type CaseMetadata = {
  map_code: string; ego_vehicle_code: string; adversary_type: string; environment_code: string;
  danger_level: DangerLevel; tag_names: string[];
};
/** In-place edit; omitted fields keep their value. Any change sends the case back to PENDING. */
export type TestCaseUpdate = Partial<CaseMetadata & { title: string; description: string | null; scenario_input: Record<string, unknown> }> & { expected_revision: number };
/** One scenario: generated once, edited in place (no versions), locked for good once put into a simulator run. */
export type TestCase = {
  id: number; case_key: string; title: string; description: string | null; status: TestCaseStatus;
  map_code: string; ego_vehicle_code: string; adversary_type: string; environment_code: string; danger_level: DangerLevel;
  scenario_input: Record<string, unknown>; xosc_artifact_id: number | null; xosc_sha256: string | null; catalog_snapshot_id: number | null;
  revision: number; config_sha256: string;
  created_by: number; created_by_name: string | null; last_edited_by: number | null; last_edited_by_name: string | null; last_edited_at: string | null;
  decided_by: number | null; decided_by_name: string | null; decided_at: string | null;
  discarded_at: string | null; locked_at: string | null; archived_at: string | null; created_at: string; updated_at: string; tags: string[];
  /** Test Case Builder session that generated the case, and its position (1..10) in that session. */
  builder_session_id: number | null; builder_variant_no: number | null;
  can: TestCaseAction[];
};
export type TestCaseDecision = {
  id: number; test_case_id: number; case_key: string | null; title: string | null; decision: Decision; revision: number;
  config_sha256: string; decided_by: number; decided_by_name: string | null; created_at: string; undone_at: string | null;
};
export type BatchItemResult = { id: number; status: TestCaseStatus | null; error: string | null; message: string | null };
/** One picked map and the values ticked for it. The ego is required; any other empty list leaves that category to the
 * Agent. `catalog_snapshot_id` is the CARLA data the choices were listed from (null on older sessions). */
export type BuilderMapOptions = {
  map_code: string; catalog_snapshot_id?: number | null; ego_vehicle_codes: string[]; adversary_types: string[]; environment_codes: string[]; danger_levels: DangerLevel[];
};
export type BuilderSessionCreate = {
  title: string | null; description: string; catalog_source: "DEFAULT" | "PROJECT"; maps: BuilderMapOptions[]; tag_names: string[];
};
export type BuilderSessionStatus = "GENERATING" | "COMPLETED" | "PARTIAL" | "FAILED";
export type BuilderSession = {
  id: number; title: string; title_source: "USER" | "AUTO"; prompt: string; catalog_source: string; maps: BuilderMapOptions[];
  tag_names: string[]; target_count: number; status: BuilderSessionStatus; succeeded_count: number; failed_count: number; pending_count: number;
  created_by: number; created_by_name: string | null; created_at: string; updated_at: string; finished_at: string | null;
};
/** One Scenario Forge Bridge linked to the project; `connection_uid` is shared by the Bridge, Backend and Frontend. */
export type BridgeConnection = {
  connection_uid: string; bridge_uid: string; name: string; hostname: string; os: string; bridge_version: string; online: boolean;
  carla_host: string | null; carla_port: number | null; carla_reachable: boolean | null; last_seen_at: string | null;
  paired_at: string; paired_by: number; paired_by_name: string | null;
  /** Maps this Bridge synced into the project (WORKER catalog snapshots). */
  synced_maps: string[]; last_synced_at: string | null; sync: BridgeSyncState | null;
};
/** Catalog sync in flight (or the last one finished): which map the Bridge is reading and what was stored. */
export type BridgeSyncState = {
  request_id: string | null; finished: boolean; status: "requested" | "loading" | "uploaded" | "failed" | "done" | null;
  index: number | null; total: number | null; map_name: string | null;
  synced: string[]; failed: { map_name: string | null; error: string }[]; started_at?: string; finished_at?: string;
};
export type BridgePairCode = { id: number; code: string; expires_at: string; ttl_seconds: number };
export type BridgeEvent =
  | { type: "ready" }
  | { type: "bridge.paired"; pair_code_id: number; connection: BridgeConnection | null }
  | { type: "bridge.online" | "bridge.offline" | "bridge.status"; connection_uid: string; bridge_uid: string; online: boolean; carla_reachable: boolean | null }
  | { type: "bridge.unpaired"; connection_uid: string }
  | { type: "catalog.sync.progress" | "catalog.sync.done"; connection_uid: string; sync: BridgeSyncState }
  | { type: "catalog.synced"; connection_uid: string; snapshot: { id: number; map_name: string; carla_version: string; created: boolean } };
export type BuilderError = { variant_no: number; map_code: string; code: string; message: string };
export type BuilderSessionDetail = BuilderSession & { errors: BuilderError[]; test_cases: TestCase[] };
export type TestCasePage = { items: TestCase[]; page: number; page_size: number; total: number };
export type SearchFilters = {
  map_code?: string[]; adversary_type?: string[]; environment_code?: string[];
  danger_level?: string[]; status?: string[]; creator_id?: number[]; tag?: string[];
};
export type SearchHit = {
  case_id: number; case_key: string; revision: number; title: string; status: TestCaseStatus;
  map_code: string; adversary_type: string; environment_code: string; danger_level: DangerLevel; tags: string[];
  score: number | null; matched_by: string[];
};
export type SearchResponse = { items: SearchHit[]; page: number; page_size: number; total: number; semantic_fallback: boolean };
export type RagSearchFilters = Partial<Record<"map_code" | "adversary_type" | "environment_code" | "danger_level" | "status" | "tag" | "verdict", string[]>> & { collision_only?: boolean };
export type SimulationSummary = { run_result_id: number | null; run_job_id: number | null; verdict: RunVerdict; collision: boolean; min_ttc_seconds: number | null; duration_ms: number | null; metrics: Record<string, unknown> };
export type RagSearchHit = SearchHit & { match_reasons: string[]; simulation: SimulationSummary | null };
export type RagSearchResponse = { prompt: string; interpreted_filters: RagSearchFilters; items: RagSearchHit[]; total: number; retrieval_mode: string; semantic_fallback: boolean };
export type WorkSummary = { scope: "all" | "mine"; pending_review: number; rejected: number; approved_not_in_suite: number; in_suite_without_result: number };
export type TestSuiteItem = { test_case_id: number; position: number; added_by: number; added_at: string };
export type TestSuite = { id: number; name: string; description: string | null; created_by: number; created_at: string; updated_at: string; items: TestSuiteItem[] };
/** Simulator Runner run executed by the project's Bridge (run.assign → results over the Bridge socket). */
export type SimulatorRunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type SimulatorRunJob = {
  test_case_id: number; case_key: string; title: string; map_code: string; adversary_type: string; environment_code: string;
  danger_level: DangerLevel; revision: number | null; status: RunStatus; verdict: RunVerdict | null; collision: boolean | null;
  min_ttc_seconds: number | null; duration_ms: number | null; exit_code: number | null; metrics: Record<string, unknown>;
  error_code: string | null; error_message: string | null; started_at: string | null; finished_at: string | null;
};
export type SimulatorRun = {
  id: number; status: SimulatorRunStatus; created_at: string; started_at: string | null; finished_at: string | null;
  dispatched_at: string | null; accepted_at: string | null; requested_by: number; requested_by_name: string | null;
  bridge: { connection_uid: string; name: string; online: boolean } | null;
  total: number; done: number; passed: number; failed: number; errors: number; test_case_ids: number[];
};
export type SimulatorRunDetail = SimulatorRun & { jobs: SimulatorRunJob[]; skipped: { id: number; reason: string }[] };
export type CaseRunEntry = { run_id: number; created_at: string; requested_by_name: string | null; job: SimulatorRunJob };
export type SuiteRun = { id: number; suite_id: number; status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; total_jobs: number; completed_jobs: number; created_at: string; started_at: string | null; finished_at: string | null };
export type RunResult = { id: number; run_job_id: number; test_case_id: number; revision: number | null; verdict: RunVerdict; metrics: Record<string, unknown>; scenario_runner_exit_code: number | null; duration_ms: number | null };
export type RunResultListItem = RunResult & { case_key: string; title: string; map_code: string; adversary_type: string; environment_code: string; collision: boolean; min_ttc_seconds: number | null; created_at: string };
export type RunResultPage = { items: RunResultListItem[]; page: number; page_size: number; total: number };
export type RunJob = { id: number; suite_run_id: number | null; test_case_id: number; status: RunStatus; worker_id: string | null; attempt: number; error_code: string | null; error_message: string | null; result: RunResult | null };
export type RunJobSummary = Omit<RunJob, "suite_run_id" | "worker_id" | "result">;
export type CatalogSource = "DEFAULT" | "WORKER" | "IMPORT";
export type CatalogSnapshot = { id: number; source: CatalogSource; is_default: boolean; carla_version: string; map_name: string; content_hash: string; spawn_point_count: number; waypoint_count: number; vehicle_count: number; walker_count: number; label: string | null; worker_installation_id: number | null; created_by: number | null; created_at: string };
export type MapOption = { code: string; has_lane_data: boolean; snapshot_ids: number[]; sources: string[]; carla_versions: string[] };
export type VehicleOption = { code: string; label: string; base_type: string; snapshot_ids: number[] };
export type CodeOption = { code: string; label: string; snapshot_ids: number[]; group: string | null };
export type MetadataOptions = { snapshots: CatalogSnapshot[]; maps: MapOption[]; ego_vehicles: VehicleOption[]; adversary_types: CodeOption[]; environments: CodeOption[] };
export type GenerationCreate = {
  prompt: string; catalog_source: "DEFAULT" | "PROJECT"; catalog_snapshot_id?: number | null; map_name?: string | null; auto_repair?: boolean; seed?: number | null;
  spec?: ScenarioSpec | null; constraints?: Record<string, unknown> | null;
  // The map is required; ego / adversary / environment are optional and chosen by the Agent when left out.
  metadata?: { map_code: string; ego_vehicle_code?: string | null; adversary_type?: string | null; environment_code?: string | null } | null;
};
export type NumberRange = { min: number; max: number };
export type ScenarioSpec = {
  goal: string;
  scenario_type: "cut_in" | "sudden_brake" | "crossing" | "oncoming_lane_departure" | "door_opening";
  adversary_type: string;
  direction: "left" | "right";
  road_type: "any" | "intersection" | "straight" | "curve" | "highway";
  weather: ("clear" | "rain" | "heavy_rain" | "fog")[];
  lighting: "day" | "dusk" | "night";
  objective: "critical" | "boundary" | "nominal";
  ego_speed_kmh: NumberRange; actor_speed_kmh: NumberRange; initial_gap_m: NumberRange; trigger_distance_m: NumberRange;
  ego_blueprint: string | null;
};
export type RoadCategory = "intersection" | "straight" | "curve" | "highway";
export type WeatherCode = "clear" | "rain" | "heavy_rain" | "fog";
export type LightingCode = "day" | "dusk" | "night";
export type OddDeclaration = {
  catalog_snapshot_ids: number[]; road_types: RoadCategory[]; weather: WeatherCode[]; lighting: LightingCode[];
  adversary_types: string[]; ego_vehicles: string[]; ego_speed_kmh: NumberRange; actor_speed_kmh: NumberRange; note: string;
};
export type MapCapability = {
  snapshot_id: number; map_name: string; carla_version: string; origin: string; road_types: RoadCategory[];
  has_oncoming_lane: boolean; adversary_types: string[]; ego_vehicles: string[]; error: string | null;
};
export type OddGap = { dimension: string; value: string; reason: string };
export type OddProfile = { declaration: OddDeclaration | null; updated_at: string | null; updated_by: number | null; capabilities: MapCapability[]; gaps: OddGap[] };
export type PlanForm = {
  goal: string; scenario_type: ScenarioSpec["scenario_type"]; direction: "left" | "right"; objective: ScenarioSpec["objective"];
  road_types: RoadCategory[]; weather: WeatherCode[]; lighting: LightingCode[]; adversary_types: string[];
  ego_speed_kmh: NumberRange; actor_speed_kmh: NumberRange; initial_gap_m: NumberRange; trigger_distance_m: NumberRange; ego_blueprint: string | null;
};
export type PlannedCase = { index: number; catalog_snapshot_id: number; map_name: string; cell: Record<"road_type" | "weather" | "lighting" | "adversary_type", string>; spec: ScenarioSpec };
export type CasePlan = {
  cases: PlannedCase[]; auto: boolean; coverage: Record<string, Record<string, number>>; pairs_total: number; pairs_covered: number;
  uncovered_pairs: string[]; infeasible: OddGap[]; outside_odd: OddGap[];
};
export type CoverageReport = {
  source: "odd" | "observed"; values: Record<string, Record<string, number>>; values_total: number; values_covered: number;
  pairs_total: number; pairs_covered: number; uncovered_pairs: string[]; outside_odd: string[];
};
export type GenerationReport = {
  days: number; attempts: number; completed: number; valid: number; fully_on_lanes: number;
  valid_rate: number | null; valid_rate_of_completed: number | null; completion_rate: number | null;
  failures: Record<string, number>; invalid_reasons: Record<string, number>; by_mode: Record<string, number>; from_form: number;
  review: { saved: number; statuses: Record<string, number>; approval_rate: number | null };
  danger: { levels: Record<string, number>; expected_verdicts: Record<string, number>; mean_threat: number | null; median_threat: number | null };
  coverage: CoverageReport;
  cost: {
    refine_calls: number; input_tokens: number; output_tokens: number; usd: number; usd_per_valid: number | null; tokens_per_generation: number;
    cache_hits: number; tokens_saved_by_cache: number; latency_p50_ms: number; latency_p95_ms: number; price_input_per_mtok: number; price_output_per_mtok: number;
  };
  daily: { day: string; attempts: number; completed: number; valid: number }[];
};
export type SpecAdjustment = { field: string; before: string; after: string; reason: string };
export type RefinedSpec = {
  catalog: CatalogSnapshot; refined_prompt: string;
  values: { ego_speed_kmh: number; actor_speed_kmh: number; initial_gap_m: number; trigger_distance_m: number };
  rationale: string; refine_mode: "llm" | "deterministic"; model: string | null; spec: ScenarioSpec; constraints: Record<string, unknown>;
  adjustments: SpecAdjustment[]; road_type: string; weather: string; warnings: string[];
};
export type ScenarioActorIR = { actor_type: string; relative_position: string; initial_distance_m: number; initial_speed_kmh: number; trigger: string | null; trigger_distance_m: number | null };
export type ScenarioIR = { name: string; description: string; ego: { initial_speed_kmh: number; road_type: string }; actors: ScenarioActorIR[]; weather: string; time_of_day_hour: number; map_name: string; occlusions: string[]; expected_outcome: { expected_verdict: string; expected_min_ttc_range: [number, number] | null; rationale: string | null } | null };
export type PreviewPoint = { step: number; x: number; y: number };
export type PlacedEntity = { entity_name: string; actor_type: string; blueprint: string; x: number; y: number; z: number; yaw_deg: number; initial_speed_kmh: number; relative_position: string; trigger: string | null; trigger_distance_m: number | null; lane: { road_id: number; lane_id: number } | null; placement: "lane" | "roadside" | "geometric" | "spawn_point"; preview_waypoints: PreviewPoint[] };
export type Grounding = { map_name: string; carla_version: string; catalog_content_hash: string | null; ego_spawn_index: number; fully_on_lanes: boolean; ego: PlacedEntity; actors: PlacedEntity[]; warnings: string[] };
export type SuggestedMetadata = { map_code: string; ego_vehicle_code: string; adversary_type: string; environment_code: string; danger_level: DangerLevel; tag_names: string[] };
export type ScenarioGeneration = {
  id: number; status: "COMPLETED" | "ACCEPTED"; prompt: string; catalog: CatalogSnapshot; generation_mode: "llm" | "deterministic"; model: string | null;
  scenario_ir: ScenarioIR; form: { spec: ScenarioSpec | null; constraints: Record<string, unknown> | null } | null; interpretation: Record<string, unknown>; validation: { is_valid?: boolean; verdict?: string; errors?: string[]; warnings?: string[] };
  threat_score: { weighted_threat?: number; dominant_dimension?: string }; grounding: Grounding; xosc_validation: { schema_valid?: boolean; errors?: string[]; schema?: string };
  retrieved_regulations: string[]; warnings: string[]; xosc: string; xosc_sha256: string; suggested_metadata: SuggestedMetadata; accepted_test_case_id: number | null; created_by: number; created_at: string;
};
export type AuditLog = { id: number; actor_user_id: number | null; actor_name: string | null; actor_email: string | null; entity_label: string | null; action: string; entity_type: string; entity_id: number | null; entity_version_id: number | null; request_id: string | null; before_data: Record<string, unknown> | null; after_data: Record<string, unknown> | null; created_at: string };

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}

async function parseError(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => null) as { error?: { message?: string }; detail?: string | { msg?: string }[] } | null;
  const detail = Array.isArray(payload?.detail) ? payload.detail.map((item) => item.msg).filter(Boolean).join("; ") : payload?.detail;
  return new ApiError(payload?.error?.message ?? detail ?? "Không thể kết nối tới Backend.", response.status);
}

// Backend resources that belong to one project are served under /projects/{project_id}/….
const PROJECT_SCOPED = [
  "/test-cases", "/test-case-decisions", "/rag-search", "/test-suites", "/suite-runs", "/run-jobs", "/run-results",
  "/artifacts", "/audit-logs", "/dashboard", "/carla-catalog", "/scenario-generations", "/odd-profile", "/reports", "/builder-sessions",
  "/bridges", "/simulator-runs",
];

/** Project of the page (/projects/{id}/… in the address bar), else the project selected in the access token. */
function currentProjectId(token?: string): string | null {
  if (typeof window !== "undefined") {
    const match = /^\/projects\/(\d+)(?=\/|$)/.exec(window.location.pathname);
    if (match) return match[1];
  }
  try {
    const payload = JSON.parse(atob((token ?? "").split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.project_id ? String(payload.project_id) : null;
  } catch {
    return null;
  }
}

function apiPath(path: string, token?: string) {
  if (!PROJECT_SCOPED.some((prefix) => path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}?`))) return path;
  const projectId = currentProjectId(token);
  return projectId ? `/projects/${projectId}${path}` : path;
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${apiPath(path, token)}`, { ...options, headers });
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** Live Bridge events of a project (browsers cannot send headers on WebSockets, so the token goes in the query). */
export function bridgeEventsUrl(token: string, projectId: number | string) {
  const base = API_BASE.startsWith("http") ? API_BASE : `${window.location.origin}${API_BASE}`;
  return `${base.replace(/^http/, "ws")}/projects/${projectId}/bridges/events?token=${encodeURIComponent(token)}`;
}

async function download(path: string, token: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}${apiPath(path, token)}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) throw await parseError(response);
  return response.blob();
}

type Id = number | string;

export const api = {
  login: (email: string, password: string) => request<Session>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  register: (email: string, password: string, displayName: string) => request<Session>("/auth/register", { method: "POST", body: JSON.stringify({ email, password, display_name: displayName }) }),
  refresh: (refreshToken: string) => request<Session>("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }),
  me: (token: string) => request<CurrentSession>("/auth/me", {}, token),
  selectProject: (token: string, projectId: number, refreshToken?: string) => request<Session>("/auth/select-project", { method: "POST", body: JSON.stringify({ project_id: projectId, refresh_token: refreshToken }) }, token),
  logout: (refreshToken: string) => request<void>("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }),

  listProjects: (token: string) => request<Project[]>("/projects", {}, token),
  getProject: (token: string, id: Id) => request<Project>(`/projects/${id}`, {}, token),
  createProject: (token: string, name: string, description: string) => request<Project>("/projects", { method: "POST", body: JSON.stringify({ name, description }) }, token),
  updateProject: (token: string, id: Id, name: string, description: string) => request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify({ name, description }) }, token),
  listProjectUsers: (token: string, projectId: Id) => request<ProjectUser[]>(`/projects/${projectId}/users`, {}, token),
  deleteProject: (token: string, id: Id) => request<void>(`/projects/${id}`, { method: "DELETE" }, token),
  leaveProject: (token: string, id: Id) => request<void>(`/projects/${id}/leave`, { method: "POST" }, token),
  addProjectUser: (token: string, projectId: Id, email: string, role: Role, responsibilities: Responsibility[]) => request<ProjectUser>(`/projects/${projectId}/users`, { method: "POST", body: JSON.stringify({ email, role, responsibilities }) }, token),
  updateProjectUserRole: (token: string, projectId: Id, userId: Id, role: Role) => request<ProjectUser>(`/projects/${projectId}/users/${userId}/role`, { method: "PATCH", body: JSON.stringify({ role }) }, token),
  updateProjectUserResponsibilities: (token: string, projectId: Id, userId: Id, responsibilities: Responsibility[]) => request<ProjectUser>(`/projects/${projectId}/users/${userId}/responsibilities`, { method: "PUT", body: JSON.stringify({ responsibilities }) }, token),
  removeProjectUser: (token: string, projectId: Id, userId: Id) => request<void>(`/projects/${projectId}/users/${userId}`, { method: "DELETE" }, token),

  createBuilderSession: (token: string, body: BuilderSessionCreate) => request<BuilderSession>("/builder-sessions", { method: "POST", body: JSON.stringify(body) }, token),
  listBuilderSessions: (token: string) => request<BuilderSession[]>("/builder-sessions", {}, token),
  getBuilderSession: (token: string, id: Id) => request<BuilderSessionDetail>(`/builder-sessions/${id}`, {}, token),
  listCases: (token: string, query: URLSearchParams) => request<TestCasePage>(`/test-cases?${query.toString()}`, {}, token),
  searchCases: (token: string, body: { query: string; mode: "filter" | "keyword" | "semantic" | "hybrid"; filters: SearchFilters; page: number; page_size: number }) => request<SearchResponse>("/test-cases/search", { method: "POST", body: JSON.stringify(body) }, token),
  ragSearch: (token: string, body: { prompt: string; filters: RagSearchFilters; limit?: number }) => request<RagSearchResponse>("/rag-search", { method: "POST", body: JSON.stringify(body) }, token),
  getCase: (token: string, id: Id) => request<TestCase>(`/test-cases/${id}`, {}, token),
  updateCase: (token: string, id: Id, body: TestCaseUpdate) => request<TestCase>(`/test-cases/${id}`, { method: "PATCH", body: JSON.stringify(body) }, token),
  uploadXosc: (token: string, caseId: Id, file: File, expectedRevision: number) => { const body = new FormData(); body.set("file", file); body.set("expected_revision", String(expectedRevision)); return request<{ artifact_id: number; sha256: string; size_bytes: number }>(`/test-cases/${caseId}/xosc`, { method: "POST", body }, token); },
  downloadXosc: (token: string, caseId: Id) => download(`/test-cases/${caseId}/xosc`, token),
  decideCase: (token: string, id: Id, decision: Decision, expectedRevision?: number) => request<TestCase>(`/test-cases/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, expected_revision: expectedRevision ?? null }) }, token),
  undoDecision: (token: string, id: Id) => request<TestCase>(`/test-cases/${id}/decision/undo`, { method: "POST" }, token),
  decideCases: (token: string, ids: number[], decision: Decision) => request<{ results: BatchItemResult[] }>("/test-cases/batch/decision", { method: "POST", body: JSON.stringify({ ids, decision }) }, token),
  discardCase: (token: string, id: Id) => request<TestCase>(`/test-cases/${id}/discard`, { method: "POST" }, token),
  discardCases: (token: string, ids: number[]) => request<{ results: BatchItemResult[] }>("/test-cases/batch/discard", { method: "POST", body: JSON.stringify({ ids }) }, token),
  restoreCase: (token: string, id: Id) => request<TestCase>(`/test-cases/${id}/restore`, { method: "POST" }, token),
  /** Putting cases into a simulator run locks them for good; cases no longer APPROVED come back in `skipped`. */
  createSimulatorRun: (token: string, connectionUid: string, testCaseIds: number[]) => request<SimulatorRunDetail>("/simulator-runs", { method: "POST", body: JSON.stringify({ connection_uid: connectionUid, test_case_ids: testCaseIds }) }, token),
  listSimulatorRuns: (token: string) => request<SimulatorRun[]>("/simulator-runs", {}, token),
  getSimulatorRun: (token: string, id: Id) => request<SimulatorRunDetail>(`/simulator-runs/${id}`, {}, token),
  listCaseRuns: (token: string, caseId: Id) => request<CaseRunEntry[]>(`/simulator-runs/by-case/${caseId}`, {}, token),
  lockCases: (token: string, ids: number[]) => request<{ locked: number[]; skipped: BatchItemResult[] }>("/test-cases/lock", { method: "POST", body: JSON.stringify({ ids }) }, token),
  listCaseDecisions: (token: string, id: Id) => request<TestCaseDecision[]>(`/test-cases/${id}/decisions`, {}, token),
  createBridgePairCode: (token: string) => request<BridgePairCode>("/bridges/pair-codes", { method: "POST" }, token),
  cancelBridgePairCode: (token: string, id: Id) => request<void>(`/bridges/pair-codes/${id}`, { method: "DELETE" }, token),
  listBridges: (token: string) => request<BridgeConnection[]>("/bridges", {}, token),
  unpairBridge: (token: string, connectionUid: string) => request<void>(`/bridges/${encodeURIComponent(connectionUid)}`, { method: "DELETE" }, token),
  syncBridgeCatalog: (token: string, connectionUid: string, maps?: string[]) =>
    request<{ request_id: string }>(`/bridges/${encodeURIComponent(connectionUid)}/sync`, { method: "POST", body: JSON.stringify({ maps: maps ?? null }) }, token),
  workSummary: (token: string) => request<WorkSummary>("/dashboard/summary", {}, token),
  listDecisions: (token: string) => request<TestCaseDecision[]>("/test-case-decisions", {}, token),
  listSuites: (token: string) => request<TestSuite[]>("/test-suites", {}, token),
  getSuite: (token: string, id: Id) => request<TestSuite>(`/test-suites/${id}`, {}, token),
  createSuite: (token: string, name: string, description: string) => request<TestSuite>("/test-suites", { method: "POST", body: JSON.stringify({ name, description }) }, token),
  updateSuite: (token: string, id: Id, name: string, description: string) => request<TestSuite>(`/test-suites/${id}`, { method: "PATCH", body: JSON.stringify({ name, description }) }, token),
  deleteSuite: (token: string, id: Id) => request<void>(`/test-suites/${id}`, { method: "DELETE" }, token),
  addSuiteItem: (token: string, id: Id, caseId: Id) => request<TestSuite>(`/test-suites/${id}/items`, { method: "POST", body: JSON.stringify({ test_case_id: Number(caseId) }) }, token),
  removeSuiteItem: (token: string, id: Id, caseId: Id) => request<TestSuite>(`/test-suites/${id}/items/${caseId}`, { method: "DELETE" }, token),
  reorderSuiteItems: (token: string, id: Id, caseIds: number[]) => request<TestSuite>(`/test-suites/${id}/items/reorder`, { method: "PATCH", body: JSON.stringify({ test_case_ids: caseIds }) }, token),
  runSuite: (token: string, id: Id) => request<SuiteRun>(`/test-suites/${id}/runs`, { method: "POST" }, token),
  listSuiteRuns: (token: string, id: Id) => request<SuiteRun[]>(`/test-suites/${id}/runs`, {}, token),
  exportSuite: (token: string, id: Id) => request<{ state: string; test_case_ids: number[] }>(`/test-suites/${id}/export`, { method: "POST" }, token),
  getSuiteRun: (token: string, id: Id) => request<SuiteRun>(`/suite-runs/${id}`, {}, token),
  listRunJobs: (token: string, id: Id) => request<RunJobSummary[]>(`/suite-runs/${id}/jobs`, {}, token),
  getRunJob: (token: string, id: Id) => request<RunJob>(`/run-jobs/${id}`, {}, token),
  listRunResults: (token: string, query: URLSearchParams) => request<RunResultPage>(`/run-results?${query.toString()}`, {}, token),
  cancelRunJob: (token: string, id: Id) => request<RunJob>(`/run-jobs/${id}/cancel`, { method: "POST" }, token),
  listCatalogSnapshots: (token: string, source?: "DEFAULT" | "PROJECT") => request<CatalogSnapshot[]>(`/carla-catalog/snapshots${source ? `?source=${source}` : ""}`, {}, token),
  importCatalogSnapshot: (token: string, catalog: unknown, label: string) => request<{ snapshot: CatalogSnapshot; created: boolean }>("/carla-catalog/snapshots/import", { method: "POST", body: JSON.stringify({ catalog, label: label || null }) }, token),
  getMetadataOptions: (token: string) => request<MetadataOptions>("/carla-catalog/metadata-options", {}, token),
  uploadCatalogFile: (token: string, file: File, label: string) => { const body = new FormData(); body.set("file", file); if (label) body.set("label", label); return request<{ snapshot: CatalogSnapshot; created: boolean }>("/carla-catalog/snapshots/upload", { method: "POST", body }, token); },
  refineScenarioSpec: (token: string, body: { spec: ScenarioSpec; catalog_source: "DEFAULT" | "PROJECT"; catalog_snapshot_id: number | null; seed?: number | null }) => request<RefinedSpec>("/scenario-generations/refine", { method: "POST", body: JSON.stringify(body) }, token),
  getOdd: (token: string) => request<OddProfile>("/odd-profile", {}, token),
  checkOdd: (token: string, body: OddDeclaration) => request<{ gaps: OddGap[] }>("/odd-profile/check", { method: "POST", body: JSON.stringify(body) }, token),
  saveOdd: (token: string, body: OddDeclaration) => request<OddProfile>("/odd-profile", { method: "PUT", body: JSON.stringify(body) }, token),
  getGenerationReport: (token: string, days: number) => request<GenerationReport>(`/reports/generation?days=${days}`, {}, token),
  planCases: (token: string, form: PlanForm, count: number | null) => request<CasePlan>("/odd-profile/plan", { method: "POST", body: JSON.stringify({ form, count }) }, token),
  createGeneration: (token: string, body: GenerationCreate) => request<ScenarioGeneration>("/scenario-generations", { method: "POST", body: JSON.stringify(body) }, token),
  getGeneration: (token: string, id: Id) => request<ScenarioGeneration>(`/scenario-generations/${id}`, {}, token),
  listAudit: (token: string, query: URLSearchParams) => request<AuditLog[]>(`/audit-logs?${query.toString()}`, {}, token),
};
