# Bước 6: Xuất XOSC và chuyển Backend sang agent2

## Đã triển khai

- Bridge ghi thêm `ego_s`, `motorcycle_s`, `s_direction`, `target_side` vào mỗi `cut_in_site`. Chỉ ghi site nếu hai làn liên tục cùng road/section/lane và OpenDRIVE `s` tiến cùng hướng trên toàn hành lang xác minh. Catalog cũ vẫn đọc được nhưng không đủ dữ liệu để xuất XOSC: cần sync lại từ Bridge.
- `agent2/app/cut_in/xosc.py` xuất OpenSCENARIO 1.0 bằng Python từ plan đã qua `validate_cut_in`. Hai xe được đặt bằng `LanePosition`, có tốc độ ban đầu, môi trường, `RoadCondition`, sự kiện `LaneChangeAction` và trigger kết thúc. Dấu `RelativeTargetLane` lấy từ hướng làn kề do Bridge xác minh. SHA-256 tính từ đúng XML trả ra.
- Bản sao schema ASAM OpenSCENARIO 1.0 nằm ở `agent2/knowledge/schemas`, độc lập với thư mục `agent/` cũ. Test dùng `xmllint` để kiểm tra XML mẫu với schema.
- `agent2/app/service.py` nối tạm các bước trích prompt → tìm site → chọn bối cảnh → đề xuất/sửa plan tối đa 3 lần → validate → XOSC. `agent2/app/api.py` mở `POST /v1/scenarios/generate`, nhận `GenerationRequest` chứa snapshot đã chọn và kiểm tra `X-API-Key` khi cấu hình key. Đây là điểm vào để Backend hoạt động trước bước dựng LangGraph; các hàm miền giữ độc lập để dùng lại trong graph.

  Cập nhật: [bước 7](07-buoc-7-langgraph.md) đã thay vòng lặp tạm bằng `StateGraph`; API giữ nguyên.
- Backend HTTP adapter gửi catalog/snapshot thật sang agent2 và chuyển một `GeneratedScenario` về cấu trúc lưu trữ hiện tại của Backend. Docker Compose, override và Render build/chạy `agent2/`; không còn service runtime trỏ tới `agent/` cũ. Backend vẫn dùng cổng nội bộ 8100 và `AGENT_API_KEY`.

## Hợp đồng khi chạy

```text
Backend snapshot CARLA → agent2 GenerationRequest(selected_snapshots, prompt, seed, target_count=1)
→ GeneratedScenario(plan, validation, xosc, xosc_sha256)
→ Backend lưu ScenarioGeneration và XOSC → Bridge nhận XOSC khi chạy test case
```

Người dùng có thể chọn map đã sync; Backend hiện gửi một map mỗi lần gọi, còn API agent2 nhận nhiều snapshot/biến thể để dùng cho luồng nhiều map về sau. Với dữ liệu mặc định hoặc snapshot cũ không có `cut_in_sites`/OpenDRIVE `s`, agent2 trả mã lỗi và yêu cầu sync lại. Người dùng chọn adversary khác xe máy sẽ nhận `UNSUPPORTED_ACTOR`.

Adapter Backend giữ `profile` cho ODD và `title` cho Builder bằng phép tính Python đơn giản từ dữ liệu hiện có, không gọi endpoint của agent cũ. Biểu mẫu `/scenario-generations/refine` cũ trả `UNSUPPORTED_FORM` vì spec đó không biểu diễn riêng tình huống xe máy tạt đầu; luồng nhập mô tả tự do vẫn dùng `/scenario-generations` và Builder. Việc chuyển biểu mẫu cũ sang hợp đồng mới cần một thay đổi UI riêng.

## Giới hạn xác nhận

- File XOSC mẫu qua ASAM OpenSCENARIO 1.0 XSD và kiểm tra cấu trúc trong test. Chưa chạy ScenarioRunner/CARLA thật trong môi trường này; vì vậy chưa thể khẳng định diễn biến động học, khoảng hở sau đổi làn hoặc tác động vật lý của `RoadCondition` trên bản runner đang cài ở máy người dùng.
- Numeric weather trong XOSC là chính sách ánh xạ từ điều kiện/preset đã chọn, không phải toàn bộ tham số weather được lấy ngược từ CARLA. `dust` hiện biểu diễn bằng giảm tầm nhìn của Fog. Tốc độ và quãng đường đổi làn dùng giả định tốc độ không đổi của bước 5.
- Backend đang cần `OPENAI_API_KEY` để trích prompt và đề xuất maneuver; không có chế độ offline của agent cũ. Đánh giá trong simulator và sinh nhiều XOSC trong một request Backend thuộc bước sau. LangGraph đã triển khai ở [bước 7](07-buoc-7-langgraph.md).
- Các file lịch sử trong `agent/` vẫn còn trong repo để tra cứu, nhưng runtime Docker/Render và đường gọi Backend không còn phụ thuộc service đó.

## Kiểm tra

- `agent2/tests` và `bridge/tests/test_cut_in_sites.py`: **45 test qua**. Có test XSD, chiều tăng/giảm OpenDRIVE `s`, hướng đổi làn, từ chối site cũ và API key.
- `backend/tests/unit`: **76 test qua**, gồm adapter request/response agent2.
- Toàn bộ `bridge/tests`: **39 test qua, 1 test socket bị loại khỏi lần chạy cuối**. Test socket riêng bị sandbox từ chối `bind(127.0.0.1)`, không liên quan tới XOSC/site.
- XOSC sau khi đi qua `Bridge.prepare_xosc` vẫn qua ASAM XSD.
- `docker compose config --services`: hiện `agent2`, không có `agent` cũ. `git diff --check`: qua.

Tham khảo khả năng OpenSCENARIO 1.0 của [ScenarioRunner](https://github.com/carla-simulator/scenario_runner/blob/master/Docs/openscenario_support.md) và [ví dụ LaneChangeSimple](https://github.com/carla-simulator/scenario_runner/blob/master/srunner/examples/LaneChangeSimple.xosc).
