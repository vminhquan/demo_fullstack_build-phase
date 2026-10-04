# 19 — Agent sinh kịch bản dựa trên dữ liệu CARLA

> **Trạng thái:** đã triển khai luồng dữ liệu mặc định và luồng Admin import (thay tạm cho worker). Đồng bộ dữ liệu tự động qua worker mới chỉ có điểm nối, chờ worker theo [18-worker-carla-vps.md](18-worker-carla-vps.md). Những chỗ ghi **[Chưa kiểm chứng]** chưa được chạy với ScenarioRunner/CARLA thật.

Agent nhận mô tả bằng ngôn ngữ tự nhiên rồi sinh tệp OpenSCENARIO 1.0 (`.xosc`). Mọi vị trí, làn đường và blueprint trong tệp đều lấy từ **một bản dữ liệu CARLA cụ thể** (catalog). Agent không tự bịa map hay tọa độ.

---

## 1. Kiến trúc

```text
Trình duyệt ──JWT──▶ Backend (FastAPI)                                      Agent (agent/, FastAPI)
                     ├─ module catalog:    carla_catalog_snapshots ──┐      ├─ LangGraph 6 bước + RAG Qdrant (RAM)
                     │   DEFAULT (seed) · IMPORT (Admin) · WORKER    │      ├─ guardrail SOTIF + sửa bằng Z3
                     ├─ module generation: scenario_generations ─────┼─────▶├─ grounding: đặt actor lên làn thật của catalog
                     │                     POST /scenario-generations │ X-API-Key └─ xuất .xosc + kiểm tra XSD
                     └─ lưu DRAFT: test_case_version + artifact XOSC ◀┘
```

- **Backend là nơi giữ catalog** (nguồn sự thật). Agent không có database và không lưu trạng thái. Mỗi request mang theo cả catalog.
- Mỗi version sinh từ Agent ghim `catalog_snapshot_id`. Trong `scenario_input` lưu prompt, IR, kết quả grounding, validation, threat score và `content_hash` của catalog, để biết kịch bản được sinh trên dữ liệu CARLA nào.
- Frontend chỉ gọi Backend. Agent không mở cổng ra ngoài: trong compose dùng `expose`.

## 2. Hai luồng dữ liệu

| Luồng | Nguồn | Ai đưa dữ liệu vào | Phạm vi |
|---|---|---|---|
| **1. Mặc định** | `backend/app/modules/catalog/default_data/*.json` | `python -m app.seed_catalog`. Compose tự chạy sau migrate; Render chạy khi `RUN_MIGRATIONS=1` | Dùng chung cho mọi Project (`project_id` NULL) |
| **2. CARLA của người dùng** | CARLA trên máy khách (`:2000`) | **Hiện tại:** người tạo kịch bản hoặc Admin import ngay trên màn sinh kịch bản / Cài đặt, bằng `.zip` thư mục export CARLA hoặc `.json` catalog (`source=IMPORT`). **Sau này:** worker gửi `catalog.sync` (`source=WORKER`) | Chỉ Project đó |

Dữ liệu mặc định ban đầu: **Town10HD_Opt, CARLA 0.9.16**. Dữ liệu được chuyển từ bản export `P-065/connect_carla/carla_export_output/20260924T122139Z`, gồm 155 spawn point, 3066 waypoint, 41 vehicle và 52 walker. Muốn thêm map, chép tệp catalog.v1 vào `default_data/` rồi chạy lại seed. Seed bỏ qua tệp có nội dung đã cài.

Chuyển một bản export CARLA (định dạng `connect_carla/carla_export.py`) sang catalog.v1:

```bash
cd backend
python -m app.modules.catalog.importers <thư_mục_export> app/modules/catalog/default_data/Town05.json
```

`importers.from_worker_context()` cũng chuyển được payload `context` của `P-065/src/services/carla_worker.py`. Worker có thể tái dùng hàm này.

## 3. Contract `scenario-forge.catalog.v1`

Nguồn chuẩn: `agent/app/contracts/catalog_v1.py`. Backend có một bản sao ở `backend/app/modules/catalog/contract.py`; khi sửa phải sửa cả hai.

