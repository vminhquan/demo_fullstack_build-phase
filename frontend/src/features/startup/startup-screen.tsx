"use client";

import Link from "next/link";
import { ReactNode, useCallback, useEffect, useState } from "react";

import { API_BASE, api, ApiError, CatalogSnapshot, ServerImpact } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";
import { useConfirm } from "@/shared/ui/modal";
import { useCarlaDemo } from "@/features/startup/carla-demo";

import { Bridge, bridgeStatus, isPairExpired, OTP_LENGTH, PendingPair, useBridges } from "./bridge-store";

/* /start-up — first step of a project: pick where CARLA data comes from. */
export function StartUpScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const project = session?.active_project;
  const carla = useCarlaDemo(project?.id);
  const bridges = useBridges(project?.id);
  const { confirm, dialog } = useConfirm();
  const [maps, setMaps] = useState<CatalogSnapshot[] | null>(null);
  const [error, setError] = useState("");
  // A project uses one CARLA server; the newest connection is it (older ones only exist until the list reloads).
  const server = bridges.bridges.length ? bridges.bridges[bridges.bridges.length - 1] : null;
  const impact = useServerImpact(server ? `${server.connection_uid}:${server.synced_maps.join(",")}` : "");

  useEffect(() => {
    if (!session) return;
    let active = true;
    api
      .listCatalogSnapshots(session.access_token, "DEFAULT")
      .then((items) => { if (active) setMaps(items); })
      .catch((reason) => {
        if (!active) return;
        setMaps([]);
        setError(reason instanceof ApiError ? reason.message : "Không tải được danh sách map mặc định.");
      });
    return () => { active = false; };
  }, [session]);

  if (!session || !project) return <Loading text="Hãy chọn một Project…" />;
  const usingDefault = carla.state.source === "default";
  const status = bridgeStatus(bridges.bridges, usingDefault);

  /** Changing the CARLA server: the existing test cases were generated against the current server's data. */
  async function changeServer() {
    if (!server) { void bridges.requestPair(); return; }
    const latest = await impact.reload();
    const ok = await confirm({
      title: "Đổi CARLA server?",
      confirmLabel: "Tiếp tục, tạo mã kết nối",
      message: <ChangeServerWarning server={server} impact={latest} />,
    });
    if (ok) void bridges.requestPair();
  }
  async function disconnect() {
    if (!server) return;
    const latest = await impact.reload();
    const ok = await confirm({
      title: "Ngắt CARLA server?",
      confirmLabel: "Ngắt kết nối",
      message: (
        <>
          <p>Ngắt <strong>{server.name}</strong> ({server.connection_uid}) khỏi Project {project?.name}. Máy này vẫn giữ kết nối với các Project khác.</p>
          <p>Khi chưa có CARLA server, Project không chạy được Simulator Runner{latest?.test_case_count ? ` cho ${latest.test_case_count} test case hiện có` : ""}.</p>
        </>
      ),
    });
    if (ok) void bridges.remove(server.connection_uid);
  }

  return (
    <main className="main">
      <section className="heading">
        <div>
          <p className="eyebrow">Bắt đầu</p>
          <h1>Start up</h1>
          <p>Chọn nguồn dữ liệu CARLA cho Project {project.name}: kết nối một CARLA server qua Scenario Forge Bridge, hoặc dùng bộ map dựng sẵn.</p>
        </div>
        <div className="heading-actions">
          <span className={`pill carla-pill-${status.tone}`} title={status.detail}>{status.label}</span>
        </div>
      </section>

      <section className="startup-choices">
        <article className={`panel startup-choice ${server ? "selected" : ""}`}>
          <div className="startup-choice-head">
            <span className="startup-choice-icon"><Icon name="refresh" /></span>
            <div>
              <div className="panel-title">Kết nối CARLA server qua Scenario Forge Bridge</div>
              {server
                ? <span className="pill carla-pill-ready">Đã kết nối {server.name}</span>
                : <span className="pill">Chưa kết nối</span>}
            </div>
          </div>
          <p className="muted">
            Scenario Forge Bridge là phần mềm cài trên máy chạy CARLA, liên kết máy đó với website này. Mỗi Project dùng <strong>một CARLA server duy nhất</strong>;
            một CARLA server có thể phục vụ nhiều Project. Kết nối server khác sẽ thay thế server hiện tại.
          </p>
          <ul className="startup-list">
            <li>Dùng đúng map và phiên bản CARLA của server</li>
            <li>Chạy kịch bản thật trên CARLA của server đó</li>
            <li>Kết quả pass / fail gửi về Simulator Runner</li>
          </ul>

          {bridges.error && <ErrorNotice>{bridges.error}</ErrorNotice>}
          {!bridges.loaded ? <Loading text="Đang tải Bridge…" /> : server && (
            <div className="bridge-list">
              {[server].map((item) => (
                <div key={item.connection_uid} className="bridge-item">
                  <div>
                    <strong>{item.name}</strong>
                    <span className={`pill ${item.online ? "carla-pill-ready" : "carla-pill-pending"}`}>{item.online ? "Online" : "Offline"}</span>
                    <div className="case-key">Mã kết nối {item.connection_uid} · {item.hostname || "—"} · {item.os || "—"} · Bridge {item.bridge_version || "—"}</div>
                    <div className="case-key">
                      CARLA {item.carla_host ?? "127.0.0.1"}:{item.carla_port ?? 2000}{" "}
                      {item.carla_reachable === null ? "chưa kiểm tra" : item.carla_reachable ? "đang chạy" : "chưa chạy"}
                      {" · "}ghép {formatDate(item.paired_at)}{item.paired_by_name ? ` bởi ${item.paired_by_name}` : ""}
                      {" · "}lần cuối {formatDate(item.last_seen_at)}
                    </div>
                    <BridgeSyncStatus bridge={item} />
                  </div>
                  <div className="bridge-item-actions">
                    <button
                      className="button primary"
                      disabled={!item.online || !item.carla_reachable || (item.sync !== null && !item.sync.finished)}
                      title={!item.online ? "Bridge đang offline" : !item.carla_reachable ? "Chưa thấy CARLA trên máy này" : "Đọc mọi map từ CARLA của máy này"}
                      onClick={() => void bridges.sync(item.connection_uid)}
                    >
                      <Icon name="refresh" size={14} />Đồng bộ dữ liệu CARLA
                    </button>
                    <button className="button" onClick={() => void disconnect()}>
                      <Icon name="trash" size={14} />Ngắt
                    </button>
                  </div>
                </div>
              ))}
              <ServerCompatibility impact={impact.data} />
            </div>
          )}

          {bridges.pending ? (
            <BridgeOtp
              pending={bridges.pending}
              live={bridges.live}
              replacing={server && server.connection_uid !== bridges.pending.paired?.connection_uid ? server : null}
              onRegenerate={() => void bridges.requestPair()}
              onClose={() => void bridges.cancelPair()}
            />
          ) : (
            <div className="card-actions">
              <button className={`button ${server ? "" : "primary"}`} type="button" disabled={!bridges.loaded} onClick={() => void changeServer()}>
                <Icon name={server ? "refresh" : "plus"} size={14} />{server ? "Đổi CARLA server" : "Kết nối CARLA server"}
              </button>
            </div>
          )}
          <BridgeInstallGuide />
        </article>

        <article className={`panel startup-choice ${usingDefault ? "selected" : ""}`}>
          <div className="startup-choice-head">
            <span className="startup-choice-icon"><Icon name="layers" /></span>
            <div>
              <div className="panel-title">Dùng dữ liệu mặc định</div>
              {usingDefault ? <span className="pill carla-pill-ready">Đang dùng</span> : <span className="pill">{server ? "Tùy chọn" : "Chưa có Bridge"}</span>}
            </div>
          </div>
          <p className="muted">
            Chưa cài Bridge? Dùng bộ map CARLA dựng sẵn do Scenario Forge cung cấp để tạo và duyệt kịch bản ngay. Có thể chuyển sang Bridge sau.
          </p>
          <div className="field-help">Các map dưới đây sẽ có sẵn để chọn trong Test Case Builder.</div>
          {error && <ErrorNotice>{error}</ErrorNotice>}
          {maps === null ? (
            <Loading text="Đang tải map mặc định…" />
          ) : maps.length > 0 ? (
            <div className="startup-maps">
              {maps.map((item) => (
                <div key={item.id} className="startup-map">
                  <strong>{item.map_name}</strong>
                  <span className="case-key">CARLA {item.carla_version} · {item.spawn_point_count} điểm spawn · {item.vehicle_count} xe</span>
                </div>
              ))}
            </div>
          ) : (
            !error && <div className="inline-note">Chưa có map mặc định trong hệ thống.</div>
          )}
          <div className="card-actions">
            {usingDefault ? (
              <Link className="button primary" href={p("/test-case-builder/create")}><Icon name="plus" size={14} />Tạo kịch bản</Link>
            ) : (
              <button className="button primary" onClick={carla.chooseDefaultData}>Dùng dữ liệu mặc định</button>
            )}
            {usingDefault && <button className="button" onClick={carla.setupOwnCarla}>Bỏ chọn</button>}
          </div>
        </article>
      </section>
      {dialog}
    </main>
  );
}

