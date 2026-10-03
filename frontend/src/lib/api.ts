const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type Role = "ADMIN" | "MEMBER";
export type Responsibility = "TESTCASE_CREATE" | "TESTCASE_REVIEW" | "TESTCASE_SELF_REVIEW";
export type AccountStatus = "PENDING_REGISTRATION" | "ACTIVE" | "SUSPENDED";
export type ProjectStatus = "ACTIVE" | "SUSPENDED" | "ARCHIVED";
export type VersionStatus = "DRAFT" | "IN_REVIEW" | "EDIT" | "APPROVED" | "REJECTED";
export type ReviewDecision = "APPROVED" | "EDIT" | "REJECTED";
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

export type VersionPayload = {
  map_code: string; ego_vehicle_code: string; adversary_type: string; environment_code: string;
  danger_level: DangerLevel; scenario_input: Record<string, unknown>; tag_names: string[]; change_note?: string | null;
};
export type TestCaseVersion = VersionPayload & {
  id: number; test_case_id: number; version_no: number; status: VersionStatus; xosc_artifact_id: number | null;
  created_by: number; created_at: string; submitted_at: string | null; decided_at: string | null; decided_by: number | null; tags: string[];
};
export type TestCase = {
  id: number; case_key: string; title: string; description: string | null; created_by: number;
  created_at: string; updated_at: string; archived_at: string | null; latest_version: TestCaseVersion | null;
};
export type TestCasePage = { items: TestCase[]; page: number; page_size: number; total: number };
export type SearchFilters = {
  map_code?: string[]; adversary_type?: string[]; environment_code?: string[];
  danger_level?: string[]; status?: string[]; creator_id?: number[]; tag?: string[];
};
export type SearchHit = {
  case_id: number; case_key: string; version_id: number; version_no: number; title: string; status: VersionStatus;
  map_code: string; adversary_type: string; environment_code: string; danger_level: DangerLevel; tags: string[];
  score: number | null; matched_by: string[];
};
export type SearchResponse = { items: SearchHit[]; page: number; page_size: number; total: number; semantic_fallback: boolean };
export type RagSearchFilters = Partial<Record<"map_code" | "adversary_type" | "environment_code" | "danger_level" | "status" | "tag" | "verdict", string[]>> & { collision_only?: boolean };
export type SimulationSummary = { run_result_id: number | null; run_job_id: number | null; verdict: RunVerdict; collision: boolean; min_ttc_seconds: number | null; duration_ms: number | null; metrics: Record<string, unknown> };
export type RagSearchHit = SearchHit & { match_reasons: string[]; simulation: SimulationSummary | null };
export type RagSearchResponse = { prompt: string; interpreted_filters: RagSearchFilters; items: RagSearchHit[]; total: number; retrieval_mode: string; semantic_fallback: boolean };
export type ReviewComment = { id: number; creator_id: number; body: string; comment_type: "COMMENT" | "DECISION"; created_at: string };
export type ReviewEvidence = { run_result_id: number; run_job_id: number; verdict: RunVerdict; collision: boolean; min_ttc_seconds: number | null; recorded_at: string; total_runs: number };
export type Review = {
  id: number; version_id: number; requested_by: number; requested_at: string; resolved_by: number | null;
  resolved_at: string | null; decision: ReviewDecision | null; comments: ReviewComment[];
  case_id: number | null; case_key: string | null; title: string | null; version_no: number | null; version_status: VersionStatus | null;
  map_code: string | null; adversary_type: string | null; environment_code: string | null; danger_level: DangerLevel | null;
  requested_by_name: string | null; resolved_by_name: string | null; decision_comment: string | null; evidence: ReviewEvidence | null;
};
export type WorkSummary = { scope: "all" | "mine"; pending_review: number; needs_changes: number; approved_not_in_suite: number; in_suite_without_result: number };
export type TestSuiteItem = { test_case_version_id: number; position: number; added_by: number; added_at: string };
export type TestSuite = { id: number; name: string; description: string | null; created_by: number; created_at: string; updated_at: string; items: TestSuiteItem[] };
export type SuiteRun = { id: number; suite_id: number; status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; total_jobs: number; completed_jobs: number; created_at: string; started_at: string | null; finished_at: string | null };
export type RunResult = { id: number; run_job_id: number; test_case_version_id: number; verdict: RunVerdict; metrics: Record<string, unknown>; scenario_runner_exit_code: number | null; duration_ms: number | null };
export type RunResultListItem = RunResult & { test_case_id: number; case_key: string; title: string; version_no: number; map_code: string; adversary_type: string; environment_code: string; collision: boolean; min_ttc_seconds: number | null; created_at: string };
export type RunResultPage = { items: RunResultListItem[]; page: number; page_size: number; total: number };
export type RunJob = { id: number; suite_run_id: number | null; test_case_version_id: number; status: RunStatus; worker_id: string | null; attempt: number; error_code: string | null; error_message: string | null; result: RunResult | null };
export type RunJobSummary = Omit<RunJob, "suite_run_id" | "worker_id" | "result">;
export type AuditLog = { id: number; actor_user_id: number | null; actor_name: string | null; actor_email: string | null; entity_label: string | null; action: string; entity_type: string; entity_id: number | null; entity_version_id: number | null; request_id: string | null; before_data: Record<string, unknown> | null; after_data: Record<string, unknown> | null; created_at: string };

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}