```json
{
  "format": "scenario-forge.catalog.v1",
  "carla_version": "0.9.16",
  "map_name": "Town10HD_Opt",
  "available_maps": ["Town01", "Town10HD_Opt"],
  "opendrive_hash": "sha256 của .xodr",
  "vehicles": [{"id": "vehicle.yamaha.yzf", "base_type": "motorcycle", "number_of_wheels": 2}],
  "walkers": [{"id": "walker.pedestrian.0001"}],
  "spawn_points": [{"x": -64.64, "y": 24.47, "z": 0.6, "yaw": 0.16}],
  "waypoints": [{"x": -27.02, "y": 66.21, "z": 0.0, "yaw": -179.93, "road_id": 939, "section_id": 0,
                 "lane_id": -1, "s": 0.0, "lane_width": 3.5, "is_junction": true, "lane_type": "Driving"}],
  "weather_presets": ["ClearNoon"],
  "content_hash": "Backend tự tính, bỏ qua giá trị người gửi điền"
}
```

- Tọa độ theo **hệ CARLA**: đơn vị mét, hệ tay trái (x phía trước, y bên phải), yaw tính bằng độ.
- `map_name` là tên ngắn như CARLA dùng khi load map (`Town10HD_Opt`), không phải `Carla/Maps/...`.
- Waypoint lấy mẫu khoảng 2 m (`map.generate_waypoints(2.0)`), chỉ cần làn `Driving`.
- `content_hash` là sha256 của JSON đã chuẩn hóa. Gửi lại cùng nội dung thì không tạo bản mới (idempotent).

## 4. Agent đặt kịch bản lên map như thế nào (grounding)

`agent/app/scenario/grounding.py`:

1. **Hồ sơ map:** đi theo làn từ mỗi spawn point để biết map có những loại đường nào: ngã tư, đường thẳng, đường cong, đường nhiều làn cùng chiều, làn ngược chiều. Hồ sơ này được gửi kèm prompt của **mọi bước LLM**. Nếu LLM chọn loại đường mà map không có, graph đổi sang loại gần nhất và ghi cảnh báo.
2. **Chọn vị trí ego:** chấm điểm từng spawn point theo `road_type` (ngã tư ở đúng khoảng cách tác nhân chính, đường thẳng ít đổi hướng, đường cong) và theo các làn mà tác nhân cần (làn trái/phải cùng chiều, làn ngược chiều, làn cắt ngang đúng phía). Kết quả là duy nhất với cùng đầu vào; nút "Sinh lại" đổi `seed` để luân phiên giữa các vị trí tốt ngang nhau.
3. **Đặt tác nhân:** đi đúng quãng `initial_distance_m` dọc làn của ego, rồi lấy làn bên cạnh, làn ngược chiều hoặc làn cắt ngang thật. Người đi bộ và xe đạp băng qua đường được đặt ở mép đường ngoài cùng. Nếu không có làn phù hợp, tác nhân được đặt theo hình học (`placement=geometric`) **và có cảnh báo**.
4. **Blueprint:** chỉ chọn blueprint có trong catalog, theo `base_type`. Nếu catalog thiếu loại cần dùng, Agent trả `422 CATALOG_MISSING_BLUEPRINT`.

Tệp `.xosc` (`agent/app/scenario/xosc.py`):

- `LogicFile filepath` là tên map.
- Ego tên `hero`, có `AssignRouteAction` theo làn đã chọn.
- Thời tiết giới hạn `precipitation ≤ 30` (an toàn shader DX11).
- Mỗi tệp được kiểm tra với XSD OpenSCENARIO 1.0 lấy từ ScenarioRunner 0.9.15.

**[Chưa kiểm chứng]** ScenarioRunner đổi dấu `y` và hướng (heading) của `WorldPosition` khi chuyển sang CARLA. Vì vậy Agent ghi `y` và `h` đảo dấu (`XOSC_FLIP_Y=true`, đặt mặc định). Cần chạy thử một tệp trên CARLA thật để xác nhận. Nếu xe bị lật đối xứng qua trục x, đặt `XOSC_FLIP_Y=false`.

## 5. API

### Backend (`/api/v1`)

