"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { api, ApiError, TestCase } from "@/lib/api";
import { buildZip, ZipEntry } from "@/lib/zip";
import { createSimRun, useSimRuns } from "@/features/simulator-runs/run-store";
import { Bridge, useBridges } from "@/features/startup/bridge-store";
import { labels, useSession } from "@/shared/auth/session-context";
import { DangerBadge, downloadBlob, Empty, ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { withFrom } from "@/shared/ui/back-target";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";

const PAGE_SIZE = 20;
const FILTER_KEYS = ["q", "map_code", "adversary_type", "environment_code", "danger_level", "tag"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
type Filters = Record<FilterKey, string>;

function readFilters(params: URLSearchParams): Filters {
  return Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) ?? ""])) as Filters;
}
function toQuery(filters: Filters, page?: number) {
  const query = new URLSearchParams();
  FILTER_KEYS.forEach((key) => {
    const value = filters[key].trim();
    if (value) query.set(key, value);
  });
  if (page && page > 1) query.set("page", String(page));
  return query;
}

/** Test cases whose latest version was approved in the Test Case Builder review flow. */
export function ApprovedTestSuiteScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const runs = useSimRuns(session?.active_project?.id);
  const { bridges } = useBridges(session?.active_project?.id);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState("");
  // Number of Simulator Runner runs that include each case.
  const runCounts = useMemo(() => {
    const counts = new Map<number, number>();
    runs.forEach((run) => new Set(run.cases.map((item) => item.case_id)).forEach((id) => counts.set(id, (counts.get(id) ?? 0) + 1)));
    return counts;
  }, [runs]);
  const router = useRouter();
  const urlParams = useSearchParams();
  const urlQuery = urlParams.toString();
  const filters = useMemo(() => readFilters(new URLSearchParams(urlQuery)), [urlQuery]);
  const page = Math.max(1, Number.parseInt(urlParams.get("page") ?? "1", 10) || 1);
  const [draft, setDraft] = useState(filters);
  const [items, setItems] = useState<TestCase[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // Selected cases survive paging and filtering, so a run can mix cases from several pages.
  const [selected, setSelected] = useState<Map<number, TestCase>>(new Map());
  // Only the newest request may write state, so a slow response cannot overwrite a newer filter.
  const requestId = useRef(0);

  useEffect(() => setDraft(filters), [filters]);

  const load = useCallback(async () => {
    if (!session) return;
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const query = toQuery(filters);
      query.set("approved_only", "true");
      query.set("page", String(page));
      query.set("page_size", String(PAGE_SIZE));
      const response = await api.listCases(session.access_token, query);
      if (id !== requestId.current) return;
      setItems(response.items);
      setTotal(response.total);
    } catch (reason) {
      if (id !== requestId.current) return;
      setItems([]);
      setTotal(0);
      setError(reason instanceof ApiError ? reason.message : "Không thể tải Test Suite.");
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [filters, page, session]);

  useEffect(() => {
    void load();
  }, [load]);

  function apply(event: FormEvent) {
    event.preventDefault();
    router.push(p(`/test-suite?${toQuery(draft).toString()}`));
  }
  function clear() {
    setDraft(readFilters(new URLSearchParams()));
    router.push(p("/test-suite"));
  }
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  // Detail pages link back to this exact list (filters + page).
  const from = urlQuery ? p(`/test-suite?${urlQuery}`) : p("/test-suite");
  const allOnPage = items.length > 0 && items.every((item) => selected.has(item.id));
  function toggle(item: TestCase) {
    setSelected((previous) => {
      const next = new Map(previous);
      if (next.has(item.id)) next.delete(item.id);
      else next.set(item.id, item);
      return next;
    });
  }
  function togglePage() {
    setSelected((previous) => {
      const next = new Map(previous);
      items.forEach((item) => (allOnPage ? next.delete(item.id) : next.set(item.id, item)));
      return next;
    });
  }
  function startRun(bridge: Bridge) {
    const project = session?.active_project;
    if (!session || !project || selected.size === 0) return;
    const id = createSimRun(project.id, session.user.display_name || session.user.email, [...selected.values()], { id: bridge.connection_uid, name: bridge.name });
    router.push(p(`/simulator-runs/${id}`));
  }
  /** Bundles the .xosc of the latest approved version of every selected case into one ZIP. */
  async function exportXoscZip() {
    if (!session || selected.size === 0) return;
    setExporting(true);
    setError("");
    setNotice("");
    const encoder = new TextEncoder();
    const entries: ZipEntry[] = [];
    const missing: string[] = [];
    const used = new Set<string>();
    for (const item of selected.values()) {
      const version = item.latest_version;
      if (!version?.xosc_artifact_id) {
        missing.push(item.case_key);
        continue;
      }
      try {
        const blob = await api.downloadXosc(session.access_token, version.id);
        let name = `${item.case_key}_v${version.version_no}.xosc`.replace(/[\\/:*?"<>|\s]+/g, "_");
        while (used.has(name)) name = name.replace(/\.xosc$/, "_1.xosc");
        used.add(name);
        entries.push({ name, data: new Uint8Array(await blob.arrayBuffer()) });
      } catch {
        missing.push(item.case_key);
      }
    }
    if (entries.length) {
      const manifest = entries.map((entry) => entry.name).join("\n");
      entries.push({ name: "MANIFEST.txt", data: encoder.encode(`Scenario Forge – ${entries.length} file .xosc\nProject: ${session.active_project?.name ?? ""}\nXuất lúc: ${new Date().toISOString()}\n\n${manifest}\n${missing.length ? `\nKhông có .xosc: ${missing.join(", ")}\n` : ""}`) });
      const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
      downloadBlob(buildZip(entries), `xosc-test-suite-${stamp}.zip`);
    }
    if (!entries.length) setError("Không test case nào đã chọn có file .xosc để tải.");
    else if (missing.length) setNotice(`Đã tải ${entries.length - 1} file .xosc. Bỏ qua ${missing.length} test case không có .xosc: ${missing.join(", ")}.`);
    setExporting(false);
  }
  const field = (key: FilterKey) => ({
    value: draft[key],
    onChange: (event: { target: { value: string } }) => setDraft({ ...draft, [key]: event.target.value }),
  });

  return (
    <main className="main">
      <section className="heading">
        <div>
          <h1>Test Suite</h1>
          <p>Kho lưu trữ các test case đã được duyệt trong Test Case Builder.</p>
        </div>
        <div className="heading-actions">
          {selected.size > 0 && (
            <button className="button" onClick={() => setSelected(new Map())}>
              Bỏ chọn ({selected.size})
            </button>
          )}
          <RunnerMenu
            count={selected.size}
            bridges={bridges}
            exporting={exporting}
            startUpHref={p("/start-up")}
            onRun={startRun}
            onExport={() => void exportXoscZip()}
          />
          <button className="button" onClick={() => void load()}>
            <Icon name="refresh" size={14} />
            Làm mới
          </button>
        </div>
      </section>
      <section className="panel">
        <form className="filters filters-grid" onSubmit={apply}>
          <input className="search filter-wide" {...field("q")} placeholder="Tìm theo tiêu đề hoặc mô tả" />
          <input className="filter" {...field("map_code")} placeholder="Map, ví dụ Town05" />
          <input className="filter" {...field("adversary_type")} placeholder="Tác nhân, ví dụ pedestrian" />
          <input className="filter" {...field("environment_code")} placeholder="Môi trường, ví dụ heavy_rain" />
          <select className="filter" {...field("danger_level")}>
            <option value="">Mọi mức nguy hiểm</option>
            {Object.entries(labels.danger).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <input className="filter" {...field("tag")} placeholder="Thẻ" />
          <div className="filter-actions">
            <button className="button" type="button" onClick={clear}>Xóa bộ lọc</button>
            <button className="button primary" type="submit">Tìm kiếm</button>
          </div>
        </form>
        {error && <ErrorNotice>{error}</ErrorNotice>}
        {notice && <div className="inline-note">{notice}</div>}
        <div className="panel-header">
          <div>
            <div className="panel-title">{total} test case đã duyệt</div>
            <div className="panel-subtitle">Mỗi test case hiển thị version đã duyệt gần nhất; version nháp / chờ duyệt mới hơn không làm ẩn test case. Tích chọn rồi bấm “Simulator Runner” để chạy trên một Bridge hoặc tải ZIP .xosc.</div>
          </div>
        </div>
        {loading ? (
          <Loading />
        ) : !items.length ? (
          <Empty>Chưa có test case nào được duyệt. Hãy tạo trong Test Case Builder và gửi duyệt.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" checked={allOnPage} onChange={togglePage} aria-label="Chọn tất cả test case trong trang" />
                  </th>
                  <th>Kịch bản</th>
                  <th>Map / môi trường</th>
                  <th>Tác nhân</th>
                  <th>Nguy hiểm</th>
                  <th>Ngày duyệt</th>
                  <th>Số lần chạy</th>
                  <th>Thẻ</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const version = item.latest_version;
                  return (
                    <tr
                      key={item.id}
                      className="row-link"
                      title="Bấm để mở chi tiết test case ở tab mới"
                      onClick={(event) => {
                        if ((event.target as HTMLElement).closest("a, button, input, .col-check")) return;
                        window.open(withFrom(p(`/test-cases/${item.id}`), from), "_blank", "noopener");
                      }}
                    >
                      <td className="col-check" onClick={() => toggle(item)}>
                        <input type="checkbox" checked={selected.has(item.id)} onChange={() => toggle(item)} onClick={(event) => event.stopPropagation()} aria-label={`Chọn ${item.title}`} />
                      </td>
                      <td>
                        <Link className="case-title link" href={withFrom(p(`/test-cases/${item.id}`), from)} target="_blank" rel="noopener noreferrer">{item.title}</Link>
                        <div className="case-key">
                          {item.case_key}
                          {version ? ` · v${version.version_no}` : ""}
                        </div>
                        {version && (
                          <Link className="case-key link" href={withFrom(p(`/test-cases/${item.id}/versions/${version.id}`), from)} target="_blank" rel="noopener noreferrer">
                            Xem version
                          </Link>
                        )}
                      </td>
                      <td>
                        <div>{version?.map_code ?? "—"}</div>
                        <div className="muted">{version?.environment_code}</div>
                      </td>
                      <td>{version?.adversary_type ?? "—"}</td>
                      <td>{version ? <DangerBadge level={version.danger_level} /> : "—"}</td>
                      <td>{formatDate(version?.decided_at ?? null)}</td>
                      <td>{runCounts.get(item.id) ? <strong>{runCounts.get(item.id)} phiên</strong> : <span className="muted">Chưa chạy</span>}</td>
                      <td>
                        {version?.tags.map((tag) => (
                          <span className="tag" key={tag}>{tag}</span>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="pagination">
          <button className="button" disabled={page <= 1} onClick={() => router.push(p(`/test-suite?${toQuery(filters, page - 1)}`))}>
            ← Trang trước
          </button>
          <span>Trang {page} / {pages}</span>
          <button className="button" disabled={page >= pages} onClick={() => router.push(p(`/test-suite?${toQuery(filters, page + 1)}`))}>
            Trang sau →
          </button>
        </div>
      </section>
    </main>
  );
}

/** "Simulator Runner" dropdown: run on one connected Scenario Forge Bridge, or download every selected .xosc as a ZIP. */
function RunnerMenu({ count, bridges, exporting, startUpHref, onRun, onExport }: {
  count: number;
  bridges: Bridge[];
  exporting: boolean;
  startUpHref: string;
  onRun: (bridge: Bridge) => void;
  onExport: () => void;
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

  return (
    <div className="runner-menu" ref={ref}>
      <button
        className="button primary"
        disabled={count === 0 || exporting}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        title="Chọn cách chạy các test case đã tích"
      >
        <Icon name="activity" size={14} />
        {exporting ? "Đang đóng gói .xosc…" : `Simulator Runner${count > 0 ? ` (${count})` : ""}`}
        <Icon name="chevronDown" size={14} />
      </button>
      {open && (
        <div className="card-menu-list" role="menu">
          <div className="card-menu-label">Chạy với Scenario Forge Bridge</div>
          {bridges.length ? (
            bridges.map((bridge) => (
              <button
                key={bridge.connection_uid}
                type="button"
                role="menuitem"
                className="card-menu-item"
                disabled={!bridge.online}
                title={bridge.online ? `Chạy ${count} test case trên ${bridge.name} (${bridge.connection_uid})` : "Bridge đang offline"}
                onClick={() => { setOpen(false); onRun(bridge); }}
              >
                <Icon name="activity" size={14} />
                {bridge.name}
                <span className="case-key">{!bridge.online ? "Offline" : bridge.carla_reachable ? "CARLA sẵn sàng" : "Chưa thấy CARLA"}</span>
              </button>
            ))
          ) : (
            <Link className="card-menu-item" role="menuitem" href={startUpHref}>
              <Icon name="plus" size={14} />Chưa có Bridge – kết nối ở Start up
            </Link>
          )}
          <div className="card-menu-sep" />
          <button type="button" role="menuitem" className="card-menu-item" onClick={() => { setOpen(false); onExport(); }}>
            <Icon name="file" size={14} />Tải ZIP toàn bộ .xosc ({count})
          </button>
        </div>
      )}
    </div>
  );
}
