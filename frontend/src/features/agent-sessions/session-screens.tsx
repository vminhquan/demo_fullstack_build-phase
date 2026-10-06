"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, BatchItemResult, BuilderError, BuilderSession, BuilderSessionCreate, BuilderSessionDetail, BuilderSessionStatus, Decision, TestCase } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { BackLink, DangerBadge, Empty, ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { withFrom } from "@/shared/ui/back-target";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";

import { SessionInputForm, SessionInputView } from "./composer";

const POLL_MS = 4000;

type ScenarioStatus = "GENERATING" | "PENDING" | "APPROVED" | "REJECTED" | "DISCARDED" | "FAILED";
const scenarioStatusLabel: Record<ScenarioStatus, string> = {
  GENERATING: "Đang sinh",
  PENDING: "Chờ duyệt",
  APPROVED: "Đã duyệt",
  REJECTED: "Không phê duyệt",
  DISCARDED: "Đã loại bỏ",
  FAILED: "Sinh lỗi",
};
// Badge palette: grey = generating/discarded, blue = waiting, green = approved, red = rejected/failed.
const SCENARIO_BADGE: Record<ScenarioStatus, string> = { GENERATING: "DRAFT", PENDING: "PENDING", APPROVED: "APPROVED", REJECTED: "REJECTED", DISCARDED: "DISCARDED", FAILED: "REJECTED" };
const BATCH_ERRORS: Record<string, string> = {
  NOT_PENDING: "không còn chờ duyệt",
  NOT_FOUND: "không tìm thấy",
  FORBIDDEN: "không đủ quyền (tự duyệt / không phải người tạo)",
  TEST_CASE_LOCKED: "đã đưa vào chạy",
};

/** "Đã phê duyệt 8 · bỏ qua 2 (…)" from a batch response. */
function batchSummary(verb: string, results: BatchItemResult[]) {
  const done = results.filter((item) => !item.error).length;
  const skipped = results.filter((item) => item.error);
  const reasons = [...new Set(skipped.map((item) => BATCH_ERRORS[item.error ?? ""] ?? item.message ?? item.error))].join("; ");
  return `${verb} ${done}${skipped.length ? ` · bỏ qua ${skipped.length} (${reasons})` : ""}`;
}
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
    if (testCase) return { no, map_code: testCase.map_code, status: testCase.status, testCase };
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

