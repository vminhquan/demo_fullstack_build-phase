# Bước 7: Dựng luồng sinh kịch bản bằng LangGraph

## Kết quả

`app/graph.py` định nghĩa và compile một `StateGraph` cho tình huống xe máy tạt đầu ô tô. `app/service.py` gọi graph này thay cho vòng lặp Python tạm ở bước 6; API và hợp đồng Backend không đổi. Mỗi request chạy độc lập với `session_id`, seed, snapshot đã chọn và giới hạn số lần đề xuất.

```text
START → extract_constraints ── cần làm rõ/lỗi ───────────→ finish → END
                         │
                         ↓
                    find_sites → sample_contexts ── hết site/lỗi ──→ finish
                                        │
                                        ↓
                                  select_variant
                                        ↓
                                propose_maneuver ── lỗi LLM ──→ reject_variant
                                        ↓
                                  validate_plan ── lỗi sửa được và còn lượt ──┐
                                        │                                     └→ propose_maneuver
                           hợp lệ → export_xosc
                           lỗi cứng → reject_variant
                                        ↓
                                  advance_variant ── còn biến thể → select_variant
                                        └─────────── hết biến thể → finish → END
```

## Node, state và context

| Node | Việc thực hiện |
| --- | --- |
| `extract_constraints` | Gọi LLM trích điều kiện prompt; gộp lựa chọn thời tiết/ánh sáng có cấu trúc; trả lỗi hoặc câu cần làm rõ nếu có. |
| `find_sites` | Lọc `cut_in_sites` đã sync từ CARLA theo map và địa điểm người dùng yêu cầu. |
| `sample_contexts` | Chọn site, blueprint, tốc độ và môi trường từ catalog bằng seed; lưu các biến thể đã chọn. |
| `select_variant` | Khóa biến thể hiện tại, xóa plan/feedback/số lần thử cũ. |
| `propose_maneuver` | Gọi LLM đề xuất bốn tham số tạt đầu, kèm feedback khi cần sửa. |
| `validate_plan` | Kiểm tra plan bằng Python. Chỉ quay lại LLM nếu **mọi** lỗi đều có `recoverable=true` và chưa hết lượt. |
| `export_xosc` | Xuất XOSC từ plan hợp lệ và snapshot gốc; lưu SHA-256 và provenance. |
| `reject_variant`, `advance_variant`, `finish` | Ghi lỗi theo map, chuyển biến thể, tổng hợp `completed`/`partial`/`failed`/`needs_clarification`. |

`GraphState` giữ prompt, seed, tham chiếu snapshot, ràng buộc, site, biến thể đã chọn, plan hiện tại, validation, feedback, số lần thử, kết quả và lỗi. Biến thể trong state là dữ liệu đã serialize; catalog chứa danh sách waypoint đầy đủ không nằm trong state. `GraphContext` truyền request, các snapshot đầy đủ và LLM client riêng cho lần gọi; các node đọc context qua `Runtime`. Cách dùng `StateGraph`, conditional edges và runtime context bám theo [tài liệu LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api).

Các hàm tìm site, chọn bối cảnh, kiểm tra và xuất XOSC là Python thuần chạy trong node. Chúng **không** được đăng ký làm LLM tool call. LLM chỉ được gọi tại `extract_constraints` và `propose_maneuver`; nó không chọn tọa độ map và không viết XML.

## Nhánh lỗi và nhiều phiên

- Prompt mơ hồ trả `needs_clarification` cùng câu cần làm rõ; graph kết thúc ngay. Hiện API chuyển trạng thái này thành HTTP 422, chưa có bước tạm dừng chờ người dùng trong graph.
- Site/catalog không phù hợp, điều kiện không hỗ trợ hoặc đề xuất sai không thể sửa được trả `map_failures`; lỗi ở một map không xóa XOSC đã sinh thành công trên map khác (`partial`).
- `max_proposal_attempts` từ request giới hạn 1–3 lần đề xuất cho mỗi biến thể. `recursion_limit` được tăng theo số biến thể và số lần thử để request nhiều kịch bản không chạm giới hạn mặc định của LangGraph.
- Graph compile một lần và có thể xử lý nhiều request đồng thời; `session_id` dùng để theo dấu và tạo ID kịch bản ổn định. **Chưa cấu hình checkpointer**: phiên không thể resume sau khi tiến trình dừng, cũng chưa lưu lịch sử node. Việc lưu checkpoint bền vững cần thiết kế idempotency và nơi lưu riêng trước khi bật.

## Kiểm tra

- `agent2/tests` cùng `bridge/tests/test_cut_in_sites.py`: **53 test qua**. Test mới kiểm tra sửa plan theo feedback, giới hạn lượt, lỗi site không lặp LLM, nhiều map, `partial`, prompt cần làm rõ, lỗi LLM, hai phiên đồng thời và phiên sáu biến thể vượt 25 bước.
- API test hiện có vẫn đi qua `service.generate` và LangGraph. Test dùng LLM giả; chưa gọi OpenAI hoặc chạy ScenarioRunner/CARLA thật.

LangGraph 1.2.14 được cài trong môi trường kiểm thử để chạy graph thật; `agent2/requirements.txt` giới hạn phiên bản 1.2 đến trước 2 để giữ API graph đang dùng.
