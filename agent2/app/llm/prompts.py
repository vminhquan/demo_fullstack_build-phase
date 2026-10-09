"""Small, explicit LLM instructions; CARLA facts are supplied by the caller."""

from __future__ import annotations

import json

from app.cut_in.sample import SampledVariant

EXTRACT_SYSTEM = """Bạn trích ràng buộc cho DUY NHẤT tình huống xe máy tạt đầu ô tô.
Cùng tình huống này: tạt đầu, cắt đầu, cắt ngang đầu xe, chen/lấn vào làn phía trước ô tô, cut-in.
Chỉ ghi điều kiện được người dùng nói rõ. Thiếu thông tin thì để null hoặc danh sách rỗng; hệ thống sẽ chọn ngẫu nhiên sau.
location_tags chỉ dùng: straight, curve, junction, junction_approach, intersection_4way.
"ngã tư" phải là intersection_4way; junction không chứng minh là ngã tư.
weather_conditions chỉ dùng: sunny, rain, cloudy, wet, dust, fog. Các điều kiện trong danh sách phải cùng xuất hiện.
lighting chỉ dùng day, night, sunset; road_surface chỉ dùng dry, wet, slippery.
Tốc độ trả theo km/h. Nếu không rõ tốc độ thuộc xe nào hoặc đơn vị là gì, ghi vào ambiguities.
Thiếu thông tin KHÔNG phải ambiguity: không nêu tốc độ, khoảng cách, thời điểm, thời tiết hay mặt đường là bình thường.
ambiguities chỉ dành cho điều người dùng đã nói nhưng mâu thuẫn hoặc hiểu được theo nhiều cách.
Câu yêu cầu hệ thống tự chọn tham số không phải điều kiện; bỏ qua.
Nếu yêu cầu điều kiện ngoài miền hỗ trợ (ví dụ tuyết), ghi vào unsupported_requirements, không bỏ qua.
Chỉ ghi tình huống khác vào unsupported_requirements khi prompt NÊU RÕ tác nhân hoặc hành vi khác
(người đi bộ, xe đạp, xe tải tạt đầu, vượt đèn đỏ, va chạm từ phía sau...).
Prompt ngắn, không nêu loại xe hoặc chỉ nói "tạt đầu"/"cắt ngang đầu xe" vẫn là xe máy tạt đầu ô tô.
Đừng tự thêm điều kiện địa điểm, thời tiết, tốc độ hoặc tọa độ.
Ví dụ: "Xe máy tạt đầu ô tô trên đường", "tạt đầu", "xe máy cắt ngang đầu xe ô tô"
-> mọi trường null hoặc rỗng, ambiguities=[], unsupported_requirements=[].
Ví dụ: "Xe máy tạt đầu ô tô khi trời có tuyết" -> unsupported_requirements=[{"quote": "trời có tuyết", "reason": "tuyết ngoài miền hỗ trợ"}]."""


PROPOSE_SYSTEM = """Bạn chỉ đề xuất thao tác xe máy từ làn kề tạt vào phía trước ô tô.
Dữ liệu map, site, hai xe, tốc độ và môi trường trong input đã được hệ thống xác minh và khóa.
Chỉ trả bốn tham số: motorcycle_start_offset_m, trigger_time_s, lane_change_duration_s, desired_lead_gap_m.
motorcycle_start_offset_m là độ lệch dọc so với ô tô lúc bắt đầu; số dương là xe máy ở phía trước.
trigger_time_s >= 0; lane_change_duration_s > 0; desired_lead_gap_m > 0.
Chọn số phù hợp với available_length_m, hai tốc độ và mục tiêu xe máy nhập làn phía trước ô tô.
Hệ thống kiểm tra đúng các điều kiện sau (v = km/h / 3.6, t = trigger_time_s + lane_change_duration_s, L = available_length_m - 5):
0 <= motorcycle_start_offset_m <= L; lane_change_duration_s >= 1.6;
v_ô_tô * t <= L; motorcycle_start_offset_m + v_xe_máy * t <= L;
motorcycle_start_offset_m + (v_xe_máy - v_ô_tô) * t >= desired_lead_gap_m + 4.
Nếu xe máy chậm hơn ô tô, cho xe máy xuất phát phía trước đủ xa và trigger_time_s nhỏ.
Không đề xuất tọa độ, tên map, site, blueprint, thời tiết hoặc XOSC. Nếu có feedback kiểm tra, sửa bốn tham số theo feedback."""


EXTRACT_TOOLS = """Map đã chọn: {maps}.
Nếu prompt nêu địa điểm, loại đường hoặc tốc độ, có thể gọi search_cut_in_sites để xem map có đoạn đường như vậy không.
Kết quả tool chỉ để kiểm tra; vẫn chỉ ghi điều kiện người dùng nói rõ.
Nếu không map nào có đoạn đường thỏa yêu cầu người dùng nói rõ, ghi lý do vào unsupported_requirements."""

PROPOSE_TOOLS = {
    "get_site_context": "Gọi get_site_context để xem biển tốc độ, đèn, biển dừng, vạch qua đường và giao lộ trên hành lang.",
    "check_cut_in": "Gọi check_cut_in với bốn tham số dự định; chỉ trả lời khi valid, hoặc khi đã sửa theo lỗi tool báo.",
}


def extraction_messages(prompt: str, map_names: list[str] | None = None) -> list[dict[str, str]]:
    system = EXTRACT_SYSTEM if not map_names else f"{EXTRACT_SYSTEM}\n{EXTRACT_TOOLS.format(maps=', '.join(map_names))}"
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]


def proposal_messages(
    prompt: str, variant: SampledVariant, feedback: list[str] | None = None, *, tool_names: list[str] | None = None,
) -> list[dict[str, str]]:
    site, context = variant.site, variant.context
    facts = {
        "prompt": prompt,
        "map_name": site.snapshot.map_name,
        "site_id": site.site_id,
        "ego_lane": site.ego_lane.model_dump(),
        "motorcycle_lane": site.motorcycle_lane.model_dump(),
        "available_length_m": site.available_length_m,
        "ego_speed_kmh": context.ego_speed_kmh,
        "motorcycle_speed_kmh": context.motorcycle_speed_kmh,
        "weather_preset": context.environment.weather_preset,
        "road_surface": context.environment.road_surface,
        "feedback": feedback or [],
    }
    hints = [PROPOSE_TOOLS[name] for name in tool_names or [] if name in PROPOSE_TOOLS]
    return [{"role": "system", "content": "\n".join([PROPOSE_SYSTEM, *hints])},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False, sort_keys=True)}]
