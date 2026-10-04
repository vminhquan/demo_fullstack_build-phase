# ĐỀ XUẤT DỰ ÁN: SCENARIO FORGE RAV-03

## 1. Tóm tắt đề xuất

Scenario Forge RAV-03 là công cụ hỗ trợ kiểm thử hệ thống hỗ trợ lái xe (ADAS) trong môi trường mô phỏng CARLA. Dự án tập trung vào các tình huống tương tác giữa ô tô và xe máy, trước mắt là tình huống xe máy cắt vào phía trước xe ego.

Thay vì chỉ chạy một kịch bản và ghi nhận có va chạm hay không, RAV-03 sẽ tự động thay đổi một số điều kiện như vận tốc, khoảng cách ban đầu và thời điểm cắt ngang. Mục tiêu là tìm được vùng mà kết quả chuyển từ PASS sang FAIL, sau đó lưu đầy đủ cấu hình và bằng chứng để chạy lại.

Đầu ra quan trọng nhất là một cặp tình huống gần nhau: một trường hợp hệ thống xử lý được và một trường hợp thất bại. Kết quả này giúp nhóm thấy giới hạn quan sát được của hệ thống, thay vì chỉ thu thập các trường hợp lỗi rời rạc.

## 2. Vì sao dự án cần thiết

### Tai nạn giao thông vẫn là vấn đề lớn

Theo Tổng cục Thống kê, trong năm 2024 Việt Nam ghi nhận 23.484 vụ tai nạn giao thông, làm 10.944 người chết và 17.342 người bị thương. Trung bình mỗi ngày có 64 vụ, 30 người chết và 47 người bị thương [1]. Các số liệu này cho thấy nhu cầu cải thiện an toàn giao thông vẫn rất rõ ràng.

WHO đưa ra một góc nhìn khác dựa trên phương pháp ước tính quốc tế. Hồ sơ Việt Nam trong Global Status Report on Road Safety 2023 ghi nhận 5.699 ca tử vong được báo cáo trong năm 2021, trong khi ước tính của WHO là 17.229 ca, tương đương 17,7 ca trên 100.000 dân [2]. Hai con số sử dụng phương pháp và phạm vi khác nhau, vì vậy không nên so sánh trực tiếp với số liệu năm 2024. Tuy nhiên, chênh lệch này cho thấy mọi kết quả về an toàn đều cần ghi rõ nguồn, năm và phương pháp đo.

### Xe hai bánh là đối tượng đặc biệt quan trọng tại Việt Nam

Hồ sơ WHO cho Việt Nam cho biết người sử dụng phương tiện cơ giới hai hoặc ba bánh chiếm 57% số ca tử vong giao thông được báo cáo trong năm 2021. Cùng hồ sơ này ghi nhận 75.353.485 phương tiện đã đăng ký tại Việt Nam trong năm 2021 [2].

Một hướng dẫn an toàn xe hai và ba bánh của WHO ghi nhận rằng năm 2015 Việt Nam có hơn 44 triệu xe máy, chiếm khoảng 94% tổng số phương tiện đăng ký tại thời điểm đó [3]. Đây là số liệu lịch sử, không đại diện cho cơ cấu phương tiện hiện nay, nhưng cho thấy xe máy đã giữ vai trò rất lớn trong hệ thống giao thông Việt Nam trong thời gian dài.

Trên phạm vi toàn cầu, WHO ước tính tai nạn giao thông gây khoảng 1,19 triệu ca tử vong mỗi năm; 56% người tử vong là nhóm tham gia giao thông dễ bị tổn thương, gồm người đi bộ, người đi xe đạp và người sử dụng xe hai hoặc ba bánh [4].

Các số liệu trên không chứng minh rằng một công cụ mô phỏng có thể trực tiếp làm giảm tai nạn. Chúng cho thấy việc đánh giá cách hệ thống hỗ trợ lái phản ứng trước xe máy là một bài toán có tính thực tế và phù hợp với bối cảnh Việt Nam.

## 3. Khoảng trống mà dự án muốn giải quyết

CARLA là nền tảng mô phỏng mã nguồn mở được xây dựng để hỗ trợ nghiên cứu phát triển và kiểm chứng hệ thống lái tự động [5]. Tuy nhiên, có simulator không đồng nghĩa với việc đã có một quy trình kiểm thử hoàn chỉnh.

