# Bước 1: Hợp đồng dữ liệu cho agent2

## Mục tiêu và phạm vi

Bước 1 định nghĩa dữ liệu vào, dữ liệu ra và state của phiên sinh kịch bản xe máy tạt đầu ô tô. Đây là nền để viết bộ tìm vị trí, bộ chọn ngẫu nhiên, các node LangGraph và API sinh kịch bản ở những bước sau.

Các model nằm trong ba file:

| File | Nội dung đã thêm |
| --- | --- |
| `app/catalog/models.py` | Dữ liệu `catalog.v1` sync từ CARLA, tham chiếu snapshot và một vị trí tạt đầu đã được xác minh. |
| `app/cut_in/model.py` | Ràng buộc rút từ prompt, môi trường được chọn, tham số tạt đầu và kết quả kiểm tra. |
| `app/contracts.py` | Request/response sinh kịch bản, kết quả theo map, `GraphState` và `GraphContext`. |

## Input của một phiên

`GenerationRequest` gồm:

- `session_id` và prompt người dùng.
- `selected_snapshots`: từ 1 đến 20 snapshot của những map đã chọn. Mỗi snapshot có ID, tên map, SHA-256 của nội dung và catalog đầy đủ.
- `target_count`: số kịch bản cần sinh, từ 1 đến 100.
- `seed` tùy chọn. Khi không có seed, service ở bước sau sẽ tạo seed và ghi nó vào kết quả để tái hiện lần sinh.

Request từ chối map trùng hoặc snapshot ID trùng. `SelectedSnapshot` từ chối nếu tên map không khớp với catalog, hoặc hash trong catalog (khi có) không khớp với tham chiếu snapshot. Catalog sử dụng đúng các trường mà Bridge hiện sync: phiên bản CARLA, map, blueprint, spawn point, waypoint và tên weather preset.

## Dữ liệu dùng trong luồng sinh

- `PromptConstraints` chỉ giữ điều kiện người dùng đã nói: địa điểm, thời tiết, ánh sáng, mặt đường và tốc độ từng xe. Trường không được nói giữ `None` hoặc danh sách rỗng; nó **không** tự chuyển thành trời quang hay một địa điểm mặc định. Model cũng có chỗ ghi yêu cầu mơ hồ hoặc chưa được hỗ trợ.
- `CutInSite` gắn với snapshot, hai làn khác nhau, hai pose mốc, chiều dài đường có thể dùng và nhãn địa điểm. `verification_source` cho biết vị trí được xác minh từ topology CARLA hay kiểm tra thủ công. Schema chỉ kiểm tra hai làn khác nhau; nó không thể tự chứng minh hai làn kề cùng chiều và nối liên tục. Trách nhiệm đó thuộc bộ tìm site ở bước tiếp theo.
- `EnvironmentSelection` giữ profile môi trường, các điều kiện thời tiết, thời điểm, mặt đường và hệ số ma sát. `sources` ghi giá trị đến từ yêu cầu người dùng, lựa chọn ngẫu nhiên hay suy ra từ dữ liệu.
- `SampledContext` giữ site, hai blueprint, tốc độ và môi trường đã chọn trước khi LLM đề xuất thao tác.
- `CutInPlan` bổ sung độ lệch dọc ban đầu của xe máy so với ô tô, thời điểm kích hoạt, thời lượng đổi làn và khoảng cách mong muốn khi xe máy ở phía trước. Nó tham chiếu `site_id`, không chứa tọa độ do LLM tự tạo.
- `ValidationResult` gồm trạng thái đạt/không đạt và các lỗi có mã, thông báo, trường liên quan cùng khả năng sửa.

## State và output

`GraphState` giữ dữ liệu nhỏ cần đi qua các node: tham chiếu snapshot, ràng buộc prompt, các site, ứng viên đang thử, kết quả kiểm tra, kịch bản đã sinh, lỗi theo map và số lần thử. `GraphContext` mang catalog đầy đủ cho node dùng lúc chạy; các mảng waypoint lớn không phải nằm trong checkpoint của state.

Cập nhật ở [bước 7](07-buoc-7-langgraph.md): state dùng `sampled_variants` và `variant_index` để lặp qua nhiều ứng viên, còn request, snapshot đầy đủ và LLM client nằm trong runtime context. Bước 7 chưa bật checkpoint bền vững.

`GenerationResponse` trả `completed`, `partial`, `failed` hoặc `needs_clarification`, kèm seed thực dùng, các kịch bản đã sinh, lỗi theo map và câu hỏi cần làm rõ. Mỗi `GeneratedScenario` gồm snapshot, site, `CutInPlan`, kết quả kiểm tra, nội dung XOSC và SHA-256 của file. Contract từ chối kịch bản có `site_id` khác plan hoặc có kết quả kiểm tra không đạt; response `completed` phải đủ `target_count`.

## Kiểm tra đã thực hiện

Đã kiểm tra cú pháp và import của ba file. Một phép kiểm tra Pydantic với catalog mẫu xác nhận request, site, plan và response hợp lệ tạo được; đồng thời xác nhận dữ liệu bị từ chối khi map trùng, snapshot lệch map, hai làn trùng nhau hoặc response báo `completed` nhưng thiếu kịch bản. Phép kiểm tra chạy bằng môi trường Python sẵn có của Backend với Pydantic 2.13.5; chưa chạy ScenarioRunner ở bước này.

Các model hiện mới là hợp đồng dữ liệu. Bộ tìm site, chọn môi trường ngẫu nhiên, node LangGraph, LLM và bộ xuất XOSC sẽ dùng các hợp đồng này ở các bước triển khai tiếp theo.