| Method | Path | Quyền | Mô tả |
|---|---|---|---|
| GET | `/carla-catalog/snapshots?source=DEFAULT\|PROJECT` | `testcase:read` | Danh sách bản dữ liệu (chỉ thông tin tóm tắt, không kèm waypoint) |
| GET | `/carla-catalog/snapshots/{id}` | `testcase:read` | Một bản dữ liệu |
| GET | `/carla-catalog/metadata-options` | `testcase:read` | Danh mục chọn cho 5 metadata, tính động từ mọi catalog Project thấy được: `maps` (có/không có dữ liệu làn), `ego_vehicles`, `adversary_types`, `environments` (điều kiện chuẩn + preset CARLA); mỗi mục có `snapshot_ids` để lọc theo một bản dữ liệu |
| POST | `/scenario-generations/refine` | `testcase:create` | Form có cấu trúc → Agent `POST /v1/prompts/refine`: chuẩn hoá form theo map + guardrail (`adjustments`), LLM chọn giá trị cụ thể trong dải và viết mô tả (`refined_prompt`, `values`), trả `constraints` để gửi kèm `POST /scenario-generations`. Không lưu gì |
| POST | `/carla-catalog/snapshots/import` | `catalog:import` | `{catalog, label?}`. Lưu catalog.v1 với `source=IMPORT`, trả `{snapshot, created}` |
| POST | `/carla-catalog/snapshots/upload` | `catalog:import` | multipart `file` (+ `label`): `.zip` thư mục export `connect_carla/carla_export.py` (chỉ đọc `02_server_actor_state/`), `.json` catalog.v1 hoặc `.json` context của worker; tối đa 50 MB |
| POST | `/scenario-generations` | `testcase:create` | `{prompt, catalog_source: DEFAULT\|PROJECT, catalog_snapshot_id?, map_name?, auto_repair?, seed?}` |
| GET | `/scenario-generations/{id}` | `testcase:read` | Kết quả gồm IR, grounding, validation, XOSC và `suggested_version` |
| POST | `/scenario-generations/{id}/accept` | `testcase:create`, đúng người đã sinh | Tạo Test Case (hoặc version kế tiếp nếu gửi `test_case_id`) ở trạng thái DRAFT, kèm XOSC artifact |

Mã lỗi:

| Mã | HTTP | Khi nào |
|---|---|---|
| `CATALOG_NOT_SYNCED` | 409 | Chọn nguồn PROJECT khi Project chưa có dữ liệu |
| `CATALOG_NOT_INSTALLED` | 409 | Chưa seed dữ liệu mặc định |
| `NOT_A_SCENARIO_REQUEST` | 422 | Prompt là câu hỏi về quy định; `details.regulation_answer` có câu trả lời |
| `GUARDRAIL_FAILED` | 422 | Kịch bản vi phạm SOTIF và sửa bằng Z3 không cứu được |
| `CATALOG_MISSING_BLUEPRINT` | 422 | Catalog thiếu blueprint cho loại tác nhân cần dùng |
| `AGENT_UNAVAILABLE` | 503 | Agent tắt, quá hạn, hoặc lỗi LLM |
| `CONFLICT` | 409 | Bản sinh này đã được lưu thành version |

### Agent (`agent/`, chỉ Backend gọi)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/health` | `{status, rag_ready, llm_enabled, model}` |
| POST | `/v1/scenarios/generate` | Header `X-API-Key`. Body `{prompt, catalog: catalog.v1, auto_repair, offline_mode, seed}`. Trả `scenario_ir, interpretation, validation, threat_score, grounding, xosc, xosc_sha256, xosc_validation, retrieved_regulations, warnings, generation_mode, model` |

## 6. Gợi ý 5 metadata

Phần map ra 5 trường nằm ở `backend/app/modules/generation/mapping.py`. Đây chỉ là gợi ý; người tạo sửa được trước khi lưu.

| Trường | Lấy từ |
|---|---|
| `map_code` | Tên map của catalog |
| `ego_vehicle_code` | Blueprint của ego đã chọn trong catalog |
| `adversary_type` | Tác nhân có trigger đầu tiên: `bicycle` → `cyclist`, các loại khác giữ nguyên |
| `environment_code` | `weather`; trời quang mà giờ ≥ 19 hoặc < 6 thì thành `night` |
| `danger_level` | `weighted_threat`: < 0.30 LOW, < 0.45 MEDIUM, < 0.60 HIGH, còn lại CRITICAL. Nếu kỳ vọng `COLLISION_EXPECTED` thì ít nhất là HIGH. Ngưỡng là điểm khởi đầu, chưa hiệu chỉnh trên dữ liệu chạy thật |

## 7. Điểm nối với Worker (làm khi worker xong)

Đã có sẵn:

