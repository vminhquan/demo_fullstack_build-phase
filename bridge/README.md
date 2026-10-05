# Scenario Forge Bridge

Phần mềm cài trên **máy có CARLA** (Ubuntu hoặc Windows) để nối máy đó với một hoặc nhiều Project trên Scenario Forge.

Bridge là ống thông giữa Scenario Forge (production) và CARLA trên máy người dùng. Hiện làm được: **ghép nối bằng OTP 6 số**, **giữ kết nối lâu dài qua WebSocket**, **kiểm tra cổng CARLA (2000)** và **đồng bộ dữ liệu mọi map của CARLA** lên Project để Test Case Builder và Agent dùng. Chưa chạy kịch bản trên CARLA.

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

### Cách 1: file chạy đóng gói sẵn (người dùng cuối)

Build trên đúng hệ điều hành đích, vì PyInstaller không build chéo:

```bash
# Ubuntu
./scripts/build.sh                 # tạo dist/scenario-forge-bridge
./scripts/install.sh               # chép vào ~/.local/bin
```

```powershell
# Windows (PowerShell)
.\scripts\build.ps1                # tạo dist\scenario-forge-bridge.exe
```

### Cách 2: từ mã nguồn (Python 3.10+)

```bash
pipx install ./bridge              # hoặc: python -m pip install ./bridge
```

Ubuntu không có giao diện (server) thường không có Secret Service, nên device token được lưu vào file `~/.config/scenario-forge-bridge/device-token` (quyền 600). Windows lưu token trong Credential Manager.

## Đồng bộ dữ liệu CARLA

Cần gói Python `carla` **đúng phiên bản với CARLA server** (cài vào cùng môi trường với Bridge):

```bash
pipx inject scenario-forge-bridge carla==0.9.16    # đổi theo phiên bản CARLA của bạn
```

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

## Lệnh

```bash
scenario-forge-bridge pair 482913 --server https://forge.example.com/api/v1   # ghép, rồi giữ kết nối (Ctrl+C để dừng)
scenario-forge-bridge pair 482913 --no-run                                     # chỉ ghép, không giữ kết nối
scenario-forge-bridge run                 # giữ Bridge trực tuyến, tự kết nối lại khi mất mạng
scenario-forge-bridge status              # máy chủ, Bridge, các Project đã ghép, trạng thái CARLA
scenario-forge-bridge check-carla         # mã thoát 0 = CARLA đang chạy ở 127.0.0.1:2000, 2 = chưa
scenario-forge-bridge sync                # đọc mọi map của CARLA và gửi lên mọi Project đã ghép
scenario-forge-bridge sync --maps Town03,Town10HD_Opt --connection CONN-XXXX
scenario-forge-bridge unpair CONN-XXXX    # gỡ khỏi một Project
scenario-forge-bridge unpair --all        # gỡ tất cả và xóa device token
scenario-forge-bridge config --server URL --carla-host 127.0.0.1 --carla-port 2000
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

- Backend giữ WebSocket trong bộ nhớ của **một** tiến trình. Chạy nhiều instance backend cần pub/sub chung (RabbitMQ fan-out hoặc Redis) sau `app/modules/bridge/hub.py`.
- Giới hạn nhập sai mã (10 lần / 10 phút theo IP) cũng nằm trong bộ nhớ. Sau reverse proxy cần bật proxy headers để lấy đúng IP người dùng.
- `check-carla` và heartbeat chỉ thử TCP tới cổng RPC; riêng `sync` mới dùng `carla.Client`.
- Nếu CARLA đang ở synchronous mode mà không có client nào tick, `load_world` có thể treo: tắt chương trình đang điều khiển CARLA trước khi đồng bộ.
