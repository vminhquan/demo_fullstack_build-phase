# Benchmark Agent sinh kịch bản

Đo chất lượng Agent trên một bộ prompt có kỳ vọng (`cases.json`, 22 ca: 20 kịch bản + 2 câu hỏi quy định), với dữ liệu CARLA thật (mặc định: `backend/app/modules/catalog/default_data/Town10HD_Opt.json`).

## Chạy

```bash
cd agent
.venv/bin/python -m benchmark.run                         # Agent in-process; dùng OpenAI nếu có OPENAI_API_KEY (tự đọc ../.env)
.venv/bin/python -m benchmark.run --offline               # chỉ quy tắc offline, không tốn tiền OpenAI (baseline)
.venv/bin/python -m benchmark.run --concurrency 3         # chạy song song
.venv/bin/python -m benchmark.run --repeat 3              # mỗi ca 3 lần, đo độ ổn định của LLM
.venv/bin/python -m benchmark.run --tags cut_in,night     # lọc theo tag; --ids <id1,id2> lọc theo id
.venv/bin/python -m benchmark.run --catalog <catalog.json> # thử trên dữ liệu CARLA khác (vd. catalog import từ máy bạn)
.venv/bin/python -m benchmark.run --url http://localhost:8100 --api-key $AGENT_API_KEY   # gọi Agent đang chạy
.venv/bin/python -m benchmark.run --min-pass-rate 0.6     # exit 1 nếu pass < 60% (dùng cho CI)
```

Kết quả nằm ở `benchmark/results/<thời điểm>/` (đã gitignore):

- `report.md`: bảng tổng quan, kết quả theo tag, từng lượt chạy kèm lý do trượt;
- `summary.json`: số liệu tổng hợp;
- `results.jsonl`: chi tiết từng lượt (IR tóm tắt, vị trí actor so với ego, cảnh báo).

## Chấm điểm

| Nhóm | Chỉ số | Đạt khi |
|---|---|---|
| Kỹ thuật | Sinh thành công | HTTP 200 |
| | Guardrail SOTIF | `validation.is_valid` |
| | XSD | `.xosc` hợp lệ OpenSCENARIO 1.0 |
| | Trên làn thật | mọi actor đặt trên làn thật hoặc mép đường, không phải ước lượng hình học |
| | Vị trí khớp IR | trong hệ toạ độ ego: đúng phía trái/phải (lệch ngang > 1.5 m), đúng hướng (cùng chiều ±35°, ngược chiều ≥145°, cắt ngang 50–130°), đúng khoảng cách (±max(4 m, 25%)) |
| Bám prompt | road_type, weather, ban đêm, tốc độ ego | nằm trong giá trị kỳ vọng |
| | Actor | đúng loại và **đúng số lượng**, có ít nhất một actor đúng vị trí và trigger kỳ vọng |
| | Không có actor thừa | không thêm actor ngoài yêu cầu (trừ ca `allow_extra_actors`) |
| Ca phủ định | Câu hỏi quy định | trả `422 NOT_A_SCENARIO_REQUEST` |
| Độ trễ | p50 / p95 / max | |

Một lượt **Pass** khi mọi chỉ số kỹ thuật đạt **và** bám prompt 100%.

## Thêm ca

Thêm vào `cases.json`. Mọi trường trong `expect` đều tuỳ chọn:

```json
{
  "id": "vi_my_case",
  "prompt": "…",
  "tags": ["vi", "cut_in"],
  "expect": {
    "road_types": ["intersection_4way"], "weather": ["rain"], "night": false, "ego_speed_kmh": [35, 45],
    "actors": [{"type": "motorcycle", "count": 1, "positions": ["ahead_adjacent_right"], "triggers": ["cut_in"]}],
    "allow_extra_actors": false
  }
}
```

Ca phủ định dùng `"expect": {"error_code": "NOT_A_SCENARIO_REQUEST"}`.

## Kết quả tham chiếu (2026-10-03, Town10HD_Opt, CARLA 0.9.16, 1 lượt mỗi ca)

| | Offline (quy tắc) | OpenAI gpt-4o-mini |
|---|---|---|
| Pass | 32% | 68% |
| Sinh OK / XSD / Vị trí khớp IR | 100% / 100% / 100% | 100% / 100% / 100% |
| Bám prompt trung bình | 67% | 91% |
| Có actor thừa | 5% | 20% |
| Độ trễ p50 / p95 | 0.1 s / 0.5 s | 9.1 s / 14.3 s |

Lý do trượt chủ yếu ở chế độ LLM: thêm xe ngoài yêu cầu (5/22), hiểu "cắt từ làn trái" thành `crossing_from_left`, bỏ sót trigger của actor thứ hai. Đây là kết quả của một lượt; LLM không tất định, nên muốn so sánh hai phiên bản thì dùng `--repeat 3` trở lên.
