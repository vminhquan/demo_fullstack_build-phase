# Log triển khai bước 3: Chọn bối cảnh ngẫu nhiên có thể tái hiện

## Mục tiêu

Sau khi bước 2 tìm được site hợp lệ, bước 3 chọn site, blueprint ô tô, blueprint xe máy, weather preset, mặt đường và tốc độ để tạo đầu vào cố định cho LLM đề xuất thao tác tạt đầu. Phần này chưa gọi LLM và chưa xuất XOSC.

## Code đã thêm

- `app/cut_in/sample.py`: hàm `sample_variants(selected_snapshots, sites, constraints, target_count=..., seed=...)` trả `SamplingResult` gồm seed thực dùng, danh sách `SampledVariant` và lỗi theo map.
- `tests/test_sample.py`: kiểm tra tính tái hiện, nhiều map, ràng buộc người dùng, blueprint, thời tiết và các trường hợp lỗi.

`sites` là kết quả của `find_cut_in_sites` ở bước 2. Mỗi `SampledVariant` giữ `CutInSite` (có snapshot/map và tọa độ đã xác minh) cùng `SampledContext` (site ID, hai blueprint, hai tốc độ và môi trường). Nếu caller truyền site của snapshot không được chọn, hàm báo lỗi thay vì dùng lẫn dữ liệu map.

## Logic chọn

1. Chuẩn hóa các ràng buộc đã được trích từ prompt vào `PromptConstraints`. Thiếu địa điểm, thời tiết, ánh sáng, mặt đường hoặc tốc độ là hợp lệ và sẽ được chọn ngẫu nhiên hoặc suy ra. Ràng buộc mơ hồ, chưa hỗ trợ hoặc mâu thuẫn được báo rõ, không bị bỏ qua.
2. Với mỗi map có site, chỉ dùng `base_type=car` cho ô tô. Xe máy dùng `base_type=motorcycle`; nếu catalog thiếu `base_type`, chỉ chấp nhận blueprint 2 bánh có tên thuộc nhóm xe máy đã biết (Yamaha, Kawasaki, Vespa, Harley-Davidson). Xe đạp 2 bánh không được nhận thành xe máy.
3. Chỉ dùng tên weather preset có trong catalog của chính snapshot đó. Bỏ `Default` và preset không xác định được điều kiện hoặc ánh sáng. Điều kiện thời tiết trong một request là tập yêu cầu đồng thời; preset phải đáp ứng tất cả. Các tên `Clear`, `Rain`, `Cloudy`, `Wet`, `DustStorm` và `Fog` được nhận diện; sương mù chỉ có thể chọn nếu catalog thực sự có preset tương ứng.
4. Lọc preset theo ánh sáng và mặt đường mà người dùng yêu cầu. Không có ràng buộc thì mọi preset được hỗ trợ đều có thể được chọn. Trời mưa hoặc preset ướt được suy ra mặt đường `wet`; còn lại là `dry`. Người dùng có thể yêu cầu `slippery` và giữ nguyên yêu cầu đó.
5. Chọn ngẫu nhiên bằng `random.Random(seed)`. Mỗi vòng trộn các map dùng được và lấy một biến thể mỗi map, nên khi `target_count` đủ lớn, tất cả map dùng được đều xuất hiện trước khi map nào lặp lại. Trong map, site, blueprint và preset được chọn từ danh sách đã sắp xếp; cùng snapshot, ràng buộc và seed sẽ cho cùng kết quả, bất kể thứ tự input. Nếu không truyền seed, hàm tạo seed và trả về để có thể chạy lại.
6. Tốc độ người dùng chỉ định được giữ đúng nếu trong `(0, 120]` km/h. Nếu thiếu, ô tô được lấy ngẫu nhiên trong 25–55 km/h, xe máy trong 25–65 km/h. Đây là dải chính sách ban đầu để tạo ứng viên, chưa phải giới hạn tốc độ riêng của map hay xác nhận tính khả thi của cú tạt đầu.

`SampledContext.sources` và `EnvironmentSelection.sources` ghi trường nào do người dùng yêu cầu, trường nào do random, trường nào được suy ra. Weather preset là tên từ catalog; `profile_id` có dạng `carla_preset:<tên>`.

## Ví dụ dữ liệu ra

Với một site hợp lệ trên `Town01`, prompt đã được trích thành mưa, ban đêm, đường trơn, ô tô 37 km/h và xe máy 43 km/h, một biến thể có dạng rút gọn:

```json
{
  "seed": 7,
  "variants": [{
    "site": {"site_id": "Town01-site", "snapshot": {"snapshot_id": 1, "map_name": "Town01", "content_hash": "..."}},
    "context": {
      "site_id": "Town01-site",
      "ego_blueprint_id": "vehicle.tesla.model3",
      "motorcycle_blueprint_id": "vehicle.yamaha.yzf",
      "ego_speed_kmh": 37,
      "motorcycle_speed_kmh": 43,
      "environment": {
        "profile_id": "carla_preset:HardRainNight",
        "weather_preset": "HardRainNight",
        "road_surface": "slippery",
        "friction_scale_factor": 0.6
      }
    }
  }],
  "map_failures": []
}
```

Ví dụ trên lược bớt pose, làn đường, nhãn site, thời điểm và thông tin `sources`; `content_hash` thực tế luôn là SHA-256 đủ 64 ký tự.

## Lỗi và giới hạn

- `VEHICLE_NOT_AVAILABLE`: map có site nhưng catalog không có ô tô hoặc xe máy xác định được.
- `WEATHER_NOT_AVAILABLE`: map không có preset phù hợp; các map khác vẫn được lấy mẫu.
- `UNSUPPORTED_WEATHER_CONDITION`, `UNSUPPORTED_LIGHTING`, `UNSUPPORTED_ROAD_SURFACE`, `UNSUPPORTED_SPEED`, `UNSUPPORTED_REQUIREMENT`, `AMBIGUOUS_PROMPT`, `ENVIRONMENT_CONFLICT`: lỗi ràng buộc toàn request dưới dạng `SamplingError` có `code`.

Hệ số ma sát `dry=1.0`, `wet=0.8`, `slippery=0.6` là **chính sách ứng viên** của agent2, chưa được đo từ CARLA và chưa được áp dụng trong simulator ở bước này. Theo [CARLA Python API](https://carla.readthedocs.io/en/latest/python_api/), thông số weather của CARLA chủ yếu ảnh hưởng hình ảnh, không tự làm đổi vật lý xe; vì vậy bước xuất XOSC và chạy ScenarioRunner sau này phải kiểm tra riêng `RoadCondition` và hiệu ứng thực tế. Bộ chọn cũng chưa biết giới hạn tốc độ của từng đường vì `catalog.v1` hiện không mang dữ liệu đó.

## Kiểm tra đã chạy

- `tests/test_sample.py` và `tests/test_find_sites.py`: **15 test qua**. Bao gồm tái hiện bằng seed khi đảo thứ tự input, phủ nhiều map, giữ yêu cầu mưa/đêm/đường trơn/tốc độ, loại xe đạp, thiếu weather preset, điều kiện mâu thuẫn và site ngoài snapshot được chọn.
- Ruff trên file sampler và test mới: qua.

Chưa chạy CARLA thật, ScenarioRunner hoặc XOSC trong bước 3. Output này là đầu vào cho node LLM và các bước kiểm tra tính khả thi tiếp theo.
