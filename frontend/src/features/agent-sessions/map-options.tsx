"use client";

import { BuilderMapOptions, DangerLevel, MetadataOptions } from "@/lib/api";
import { labels } from "@/shared/auth/session-context";
import { Icon } from "@/shared/ui/icons";

/** Values ticked for one map (the map code is the key they are stored under). */
export type MapOptions = Omit<BuilderMapOptions, "map_code">;

const DANGER_LEVELS: DangerLevel[] = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
const EGO_GROUPS: Record<string, string> = { car: "Ô tô", van: "Xe van", truck: "Xe tải", bus: "Xe buýt" };

export const emptyMapOptions = (): MapOptions => ({
  ego_vehicle_codes: [],
  adversary_types: [],
  environment_codes: [],
  danger_levels: [],
});

type Choice = { value: string; label: string; hint?: string };

/** Values of one map's CARLA data (options carry the snapshot ids they come from). */
export function choicesFor(options: MetadataOptions | null, snapshotIds: number[]) {
  const inMap = (item: { snapshot_ids: number[] }) => item.snapshot_ids.some((id) => snapshotIds.includes(id));
  if (!options) return null;
  return {
    egos: options.ego_vehicles.filter(inMap).map<Choice & { group: string }>((item) => ({ value: item.code, label: item.label, hint: item.code, group: EGO_GROUPS[item.base_type] ?? item.base_type })),
    adversaries: options.adversary_types.filter(inMap).map<Choice>((item) => ({ value: item.code, label: item.label, hint: item.code })),
    environments: options.environments.filter(inMap).map<Choice & { group: string }>((item) => ({ value: item.code, label: item.label, hint: item.code, group: item.group === "standard" ? "Điều kiện chuẩn" : "Preset thời tiết CARLA" })),
  };
}
export type MapChoices = NonNullable<ReturnType<typeof choicesFor>>;

/* ---------------------------------------------------------------- map picker (required) */

