"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { API_BASE, api, ApiError, CatalogSnapshot } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";
import { carlaStatus, useCarlaDemo } from "@/features/startup/carla-demo";

import { Bridge, isPairExpired, OTP_LENGTH, PendingPair, useBridges } from "./bridge-store";

/* /start-up — first step of a project: pick where CARLA data comes from. */
export function StartUpScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const project = session?.active_project;
  const carla = useCarlaDemo(project?.id);
  const bridges = useBridges(project?.id);
  const [maps, setMaps] = useState<CatalogSnapshot[] | null>(null);
  const [error, setError] = useState("");

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
  const status = carlaStatus(carla.state);
  const usingDefault = carla.state.source === "default";

  return (
    <main className="main">
      <section className="heading">
        <div>
          <p className="eyebrow">Bắt đầu</p>
          <h1>Start up</h1>
          <p>Chọn nguồn dữ liệu CARLA cho Project {project.name}: đồng bộ với CARLA trên một hoặc nhiều máy qua Scenario Forge Bridge, hoặc dùng bộ map dựng sẵn.</p>
        </div>
        <div className="heading-actions">
          <span className={`pill carla-pill-${status.tone}`}>{status.label}</span>
        </div>
      </section>

      <section className="startup-choices">
        <article className={`panel startup-choice ${bridges.bridges.length > 0 ? "selected" : ""}`}>
          <div className="startup-choice-head">
            <span className="startup-choice-icon"><Icon name="refresh" /></span>
            <div>
              <div className="panel-title">Đồng bộ với Scenario Forge Bridge</div>
              {bridges.bridges.length > 0
                ? <span className="pill carla-pill-ready">{bridges.bridges.length} bridge đã kết nối</span>
                : <span className="pill">Chưa kết nối</span>}
            </div>
          </div>
          <p className="muted">
            Scenario Forge Bridge là phần mềm cài trên máy của bạn, liên kết máy đó với website này. Một Project có thể kết nối nhiều Bridge
            (mỗi máy chạy CARLA một Bridge); khi chạy Simulator Runner, bạn chọn Bridge sẽ thực thi.
          </p>
          <ul className="startup-list">
            <li>Dùng đúng map và phiên bản CARLA trên từng máy</li>
            <li>Chạy kịch bản thật trên CARLA tại máy đã chọn</li>
            <li>Kết quả pass / fail gửi về Simulator Runner</li>
          </ul>

          {bridges.error && <ErrorNotice>{bridges.error}</ErrorNotice>}
          {!bridges.loaded ? <Loading text="Đang tải Bridge…" /> : bridges.bridges.length > 0 && (
            <div className="bridge-list">
              {bridges.bridges.map((item) => (
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
                    <button className="button" onClick={() => { if (window.confirm(`Ngắt kết nối ${item.name} (${item.connection_uid}) khỏi Project?`)) void bridges.remove(item.connection_uid); }}>
                      <Icon name="trash" size={14} />Ngắt
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {bridges.pending ? (
            <BridgeOtp
              pending={bridges.pending}
              live={bridges.live}
              onRegenerate={() => void bridges.requestPair()}
              onClose={() => void bridges.cancelPair()}
            />
          ) : (
            <div className="card-actions">
              <button className="button primary" type="button" onClick={() => void bridges.requestPair()}>
                <Icon name="plus" size={14} />Thêm kết nối mới
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
              {usingDefault ? <span className="pill carla-pill-ready">Đang dùng</span> : <span className="pill">{bridges.bridges.length ? "Tùy chọn" : "Chưa có Bridge"}</span>}
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
    </main>
  );
}

/** 6-digit OTP the user types into Scenario Forge Bridge (`pair <code>`); turns into the success state when it is redeemed. */
function BridgeOtp({ pending, live, onRegenerate, onClose }: {
  pending: PendingPair;
  live: boolean;
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

// pipx installs the CLI straight from the repository's bridge/ folder.
const BRIDGE_SOURCE = "git+https://github.com/vminhquan/demo_fullstack_build-phase.git#subdirectory=bridge";
// CARLA server version the Simulator Runner is set up for (ScenarioRunner tag and `carla` package follow it).
const CARLA_VERSION = "0.9.16";

type GuideOs = "ubuntu" | "windows";
type GuideStep = { title: string; note?: string; commands: string[] };
type GuideSection = { title: string; note?: string; steps: GuideStep[] };

// Paths used in every command of one OS; the user edits CARLA's if it is installed elsewhere.
const PATHS: Record<GuideOs, { carla: string; runner: string; python: string; patched: string; runs: string }> = {
  ubuntu: {
    carla: `~/CARLA_${CARLA_VERSION}`,
    runner: "~/scenario_runner",
    python: "~/sr-venv/bin/python",
    patched: "~/scenario_runner/srunner/scenariomanager/scenarioatomics/atomic_behaviors.py",
    runs: "~/.config/scenario-forge-bridge/runs",
  },
  windows: {
    carla: String.raw`C:\CARLA_${CARLA_VERSION}`,
    runner: String.raw`C:\scenario_runner`,
    python: String.raw`C:\sr-venv\Scripts\python.exe`,
    patched: String.raw`C:\scenario_runner\srunner\scenariomanager\scenarioatomics\atomic_behaviors.py`,
    runs: String.raw`$env:LOCALAPPDATA\ScenarioForge\scenario-forge-bridge\runs`,
  },
};

// Two known bugs of ScenarioRunner v0.9.16 when the ego has a route (AssignRouteAction): crash on `len(None)`,
// and a controller that resets itself. Same edits as bridge/README.md.
function patchCommands(os: GuideOs): string[] {
  const file = PATHS[os].patched;
  if (os === "ubuntu") {
    return [
      String.raw`sed -i 's/        if len(self._waypoints) != len(self._times):/        if self._times is not None and len(self._waypoints) != len(self._times):/' ` + file,
      String.raw`sed -i 's/^                actor_dict\[self._actor.id\].reset()$/                if actor_dict[self._actor.id] is not self._actor_control:\n                    actor_dict[self._actor.id].reset()/' ` + file,
      `grep -c -e "self._times is not None and len" -e "is not self._actor_control" ${file}`,
    ];
  }
  return [
    `$f = "${file}"`,
    String.raw`(Get-Content $f) -replace '^        if len\(self\._waypoints\) != len\(self\._times\):$', '        if self._times is not None and len(self._waypoints) != len(self._times):' | Set-Content $f`,
    String.raw`(Get-Content $f) -replace '^                actor_dict\[self\._actor\.id\]\.reset\(\)$', "                if actor_dict[self._actor.id] is not self._actor_control:` + "`n" + String.raw`                    actor_dict[self._actor.id].reset()" | Set-Content $f`,
    String.raw`(Select-String -Path $f -Pattern "self._times is not None and len", "is not self._actor_control").Count`,
  ];
}

function guideSections(os: GuideOs, server: string): GuideSection[] {
  const paths = PATHS[os];
  const ubuntu = os === "ubuntu";
  return [
    {
      title: "1. Kết nối máy này với Project",
      steps: [
        ubuntu
          ? { title: "Cài Python, Git và pipx (một lần)", commands: ["sudo apt update && sudo apt install -y python3 python3-venv pipx git", "pipx ensurepath && source ~/.bashrc"] }
          : {
            title: "Cài Python, Git và pipx (một lần)",
            note: "Sau lệnh cuối, đóng PowerShell và mở lại.",
            commands: ["winget install Python.Python.3.11", "winget install Git.Git", "py -m pip install --user pipx", "py -m pipx ensurepath"],
          },
        { title: "Cài Scenario Forge Bridge", commands: [`pipx install --force "${BRIDGE_SOURCE}"`, "scenario-forge-bridge --version"] },
        { title: "Trỏ Bridge tới máy chủ này (một lần)", commands: [`scenario-forge-bridge config --server ${server}`] },
        {
          title: "Ghép nối",
          note: "Bấm “Thêm kết nối mới” ở trên để lấy mã 6 số, rồi chạy lệnh dưới trên máy đó. --no-run: ghép xong thì dừng, cài tiếp phần 2 rồi mới chạy Bridge.",
          commands: ["scenario-forge-bridge pair <mã 6 số> --no-run"],
        },
      ],
    },
    {
      title: "2. Chạy test case trên CARLA (Simulator Runner)",
      note: `Bridge chạy từng test case bằng ScenarioRunner ${CARLA_VERSION} trong một Python riêng. Đổi ${paths.carla} nếu CARLA cài ở chỗ khác.`,
      steps: [
        {
          title: `Mở CARLA ${CARLA_VERSION} có cửa sổ`,
          note: "Giữ cửa sổ CARLA mở: khi chạy test, camera tự bám xe ego để xem va chạm. Không dùng -RenderOffScreen.",
          commands: [ubuntu ? `cd ${paths.carla} && ./CarlaUE4.sh -quality-level=Low` : `${paths.carla}\\CarlaUE4.exe -quality-level=Low`, "scenario-forge-bridge check-carla"],
        },
        {
          title: "Tạo Python 3.10 riêng cho ScenarioRunner (một lần)",
          note: "ScenarioRunner cần Python 3.10 hoặc 3.11 (numpy 1.24.4 không có bản cho 3.12). uv tự tải Python, không cần quyền quản trị.",
          commands: ubuntu
            ? ["curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env", `uv venv --python 3.10 ~/sr-venv`]
            : [`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, String.raw`uv venv --python 3.10 C:\sr-venv`],
        },
        {
          title: `Cài ScenarioRunner v${CARLA_VERSION} (một lần)`,
          commands: [
            `git clone -b v${CARLA_VERSION} --depth 1 https://github.com/carla-simulator/scenario_runner.git ${paths.runner}`,
            `uv pip install --python ${paths.python} carla==${CARLA_VERSION} -r ${paths.runner}${ubuntu ? "/" : "\\"}requirements.txt`,
          ],
        },
        {
          title: "Vá 2 lỗi của ScenarioRunner (một lần)",
          note: "Lỗi có sẵn của ScenarioRunner khi xe ego có route: thiếu bản vá thì kịch bản dừng ngay (NoneType has no len / set_speed). Lệnh cuối phải in ra 2.",
          commands: patchCommands(os),
        },
        {
          title: "Cho Bridge biết dùng Python và ScenarioRunner nào (một lần)",
          note: "Lệnh status phải có dòng “Runner : sẵn sàng”.",
          commands: [`scenario-forge-bridge config --runner-python ${paths.python} --runner-root ${paths.runner} --carla-root ${paths.carla} --camera follow`, "scenario-forge-bridge status"],
        },
        {
          title: "Cài gói carla cho Bridge để đồng bộ dữ liệu map",
          note: "Chỉ cần cho nút “Đồng bộ dữ liệu CARLA”. Cài lại sau mỗi lần cập nhật Bridge.",
          commands: [`pipx inject scenario-forge-bridge carla==${CARLA_VERSION}`],
        },
        {
          title: "Chạy Bridge",
          note: "Để cửa sổ mở thì Bridge Online và nhận test case từ Simulator Runner. Không nhấn Ctrl+C khi đang có test case chạy: ScenarioRunner dừng theo và test case chạy lại từ đầu.",
          commands: ["scenario-forge-bridge run"],
        },
      ],
    },
  ];
}

function dailyCommands(os: GuideOs): { command: string; hint: string }[] {
  const runs = PATHS[os].runs;
  return [
    { command: "scenario-forge-bridge run", hint: "Bật lại Bridge (Online) sau khi tắt máy hoặc đóng cửa sổ" },
    { command: "scenario-forge-bridge status", hint: "Project đã ghép, trạng thái CARLA và Simulator Runner" },
    { command: "scenario-forge-bridge sync", hint: "Đồng bộ dữ liệu mọi map của CARLA (giống nút “Đồng bộ dữ liệu CARLA”)" },
    { command: `pipx install --force "${BRIDGE_SOURCE}"`, hint: `Cập nhật Bridge lên bản mới (sau đó chạy lại pipx inject scenario-forge-bridge carla==${CARLA_VERSION})` },
    {
      command: os === "ubuntu" ? `ls -lt ${runs}/*/` : `Get-ChildItem "${runs}" -Recurse -Filter runner.log | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName`,
      hint: "Log của từng test case: runner.log (ScenarioRunner), camera.log và báo cáo JSON",
    },
  ];
}

/** Collapsible install guide for the Bridge CLI and its Simulator Runner (Ubuntu / Windows), with this site's API address filled in. */
function BridgeInstallGuide() {
  const [os, setOs] = useState<GuideOs>("ubuntu");
  const server = API_BASE.startsWith("http") || typeof window === "undefined" ? API_BASE : `${window.location.origin}${API_BASE}`;
  const sections = guideSections(os, server);

  return (
    <details className="bridge-guide">
      <summary><Icon name="file" size={14} />Hướng dẫn cài Bridge CLI và Simulator Runner</summary>
      <div className="bridge-guide-body">
        <div className="bridge-guide-tabs" role="tablist" aria-label="Hệ điều hành">
          {(["ubuntu", "windows"] as const).map((item) => (
            <button key={item} type="button" role="tab" aria-selected={os === item} className={`button ${os === item ? "primary" : ""}`} onClick={() => setOs(item)}>
              {item === "ubuntu" ? "Ubuntu" : "Windows (PowerShell)"}
            </button>
          ))}
        </div>
        {sections.map((section) => (
          <div key={section.title} className="bridge-guide-section">
            <strong>{section.title}</strong>
            {section.note && <div className="field-help">{section.note}</div>}
            <ol className="bridge-guide-steps">
              {section.steps.map((step) => (
                <li key={step.title}>
                  <strong>{step.title}</strong>
                  {step.commands.map((command) => <CommandLine key={command} command={command} />)}
                  {step.note && <div className="field-help">{step.note}</div>}
                </li>
              ))}
            </ol>
          </div>
        ))}
        <div className="bridge-guide-daily">
          <strong>Dùng hằng ngày</strong>
          {dailyCommands(os).map((item) => (
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