Trong thực tế, một tình huống xe máy cắt đầu có thể thay đổi theo nhiều yếu tố: vận tốc ô tô, vận tốc xe máy, khoảng cách, góc tiếp cận, thời điểm bắt đầu chuyển hướng và điều kiện môi trường. Nếu kỹ sư thay đổi từng giá trị bằng tay, số lượt chạy tăng nhanh và kết quả khó được tổ chức nhất quán.

Một kết quả FAIL đơn lẻ cũng chưa trả lời được các câu hỏi quan trọng:

- Điều kiện thay đổi đến mức nào thì hệ thống bắt đầu thất bại?
- Kết quả đó có lặp lại khi chạy cùng cấu hình hay không?
- Lỗi thuộc về hệ thống được kiểm thử, kịch bản không hợp lệ hay hạ tầng mô phỏng?
- Phiên bản phần mềm mới có cải thiện vùng hoạt động hay không?

RAV-03 được đề xuất để bổ sung lớp quản lý kiểm thử này trên CARLA: tạo các biến thể có kiểm soát, tìm vùng PASS/FAIL, phân loại kết quả và lưu bằng chứng tái hiện.

## 4. Ý tưởng của RAV-03

Người dùng chọn một mẫu tình huống hoặc mô tả ngắn, ví dụ: “Xe máy cắt vào trước xe ego tại giao lộ”. Sau khi người dùng xác nhận các tham số, hệ thống thực hiện quy trình sau:

```text
Mô tả tình huống
-> Kiểm tra tham số và tính hợp lệ
-> Tạo các cấu hình thử nghiệm
-> Chạy trong CARLA
-> Thu thập va chạm, khoảng cách và TTC
-> Phân loại PASS / FAIL / INVALID / INFRA_ERROR
-> Tìm và xác nhận vùng chuyển tiếp PASS/FAIL
-> Xuất báo cáo cùng cấu hình chạy lại
```

Ví dụ, nếu khoảng cách ban đầu 5,9 m cho kết quả PASS và 5,8 m cho kết quả FAIL trong cùng điều kiện, hệ thống báo cáo khoảng chuyển tiếp 5,8-5,9 m. Đây chỉ là kết quả quan sát được trong cấu hình đã công bố, không phải “ngưỡng an toàn tuyệt đối”.

Điểm khác biệt của dự án không nằm ở việc dùng AI để sinh một cảnh mô phỏng. Trọng tâm là tạo được kết quả có thể đo lường, truy vết và tái hiện.

## 5. Phạm vi MVP

Để bảo đảm tính khả thi, MVP chỉ gồm:

- Một tình huống chính: motorcycle cut-in.
- Một bản đồ và một phiên bản CARLA cố định.
- Một hệ thống điều khiển xe ego cơ sở để thử nghiệm.
- Ba tham số ưu tiên: vận tốc ego, khoảng cách ban đầu và thời điểm cắt ngang.
- Ba chỉ số chính: collision, minimum distance và time-to-collision (TTC).
- Một phương pháp tìm kiếm đơn giản: chạy thăm dò, tìm vùng có cả PASS và FAIL, sau đó thu hẹp vùng này.
- Báo cáo kết quả và bộ cấu hình để chạy lại.

MVP chưa nhằm mô phỏng toàn bộ giao thông Việt Nam, chưa kiểm thử perception ở mức camera/LiDAR và chưa đại diện cho quy trình chứng nhận an toàn. Baseline trong CARLA cũng không được trình bày như một hệ thống ADAS thương mại.

## 6. Vai trò của AI

AI/LLM chỉ đóng vai trò hỗ trợ:

- Chuyển mô tả của người dùng thành bản nháp kịch bản có cấu trúc.
- Chỉ ra thông tin còn thiếu hoặc mơ hồ để người dùng xác nhận.
- Giải thích kết quả bằng ngôn ngữ dễ hiểu.

LLM không tự quyết định kịch bản hợp lệ, không tự đặt tiêu chuẩn an toàn và không trực tiếp quyết định PASS/FAIL. Các bước này phải dựa trên schema, quy tắc và chỉ số được xác định trước. Cách phân vai này giúp giảm rủi ro AI tạo thông tin không có căn cứ.

## 7. Kết quả và tiêu chí đánh giá

MVP được xem là đạt khi có thể chạy trọn vẹn một chiến dịch motorcycle cut-in và tạo ra:

- Ít nhất một cặp PASS/FAIL gần nhau trong ngân sách mô phỏng đã đặt.
- Bộ tham số chính xác của từng lượt chạy.
- Phiên bản phần mềm, map, seed và timestep.
- Các chỉ số collision, minimum distance và TTC.
- Trạng thái riêng cho lỗi kịch bản và lỗi hạ tầng.
- File cấu hình đủ để chạy lại hai trường hợp ở biên.

