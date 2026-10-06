# Scenario Forge Bridge

Phần mềm cài trên **máy có CARLA** (Ubuntu hoặc Windows) để nối máy đó với một hoặc nhiều Project trên Scenario Forge.

Bridge là ống thông giữa Scenario Forge (production) và CARLA trên máy người dùng. Hiện làm được: **ghép nối bằng OTP 6 số**, **giữ kết nối lâu dài qua WebSocket**, **kiểm tra cổng CARLA (2000)** **đồng bộ dữ liệu mọi map của CARLA** lên Project để Test Case Builder và Agent dùng, và **chạy test case (XOSC) bằng ScenarioRunner** khi Simulator Runner gửi xuống rồi trả kết quả về.

## Luồng ghép nối

```
Web (Start up)            Backend                               Bridge (máy có CARLA)
  "Thêm kết nối mới" ──▶ POST /projects/{id}/bridges/pair-codes
                       ◀── {code: "482913", expires_at}  (lưu HMAC của mã, sống 10 phút, dùng 1 lần)
  hiện 6 số, mở WS  ──▶ WS /projects/{id}/bridges/events
                                                           ◀── scenario-forge-bridge pair 482913
                                                               POST /bridge/pair {code, hostname, os, carla}
                         kiểm tra mã → tạo Bridge + device token
                         → tạo kết nối CONN-XXXXXXXXXX (Bridge ↔ Project)
  ◀── bridge.paired {connection_uid}                       ──▶ {bridge_uid, device_token, connection_uid}
  "Kết nối thành công · CONN-…"                                 "Kết nối thành công · CONN-…"
                                                           ◀── WS /bridge/ws (Authorization: Bearer <device token>)
  ◀── bridge.online                                             heartbeat 15 s + trạng thái cổng CARLA
  ◀── bridge.status (CARLA bật/tắt), bridge.offline
```

Web và Bridge hiển thị **cùng một mã kết nối** (`CONN-…`). Một máy có thể ghép nhiều Project: lần ghép sau dùng lại device token đã lưu, chỉ thêm kết nối mới.

## Cài đặt

### Cách 1: lệnh cài trên trang Start up (người dùng cuối)

```bash
curl -LsSf https://<web>/bridge/install.sh | sh          # Ubuntu
```
```powershell
irm https://<web>/bridge/install.ps1 | iex               # Windows PowerShell
```

Script (`frontend/public/bridge/install.{sh,ps1}`) cài `uv`, cài CLI bằng `uv tool install` (Python ≥ 3.10 bất kỳ), rồi chạy
`setup-runner`, màn hình này hỏi phiên bản CARLA server:

```
Scenario Forge Bridge · Chọn phiên bản CARLA server trên máy này
(↑/↓ để chọn, Enter để xác nhận)

 ❯ CARLA 0.9.16
   CARLA 0.9.15
   Tự động phát hiện  (CARLA phải đang chạy)
```

Không có màn hình (SSH, cài tự động): `SF_CARLA=0.9.16` (hoặc `auto`), thêm `SF_CARLA_ROOT=<thư mục CARLA>` nếu CARLA không nằm ở
chỗ quen thuộc. Chạy lại lệnh cài để cập nhật.

### Môi trường chạy test (runner) Bridge tự quản lý

Bridge **không import `carla`**: mọi lệnh tới CARLA (ScenarioRunner, camera, đồng bộ catalog) chạy trong một Python riêng do
`setup-runner` tạo, nên CLI chạy trên Python nào cũng được và không đụng Python / CARLA của máy.

```
<thư mục cấu hình>/runtime/<phiên bản CARLA>/
  venv/             Python theo bảng RUNNERS (uv tự tải)
  scenario_runner/  ScenarioRunner đúng tag, đã vá
```

| CARLA | ScenarioRunner | Python runner | Bản vá |
|---|---|---|---|
| 0.9.16 | v0.9.16 | 3.10 | `times_none`, `self_reset` |
| 0.9.15 | v0.9.15 | 3.8 (thư viện của tag này chỉ có tới cp38) | `self_reset` |

Thư viện client `carla` lấy từ chính thư mục CARLA của máy (`PythonAPI/carla/dist/carla-<ver>-cp<py>-…whl`, luôn khớp server),
không có thì từ PyPI. Thêm phiên bản: một dòng trong `RUNNERS` (`runtime.py`) sau khi đã chạy thử trên máy thật.
Lúc nhận phiên chạy, Bridge hỏi phiên bản server: khác bản đã chọn → `run.rejected CARLA_VERSION_MISMATCH`, chạy lại `setup-runner`.

