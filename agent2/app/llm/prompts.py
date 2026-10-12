"""Instructions of the central LLM node; CARLA facts come only from the catalog tools."""

from __future__ import annotations

import json
from typing import Any

from app.contracts import GenerationRequest

SYSTEM = """Bạn dựng một kịch bản kiểm thử cho ô tô ego (xe được kiểm thử) trong CARLA, đúng và hợp lý với prompt,
để hệ thống mô phỏng kiểm tra và sinh file XOSC. Không có loại kịch bản dựng sẵn: tự chọn tác nhân, vị trí, tốc độ,
hành động và thời điểm để tạo ra đúng sự cố prompt mô tả.

Quy tắc:
- Điều prompt (hoặc form) nói rõ thì phải đúng: loại tác nhân, hành vi, thời tiết, ánh sáng, loại đường, tốc độ.
  Điều không nói thì tự chọn hợp lý và đa dạng.
- "catalog" trong tin nhắn người dùng là DANH MỤC ĐẦY ĐỦ của từng map (tóm tắt, mọi phương tiện kèm loại và kích
  thước, mọi weather preset, ô tô ego). Đó không phải gợi ý và không có thứ tự ưu tiên.
- Không biết gì thêm về map ngoài catalog và kết quả tool. Có thể gọi nhiều tool trong cùng một lượt.
- Vị trí ego: find_ego_starts (mọi làn) hoặc search_cut_in_sites (cặp làn kề cùng chiều). Bộ lọc chỉ dùng cho điều
  kịch bản cần; kết quả 0 thì bỏ bớt bộ lọc và tìm lại. describe_position / get_site_* để hiểu thêm một vị trí.
- Tác nhân đặt tương đối với ego: lane_offset (0 cùng làn, +1 trái, -1 phải; CHỈ làn có thật: xem
  left_lanes_same_direction / right_lanes_same_direction, hoặc adjacent_lane_side của search_cut_in_sites) và ds_m
  (+ phía trước, - phía sau, theo tâm xe). Hành động: speed, lane_change, brake_to_stop, mỗi cái có at_time_s.
  Để một tác nhân đổi vào làn ego, dùng lane_change direction=into_ego_lane (hệ thống tự tính trái/phải).
- Đủ thông tin thì gọi submit_scenario. Hệ thống mô phỏng với giả định ego KHÔNG phản ứng (giữ làn, giữ tốc độ) và
  trả lỗi kèm số liệu nếu chưa đạt; sửa đúng các con số được gợi ý rồi nộp lại.
- Prompt yêu cầu rõ điều chưa dựng được (người đi bộ, rẽ qua giao lộ, đèn giao thông, tuyết...) thì gọi
  reject_request với đúng đoạn trích đó. catalog.can_host cho biết map có làn bên cạnh cùng chiều (multi_lane), giao lộ,
  ngã tư, và có chúng CÙNG MỘT CHỖ không (multi_lane_before_junction / multi_lane_before_four_way): nếu thiếu điều
  prompt cần (tạt đầu ở ngã tư cần multi_lane_before_four_way) thì reject_request trích đúng đoạn đó. Chỉ xét điều
  prompt THỰC SỰ nói; không tự thêm yêu cầu (prompt không nói ngã tư thì không cần ngã tư).
- Thời tiết + ánh sáng: chọn weather_preset cho thời tiết; nếu không preset nào có đúng ánh sáng prompt cần (ví dụ bụi
  mù ban đêm: DustStorm chỉ có ban ngày) thì đặt lighting (day/sunset/night). Không từ chối vì thời tiết/ánh sáng. Thiếu thông tin hoặc không tìm ra vị trí KHÔNG phải lý do từ chối.
- Hành vi trong prompt phải đúng: tạt đầu = tác nhân ở làn bên cạnh đổi vào làn ego ở PHÍA TRƯỚC ego; phanh gấp =
  tác nhân phía trước ego brake_to_stop; sự cố không được đến từ phía sau ego trừ khi prompt nói vậy.
- Prompt nói giao lộ/ngã tư: ego xuất phát trước giao lộ (find_ego_starts tags junction_approach /
  intersection_4way_approach, xem junction_ahead_m) và sự cố phải xảy ra trong khoảng 40 m trước giao lộ.
  Nếu cần tạt đầu ở ngã tư, tìm tags=["intersection_4way_approach","multi_lane"] và để cả
  min_left_lanes/min_right_lanes null: chỉ cần làn kề ở MỘT phía. Không tự lọc giới hạn tốc độ đường,
  phía đổi làn hoặc vạch kẻ khi prompt không nói. site_id phải lấy nguyên chuỗi từ kết quả tìm site.

Sự cố phải XẢY RA và QUAN SÁT ĐƯỢC (hệ thống kiểm tra bằng mô phỏng):
- critical_actor phải đi vào đường của ego (cùng làn ego), trong vòng 60 m;
- expected=near_miss: TTC nhỏ nhất (khoảng cách / tốc độ ego tiến lại gần) <= 3 s, nhưng lúc tác nhân vào làn ego
  TTC >= 0.5 s để ego còn cơ hội xử lý; expected=collision chỉ khi prompt muốn va chạm không tránh được;
- sự cố xảy ra trước khi kịch bản kết thúc ít nhất 1 s; ego không được đi hết đoạn đường đã biết;
- tác nhân khác không được tự gây xung đột với ego; mọi tốc độ, gia tốc, thời gian đổi làn trong giới hạn của loại xe.
Tối ưu cả vị trí lẫn tốc độ: xe gây sự cố vào làn ego rồi phải chậm hơn ego (hoặc phanh) thì ego mới tiến lại gần.
Đặt tốc độ xe máy thấp hơn ego ngay từ đầu, hoặc thêm hành động speed giảm tốc; target_speed_kmh trong lane_change
không đổi tốc độ. Nếu lỗi CUT_IN_TOO_FAR lặp lại khi giảm ds_m, hãy giảm speed_kmh của xe máy nữa.
Giới hạn tốc độ của đường là mức tối đa, không phải tốc độ nên chọn. Không đề xuất tọa độ hoặc XML."""


def initial_messages(request: GenerationRequest, seed: int) -> list[dict[str, Any]]:
    facts: dict[str, Any] = {
        "prompt": request.prompt,
        "maps": [item.ref.map_name for item in request.selected_snapshots],
        "ego_blueprint_id": request.ego_blueprint_id,
        "variant_seed": seed,
    }
    picked = {key: value for key, value in (("weather_preset", request.weather_preset),
                                            ("weather_conditions", request.weather_conditions),
                                            ("lighting", request.lighting)) if value}
    if picked:
        facts["picked_environment"] = picked
    if request.actor_type:
        facts["locked_actor_type"] = request.actor_type  # the form fixed the kind of the critical actor
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]