- `catalog.service.ingest_snapshot(session, catalog, source=WORKER, project_id, worker_installation_id)`: một đầu vào chung cho mọi nguồn, kiểm tra contract, chống trùng bằng hash và ghi Nhật ký hoạt động.
- Cột `carla_catalog_snapshots.worker_installation_id` (BIGINT, chưa có FK vì bảng `worker_installations` chưa tồn tại).
- WebSocket `/integration/worker/ws` đã nhận `catalog.sync` và hiện trả `error INSTALLATION_REQUIRED`. Socket dùng token chung nên chưa biết worker thuộc Project nào.
- `importers.from_worker_context()` chuyển payload `carla_worker.context()` sang catalog.v1.

Đề xuất bổ sung cho doc 18 (cần nhóm worker đồng ý):

1. `catalog.sync` trên WebSocket **chỉ gửi bản tóm tắt**: `carla_version, current_map, maps, content_hash`, số lượng spawn point, waypoint và blueprint. Lý do: waypoint của một map khoảng 2 MB, vượt `max_size=1 MiB` của WebSocket.
2. Backend trả `catalog.ack {content_hash, known: true|false}`.
3. Nếu `known=false`, worker gửi catalog.v1 đầy đủ qua `POST /api/v1/worker/catalog-snapshots` (HTTPS, access token của worker). Backend gọi `ingest_snapshot(source=WORKER, project_id=project của installation)`.
4. Khi người dùng đổi map trên CARLA, worker gửi `catalog.sync` mới. Backend chỉ lưu thêm khi hash khác.

## 8. Cấu hình

| Biến | Dịch vụ | Mặc định | Ghi chú |
|---|---|---|---|
| `AGENT_SERVICE_URL` | backend | `http://localhost:8100` (compose: `http://agent:8100`) | Để trống thì các API sinh kịch bản trả 503 |
| `AGENT_API_KEY` | backend + agent | trống | Đặt **cùng một giá trị** ở cả hai bên. Bắt buộc khi `APP_ENV` khác `development`/`test` |
| `AGENT_TIMEOUT_SECONDS` | backend | 180 | Thời gian chờ Agent trả kết quả |
| `OPENAI_API_KEY` | agent | trống | Trống thì sinh theo quy tắc offline (`generation_mode=deterministic`) |
| `MODEL_NAME` | agent | `gpt-4o-mini` | |
| `OPENAI_BASE_URL` | agent | trống | Chỉ đặt khi dùng gateway tương thích OpenAI |
| `XOSC_FLIP_Y` | agent | `true` | Xem mục 4 |
| `RAG_ENABLED` | agent | `true` | Qdrant in-memory, nạp sẵn từ `agent/knowledge/` (8 quy định, 20 kịch bản mẫu, 3 tài liệu RAV03) |

Agent chạy **một tiến trình** uvicorn, vì RAG và cache chỉ mục làn nằm trong bộ nhớ.

## 9. Chạy và kiểm thử

```bash
docker compose up --build            # thêm service agent; migrate tự chạy seed_catalog

# Agent riêng
cd agent && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest           # 11 test offline: grounding trên Town10HD_Opt, XSD, HTTP contract
.venv/bin/uvicorn app.main:app --port 8100

# Backend
cd backend && .venv/bin/python -m pytest   # thêm test cho catalog/hash/importer/mapping
```

Đã chạy end-to-end (2026-10-03) trên database tạm, với Backend local và Agent dùng OpenAI `gpt-4o-mini`:

- Sinh kịch bản từ dữ liệu mặc định mất khoảng 11 giây; XOSC hợp lệ theo XSD.
- Lưu thành DRAFT; tải XOSC về có sha256 khớp; gửi review thành công.
- Import catalog: lần đầu tạo bản mới, gửi lại cùng nội dung không tạo bản trùng, tài liệu sai định dạng bị từ chối với 422.
- Sinh kịch bản với nguồn PROJECT dùng đúng bản Admin import; trước khi import trả `409 CATALOG_NOT_SYNCED`.
- Prompt hỏi về quy định trả `422 NOT_A_SCENARIO_REQUEST`.
- Giao diện `/test-cases/generate` và `/settings` hoạt động trên trình duyệt.

**Chưa kiểm chứng:** chạy tệp `.xosc` sinh ra bằng ScenarioRunner trên CARLA 0.9.16 (đang chờ worker).

Benchmark chất lượng (22 ca có kỳ vọng, chấm kỹ thuật + vị trí + bám prompt + độ trễ): `cd agent && .venv/bin/python -m benchmark.run`, xem [agent/benchmark/README.md](../agent/benchmark/README.md).

## 10. Nguồn gốc mã

