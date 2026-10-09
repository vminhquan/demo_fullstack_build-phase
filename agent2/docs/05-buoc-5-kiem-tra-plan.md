# Log triển khai bước 5: Kiểm tra plan tạt đầu trước khi xuất XOSC

## Mục tiêu

Bước 5 nhận `CutInPlan` từ LLM, `SampledVariant` của bước 3 và `SelectedSnapshot` chứa catalog gốc. Hàm `validate_cut_in(plan, variant, snapshot)` trả `ValidationResult` với các lỗi có mã. Đây là **bộ lọc sơ bộ bằng Python**; `valid=true` chưa có nghĩa XOSC chạy đúng trong ScenarioRunner.

## Code đã thêm

- `app/cut_in/validate.py`: kiểm tra dữ liệu gắn với snapshot, cặp làn và ước lượng chuyển động của hai xe.
- `app/cut_in/sample.py`: đổi bộ nhận diện blueprint xe máy thành `is_motorcycle_blueprint` để sampler và validator dùng cùng một quy tắc.
- `tests/test_cut_in.py`: trường hợp hợp lệ, offset âm, vượt hành lang, thiếu khoảng cách phía trước, đổi làn quá nhanh, sai snapshot/catalog/weather, làn ngược hướng và số không hữu hạn.

## Quy tắc kiểm tra

**Nguồn dữ liệu.** Site phải thuộc đúng snapshot và trùng toàn bộ bản ghi `cut_in_sites` trong catalog. Site cần có ít nhất 60 m hành lang đã xác minh. `site_id`, hai blueprint, hai tốc độ, môi trường và `sources` trong plan phải giữ nguyên từ `SampledContext`; blueprint phải là ô tô và xe máy trong catalog, weather preset phải có trên map đó. Sai nguồn dữ liệu là lỗi không thể sửa chỉ bằng cách chỉnh bốn số do LLM đề xuất.

**Hình học.** Hai làn phải cùng road/section, cùng dấu lane ID, lệch yaw không quá 20°, chênh cao không quá 1 m và cách tâm làn 2–6 m tại pose mốc. Thời gian đổi làn phải đủ dài để tốc độ ngang ước lượng không vượt 2,5 m/s. Đây là ngưỡng chính sách để loại đề xuất quá gấp, chưa phải mô hình động lực học của xe máy.

**Hành lang và khoảng cách.** Bước 2 chỉ xác minh đoạn đường **phía trước** mỗi pose mốc. Do đó `motorcycle_start_offset_m` phải từ 0 đến `available_length_m - 5 m`; offset âm bị từ chối vì phần đường phía sau chưa được xác minh. Với giả định tốc độ không đổi:

```text
t = trigger_time_s + lane_change_duration_s
ego_s(t) = ego_speed_kmh / 3.6 * t
motorcycle_s(t) = motorcycle_start_offset_m + motorcycle_speed_kmh / 3.6 * t
```

Tại lúc đổi làn hoàn tất, cả hai giá trị `s(t)` phải nằm trong hành lang còn 5 m dự phòng ở cuối. Xe máy phải ở trước ô tô ít nhất `desired_lead_gap_m + 4 m` theo khoảng cách giữa **tâm xe**. Phần 4 m là dự phòng chiều dài xe mang tính chính sách vì catalog hiện chưa có kích thước xe; nó không chứng minh khoảng hở giữa hai cản xe. Bộ kiểm tra cũng từ chối số `NaN` hoặc vô cực.

## Kết quả và vòng sửa

`ValidationResult(valid=false, issues=[...])` có mã, thông báo, trường liên quan và `recoverable`:

| Mã | Ý nghĩa | LLM có thể sửa bốn tham số? |
| --- | --- | --- |
| `SNAPSHOT_MISMATCH`, `SITE_NOT_IN_CATALOG`, `SITE_TOO_SHORT` | Site/snapshot không đúng hoặc thiếu hành lang. | Không |
| `CONTEXT_CHANGED`, `EGO_NOT_CAR`, `ACTOR_NOT_MOTORCYCLE`, `WEATHER_NOT_IN_CATALOG` | Dữ liệu đã chọn bị đổi hoặc không có trong catalog. | Không |
| `INVALID_LANE_GEOMETRY`, `NON_FINITE_SITE_LENGTH` | Site không đúng hình học hoặc chiều dài không hữu hạn. | Không |
| `NON_FINITE_PARAMETER` | Số trong plan không hữu hạn. | Có |
| `START_OUTSIDE_CORRIDOR`, `TRAVEL_OUTSIDE_CORRIDOR` | Vị trí bắt đầu hoặc quãng đường đi vượt phần đã xác minh. | Có |
| `LANE_CHANGE_TOO_FAST`, `INSUFFICIENT_LEAD_GAP` | Đổi làn quá nhanh hoặc xe máy chưa vượt đủ lên phía trước. | Có |

Caller ở bước LangGraph sau này có thể đưa thông báo `recoverable=true` vào `feedback` của `propose_maneuver`, với giới hạn số lần thử. Khi lỗi không thể sửa bằng bốn tham số, cần chọn site/bối cảnh khác hoặc báo lỗi cho map.

## Giới hạn đã biết

- Công thức dùng tốc độ không đổi; chưa xét phanh, tăng tốc, kích thước xe thực, va chạm, gia tốc ngang, luật giao thông hoặc giới hạn tốc độ của làn.
- Site hiện có hai pose mốc và độ dài hành lang, chưa có chuỗi pose dọc làn hoặc tọa độ OpenDRIVE `s` của mốc. Đặc biệt với đường cong, bước xuất XOSC không thể suy ra vị trí cách mốc nhiều mét bằng cách kéo thẳng theo yaw; cần bổ sung đường đi hoặc truy vấn lại CARLA trước khi khẳng định tọa độ xuất ra chính xác.

  Cập nhật ở [bước 6](06-buoc-6-xosc-va-ket-noi-agent2.md): Bridge sync thêm OpenDRIVE `s` và hướng di chuyển; site cũ thiếu trường này bị từ chối khi xuất XOSC.
- Validator không chạy CARLA/ScenarioRunner và chưa xác nhận `RoadCondition` thật sự thay đổi ma sát trong bản runner của người dùng.

## Kiểm tra đã chạy

Chạy toàn bộ `agent2/tests`: **34 test qua**. Ruff cho các file Python vừa sửa và `git diff --check`: qua. Các test dùng catalog mẫu và phép tính thuần Python, chưa chạy trên map CARLA thật.
