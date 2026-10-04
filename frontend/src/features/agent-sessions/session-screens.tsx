"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, BuilderError, BuilderSession, BuilderSessionCreate, BuilderSessionDetail, BuilderSessionStatus, TestCase } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { BackLink, DangerBadge, Empty, ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { withFrom } from "@/shared/ui/back-target";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";

import { SessionInputForm, SessionInputView } from "./composer";

const POLL_MS = 4000;

type ScenarioStatus = "GENERATING" | "PENDING" | "APPROVED" | "REJECTED" | "FAILED";
const scenarioStatusLabel: Record<ScenarioStatus, string> = {
  GENERATING: "Đang sinh",
  PENDING: "Chờ duyệt",
  APPROVED: "Đã duyệt",
  REJECTED: "Từ chối",
  FAILED: "Sinh lỗi",
};
// Reuse the version badge palette: grey = generating, blue = waiting, green = approved, red = rejected/failed.
const SCENARIO_BADGE: Record<ScenarioStatus, string> = { GENERATING: "DRAFT", PENDING: "IN_REVIEW", APPROVED: "APPROVED", REJECTED: "REJECTED", FAILED: "REJECTED" };
const sessionStatusLabel: Record<BuilderSessionStatus, string> = {
  GENERATING: "Đang sinh",
  COMPLETED: "Hoàn tất",
  PARTIAL: "Hoàn tất một phần",
  FAILED: "Sinh lỗi",
};
const SESSION_BADGE: Record<BuilderSessionStatus, string> = { GENERATING: "badge IN_REVIEW", COMPLETED: "badge APPROVED", PARTIAL: "badge EDIT", FAILED: "badge REJECTED" };

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}
function ScenarioBadge({ status }: { status: ScenarioStatus }) {
  return <span className={`badge ${SCENARIO_BADGE[status]}`}>{scenarioStatusLabel[status]}</span>;
}
function SessionBadge({ status }: { status: BuilderSessionStatus }) {
  return <span className={SESSION_BADGE[status]}>{sessionStatusLabel[status]}</span>;
}
function Heading({ title, description, actions }: { title: ReactNode; description: ReactNode; actions?: ReactNode }) {
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
/** Polls `load` every few seconds while `active` is true. */
function usePolling(active: boolean, load: () => void) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);
}