/** Test cases of the project against the connected CARLA server; `key` changes when the server or its synced maps do. */
function useServerImpact(key: string) {
  const { session } = useSession();
  const token = session?.access_token;
  const [data, setData] = useState<ServerImpact | null>(null);
  const reload = useCallback(async () => {
    if (!token) return null;
    try {
      const next = await api.getServerImpact(token);
      setData(next);
      return next;
    } catch {
      return null; // the warning still shows, without the per-map list
    }
  }, [token]);
  useEffect(() => {
    if (!token) return;
    let active = true;
    api.getServerImpact(token).then((next) => { if (active) setData(next); }).catch(() => undefined);
    return () => { active = false; };
  }, [token, key]);
  return { data, reload };
}

function mapList(items: ServerImpact["maps"]) {
  return items.map((item) => `${item.map_code} (${item.count})`).join(", ");
}

/** Body of the "Đổi CARLA server?" confirmation. */
function ChangeServerWarning({ server, impact }: { server: Bridge; impact: ServerImpact | null }) {
  const versions = [...new Set(impact?.maps.flatMap((item) => item.carla_versions) ?? [])];
  return (
    <>
      <p>
        Project đang dùng CARLA server <strong>{server.name}</strong> ({server.hostname || server.connection_uid}). Mỗi Project chỉ kết nối một CARLA server:
        khi Bridge mới nhập mã, server hiện tại sẽ bị ngắt khỏi Project này (vẫn giữ kết nối với các Project khác).
      </p>
      {impact && impact.test_case_count > 0 ? (
        <div className="notice notice-warning">
          <strong>{impact.test_case_count} test case hiện có có thể không hoạt động với CARLA server mới.</strong>
          <div>
            Chúng được sinh từ dữ liệu của server cũ: bản đồ {mapList(impact.maps)}{versions.length ? `, CARLA ${versions.join(", ")}` : ""}.
            Nếu server mới thiếu bản đồ, khác phiên bản CARLA hoặc thiếu blueprint xe, Simulator Runner có thể chạy lỗi.
          </div>
        </div>
      ) : (
        <p className="muted">Project chưa có test case nào; đổi server không ảnh hưởng dữ liệu hiện có.</p>
      )}
      <p className="muted">Sau khi kết nối, hãy bấm “Đồng bộ dữ liệu CARLA” để Test Case Builder dùng bản đồ của server mới. Dữ liệu đã đồng bộ từ server cũ sẽ không còn được dùng để tạo test case.</p>
    </>
  );
}

