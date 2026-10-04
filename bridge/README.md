# Scenario Forge Bridge

Phần mềm cài trên **máy có CARLA** (Ubuntu hoặc Windows) để nối máy đó với một hoặc nhiều Project trên Scenario Forge.

Phiên bản hiện tại chỉ làm: **ghép nối bằng OTP 6 số**, **giữ kết nối lâu dài qua WebSocket** và **kiểm tra cổng CARLA (2000)**. Chưa điều khiển CARLA.

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

## Lệnh

```bash
scenario-forge-bridge pair 482913 --server https://forge.example.com/api/v1   # ghép, rồi giữ kết nối (Ctrl+C để dừng)
scenario-forge-bridge pair 482913 --no-run                                     # chỉ ghép, không giữ kết nối
scenario-forge-bridge run                 # giữ Bridge trực tuyến, tự kết nối lại khi mất mạng
scenario-forge-bridge status              # máy chủ, Bridge, các Project đã ghép, trạng thái CARLA
scenario-forge-bridge check-carla         # mã thoát 0 = CARLA đang chạy ở 127.0.0.1:2000, 2 = chưa
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
- Kiểm tra CARLA mới chỉ là TCP connect tới cổng RPC, chưa gọi `carla.Client`.