Mã Agent được port từ `P-065/src` (ScenarioForge): `agents/graph.py`, `agents/nodes/generator.py`, `services/{rag,suite_validator,smt_synthesizer,curve_dynamics,threat_scoring,xosc_adapter}.py` và `models/scenario_schemas.py`.

Những thay đổi so với P-065:

- Bỏ chat stub, vòng HITL trước khi sinh, phần sinh script Python CARLA và các luồng `/workflows`, `/local/*`, campaign.
- Thêm `grounding.py`, đưa ngữ cảnh map vào prompt LLM và kiểm tra XSD.
- Đọc key và model từ settings thay vì `os.environ`.
- Dùng `with_structured_output(method="function_calling")`, vì chế độ strict của OpenAI từ chối trường tuple `expected_min_ttc_range`.
- Sửa lỗi `index_for_rag` bị LangGraph bỏ qua: không còn ghi ngược kết quả vào RAG.

## 11. Giao diện chọn dữ liệu (màn `/test-cases/generate`)

1. **Chọn dữ liệu CARLA:** khi chưa có dữ liệu đồng bộ qua worker, màn hình báo "Chưa kết nối CARLA của bạn qua worker" và đưa ra 2 lựa chọn:
   - **Dùng CARLA mặc định của hệ thống:** được chọn sẵn, dùng ngay.
   - **Dùng dữ liệu CARLA của tôi:** chọn một bản đã import, hoặc bấm **Import dữ liệu CARLA** để mở hộp thoại kéo-thả `.zip`/`.json`. Import xong thì bản đó được chọn luôn.
2. **Mô tả tình huống.**
3. **Kết quả.**
4. **Lưu thành bản nháp.**

Quyền `catalog:import` thuộc về Admin và người có nhiệm vụ Tạo kịch bản (`TESTCASE_CREATE`). Dữ liệu import chỉ thuộc Project hiện tại.

## 12. Chọn 5 metadata từ dữ liệu CARLA (mọi form version)

Form tạo test case (`/test-cases/create`), sửa version và lưu kết quả AI đều dùng `features/console/metadata-fields.tsx`. Các ô metadata là **dropdown lấy động** từ `GET /carla-catalog/metadata-options`:

| Ô | Nguồn |
|---|---|
| Lấy danh mục từ | "Tất cả dữ liệu CARLA khả dụng", hoặc một bản cụ thể (mặc định / import / worker). Form lưu kết quả AI tự chọn đúng bản đã dùng để sinh |
| Bản đồ | Nhóm "Có dữ liệu làn đường (Agent sinh được)" = `map_name` của các catalog. Nhóm "Có trên máy CARLA, chưa có dữ liệu làn" = `available_maps` |
| Xe ego | Blueprint `vehicle.*` có `base_type` là car/van/truck/bus, nhóm theo loại |
| Loại tác nhân | Suy ra từ `base_type` của xe (bicycle → `cyclist`) và có `pedestrian` nếu catalog có walker |
| Môi trường | 6 điều kiện chuẩn Agent dùng + preset thời tiết trong catalog (`weather_presets`, bỏ `Default`) |

Import hoặc đồng bộ thêm dữ liệu từ máy CARLA khác thì danh sách tự mở rộng, không cần sửa code. Giá trị cũ không có trong dữ liệu vẫn hiển thị kèm ghi chú "giá trị cũ". Khi Project chưa có catalog nào, form chuyển về ô nhập tay và kèm link import.

## 13. Form có cấu trúc → LLM tinh chỉnh → sinh test case

Bước 2 của `/test-cases/generate` có tab **Form có cấu trúc** (mặc định) và **Tự viết mô tả**. Form theo mẫu campaign của P-065: mục tiêu kiểm thử + mục tiêu tìm kiếm (nguy hiểm / ranh giới PASS–FAIL / thông thường), ODD (loại đoạn đường, thời tiết chọn nhiều, ánh sáng, tác nhân lấy từ catalog, loại scenario, hướng), 4 dải tham số (tốc độ ego, tốc độ tác nhân, khoảng cách ban đầu, khoảng cách kích hoạt) và xe ego (từ catalog).

