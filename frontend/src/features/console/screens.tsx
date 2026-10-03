"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { FormEvent, Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import {
  api,
  ApiError,
  AuditLog,
  DangerLevel,
  RagSearchFilters,
  RagSearchHit,
  Review,
  ReviewDecision,
  RunJob,
  RunJobSummary,
  RunResultListItem,
  RunVerdict,
  SuiteRun,
  TestCase,
  TestCaseVersion,
  TestSuite,
  VersionPayload,
  VersionStatus,
  WorkSummary,
} from "@/lib/api";
import { labels, useSession } from "@/shared/auth/session-context";
import {
  BackLink,
  DangerBadge,
  downloadBlob,
  Empty,
  ErrorNotice,
  formatDate,
  Loading,
  StatusBadge,
  SuccessNotice,
} from "@/shared/ui/components";
import { Icon } from "@/shared/ui/icons";
import { useConfirm } from "@/shared/ui/modal";
import { carlaStatus, useCarlaDemo } from "@/features/settings/carla-demo";

import { actionGroups, actionLabel, actionTone, auditChanges, auditEntityTypes, entityTypeLabel } from "./audit-format";

type LoadState<T> = { data: T | null; loading: boolean; error: string };
const emptyLoad = <T,>(): LoadState<T> => ({
  data: null,
  loading: true,
  error: "",
});
const DEFAULT_VERSION: VersionPayload = {
  map_code: "",
  ego_vehicle_code: "",
  adversary_type: "",
  environment_code: "",
  danger_level: "MEDIUM",
  scenario_input: {},
  tag_names: [],
  change_note: "",
};

const PAGE_SIZE = 20;

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}
function readPage(params: URLSearchParams) {
  return Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
}
function parseTags(text: string) {
  return text.split(",").map((tag) => tag.trim()).filter(Boolean);
}
/** Returns a guard that is true only for the most recent request, so slow responses cannot overwrite newer data. */
function useLatestRequest() {
  const counter = useRef(0);
  return useCallback(() => {
    const id = ++counter.current;
    return () => id === counter.current;
  }, []);
}
function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="button" onClick={onClick}>
      <Icon name="refresh" size={14} />
      Làm mới
    </button>
  );
}
function pageTitle(
  title: string,
  description: string,
  actions?: React.ReactNode,
) {
  return (
    <section className="heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </section>
  );
}
function isOwner(version: TestCaseVersion, userId: number) {
  return version.created_by === userId;
}
function metadataRows(version: TestCaseVersion) {
  return [
    ["Bản đồ", version.map_code],
    ["Xe ego", version.ego_vehicle_code],
    ["Tác nhân", version.adversary_type],
    ["Môi trường", version.environment_code],
    ["Mức nguy hiểm", labels.danger[version.danger_level]],
  ];
}

