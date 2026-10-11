"use client";

import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { api, BuilderSession, BuilderSessionCreate, MetadataOptions } from "@/lib/api";
import { labels, useSession } from "@/shared/auth/session-context";
import { Icon } from "@/shared/ui/icons";

import { choicesFor, egoChoicesFor, EgoPicker, emptyMapOptions, MapOptions, MapOptionTabs, MapPicker } from "./map-options";

const EXAMPLE_PROMPTS = [
  { label: "Xe tải phanh gấp trên cao tốc", text: "Xe tải phía trước phanh gấp trên đường thẳng, ego 50 km/h, cách 25 m." },
  { label: "Người đi bộ băng ngang ban đêm", text: "Người đi bộ băng qua đường từ bên trái vào ban đêm, ego 35 km/h." },
  { label: "Xe máy tạt đầu ở ngã tư", text: "Xe máy tạt đầu từ làn bên phải khi ego đi 40 km/h vào ngã tư, cách 15 m." },
];

function parseTags(text: string) {
  return text.split(",").map((tag) => tag.trim()).filter(Boolean);
}

/** ChatGPT-style composer of a new session: one required ego, required maps, one description box, per-map options in a
 * collapsible panel. */
export function SessionInputForm({ busy, onSubmit }: { busy: boolean; onSubmit: (body: BuilderSessionCreate) => void }) {
  const { session } = useSession();
  const token = session?.access_token;
  // Once the project has CARLA data of its own (synced by a Bridge, or imported), only those maps are offered and
  // the Agent generates against them; the built-in maps are the fallback for projects without any.
  const [source, setSource] = useState<"PROJECT" | "DEFAULT" | null>(null);
  const [snapshotsByMap, setSnapshotsByMap] = useState<Record<string, number[]> | null>(null);
  const [metadata, setMetadata] = useState<MetadataOptions | null>(null);
  useEffect(() => {
    if (!token) return;
    let active = true;
    (async () => {
      const own = await api.listCatalogSnapshots(token, "PROJECT").catch(() => []);
      const items = own.length ? own : await api.listCatalogSnapshots(token, "DEFAULT").catch(() => []);
      if (!active) return;
      // Newest first: keep one snapshot per map (the one generation resolves to), so dropdowns show that data only.
      const byMap: Record<string, number[]> = {};
      items.forEach((item) => { if (!byMap[item.map_name]) byMap[item.map_name] = [item.id]; });
      setSource(own.length ? "PROJECT" : "DEFAULT");
      setSnapshotsByMap(byMap);
    })();
    api.getMetadataOptions(token).then((result) => { if (active) setMetadata(result); }).catch(() => { /* lists stay empty: Agent chooses */ });
    return () => { active = false; };
  }, [token]);
  const available = useMemo(() => (snapshotsByMap ? Object.keys(snapshotsByMap).sort() : null), [snapshotsByMap]);
  const choices = useMemo(() => Object.fromEntries((available ?? []).map((map) => [map, choicesFor(metadata, snapshotsByMap?.[map] ?? [])])), [available, metadata, snapshotsByMap]);
  const egoChoices = useMemo(() => egoChoicesFor(available, choices), [available, choices]);

  // The one ego of every test case in the session (the Agent never picks it).
  const [ego, setEgo] = useState("");
  const [egoNote, setEgoNote] = useState("");
  const [maps, setMaps] = useState<string[]>([]);
  const [mapOptions, setMapOptions] = useState<Record<string, MapOptions>>({});
  const [activeMap, setActiveMap] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagsText, setTagsText] = useState("");
  // Two panels under the composer: general options, and per-map options.
  const [panel, setPanel] = useState<"" | "options" | "maps">("");
  const togglePanel = (next: "options" | "maps") => setPanel(panel === next ? "" : next);
  const [error, setError] = useState("");
  const textRef = useRef<HTMLTextAreaElement>(null);
  const optionsOf = (map: string) => mapOptions[map] ?? emptyMapOptions();
  const current = maps.includes(activeMap) ? activeMap : maps[0] ?? "";
  const egoLabel = egoChoices?.find((item) => item.value === ego)?.label ?? ego;
  const hasEgo = (map: string, code = ego) => !code || (choices[map]?.egos ?? []).some((item) => item.value === code);
  // Maps and an ego are required: the Agent only places the scenario on the picked maps, with the picked ego. A missing
  // ego stays sendable so submit() can say so.
  const canSend = !busy && description.trim().length >= 3 && maps.length > 0;

  function changeEgo(next: string) {
    setEgo(next);
    setError("");
    // A picked map whose CARLA data lacks the new ego cannot host it: drop it and say so.
    const dropped = maps.filter((map) => !hasEgo(map, next));
    if (dropped.length) setMaps(maps.filter((map) => hasEgo(map, next)));
    setEgoNote(dropped.length ? `Đã bỏ bản đồ ${dropped.join(", ")}: dữ liệu CARLA của bản đồ đó không có xe này.` : "");
  }

  function toggleMap(map: string) {
    if (maps.includes(map)) { removeMap(map); return; }
    setError("");
    setEgoNote("");
    setMaps((available ?? []).filter((item) => item === map || maps.includes(item)));
    setMapOptions((previous) => ({ ...previous, [map]: previous[map] ?? emptyMapOptions() }));
    setActiveMap(map);
  }
  function removeMap(map: string) {
    setMaps(maps.filter((item) => item !== map));
    if (current === map) setActiveMap(maps.find((item) => item !== map) ?? "");
  }
  function changeMapOptions(map: string, next: MapOptions) {
    setMapOptions((previous) => ({ ...previous, [map]: next }));
  }
  // Copy one map's settings to the others, keeping only values that exist in each target map's CARLA data.
  function applyToAll(from: string) {
    const source = optionsOf(from);
    const others = maps.filter((map) => map !== from);
    setMapOptions((previous) => {
      const next = { ...previous };
      others.forEach((map) => {
        const target = choices[map];
        const keep = (values: string[], list: { value: string }[] | undefined) => values.filter((value) => list?.some((item) => item.value === value));
        next[map] = {
          ego_vehicle_codes: [],
          adversary_types: keep(source.adversary_types, target?.adversaries),
          environment_codes: keep(source.environment_codes, target?.environments),
          danger_levels: [...source.danger_levels],
        };
      });
      return next;
    });
  }

  function changeDescription(next: string) {
    setDescription(next);
    setError("");
    // Grow with the text up to the CSS max-height, like a chat composer.
    const box = textRef.current;
    if (box) { box.style.height = "auto"; box.style.height = `${box.scrollHeight}px`; }
  }
  function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const text = description.trim();
    if (!ego) { setError("Chọn một xe ego để tạo test case."); return; }
    if (maps.length === 0) { setError("Chọn ít nhất một bản đồ để tạo test case."); return; }
    if (text.length < 3) { setError("Mô tả tình huống cần ít nhất 3 ký tự."); textRef.current?.focus(); return; }
    // An empty title is summarised by the Backend (Agent), like a chat app names a new conversation.
    onSubmit({
      title: title.trim() || null,
      description: text,
      catalog_source: source ?? "DEFAULT",
      ego_vehicle_code: ego,
      // Pin the snapshot the choices came from, so generation uses the same CARLA data the user picked from.
      maps: maps.map((map) => ({ map_code: map, catalog_snapshot_id: snapshotsByMap?.[map]?.[0] ?? null, ...optionsOf(map), ego_vehicle_codes: [ego] })),
      tag_names: parseTags(tagsText),
    });
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter breaks the line; ignore Enter while a Vietnamese IME is composing.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSend) submit();
    }
  }

  return (
    <form className="chat-create" onSubmit={submit} noValidate>
      <h1 className="chat-title">Bạn muốn kiểm thử tình huống nào?</h1>
      <p className="chat-lead">Chọn xe ego và bản đồ, rồi mô tả tình huống bằng tiếng Việt tự nhiên. Agent dựng kịch bản OpenSCENARIO trên đúng bản đồ đã chọn và lưu mỗi kịch bản thành bản nháp v1 để duyệt.</p>

      {source && (
        <div className="field-help catalog-source-note">
          {source === "PROJECT"
            ? <>Dùng <strong>dữ liệu CARLA của Project</strong> (đồng bộ từ Bridge): bản đồ, phương tiện và thời tiết lấy từ CARLA của máy người dùng.</>
            : <>Đang dùng <strong>bộ map mặc định</strong>. Kết nối Bridge và bấm “Đồng bộ dữ liệu CARLA” ở Start up để dùng map từ CARLA của bạn.</>}
        </div>
      )}
      <EgoPicker choices={egoChoices} value={ego} mapCount={available?.length ?? 0} busy={busy} note={egoNote} onChange={changeEgo} />
      <MapPicker
        available={available}
        selected={maps}
        busy={busy}
        unavailable={(map) => (hasEgo(map) ? null : `Dữ liệu CARLA của ${map} không có xe ego ${egoLabel}`)}
        onToggle={toggleMap}
      />

      <div className={`chat-composer ${busy ? "busy" : ""}`}>
        <textarea
          ref={textRef}
          rows={2}
          autoFocus
          maxLength={3800}
          disabled={busy}
          value={description}
          onChange={(event) => changeDescription(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Mô tả tình huống bạn muốn test…"
          aria-label="Mô tả tình huống"
        />
        <div className="chat-composer-bar">
          <button type="button" className={`chat-options-toggle ${panel === "options" ? "active" : ""}`} aria-expanded={panel === "options"} aria-controls="chat-options" onClick={() => togglePanel("options")}>
            <Icon name="sliders" size={14} />Tùy chọn
            <Icon name="chevronDown" size={12} className="chat-options-caret" />
          </button>
          <button type="button" className={`chat-options-toggle ${panel === "maps" ? "active" : ""}`} aria-expanded={panel === "maps"} aria-controls="chat-maps-options" onClick={() => togglePanel("maps")}>
            <Icon name="layers" size={14} />Bản đồ
            {maps.length > 0 && <span className="chat-options-count">{maps.length}</span>}
            <Icon name="chevronDown" size={12} className="chat-options-caret" />
          </button>
          <span className="chat-composer-meta">{!ego ? "Chưa chọn xe ego" : !maps.length ? "Chưa chọn bản đồ" : `${maps.length} bản đồ · ego ${egoLabel}`}</span>
          <button type="submit" className="chat-send" disabled={!canSend} title={!ego ? "Chọn một xe ego" : !maps.length ? "Chọn ít nhất một bản đồ để tạo test case" : "Tạo test case (Enter)"} aria-label="Tạo test case">
            {busy ? <span className="spinner" /> : <Icon name="arrowUp" size={16} />}
          </button>
        </div>
      </div>
      {error && <div className="notice notice-error chat-error">{error}</div>}

      <div id="chat-options" className="chat-options panel" hidden={panel !== "options"}>
        <div className="options-section">
          <div className="options-section-title">Tùy chọn</div>
          <div className="form form-columns">
            <label className="field field-full">
              Tiêu đề
              <input maxLength={290} disabled={busy} value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Để trống: lấy dòng đầu của mô tả" />
              <span className="field-help">Test case được đánh số #1, #2… và ghi kèm tên bản đồ.</span>
            </label>
            <label className="field field-full">
              Thẻ (cách nhau bởi dấu phẩy)
              <input disabled={busy} value={tagsText} onChange={(event) => setTagsText(event.target.value)} placeholder="pedestrian, crossing, rain" />
            </label>
          </div>
        </div>
      </div>

      <div id="chat-maps-options" className="chat-options panel" hidden={panel !== "maps"}>
        <div className="options-section">
          <div className="options-section-title">Bản đồ</div>
          <MapOptionTabs
            maps={maps}
            active={current}
            options={mapOptions}
            choices={choices}
            busy={busy}
            onActive={setActiveMap}
            onChange={changeMapOptions}
            onRemove={removeMap}
            onApplyAll={applyToAll}
          />
        </div>
      </div>

      {!panel && (
        <div className="chat-suggestions">
          {EXAMPLE_PROMPTS.map((example) => (
            <button type="button" key={example.label} className="chat-suggestion" disabled={busy} onClick={() => { changeDescription(example.text); textRef.current?.focus(); }}>
              {example.label}
            </button>
          ))}
        </div>
      )}
      <p className="chat-footnote">Enter để gửi · Shift + Enter để xuống dòng</p>
    </form>
  );
}

const AUTO = "Agent tự chọn";

function ReadOnlyField({ label, value, full = false, multiline = false }: { label: string; value: string; full?: boolean; multiline?: boolean }) {
  return (
    <label className={`field ${full ? "field-full" : ""}`}>
      {label}
      {multiline ? <textarea disabled value={value} /> : <input disabled value={value} />}
    </label>
  );
}

/** The same inputs as the create form, read-only: what this session sent to the Agent. */
export function SessionInputView({ input }: { input: BuilderSession }) {
  return (
    <section className="panel form-panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Thông tin Test Case</div>
          <div className="panel-subtitle">Đầu vào đã gửi cho Agent khi tạo phiên (chỉ xem).</div>
        </div>
      </div>
      <div className="form form-columns">
        <ReadOnlyField full label="Tiêu đề" value={input.title} />
        <ReadOnlyField full multiline label="Mô tả tình huống" value={input.prompt} />
      </div>
      <div className="form form-columns metadata-form">
        <div className="section-label field-full">Bản đồ và metadata</div>
        <div className="field field-full">
          Theo từng bản đồ
          <div className="table-wrap">
            <table className="table map-options-table">
              <thead><tr><th>Bản đồ</th><th>Xe ego</th><th>Tác nhân</th><th>Môi trường</th><th>Nguy hiểm</th></tr></thead>
              <tbody>
                {input.maps.map((options) => {
                  const list = (values: string[]) => values.join(", ") || AUTO;
                  return (
                    <tr key={options.map_code}>
                      <td>{options.map_code}</td>
                      <td>{list(options.ego_vehicle_codes)}</td>
                      <td>{list(options.adversary_types)}</td>
                      <td>{list(options.environment_codes)}</td>
                      <td>{options.danger_levels.map((level) => labels.danger[level]).join(", ") || AUTO}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        <ReadOnlyField label="Thẻ (cách nhau bởi dấu phẩy)" value={input.tag_names.join(", ") || "Agent gợi ý"} />
      </div>
    </section>
  );
}
