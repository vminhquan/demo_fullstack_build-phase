# Chạy thử XOSC của Scenario Forge trên CARLA 0.9.16 + ScenarioRunner v0.9.16

## Các file

| File | Nội dung |
|---|---|
| `sf_brake.xosc` | Town10HD_Opt, trời nắng: ego chạy 40 km/h, xe tải phía trước cùng làn đi 35 km/h, phanh gấp khi ego còn cách 16 m |
| `sf_cutin.xosc` | Town10HD_Opt, trời mưa, ngã tư: xe máy bên phải tạt đầu khi ego còn cách 12 m |
| `*.criteria.xosc` | Bản giống hệt nhưng có thêm `criteria_CollisionTest`. Bản gốc không có criteria nên ScenarioRunner luôn báo đạt |
| `add_criteria.py` | Thêm criteria vào XOSC khác: `python add_criteria.py file.xosc` |

Các file trên do agent sinh ở chế độ offline (không gọi LLM), dùng catalog Town10HD_Opt của CARLA 0.9.16 có trong repo.

## 1. Cài một lần (Ubuntu)

```bash
# CARLA 0.9.16 đã giải nén, ví dụ ~/CARLA_0.9.16
export CARLA_ROOT=~/CARLA_0.9.16

# ScenarioRunner đúng tag
git clone -b v0.9.16 --depth 1 https://github.com/carla-simulator/scenario_runner.git ~/scenario_runner
export SCENARIO_RUNNER_ROOT=~/scenario_runner

# Python 3.10 hoặc 3.11 (numpy==1.24.4 trong requirements không có bản cho 3.12)
python3.10 -m venv ~/sr-venv
~/sr-venv/bin/pip install --upgrade pip
~/sr-venv/bin/pip install carla==0.9.16 -r $SCENARIO_RUNNER_ROOT/requirements.txt

# ScenarioRunner import `agents.navigation...` nằm trong PythonAPI của CARLA
export PYTHONPATH=$CARLA_ROOT/PythonAPI/carla:$PYTHONPATH
```

Windows (PowerShell): giống trên, đổi sang `py -3.10 -m venv C:\sr-venv`, `C:\sr-venv\Scripts\pip ...`, `$env:CARLA_ROOT="C:\CARLA_0.9.16"`, `$env:PYTHONPATH="$env:CARLA_ROOT\PythonAPI\carla"`.

Kiểm tra:

```bash
~/sr-venv/bin/python -c "import carla, py_trees, agents.navigation.basic_agent; print('ok', carla.__file__)"
```

## 2. Mở CARLA

```bash
cd $CARLA_ROOT && ./CarlaUE4.sh -quality-level=Low      # Windows: CarlaUE4.exe -quality-level=Low
```

Chờ cửa sổ CARLA hiện ra (cổng 2000). Không cần tự đổi map: ScenarioRunner tự `load_world("Town10HD_Opt")` theo `LogicFile`.

## 3. Chạy kịch bản

Terminal thứ hai (đã `export` 3 biến ở bước 1):

```bash
cd $SCENARIO_RUNNER_ROOT
mkdir -p /tmp/sf-out
~/sr-venv/bin/python scenario_runner.py \
  --openscenario /đường/dẫn/samples/sf_brake.criteria.xosc \
  --host 127.0.0.1 --port 2000 \
  --output --json --outputDir /tmp/sf-out \
  2>&1 | tee /tmp/sf-out/runner.log
echo "exit code: ${PIPESTATUS[0]}"
```

Để nhìn xe chạy, mở terminal thứ ba ngay sau khi chạy lệnh trên. Camera spectator sẽ bám theo xe ego (`hero`) từ phía sau và trên cao:

```bash
~/sr-venv/bin/python - <<'EOF'
import carla, math, time
world = carla.Client("127.0.0.1", 2000).get_world()
while True:
    heroes = [a for a in world.get_actors().filter("vehicle.*") if a.attributes.get("role_name") == "hero"]
    if heroes:
        t = heroes[0].get_transform(); yaw = math.radians(t.rotation.yaw)
        loc = t.location + carla.Location(x=-12 * math.cos(yaw), y=-12 * math.sin(yaw), z=8)
        world.get_spectator().set_transform(carla.Transform(loc, carla.Rotation(pitch=-25, yaw=t.rotation.yaw)))
    time.sleep(0.05)
EOF
```

Không dùng `manual_control.py` của ScenarioRunner để xem: script đó điều khiển xe `hero` và sẽ tranh quyền lái với `NpcVehicleControl`.

## 4. Đọc kết quả

- `--output`: bảng kết quả in ra terminal (CollisionTest, Duration, SUCCESS/FAILURE).
- `--json`: file JSON trong `/tmp/sf-out`, dạng:

```json
{ "scenario": "...", "success": true,
  "criteria": [ {"name": "...", "actor": "tesla.model3-123", "optional": false, "expected": 0, "actual": 0, "success": true},
                {"name": "Duration", "actor": "all", "optional": false, "expected": ..., "actual": ..., "success": true} ] }
```

- Exit code `0` = mọi tiêu chí đạt, `1` = có tiêu chí trượt.

## 5. Cần ghi lại và gửi lại (để viết Bridge cho khớp)

1. Xe ego `hero` có tự chạy theo route ở 40 km/h không (ScenarioRunner giao xe không có ControllerAction cho `NpcVehicleControl`).
2. Xe tải có phanh khi ego còn ~16 m không; có va chạm không.
3. Nội dung file JSON (đặc biệt `name` và `actual` của tiêu chí va chạm).
4. Exit code và 50 dòng cuối `runner.log`.
5. Thời gian từ lúc chạy lệnh tới khi kết thúc (gồm thời gian load map).

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `ModuleNotFoundError: agents` | Thiếu `PYTHONPATH=$CARLA_ROOT/PythonAPI/carla` |
| `No module named carla` / lỗi version mismatch | Venv chưa cài `carla==0.9.16` hoặc đang dùng nhầm Python |
| Pip lỗi khi cài `numpy==1.24.4` | Đang dùng Python 3.12: tạo lại venv bằng 3.10/3.11 |
| `time-out of 10000ms while waiting for the simulator` | CARLA chưa mở xong hoặc sai `--host/--port`; thêm `--timeout 60` khi map load chậm |
| `Nothing to analyze, this scenario has no criteria` | Đang chạy bản không có `.criteria`: dùng `*.criteria.xosc` |
| Treo khi load map | CARLA đang ở synchronous mode do chương trình khác: tắt chương trình đó rồi chạy lại |