export function MapPicker({
  available, selected, busy, onToggle,
}: {
  available: string[] | null;
  selected: string[];
  busy: boolean;
  onToggle: (map: string) => void;
}) {
  return (
    <section className="map-required" aria-labelledby="map-required-title">
      <div className="map-required-head">
        <div>
          <strong id="map-required-title">Bản đồ</strong>
          <span className="required-badge">Bắt buộc</span>
        </div>
        {selected.length > 0 && <span className="map-required-count">Đã chọn {selected.length} bản đồ</span>}
      </div>
      {available === null ? (
        <span className="field-help"><span className="spinner" />Đang tải danh sách bản đồ…</span>
      ) : available.length === 0 ? (
        <span className="field-help">Nguồn dữ liệu chưa có bản đồ nào nên chưa tạo được test case. Chọn nguồn ở trang Start up.</span>
      ) : (
        <>
          <div className="map-picker" role="group" aria-label="Chọn bản đồ">
            {available.map((map) => {
              const on = selected.includes(map);
              return (
                <button type="button" key={map} className={`map-chip ${on ? "selected" : ""}`} aria-pressed={on} disabled={busy} onClick={() => onToggle(map)}>
                  <span className="map-chip-box" aria-hidden="true">{on && <Icon name="tick" size={11} />}</span>
                  {map}
                </button>
              );
            })}
          </div>
          <span className="field-help">
            {selected.length === 0
              ? "Chọn ít nhất một bản đồ để tạo test case."
              : "Mỗi bản đồ có tùy chọn riêng trong mục “Bản đồ”. Bản đồ không có loại đường mà mô tả cần sẽ không sinh được."}
          </span>
        </>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- per-map tabs */

function CheckGroup({
  title, help, choices, values, disabled, onChange,
}: {
  title: string;
  help: string;
  choices: (Choice & { group?: string })[];
  values: string[];
  disabled: boolean;
  onChange: (values: string[]) => void;
}) {
  const groups = [...new Set(choices.map((choice) => choice.group ?? ""))];
  const toggle = (value: string) => onChange(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);
  return (
    <fieldset className="check-category" disabled={disabled}>
      <legend>
        {title}
        <span className={`check-category-state ${values.length ? "chosen" : ""}`}>{values.length ? `Đã chọn ${values.length}` : "Agent tự chọn"}</span>
        {values.length > 0 && <button type="button" className="text-button" onClick={() => onChange([])}>Bỏ chọn</button>}
      </legend>
      <span className="field-help">{help}</span>
      {choices.length === 0 ? (
        <span className="field-help">Dữ liệu CARLA của bản đồ này không có lựa chọn nào.</span>
      ) : (
        <div className="check-list">
          {groups.map((group) => (
            <div key={group || "all"} className="check-list-group">
              {group && <div className="check-list-label">{group}</div>}
              {choices.filter((choice) => (choice.group ?? "") === group).map((choice) => (
                <label key={choice.value} className={`check-option ${values.includes(choice.value) ? "checked" : ""}`}>
                  <input type="checkbox" checked={values.includes(choice.value)} onChange={() => toggle(choice.value)} />
                  <span>{choice.label}{choice.hint && choice.hint !== choice.label && <span className="case-key"> {choice.hint}</span>}</span>
                </label>
              ))}
            </div>
          ))}
        </div>
      )}
    </fieldset>
  );
}

export function MapOptionTabs({
  maps, active, options, choices, busy, onActive, onChange, onRemove, onApplyAll,
}: {
  maps: string[];
  active: string;
  options: Record<string, MapOptions>;
  choices: Record<string, MapChoices | null>;
  busy: boolean;
  onActive: (map: string) => void;
  onChange: (map: string, next: MapOptions) => void;
  onRemove: (map: string) => void;
  onApplyAll: (map: string) => void;
}) {
  if (maps.length === 0)
    return <div className="map-tabs-empty">Chọn ít nhất một bản đồ ở trên để chỉnh tùy chọn riêng của từng bản đồ.</div>;

  const current = options[active] ?? emptyMapOptions();
  const mapChoices = choices[active];
  const set = (patch: Partial<MapOptions>) => onChange(active, { ...current, ...patch });

  return (
    <div className="map-tabs">
      <div className="map-tab-list" role="tablist" aria-label="Tùy chọn theo bản đồ">
        {maps.map((map) => {
          return (
            <div key={map} className={`map-tab ${map === active ? "active" : ""}`}>
              <button type="button" role="tab" aria-selected={map === active} onClick={() => onActive(map)}>
                {map}
              </button>
              <button type="button" className="map-tab-remove" title={`Bỏ bản đồ ${map}`} aria-label={`Bỏ bản đồ ${map}`} disabled={busy} onClick={() => onRemove(map)}>
                <Icon name="x" size={11} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="map-tab-panel" role="tabpanel">
        <div className="map-tab-top">
          <span className="field-help">
            Chọn bao nhiêu giá trị tùy ý; mỗi giá trị đã chọn đều được dùng. Danh mục để trống do Agent tự chọn.
          </span>
          {maps.length > 1 && (
            <button type="button" className="button" disabled={busy} onClick={() => onApplyAll(active)} title="Sao chép các lựa chọn sang các bản đồ khác (bỏ qua giá trị bản đồ đó không có)">
              Áp dụng cho tất cả bản đồ
            </button>
          )}
        </div>

        {mapChoices === null ? (
          <div className="inline-note"><span className="spinner" />Đang tải dữ liệu CARLA của {active}…</div>
        ) : (
          <div className="check-categories">
            <CheckGroup title="Xe ego" help="Xe được kiểm thử." choices={mapChoices?.egos ?? []} values={current.ego_vehicle_codes} disabled={busy} onChange={(values) => set({ ego_vehicle_codes: values })} />
            <CheckGroup title="Tác nhân" help="Đối tượng gây tình huống." choices={mapChoices?.adversaries ?? []} values={current.adversary_types} disabled={busy} onChange={(values) => set({ adversary_types: values })} />
            <CheckGroup title="Môi trường" help="Thời tiết và ánh sáng." choices={mapChoices?.environments ?? []} values={current.environment_codes} disabled={busy} onChange={(values) => set({ environment_codes: values })} />
            <CheckGroup
              title="Mức nguy hiểm"
              help="Mức gắn cho test case."
              choices={DANGER_LEVELS.map((level) => ({ value: level, label: labels.danger[level] }))}
              values={current.danger_levels}
              disabled={busy}
              onChange={(values) => set({ danger_levels: values as DangerLevel[] })}
            />
          </div>
        )}
      </div>
    </div>
  );
}
