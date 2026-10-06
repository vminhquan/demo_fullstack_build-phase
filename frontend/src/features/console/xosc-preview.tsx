"use client";

import { useEffect, useMemo, useState } from "react";

import { api, ApiError, Grounding } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { downloadBlob } from "@/shared/ui/components";
import { ScenePreview } from "@/features/generation/scene-preview";

type XoscEntity = { name: string; kind: string; model: string; x: number | null; y: number | null; heading: number | null; speed: number | null };
type XoscEvent = { name: string; actor: string; action: string; condition: string };
type ParsedXosc = {
  description: string; author: string; map: string; entities: XoscEntity[]; events: XoscEvent[];
  weather: string; timeOfDay: string; route: { x: number; y: number }[]; stopAfter: string;
};

const CATEGORY_LABELS: Record<string, string> = {
  car: "Ô tô", motorbike: "Xe máy", bicycle: "Xe đạp", truck: "Xe tải", bus: "Xe buýt", van: "Xe van", pedestrian: "Người đi bộ",
};

function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function first(root: Element | Document, selector: string): Element | null {
  return root.querySelector(selector);
}

function conditionText(event: Element): string {
  const condition = first(event, "StartTrigger Condition");
  if (!condition) return "—";
  const byValue = first(condition, "ByValueCondition > *");
  const byEntity = first(condition, "EntityCondition > *");
  const node = byEntity ?? byValue;
  if (!node) return condition.getAttribute("name") ?? "—";
  const value = node.getAttribute("value") ?? node.getAttribute("distance") ?? "";
  const rule = node.getAttribute("rule") ?? "";
  const ruleSign: Record<string, string> = { lessThan: "<", greaterThan: ">", equalTo: "=", lessOrEqual: "≤", greaterOrEqual: "≥" };
  const subject = byEntity ? first(condition, "TriggeringEntities EntityRef")?.getAttribute("entityRef") : null;
  return `${node.tagName}${subject ? ` (${subject})` : ""} ${ruleSign[rule] ?? rule} ${value}`.trim();
}

export function parseXosc(text: string): ParsedXosc {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.querySelector("parsererror")) throw new Error("Tệp .xosc bị lỗi định dạng, không đọc được.");
  const header = first(doc, "FileHeader");
  const speeds = new Map<string, number>();
  const positions = new Map<string, { x: number | null; y: number | null; heading: number | null }>();
  doc.querySelectorAll("Init Actions Private").forEach((item) => {
    const ref = item.getAttribute("entityRef") ?? "";
    const world = first(item, "TeleportAction WorldPosition");
    if (world) positions.set(ref, { x: num(world.getAttribute("x")), y: num(world.getAttribute("y")), heading: num(world.getAttribute("h")) });
    const speed = num(first(item, "AbsoluteTargetSpeed")?.getAttribute("value"));
    if (speed !== null) speeds.set(ref, speed);
  });
  const entities: XoscEntity[] = Array.from(doc.querySelectorAll("Entities > ScenarioObject")).map((item) => {
    const name = item.getAttribute("name") ?? "?";
    const vehicle = first(item, "Vehicle");
    const pedestrian = first(item, "Pedestrian");
    const kind = vehicle?.getAttribute("vehicleCategory") ?? (pedestrian ? "pedestrian" : first(item, "CatalogReference") ? "catalog" : "?");
    const model = vehicle?.getAttribute("name") ?? pedestrian?.getAttribute("model") ?? pedestrian?.getAttribute("name") ?? first(item, "CatalogReference")?.getAttribute("entryName") ?? "";
    const pos = positions.get(name) ?? { x: null, y: null, heading: null };
    const speed = speeds.get(name);
    return { name, kind, model, ...pos, speed: speed === undefined ? null : speed };
  });
  const events: XoscEvent[] = Array.from(doc.querySelectorAll("Storyboard Event")).map((event) => {
    const group = event.closest("ManeuverGroup");
    const actor = group ? Array.from(group.querySelectorAll("Actors EntityRef")).map((ref) => ref.getAttribute("entityRef")).join(", ") : "";
    const action = Array.from(event.querySelectorAll(":scope > Action")).map((item) => {
      const inner = first(item, "PrivateAction > * > *") ?? first(item, "PrivateAction > *") ?? first(item, "GlobalAction > *");
      return `${item.getAttribute("name") ?? ""}${inner ? ` (${inner.tagName})` : ""}`;
    }).join("; ");
    return { name: event.getAttribute("name") ?? "?", actor, action, condition: conditionText(event) };
  });
  const weather = first(doc, "Init EnvironmentAction Weather");
  const precipitation = first(doc, "Init EnvironmentAction Precipitation");
  const fog = first(doc, "Init EnvironmentAction Fog");
  const weatherText = [
    weather?.getAttribute("cloudState"),
    precipitation ? `${precipitation.getAttribute("precipitationType")} ${precipitation.getAttribute("intensity") ?? ""}`.trim() : null,
    fog ? `tầm nhìn ${fog.getAttribute("visualRange")} m` : null,
  ].filter(Boolean).join(" · ");
  const route = Array.from(doc.querySelectorAll("AssignRouteAction Waypoint WorldPosition"))
    .map((item) => ({ x: num(item.getAttribute("x")), y: num(item.getAttribute("y")) }))
    .filter((p): p is { x: number; y: number } => p.x !== null && p.y !== null);
  const stop = first(doc, "StopTrigger SimulationTimeCondition");
  return {
    description: header?.getAttribute("description") ?? "",
    author: header?.getAttribute("author") ?? "",
    map: first(doc, "RoadNetwork LogicFile")?.getAttribute("filepath") ?? "—",
    entities,
    events,
    weather: weatherText || "—",
    timeOfDay: first(doc, "Init EnvironmentAction TimeOfDay")?.getAttribute("dateTime")?.slice(11, 16) ?? "—",
    route,
    stopAfter: stop ? `${stop.getAttribute("value")} s` : "—",
  };
}