/** After a server is connected and synced: which existing test cases are on maps or CARLA versions it lacks. */
function ServerCompatibility({ impact }: { impact: ServerImpact | null }) {
  if (!impact || impact.test_case_count === 0 || !impact.server_maps.length) return null;
  const missing = impact.maps.filter((item) => item.on_server === false);
  const otherVersion = impact.maps.filter((item) => item.on_server && item.carla_versions.some((version) => !impact.server_carla_versions.includes(version)));
  if (!missing.length && !otherVersion.length) return null;
  const count = (items: ServerImpact["maps"]) => items.reduce((total, item) => total + item.count, 0);
  const lines: ReactNode[] = [];
  if (missing.length) lines.push(<div key="maps">{count(missing)} test case trên bản đồ server này chưa có: {mapList(missing)}.</div>);
  if (otherVersion.length) {
    lines.push(<div key="versions">{count(otherVersion)} test case sinh từ phiên bản CARLA khác ({mapList(otherVersion)}); server này có CARLA {impact.server_carla_versions.join(", ")}.</div>);
  }
  return (
    <div className="notice notice-warning">
      <strong>Một số test case có thể không hoạt động với CARLA server này</strong>
      {lines}
    </div>
  );
}

/** 6-digit OTP the user types into Scenario Forge Bridge (`pair <code>`); turns into the success state when it is redeemed. */
function BridgeOtp({ pending, live, replacing, onRegenerate, onClose }: {
  pending: PendingPair;
  live: boolean;
  /** Server the new Bridge will replace (null when the project has none, or it re-paired the same one). */
  replacing: Bridge | null;
  onRegenerate: () => void;
  onClose: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  const [copied, setCopied] = useState(false);
  const expired = !pending.paired && isPairExpired(pending, now);

  useEffect(() => {
    if (expired || pending.paired) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expired, pending.paired]);

  if (pending.paired) {
    return (
      <div className="bridge-pair bridge-otp bridge-otp-success">
        <strong className="bridge-success-title"><Icon name="check" size={16} />Kết nối thành công</strong>
        <div>
          {pending.paired.name} · {pending.paired.hostname} · {pending.paired.os}
        </div>
        <div className="case-key">Mã kết nối <code className="session-code">{pending.paired.connection_uid}</code> (trùng với mã Bridge hiển thị)</div>
        <div className="field-help">Đây là CARLA server của Project. Bấm “Đồng bộ dữ liệu CARLA” để dùng bản đồ của server này.</div>
        <div className="card-actions"><button className="button" type="button" onClick={onClose}>Đóng</button></div>
      </div>
    );
  }

  const left = expired ? 0 : Math.max(0, Math.ceil((Date.parse(pending.expires_at) - now) / 1000));
  const countdown = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  const command = `scenario-forge-bridge pair ${pending.code}`;

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="bridge-pair bridge-otp">
      <div className="field-help">
        Trên máy chạy CARLA, mở terminal và gõ lệnh dưới đây (mã gồm {OTP_LENGTH} số, dùng được một lần):
      </div>
      {replacing && (
        <div className="inline-note">Nhập mã trên máy khác sẽ thay thế CARLA server hiện tại ({replacing.name}). Nhập trên chính máy đó thì chỉ ghép lại.</div>
      )}
      <div className={`otp-digits ${expired ? "expired" : ""}`} aria-label={`Mã kết nối ${pending.code.split("").join(" ")}`}>
        {pending.code.split("").map((digit, index) => <span key={index} className="otp-digit">{digit}</span>)}
      </div>
      <code className="session-code">{command}</code>
      <div className={expired ? "otp-expiry expired" : "otp-expiry"}>
        {expired ? "Mã đã hết hạn. Hãy tạo mã mới." : <>Hết hạn sau <strong>{countdown}</strong></>}
      </div>
      <div className="card-actions">
        {!expired && <button className="button" type="button" onClick={() => void copy()}>{copied ? "Đã chép" : "Sao chép lệnh"}</button>}
        <button className="button" type="button" onClick={onRegenerate}><Icon name="refresh" size={14} />Tạo mã mới</button>
        <button className="button" type="button" onClick={onClose}>Hủy</button>
      </div>
      {!expired && (
        <div className="inline-note">
          {live ? "Đang chờ Bridge nhập mã… Trang sẽ tự báo khi kết nối thành công." : "Đang kết nối kênh thông báo… (nếu lâu, hãy tải lại trang)"}
        </div>
      )}
    </div>
  );
}

