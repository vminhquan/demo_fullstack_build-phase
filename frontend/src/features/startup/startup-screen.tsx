"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, ApiError, CatalogSnapshot } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";
import { carlaStatus, useCarlaDemo } from "@/features/startup/carla-demo";

import { isPairExpired, OTP_LENGTH, PendingPair, useBridges } from "./bridge-store";

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
                  </div>
                  <div className="bridge-item-actions">
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
          <div className="inline-note">
            Cài Bridge trên máy có CARLA (Ubuntu / Windows), bấm “Thêm kết nối mới” rồi gõ <code className="session-code">scenario-forge-bridge pair &lt;mã 6 số&gt;</code> trên máy đó.
          </div>
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