/** Top-down plot of the initial positions in OpenSCENARIO coordinates (x right, y up). */
function XoscPlot({ parsed }: { parsed: ParsedXosc }) {
  const placed = parsed.entities.filter((item) => item.x !== null && item.y !== null);
  if (!placed.length) return <div className="inline-note">Tệp không có vị trí WorldPosition để vẽ sơ đồ.</div>;
  const points = [...placed.map((item) => ({ x: item.x!, y: item.y! })), ...parsed.route];
  const minX = Math.min(...points.map((p) => p.x)) - 10;
  const maxX = Math.max(...points.map((p) => p.x)) + 10;
  const minY = Math.min(...points.map((p) => p.y)) - 10;
  const maxY = Math.max(...points.map((p) => p.y)) + 10;
  const span = Math.max(maxX - minX, maxY - minY, 30);
  const size = 320;
  const scale = size / span;
  const tx = (x: number) => (x - minX) * scale + (size - (maxX - minX) * scale) / 2;
  const ty = (y: number) => size - ((y - minY) * scale + (size - (maxY - minY) * scale) / 2);
  return (
    <figure className="xosc-plot">
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Vị trí xuất phát các thực thể">
        <rect width={size} height={size} fill="var(--bg-subtle)" />
        {parsed.route.length > 1 && (
          <polyline points={parsed.route.map((p) => `${tx(p.x)},${ty(p.y)}`).join(" ")} fill="none" stroke="var(--blue)" strokeWidth="2" strokeDasharray="6 4" />
        )}
        {placed.map((item, index) => {
          const ego = item.name === "hero" || index === 0;
          const h = item.heading ?? 0;
          const x = tx(item.x!);
          const y = ty(item.y!);
          return (
            <g key={item.name}>
              <line x1={x} y1={y} x2={x + Math.cos(h) * 14} y2={y - Math.sin(h) * 14} stroke={ego ? "var(--blue)" : "var(--red)"} strokeWidth="2" />
              <circle cx={x} cy={y} r={ego ? 7 : 6} fill={ego ? "var(--blue)" : item.kind === "pedestrian" ? "var(--green)" : "var(--red)"} />
              <text x={x + 9} y={y - 8} fontSize="11" fill="var(--fg)">{item.name}</text>
            </g>
          );
        })}
      </svg>
      <figcaption className="muted">Nhìn từ trên xuống theo toạ độ trong tệp .xosc. Mũi tên là hướng xuất phát, nét đứt là tuyến đường của ego.</figcaption>
    </figure>
  );
}

