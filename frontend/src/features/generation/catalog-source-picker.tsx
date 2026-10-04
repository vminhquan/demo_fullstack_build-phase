"use client";

import Link from "next/link";
import { DragEvent, FormEvent, useState } from "react";

import { api, ApiError, CatalogSnapshot } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice, formatDate } from "@/shared/ui/components";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";
import { Modal } from "@/shared/ui/modal";

export function snapshotTitle(item: CatalogSnapshot) {
  return `${item.map_name} · CARLA ${item.carla_version}`;
}
export function snapshotOrigin(item: CatalogSnapshot) {
  if (item.is_default) return "Mặc định của hệ thống";
  return item.source === "WORKER" ? "Đồng bộ qua worker" : `Đã import${item.label && item.label !== item.map_name ? ` · ${item.label}` : ""}`;
}
export function snapshotStats(item: CatalogSnapshot) {
  return `${item.spawn_point_count} điểm xuất phát · ${item.waypoint_count} điểm làn đường · ${item.vehicle_count} loại xe · ${item.walker_count} người đi bộ`;
}

/** Upload of the user's own CARLA data: the zipped export folder or a catalog .json. */
export function ImportCatalogDialog({ onClose, onImported }: { onClose: () => void; onImported: (snapshot: CatalogSnapshot) => void }) {
  const { session } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ snapshot: CatalogSnapshot; created: boolean } | null>(null);

  function pick(next: File | null | undefined) {
    setError("");
    if (!next) return;
    if (!/\.(zip|json)$/i.test(next.name)) {
      setError("Chỉ nhận tệp .zip (thư mục export CARLA đã nén) hoặc .json (catalog).");
      return;
    }
    setFile(next);
  }
  function drop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    pick(event.dataTransfer.files?.[0]);
  }
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!session || !file) return;
    setBusy(true);
    setError("");
    try {
      setDone(await api.uploadCatalogFile(session.access_token, file, label.trim()));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không import được dữ liệu CARLA.");
    } finally {
      setBusy(false);
    }
  }

  const footer = done
    ? <><span /><button type="button" className="button primary" onClick={() => { onImported(done.snapshot); onClose(); }}>Dùng dữ liệu này</button></>
    : <><button type="button" className="button" onClick={onClose}>Hủy</button><button type="button" className="button primary" disabled={!file || busy} onClick={() => void submit()}>{busy ? <><span className="spinner" />Đang import…</> : "Import"}</button></>;

  return (
    <Modal title="Import dữ liệu CARLA của bạn" onClose={onClose} footer={footer}>
      {done ? (
        <div className="import-done">
          <div className="notice notice-success">{done.created ? "Đã import xong." : "Dữ liệu này đã có sẵn trong Project, không tạo bản trùng."}</div>
          <dl className="details details-compact">
            <dt>Bản đồ</dt><dd>{done.snapshot.map_name}</dd>
            <dt>CARLA</dt><dd>{done.snapshot.carla_version}</dd>
            <dt>Dữ liệu</dt><dd>{snapshotStats(done.snapshot)}</dd>
          </dl>
        </div>
      ) : (
        <form className="import-form" onSubmit={submit}>
          <ol className="import-steps">
            <li>Trên máy chạy CARLA, chạy script export (<code>connect_carla/carla_export.py</code>).</li>
            <li>Nén <strong>cả thư mục kết quả</strong> (ví dụ <code>20260924T122139Z</code>) thành tệp <code>.zip</code>.</li>
            <li>Kéo thả tệp vào ô dưới đây. Đã có tệp catalog <code>.json</code> thì chọn trực tiếp tệp đó.</li>
          </ol>
          <label className={`drop-zone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
            <input type="file" accept=".zip,.json,application/zip,application/json" onChange={(event) => pick(event.target.files?.[0])} />
            {file
              ? <><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(1)} MB · bấm để chọn tệp khác</span></>
              : <><strong>Kéo thả tệp .zip / .json vào đây</strong><span>hoặc bấm để chọn tệp · tối đa 50 MB</span></>}
          </label>
          <label className="field">
            Tên gợi nhớ (tuỳ chọn)
            <input value={label} maxLength={200} onChange={(event) => setLabel(event.target.value)} placeholder="Ví dụ: Máy lab tầng 3" />
          </label>
          <p className="field-help">Chỉ dùng bản đồ, làn đường, điểm xuất phát và danh sách xe/người đi bộ. Ảnh, video và log cảm biến trong tệp được bỏ qua.</p>
          {error && <ErrorNotice>{error}</ErrorNotice>}
        </form>
      )}
    </Modal>
  );
}

type Choice = "DEFAULT" | "MINE";

/** Step 1 of generation: pick the CARLA data the Agent grounds the scenario on. */
export function CatalogSourcePicker({
  snapshots,
  selectedId,
  onSelect,
  onImported,
}: {
  snapshots: CatalogSnapshot[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onImported: (snapshot: CatalogSnapshot) => void;
}) {
  const p = useProjectPath();
  const { can } = useSession();
  const [importing, setImporting] = useState(false);
  const defaults = snapshots.filter((item) => item.is_default);
  const mine = snapshots.filter((item) => !item.is_default);
  const workerConnected = mine.some((item) => item.source === "WORKER");
  const selected = snapshots.find((item) => item.id === selectedId) ?? null;
  const choice: Choice | null = selected ? (selected.is_default ? "DEFAULT" : "MINE") : null;
  const canImport = can("catalog:import");

  function choose(next: Choice) {
    if (next === "DEFAULT" && defaults[0]) onSelect((choice === "DEFAULT" && selected) ? selected.id : defaults[0].id);
    if (next === "MINE") {
      if (mine[0]) onSelect((choice === "MINE" && selected) ? selected.id : mine[0].id);
      else if (canImport) setImporting(true);
    }
  }

  return (
    <div className="source-picker">
      {!workerConnected && (
        <div className="notice notice-info source-hint">
          <strong>Chưa kết nối CARLA của bạn qua worker.</strong> Bạn vẫn sinh kịch bản được ngay bằng một trong hai cách dưới đây.
        </div>
      )}
      <div className="source-options" role="radiogroup" aria-label="Nguồn dữ liệu CARLA">
        <div
          role="radio"
          aria-checked={choice === "DEFAULT"}
          aria-disabled={!defaults.length}
          tabIndex={0}
          className={`source-option ${choice === "DEFAULT" ? "selected" : ""} ${!defaults.length ? "disabled" : ""}`}
          onClick={() => choose("DEFAULT")}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose("DEFAULT"); } }}
        >
          <span className="source-radio" aria-hidden="true" />
          <div className="source-body">
            <strong>Dùng CARLA mặc định của hệ thống</strong>
            <span>Sẵn sàng ngay, không cần cài đặt. Phù hợp để thử nhanh.</span>
            {defaults.length ? (
              defaults.length > 1 && choice === "DEFAULT" ? (
                <select value={selectedId ?? ""} onClick={(event) => event.stopPropagation()} onChange={(event) => onSelect(Number(event.target.value))}>
                  {defaults.map((item) => <option key={item.id} value={item.id}>{snapshotTitle(item)}</option>)}
                </select>
              ) : (
                <div className="source-meta"><b>{snapshotTitle(defaults[0])}</b><small>{snapshotStats(defaults[0])}</small></div>
              )
            ) : <small className="error-code">Hệ thống chưa cài dữ liệu mặc định. Liên hệ quản trị viên.</small>}
          </div>
        </div>

        <div
          role="radio"
          aria-checked={choice === "MINE"}
          aria-disabled={!mine.length && !canImport}
          tabIndex={0}
          className={`source-option ${choice === "MINE" ? "selected" : ""} ${!mine.length && !canImport ? "disabled" : ""}`}
          onClick={() => choose("MINE")}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose("MINE"); } }}
        >
          <span className="source-radio" aria-hidden="true" />
          <div className="source-body">
            <strong>Dùng dữ liệu CARLA của tôi</strong>
            <span>Kịch bản khớp đúng bản đồ và xe trên máy CARLA của bạn. Import bản export (.zip) hoặc catalog (.json).</span>
            {mine.length ? (
              <>
                {choice === "MINE" ? (
                  <select value={selectedId ?? ""} onClick={(event) => event.stopPropagation()} onChange={(event) => onSelect(Number(event.target.value))}>
                    {mine.map((item) => <option key={item.id} value={item.id}>{snapshotTitle(item)} · {snapshotOrigin(item)}</option>)}
                  </select>
                ) : <div className="source-meta"><b>{mine.length} bản dữ liệu</b><small>Mới nhất: {snapshotTitle(mine[0])} · {formatDate(mine[0].created_at)}</small></div>}
                {canImport && <button type="button" className="text-button" onClick={(event) => { event.stopPropagation(); setImporting(true); }}>+ Import bản khác</button>}
              </>
            ) : canImport ? (
              <button type="button" className="button source-cta" onClick={(event) => { event.stopPropagation(); setImporting(true); }}><Icon name="plus" size={14} />Import dữ liệu CARLA</button>
            ) : <small className="muted">Bạn chưa có quyền import. Nhờ người tạo kịch bản hoặc Admin import giúp.</small>}
          </div>
        </div>
      </div>
      <p className="source-footnote muted">
        Kết nối trực tiếp CARLA qua worker đang được phát triển. Khi xong, dữ liệu sẽ tự đồng bộ vào mục &ldquo;Dữ liệu CARLA của tôi&rdquo;. <Link className="link" href={p("/start-up")}>Xem trang Start up</Link>
      </p>
      {selected && (
        <div className="source-selected">
          <Icon name="check" size={14} />Agent sẽ dùng: <strong>{snapshotTitle(selected)}</strong> <span className="muted">({snapshotOrigin(selected)})</span>
        </div>
      )}
      {importing && <ImportCatalogDialog onClose={() => setImporting(false)} onImported={(snapshot) => { onImported(snapshot); onSelect(snapshot.id); }} />}
    </div>
  );
}