Các chỉ số đánh giá gồm số lượt chạy cần thiết để tìm vùng chuyển tiếp, độ rộng vùng PASS/FAIL, tỷ lệ lượt chạy hoàn tất, tỷ lệ tái hiện cùng kết quả và thời gian của toàn chiến dịch. Hiệu quả tìm kiếm sẽ được so sánh với random search trong cùng ngân sách lượt chạy.

## 8. Giá trị dự kiến

Đối với nhóm phát triển, RAV-03 tạo ra một demo nhỏ nhưng có kết quả định lượng rõ ràng. Đối với kỹ sư kiểm thử, công cụ giúp giảm thao tác thay đổi tham số thủ công và chuẩn hóa bằng chứng. Đối với hướng nghiên cứu, kết quả có thể mở rộng thành kiểm thử hồi quy, so sánh phiên bản hoặc hiệu chỉnh tình huống bằng dữ liệu quỹ đạo thực tế.

Việc tập trung vào xe máy tạo điểm gắn kết với bối cảnh giao thông Việt Nam. Tuy nhiên, trong MVP, các dải tham số chỉ được xem là giá trị thử nghiệm. Nhóm chỉ tuyên bố đại diện cho hành vi giao thông thực khi có dữ liệu quỹ đạo phù hợp và phương pháp hiệu chỉnh được kiểm chứng.

## 9. Rủi ro và cách kiểm soát

Kết quả CARLA có thể không hoàn toàn ổn định. Nhóm sẽ cố định seed và timestep, đồng thời chạy lặp lại các điểm gần vùng chuyển tiếp. Nếu kết quả thay đổi, báo cáo sẽ thể hiện tỷ lệ PASS/FAIL thay vì chọn một kết quả duy nhất.

Quan hệ giữa một tham số và kết quả có thể không đơn điệu. Khi đó, hệ thống không tiếp tục thu hẹp theo giả định sai mà chuyển sang lấy mẫu nhiều điểm trong vùng nghi ngờ.

Rủi ro lớn nhất về nội dung là trình bày quá mức giá trị của mô phỏng. Vì vậy, báo cáo luôn ghi rõ phiên bản, phạm vi, giả định, nguồn dữ liệu và giới hạn áp dụng.

## 10. Đề nghị senior góp ý

Nhóm mong muốn nhận góp ý cho bốn quyết định:

1. Motorcycle cut-in có phù hợp làm tình huống đầu tiên không?
2. Baseline nào trong CARLA đủ ổn định và có ý nghĩa cho MVP?
3. Collision, minimum distance và TTC có đủ làm bộ chỉ số ban đầu không?
4. Evidence bundle cần thêm thông tin nào để phù hợp với quy trình kiểm thử ADAS thực tế?

## 11. Tài liệu tham khảo

[1] Tổng cục Thống kê. “Báo cáo tình hình kinh tế - xã hội quý IV và năm 2024”, mục Tai nạn giao thông. Công bố tháng 01/2025. https://www.nso.gov.vn/bai-top/2025/01/bao-cao-tinh-hinh-kinh-te-xa-hoi-quy-iv-va-nam-2024/

[2] World Health Organization. “Road Safety Viet Nam 2023 Country Profile”, dữ liệu năm 2021; công bố ngày 30/04/2024. https://www.who.int/publications/m/item/road-safety-vnm-2023-country-profile

[3] World Health Organization. “Powered two- and three-wheeler safety: a road safety manual for decision-makers and practitioners”, Box 3.4: Motorcycle safety in Viet Nam, dữ liệu năm 2015. Geneva: WHO, 2017. ISBN 978-92-4-151192-6. https://iris.who.int/handle/10665/254759

[4] World Health Organization. “Global status report on road safety 2023”. Geneva: WHO, 2023. ISBN 978-92-4-008651-7. https://www.who.int/teams/social-determinants-of-health/safety-and-mobility/global-status-report-on-road-safety-2023

[5] Dosovitskiy, A.; Ros, G.; Codevilla, F.; Lopez, A.; Koltun, V. “CARLA: An Open Urban Driving Simulator”. Proceedings of the 1st Annual Conference on Robot Learning, PMLR 78, trang 1-16, 2017. https://proceedings.mlr.press/v78/dosovitskiy17a.html