/** Preview of the .xosc attached to a test case: scene sketch, entities, storyboard events, environment and raw XML. */
export function XoscPreview({ caseId, fileName, grounding }: { caseId: number; fileName: string; grounding?: Grounding | null }) {
  const { session } = useSession();
  const token = session?.access_token;
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) return;
    let active = true;
    api.downloadXosc(token, caseId)
      .then((blob) => blob.text())
      .then((value) => { if (active) { setText(value); setError(""); } })
      .catch((reason) => { if (active) setError(reason instanceof ApiError ? reason.message : "Không tải được tệp .xosc."); });
    return () => { active = false; };
  }, [token, caseId]);

  const parsed = useMemo(() => {
    if (text === null) return null;
    try {
      return { value: parseXosc(text), error: "" };
    } catch (reason) {
      return { value: null, error: reason instanceof Error ? reason.message : "Không đọc được tệp .xosc." };
    }
  }, [text]);

  if (error) return <div className="notice notice-error xosc-preview-note">{error}</div>;
  if (text === null || parsed === null) return <div className="inline-note"><span className="spinner" />Đang tải tệp .xosc…</div>;
  const data = parsed.value;
  return (
    <div className="xosc-preview">
      {parsed.error && <div className="notice notice-error">{parsed.error}</div>}
      {data && (
        <div className="detail-grid xosc-preview-grid">
          <div>
            <dl className="details">
              <dt>Bản đồ</dt><dd>{data.map}</dd>
              <dt>Môi trường</dt><dd>{data.weather} · {data.timeOfDay}</dd>
              <dt>Dừng sau</dt><dd>{data.stopAfter}</dd>
              {data.description && <><dt>Mô tả</dt><dd>{data.description}</dd></>}
              {data.author && <><dt>Tác giả</dt><dd>{data.author}</dd></>}
            </dl>
          </div>
          {grounding ? <ScenePreview grounding={grounding} /> : <XoscPlot parsed={data} />}
        </div>
      )}
      {data && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Thực thể</th><th>Loại</th><th>Mẫu xe / người</th><th>Vị trí xuất phát (x, y)</th><th>Tốc độ đầu</th></tr></thead>
            <tbody>
              {data.entities.map((item) => (
                <tr key={item.name}>
                  <td><strong>{item.name}</strong>{item.name === "hero" && <div className="muted">Xe ego</div>}</td>
                  <td>{CATEGORY_LABELS[item.kind] ?? item.kind}</td>
                  <td>{item.model || "—"}</td>
                  <td>{item.x !== null && item.y !== null ? `${item.x.toFixed(1)}, ${item.y.toFixed(1)}` : "—"}</td>
                  <td>{item.speed !== null ? `${(item.speed * 3.6).toFixed(1)} km/h` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.events.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Sự kiện</th><th>Thực thể</th><th>Hành động</th><th>Điều kiện kích hoạt</th></tr></thead>
            <tbody>
              {data.events.map((item, index) => (
                <tr key={`${item.name}-${index}`}><td>{item.name}</td><td>{item.actor || "—"}</td><td>{item.action || "—"}</td><td>{item.condition}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <details className="setup-technical xosc-raw">
        <summary>Xem nội dung tệp .xosc ({(text.length / 1024).toFixed(1)} KB · OpenSCENARIO 1.0, định dạng XML)</summary>
        <pre>{text}</pre>
      </details>
      <div className="form-actions">
        <button type="button" className="button" onClick={() => downloadBlob(new Blob([text], { type: "application/xml" }), fileName)}>Tải tệp .xosc</button>
      </div>
    </div>
  );
}