/* /test-case-builder — every Builder session of the project. */
export function AgentSessionListScreen() {
  const p = useProjectPath();
  const { session, can } = useSession();
  const router = useRouter();
  const [sessions, setSessions] = useState<BuilderSession[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const token = session?.access_token;

  const load = useCallback(() => {
    if (!token) return;
    api.listBuilderSessions(token)
      .then((items) => { setSessions(items); setError(""); })
      .catch((reason) => { setSessions((previous) => previous ?? []); setError(errorText(reason, "Không tải được danh sách phiên.")); });
  }, [token]);
  useEffect(load, [load]);
  usePolling(Boolean(sessions?.some((item) => item.status === "GENERATING")), load);

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    if (!sessions || !text) return sessions ?? [];
    return sessions.filter((item) => [item.title, item.prompt, `#${item.id}`, item.created_by_name ?? "", ...item.maps.map((map) => map.map_code), ...item.tag_names]
      .some((value) => value.toLowerCase().includes(text)));
  }, [sessions, query]);

  return (
    <main className="main">
      <Heading
        title="Test Case Builder"
        description="Mỗi phiên là một lần gửi mô tả cho Scenario Agent, sinh 10 test case trên các bản đồ đã chọn. Mở phiên để xem đầu vào và duyệt các test case."
        actions={can("testcase:create") && <Link className="button primary" href={p("/test-case-builder/create")}><Icon name="plus" size={14} />Phiên mới</Link>}
      />
      <section className="panel">
        <div className="filters">
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo tiêu đề, mô tả, mã phiên, người tạo, bản đồ, thẻ…" aria-label="Tìm phiên" />
        </div>
        {error && <ErrorNotice>{error}</ErrorNotice>}
        {sessions === null ? <Loading /> : visible.length === 0 ? (
          <Empty>{sessions.length === 0 ? "Chưa có phiên nào. Bấm “Phiên mới” để sinh test case." : "Không có phiên nào khớp tìm kiếm."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Phiên</th><th>Bản đồ</th><th>Tiến độ</th><th>Người tạo</th></tr></thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={item.id} className="row-link" onClick={() => router.push(p(`/test-case-builder/${item.id}`))}>
                    <td>
                      <Link className="link" href={p(`/test-case-builder/${item.id}`)} onClick={(event) => event.stopPropagation()}>{item.title}</Link>
                      <div className="muted session-row-sub"><span className="session-code">#{item.id}</span> · {item.prompt}</div>
                      <div>{item.tag_names.map((tag) => <span key={tag} className="tag">{tag}</span>)}</div>
                    </td>
                    <td>{item.maps.map((map) => map.map_code).join(", ")}</td>
                    <td>
                      <div>
                        {item.succeeded_count}/{item.target_count} test case{item.failed_count > 0 ? ` · ${item.failed_count} lỗi` : ""}
                      </div>
                      {item.pending_count > 0 && <span className="badge IN_REVIEW">{item.pending_count} chờ duyệt</span>}
                    </td>
                    <td>
                      <div>{item.created_by_name ?? `#${item.created_by}`}</div>
                      <div className="muted">{formatDate(item.updated_at)}</div>
                    </td>
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

/* /test-case-builder/create — full-width input form; submitting creates the session and opens its detail page. */
export function AgentSessionCreateScreen() {
  const p = useProjectPath();
  const { session, can } = useSession();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!can("testcase:create"))
    return <main className="main"><ErrorNotice>Bạn không có quyền tạo test case trong Project này.</ErrorNotice></main>;

  async function submit(body: BuilderSessionCreate) {
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const created = await api.createBuilderSession(session.access_token, body);
      router.push(p(`/test-case-builder/${created.id}`));
    } catch (reason) {
      setError(errorText(reason, "Không tạo được phiên."));
      setBusy(false);
    }
  }

  return (
    <main className="main chat-create-page">
      <div className="chat-create-top">
        <Link className="button" href={p("/test-case-builder")}><Icon name="clock" size={14} />Xem danh sách phiên</Link>
      </div>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <SessionInputForm busy={busy} onSubmit={(body) => void submit(body)} />
    </main>
  );
}

type ScenarioItem = { no: number; map_code: string; status: ScenarioStatus; testCase?: TestCase; error?: BuilderError };

/** One row per planned variant (1..target): saved test case, failure, or still generating. */
function scenarioItems(detail: BuilderSessionDetail): ScenarioItem[] {
  return Array.from({ length: detail.target_count }, (_, index) => {
    const no = index + 1;
    const plannedMap = detail.maps[index % Math.max(detail.maps.length, 1)]?.map_code ?? "";
    const testCase = detail.test_cases.find((item) => item.builder_variant_no === no);
    if (testCase) {
      const version = testCase.latest_version;
      const status: ScenarioStatus = version?.status === "APPROVED" ? "APPROVED" : version?.status === "REJECTED" ? "REJECTED" : "PENDING";
      return { no, map_code: version?.map_code ?? plannedMap, status, testCase };
    }
    const error = detail.errors.find((item) => item.variant_no === no);
    if (error) return { no, map_code: error.map_code, status: "FAILED", error };
    return {
      no,
      map_code: plannedMap,
      status: detail.status === "GENERATING" ? "GENERATING" : "FAILED",
      error: detail.status === "GENERATING" ? undefined : { variant_no: no, map_code: plannedMap, code: "INTERRUPTED", message: "Phiên bị gián đoạn trước khi sinh xong biến thể này." },
    };
  });
}

type QueueFilter = "ALL" | ScenarioStatus;

function ScenarioRow({ sessionId, item, canDecide, onDecided }: { sessionId: number; item: ScenarioItem; canDecide: boolean; onDecided: () => void }) {
  const p = useProjectPath();
  const { session } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const version = item.testCase?.latest_version;

  // Records the decision through the normal review flow, so /test-suite and the review history stay in sync.
  async function decide(decision: "APPROVED" | "REJECTED") {
    if (!session || !version) return;
    setBusy(true);
    setError("");
    try {
      let reviewId: number | undefined;
      if (version.status === "IN_REVIEW") {
        reviewId = (await api.listReviews(session.access_token)).find((review) => review.version_id === version.id)?.id;
      }
      if (!reviewId) reviewId = (await api.submitReview(session.access_token, version.id, "Gửi duyệt từ Test Case Builder.")).id;
      await api.decideReview(session.access_token, reviewId, decision, decision === "REJECTED" ? "Từ chối trong Test Case Builder." : "");
      onDecided();
    } catch (reason) {
      setError(errorText(reason, "Không lưu được quyết định duyệt."));
    } finally {
      setBusy(false);
    }
  }

  const title = item.testCase?.title ?? `Biến thể #${item.no}`;
  return (
    <li className="session-scenario">
      <div className="version-card-head">
        <div>
          <div className="version-title">
            {item.testCase ? (
              <Link className="link" href={withFrom(p(`/test-cases/${item.testCase.id}`), p(`/test-case-builder/${sessionId}`))} target="_blank" rel="noopener noreferrer" title="Mở chi tiết test case ở tab mới">{title}</Link>
            ) : title}
          </div>
          <div className="muted">
            <span className="session-code">{item.testCase ? item.testCase.case_key : `#${item.no}`}</span> · {item.map_code}
          </div>
        </div>
        <div className="session-scenario-actions">
          {item.status === "PENDING" && canDecide && <>
            <button type="button" className="icon-button session-reject" title="Từ chối" aria-label={`Từ chối ${title}`} disabled={busy} onClick={() => void decide("REJECTED")}><Icon name="x" /></button>
            <button type="button" className="icon-button session-approve" title="Duyệt" aria-label={`Duyệt ${title}`} disabled={busy} onClick={() => void decide("APPROVED")}><Icon name="tick" /></button>
          </>}
          <ScenarioBadge status={item.status} />
        </div>
      </div>
      {item.status === "GENERATING" && <div className="muted"><span className="spinner" />Agent đang sinh…</div>}
      {item.error && <div className="error-code">{item.error.message}</div>}
      {error && <div className="error-code">{error}</div>}
      {version && (
        <div className="metadata-summary">
          <span><DangerBadge level={version.danger_level} /></span>
          <span><b>Xe ego</b> {version.ego_vehicle_code}</span>
          <span><b>Tác nhân</b> {version.adversary_type}</span>
          <span><b>Môi trường</b> {version.environment_code}</span>
        </div>
      )}
    </li>
  );
}

function ScenarioReviewList({ detail, canDecide, onDecided }: { detail: BuilderSessionDetail; canDecide: boolean; onDecided: () => void }) {
  const [filter, setFilter] = useState<QueueFilter>("ALL");
  const items = scenarioItems(detail);
  const count = (status: ScenarioStatus) => items.filter((item) => item.status === status).length;
  const tabs: { key: QueueFilter; label: string; count: number }[] = [
    { key: "ALL", label: "Tất cả", count: items.length },
    { key: "PENDING", label: "Chờ duyệt", count: count("PENDING") },
    { key: "APPROVED", label: "Đã duyệt", count: count("APPROVED") },
    { key: "REJECTED", label: "Từ chối", count: count("REJECTED") },
  ];
  const visible = filter === "ALL" ? items : items.filter((item) => item.status === filter);

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Kịch bản đã sinh</div>
          <div className="panel-subtitle">
            {count("GENERATING") > 0 ? `Agent đang sinh ${count("GENERATING")}/${items.length}… · ` : ""}
            {count("PENDING")} chờ duyệt · {count("APPROVED")} đã duyệt · {count("REJECTED") + count("FAILED")} bị loại
          </div>
        </div>
        <SessionBadge status={detail.status} />
      </div>
      <div className="tabs session-tabs" role="tablist">
        {tabs.map((tab) => (
          <button key={tab.key} type="button" role="tab" aria-selected={filter === tab.key} className={filter === tab.key ? "active" : ""} onClick={() => setFilter(tab.key)}>
            {tab.label} <span className="muted">{tab.count}</span>
          </button>
        ))}
      </div>
      {visible.length === 0 ? <Empty>Không có kịch bản ở trạng thái này.</Empty> : (
        <ul className="session-queue">
          {visible.map((item) => <ScenarioRow key={item.no} sessionId={detail.id} item={item} canDecide={canDecide} onDecided={onDecided} />)}
        </ul>
      )}
    </section>
  );
}

/* /test-case-builder/[id] — left: the inputs of this session (read-only), right: generated test cases to review. */
export function AgentSessionDetailScreen() {
  const p = useProjectPath();
  const { session, can } = useSession();
  const params = useParams<{ id: string }>();
  const [detail, setDetail] = useState<BuilderSessionDetail | null>(null);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const token = session?.access_token;

  const load = useCallback(() => {
    if (!token) return;
    api.getBuilderSession(token, params.id)
      .then((result) => { setDetail(result); setError(""); })
      .catch((reason) => setError(errorText(reason, "Không tải được phiên.")))
      .finally(() => setLoaded(true));
  }, [token, params.id]);
  useEffect(load, [load]);
  usePolling(detail?.status === "GENERATING", load);

  if (!loaded) return <main className="main"><Loading /></main>;
  if (!detail)
    return (
      <main className="main">
        <BackLink href={p("/test-case-builder")}>Danh sách phiên</BackLink>
        <ErrorNotice>{error || "Không tìm thấy phiên."}</ErrorNotice>
      </main>
    );

  return (
    <main className="main">
      <Heading
        title={detail.title}
        description={<><span className="session-code">#{detail.id}</span> · {detail.created_by_name ?? `#${detail.created_by}`} · tạo {formatDate(detail.created_at)} · cập nhật {formatDate(detail.updated_at)}</>}
      />
      <BackLink href={p("/test-case-builder")}>Danh sách phiên</BackLink>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <div className="session-workspace">
        <div className="session-col"><SessionInputView input={detail} /></div>
        <div className="session-col session-col-sticky">
          <ScenarioReviewList detail={detail} canDecide={can("review:decide")} onDecided={load} />
        </div>
      </div>
    </main>
  );
}
