"use client";

import Link from "next/link";
import { useState } from "react";

import { labels, useSession } from "@/shared/auth/session-context";
import { formatDate, Loading } from "@/shared/ui/components";
import { Icon } from "@/shared/ui/icons";

import { carlaStatus, DEMO_MAPS, PAIR_CODE_TTL_MINUTES, useCarlaDemo, type CarlaDemoState } from "./carla-demo";

const STEPS = ["Chuẩn bị CARLA", "Ghép worker", "Kiểm tra", "Đồng bộ"];

function activeStep(state: CarlaDemoState) {
  if (state.stage === "ready") return 5;
  if (state.stage === "unpaired") return 1;
  if (state.stage === "awaiting_pair") return 2;
  if (state.stage === "paired" || state.stage === "checking") return 3;
  return 4;
}

function workerLabel(state: CarlaDemoState) {
  if (state.stage === "unpaired") return "Chưa ghép";
  if (state.stage === "awaiting_pair") return "Đang chờ ghép";
  return "Đã ghép";
}
function carlaLabel(state: CarlaDemoState) {
  if (state.stage === "checking") return "Đang kiểm tra";
  if (["compatible", "syncing", "ready"].includes(state.stage)) return "Tương thích";
  return "Chưa kiểm tra";
}
function catalogLabel(state: CarlaDemoState) {
  if (state.stage === "syncing") return "Đang đồng bộ";
  if (state.stage === "ready") return "Đã đồng bộ";
  return state.snapshotId ? "Bản cũ" : "Chưa đồng bộ";
}

function Stage({ number, title, hint, children }: { number: number; title: string; hint: string; children: React.ReactNode }) {
  return <section className="setup-stage">
    <div className="setup-stage-head"><span className="setup-index">Bước {number}</span><strong>{title}</strong></div>
    <p className="muted">{hint}</p>
    {children}
  </section>;
}