type GuideOs = "ubuntu" | "windows";
type GuideStep = { title: string; note?: string; commands: string[] };

// The installers live in frontend/public/bridge/: uv, the Bridge CLI, then `setup-runner`, which asks the CARLA version.
function installCommand(os: GuideOs, origin: string): string {
  return os === "ubuntu" ? `curl -LsSf ${origin}/bridge/install.sh | sh` : `irm ${origin}/bridge/install.ps1 | iex`;
}

function guideSteps(os: GuideOs, origin: string, server: string): GuideStep[] {
  return [
    {
      title: "Cài Bridge (một lần)",
      note: "Màn hình cài sẽ hỏi phiên bản CARLA server của máy này: CARLA 0.9.16, CARLA 0.9.15 hoặc Tự động phát hiện (CARLA phải đang chạy). "
        + "Bridge tự chuẩn bị mọi thứ để chạy test, không đụng tới Python hay CARLA đang có trên máy."
        + (os === "ubuntu" ? " Cần git (sudo apt install -y git)." : " Cần Git (winget install Git.Git)."),
      commands: [installCommand(os, origin)],
    },
    {
      title: "Ghép nối với Project này",
      note: "Bấm “Thêm kết nối mới” ở trên để lấy mã 6 số. Ghép xong Bridge giữ kết nối luôn: để cửa sổ mở thì Bridge Online.",
      commands: [`scenario-forge-bridge pair <mã 6 số> --server ${server}`],
    },
    {
      title: "Mở CARLA có cửa sổ",
      note: "Khi chạy test, camera trong cửa sổ CARLA tự bám xe ego để xem va chạm. Không nhấn Ctrl+C ở cửa sổ Bridge khi đang có test chạy.",
      commands: [os === "ubuntu" ? "./CarlaUE4.sh -quality-level=Low" : "CarlaUE4.exe -quality-level=Low"],
    },
  ];
}

