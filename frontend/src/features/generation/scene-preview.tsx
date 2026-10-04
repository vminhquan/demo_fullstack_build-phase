import type { Grounding, PlacedEntity } from "@/lib/api";

const ACTOR_COLORS: Record<string, string> = {
  car: "var(--amber)",
  truck: "var(--red)",
  motorcycle: "var(--purple)",
  bicycle: "var(--green)",
  pedestrian: "var(--red)",
};

type Point = { x: number; y: number };

/** Top-down sketch in the ego frame (ego at the bottom, driving up). CARLA is left-handed: +y is to the right. */
export function ScenePreview({ grounding }: { grounding: Grounding }) {
  const { ego } = grounding;
  const yaw = (ego.yaw_deg * Math.PI) / 180;
  const fwd = { x: Math.cos(yaw), y: Math.sin(yaw) };
  const right = { x: -fwd.y, y: fwd.x };
  const toLocal = (p: Point) => {
    const dx = p.x - ego.x;
    const dy = p.y - ego.y;
    return { x: dx * right.x + dy * right.y, y: -(dx * fwd.x + dy * fwd.y) };
  };
  const entities: PlacedEntity[] = [ego, ...grounding.actors];
  const points = entities.flatMap((entity) => [entity, ...entity.preview_waypoints].map(toLocal));
  const minX = Math.min(-12, ...points.map((p) => p.x)) - 6;
  const maxX = Math.max(12, ...points.map((p) => p.x)) + 6;
  const minY = Math.min(...points.map((p) => p.y)) - 8;
  const maxY = Math.max(8, ...points.map((p) => p.y)) + 8;
  const width = maxX - minX;
  const height = maxY - minY;

  return (
    <figure className="scene-preview">
      <svg viewBox={`${minX} ${minY} ${width} ${height}`} role="img" aria-label="Sơ đồ vị trí ego và các tác nhân trên bản đồ">
        <line x1={0} y1={maxY} x2={0} y2={minY} className="scene-axis" />
        {entities.map((entity) => {
          const path = entity.preview_waypoints.map(toLocal);
          if (path.length < 2) return null;
          return <polyline key={`${entity.entity_name}-path`} points={path.map((p) => `${p.x},${p.y}`).join(" ")} className="scene-path" stroke={entity === ego ? "var(--blue)" : ACTOR_COLORS[entity.actor_type] ?? "var(--muted)"} />;
        })}
        {entities.map((entity) => {
          const p = toLocal(entity);
          const heading = entity.yaw_deg - ego.yaw_deg;
          const color = entity === ego ? "var(--blue)" : ACTOR_COLORS[entity.actor_type] ?? "var(--muted)";
          const walker = entity.actor_type === "pedestrian";
          return (
            <g key={entity.entity_name} transform={`translate(${p.x} ${p.y}) rotate(${heading})`}>
              {walker ? <circle r={1.2} fill={color} /> : <rect x={-1} y={-2.3} width={2} height={4.6} rx={0.5} fill={color} />}
              <text y={walker ? -2 : -3.2} transform={`rotate(${-heading})`} className="scene-label">{entity === ego ? "Ego" : entity.entity_name.replace("adversary_", "A")}</text>
            </g>
          );
        })}
      </svg>
      <figcaption className="muted">Nhìn từ trên xuống, ego chạy lên trên. Đường nét đứt là quỹ đạo dự kiến ~5 giây đầu. Toạ độ thật trên {grounding.map_name}.</figcaption>
    </figure>
  );
}