Luồng:
1. **Tinh chỉnh bằng AI** → `POST /scenario-generations/refine` → Agent `normalize_spec` (`agent/app/scenario/spec.py`): đảo dải ngược, kẹp theo giới hạn IR/guardrail (ego 10–130 km/h, người đi bộ ≤ 15 km/h, kích hoạt ≤ khoảng cách ban đầu), đổi loại đường map không có, bỏ xe ego không có trong catalog; mỗi thay đổi là một `adjustment` hiển thị cho người dùng. Tác nhân không có trong catalog → 422 `CATALOG_MISSING_BLUEPRINT`.
2. LLM (`refiner.py`) chọn giá trị cụ thể trong dải theo mục tiêu tìm kiếm và viết mô tả tiếng Việt. Số ngoài dải hoặc TTC < 0,8 s bị chỉnh lại (`fit_values`) và mô tả được dựng lại. Không có OpenAI key / LLM lỗi → mô tả dựng sẵn từ form.
3. Người dùng sửa được mô tả rồi bấm **Sinh kịch bản**: gửi `prompt` + `spec` + `constraints`. Trong graph, `apply_constraints` chạy sau `assemble_ir` và sau mỗi `smt_repair`: kẹp tốc độ/khoảng cách, ép loại tác nhân, vị trí, hành vi, thời tiết, giờ, loại đường và chỉ giữ 1 tác nhân; mỗi thay đổi thành cảnh báo `Form: …`. Xe ego chọn trong form được dùng làm blueprint của `hero`.
4. Chọn nhiều thời tiết: mỗi lần **Sinh lại** tinh chỉnh lại với seed kế tiếp, thời tiết luân phiên. XOSC giờ đặt độ cao mặt trời theo giờ (mưa + ban đêm vẫn tối).
5. Khi lưu, `scenario_input.form = {spec, constraints}` đi cùng version.

Chưa làm (cần Worker/CARLA chạy thật): ngân sách run, số case nguy hiểm mục tiêu, chiến lược LLM tổng hợp kết quả run trước như campaign P-065.


## 14. `/test-cases/create` gọi Agent, sinh nhiều test case

Upload tệp .xosc thủ công **không thuộc scope** sản phẩm: mọi tệp .xosc đều do Agent sinh.

- **Tạo mới** (`/test-cases/create`; tạo xong chuyển sang chi tiết phiên `/test-cases/[id]`, kết quả và duyệt ở cột phải): Tiêu đề, Mô tả tình huống, **Số lượng test case cần sinh** (1–10) và 5 metadata. Mỗi test case: `POST /scenario-generations` (`prompt` = mô tả + gợi ý "biến thể i/N", `seed` = i, `metadata`) rồi `POST /scenario-generations/{id}/accept` → Test Case mới, tiêu đề đánh số `(#i)`. Frontend chạy tối đa 3 lượt song song và hiện tiến độ từng test case; lượt lỗi không chặn lượt khác.
- **Version cũ chưa có .xosc / muốn sinh lại** (`/scenarios/{id}/versions/{vid}`): nút "Sinh .xosc bằng Agent" dùng mô tả test case + 5 metadata đang chọn, accept với `version_id` → cập nhật đúng Draft đó (kiểm tra DRAFT + chủ sở hữu), artifact .xosc mới, không tạo version mới.
- Backend với `metadata`: chọn dữ liệu CARLA có làn của `map_code` (Project trước, rồi mặc định; map không có làn → 409 `CATALOG_NOT_SYNCED`); kiểm tra `ego_vehicle_code` có trong catalog (422); đổi metadata thành `constraints` (`generation/mapping.py` `metadata_constraints`): xe ego → blueprint `hero`, tác nhân → `actor_type` (cyclist→bicycle, van→car, bus→truck), môi trường → thời tiết + giờ (`HardRainNight` → `heavy_rain`, 22h; `DustStorm` → `fog` là xấp xỉ).
- Số trong mô tả (ví dụ "40 km/h, cách 15 m") được LLM giữ nguyên, nên các biến thể khi đó chủ yếu khác vị trí xuất phát trên map. Muốn biến thể khác cả tốc độ/khoảng cách, bỏ số khỏi mô tả hoặc dùng form dải tham số (§13).

## 15. Xem chi tiết và preview .xosc

- Danh mục kịch bản: bấm vào bất kỳ đâu trên một dòng để mở chi tiết test case.
- Chi tiết test case: panel **Preview kịch bản** của version mới nhất; trang version có panel **Preview tệp .xosc**.
- `features/console/xosc-preview.tsx` đọc trực tiếp tệp đã lưu (`GET /test-case-versions/{id}/xosc`): bản đồ (`LogicFile`), môi trường (mây, mưa, sương, giờ), thời gian dừng, bảng thực thể (loại, mẫu xe, vị trí xuất phát, tốc độ đầu), bảng sự kiện storyboard (hành động, điều kiện kích hoạt), XML gốc và nút tải. Sơ đồ dùng `scenario_input.grounding` (làn đường thật) nếu có, không thì vẽ từ `WorldPosition` trong tệp.