function ScenarioRow({ sessionId, item, filter, selected, onToggle, onDecided }: {
  sessionId: number; item: ScenarioItem; filter: QueueFilter; selected: boolean; onToggle: () => void;
  onDecided: (message: string, undo?: { id: number; title: string }) => void;
}) {
  const p = useProjectPath();
  const { session } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const testCase = item.testCase;
  const canDecide = !!testCase?.can.includes("decide");
  const selectable = !!testCase && (canDecide || testCase.can.includes("discard"));

  async function decide(decision: Decision) {
    if (!session || !testCase) return;
    setBusy(true);
    setError("");
    try {
      await api.decideCase(session.access_token, testCase.id, decision, testCase.revision);
      onDecided(`${decision === "APPROVED" ? "Đã phê duyệt" : "Không phê duyệt"} ${testCase.case_key}`, { id: testCase.id, title: testCase.case_key });
    } catch (reason) {
      setError(errorText(reason, "Không lưu được quyết định duyệt."));
    } finally {
      setBusy(false);
    }
  }

  const title = testCase?.title ?? `Biến thể #${item.no}`;
  // The detail page walks the same list (session + tab) with Trước / Sau.
  const detailHref = testCase
    ? withFrom(p(`/test-cases/${testCase.id}?session=${sessionId}&filter=${filter}`), p(`/test-case-builder/${sessionId}`))
    : "";
  return (
    <li className={`session-scenario ${selected ? "is-selected" : ""}`}>
      <div className="version-card-head">
        <div className="session-scenario-main">
          {selectable && <input type="checkbox" className="session-select" checked={selected} onChange={onToggle} aria-label={`Chọn ${title}`} />}
          <div>
            <div className="version-title">
              {testCase ? <Link className="link" href={detailHref} title="Mở chi tiết test case">{title}</Link> : title}
            </div>
            <div className="muted">
              <span className="session-code">{testCase ? testCase.case_key : `#${item.no}`}</span> · {item.map_code}
              {testCase?.locked_at && <> · <span title="Đã đưa vào chạy mô phỏng, không sửa được nữa">🔒 đã khóa</span></>}
            </div>
          </div>
        </div>
        <div className="session-scenario-actions">
          {item.status === "PENDING" && canDecide && <>
            <button type="button" className="icon-button session-reject" title="Không phê duyệt" aria-label={`Không phê duyệt ${title}`} disabled={busy} onClick={() => void decide("REJECTED")}><Icon name="x" /></button>
            <button type="button" className="icon-button session-approve" title="Phê duyệt" aria-label={`Phê duyệt ${title}`} disabled={busy} onClick={() => void decide("APPROVED")}><Icon name="tick" /></button>
          </>}
          <ScenarioBadge status={item.status} />
        </div>
      </div>
      {item.status === "GENERATING" && <div className="muted"><span className="spinner" />Agent đang sinh…</div>}
      {item.error && <div className="error-code">{item.error.message}</div>}
      {error && <div className="error-code">{error}</div>}
      {testCase && (
        <div className="metadata-summary">
          <span><DangerBadge level={testCase.danger_level} /></span>
          <span><b>Xe ego</b> {testCase.ego_vehicle_code}</span>
          <span><b>Tác nhân</b> {testCase.adversary_type}</span>
          <span><b>Môi trường</b> {testCase.environment_code}</span>
          {testCase.revision > 1 && <span><b>Đã sửa</b> {testCase.revision - 1} lần</span>}
        </div>
      )}
    </li>
  );
}