function dailyCommands(os: GuideOs, origin: string): { command: string; hint: string }[] {
  const runs = os === "ubuntu" ? "~/.config/scenario-forge-bridge/runs" : String.raw`$env:LOCALAPPDATA\ScenarioForge\scenario-forge-bridge\runs`;
  return [
    { command: "scenario-forge-bridge run", hint: "Bật lại Bridge (Online) sau khi tắt máy hoặc đóng cửa sổ" },
    { command: "scenario-forge-bridge status", hint: "Project đã ghép, trạng thái CARLA và môi trường chạy test" },
    { command: "scenario-forge-bridge setup-runner", hint: "Chọn lại phiên bản CARLA (khi đổi hoặc nâng cấp CARLA)" },
    { command: "scenario-forge-bridge sync", hint: "Đồng bộ dữ liệu mọi map của CARLA (giống nút “Đồng bộ dữ liệu CARLA”)" },
    { command: installCommand(os, origin), hint: "Cập nhật Bridge lên bản mới (chạy lại lệnh cài)" },
    {
      command: os === "ubuntu" ? `ls -lt ${runs}/*/` : `Get-ChildItem "${runs}" -Recurse -Filter runner.log | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName`,
      hint: "Log của từng test case: runner.log (ScenarioRunner), camera.log và báo cáo JSON",
    },
  ];
}