## 16. Sau khi phê duyệt: đưa vào bộ kiểm thử để chạy CARLA

Luồng chạy CARLA đi qua **Bộ kiểm thử** (docs/07): bộ ghim `test_case_version_id` đã APPROVED; "Chạy batch" tạo `suite_run` + `run_jobs` ở trạng thái QUEUED; worker (docs/18) nhận job, chạy .xosc trên ScenarioRunner và gửi kết quả.

- Hàng đợi duyệt: sau khi Phê duyệt hiện thông báo kèm link "Đưa vào bộ kiểm thử →" tới trang version.
- Trang version APPROVED: panel "Đưa vào bộ kiểm thử để chạy CARLA" (`features/console/add-to-suite.tsx`) — 3 bước, các bộ đang chứa version, chọn bộ có sẵn hoặc tạo bộ mới rồi thêm (`suite:manage`), link mở bộ để Chạy.
- Chưa có worker: run_jobs nằm ở QUEUED cho tới khi worker nhận.
- Sửa lỗi có sẵn: `decideReview` gọi `/reviews/{id}/approved|rejected` (404); nay ánh xạ đúng `approve | request-edit | reject`.

## 17. Khai báo ODD và form sinh phủ ODD

**Khai báo ODD theo Project** (Cài đặt → "Khai báo ODD của Project", quyền `odd:manage` = Admin + Người tạo): dữ liệu CARLA/map dùng để kiểm thử, loại đoạn đường (giao lộ / đường thẳng / đường cong / cao tốc), thời tiết, ánh sáng, loại tác nhân, xe ego, dải tốc độ ego và tác nhân, ghi chú. Lưu ở bảng `odd_profiles` (migration `0003_odd_profile`).

- Khả năng của từng map (`MapCapability`): Agent `POST /v1/catalog/profile` phân tích dữ liệu làn một lần, kết quả lưu vào `carla_catalog_snapshots.map_profile`; loại tác nhân và xe ego lấy từ blueprint.
- **Lỗ hổng** = ODD yêu cầu nhưng map đã chọn không dựng được (loại đường, tác nhân, xe ego). Kiểm tra trực tiếp khi sửa (`POST /odd-profile/check`) và trả về khi lưu.

API: `GET /odd-profile` (khai báo + khả năng + lỗ hổng), `PUT /odd-profile`, `POST /odd-profile/check`, `POST /odd-profile/plan`.

**Form sinh** (`/test-cases/generate`, tab "Form theo ODD"):
1. Chọn nhiều giá trị trong ODD cho loại đường, thời tiết, ánh sáng, tác nhân; loại scenario, hướng, mục tiêu tìm kiếm; 4 dải tham số; xe ego; **số lượng test case** (1–20). Chưa khai báo ODD thì form cho chọn mọi giá trị dữ liệu CARLA dựng được và nhắc khai báo.
2. **Lập kế hoạch phủ ODD** (`odd/planner.py`): chọn tổ hợp theo kiểu phủ cặp (mọi cặp giá trị khả thi như mưa × giao lộ đều có case trước khi lặp), chia mỗi dải thành N khoảng theo kiểu Latin hypercube (khoảng cách và kích hoạt đi cùng nhau), chọn map dựng được tổ hợp và ít dùng nhất. Hiển thị bảng kế hoạch, số cặp đã phủ, cặp còn thiếu, giá trị ngoài ODD (bị bỏ hoặc kẹp) và tổ hợp không dựng được.
3. N = 1: tinh chỉnh bằng AI → sửa mô tả → sinh → xem trước → lưu (như trước). N > 1: mỗi case tinh chỉnh → sinh → lưu nháp tự động (tối đa 3 song song), tiêu đề "tiêu đề chung #i — tác nhân · đường · thời tiết · ánh sáng · map", thẻ `odd-plan`.

Môi trường mưa sau khi tối được lưu bằng preset CARLA (`MidRainyNight`, `HardRainNight`, `MidRainSunset`, `HardRainSunset`) để giữ cả thời tiết lẫn ánh sáng.

Chưa làm: đo độ phủ trên **kết quả chạy CARLA** (cần worker); hiện độ phủ là của kế hoạch sinh.