async function parseError(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => null) as { error?: { message?: string }; detail?: string | { msg?: string }[] } | null;
  const detail = Array.isArray(payload?.detail) ? payload.detail.map((item) => item.msg).filter(Boolean).join("; ") : payload?.detail;
  return new ApiError(payload?.error?.message ?? detail ?? "Không thể kết nối tới Backend.", response.status);
}

async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function download(path: string, token: string): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${token}` } });
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

  listCases: (token: string, query: URLSearchParams) => request<TestCasePage>(`/test-cases?${query.toString()}`, {}, token),
  searchCases: (token: string, body: { query: string; mode: "filter" | "keyword" | "semantic" | "hybrid"; filters: SearchFilters; page: number; page_size: number }) => request<SearchResponse>("/test-cases/search", { method: "POST", body: JSON.stringify(body) }, token),
  ragSearch: (token: string, body: { prompt: string; filters: RagSearchFilters; limit?: number }) => request<RagSearchResponse>("/rag-search", { method: "POST", body: JSON.stringify(body) }, token),
  getCase: (token: string, id: Id) => request<TestCase>(`/test-cases/${id}`, {}, token),
  createCase: (token: string, title: string, description: string) => request<TestCase>("/test-cases", { method: "POST", body: JSON.stringify({ title, description }) }, token),
  updateCase: (token: string, id: Id, title: string, description: string) => request<TestCase>(`/test-cases/${id}`, { method: "PATCH", body: JSON.stringify({ title, description }) }, token),
  listVersions: (token: string, caseId: Id) => request<TestCaseVersion[]>(`/test-cases/${caseId}/versions`, {}, token),
  getVersion: (token: string, versionId: Id) => request<TestCaseVersion>(`/test-case-versions/${versionId}`, {}, token),
  createVersion: (token: string, caseId: Id, payload: VersionPayload) => request<TestCaseVersion>(`/test-cases/${caseId}/versions`, { method: "POST", body: JSON.stringify(payload) }, token),
  updateVersion: (token: string, versionId: Id, payload: VersionPayload) => request<TestCaseVersion>(`/test-case-versions/${versionId}`, { method: "PATCH", body: JSON.stringify(payload) }, token),
  cloneVersion: (token: string, versionId: Id) => request<TestCaseVersion>(`/test-case-versions/${versionId}/clone`, { method: "POST" }, token),
  uploadXosc: (token: string, versionId: Id, file: File) => { const body = new FormData(); body.set("file", file); return request<{ artifact_id: number; sha256: string; size_bytes: number }>(`/test-case-versions/${versionId}/xosc`, { method: "POST", body }, token); },
  downloadXosc: (token: string, versionId: Id) => download(`/test-case-versions/${versionId}/xosc`, token),
  submitReview: (token: string, versionId: Id, message: string) => request<Review>(`/test-case-versions/${versionId}/submit-review`, { method: "POST", body: JSON.stringify({ message }) }, token),
  // "open": waiting for a decision; "resolved": decision history. Members without review permission only get their own.
  listReviews: (token: string, filter: "open" | "resolved" = "open") => request<Review[]>(filter === "resolved" ? "/reviews?resolved_only=true" : "/reviews?open_only=true", {}, token),
  workSummary: (token: string) => request<WorkSummary>("/dashboard/summary", {}, token),
  getReview: (token: string, id: Id) => request<Review>(`/reviews/${id}`, {}, token),
  commentReview: (token: string, id: Id, body: string) => request<Review>(`/reviews/${id}/comments`, { method: "POST", body: JSON.stringify({ body }) }, token),
  decideReview: (token: string, id: Id, decision: ReviewDecision, comment: string) => request<Review>(`/reviews/${id}/${decision === "EDIT" ? "request-edit" : decision.toLowerCase()}`, { method: "POST", body: JSON.stringify({ comment }) }, token),
  listSuites: (token: string) => request<TestSuite[]>("/test-suites", {}, token),
  getSuite: (token: string, id: Id) => request<TestSuite>(`/test-suites/${id}`, {}, token),
  createSuite: (token: string, name: string, description: string) => request<TestSuite>("/test-suites", { method: "POST", body: JSON.stringify({ name, description }) }, token),
  updateSuite: (token: string, id: Id, name: string, description: string) => request<TestSuite>(`/test-suites/${id}`, { method: "PATCH", body: JSON.stringify({ name, description }) }, token),
  deleteSuite: (token: string, id: Id) => request<void>(`/test-suites/${id}`, { method: "DELETE" }, token),
  addSuiteItem: (token: string, id: Id, versionId: Id) => request<TestSuite>(`/test-suites/${id}/items`, { method: "POST", body: JSON.stringify({ test_case_version_id: Number(versionId) }) }, token),
  removeSuiteItem: (token: string, id: Id, versionId: Id) => request<TestSuite>(`/test-suites/${id}/items/${versionId}`, { method: "DELETE" }, token),
  reorderSuiteItems: (token: string, id: Id, versionIds: number[]) => request<TestSuite>(`/test-suites/${id}/items/reorder`, { method: "PATCH", body: JSON.stringify({ version_ids: versionIds }) }, token),
  runSuite: (token: string, id: Id) => request<SuiteRun>(`/test-suites/${id}/runs`, { method: "POST" }, token),
  listSuiteRuns: (token: string, id: Id) => request<SuiteRun[]>(`/test-suites/${id}/runs`, {}, token),
  exportSuite: (token: string, id: Id) => request<{ state: string; version_ids: number[] }>(`/test-suites/${id}/export`, { method: "POST" }, token),
  getSuiteRun: (token: string, id: Id) => request<SuiteRun>(`/suite-runs/${id}`, {}, token),
  listRunJobs: (token: string, id: Id) => request<RunJobSummary[]>(`/suite-runs/${id}/jobs`, {}, token),
  getRunJob: (token: string, id: Id) => request<RunJob>(`/run-jobs/${id}`, {}, token),
  listRunResults: (token: string, query: URLSearchParams) => request<RunResultPage>(`/run-results?${query.toString()}`, {}, token),
  cancelRunJob: (token: string, id: Id) => request<RunJob>(`/run-jobs/${id}/cancel`, { method: "POST" }, token),
  listAudit: (token: string, query: URLSearchParams) => request<AuditLog[]>(`/audit-logs?${query.toString()}`, {}, token),
};
