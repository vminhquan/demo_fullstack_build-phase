"use client";

import { useProjectPath } from "@/shared/ui/project-path";
import Link from "next/link";
import { ReactNode, useEffect, useMemo, useState } from "react";

import { api, ApiError, CaseMetadata, CatalogSnapshot, MetadataOptions } from "@/lib/api";
import { labels, useSession } from "@/shared/auth/session-context";

type Scope = "all" | number;

const EGO_GROUPS: Record<string, string> = { car: "Ô tô", van: "Xe van", truck: "Xe tải", bus: "Xe buýt" };

function originLabel(item: CatalogSnapshot) {
  if (item.is_default) return "Mặc định";
  return item.source === "WORKER" ? "Đồng bộ qua worker" : `Import${item.label && item.label !== item.map_name ? ` · ${item.label}` : ""}`;
}

/** A select whose current value stays visible even when it is not in the CARLA data (older cases). */
function OptionSelect({
  label, value, onChange, disabled, placeholder, groups, help,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  placeholder: string;
  groups: { label: string; options: { value: string; label: string }[] }[];
  help?: ReactNode;
}) {
  const known = groups.some((group) => group.options.some((option) => option.value === value));
  const visible = groups.filter((group) => group.options.length);
  return (
    <label className="field">
      {label}
      <select required disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="" disabled>{placeholder}</option>
        {value && !known && <option value={value}>{value} (giá trị cũ, không có trong dữ liệu CARLA)</option>}
        {visible.length === 1
          ? visible[0].options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)
          : visible.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </optgroup>
          ))}
      </select>
      {help && <span className="field-help">{help}</span>}
    </label>
  );
}