/** Collapsible install guide for the Bridge CLI (Ubuntu / Windows), with this site's addresses filled in. */
function BridgeInstallGuide() {
  const [os, setOs] = useState<GuideOs>("ubuntu");
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const server = API_BASE.startsWith("http") ? API_BASE : `${origin}${API_BASE}`;
  const steps = guideSteps(os, origin, server);

  return (
    <details className="bridge-guide">
      <summary><Icon name="file" size={14} />Hướng dẫn cài Bridge CLI</summary>
      <div className="bridge-guide-body">
        <div className="bridge-guide-tabs" role="tablist" aria-label="Hệ điều hành">
          {(["ubuntu", "windows"] as const).map((item) => (
            <button key={item} type="button" role="tab" aria-selected={os === item} className={`button ${os === item ? "primary" : ""}`} onClick={() => setOs(item)}>
              {item === "ubuntu" ? "Ubuntu" : "Windows (PowerShell)"}
            </button>
          ))}
        </div>
        <ol className="bridge-guide-steps">
          {steps.map((step) => (
            <li key={step.title}>
              <strong>{step.title}</strong>
              {step.commands.map((command) => <CommandLine key={command} command={command} />)}
              {step.note && <div className="field-help">{step.note}</div>}
            </li>
          ))}
        </ol>
        <div className="bridge-guide-daily">
          <strong>Dùng hằng ngày</strong>
          {dailyCommands(os, origin).map((item) => (
            <div key={item.command}>
              <CommandLine command={item.command} />
              <div className="field-help">{item.hint}</div>
            </div>
          ))}
        </div>
        <div className="inline-note">
          Không cần cùng mạng: Bridge chỉ kết nối ra ngoài tới máy chủ, máy cài Bridge không phải mở cổng nào. Báo “hết thời gian chờ” thì chạy lại lệnh sau ít giây.
        </div>
      </div>
    </details>
  );
}

function CommandLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="command-line">
      <code>{command}</code>
      <button type="button" className="button command-copy" onClick={() => void copy()} aria-label={`Sao chép lệnh ${command}`}>
        {copied ? "Đã chép" : "Chép"}
      </button>
    </div>
  );
}

/** What the Bridge synced from its CARLA, and the progress of a sync in flight. */
function BridgeSyncStatus({ bridge }: { bridge: Bridge }) {
  const sync = bridge.sync;
  const running = sync !== null && !sync.finished;
  return (
    <div className="bridge-sync">
      {running && (
        <div className="bridge-sync-progress">
          <span className="spinner" />
          {sync.status === "requested" || !sync.total
            ? "Đã gửi yêu cầu, Bridge đang kết nối CARLA…"
            : `Đang đọc map ${sync.index}/${sync.total}: ${sync.map_name} (Bridge mở lần lượt từng map trong CARLA)`}
          {sync.total ? <progress max={sync.total} value={Math.max(0, (sync.index ?? 1) - (sync.status === "loading" ? 1 : 0))} /> : null}
        </div>
      )}
      {sync?.finished && (
        <div className={sync.failed.length ? "bridge-sync-result warn" : "bridge-sync-result"}>
          Lần đồng bộ gần nhất: {sync.synced.length} map thành công{sync.failed.length ? `, ${sync.failed.length} lỗi` : ""}.
          {sync.failed.map((item, index) => (
            <div key={index} className="case-key">✗ {item.map_name ?? "CARLA"}: {item.error}</div>
          ))}
        </div>
      )}
      {bridge.synced_maps.length > 0 ? (
        <div className="bridge-sync-maps">
          <span className="case-key">Đã đồng bộ {bridge.synced_maps.length} map{bridge.last_synced_at ? ` · dữ liệu mới nhất ${formatDate(bridge.last_synced_at)}` : ""}:</span>
          <div>{bridge.synced_maps.map((map) => <span key={map} className="tag">{map}</span>)}</div>
        </div>
      ) : (
        !running && <div className="case-key">Chưa đồng bộ dữ liệu CARLA. Mở CARLA trên máy này rồi bấm “Đồng bộ dữ liệu CARLA”.</div>
      )}
    </div>
  );
}