### Cách 2: file chạy đóng gói sẵn (PyInstaller)

Build trên đúng hệ điều hành đích, vì PyInstaller không build chéo: `./scripts/build.sh` (Ubuntu) hoặc `.\scripts\build.ps1` (Windows),
rồi `scenario-forge-bridge setup-runner`. Cần `uv` trên máy để tạo runner.

### Cách 3: từ mã nguồn

```bash
uv tool install ./bridge           # hoặc: pipx install ./bridge
scenario-forge-bridge setup-runner
```

Ubuntu không có giao diện (server) thường không có Secret Service, nên device token được lưu vào file `~/.config/scenario-forge-bridge/device-token` (quyền 600). Windows lưu token trong Credential Manager.

## Đồng bộ dữ liệu CARLA

Đọc CARLA trong Python của runner (`setup-runner`): không cần cài thêm gì.

Trên trang Start up bấm **Đồng bộ dữ liệu CARLA** ở Bridge đang Online (hoặc gõ `scenario-forge-bridge sync`). Bridge:

1. đọc phiên bản CARLA, danh sách map, blueprint phương tiện / người đi bộ và preset thời tiết;
2. lần lượt **mở từng map** trong CARLA, đọc spawn point, waypoint làn đường (mỗi 2 m) và hash OpenDRIVE;
3. gửi mỗi map thành một catalog `scenario-forge.catalog.v1` (snapshot nguồn `WORKER`) cho Project;
4. mở lại map đang mở trước đó.

Trong lúc đồng bộ, CARLA chuyển map liên tục: đừng chạy mô phỏng khác cùng lúc. Map lớn (Town12/13/15) có thể mất vài phút.
Khi Project đã có dữ liệu đồng bộ, Test Case Builder chỉ hiện các map đó (không hiện map mặc định), và Agent sinh kịch bản trên đúng dữ liệu này.

```
Web "Đồng bộ dữ liệu CARLA" ─▶ POST /projects/{id}/bridges/{conn}/sync ─▶ WS catalog.sync.request ─▶ Bridge
Bridge ─(mỗi map)─▶ POST /bridge/catalog (device token) ─▶ snapshot WORKER ─▶ WS catalog.synced ─▶ Web
Bridge ─▶ WS catalog.sync.progress / catalog.sync.done ─▶ Backend ─▶ Web (thanh tiến độ)
```

## Chạy test case (Simulator Runner)

Web chọn test case → Simulator Runner → chọn Bridge. Backend gửi `run.assign` (XOSC của mọi test case) qua WebSocket; Bridge chạy lần lượt
từng test case bằng ScenarioRunner trong một tiến trình con rồi trả `job.started` → `job.completed | job.failed` → `run.completed`
(hợp đồng: `docs/21-simulator-runner-bridge.md`).

### Chuẩn bị máy

Lệnh cài ở trên đã chuẩn bị runner. Kiểm tra: `scenario-forge-bridge status` có dòng "Runner : sẵn sàng · CARLA 0.9.16".
Cấu hình tay (dùng runner tự dựng) vẫn được: `config --runner-python … --runner-root … --carla-root …`.

Mở CARLA **có cửa sổ** (`./CarlaUE4.sh`) rồi `scenario-forge-bridge run`. Khi chạy test, camera cửa sổ CARLA tự bám xe ego và lùi ra
để thấy cả hai xe lúc sắp va chạm (`config --camera off` để tắt).

### Mỗi test case

1. Kiểm tra `sha256(xosc)` = `xosc_sha256` (lệch → `job.failed XOSC_HASH_MISMATCH`) và cổng CARLA (đóng → `CARLA_UNREACHABLE`).
2. Chuẩn bị XOSC cho ScenarioRunner v0.9.16: thêm `criteria_CollisionTest` (không có criteria thì luôn coi là đạt) và
   thêm StopTrigger theo thời gian cho mỗi Act thiếu nó (ScenarioRunner bỏ qua StopTrigger cấp Storyboard, kịch bản sẽ chạy mãi).
   Route (`AssignRouteAction`) trong Init được chuyển thành event của Act: route trong Init giữ kịch bản RUNNING tới khi xe
   tới waypoint cuối, kể cả khi Act đã dừng.
   `RelativeTargetLane value="0"` của LaneChangeAction (ScenarioRunner chia cho 0) được đổi thành ±1 về phía xe được tham chiếu.