export function SettingsScreen() {
  const { session } = useSession();
  const project = session?.active_project;
  const carla = useCarlaDemo(project?.id);
  const { state } = carla;
  const [copied, setCopied] = useState(false);

  if (!session || !project) return <Loading text="Hãy chọn một Project…" />;
  const step = activeStep(state);
  const status = carlaStatus(state);
  const defaultMode = state.source === "default";

  async function copyCode() {
    if (!state.pairCode) return;
    try {
      await navigator.clipboard.writeText(state.pairCode);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  let body: React.ReactNode;
  if (state.stage === "unpaired") {
    body = <>
      <Stage number={1} title="Chuẩn bị CARLA" hint="Tải và cài hai thành phần trước khi ghép nối. Worker cài trên máy chạy CARLA hoặc một máy cùng mạng nội bộ.">
        <div className="setup-downloads">
          <div className="workflow-card">
            <strong>CARLA</strong>
            <span>Phần mềm mô phỏng trên máy của bạn.</span>
            <div className="card-actions">
              <a className="button" href="https://carla.org/" target="_blank" rel="noopener noreferrer">Trang chủ CARLA ↗</a>
              <a className="text-button" href="https://github.com/carla-simulator/carla/releases" target="_blank" rel="noopener noreferrer">Các bản tải chính thức ↗</a>
            </div>
          </div>
          <div className="workflow-card">
            <strong>Scenario Forge Worker</strong>
            <span>Kết nối máy CARLA với Project này.</span>
            <div className="card-actions">
              <a className="button" href="mailto:hotro@scenarioforge.test?subject=Yeu%20cau%20bo%20cai%20Worker">Yêu cầu bộ cài worker ↗</a>
            </div>
          </div>
        </div>
      </Stage>
      <Stage number={2} title="Ghép worker" hint="Mã ghép nối liên kết worker trên máy của bạn với Project này.">
        <button className="button primary" onClick={carla.requestPairCode}>Tạo mã ghép nối</button>
      </Stage>
    </>;
  } else if (state.stage === "awaiting_pair") {
    body = <Stage number={2} title="Ghép worker" hint="Nhập mã vào worker, sau đó kiểm tra ghép nối. Nếu mã hết hạn, hãy tạo mã khác.">
      <div className="pair-code" role="status">
        <span className="muted">Mã ghép nối</span>
        <strong>{state.pairCode}</strong>
        <span className="muted">Nhập mã vào worker trong {PAIR_CODE_TTL_MINUTES} phút{state.pairCodeIssuedAt ? ` · tạo lúc ${formatDate(state.pairCodeIssuedAt)}` : ""}.</span>
        <div className="card-actions">
          <button className="button" onClick={() => void copyCode()}>{copied ? "Đã sao chép" : "Sao chép mã"}</button>
          <button className="button" onClick={carla.requestPairCode}>Tạo mã khác</button>
          <button className="button primary" onClick={carla.confirmPair}>Kiểm tra ghép nối</button>
        </div>
      </div>
    </Stage>;
  } else if (state.stage === "paired" || state.stage === "checking") {
    body = <Stage number={3} title="Kiểm tra môi trường" hint="Worker kiểm tra CARLA, map và ScenarioRunner. Nếu không phù hợp, hệ thống sẽ nêu phần cần sửa.">
      <button className="button primary" disabled={state.stage === "checking"} onClick={carla.probeCarla}>
        {state.stage === "checking" ? <><span className="spinner" />Đang kiểm tra…</> : "Kiểm tra CARLA"}
      </button>
    </Stage>;
  } else if (state.stage === "compatible" || state.stage === "syncing") {
    body = <Stage number={4} title="Đồng bộ dữ liệu" hint="Xem và xác nhận thông tin cần dùng trước khi đồng bộ.">
      <div className="consent-box">
        <strong>Thông tin sẽ đồng bộ</strong>
        <p>Scenario Forge cần phiên bản CARLA, worker và runner; map, mã phương tiện và dữ liệu đường cần để tạo scenario; cùng trạng thái kết nối. Không gửi video, log chạy hoặc file map gốc ở bước này.</p>
        <label className="consent-choice">
          <input type="checkbox" checked={state.consent} disabled={state.stage === "syncing"} onChange={(event) => carla.setConsent(event.target.checked)} />
          <span>Tôi xác nhận tổ chức cho phép chia sẻ các thông tin trên với Scenario Forge để thiết lập môi trường và tạo scenario.</span>
        </label>
      </div>
      <button className="button primary" disabled={!state.consent || state.stage === "syncing"} onClick={carla.syncCatalog}>
        {state.stage === "syncing" ? <><span className="spinner" />Đang đồng bộ…</> : "Đồng bộ catalog"}
      </button>
    </Stage>;
  } else {
    body = <div className="notice notice-success">Môi trường đã sẵn sàng. Bạn có thể tạo scenario.</div>;
  }

  const connection = <div className="dashboard-grid settings-grid">
    <section className="panel">
      <div className="panel-header">
        <div><div className="panel-title">Kết nối CARLA</div><div className="panel-subtitle">Ghép worker trên máy chạy mô phỏng với Project này.</div></div>
        {state.stage !== "unpaired" && <button className="button" onClick={carla.disconnect}>Ngắt kết nối</button>}
      </div>
      <div className="setup-body">
        <ol className="setup-steps">
          {STEPS.map((label, index) => <li key={label} className={index + 1 < step ? "done" : index + 1 === step ? "current" : ""}><span>{index + 1 < step ? "✓" : index + 1}</span>{label}</li>)}
        </ol>
        {body}
      </div>
    </section>
    <section className="panel">
      <div className="panel-header"><div className="panel-title">Trạng thái máy chạy CARLA</div></div>
      <div className="connection-state">
        <div><span className="muted">Worker</span><strong>{workerLabel(state)}</strong></div>
        <div><span className="muted">CARLA</span><strong>{carlaLabel(state)}</strong></div>
        <div><span className="muted">Catalog</span><strong>{catalogLabel(state)}</strong></div>
      </div>
      <dl className="details">
        <dt>Map hiện tại</dt><dd>{state.map ?? "Chưa kiểm tra"}</dd>
        <dt>Lần đồng bộ tốt</dt><dd>{state.lastSync ? formatDate(state.lastSync) : "Chưa có"}</dd>
      </dl>
      <details className="setup-technical">
        <summary>Thông tin kỹ thuật</summary>
        <dl className="details">
          <dt>Mã cài đặt</dt><dd>{state.installationId ?? "Chưa có"}</dd>
          <dt>Phiên bản worker</dt><dd>{state.workerVersion ?? "Chưa nhận"}</dd>
          <dt>CARLA</dt><dd>{state.carlaVersion ?? "Chưa kiểm tra"}</dd>
          <dt>ScenarioRunner</dt><dd>{state.runnerVersion ?? "Chưa kiểm tra"}</dd>
          <dt>Tín hiệu worker gần nhất</dt><dd>{state.lastHeartbeat ? formatDate(state.lastHeartbeat) : "Chưa có"}</dd>
          <dt>Snapshot gần nhất</dt><dd>{state.snapshotId ?? "Chưa có"}</dd>
          <dt>Hash snapshot</dt><dd className="case-key">{state.snapshotHash ?? "Chưa có"}</dd>
        </dl>
      </details>
    </section>
  </div>;

  return <main className="main">
    <section className="heading">
      <div>
        <p className="eyebrow">Môi trường mô phỏng</p>
        <h1>Cài đặt</h1>
        <p>Chọn dữ liệu để tạo scenario và kết nối máy chạy CARLA cho Project này.</p>
      </div>
    </section>
    <div className="notice notice-info demo-notice"><strong>Bản demo giao diện.</strong> Các bước dưới đây chạy bằng dữ liệu mô phỏng trong trình duyệt, chưa kết nối CARLA hay worker thật.</div>

    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Dữ liệu môi trường</div>
          <div className="panel-subtitle">{defaultMode ? "Dữ liệu mặc định đang được dùng" : state.source === "synced" ? "Đang dùng catalog đã đồng bộ" : "Chọn dữ liệu để tạo scenario"}</div>
        </div>
        <span className={`pill carla-pill-${status.tone}`}>{status.label}</span>
      </div>
      <div className="setup-body">
        <p className="muted">{defaultMode
          ? "Tạo scenario và thử hàng đợi/kết quả bằng dữ liệu mô phỏng. Chế độ này chưa kết nối CARLA trên máy của bạn."
          : "Có thể dùng dữ liệu mặc định để thử giao diện trước khi kết nối CARLA."}</p>
        <div className="card-actions">
          <button className={`button ${defaultMode ? "primary" : ""}`} aria-pressed={defaultMode} onClick={carla.chooseDefaultData}>Dùng dữ liệu mặc định</button>
          {state.snapshotId && <button className="button" disabled={!defaultMode} onClick={carla.chooseSyncedCatalog}>Dùng catalog đã đồng bộ</button>}
          {defaultMode && !state.snapshotId && <button className="button" onClick={carla.setupOwnCarla}>Thiết lập CARLA của tôi</button>}
          <Link className="button" href="/test-cases/new"><Icon name="plus" size={14} />Tạo kịch bản</Link>
        </div>
        {defaultMode && <dl className="details details-inline">
          <dt>Nguồn dữ liệu</dt><dd>Mặc định · Demo</dd>
          <dt>Map tham khảo</dt><dd>{DEMO_MAPS.join(", ")}</dd>
        </dl>}
      </div>
    </section>

    {defaultMode
      ? <details className="panel settings-optional"><summary>Kết nối CARLA của tôi (tùy chọn)</summary>{connection}</details>
      : connection}

    <section className="panel">
      <div className="panel-header"><div className="panel-title">Tài khoản</div></div>
      <dl className="details">
        <dt>Người dùng</dt><dd>{session.user.display_name || session.user.email}</dd>
        <dt>Email</dt><dd>{session.user.email}</dd>
        <dt>Vai trò trong Project</dt><dd>{labels.role[project.role]}{project.is_owner ? " · Người tạo" : ""}</dd>
        <dt>Nhiệm vụ</dt><dd>{project.responsibilities?.length ? project.responsibilities.map((item) => labels.responsibility[item]).join(", ") : "Chưa được giao nhiệm vụ"}</dd>
      </dl>
    </section>
  </main>;
}