export function DashboardScreen() {
  const { session, can } = useSession();
  const carla = useCarlaDemo(session?.active_project?.id);
  const [counts, setCounts] = useState<{ total: number; approved: number; inReview: number } | null>(null);
  const [reviewCount, setReviewCount] = useState<number | null>(null);
  const [suiteCount, setSuiteCount] = useState<number | null>(null);
  const [work, setWork] = useState<WorkSummary | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!session) return;
    setError("");
    const countCases = (status?: VersionStatus) =>
      api
        .listCases(
          session.access_token,
          new URLSearchParams(status ? { status, page_size: "1" } : { page_size: "1" }),
        )
        .then((page) => page.total);
    try {
      const [total, approved, inReview, reviews, suites, summary] = await Promise.all([
        countCases(),
        countCases("APPROVED"),
        countCases("IN_REVIEW"),
        can("review:read")
          ? api.listReviews(session.access_token)
          : Promise.resolve(null),
        api.listSuites(session.access_token),
        api.workSummary(session.access_token),
      ]);
      setCounts({ total, approved, inReview });
      setReviewCount(reviews?.length ?? null);
      setSuiteCount(suites.length);
      setWork(summary);
    } catch (reason) {
      setError(errorText(reason, "Không thể tải số liệu tổng quan."));
    }
  }, [can, session]);
  useEffect(() => {
    void load();
  }, [load]);
  const followsReviews = can("review:read") || can("testcase:submit_review");
  const mine = work?.scope === "mine";
  const status = carlaStatus(carla.state);
  return (
    <main className="main">
      {pageTitle(
        "Tổng quan vận hành",
        "Theo dõi trạng thái catalog, review và bộ kiểm thử từ dữ liệu Backend.",
        <>
          {can("testcase:create") ? (
            <Link className="button primary" href="/test-cases/new"><Icon name="plus" size={14} />Tạo kịch bản</Link>
          ) : can("review:read") ? (
            <Link className="button primary" href="/reviews">Mở hàng đợi duyệt</Link>
          ) : null}
          <RefreshButton onClick={() => void load()} />
        </>,
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="stats stats-four">
        <Stat
          label="Kịch bản trong catalog"
          value={counts?.total ?? "—"}
          detail="Test case chưa lưu trữ"
        />
        <Stat
          label="Đã phê duyệt"
          value={counts?.approved ?? "—"}
          detail="Có thể thêm vào Test Suite"
        />
        <Stat
          label="Đang chờ duyệt"
          value={reviewCount ?? counts?.inReview ?? "—"}
          detail="Review request đang mở"
        />
        <Stat
          label="Bộ kiểm thử"
          value={suiteCount ?? "—"}
          detail="Version được pin theo suite"
        />
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Việc cần xử lý</div>
            <div className="panel-subtitle">
              {mine ? "Số chờ duyệt và cần chỉnh sửa chỉ tính kịch bản của bạn." : "Tính trên toàn Project."} Bấm vào thẻ để mở đúng màn.
            </div>
          </div>
        </div>
        <div className="workflow-grid">
          <Link href={followsReviews ? "/reviews" : "/test-cases"} className="workflow-card work-card">
            <span>Chờ Reviewer quyết định</span>
            <strong className="work-value">{work?.pending_review ?? "—"}</strong>
            <span>Mở hàng đợi duyệt →</span>
          </Link>
          <Link href={followsReviews ? "/reviews" : "/test-cases?status=EDIT"} className="workflow-card work-card">
            <span>Cần chỉnh sửa</span>
            <strong className="work-value">{work?.needs_changes ?? "—"}</strong>
            <span>Xem lịch sử quyết định →</span>
          </Link>
          <Link href="/test-cases?status=APPROVED" className="workflow-card work-card">
            <span>Đã duyệt, chưa vào bộ kiểm thử</span>
            <strong className="work-value">{work?.approved_not_in_suite ?? "—"}</strong>
            <span>Chọn bản đã duyệt →</span>
          </Link>
          <Link href="/run-results" className="workflow-card work-card">
            <span>Trong bộ kiểm thử, chưa có kết quả chạy</span>
            <strong className="work-value">{work?.in_suite_without_result ?? "—"}</strong>
            <span>Xem kết quả chạy →</span>
          </Link>
        </div>
      </section>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Đi đến công việc</div>
              <div className="panel-subtitle">
                Mỗi bước giữ nguyên liên kết Test Case → Version → Review → Suite → Run.
              </div>
            </div>
          </div>
          <div className="flow-list">
            {[
              { step: "01", title: "Tạo bản nháp và gửi duyệt", href: "/test-cases/new", show: can("testcase:create") },
              { step: "02", title: "Chạy CARLA và xem kết quả", href: "/run-results", show: true },
              { step: "03", title: "Review bằng chứng", href: "/reviews", show: followsReviews },
              { step: "04", title: "Tìm bản đã duyệt", href: "/test-cases?status=APPROVED", show: true },
              { step: "05", title: "Bộ kiểm thử hồi quy", href: "/suites", show: true },
            ].filter((item) => item.show).map((item) => (
              <Link key={item.step} href={item.href} className="flow-step">
                <span className="flow-index">{item.step}</span>
                <strong>{item.title}</strong>
                <Icon name="arrowRight" />
              </Link>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Môi trường CARLA</div>
              <div className="panel-subtitle">Bản demo giao diện</div>
            </div>
            <span className={`pill carla-pill-${status.tone}`}>{status.label}</span>
          </div>
          <div className="setup-body">
            <p className="muted">
              {carla.state.source === "default"
                ? "Đang dùng dữ liệu mặc định cho luồng demo."
                : carla.state.stage === "ready"
                  ? `CARLA đã kết nối và catalog đã đồng bộ lúc ${formatDate(carla.state.lastSync)}.`
                  : "Chưa có môi trường CARLA sẵn sàng để tạo hoặc chạy scenario mới."}
            </p>
            <Link className="button" href="/settings">Mở cài đặt CARLA</Link>
          </div>
        </section>
      </div>
    </main>
  );
}

function Stat({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-delta">{detail}</div>
    </div>
  );
}

type FilterValues = {
  q: string;
  map_code: string;
  adversary_type: string;
  environment_code: string;
  danger_level: string;
  status: string;
  creator_id: string;
  tag: string;
};
function readFilters(params: URLSearchParams): FilterValues {
  return {
    q: params.get("q") ?? "",
    map_code: params.get("map_code") ?? "",
    adversary_type: params.get("adversary_type") ?? "",
    environment_code: params.get("environment_code") ?? "",
    danger_level: params.get("danger_level") ?? "",
    status: params.get("status") ?? "",
    creator_id: params.get("creator_id") ?? "",
    tag: params.get("tag") ?? "",
  };
}
function asRagSearchFilters(filters: FilterValues): RagSearchFilters {
  return Object.fromEntries(
    Object.entries(filters)
      .filter(
        ([key, value]) => key !== "q" && key !== "creator_id" && Boolean(value),
      )
      .map(([key, value]) => [key, [value]]),
  ) as RagSearchFilters;
}

export function TestCaseListScreen() {
  const { session } = useSession();
  const isLatest = useLatestRequest();
  const router = useRouter();
  const urlParams = useSearchParams();
  const filterQuery = urlParams.toString();
  const filters = useMemo(
    () => readFilters(new URLSearchParams(filterQuery)),
    [filterQuery],
  );
  const [draft, setDraft] = useState(filters);
  const [state, setState] =
    useState<LoadState<TestCase[] | SearchCase[]>>(emptyLoad());
  const [ragResults, setRagResults] = useState<RagSearchHit[]>([]);
  const [interpreted, setInterpreted] = useState<RagSearchFilters | null>(null);
  const [mode, setMode] = useState("");
  const [fallback, setFallback] = useState(false);
  const page = readPage(urlParams);
  const [total, setTotal] = useState(0);
  useEffect(() => {
    setDraft(filters);
  }, [filters]);
  const load = useCallback(async () => {
    if (!session) return;
    const current = isLatest();
    setState((previous) => ({ ...previous, loading: true, error: "" }));
    try {
      if (filters.q) {
        const response = await api.ragSearch(session.access_token, {
          prompt: filters.q,
          filters: asRagSearchFilters(filters),
          limit: 50,
        });
        if (!current()) return;
        setState({ data: response.items, loading: false, error: "" });
        setRagResults(response.items);
        setInterpreted(response.interpreted_filters);
        setMode(response.retrieval_mode);
        setTotal(response.total);
        setFallback(response.semantic_fallback);
      } else {
        const query = new URLSearchParams();
        Object.entries(filters).forEach(([key, value]) => {
          if (key !== "q" && value) query.set(key, value);
        });
        query.set("page", String(page));
        query.set("page_size", String(PAGE_SIZE));
        const response = await api.listCases(session.access_token, query);
        if (!current()) return;
        setState({ data: response.items, loading: false, error: "" });
        setRagResults([]);
        setInterpreted(null);
        setMode("");
        setTotal(response.total);
        setFallback(false);
      }
    } catch (reason) {
      if (!current()) return;
      setRagResults([]);
      setInterpreted(null);
      setTotal(0);
      setState({
        data: [],
        loading: false,
        error: errorText(reason, "Không thể tải catalog test case."),
      });
    }
  }, [filters, isLatest, page, session]);
  useEffect(() => {
    void load();
  }, [load]);
  function apply(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams();
    Object.entries(draft).forEach(([key, value]) => {
      if (value.trim()) next.set(key, value.trim());
    });
    router.push(`/test-cases?${next.toString()}`);
  }
  function clear() {
    setDraft({
      q: "",
      map_code: "",
      adversary_type: "",
      environment_code: "",
      danger_level: "",
      status: "",
      creator_id: "",
      tag: "",
    });
    router.push("/test-cases");
  }
  const items = filters.q ? ragResults : (state.data ?? []);
  return (
    <main className="main">
      {pageTitle(
        "Danh mục kịch bản kiểm thử",
        "Lọc chính xác theo metadata trước; khi nhập mô tả, Backend thực hiện hybrid retrieval và vẫn giữ filter là nguồn sự thật.",
      )}
      <section className="panel">
        <form className="filters filters-grid" onSubmit={apply}>
          <input
            className="search filter-wide"
            value={draft.q}
            onChange={(event) => setDraft({ ...draft, q: event.target.value })}
            placeholder="Mô tả tình huống cần tìm, ví dụ: người đi bộ băng qua đường khi mưa"
          />
          <input
            className="filter"
            value={draft.map_code}
            onChange={(event) =>
              setDraft({ ...draft, map_code: event.target.value })
            }
            placeholder="Map, ví dụ Town05"
          />
          <input
            className="filter"
            value={draft.adversary_type}
            onChange={(event) =>
              setDraft({ ...draft, adversary_type: event.target.value })
            }
            placeholder="Tác nhân, ví dụ pedestrian"
          />
          <input
            className="filter"
            value={draft.environment_code}
            onChange={(event) =>
              setDraft({ ...draft, environment_code: event.target.value })
            }
            placeholder="Môi trường, ví dụ heavy_rain"
          />
          <select
            className="filter"
            value={draft.danger_level}
            onChange={(event) =>
              setDraft({ ...draft, danger_level: event.target.value })
            }
          >
            <option value="">Mọi mức nguy hiểm</option>
            {Object.entries(labels.danger).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            className="filter"
            value={draft.status}
            onChange={(event) =>
              setDraft({ ...draft, status: event.target.value })
            }
          >
            <option value="">Mọi trạng thái</option>
            {Object.entries(labels.status).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <input
            className="filter"
            type="number"
            min={1}
            inputMode="numeric"
            value={draft.creator_id}
            onChange={(event) =>
              setDraft({ ...draft, creator_id: event.target.value })
            }
            placeholder="ID người tạo (số)"
          />
          <input
            className="filter"
            value={draft.tag}
            onChange={(event) =>
              setDraft({ ...draft, tag: event.target.value })
            }
            placeholder="Thẻ"
          />
          <div className="filter-actions">
            <button className="button" type="button" onClick={clear}>
              Xóa bộ lọc
            </button>
            <button className="button primary" type="submit">
              Tìm kiếm
            </button>
          </div>
        </form>
        {filters.q && interpreted && (
          <RagSearchInsight filters={interpreted} mode={mode} />
        )}
        {fallback && (
          <div className="notice notice-info">
            Semantic embedding chưa có API key nên đang dùng keyword + taxonomy
            trên dữ liệu PostgreSQL.
          </div>
        )}
        {state.error && <ErrorNotice>{state.error}</ErrorNotice>}
        <div className="panel-header">
          <div>
            <div className="panel-title">{total} kết quả</div>
            <div className="panel-subtitle">
              Filter và query được lưu trên URL để có thể tải lại hoặc chia sẻ.
            </div>
          </div>
        </div>
        <CaseTable
          items={items}
          loading={state.loading}
          searchMode={Boolean(filters.q)}
        />
        {!filters.q && (
          <Pagination
            page={page}
            total={total}
            onPage={(next) =>
              router.push(
                `/test-cases?${new URLSearchParams({ ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value)), page: String(next) }).toString()}`,
              )
            }
          />
        )}
      </section>
    </main>
  );
}

function Pagination({
  page,
  total,
  onPage,
}: {
  page: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="pagination">
      <button
        className="button"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        ← Trang trước
      </button>
      <span>
        Trang {page} / {pages}
      </span>
      <button
        className="button"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        Trang sau →
      </button>
    </div>
  );
}

type SearchCase = {
  case_id: number;
  case_key: string;
  title: string;
  version_id: number;
  version_no: number;
  status: VersionStatus;
  map_code: string;
  adversary_type: string;
  environment_code: string;
  danger_level: DangerLevel;
  tags: string[];
  score: number | null;
  matched_by: string[];
  match_reasons?: string[];
  simulation?: {
    verdict: RunVerdict;
    collision: boolean;
    min_ttc_seconds: number | null;
    duration_ms: number | null;
  } | null;
};
function RagSearchInsight({
  filters,
  mode,
}: {
  filters: RagSearchFilters;
  mode: string;
}) {
  const entries = [
    ["Map", filters.map_code?.join(", ")],
    ["Tác nhân", filters.adversary_type?.join(", ")],
    ["Môi trường", filters.environment_code?.join(", ")],
    ["Nguy hiểm", filters.danger_level?.join(", ")],
    ["Trạng thái", filters.status?.join(", ")],
    ["Kết quả", filters.verdict?.join(", ")],
    ["Collision", filters.collision_only ? "Có" : "—"],
  ].filter(([, value]) => Boolean(value));
  return (
    <div className="notice notice-info">
      <b>Hệ thống đã hiểu prompt</b>
      <div className="metadata-summary">
        {entries.map(([label, value]) => (
          <span key={label}>
            <b>{label}:</b> {value}
          </span>
        ))}
      </div>
      <span className="case-key">
        Nguồn: PostgreSQL · Retrieval: {mode}
      </span>
    </div>
  );
}
function CaseTable({
  items,
  loading,
  searchMode,
}: {
  items: (TestCase | SearchCase)[];
  loading: boolean;
  searchMode: boolean;
}) {
  if (loading) return <Loading />;
  if (!items.length)
    return (
      <Empty>
        Chưa có test case thỏa điều kiện. Bạn có thể đổi prompt hoặc tạo một bản
        nháp mới.
      </Empty>
    );
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Kịch bản</th>
            <th>Map / môi trường</th>
            <th>Tác nhân</th>
            <th>Nguy hiểm</th>
            <th>Trạng thái</th>
            {searchMode && <th>Kết quả CARLA</th>}
            <th>Khớp / thẻ</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const isSearch = "case_id" in item;
            const result = isSearch ? item : null;
            const simulation = result?.simulation;
            const version = isSearch ? item : item.latest_version;
            const caseId = isSearch ? item.case_id : item.id;
            const versionNo = isSearch ? item.version_no : version?.version_no;
            return (
              <tr key={`${caseId}-${versionNo ?? "empty"}`}>
                <td>
                  <Link
                    className="case-title link"
                    href={`/test-cases/${caseId}`}
                  >
                    {item.title}
                  </Link>
                  <div className="case-key">
                    {item.case_key}
                    {versionNo ? ` · v${versionNo}` : " · chưa có version"}
                    {isSearch && item.score !== null
                      ? ` · xếp hạng ${item.score.toFixed(2)}`
                      : ""}
                  </div>
                  {isSearch && (
                    <Link
                      className="case-key link"
                      href={`/test-cases/${caseId}/versions/${item.version_id}`}
                    >
                      Xem version
                    </Link>
                  )}
                </td>
                <td>
                  {version ? (
                    <>
                      <div>{version.map_code}</div>
                      <div className="muted">{version.environment_code}</div>
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{version?.adversary_type ?? "—"}</td>
                <td>
                  {version ? <DangerBadge level={version.danger_level} /> : "—"}
                </td>
                <td>
                  {version ? <StatusBadge status={version.status} /> : "—"}
                </td>
                {searchMode && (
                  <td>
                    {simulation ? (
                      <>
                        <span className={`verdict ${simulation.verdict}`}>
                          {labels.verdict[simulation.verdict]}
                        </span>
                        <div className="case-key">
                          {simulation.collision
                            ? "Collision"
                            : "Không collision"}{" "}
                          · TTC {simulation.min_ttc_seconds ?? "—"}s
                        </div>
                      </>
                    ) : (
                      "Chưa có run"
                    )}
                  </td>
                )}
                <td>
                  {version?.tags.map((tag) => (
                    <span className="tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                  {searchMode && isSearch && (
                    <div className="match-note">
                      {result?.match_reasons?.join(" · ") ??
                        result?.matched_by.join(" + ")}
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

type ResultFilterValues = { q: string; verdict: string };
const ALL_RESULT_VERSION_STATUSES: VersionStatus[] = [
  "DRAFT",
  "IN_REVIEW",
  "EDIT",
  "APPROVED",
  "REJECTED",
];
type ResultSearchRow = RunResultListItem & {
  score?: number | null;
  match_reasons?: string[];
};

function readResultFilters(params: URLSearchParams): ResultFilterValues {
  return { q: params.get("q") ?? "", verdict: params.get("verdict") ?? "" };
}

function resultRowFromRag(hit: RagSearchHit): ResultSearchRow | null {
  const simulation = hit.simulation;
  if (!simulation?.run_result_id || !simulation.run_job_id) return null;
  return {
    id: simulation.run_result_id,
    run_job_id: simulation.run_job_id,
    test_case_version_id: hit.version_id,
    test_case_id: hit.case_id,
    case_key: hit.case_key,
    title: hit.title,
    version_no: hit.version_no,
    map_code: hit.map_code,
    adversary_type: hit.adversary_type,
    environment_code: hit.environment_code,
    verdict: simulation.verdict,
    metrics: simulation.metrics,
    scenario_runner_exit_code: null,
    duration_ms: simulation.duration_ms,
    collision: simulation.collision,
    min_ttc_seconds: simulation.min_ttc_seconds,
    created_at: "",
    score: hit.score,
    match_reasons: hit.match_reasons,
  };
}

export function RunResultsScreen() {
  const { session } = useSession();
  const isLatest = useLatestRequest();
  const router = useRouter();
  const urlParams = useSearchParams();
  const filterQuery = urlParams.toString();
  const filters = useMemo(
    () => readResultFilters(new URLSearchParams(filterQuery)),
    [filterQuery],
  );
  const [draft, setDraft] = useState(filters);
  const [state, setState] = useState<LoadState<ResultSearchRow[]>>(emptyLoad());
  const [interpreted, setInterpreted] = useState<RagSearchFilters | null>(null);
  const [mode, setMode] = useState("");
  const [fallback, setFallback] = useState(false);
  const [total, setTotal] = useState(0);
  const page = readPage(urlParams);

  useEffect(() => {
    setDraft(filters);
  }, [filters]);

  const load = useCallback(async () => {
    if (!session) return;
    const current = isLatest();
    setState((previous) => ({ ...previous, loading: true, error: "" }));
    try {
      if (filters.q) {
        const response = await api.ragSearch(session.access_token, {
          prompt: filters.q,
          filters: {
            status: ALL_RESULT_VERSION_STATUSES,
            ...(filters.verdict ? { verdict: [filters.verdict] } : {}),
          },
          limit: 50,
        });
        if (!current()) return;
        const items = response.items
          .map(resultRowFromRag)
          .filter((item): item is ResultSearchRow => item !== null);
        setState({ data: items, loading: false, error: "" });
        setInterpreted(response.interpreted_filters);
        setMode(response.retrieval_mode);
        setFallback(response.semantic_fallback);
        setTotal(items.length);
      } else {
        const query = new URLSearchParams({
          page: String(page),
          page_size: String(PAGE_SIZE),
        });
        if (filters.verdict) query.set("verdict", filters.verdict);
        const response = await api.listRunResults(session.access_token, query);
        if (!current()) return;
        setState({ data: response.items, loading: false, error: "" });
        setInterpreted(null);
        setMode("");
        setFallback(false);
        setTotal(response.total);
      }
    } catch (reason) {
      if (!current()) return;
      setTotal(0);
      setState({
        data: [],
        loading: false,
        error: errorText(reason, "Không thể tải kết quả chạy CARLA."),
      });
    }
  }, [filters, isLatest, page, session]);

  useEffect(() => {
    void load();
  }, [load]);

  function apply(event: FormEvent) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (draft.q.trim()) next.set("q", draft.q.trim());
    if (draft.verdict) next.set("verdict", draft.verdict);
    router.push(`/run-results?${next.toString()}`);
  }

  function clear() {
    setDraft({ q: "", verdict: "" });
    router.push("/run-results");
  }

  const items = state.data ?? [];
  return (
    <main className="main">
      {pageTitle(
        "Kết quả chạy CARLA",
        "Theo dõi kết quả mô phỏng đã lưu. Nhập mô tả để RAG tìm theo ngữ cảnh scenario và metrics CARLA.",
      )}
      <section className="panel">
        <form className="filters" onSubmit={apply}>
          <input
            className="search filter-wide"
            value={draft.q}
            onChange={(event) => setDraft({ ...draft, q: event.target.value })}
            placeholder="Ví dụ: tìm xe máy tạt đầu khi mưa ở Town03 đã va chạm"
          />
          <select
            className="filter"
            value={draft.verdict}
            onChange={(event) => setDraft({ ...draft, verdict: event.target.value })}
          >
            <option value="">Mọi kết quả</option>
            {Object.entries(labels.verdict).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <button className="button primary" type="submit">Tìm kết quả</button>
          <button className="button" type="button" onClick={clear}>Xóa bộ lọc</button>
        </form>
        {filters.q && interpreted && <RagSearchInsight filters={interpreted} mode={mode} />}
        {filters.q && fallback && (
          <div className="notice notice-info">
            Semantic embedding chưa có API key nên RAG đang dùng keyword + taxonomy trên PostgreSQL.
          </div>
        )}
        {state.error && <ErrorNotice>{state.error}</ErrorNotice>}
        <div className="panel-header">
          <div>
            <div className="panel-title">{total} kết quả chạy</div>
            <div className="panel-subtitle">
              Mỗi dòng gắn với một version kịch bản và kết quả worker/ScenarioRunner đã lưu.
            </div>
          </div>
          <RefreshButton onClick={() => void load()} />
        </div>
        <RunResultsTable items={items} loading={state.loading} searchMode={Boolean(filters.q)} />
        {!filters.q && (
          <Pagination
            page={page}
            total={total}
            onPage={(next) =>
              router.push(
                `/run-results?${new URLSearchParams({ ...(filters.verdict ? { verdict: filters.verdict } : {}), page: String(next) }).toString()}`,
              )
            }
          />
        )}
      </section>
    </main>
  );
}

function RunResultsTable({
  items,
  loading,
  searchMode,
}: {
  items: ResultSearchRow[];
  loading: boolean;
  searchMode: boolean;
}) {
  if (loading) return <Loading text="Đang tải kết quả chạy CARLA…" />;
  if (!items.length) {
    return <Empty>Chưa có kết quả chạy phù hợp với điều kiện hoặc prompt.</Empty>;
  }
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Kịch bản / version</th>
            <th>Map / môi trường</th>
            <th>Kết quả CARLA</th>
            <th>Collision / TTC</th>
            <th>Thời lượng</th>
            {searchMode && <th>Lý do khớp</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                <Link className="case-title link" href={`/test-cases/${item.test_case_id}`}>
                  {item.title}
                </Link>
                <div className="case-key">{item.case_key} · v{item.version_no}</div>
                <Link className="case-key link" href={`/test-cases/${item.test_case_id}/versions/${item.test_case_version_id}`}>
                  Xem version
                </Link>
              </td>
              <td>
                <div>{item.map_code}</div>
                <div className="muted">{item.environment_code} · {item.adversary_type}</div>
              </td>
              <td>
                <span className={`verdict ${item.verdict}`}>{labels.verdict[item.verdict]}</span>
                <div className="case-key">Exit code: {item.scenario_runner_exit_code ?? "—"}</div>
              </td>
              <td>
                <div>{item.collision ? "Có collision" : "Không collision"}</div>
                <div className="case-key">TTC {item.min_ttc_seconds ?? "—"}s</div>
              </td>
              <td>
                <div>{item.duration_ms === null ? "—" : `${(item.duration_ms / 1000).toFixed(2)}s`}</div>
                {item.created_at && <div className="case-key">{formatDate(item.created_at)}</div>}
              </td>
              {searchMode && (
                <td>
                  <div className="match-note">{item.match_reasons?.join(" · ") ?? "—"}</div>
                  {item.score !== undefined && item.score !== null && <div className="case-key">Xếp hạng {item.score.toFixed(2)}</div>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function NewTestCaseScreen() {
  const { session, can } = useSession();
  const router = useRouter();
  // Keep partial progress so a retry after a failed step does not create duplicate test cases.
  const [createdCaseId, setCreatedCaseId] = useState<number | null>(null);
  const [createdVersionId, setCreatedVersionId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [version, setVersion] = useState<VersionPayload>(DEFAULT_VERSION);
  const [tagsText, setTagsText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      let caseId = createdCaseId;
      if (caseId === null) {
        caseId = (await api.createCase(session.access_token, title, description)).id;
        setCreatedCaseId(caseId);
      }
      let versionId = createdVersionId;
      if (versionId === null) {
        versionId = (
          await api.createVersion(session.access_token, caseId, {
            ...version,
            tag_names: parseTags(tagsText),
          })
        ).id;
        setCreatedVersionId(versionId);
      }
      if (file) {
        try {
          await api.uploadXosc(session.access_token, versionId, file);
        } catch (reason) {
          // Case and Draft already exist: continue on the version page where the upload can be retried.
          router.push(`/test-cases/${caseId}/versions/${versionId}`);
          throw reason;
        }
      }
      router.push(`/test-cases/${caseId}`);
    } catch (reason) {
      setError(errorText(reason, "Không thể tạo Test Case và Draft version."));
    } finally {
      setBusy(false);
    }
  }
  if (!can("testcase:create"))
    return (
      <main className="main">
        {pageTitle("Tạo test case mới", "Chỉ Người tạo (Creator) mới có quyền tạo test case.")}
        <ErrorNotice>Bạn không có quyền tạo test case trong Project này.</ErrorNotice>
      </main>
    );
  return (
    <main className="main">
      {pageTitle(
        "Tạo test case mới",
        "Tạo một logical container và version 1 ở trạng thái DRAFT. Cần đủ 5 metadata cùng tệp XOSC trước khi gửi review.",
      )}
      <BackLink href="/test-cases">Danh mục kịch bản</BackLink>
      {notice && <SuccessNotice>{notice}</SuccessNotice>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <form className="panel form-panel" onSubmit={submit}>
        <div className="panel-header">
          <div>
            <div className="panel-title">Thông tin Test Case</div>
            <div className="panel-subtitle">
              Tiêu đề/mô tả thuộc logical test case; metadata mô phỏng thuộc
              version.
            </div>
          </div>
        </div>
        <div className="form form-columns">
          <label className="field field-full">
            Tiêu đề
            <input
              required
              disabled={createdCaseId !== null}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Người đi bộ băng qua đường khi mưa lớn"
            />
          </label>
          <label className="field field-full">
            Mô tả
            <textarea
              disabled={createdCaseId !== null}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Mô tả hành vi nguy hiểm, điều kiện kích hoạt và mục tiêu kiểm thử."
            />
          </label>
        </div>
        <VersionFields
          value={version}
          onChange={setVersion}
          tagsText={tagsText}
          onTagsChange={setTagsText}
          disabled={createdVersionId !== null}
        />
        <div className="upload-box">
          <label className="field">
            Tệp OpenSCENARIO (.xosc)
            <input
              type="file"
              accept=".xosc,application/xml,text/xml"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <span className="field-help">
              Tệp được upload qua Backend và lưu bằng artifact immutable. Không
              thể gửi review nếu chưa có XOSC.
            </span>
          </label>
        </div>
        <div className="form-actions">
          <Link className="button" href="/test-cases">
            Hủy
          </Link>
          <button className="button primary" disabled={busy}>
            {busy ? "Đang tạo…" : "Tạo Test Case và Draft v1"}
          </button>
        </div>
      </form>
    </main>
  );
}

function VersionFields({
  value,
  onChange,
  tagsText,
  onTagsChange,
  disabled = false,
}: {
  value: VersionPayload;
  onChange: (value: VersionPayload) => void;
  tagsText: string;
  onTagsChange: (value: string) => void;
  disabled?: boolean;
}) {
  const patch = (key: keyof VersionPayload, next: string) =>
    onChange({ ...value, [key]: next });
  return (
    <div className="form form-columns metadata-form">
      <div className="section-label field-full">
        5 metadata bắt buộc của version
      </div>
      <label className="field">
        Map code
        <input
          required
          disabled={disabled}
          value={value.map_code}
          onChange={(event) => patch("map_code", event.target.value)}
          placeholder="Town05"
        />
      </label>
      <label className="field">
        Ego vehicle code
        <input
          required
          disabled={disabled}
          value={value.ego_vehicle_code}
          onChange={(event) => patch("ego_vehicle_code", event.target.value)}
          placeholder="vehicle.tesla.model3"
        />
      </label>
      <label className="field">
        Adversary type
        <input
          required
          disabled={disabled}
          value={value.adversary_type}
          onChange={(event) => patch("adversary_type", event.target.value)}
          placeholder="pedestrian"
        />
      </label>
      <label className="field">
        Environment code
        <input
          required
          disabled={disabled}
          value={value.environment_code}
          onChange={(event) => patch("environment_code", event.target.value)}
          placeholder="heavy_rain"
        />
      </label>
      <label className="field">
        Danger level
        <select
          disabled={disabled}
          value={value.danger_level}
          onChange={(event) => patch("danger_level", event.target.value)}
        >
          {Object.entries(labels.danger).map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        Thẻ (cách nhau bởi dấu phẩy)
        <input
          disabled={disabled}
          value={tagsText}
          onChange={(event) => onTagsChange(event.target.value)}
          placeholder="pedestrian, crossing, rain"
        />
      </label>
      <label className="field field-full">
        Ghi chú thay đổi
        <textarea
          disabled={disabled}
          value={value.change_note ?? ""}
          onChange={(event) => patch("change_note", event.target.value)}
          placeholder="Lý do tạo hoặc sửa version này"
        />
      </label>
    </div>
  );
}

export function TestCaseDetailScreen() {
  const { session, can } = useSession();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [state, setState] =
    useState<LoadState<{ testCase: TestCase; versions: TestCaseVersion[] }>>(
      emptyLoad(),
    );
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!session) return;
    setState((old) => ({ ...old, loading: old.data === null, error: "" }));
    try {
      const [testCase, versions] = await Promise.all([
        api.getCase(session.access_token, params.id),
        api.listVersions(session.access_token, params.id),
      ]);
      setState({ data: { testCase, versions }, loading: false, error: "" });
    } catch (reason) {
      setState({
        data: null,
        loading: false,
        error: errorText(reason, "Không thể tải chi tiết Test Case."),
      });
    }
  }, [params.id, session]);
  useEffect(() => {
    void load();
  }, [load]);
  async function clone(versionId: number) {
    if (!session) return;
    setError("");
    try {
      const next = await api.cloneVersion(session.access_token, versionId);
      setMessage(`Đã tạo Draft v${next.version_no} từ version trước.`);
      await load();
      router.push(`/test-cases/${params.id}/versions/${next.id}`);
    } catch (reason) {
      setError(errorText(reason, "Không thể clone version."));
    }
  }
  if (state.loading)
    return (
      <main className="main">
        <Loading />
      </main>
    );
  if (!state.data)
    return (
      <main className="main">
        <BackLink href="/test-cases" />{" "}
        <ErrorNotice>{state.error || "Không tìm thấy Test Case."}</ErrorNotice>
      </main>
    );
  const { testCase, versions } = state.data;
  return (
    <main className="main">
      {pageTitle(
        testCase.title,
        testCase.description ?? "Chưa có mô tả.",
      )}
      <BackLink href="/test-cases">Danh mục kịch bản</BackLink>
      {message && <SuccessNotice>{message}</SuccessNotice>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Thông tin logic</div>
              <div className="panel-subtitle">
                Mã test case: {testCase.case_key}
              </div>
            </div>
          </div>
          <dl className="details">
            <dt>Người tạo</dt>
            <dd>
              {testCase.created_by === session?.user.id
                ? "Bạn"
                : testCase.created_by}
            </dd>
            <dt>Tạo lúc</dt>
            <dd>{formatDate(testCase.created_at)}</dd>
            <dt>Cập nhật</dt>
            <dd>{formatDate(testCase.updated_at)}</dd>
          </dl>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Nguyên tắc versioning</div>
              <div className="panel-subtitle">
                Version đã gửi review/approved/rejected là immutable; thay đổi
                phải clone thành Draft mới.
              </div>
            </div>
          </div>
          <div className="inline-note">
            Test Suite và Run Result luôn pin chính xác `test_case_version_id`,
            không dùng “latest” di động.
          </div>
        </div>
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Lịch sử version</div>
            <div className="panel-subtitle">
              Mỗi version chứa metadata, tags và liên kết XOSC riêng.
            </div>
          </div>
          <span className="muted">{versions.length} version</span>
        </div>
        {versions.length === 0 ? (
          <Empty>
            Test Case này chưa có version. Hãy tạo metadata mô phỏng đầu tiên.
          </Empty>
        ) : (
          <div className="timeline">
            {versions.map((version) => (
              <article className="version-card" key={version.id}>
                <div className="version-card-head">
                  <div>
                    <Link
                      className="version-title link"
                      href={`/test-cases/${testCase.id}/versions/${version.id}`}
                    >
                      Version {version.version_no}
                    </Link>
                    <div className="case-key">
                      Tạo {formatDate(version.created_at)} ·{" "}
                      {version.xosc_artifact_id ? "Đã có XOSC" : "Chưa có XOSC"}
                    </div>
                  </div>
                  <StatusBadge status={version.status} />
                </div>
                <div className="metadata-summary">
                  {metadataRows(version).map(([label, value]) => (
                    <span key={label}>
                      <b>{label}:</b> {value}
                    </span>
                  ))}
                </div>
                <div>
                  {version.tags.map((tag) => (
                    <span className="tag" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
                <div className="card-actions">
                  <Link
                    className="button"
                    href={`/test-cases/${testCase.id}/versions/${version.id}`}
                  >
                    Xem chi tiết
                  </Link>
                  {can("testcase:create") &&
                    isOwner(version, session?.user.id ?? 0) &&
                    version.status !== "DRAFT" && (
                      <button
                        className="button"
                        onClick={() => void clone(version.id)}
                      >
                        Clone thành Draft
                      </button>
                    )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

export function VersionDetailScreen() {
  const { session, can } = useSession();
  const params = useParams<{ id: string; versionId: string }>();
  const router = useRouter();
  const [state, setState] = useState<LoadState<TestCaseVersion>>(emptyLoad());
  const [form, setForm] = useState<VersionPayload>(DEFAULT_VERSION);
  const [tagsText, setTagsText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    if (!session) return;
    setState((old) => ({ ...old, loading: old.data === null, error: "" }));
    try {
      const version = await api.getVersion(
        session.access_token,
        params.versionId,
      );
      setState({ data: version, loading: false, error: "" });
      setForm({
        map_code: version.map_code,
        ego_vehicle_code: version.ego_vehicle_code,
        adversary_type: version.adversary_type,
        environment_code: version.environment_code,
        danger_level: version.danger_level,
        scenario_input: version.scenario_input,
        tag_names: version.tags,
        change_note: version.change_note,
      });
      setTagsText(version.tags.join(", "));
    } catch (reason) {
      setState({
        data: null,
        loading: false,
        error: errorText(reason, "Không thể tải version."),
      });
    }
  }, [params.versionId, session]);
  useEffect(() => {
    void load();
  }, [load]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!session || !state.data) return;
    setBusy(true);
    setError("");
    try {
      const payload = { ...form, tag_names: parseTags(tagsText) };
      const version = await api.updateVersion(
        session.access_token,
        state.data.id,
        payload,
      );
      if (file) await api.uploadXosc(session.access_token, version.id, file);
      setFile(null);
      setFileInputKey((key) => key + 1);
      setNotice("Đã lưu Draft version.");
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể cập nhật Draft version."));
    } finally {
      setBusy(false);
    }
  }
  async function submitReview() {
    if (!session || !state.data) return;
    const message = window.prompt("Lời nhắn gửi Reviewer (không bắt buộc):");
    if (message === null) return;
    setBusy(true);
    setError("");
    try {
      await api.submitReview(session.access_token, state.data.id, message);
      setNotice("Đã gửi version vào hàng đợi review.");
      await load();
    } catch (reason) {
      setError(
        errorText(
          reason,
          "Không thể gửi review. Kiểm tra 5 metadata và XOSC artifact.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  async function clone() {
    if (!session || !state.data) return;
    setBusy(true);
    setError("");
    try {
      const next = await api.cloneVersion(session.access_token, state.data.id);
      router.push(`/test-cases/${params.id}/versions/${next.id}`);
    } catch (reason) {
      setError(errorText(reason, "Không thể clone version."));
    } finally {
      setBusy(false);
    }
  }
  async function downloadXosc() {
    if (!session || !state.data) return;
    setError("");
    try {
      downloadBlob(
        await api.downloadXosc(session.access_token, state.data.id),
        `scenario-v${state.data.version_no}.xosc`,
      );
    } catch (reason) {
      setError(errorText(reason, "Không thể tải XOSC."));
    }
  }
  if (state.loading)
    return (
      <main className="main">
        <Loading />
      </main>
    );
  if (!state.data)
    return (
      <main className="main">
        <BackLink href={`/test-cases/${params.id}`} />{" "}
        <ErrorNotice>{state.error || "Không tìm thấy version."}</ErrorNotice>
      </main>
    );
  const version = state.data;
  const editable =
    version.status === "DRAFT" && isOwner(version, session?.user.id ?? 0);
  return (
    <main className="main">
      {pageTitle(
        `Version ${version.version_no}`,
        "Snapshot metadata của version này. Chỉ bản nháp do chính bạn tạo mới có thể chỉnh sửa.",
      )}
      <BackLink href={`/test-cases/${params.id}`}>Chi tiết Test Case</BackLink>
      {notice && <SuccessNotice>{notice}</SuccessNotice>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Trạng thái review</div>
              <div className="panel-subtitle">
                Tạo {formatDate(version.created_at)} · Gửi duyệt{" "}
                {formatDate(version.submitted_at)}
              </div>
            </div>
            <StatusBadge status={version.status} />
          </div>
          <dl className="details">
            <dt>XOSC artifact</dt>
            <dd>
              {version.xosc_artifact_id ? (
                <button
                  className="text-button"
                  onClick={() => void downloadXosc()}
                >
                  Tải tệp .xosc
                </button>
              ) : (
                "Chưa upload"
              )}
            </dd>
            <dt>Người quyết định</dt>
            <dd>{version.decided_by ?? "—"}</dd>
            <dt>Quy tắc</dt>
            <dd>Approved/Rejected không chỉnh tại chỗ.</dd>
          </dl>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Thao tác</div>
              <div className="panel-subtitle">
                Backend xác thực quyền và transition trước khi thay đổi.
              </div>
            </div>
          </div>
          <div className="stack-actions">
            {editable && (
              <button
                className="button primary"
                disabled={busy || !version.xosc_artifact_id}
                onClick={() => void submitReview()}
              >
                Gửi review
              </button>
            )}
            {version.status !== "DRAFT" &&
              can("testcase:create") &&
              isOwner(version, session?.user.id ?? 0) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void clone()}
                >
                  Clone thành Draft mới
                </button>
              )}
            {editable && !version.xosc_artifact_id && (
              <span className="muted">
                Cần upload XOSC trước khi gửi review.
              </span>
            )}
          </div>
        </div>
      </section>
      <form className="panel form-panel" onSubmit={save}>
        <div className="panel-header">
          <div>
            <div className="panel-title">Metadata version</div>
            <div className="panel-subtitle">
              Năm trường này là contract shared cho search/filter và approval
              workflow.
            </div>
          </div>
        </div>
        <VersionFields
          value={form}
          onChange={setForm}
          tagsText={tagsText}
          onTagsChange={setTagsText}
          disabled={!editable}
        />
        {editable && (
          <div className="upload-box">
            <label className="field">
              Thay / tải XOSC
              <input
                key={fileInputKey}
                type="file"
                accept=".xosc,application/xml,text/xml"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <span className="field-help">
                Upload tạo artifact/object key mới, không ghi đè XOSC cũ.
              </span>
            </label>
          </div>
        )}
        {editable && (
          <div className="form-actions">
            <button className="button primary" disabled={busy}>
              {busy ? "Đang lưu…" : "Lưu Draft"}
            </button>
          </div>
        )}
      </form>
    </main>
  );
}

const REVIEW_PAGE_SIZES = [25, 50, 100];

function reviewSearchText(review: Review) {
  return [review.case_key, review.title, review.requested_by_name, review.map_code, review.adversary_type, review.environment_code]
    .filter(Boolean).join(" ").toLowerCase();
}

function ReviewEvidenceBlock({ review }: { review: Review }) {
  const evidence = review.evidence;
  return <div className="review-evidence">
    <b>Bằng chứng chạy</b>
    {evidence ? <span>
      <span className={`verdict ${evidence.verdict}`}>{labels.verdict[evidence.verdict]}</span>
      {" "}· {evidence.collision ? "Có va chạm" : "Không va chạm"} · TTC tối thiểu {evidence.min_ttc_seconds ?? "—"}s
      {" "}· ghi lúc {formatDate(evidence.recorded_at)} · {evidence.total_runs} lượt chạy
    </span> : <span className="muted">Chưa có lượt chạy cho phiên bản này.</span>}
  </div>;
}

function ReviewPager({ total, page, size, onPage, onSize }: { total: number; page: number; size: number; onPage: (page: number) => void; onSize: (size: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const from = total ? (page - 1) * size + 1 : 0;
  return <div className="pagination">
    <span>{from}–{Math.min(page * size, total)} / {total}</span>
    <label className="pager-size">Hiển thị
      <select value={size} onChange={(event) => onSize(Number(event.target.value))}>
        {REVIEW_PAGE_SIZES.map((option) => <option key={option} value={option}>{option}</option>)}
      </select>
    </label>
    <span className="pager-buttons">
      <button className="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>← Trước</button>
      <span>{page} / {pages}</span>
      <button className="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Sau →</button>
    </span>
  </div>;
}

export function ReviewQueueScreen() {
  const { session, can } = useSession();
  const [tab, setTab] = useState<"queue" | "history">("queue");
  const [reviews, setReviews] = useState<Review[]>([]);
  const [history, setHistory] = useState<Review[]>([]);
  const [versions, setVersions] = useState<Record<number, TestCaseVersion>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [comment, setComment] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  // Reviewers decide; members who only create follow their own submissions read-only.
  const reviewer = can("review:read");
  const allowed = reviewer || can("testcase:submit_review");
  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError("");
    try {
      const [open, decided] = await Promise.all([
        api.listReviews(session.access_token, "open"),
        api.listReviews(session.access_token, "resolved"),
      ]);
      const versionEntries = await Promise.all(
        open.map(async (review) => [review.version_id, await api.getVersion(session.access_token, review.version_id)] as const),
      );
      setReviews(open);
      setHistory(decided);
      setVersions(Object.fromEntries(versionEntries));
    } catch (reason) {
      setError(errorText(reason, "Không thể tải hàng đợi review."));
    } finally {
      setLoading(false);
    }
  }, [session]);
  useEffect(() => {
    if (allowed) void load();
  }, [allowed, load]);
  async function decide(review: Review, decision: ReviewDecision) {
    if (!session) return;
    const text = comment[review.id] ?? "";
    if (decision !== "APPROVED" && !text.trim()) {
      setError(
        decision === "EDIT"
          ? "Yêu cầu chỉnh sửa bắt buộc phải có nhận xét cho Người tạo."
          : "Từ chối review bắt buộc phải có nhận xét.",
      );
      return;
    }
    setBusyId(review.id);
    setError("");
    try {
      await api.decideReview(session.access_token, review.id, decision, text);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể ra quyết định review."));
    } finally {
      setBusyId(null);
    }
  }
  async function addComment(review: Review) {
    if (!session || !(comment[review.id] ?? "").trim()) return;
    setBusyId(review.id);
    setError("");
    try {
      await api.commentReview(session.access_token, review.id, comment[review.id]);
      setComment({ ...comment, [review.id]: "" });
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể thêm nhận xét."));
    } finally {
      setBusyId(null);
    }
  }
  if (!allowed)
    return (
      <main className="main">
        {pageTitle("Hàng đợi duyệt", "Cần nhiệm vụ Tạo hoặc Duyệt test case để xem trang này.")}
        <ErrorNotice>Bạn không có quyền xem hàng đợi review.</ErrorNotice>
      </main>
    );
  const needle = query.trim().toLowerCase();
  const source = tab === "queue" ? reviews : history;
  const matches = source.filter((review) => !needle || reviewSearchText(review).includes(needle));
  const pages = Math.max(1, Math.ceil(matches.length / pageSize));
  const currentPage = Math.min(page, pages);
  const visible = matches.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const switchTab = (next: "queue" | "history") => { setTab(next); setPage(1); };
  return (
    <main className="main">
      {pageTitle(
        "Hàng đợi duyệt",
        reviewer
          ? "Reviewer xem metadata, XOSC và bằng chứng chạy rồi đưa ra quyết định trên từng version, không duyệt trên logical test case."
          : "Các version bạn đã gửi duyệt và quyết định của Reviewer. Chỉ người có nhiệm vụ Duyệt mới ra quyết định.",
        <RefreshButton onClick={() => void load()} />,
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "queue"} className={tab === "queue" ? "active" : ""} onClick={() => switchTab("queue")}>
          {reviewer ? "Chờ duyệt" : "Đã gửi, chờ quyết định"} ({reviews.length})
        </button>
        <button role="tab" aria-selected={tab === "history"} className={tab === "history" ? "active" : ""} onClick={() => switchTab("history")}>
          Lịch sử quyết định ({history.length})
        </button>
      </div>
      <div className="toolbar review-toolbar">
        <input
          value={query}
          onChange={(event) => { setQuery(event.target.value); setPage(1); }}
          placeholder="Tìm mã, tên kịch bản, người gửi, bản đồ hoặc tác nhân"
          aria-label="Tìm review"
        />
      </div>
      {loading ? (
        <Loading />
      ) : !matches.length ? (
        <Empty>{needle ? "Không có review khớp từ khóa." : tab === "queue" ? "Không có version nào đang chờ duyệt." : "Chưa có quyết định nào."}</Empty>
      ) : tab === "history" ? (
        <section className="panel">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Kịch bản / version</th>
                  <th>Quyết định</th>
                  <th>Lý do</th>
                  <th>Người duyệt</th>
                  <th>Bằng chứng chạy</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((review) => (
                  <tr key={review.id}>
                    <td>
                      {review.case_id ? (
                        <Link className="link" href={`/test-cases/${review.case_id}/versions/${review.version_id}`}>
                          {review.case_key} · v{review.version_no}
                        </Link>
                      ) : `Version #${review.version_id}`}
                      <div className="case-key">{review.title} · gửi bởi {review.requested_by_name ?? `#${review.requested_by}`}</div>
                    </td>
                    <td>{review.decision ? <span className={`badge ${review.decision}`}>{labels.status[review.decision]}</span> : "—"}</td>
                    <td className="review-reason">{review.decision_comment || <span className="muted">Không có ghi chú</span>}</td>
                    <td>
                      {review.resolved_by_name ?? "—"}
                      <div className="case-key">{formatDate(review.resolved_at)}</div>
                    </td>
                    <td>
                      {review.evidence ? (
                        <>
                          <span className={`verdict ${review.evidence.verdict}`}>{labels.verdict[review.evidence.verdict]}</span>
                          <div className="case-key">{review.evidence.collision ? "Có va chạm" : "Không va chạm"} · {review.evidence.total_runs} lượt</div>
                        </>
                      ) : <span className="muted">Chưa có lượt chạy</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ReviewPager total={matches.length} page={currentPage} size={pageSize} onPage={setPage} onSize={(size) => { setPageSize(size); setPage(1); }} />
        </section>
      ) : (
        <>
          <section className="review-list">
            {visible.map((review) => {
              const version = versions[review.version_id];
              const ownCase = (version?.created_by ?? review.requested_by) === session?.user.id;
              const canDecide = can("review:decide") && (!ownCase || can("review:decide_own"));
              return (
                <article className="panel review-card" key={review.id}>
                  <div className="panel-header">
                    <div>
                      <div className="panel-title">
                        {review.case_id ? (
                          <Link className="link" href={`/test-cases/${review.case_id}/versions/${review.version_id}`}>
                            {review.case_key} · {review.title}
                          </Link>
                        ) : `Review version ${version?.version_no ?? "…"}`}
                      </div>
                      <div className="panel-subtitle">
                        Version {review.version_no ?? version?.version_no ?? "…"} · gửi bởi {review.requested_by_name ?? `#${review.requested_by}`} lúc {formatDate(review.requested_at)}
                      </div>
                    </div>
                    {version && <StatusBadge status={version.status} />}
                  </div>
                  {version && (
                    <div className="metadata-summary review-metadata">
                      {metadataRows(version).map(([label, value]) => (
                        <span key={label}>
                          <b>{label}:</b> {value}
                        </span>
                      ))}
                      <span>
                        <b>XOSC:</b>{" "}
                        {version.xosc_artifact_id ? "Đã có artifact" : "Chưa có artifact"}
                      </span>
                    </div>
                  )}
                  <ReviewEvidenceBlock review={review} />
                  <div className="review-comments">
                    {review.comments.length ? (
                      review.comments.map((item) => (
                        <div className="comment" key={item.id}>
                          <div>
                            <b>{item.comment_type === "DECISION" ? "Quyết định" : "Nhận xét"}</b>{" "}
                            · {formatDate(item.created_at)}
                          </div>
                          <p>{item.body}</p>
                        </div>
                      ))
                    ) : (
                      <span className="muted">Chưa có nhận xét.</span>
                    )}
                  </div>
                  {!review.resolved_at && reviewer && (
                    <div className="decision-area">
                      <textarea
                        value={comment[review.id] ?? ""}
                        onChange={(event) => setComment({ ...comment, [review.id]: event.target.value })}
                        placeholder="Nhận xét cho Người tạo. Bắt buộc khi yêu cầu chỉnh sửa hoặc từ chối."
                      />
                      {can("review:comment") && (
                        <button className="button" disabled={busyId === review.id} onClick={() => void addComment(review)}>
                          Thêm nhận xét
                        </button>
                      )}
                      {can("review:decide") && !canDecide && (
                        <span className="muted">Bạn chưa được giao nhiệm vụ tự duyệt test case của mình.</span>
                      )}
                      {canDecide && (
                        <div className="decision-actions">
                          <button className="button" disabled={busyId === review.id} onClick={() => void decide(review, "EDIT")}>
                            Yêu cầu chỉnh sửa
                          </button>
                          <button className="button danger-button" disabled={busyId === review.id} onClick={() => void decide(review, "REJECTED")}>
                            Từ chối
                          </button>
                          <button className="button primary" disabled={busyId === review.id} onClick={() => void decide(review, "APPROVED")}>
                            Phê duyệt
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  {!review.resolved_at && !reviewer && (
                    <div className="decision-area review-readonly">
                      <span className="muted">Đang chờ Reviewer quyết định. Bạn sẽ thấy kết quả ở tab Lịch sử quyết định.</span>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
          <section className="panel review-pager-panel">
            <ReviewPager total={matches.length} page={currentPage} size={pageSize} onPage={setPage} onSize={(size) => { setPageSize(size); setPage(1); }} />
          </section>
        </>
      )}
    </main>
  );
}

export function SuiteListScreen() {
  const { session, can } = useSession();
  const router = useRouter();
  const [suites, setSuites] = useState<TestSuite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    try {
      setSuites(await api.listSuites(session.access_token));
      setError("");
    } catch (reason) {
      setError(errorText(reason, "Không thể tải Test Suite."));
    } finally {
      setLoading(false);
    }
  }, [session]);
  useEffect(() => {
    void load();
  }, [load]);
  async function create(event: FormEvent) {
    event.preventDefault();
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const suite = await api.createSuite(
        session.access_token,
        name,
        description,
      );
      router.push(`/suites/${suite.id}`);
    } catch (reason) {
      setError(errorText(reason, "Không thể tạo Test Suite."));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="main">
      {pageTitle(
        "Bộ kiểm thử",
        "Test Suite pin chính xác test_case_version_id đã Approved; không tự nhảy sang latest version.",
      )}{" "}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      {can("suite:manage") && (
        <section className="panel compact-panel">
          <form className="inline-form" onSubmit={create}>
            <label className="field grow">
              Tên bộ kiểm thử
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Critical Urban Regression"
              />
            </label>
            <label className="field grow">
              Mô tả
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Mục tiêu hoặc phạm vi batch run"
              />
            </label>
            <button className="button primary" disabled={busy}>
              {busy ? "Đang tạo…" : "Tạo Suite"}
            </button>
          </form>
        </section>
      )}
      {loading ? (
        <Loading />
      ) : !suites.length ? (
        <Empty>
          Chưa có Test Suite. Chỉ user có quyền suite:manage mới tạo được.
        </Empty>
      ) : (
        <section className="suite-grid">
          {suites.map((suite) => (
            <Link
              key={suite.id}
              href={`/suites/${suite.id}`}
              className="suite-card"
            >
              <div>
                <strong>{suite.name}</strong>
                <p>{suite.description || "Chưa có mô tả"}</p>
              </div>
              <div className="suite-stats">
                <span>{suite.items.length} version được pin</span>
                <span>Cập nhật {formatDate(suite.updated_at)}</span>
              </div>
            </Link>
          ))}
        </section>
      )}
    </main>
  );
}

export function SuiteDetailScreen() {
  const { session, can } = useSession();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [suite, setSuite] = useState<TestSuite | null>(null);
  const [versions, setVersions] = useState<Record<number, TestCaseVersion>>({});
  const [approved, setApproved] = useState<TestCase[]>([]);
  const [runs, setRuns] = useState<SuiteRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedVersion, setSelectedVersion] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    try {
      const [nextSuite, page, nextRuns] = await Promise.all([
        api.getSuite(session.access_token, params.id),
        api.listCases(
          session.access_token,
          new URLSearchParams("status=APPROVED&page_size=100"),
        ),
        api.listSuiteRuns(session.access_token, params.id),
      ]);
      const ids = nextSuite.items.map((item) => item.test_case_version_id);
      const entries = await Promise.all(
        ids.map(
          async (id) =>
            [id, await api.getVersion(session.access_token, id)] as const,
        ),
      );
      setSuite(nextSuite);
      setVersions(Object.fromEntries(entries));
      setApproved(page.items);
      setRuns(nextRuns);
      setError("");
    } catch (reason) {
      setError(errorText(reason, "Không thể tải chi tiết Test Suite."));
    } finally {
      setLoading(false);
    }
  }, [params.id, session]);
  useEffect(() => {
    void load();
  }, [load]);
  async function add() {
    if (!session || !selectedVersion) return;
    setBusy(true);
    setError("");
    try {
      await api.addSuiteItem(session.access_token, params.id, selectedVersion);
      setSelectedVersion("");
      setNotice("Đã pin version Approved vào Test Suite.");
      await load();
    } catch (reason) {
      setError(
        errorText(
          reason,
          "Không thể thêm version. Chỉ Approved version mới hợp lệ.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  async function remove(versionId: number) {
    if (!session) return;
    setBusy(true);
    try {
      await api.removeSuiteItem(session.access_token, params.id, versionId);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể xóa item khỏi suite."));
    } finally {
      setBusy(false);
    }
  }
  async function reorder(versionId: number, direction: -1 | 1) {
    if (!session || !suite) return;
    const ids = suite.items
      .slice()
      .sort((a, b) => a.position - b.position)
      .map((item) => item.test_case_version_id);
    const index = ids.indexOf(versionId);
    const next = index + direction;
    if (next < 0 || next >= ids.length) return;
    [ids[index], ids[next]] = [ids[next], ids[index]];
    setBusy(true);
    try {
      await api.reorderSuiteItems(session.access_token, suite.id, ids);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể đổi thứ tự Test Suite."));
    } finally {
      setBusy(false);
    }
  }
  async function run() {
    if (!session || !suite) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.runSuite(session.access_token, suite.id);
      router.push(`/runs/${created.id}`);
    } catch (reason) {
      setError(errorText(reason, "Không thể tạo batch run."));
    } finally {
      setBusy(false);
    }
  }
  async function exportPackage() {
    if (!session || !suite) return;
    setBusy(true);
    try {
      const response = await api.exportSuite(session.access_token, suite.id);
      setNotice(
        `Đã yêu cầu export manifest cho ${response.version_ids.length} version đã pin.`,
      );
    } catch (reason) {
      setError(errorText(reason, "Không thể export Test Suite."));
    } finally {
      setBusy(false);
    }
  }
  if (loading)
    return (
      <main className="main">
        <Loading />
      </main>
    );
  if (!suite)
    return (
      <main className="main">
        <BackLink href="/suites" />
        <ErrorNotice>{error || "Không tìm thấy Test Suite."}</ErrorNotice>
      </main>
    );
  const candidates = approved.filter(
    (item) =>
      item.latest_version &&
      !suite.items.some(
        (suiteItem) =>
          suiteItem.test_case_version_id === item.latest_version?.id,
      ),
  );
  return (
    <main className="main">
      {pageTitle(
        suite.name,
        suite.description || "Chưa có mô tả.",
        <div className="top-actions">
          {can("suite:run") && (
            <button
              className="button primary"
              disabled={busy || !suite.items.length}
              onClick={() => void run()}
            >
              Chạy batch
            </button>
          )}
          <button
            className="button"
            disabled={busy}
            onClick={() => void exportPackage()}
          >
            Xuất manifest
          </button>
        </div>,
      )}
      <BackLink href="/suites">Danh sách Suite</BackLink>
      {notice && <SuccessNotice>{notice}</SuccessNotice>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Version được pin</div>
              <div className="panel-subtitle">
                Chỉ Approved version được Backend chấp nhận.
              </div>
            </div>
            <span className="muted">{suite.items.length} item</span>
          </div>
          {!suite.items.length ? (
            <Empty>Suite chưa có item nào.</Empty>
          ) : (
            <div className="suite-items">
              {suite.items
                .slice()
                .sort((a, b) => a.position - b.position)
                .map((item, index) => {
                  const version = versions[item.test_case_version_id];
                  return (
                    <div className="suite-item" key={item.test_case_version_id}>
                      <span className="position">{index + 1}</span>
                      <div className="grow">
                        <Link
                          className="link"
                          href={`/test-cases/${version?.test_case_id ?? ""}/versions/${item.test_case_version_id}`}
                        >
                          Version {version?.version_no ?? "…"}
                        </Link>
                        <div className="case-key">
                          {version
                            ? `${version.map_code} · ${version.adversary_type} · ${version.environment_code}`
                            : item.test_case_version_id}
                        </div>
                      </div>
                      {version && <StatusBadge status={version.status} />}
                      {can("suite:manage") && (
                        <div className="mini-actions">
                          <button
                            className="icon-button"
                            disabled={busy || index === 0}
                            onClick={() =>
                              void reorder(item.test_case_version_id, -1)
                            }
                          >
                            ↑
                          </button>
                          <button
                            className="icon-button"
                            disabled={busy || index === suite.items.length - 1}
                            onClick={() =>
                              void reorder(item.test_case_version_id, 1)
                            }
                          >
                            ↓
                          </button>
                          <button
                            className="icon-button destructive"
                            disabled={busy}
                            onClick={() =>
                              void remove(item.test_case_version_id)
                            }
                          >
                            ×
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          )}
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Thêm version Approved</div>
              <div className="panel-subtitle">
                Danh sách lấy trực tiếp từ catalog với status=APPROVED.
              </div>
            </div>
          </div>
          {can("suite:manage") ? (
            <div className="add-suite">
              <select
                value={selectedVersion}
                onChange={(event) => setSelectedVersion(event.target.value ? Number(event.target.value) : "")}
              >
                <option value="">Chọn version Approved</option>
                {candidates.map((item) => (
                  <option
                    key={item.latest_version?.id}
                    value={item.latest_version?.id}
                  >
                    {item.case_key} · v{item.latest_version?.version_no} ·{" "}
                    {item.title}
                  </option>
                ))}
              </select>
              <button
                className="button primary"
                disabled={!selectedVersion || busy}
                onClick={() => void add()}
              >
                Thêm vào Suite
              </button>
            </div>
          ) : (
            <div className="inline-note">Bạn chỉ có quyền xem Suite.</div>
          )}
        </div>
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Lịch sử batch run</div>
            <div className="panel-subtitle">
              Mỗi run snapshot item tại thời điểm yêu cầu chạy.
            </div>
          </div>
        </div>
        {!runs.length ? (
          <Empty>Chưa có batch run.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Trạng thái</th>
                  <th>Tiến độ</th>
                  <th>Tạo lúc</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((runItem) => (
                  <tr key={runItem.id}>
                    <td>
                      <Link className="link" href={`/runs/${runItem.id}`}>
                        #{runItem.id}
                      </Link>
                    </td>
                    <td>
                      <span className={`run-status ${runItem.status}`}>
                        {labels.run[runItem.status]}
                      </span>
                    </td>
                    <td>
                      {runItem.completed_jobs}/{runItem.total_jobs} job
                    </td>
                    <td>{formatDate(runItem.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

export function RunDetailScreen() {
  const { session, can } = useSession();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const isLatest = useLatestRequest();
  const params = useParams<{ id: string }>();
  const [run, setRun] = useState<SuiteRun | null>(null);
  const [jobs, setJobs] = useState<RunJobSummary[]>([]);
  const [details, setDetails] = useState<Record<number, RunJob>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!session) return;
    const current = isLatest();
    try {
      const [nextRun, summaries] = await Promise.all([
        api.getSuiteRun(session.access_token, params.id),
        api.listRunJobs(session.access_token, params.id),
      ]);
      const rows = await Promise.all(
        summaries.map(
          async (job) =>
            [
              job.id,
              await api.getRunJob(session.access_token, job.id),
            ] as const,
        ),
      );
      if (!current()) return;
      setRun(nextRun);
      setJobs(summaries);
      setDetails(Object.fromEntries(rows));
      setError("");
    } catch (reason) {
      if (current()) setError(errorText(reason, "Không thể tải trạng thái batch run."));
    } finally {
      if (current()) setLoading(false);
    }
  }, [isLatest, params.id, session]);
  useEffect(() => {
    void load();
  }, [load]);
  const runStatus = run?.status;
  useEffect(() => {
    if (!runStatus || ["COMPLETED", "FAILED", "CANCELLED"].includes(runStatus))
      return;
    // Schedule the next poll only after the previous one settled, so slow requests never pile up.
    let timer: number | undefined;
    let stopped = false;
    const tick = async () => {
      await load();
      if (!stopped) timer = window.setTimeout(() => void tick(), 3000);
    };
    timer = window.setTimeout(() => void tick(), 3000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [load, runStatus]);
  async function cancel(id: number) {
    if (!session) return;
    if (!(await confirm({
      title: "Hủy lượt chạy?",
      confirmLabel: "Hủy lượt chạy",
      message: <>Lượt chạy mô phỏng <strong>#{id}</strong> đang chờ sẽ bị hủy và không chạy nữa.</>,
    }))) return;
    setError("");
    try {
      await api.cancelRunJob(session.access_token, id);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể hủy run job."));
    }
  }
  if (loading)
    return (
      <main className="main">
        <Loading text="Đang tải và theo dõi batch run…" />
      </main>
    );
  if (!run)
    return (
      <main className="main">
        <BackLink href="/suites" />{" "}
        <ErrorNotice>{error || "Không tìm thấy batch run."}</ErrorNotice>
      </main>
    );
  return (
    <main className="main">
      {pageTitle(
        "Chi tiết batch run",
        "Màn hình polling 3 giây để hiển thị trạng thái Worker/ScenarioRunner mà không giữ HTTP request treo.",
        <RefreshButton onClick={() => void load()} />,
      )}
      <BackLink href={`/suites/${run.suite_id}`}>Quay lại Test Suite</BackLink>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="stats stats-three">
        <Stat
          label="Trạng thái"
          value={labels.run[run.status]}
          detail="Suite run lifecycle"
        />
        <Stat
          label="Tiến độ"
          value={`${run.completed_jobs}/${run.total_jobs}`}
          detail="Job đã kết thúc"
        />
        <Stat
          label="Bắt đầu"
          value={formatDate(run.started_at)}
          detail="Snapshot lúc tạo run"
        />
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Run jobs</div>
            <div className="panel-subtitle">
              Kết quả gắn với đúng test_case_version_id được pin trong suite.
            </div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Version</th>
                <th>Trạng thái</th>
                <th>Kết quả / metrics</th>
                <th>Lỗi</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((summary) => {
                const job = details[summary.id];
                return (
                  <tr key={summary.id}>
                    <td className="case-key">#{summary.id}</td>
                    <td className="case-key">
                      #{summary.test_case_version_id}
                    </td>
                    <td>
                      <span className={`run-status ${summary.status}`}>
                        {labels.run[summary.status]}
                      </span>
                    </td>
                    <td>
                      {job?.result ? (
                        <>
                          <span className={`verdict ${job.result.verdict}`}>
                            {labels.verdict[job.result.verdict]}
                          </span>
                          <pre className="metrics">
                            {JSON.stringify(job.result.metrics, null, 2)}
                          </pre>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {summary.error_code ? (
                        <span className="error-code">
                          {summary.error_code}
                          <br />
                          {summary.error_message}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      {can("suite:run") &&
                        ["QUEUED", "CLAIMED"].includes(summary.status) && (
                          <button
                            className="button"
                            onClick={() => void cancel(summary.id)}
                          >
                            Hủy
                          </button>
                        )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      {confirmDialog}
    </main>
  );
}

export function AuditScreen() {
  const { session, can } = useSession();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [filters, setFilters] = useState({ action: "", entityType: "" });
  const isLatest = useLatestRequest();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
  const load = useCallback(async () => {
    if (!session || !can("audit:read")) return;
    const current = isLatest();
    setLoading(true);
    try {
      const query = new URLSearchParams("page_size=100");
      if (filters.action) query.set("action", filters.action);
      if (filters.entityType) query.set("entity_type", filters.entityType);
      const next = await api.listAudit(session.access_token, query);
      if (!current()) return;
      setLogs(next);
      setError("");
    } catch (reason) {
      if (current()) setError(errorText(reason, "Không thể tải nhật ký hoạt động."));
    } finally {
      if (current()) setLoading(false);
    }
  }, [filters, can, isLatest, session]);
  useEffect(() => {
    void load();
  }, [load]);
  if (!can("audit:read"))
    return (
      <main className="main">
        {pageTitle("Nhật ký hoạt động", "Chỉ Quản trị viên của Project mới xem được nhật ký này.")}
        <ErrorNotice>Bạn không có quyền xem nhật ký hoạt động.</ErrorNotice>
      </main>
    );
  const filtered = Boolean(filters.action || filters.entityType);
  return (
    <main className="main">
      {pageTitle(
        "Nhật ký hoạt động",
        "Ghi lại ai đã làm gì trong Project và vào lúc nào. Nhật ký chỉ được thêm mới, không ai sửa hay xóa được.",
        <RefreshButton onClick={() => void load()} />,
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="panel">
        <div className="filters">
          <label className="audit-filter">
            <span>Hoạt động</span>
            <select value={filters.action} onChange={(event) => setFilters({ ...filters, action: event.target.value })}>
              <option value="">Tất cả hoạt động</option>
              {actionGroups().map(([group, items]) => (
                <optgroup key={group} label={group}>
                  {items.map((item) => <option key={item.code} value={item.code}>{item.label}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          <label className="audit-filter">
            <span>Liên quan đến</span>
            <select value={filters.entityType} onChange={(event) => setFilters({ ...filters, entityType: event.target.value })}>
              <option value="">Tất cả</option>
              {Object.entries(auditEntityTypes).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
          </label>
          {filtered && <button className="button audit-filter-clear" type="button" onClick={() => setFilters({ action: "", entityType: "" })}>Xóa bộ lọc</button>}
        </div>
        {loading ? (
          <Loading />
        ) : !logs.length ? (
          <Empty>{filtered ? "Không có hoạt động nào khớp bộ lọc." : "Chưa có hoạt động nào được ghi lại."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table audit-table">
              <thead>
                <tr>
                  <th>Thời gian</th>
                  <th>Người thực hiện</th>
                  <th>Hoạt động</th>
                  <th>Liên quan đến</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => {
                  const changes = auditChanges(log.before_data, log.after_data);
                  const open = expanded === log.id;
                  return (
                    <Fragment key={log.id}>
                      <tr>
                        <td className="audit-time">{formatDate(log.created_at)}</td>
                        <td>
                          {log.actor_user_id ? (
                            <div className="audit-person">
                              <strong>{log.actor_name ?? "Người dùng đã bị xóa"}</strong>
                              {log.actor_email && log.actor_email !== log.actor_name && <span className="muted" title={log.actor_email}>{log.actor_email}</span>}
                            </div>
                          ) : (
                            <div className="audit-person">
                              <strong>Hệ thống</strong>
                              <span className="muted">Tự động</span>
                            </div>
                          )}
                        </td>
                        <td>
                          <span className={`audit-action audit-${actionTone(log.action)}`}>{actionLabel(log.action)}</span>
                        </td>
                        <td>
                          <div className="audit-person">
                            <strong>{log.entity_label ?? (log.entity_id ? "(không còn tồn tại)" : "—")}</strong>
                            <span className="muted">{entityTypeLabel(log.entity_type)}</span>
                          </div>
                        </td>
                        <td className="audit-toggle">
                          {changes.length > 0 && (
                            <button className="text-button" onClick={() => setExpanded(open ? null : log.id)} aria-expanded={open}>
                              {open ? "Ẩn chi tiết" : "Xem chi tiết"}
                            </button>
                          )}
                        </td>
                      </tr>
                      {open && (
                        <tr className="audit-detail-row">
                          <td colSpan={5}>
                            <table className="audit-changes">
                              <thead>
                                <tr><th>Thông tin</th><th>Trước</th><th>Sau</th></tr>
                              </thead>
                              <tbody>
                                {changes.map((change) => (
                                  <tr key={change.field}>
                                    <td>{change.label}</td>
                                    <td className={change.before === null ? "muted" : ""}>{change.before ?? "—"}</td>
                                    <td>{change.after ?? "—"}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
