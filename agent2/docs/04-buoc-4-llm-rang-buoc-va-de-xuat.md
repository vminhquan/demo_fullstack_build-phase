# Log triển khai bước 4: LLM trích ràng buộc và đề xuất tạt đầu

## Mục tiêu

Bước 4 nối `agent2` với OpenAI API, dùng model mặc định `gpt-4o-mini`. LLM làm hai việc có schema rõ ràng: trích điều kiện người dùng từ prompt và đề xuất bốn tham số thao tác cho một `SampledVariant` đã được bước 3 chọn. Bước này chưa dựng LangGraph, chưa xuất XOSC và chưa chạy ScenarioRunner.

## Code đã thêm

| File | Nội dung |
| --- | --- |
| `app/llm/prompts.py` | System prompt trích điều kiện, system prompt đề xuất thao tác và hàm tạo message. Prompt đề xuất chỉ nhận thông tin gọn của một site, không gửi toàn bộ catalog hoặc mảng waypoint. |
| `app/llm/client.py` | `OpenAILLM`, hai schema output, hai phương thức `extract_constraints` và `propose_maneuver`, cùng `LLMCallError` có mã. `from_settings()` lấy model và API key từ cấu hình hiện có. |
| `app/cut_in/sample.py` | `SampledVariant` từ chối nếu `context.site_id` khác `site.site_id`. |
| `tests/test_llm.py` | Test hợp đồng lời gọi, khóa dữ liệu đã chọn, phản hồi lỗi và prompt gọn bằng OpenAI client giả lập. |

[Tài liệu Structured Outputs của OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs) xác nhận `gpt-4o-mini` hỗ trợ output theo schema và Python SDK có `chat.completions.parse` với model Pydantic. Client dùng cơ chế đó; không cần tool call cho hai thao tác này vì LLM không truy cập CARLA hay Backend. Hàm tìm site và chọn bối cảnh vẫn do Python thực hiện.

## Luồng dữ liệu

1. `extract_constraints(prompt)` trả `PromptConstraints`: `location_tags`, thời tiết, ánh sáng, mặt đường, tốc độ từng xe, yêu cầu mơ hồ hoặc chưa hỗ trợ. Prompt thiếu thông tin giữ `None` hoặc danh sách rỗng để bước 3 random. “Ngã tư” được yêu cầu trích thành `intersection_4way`, không hạ xuống `junction`.
2. Bước 2 tìm site theo địa điểm; bước 3 chọn site, xe, preset và tốc độ từ snapshot map phù hợp.
3. `propose_maneuver(prompt, sampled_variant, feedback=...)` gửi cho LLM tên map, site ID, hai làn, chiều dài đoạn đường, hai tốc độ, preset thời tiết và mặt đường. Nếu có lỗi kiểm tra từ vòng trước, caller có thể truyền feedback.
4. Output LLM ở bước đề xuất **chỉ gồm** `motorcycle_start_offset_m`, `trigger_time_s`, `lane_change_duration_s`, `desired_lead_gap_m`. Code ghép bốn số đó với `SampledContext` để tạo `CutInPlan`. LLM không quyết định lại map, vị trí, blueprint, tốc độ, thời tiết hay tọa độ.

Ví dụ rút gọn cho lời gọi thứ hai:

```json
{
  "input": {
    "map_name": "Town01", "site_id": "Town01-site", "available_length_m": 65,
    "ego_speed_kmh": 37, "motorcycle_speed_kmh": 43,
    "weather_preset": "HardRainNight", "road_surface": "slippery"
  },
  "llm_proposal": {
    "motorcycle_start_offset_m": 10,
    "trigger_time_s": 2,
    "lane_change_duration_s": 2.5,
    "desired_lead_gap_m": 5
  }
}
```

Đây là tham số **đề xuất**, chưa được xác nhận là khả thi hoặc chạy đúng. Bước kiểm tra hình học/động học và chạy mô phỏng phải quyết định có chấp nhận plan hay yêu cầu LLM sửa.

## Xử lý lỗi và giới hạn

- `OPENAI_NOT_CONFIGURED`: thiếu `OPENAI_API_KEY` khi tạo client thật.
- `LLM_EMPTY_RESPONSE`, `LLM_INCOMPLETE_RESPONSE`, `LLM_REFUSAL`, `LLM_UNPARSEABLE_RESPONSE`: phản hồi API không tạo được output theo schema.
- `LLM_INVALID_CONSTRAINTS`, `LLM_INVALID_PROPOSAL`: output có schema nhưng không đạt ràng buộc domain của Pydantic.

Client không ghi API key vào prompt hoặc log. Model mặc định lấy từ `Settings.model_name`; có thể đổi bằng `AGENT2_MODEL_NAME`. Seed ở bước 3 chỉ tái hiện phần chọn bối cảnh bằng Python; bước 4 hiện không cam kết lặp lại từng số do LLM đề xuất. Chất lượng trích ràng buộc tiếng Việt và tính khả thi của tham số vẫn cần đánh giá bằng bộ prompt thực tế và ScenarioRunner.

## Kiểm tra đã chạy

Chạy toàn bộ `agent2/tests`: **25 test qua**. Test bước 4 dùng SDK client giả lập, kiểm tra prompt thiếu ngữ cảnh, ràng buộc ngã tư/mưa/đường trơn, bốn tham số đề xuất, việc giữ nguyên dữ liệu đã chọn, site ID lệch, phản hồi từ chối/cụt/thiếu parsed và thiếu key. Ruff với các file vừa sửa và `git diff --check`: qua.

Chưa gọi OpenAI API thật trong lần kiểm tra này; môi trường test hiện chưa cài package `openai` và không dùng API key. Khi cài `agent2/requirements.txt` và cấu hình key, `from_settings()` sẽ tạo client thật theo đường chạy đã viết.
