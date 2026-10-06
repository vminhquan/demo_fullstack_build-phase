"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import {
  api,
  ApiError,
  CaseMetadata,
  Decision,
  Grounding,
  TestCase,
  TestCaseDecision,
  TestCaseStatus,
} from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import {
  BackLink,
  DangerBadge,
  downloadBlob,
  ErrorNotice,
  formatDate,
  Loading,
  StatusBadge,
} from "@/shared/ui/components";
import { useConfirm } from "@/shared/ui/modal";
import { backTarget, safeFrom } from "@/shared/ui/back-target";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";
import { bridgeStatus, useBridges } from "@/features/startup/bridge-store";
import { useCarlaDemo } from "@/features/startup/carla-demo";

import { MetadataFields } from "./metadata-fields";
import { XoscPreview } from "./xosc-preview";
import { CaseRunHistory } from "@/features/simulator-runs/run-screens";

type LoadState<T> = { data: T | null; loading: boolean; error: string };
const emptyLoad = <T,>(): LoadState<T> => ({
  data: null,
  loading: true,
  error: "",
});

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}
function parseTags(text: string) {
  return text.split(",").map((tag) => tag.trim()).filter(Boolean);
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
function groundingOf(testCase: TestCase): Grounding | null {
  const grounding = testCase.scenario_input?.grounding;
  return grounding && typeof grounding === "object" && "ego" in grounding ? (grounding as Grounding) : null;
}

export function DashboardScreen() {
  const p = useProjectPath();
  const { session, can } = useSession();
  const carla = useCarlaDemo(session?.active_project?.id);
  const bridges = useBridges(session?.active_project?.id);
  const [counts, setCounts] = useState<{ total: number; approved: number; inReview: number } | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!session) return;
    setError("");
    const countCases = (status?: TestCaseStatus) =>
      api
        .listCases(
          session.access_token,
          new URLSearchParams(status ? { status, page_size: "1" } : { page_size: "1" }),
        )
        .then((page) => page.total);
    try {
      const [total, approved, inReview] = await Promise.all([
        countCases(),
        countCases("APPROVED"),
        countCases("PENDING"),
      ]);
      setCounts({ total, approved, inReview });
    } catch (reason) {
      setError(errorText(reason, "Không thể tải số liệu tổng quan."));
    }
  }, [session]);
  useEffect(() => {
    void load();
  }, [load]);
  const status = bridgeStatus(bridges.bridges, carla.state.source === "default");
  return (
    <main className="main">
      {pageTitle(
        "Tổng quan vận hành",
        "Theo dõi số test case đã sinh và đã duyệt từ dữ liệu Backend.",
        <>
          {can("testcase:create") && (
            <Link className="button primary" href={p("/test-case-builder/create")}><Icon name="plus" size={14} />Tạo kịch bản</Link>
          )}
          <RefreshButton onClick={() => void load()} />
        </>,
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="stats stats-three">
        <Stat
          label="Test case"
          value={counts?.total ?? "—"}
          detail="Test case chưa lưu trữ"
        />
        <Stat
          label="Đã duyệt"
          value={counts?.approved ?? "—"}
          detail="Có trong Test Suite"
        />
        <Stat
          label="Đang chờ duyệt"
          value={counts?.inReview ?? "—"}
          detail="Đang chờ phê duyệt hoặc không phê duyệt"
        />
      </section>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Đi đến công việc</div>
              <div className="panel-subtitle">
                Sinh kịch bản trong Test Case Builder, duyệt ngay trong phiên, bản đã duyệt nằm ở Test Suite.
              </div>
            </div>
          </div>
          <div className="flow-list">
            {[
              { title: "Tạo phiên sinh kịch bản", href: p("/test-case-builder/create"), show: can("testcase:create") },
              { title: "Duyệt kịch bản trong các phiên", href: p("/test-case-builder"), show: true },
              { title: "Xem bản đã duyệt trong Test Suite", href: p("/test-suite"), show: true },
            ].filter((item) => item.show).map((item, index) => (
              <Link key={item.href} href={item.href} className="flow-step">
                <span className="flow-index">{String(index + 1).padStart(2, "0")}</span>
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
              <div className="panel-subtitle">Bridge và CARLA trên máy đã ghép</div>
            </div>
            <span className={`pill carla-pill-${status.tone}`}>{status.label}</span>
          </div>
          <div className="setup-body">
            <p className="muted">{status.detail}</p>
            {(bridges.bridges ?? []).map((item) => (
              <div key={item.connection_uid} className="case-key">
                {item.name} · {item.online ? "Online" : "Offline"} · CARLA {item.carla_reachable ? "đang chạy" : "chưa chạy"}
                {item.last_synced_at ? ` · đồng bộ ${formatDate(item.last_synced_at)}` : ""}
              </div>
            ))}
            <Link className="button" href={p("/start-up")}>Mở Start up</Link>
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

const NAV_FILTERS = new Set(["PENDING", "APPROVED", "REJECTED", "DISCARDED"]);

function metadataOf(testCase: TestCase): CaseMetadata {
  return {
    map_code: testCase.map_code,
    ego_vehicle_code: testCase.ego_vehicle_code,
    adversary_type: testCase.adversary_type,
    environment_code: testCase.environment_code,
    danger_level: testCase.danger_level,
    tag_names: testCase.tags,
  };
}

/** Keyboard shortcuts must not fire while typing or while a dialog is open. */
function shortcutBlocked(event: KeyboardEvent) {
  if (event.metaKey || event.ctrlKey || event.altKey) return true;
  const target = event.target as HTMLElement | null;
  if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return true;
  return !!document.querySelector(".modal-backdrop");
}

function CaseMenu({ testCase, busy, onDiscard, onRestore, onDownload }: {
  testCase: TestCase; busy: boolean; onDiscard: () => void; onRestore: () => void; onDownload: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const items = [
    testCase.xosc_artifact_id && { key: "download", label: "Tải tệp .xosc", run: onDownload, destructive: false },
    testCase.can.includes("restore") && { key: "restore", label: "Khôi phục về Chờ duyệt", run: onRestore, destructive: false },
    testCase.can.includes("discard") && { key: "discard", label: "Loại bỏ test case", run: onDiscard, destructive: true },
  ].filter((item): item is { key: string; label: string; run: () => void; destructive: boolean } => !!item);
  if (!items.length) return null;
  return (
    <div className="card-menu" ref={ref}>
      <button type="button" className="button" aria-label="Tùy chọn khác" aria-haspopup="menu" aria-expanded={open} disabled={busy} onClick={() => setOpen((value) => !value)}>
        <Icon name="more" />
      </button>
      {open && (
        <div className="card-menu-list" role="menu">
          {items.map((item) => (
            <button key={item.key} type="button" role="menuitem" className={`card-menu-item ${item.destructive ? "destructive" : ""}`} onClick={() => { setOpen(false); item.run(); }}>
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function CaseEditForm({ testCase, onSaved, onCancel }: { testCase: TestCase; onSaved: (message: string) => void; onCancel: () => void }) {
  const { session } = useSession();
  const [title, setTitle] = useState(testCase.title);
  const [description, setDescription] = useState(testCase.description ?? "");
  const [metadata, setMetadata] = useState<CaseMetadata>(metadataOf(testCase));
  const [tagsText, setTagsText] = useState(testCase.tags.join(", "));
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const updated = await api.updateCase(session.access_token, testCase.id, {
        expected_revision: testCase.revision,
        title: title.trim(),
        description: description.trim() || null,
        ...metadata,
        tag_names: parseTags(tagsText),
      });
      if (file) await api.uploadXosc(session.access_token, testCase.id, file, updated.revision);
      const changed = file || updated.revision !== testCase.revision;
      onSaved(!changed ? "Không có thay đổi nào." : testCase.status === "PENDING" ? "Đã lưu chỉnh sửa." : "Đã lưu chỉnh sửa · test case quay về Chờ duyệt.");
    } catch (reason) {
      setError(errorText(reason, "Không lưu được chỉnh sửa."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel form-panel" onSubmit={save}>
      <div className="panel-header">
        <div>
          <div className="panel-title">Sửa test case</div>
          <div className="panel-subtitle">Ghi đè trực tiếp, không gọi lại Agent.</div>
        </div>
      </div>
      {testCase.status !== "PENDING" && (
        <div className="notice notice-info">Lưu thay đổi sẽ đưa test case về <b>Chờ duyệt</b> và cần được phê duyệt lại.</div>
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <div className="form-grid">
        <label className="field field-wide">
          Tiêu đề
          <input required maxLength={300} value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="field field-wide">
          Mô tả
          <textarea rows={4} value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
      </div>
      <MetadataFields
        value={metadata}
        onChange={setMetadata}
        tagsText={tagsText}
        onTagsChange={setTagsText}
        catalogSnapshotId={testCase.catalog_snapshot_id}
      />
      <div className="form-grid">
        <label className="field field-wide">
          Thay tệp .xosc (không bắt buộc)
          <input type="file" accept=".xosc,.xml" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </label>
      </div>
      <div className="form-actions">
        <button type="button" className="button" disabled={busy} onClick={onCancel}>Hủy</button>
        <button className="button primary" disabled={busy}>{busy ? "Đang lưu…" : "Lưu chỉnh sửa"}</button>
      </div>
    </form>
  );
}

const DECISION_LABEL: Record<Decision, string> = { APPROVED: "Phê duyệt", REJECTED: "Không phê duyệt" };
type Toast = { message: string; undoId?: number };
// Moving to another case remounts the page ([id] segment), so the toast is handed over here.
let carriedToast: Toast | null = null;
const AUTO_NEXT_KEY = "scenario-forge.review-auto-next";
function readAutoNext() {
  try { return localStorage.getItem(AUTO_NEXT_KEY) !== "0"; } catch { return true; }
}
function saveAutoNext(value: boolean) {
  try { localStorage.setItem(AUTO_NEXT_KEY, value ? "1" : "0"); } catch { /* per-viewer convenience only */ }
}

export function TestCaseDetailScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const from = safeFrom(search.get("from"));
  const back = backTarget(from, p);
  const builderSession = search.get("session");
  const navFilter = search.get("filter") ?? "ALL";
  const { confirm, dialog } = useConfirm();
  const [state, setState] = useState<LoadState<{ testCase: TestCase; decisions: TestCaseDecision[] }>>(emptyLoad());
  // Cases of the same Builder session (and tab) for Trước / Sau.
  const [siblings, setSiblings] = useState<number[]>([]);
  const [editing, setEditing] = useState(false);
  const [autoNext, setAutoNext] = useState(readAutoNext);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(() => {
    const handed = carriedToast;
    carriedToast = null;
    return handed;
  });

  const load = useCallback(async () => {
    if (!session) return;
    setState((old) => ({ ...old, loading: old.data === null, error: "" }));
    try {
      const [testCase, decisions] = await Promise.all([
        api.getCase(session.access_token, params.id),
        api.listCaseDecisions(session.access_token, params.id),
      ]);
      setState({ data: { testCase, decisions }, loading: false, error: "" });
    } catch (reason) {
      setState({ data: null, loading: false, error: errorText(reason, "Không thể tải chi tiết Test Case.") });
    }
  }, [params.id, session]);

  const loadSiblings = useCallback(async () => {
    if (!session || !builderSession) return;
    const query = new URLSearchParams({ builder_session_id: builderSession, page_size: "200", include_discarded: "true" });
    if (NAV_FILTERS.has(navFilter)) query.set("status", navFilter);
    try {
      const page = await api.listCases(session.access_token, query);
      setSiblings(page.items.map((item) => item.id));
    } catch {
      setSiblings([]);
    }
  }, [builderSession, navFilter, session]);

  useEffect(() => { setEditing(false); setError(""); void load(); }, [load]);
  useEffect(() => { void loadSiblings(); }, [loadSiblings]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), toast.undoId ? 5000 : 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const currentId = Number(params.id);
  const position = siblings.indexOf(currentId);
  const go = useCallback((id: number | undefined) => {
    if (!id) return;
    const query = new URLSearchParams(search.toString());
    router.replace(`${p(`/test-cases/${id}`)}?${query.toString()}`);
  }, [p, router, search]);
  const prevId = position > 0 ? siblings[position - 1] : undefined;
  const nextId = position >= 0 && position < siblings.length - 1 ? siblings[position + 1] : undefined;

  const testCase = state.data?.testCase ?? null;
  const canDecide = !!testCase && testCase.status === "PENDING" && testCase.can.includes("decide");
  const canEdit = !!testCase?.can.includes("edit");

  const decide = useCallback(async (decision: Decision) => {
    if (!session || !testCase || busy) return;
    setBusy(true);
    setError("");
    // Pick the next case from the list as it was before the decision (the tab may drop this case).
    const target = nextId;
    try {
      await api.decideCase(session.access_token, testCase.id, decision, testCase.revision);
      const done = { message: `${DECISION_LABEL[decision]} ${testCase.case_key}`, undoId: testCase.id };
      if (autoNext && target) {
        carriedToast = done;
        go(target);
      } else {
        setToast(done);
        await Promise.all([load(), loadSiblings()]);
      }
    } catch (reason) {
      setError(errorText(reason, "Không lưu được quyết định duyệt."));
      await load();
    } finally {
      setBusy(false);
    }
  }, [autoNext, busy, go, load, loadSiblings, nextId, session, testCase]);

  async function undo(id: number) {
    if (!session) return;
    try {
      await api.undoDecision(session.access_token, id);
      const done = { message: "Đã hoàn tác quyết định." };
      if (id !== currentId) {
        carriedToast = done;
        go(id);
      } else {
        setToast(done);
        await Promise.all([load(), loadSiblings()]);
      }
    } catch (reason) {
      setToast({ message: errorText(reason, "Không hoàn tác được.") });
    }
  }

  async function lifecycle(kind: "discard" | "restore") {
    if (!session || !testCase) return;
    if (kind === "discard") {
      const ok = await confirm({
        title: "Loại bỏ test case?",
        message: <>Test case <b>{testCase.case_key}</b> sẽ bị ẩn khỏi hàng chờ duyệt. Bạn có thể khôi phục lại từ menu ⋯ khi nó chưa được đưa vào chạy.</>,
        confirmLabel: "Loại bỏ",
      });
      if (!ok) return;
    }
    setBusy(true);
    setError("");
    try {
      if (kind === "discard") await api.discardCase(session.access_token, testCase.id);
      else await api.restoreCase(session.access_token, testCase.id);
      setToast({ message: kind === "discard" ? "Đã loại bỏ test case." : "Đã khôi phục về Chờ duyệt." });
      await Promise.all([load(), loadSiblings()]);
    } catch (reason) {
      setError(errorText(reason, "Không thực hiện được thao tác."));
    } finally {
      setBusy(false);
    }
  }

  async function downloadXosc() {
    if (!session || !testCase) return;
    try {
      downloadBlob(await api.downloadXosc(session.access_token, testCase.id), `${testCase.case_key}-r${testCase.revision}.xosc`);
    } catch (reason) {
      setError(errorText(reason, "Không thể tải XOSC."));
    }
  }

  // A = phê duyệt, R = không phê duyệt, J / K = sau / trước, E = sửa.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (shortcutBlocked(event) || editing) return;
      const key = event.key.toLowerCase();
      if (key === "a" && canDecide) { event.preventDefault(); void decide("APPROVED"); }
      else if (key === "r" && canDecide) { event.preventDefault(); void decide("REJECTED"); }
      else if (key === "j") go(nextId);
      else if (key === "k") go(prevId);
      else if (key === "e" && canEdit) { event.preventDefault(); setEditing(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canDecide, canEdit, decide, editing, go, nextId, prevId]);

  if (state.loading) return <main className="main"><Loading /></main>;
  if (!state.data || !testCase)
    return (
      <main className="main">
        <BackLink href={back.href} />{" "}
        <ErrorNotice>{state.error || "Không tìm thấy Test Case."}</ErrorNotice>
      </main>
    );
  const { decisions } = state.data;
  const you = session?.user.id;
  const who = (id: number | null, name: string | null) => (id === null ? "—" : id === you ? "Bạn" : name ?? `#${id}`);

  return (
    <main className="main">
      {pageTitle(
        testCase.title,
        testCase.description ?? "Chưa có mô tả.",
        <>
          {canEdit && !editing && <button type="button" className="button" title="Phím tắt: E" onClick={() => setEditing(true)}><Icon name="edit" size={14} />Sửa</button>}
          <CaseMenu testCase={testCase} busy={busy} onDiscard={() => void lifecycle("discard")} onRestore={() => void lifecycle("restore")} onDownload={() => void downloadXosc()} />
        </>,
      )}
      <BackLink href={back.href}>{back.label}</BackLink>
      {dialog}

      <section className="review-bar" aria-label="Duyệt nhanh">
        <div className="review-bar-status">
          <StatusBadge status={testCase.status} />
          <span className="session-code">{testCase.case_key}</span>
          <span className="muted">lần sửa #{testCase.revision}</span>
        </div>
        {canDecide && (
          <div className="review-bar-actions">
            <button type="button" className="button primary" disabled={busy} title="Phím tắt: A" onClick={() => void decide("APPROVED")}><Icon name="tick" size={14} />Phê duyệt <kbd>A</kbd></button>
            <button type="button" className="button danger-button" disabled={busy} title="Phím tắt: R" onClick={() => void decide("REJECTED")}><Icon name="x" size={14} />Không phê duyệt <kbd>R</kbd></button>
          </div>
        )}
        {!canDecide && testCase.status === "PENDING" && (
          <span className="muted">{testCase.can.length ? "Bạn không thể tự duyệt test case mình tạo hoặc sửa gần nhất." : "Bạn chỉ có quyền xem."}</span>
        )}
        {siblings.length > 0 && (
          <div className="review-bar-nav">
            <label className="review-bar-auto"><input type="checkbox" checked={autoNext} onChange={(event) => { setAutoNext(event.target.checked); saveAutoNext(event.target.checked); }} />Tự chuyển tiếp</label>
            <button type="button" className="button" disabled={!prevId} title="Phím tắt: K" onClick={() => go(prevId)}>← Trước</button>
            <span className="muted">{position >= 0 ? `${position + 1}/${siblings.length}` : `—/${siblings.length}`}</span>
            <button type="button" className="button" disabled={!nextId} title="Phím tắt: J" onClick={() => go(nextId)}>Sau →</button>
          </div>
        )}
      </section>
      {toast && (
        <div className="notice notice-info review-toast" role="status">
          {toast.message}
          {toast.undoId && <button type="button" className="text-button" onClick={() => void undo(toast.undoId!)}>Hoàn tác</button>}
        </div>
      )}
      {testCase.locked_at && (
        <div className="notice notice-info">🔒 Đã đưa vào chạy mô phỏng lúc {formatDate(testCase.locked_at)} — khóa chỉnh sửa.</div>
      )}
      {testCase.status === "DISCARDED" && (
        <div className="notice notice-info">Test case đã bị loại bỏ{testCase.can.includes("restore") ? ". Có thể khôi phục từ menu ⋯ góc phải." : "."}</div>
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}

      {editing ? (
        <CaseEditForm
          key={testCase.revision}
          testCase={testCase}
          onCancel={() => setEditing(false)}
          onSaved={(message) => { setEditing(false); setToast({ message }); void load(); void loadSiblings(); }}
        />
      ) : (
        <section className="detail-grid">
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Thông tin kịch bản</div>
                <div className="panel-subtitle">{testCase.map_code} · {testCase.environment_code}</div>
              </div>
              <DangerBadge level={testCase.danger_level} />
            </div>
            <dl className="details">
              <dt>Xe ego</dt><dd>{testCase.ego_vehicle_code}</dd>
              <dt>Tác nhân</dt><dd>{testCase.adversary_type}</dd>
              <dt>Môi trường</dt><dd>{testCase.environment_code}</dd>
              <dt>Tags</dt><dd>{testCase.tags.length ? testCase.tags.join(", ") : "—"}</dd>
            </dl>
          </div>
          <div className="panel">
            <div className="panel-header">
              <div>
                <div className="panel-title">Nguồn gốc</div>
                <div className="panel-subtitle">
                  {testCase.builder_session_id
                    ? <Link className="link" href={p(`/test-case-builder/${testCase.builder_session_id}`)}>Phiên Builder #{testCase.builder_session_id} · biến thể {testCase.builder_variant_no}</Link>
                    : "Tạo thủ công"}
                </div>
              </div>
            </div>
            <dl className="details">
              <dt>Người tạo</dt><dd>{who(testCase.created_by, testCase.created_by_name)} · {formatDate(testCase.created_at)}</dd>
              <dt>Sửa gần nhất</dt><dd>{testCase.last_edited_by ? `${who(testCase.last_edited_by, testCase.last_edited_by_name)} · ${formatDate(testCase.last_edited_at)}` : "—"}</dd>
              <dt>Quyết định</dt><dd>{testCase.decided_by ? `${who(testCase.decided_by, testCase.decided_by_name)} · ${formatDate(testCase.decided_at)}` : "—"}</dd>
            </dl>
          </div>
        </section>
      )}

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Preview kịch bản</div>
            <div className="panel-subtitle">
              {testCase.map_code} · xe ego {testCase.ego_vehicle_code} · tác nhân {testCase.adversary_type} · {testCase.environment_code}
            </div>
          </div>
        </div>
        {testCase.xosc_artifact_id ? (
          <XoscPreview key={testCase.xosc_sha256 ?? testCase.xosc_artifact_id} caseId={testCase.id} fileName={`${testCase.case_key}-r${testCase.revision}.xosc`} grounding={groundingOf(testCase)} />
        ) : (
          <div className="inline-note">Test case chưa có tệp .xosc.{canEdit ? " Bấm “Sửa” để tải tệp lên." : ""}</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Lịch sử duyệt</div>
            <div className="panel-subtitle">Mỗi quyết định ghi lại lần sửa đã được xem.</div>
          </div>
        </div>
        {decisions.length === 0 ? <div className="inline-note">Chưa có quyết định nào.</div> : (
          <table className="table">
            <thead><tr><th>Quyết định</th><th>Lần sửa</th><th>Người duyệt</th><th>Thời điểm</th></tr></thead>
            <tbody>
              {decisions.map((item) => (
                <tr key={item.id} className={item.undone_at ? "muted" : ""}>
                  <td>
                    <span className={`badge ${item.decision}`}>{DECISION_LABEL[item.decision]}</span>
                    {item.undone_at && <> · đã hoàn tác</>}
                    {!item.undone_at && item.revision !== testCase.revision && <> · trước khi sửa</>}
                  </td>
                  <td>#{item.revision}</td>
                  <td>{who(item.decided_by, item.decided_by_name)}</td>
                  <td>{formatDate(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <CaseRunHistory caseId={testCase.id} />
    </main>
  );
}