## 18. Đối chiếu yêu cầu đề bài

| Yêu cầu | Trạng thái | Ở đâu |
|---|---|---|
| **Cơ bản** — nhập mô tả tự nhiên → file kịch bản CARLA | Có | `/test-cases/create`, `/test-cases/generate` → Agent → `.xosc` (OpenSCENARIO 1.0, kiểm tra XSD). Chạy thật trên ScenarioRunner: chờ worker |
| Preview kịch bản | Có | Sơ đồ làn thật + bảng thực thể/sự kiện + nội dung `.xosc` (§15) |
| Thư viện lưu trữ có tag | Có | Danh mục kịch bản, lọc theo tag/map/tác nhân/môi trường/mức nguy hiểm/trạng thái |
| ≥ 2 vai trò, HITL phê duyệt | Có | Người tạo / Người duyệt, hàng đợi duyệt, phê duyệt → bộ kiểm thử (§16) |
| Báo cáo tỷ lệ kịch bản hợp lệ | Có (mới) | `/reports` (§19) |
| **Nâng cao** — agent sinh bộ đa dạng phủ ODD | Có | Khai báo ODD + kế hoạch phủ cặp, chế độ tự động (§17, §19) |
| Tự chạy trong sim, thu coverage/nguy hiểm | Một phần | Độ phủ ODD + mức nguy hiểm **dự đoán** có trong báo cáo; chạy sim và chỉ số thật cần worker (doc 18) |
| Closed-loop với mô hình lái, tìm kịch bản làm mô hình thất bại | Chưa | Cần worker chạy CARLA + mô hình lái (SUT) và kết quả run để làm vòng tìm kiếm |
| Tối ưu chi phí sinh | Có | Đo token/thời gian mỗi lượt, cache yêu cầu trùng, kế hoạch tự tính số case tối thiểu, chế độ offline 0 token (§19) |

## 19. Báo cáo sinh kịch bản và tối ưu chi phí

- **Nhật ký lượt gọi Agent** (`agent_calls`, migration `0004_agent_calls`): mỗi lượt REFINE / GENERATE, kể cả bị từ chối (422: `GUARDRAIL_FAILED`, `NOT_A_SCENARIO_REQUEST`, thiếu blueprint…) và lỗi (`AGENT_UNAVAILABLE`), kèm token vào/ra và thời gian. Agent trả `usage` (đếm bằng `get_usage_metadata_callback` của LangChain).
- **Kịch bản hợp lệ** = đạt ràng buộc an toàn (guardrail) **và** đúng schema OpenSCENARIO 1.0. Tỷ lệ hợp lệ = hợp lệ / mọi lượt sinh.
- `GET /reports/generation?days=7|30|90` (`app/modules/report/`), trang `/reports`: tỷ lệ hợp lệ, tỷ lệ Agent sinh được, lý do không hợp lệ, theo ngày, kết quả duyệt (tỷ lệ duyệt = duyệt / (duyệt + từ chối)), mức nguy hiểm + kết quả dự kiến (từ điểm đe doạ của Agent, chưa phải kết quả chạy), độ phủ ODD của thư viện (giá trị, cặp, cặp chưa phủ, giá trị ngoài ODD), chi phí (token, USD theo `LLM_PRICE_INPUT_PER_MTOK` / `LLM_PRICE_OUTPUT_PER_MTOK`, USD / kịch bản hợp lệ, cache, p50/p95).
- **Cache**: `scenario_generations.request_hash` = sha256(mô tả, content hash dữ liệu CARLA, ràng buộc, seed, auto_repair). Yêu cầu trùng trong Project dùng lại kết quả, 0 token (`use_cache: false` để bỏ qua).
- **Kế hoạch tự động**: `POST /odd-profile/plan` với `count: null` → thêm case theo thuật toán tham lam tới khi phủ mọi cặp khả thi (tối đa 20). Gần tối thiểu, không bảo đảm tối thiểu tuyệt đối.
- **Sau sinh hàng loạt**: nút "Gửi duyệt tất cả" gửi các bản nháp vào hàng đợi duyệt.
- **Sửa trong lúc kiểm thử**: người đi bộ + loại scenario khác "băng ngang" → tự đổi sang băng ngang (báo là điều chỉnh); sau khi kẹp vào dải, nếu TTC < 0,8 s thì lùi điểm kích hoạt → nới khoảng cách → hạ tốc độ ego, đều trong dải (`spec._keep_fair`).