function ScenarioReviewList({ detail, onChanged }: { detail: BuilderSessionDetail; onChanged: () => void }) {
  const { session } = useSession();
  const [filter, setFilter] = useState<QueueFilter>("ALL");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ message: string; undo?: { id: number; title: string } } | null>(null);
  const items = scenarioItems(detail);
  const count = (status: ScenarioStatus) => items.filter((item) => item.status === status).length;
  const tabs: { key: QueueFilter; label: string; count: number }[] = [
    { key: "ALL", label: "Tất cả", count: items.length },
    { key: "PENDING", label: "Chờ duyệt", count: count("PENDING") },
    { key: "APPROVED", label: "Đã duyệt", count: count("APPROVED") },
    { key: "REJECTED", label: "Không phê duyệt", count: count("REJECTED") },
    { key: "DISCARDED", label: "Đã loại bỏ", count: count("DISCARDED") },
  ];
  const visible = filter === "ALL" ? items : items.filter((item) => item.status === filter);
  const cases = new Map(items.filter((item) => item.testCase).map((item) => [item.testCase!.id, item.testCase!]));
  const chosen = [...selected].map((id) => cases.get(id)).filter((item): item is TestCase => !!item);
  const decidable = chosen.filter((item) => item.can.includes("decide"));
  const discardable = chosen.filter((item) => item.can.includes("discard"));
  const selectableVisible = visible.map((item) => item.testCase).filter((item): item is TestCase => !!item && (item.can.includes("decide") || item.can.includes("discard")));

  // The notice (with its undo button) disappears after a few seconds, like a toast.
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), notice.undo ? 5000 : 8000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function toggle(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function batch(kind: Decision | "DISCARD") {
    if (!session) return;
    setBusy(true);
    try {
      const result = kind === "DISCARD"
        ? await api.discardCases(session.access_token, discardable.map((item) => item.id))
        : await api.decideCases(session.access_token, decidable.map((item) => item.id), kind);
      const verb = kind === "DISCARD" ? "Đã loại bỏ" : kind === "APPROVED" ? "Đã phê duyệt" : "Không phê duyệt";
      setNotice({ message: batchSummary(verb, result.results) });
      setSelected(new Set());
      onChanged();
    } catch (reason) {
      setNotice({ message: errorText(reason, "Không thực hiện được thao tác hàng loạt.") });
    } finally {
      setBusy(false);
    }
  }

  async function undo(target: { id: number; title: string }) {
    if (!session) return;
    try {
      await api.undoDecision(session.access_token, target.id);
      setNotice({ message: `Đã hoàn tác quyết định cho ${target.title}` });
      onChanged();
    } catch (reason) {
      setNotice({ message: errorText(reason, "Không hoàn tác được.") });
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Kịch bản đã sinh</div>
          <div className="panel-subtitle">
            {count("GENERATING") > 0 ? `Agent đang sinh ${count("GENERATING")}/${items.length}… · ` : ""}
            {count("PENDING")} chờ duyệt · {count("APPROVED")} đã duyệt · {count("REJECTED")} không phê duyệt · {count("DISCARDED") + count("FAILED")} bị loại
          </div>
        </div>
        <SessionBadge status={detail.status} />
      </div>
      <div className="tabs session-tabs" role="tablist">
        {tabs.map((tab) => (
          <button key={tab.key} type="button" role="tab" aria-selected={filter === tab.key} className={filter === tab.key ? "active" : ""} onClick={() => { setFilter(tab.key); setSelected(new Set()); }}>
            {tab.label} <span className="muted">{tab.count}</span>
          </button>
        ))}
      </div>
      {notice && (
        <div className="notice notice-info session-notice" role="status">
          {notice.message}
          {notice.undo && <button type="button" className="text-button" onClick={() => void undo(notice.undo!)}>Hoàn tác</button>}
        </div>
      )}
      {selectableVisible.length > 0 && (
        <div className="session-bulk">
          <label className="session-bulk-all">
            <input
              type="checkbox"
              checked={selectableVisible.every((item) => selected.has(item.id))}
              onChange={(event) => setSelected(event.target.checked ? new Set(selectableVisible.map((item) => item.id)) : new Set())}
            />
            {chosen.length ? `Đã chọn ${chosen.length}` : "Chọn tất cả"}
          </label>
          {chosen.length > 0 && <>
            {decidable.length > 0 && <>
              <button type="button" className="button primary" disabled={busy} onClick={() => void batch("APPROVED")}>Phê duyệt ({decidable.length})</button>
              <button type="button" className="button danger-button" disabled={busy} onClick={() => void batch("REJECTED")}>Không phê duyệt ({decidable.length})</button>
            </>}
            {discardable.length > 0 && <button type="button" className="button" disabled={busy} onClick={() => void batch("DISCARD")}>Loại bỏ ({discardable.length})</button>}
            <button type="button" className="text-button" onClick={() => setSelected(new Set())}>Bỏ chọn</button>
          </>}
        </div>
      )}
      {visible.length === 0 ? <Empty>Không có kịch bản ở trạng thái này.</Empty> : (
        <ul className="session-queue">
          {visible.map((item) => (
            <ScenarioRow
              key={item.no}
              sessionId={detail.id}
              item={item}
              filter={filter}
              selected={!!item.testCase && selected.has(item.testCase.id)}
              onToggle={() => item.testCase && toggle(item.testCase.id)}
              onDecided={(message, undoTarget) => { setNotice({ message, undo: undoTarget }); onChanged(); }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/* /test-case-builder/[id] — left: the inputs of this session (read-only), right: generated test cases to review. */
export function AgentSessionDetailScreen() {
  const p = useProjectPath();
  const { session } = useSession();
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
          <ScenarioReviewList detail={detail} onChanged={load} />
        </div>
      </div>
    </main>
  );
}