3. Chạy `<runner_python> scenario_runner.py --openscenario … --json --outputDir …` với `PYTHONPATH=<carla_root>/PythonAPI/carla`
   (gói `agents`); quá `timeout_s` → dừng tiến trình, `job.failed TIMEOUT`.
   ScenarioRunner đã in `No more scenarios .... Exiting` mà 15 giây sau chưa thoát (thường sau một exception) → dừng nó và
   báo `SCENARIO_RUNNER_ERROR` kèm traceback, không đợi tới `timeout_s`.
4. Đọc báo cáo JSON: `success` → `PASS`/`FAIL`, `CollisionTest.actual` → `collision_count`. Exit code chỉ để tham khảo
   (ScenarioRunner có thể trả 120 dù chạy xong).

Thư mục làm việc (XOSC, `runner.log`, `camera.log`, báo cáo JSON) giữ lại ở `<thư mục cấu hình>/runs/<run_id>/<case_key>/` để tra lỗi.
Thiếu cấu hình runner → cả phiên bị từ chối `RUNNER_NOT_READY` kèm lý do.

## Lệnh

```bash
scenario-forge-bridge pair 482913 --server https://forge.example.com/api/v1   # ghép, rồi giữ kết nối (Ctrl+C để dừng)
scenario-forge-bridge pair 482913 --no-run                                     # chỉ ghép, không giữ kết nối
scenario-forge-bridge run                 # giữ Bridge trực tuyến, tự kết nối lại khi mất mạng
scenario-forge-bridge setup-runner        # chọn phiên bản CARLA (menu) và chuẩn bị môi trường chạy test
scenario-forge-bridge setup-runner --carla 0.9.16 --carla-root ~/CARLA_0.9.16   # không cần menu
scenario-forge-bridge status              # máy chủ, Bridge, các Project đã ghép, trạng thái CARLA
scenario-forge-bridge check-carla         # mã thoát 0 = CARLA đang chạy ở 127.0.0.1:2000, 2 = chưa
scenario-forge-bridge sync                # đọc mọi map của CARLA và gửi lên mọi Project đã ghép
scenario-forge-bridge sync --maps Town03,Town10HD_Opt --connection CONN-XXXX
scenario-forge-bridge unpair CONN-XXXX    # gỡ khỏi một Project
scenario-forge-bridge unpair --all        # gỡ tất cả và xóa device token
scenario-forge-bridge config --server URL --carla-host 127.0.0.1 --carla-port 2000
scenario-forge-bridge config --runner-python ~/sr-venv/bin/python --runner-root ~/scenario_runner --carla-root ~/CARLA_0.9.16 --camera follow
```

`sfbridge` là tên ngắn của cùng lệnh.

Địa chỉ máy chủ mặc định là `http://localhost:8000/api/v1`. Cấu hình nằm ở:
- Ubuntu: `~/.config/scenario-forge-bridge/config.json`
- Windows: `%LOCALAPPDATA%\ScenarioForge\scenario-forge-bridge\config.json`

Biến môi trường dùng khi phát triển/kiểm thử: `SF_BRIDGE_CONFIG_DIR` (đổi thư mục cấu hình) và `SF_BRIDGE_TOKEN_STORE=file` (bỏ qua keyring).

## Phát triển

```bash
cd bridge
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest && ruff check .
```

## Giới hạn hiện tại

- Kết quả chưa được máy chủ xác nhận (`ack`) được gửi lại sau mỗi lần kết nối lại, nhưng chỉ giữ trong bộ nhớ: tắt hẳn Bridge thì mất.
- Không bấm "Đồng bộ dữ liệu CARLA" khi đang chạy test: cả hai cùng đổi map.
- Test case bị dừng vì quá thời gian có thể để lại xe trên map: mở lại CARLA trước lần chạy sau.

- Backend giữ WebSocket trong bộ nhớ của **một** tiến trình. Chạy nhiều instance backend cần pub/sub chung (RabbitMQ fan-out hoặc Redis) sau `app/modules/bridge/hub.py`.
- Giới hạn nhập sai mã (10 lần / 10 phút theo IP) cũng nằm trong bộ nhớ. Sau reverse proxy cần bật proxy headers để lấy đúng IP người dùng.
- `check-carla` và heartbeat chỉ thử TCP tới cổng RPC; riêng `sync` mới dùng `carla.Client`.
- Nếu CARLA đang ở synchronous mode mà không có client nào tick, `load_world` có thể treo: tắt chương trình đang điều khiển CARLA trước khi đồng bộ.