/** The 5 required test case metadata. Choices come from the CARLA catalogs (defaults, imports, Worker syncs). */
export function MetadataFields({
  value,
  onChange,
  tagsText,
  onTagsChange,
  disabled = false,
  catalogSnapshotId = null,
  hideMap = false,
}: {
  value: CaseMetadata;
  onChange: (value: CaseMetadata) => void;
  tagsText: string;
  onTagsChange: (value: string) => void;
  disabled?: boolean;
  catalogSnapshotId?: number | null;
  /** The map is picked elsewhere (Test Case Builder picks several maps from Start up). */
  hideMap?: boolean;
}) {
  const p = useProjectPath();
  const { session } = useSession();
  const token = session?.access_token;
  const [options, setOptions] = useState<MetadataOptions | null>(null);
  const [error, setError] = useState("");
  const [scope, setScope] = useState<Scope>(catalogSnapshotId ?? "all");

  useEffect(() => {
    if (!token) return;
    let active = true;
    api.getMetadataOptions(token)
      .then((result) => { if (active) setOptions(result); })
      .catch((reason) => { if (active) setError(reason instanceof ApiError ? reason.message : "Không tải được danh mục CARLA."); });
    return () => { active = false; };
  }, [token]);
  // Follow a new snapshot from the parent (e.g. regenerated on other CARLA data) without an effect.
  const [followed, setFollowed] = useState(catalogSnapshotId);
  if (followed !== catalogSnapshotId) {
    setFollowed(catalogSnapshotId);
    if (catalogSnapshotId !== null) setScope(catalogSnapshotId);
  }

  const scoped = useMemo(() => {
    if (!options) return null;
    if (scope === "all") return options;
    const snapshot = options.snapshots.find((item) => item.id === scope);
    const inScope = <T extends { snapshot_ids: number[] }>(item: T) => item.snapshot_ids.includes(scope);
    return {
      ...options,
      maps: options.maps.filter((item) => inScope(item) || (!item.has_lane_data && !!snapshot && item.carla_versions.includes(snapshot.carla_version))),
      ego_vehicles: options.ego_vehicles.filter(inScope),
      adversary_types: options.adversary_types.filter(inScope),
      environments: options.environments.filter(inScope),
    };
  }, [options, scope]);

  const patch = (key: keyof CaseMetadata, next: string) => onChange({ ...value, [key]: next });
  const noCatalog = options !== null && options.snapshots.length === 0;

  return (
    <div className="form form-columns metadata-form">
      <div className="section-label field-full">{hideMap ? "Metadata của test case" : "5 metadata bắt buộc của test case"}</div>
      {error && <div className="notice notice-error field-full">{error}</div>}

      {noCatalog || error ? (
        <>
          <div className="notice notice-info field-full">
            Chưa có dữ liệu CARLA nào để chọn. Hãy <Link className="link" href={p("/start-up")}>import dữ liệu CARLA ở Start up</Link> hoặc nhập tạm bằng tay bên dưới.
          </div>
          {([
            ["map_code", "Bản đồ (map)", "Town05"],
            ["ego_vehicle_code", "Xe ego", "vehicle.tesla.model3"],
            ["adversary_type", "Loại tác nhân", "pedestrian"],
            ["environment_code", "Môi trường", "heavy_rain"],
          ] as const).filter(([key]) => !hideMap || key !== "map_code").map(([key, label, placeholder]) => (
            <label className="field" key={key}>
              {label}
              <input required disabled={disabled} value={value[key]} onChange={(event) => patch(key, event.target.value)} placeholder={placeholder} />
            </label>
          ))}
        </>
      ) : !scoped ? (
        <div className="inline-note field-full"><span className="spinner" />Đang tải danh mục từ dữ liệu CARLA…</div>
      ) : (
        <>
          <label className="field field-full catalog-scope">
            Lấy danh mục từ
            <select disabled={disabled} value={String(scope)} onChange={(event) => setScope(event.target.value === "all" ? "all" : Number(event.target.value))}>
              <option value="all">Tất cả dữ liệu CARLA khả dụng ({options!.snapshots.length} bản)</option>
              {options!.snapshots.map((item) => (
                <option key={item.id} value={item.id}>{item.map_name} · CARLA {item.carla_version} · {originLabel(item)}</option>
              ))}
            </select>
            <span className="field-help">Các lựa chọn bên dưới lấy từ dữ liệu CARLA mặc định và dữ liệu import/đồng bộ từ máy CARLA của bạn. Danh sách tự cập nhật khi có dữ liệu mới.</span>
          </label>
          {!hideMap && <OptionSelect
            label="Bản đồ (map)"
            value={value.map_code}
            onChange={(next) => patch("map_code", next)}
            disabled={disabled}
            placeholder="— Chọn bản đồ —"
            groups={[
              { label: "Có dữ liệu làn đường (Agent sinh được)", options: scoped.maps.filter((m) => m.has_lane_data).map((m) => ({ value: m.code, label: `${m.code} · CARLA ${m.carla_versions.join(", ")}` })) },
              { label: "Có trên máy CARLA, chưa có dữ liệu làn", options: scoped.maps.filter((m) => !m.has_lane_data).map((m) => ({ value: m.code, label: m.code })) },
            ]}
            help={scoped.maps.find((m) => m.code === value.map_code && !m.has_lane_data) ? "Map này chưa có dữ liệu làn đường: chạy được trên CARLA nhưng Agent chưa sinh kịch bản trên map này." : undefined}
          />}
          <OptionSelect
            label="Xe ego"
            value={value.ego_vehicle_code}
            onChange={(next) => patch("ego_vehicle_code", next)}
            disabled={disabled}
            placeholder="— Chọn xe ego —"
            groups={Object.entries(EGO_GROUPS).map(([base, groupLabel]) => ({
              label: groupLabel,
              options: scoped.ego_vehicles.filter((v) => v.base_type === base).map((v) => ({ value: v.code, label: `${v.label} — ${v.code}` })),
            }))}
          />
          <OptionSelect
            label="Loại tác nhân"
            value={value.adversary_type}
            onChange={(next) => patch("adversary_type", next)}
            disabled={disabled}
            placeholder="— Chọn tác nhân —"
            groups={[{ label: "Tác nhân", options: scoped.adversary_types.map((a) => ({ value: a.code, label: `${a.label} (${a.code})` })) }]}
          />
          <OptionSelect
            label="Môi trường"
            value={value.environment_code}
            onChange={(next) => patch("environment_code", next)}
            disabled={disabled}
            placeholder="— Chọn môi trường —"
            groups={[
              { label: "Điều kiện chuẩn (Agent dùng)", options: scoped.environments.filter((e) => e.group === "standard").map((e) => ({ value: e.code, label: `${e.label} (${e.code})` })) },
              { label: "Preset thời tiết của CARLA", options: scoped.environments.filter((e) => e.group === "carla_preset").map((e) => ({ value: e.code, label: `${e.label} (${e.code})` })) },
            ]}
          />
        </>
      )}

      <label className="field">
        Mức nguy hiểm
        <select disabled={disabled} value={value.danger_level} onChange={(event) => patch("danger_level", event.target.value)}>
          {Object.entries(labels.danger).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </label>
      <label className="field">
        Thẻ (cách nhau bởi dấu phẩy)
        <input disabled={disabled} value={tagsText} onChange={(event) => onTagsChange(event.target.value)} placeholder="pedestrian, crossing, rain" />
      </label>
    </div>
  );
}
