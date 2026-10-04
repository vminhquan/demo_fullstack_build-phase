# Đề xuất dự án Scenario Forge RAV-03

## 1. Tóm tắt đề tài

Scenario Forge RAV-03 là công cụ hỗ trợ kiểm thử hệ thống hỗ trợ lái xe ADAS trong môi trường mô phỏng CARLA.

Thay vì chỉ tạo một tình huống giao thông rồi kiểm tra xe có va chạm hay không, hệ thống sẽ thay đổi có kiểm soát các tham số như vận tốc, khoảng cách và thời điểm xe máy cắt ngang. Mục tiêu là tìm ra ngưỡng mà tại đó hệ thống ADAS chuyển từ xử lý an toàn sang thất bại.

Ví dụ, với tình huống xe máy tạt đầu ô tô:

- Khoảng cách ban đầu 6,0 m: PASS.
- Khoảng cách ban đầu 5,9 m: PASS.
- Khoảng cách ban đầu 5,8 m: FAIL.

Kết quả hữu ích không chỉ là một lần va chạm, mà là khoảng chuyển tiếp đã được chạy lại và xác nhận, ví dụ: hệ thống bắt đầu có nguy cơ thất bại trong khoảng 5,8–5,9 m dưới đúng điều kiện kiểm thử đã công bố.

## 2. Vấn đề cần giải quyết

Khi kiểm thử ADAS bằng mô phỏng, kỹ sư thường gặp ba khó khăn:

1. Không gian tham số rất lớn. Chỉ một tình huống đã có nhiều biến như vận tốc xe ego, vận tốc xe máy, khoảng cách, góc cắt, thời tiết và tầm nhìn.
2. Chạy thử thủ công tốn thời gian. Kỹ sư phải sửa từng giá trị, chạy lại và tự so sánh kết quả.
3. Một tình huống FAIL đơn lẻ chưa cho biết hệ thống yếu ở mức nào, có tái hiện được hay không, và phiên bản phần mềm mới có cải thiện hay không.

Các nền tảng thương mại đã giải quyết bài toán này ở quy mô lớn nhưng thường phức tạp và đắt. Trong hệ sinh thái mã nguồn mở, CARLA và ScenarioRunner hỗ trợ chạy kịch bản nhưng vẫn cần thêm lớp quản lý tham số, tìm ngưỡng lỗi và lưu bằng chứng có thể tái hiện.

## 3. Giải pháp đề xuất

RAV-03 sẽ cung cấp một quy trình nhỏ, rõ ràng và có thể đo lường:

```text
Mô tả tình huống bằng ngôn ngữ tự nhiên
→ Kịch bản logic có cấu trúc
→ Kiểm tra quy tắc và tính hợp lệ
→ Tạo các cấu hình thử nghiệm
→ Chạy trong CARLA
→ Đánh giá PASS / FAIL
→ Thu hẹp vùng chuyển tiếp
→ Xuất báo cáo và cấu hình để chạy lại
```

Người dùng có thể nhập yêu cầu như:

> Xe máy tạt đầu xe ego tại giao lộ khi trời mưa nhẹ.

Hệ thống chuyển yêu cầu này thành một bản nháp có cấu trúc, ví dụ:

- Vận tốc xe ego: 20–40 km/h.
- Vận tốc xe máy: 10–30 km/h.
- Khoảng cách ban đầu: 3–12 m.
- Thời điểm bắt đầu cắt ngang: 1–3 giây.
- Mức mưa: một dải được CARLA hỗ trợ.

Các dải trên chỉ được sử dụng sau khi người dùng xem và xác nhận. Thông số nào chưa có dữ liệu thực tế sẽ được ghi rõ là giá trị thử nghiệm, không được trình bày như phân phối giao thông Việt Nam đã được chứng minh.

## 4. Điểm khác biệt chính

Điểm khác biệt của RAV-03 không phải là dùng LLM để tạo một cảnh CARLA. Nhiều công cụ và nghiên cứu đã làm được việc tương tự.

Điểm tập trung của dự án là:

> Tự động tìm và tái hiện cặp tình huống gần nhau nhất mà một trường hợp PASS và trường hợp còn lại FAIL.

Mỗi kết quả sẽ đi kèm:

- Bộ tham số chính xác.
- Phiên bản CARLA, ScenarioRunner và hệ thống được kiểm thử.
- Map, seed và bước thời gian mô phỏng.
- Chỉ số như khoảng cách nhỏ nhất, TTC và va chạm.
- Trạng thái PASS, FAIL, dữ liệu không hợp lệ hoặc lỗi hạ tầng.
- File cấu hình và bằng chứng để chạy lại.

Điều này giúp kỹ sư trả lời được câu hỏi thực tế hơn: “Hệ thống bắt đầu thất bại khi điều kiện thay đổi đến mức nào?”

## 5. Vì sao tập trung vào xe máy và giao thông Việt Nam

Giao thông đô thị Việt Nam có mật độ xe máy cao và nhiều tương tác hỗn hợp giữa ô tô, xe máy và người đi bộ. Đây là bối cảnh phù hợp để nghiên cứu các tình huống như xe máy cắt đầu, lọc làn và tương tác tại giao lộ.

Tuy nhiên, trong giai đoạn đầu, dự án không tuyên bố đã mô phỏng đầy đủ giao thông Việt Nam. Dữ liệu công khai tại Việt Nam như PHENIKAA và UIT-ADrone chủ yếu phù hợp với bài toán nhận thức hoặc phát hiện bất thường, chưa trực tiếp cung cấp đầy đủ phân phối hành vi để đưa vào mô phỏng.

Do đó, dự án sẽ thực hiện theo hai bước:

1. MVP dùng một tình huống xe máy cắt đầu được giới hạn rõ ràng trong CARLA.
2. Sau đó mới hiệu chỉnh dải tham số bằng dữ liệu quỹ đạo phù hợp hoặc dữ liệu thu thập cùng đối tác tại Việt Nam.

## 6. Phạm vi MVP

MVP đầu tiên chỉ gồm:

- Một nhóm tình huống: xe máy cắt vào phía trước xe ego.
- Một bản đồ CARLA.
- Một phiên bản CARLA và ScenarioRunner được cố định.
- Một hệ thống điều khiển xe ego cơ sở, được mô tả rõ là baseline thử nghiệm, không phải ADAS thương mại.
- Hai hoặc ba tham số có thể thay đổi, ưu tiên khoảng cách ban đầu, vận tốc ego và thời điểm cắt ngang.
- Một bộ quy tắc đánh giá gồm va chạm, khoảng cách nhỏ nhất và TTC.
- Thuật toán tìm kiếm đơn giản: chạy thăm dò, tìm một khoảng có cả PASS và FAIL, sau đó thu hẹp khoảng bằng binary search.
- Báo cáo kết quả và khả năng chạy lại đúng cấu hình.

## 7. Vai trò của AI và LLM

LLM chỉ hỗ trợ:

- Chuyển mô tả của người dùng thành bản nháp kịch bản có cấu trúc.
- Phát hiện thông tin còn mơ hồ để hỏi lại người dùng.
- Giải thích kết quả mô phỏng bằng ngôn ngữ dễ hiểu.

LLM không được tự quyết định:

- Kịch bản có hợp lệ về hình học hay không.
- Tham số nào là an toàn.
- Kết quả PASS hay FAIL.
- Mã lệnh nào được thực thi trong CARLA.

Các phần này phải được xử lý bằng schema, quy tắc và thuật toán xác định để kết quả có thể kiểm tra và tái hiện.

## 8. Cách đánh giá dự án

Dự án sẽ được đánh giá bằng các chỉ số thực tế:

- Tỷ lệ kịch bản hợp lệ sau bước kiểm tra.
- Tỷ lệ lượt chạy CARLA hoàn tất thành công.
- Khả năng tái hiện cùng kết quả khi chạy lại cùng cấu hình.
- Số lượt mô phỏng cần để tìm được một khoảng PASS/FAIL.
- Độ rộng của khoảng chuyển tiếp tìm được.
- Hiệu quả so với tìm kiếm ngẫu nhiên khi dùng cùng số lượt chạy.
- Tỷ lệ kết quả có đầy đủ cấu hình, log và bằng chứng.
- Số trường cần con người sửa sau khi LLM tạo bản nháp.

## 9. Kết quả mong đợi của MVP

Sau MVP, người dùng có thể:

1. Nhập hoặc chọn một tình huống xe máy cắt đầu.
2. Xem và sửa các tham số trước khi chạy.
3. Chạy một chiến dịch kiểm thử với số lượt giới hạn.
4. Nhận một cặp kết quả PASS/FAIL gần nhau.
5. Xem chỉ số và bằng chứng của hai lượt chạy.
6. Chạy lại cùng cấu hình để xác nhận.
7. So sánh ngưỡng quan sát được giữa hai phiên bản hệ thống điều khiển.

## 10. Rủi ro chính

### Rủi ro kỹ thuật

CARLA hoặc hệ thống điều khiển có thể cho kết quả không hoàn toàn ổn định. Quan hệ giữa một tham số và PASS/FAIL cũng có thể không đơn điệu, khiến binary search không luôn phù hợp.

Giải pháp là chạy thăm dò trước, cố định timestep và seed, lặp lại các điểm gần vùng chuyển tiếp, đồng thời báo cáo một khoảng quan sát thay vì khẳng định một ngưỡng tuyệt đối.

### Rủi ro sản phẩm

Dự án có thể trở thành một demo kỹ thuật tốt nhưng chưa giải quyết đúng quy trình của kỹ sư ADAS thực tế.

Giải pháp là xin phản hồi sớm từ senior hoặc kỹ sư mô phỏng về ba nội dung: tình huống ưu tiên, cách định nghĩa PASS/FAIL và định dạng bằng chứng họ thực sự cần.

### Rủi ro dữ liệu

Chưa có nguồn dữ liệu quỹ đạo Việt Nam đã được xác minh đầy đủ cho các phân phối như khoảng cách cắt đầu hoặc hành vi lọc làn.

Giải pháp là ghi rõ nguồn và trạng thái của từng tham số, không tạo số liệu không có căn cứ, và xem hợp tác dữ liệu là bước phát triển tiếp theo.

## 11. Những nội dung chưa làm trong giai đoạn đầu

- Chuyển video tai nạn thành kịch bản CARLA tự động.
- Xây dựng bản sao số của toàn bộ giao thông Việt Nam.
- Kiểm thử perception ở mức camera hoặc LiDAR.
- Hỗ trợ nhiều simulator cùng lúc.
- Dùng reinforcement learning hoặc mô hình tối ưu phức tạp.
- Xây dựng hệ thống chứng nhận an toàn.
- Khẳng định kết quả của baseline CARLA đại diện cho ADAS thương mại.

## 12. Kế hoạch triển khai ngắn hạn

### Hai tuần đầu

- Chốt một map, một baseline, một tình huống và bộ quy tắc PASS/FAIL.
- Xây dựng schema kịch bản và bộ kiểm tra deterministic.
- Kết nối một lượt chạy CARLA thực.
- Thu thập chỉ số và lưu cấu hình chạy lại.
- Cài đặt tìm kiếm thăm dò và binary search.
- Thực hiện một thử nghiệm hoàn chỉnh và so sánh với random search.

### Hai tuần tiếp theo

- Thêm LLM parser có bước con người xác nhận.
- Thử xuất một tập con OpenSCENARIO được ScenarioRunner hỗ trợ.
- Kiểm tra độ ổn định bằng các lượt chạy lặp.
- So sánh kết quả giữa hai cấu hình hoặc phiên bản SUT.
- Thử phương pháp trích xuất tham số từ một tập dữ liệu quỹ đạo nhỏ có giấy phép phù hợp.

## 13. Đề nghị senior góp ý

Nhóm mong muốn nhận góp ý cho các quyết định sau:

1. Tình huống motorcycle cut-in có đủ giá trị để làm use case đầu tiên không?
2. Nên chọn baseline nào để kết quả có ý nghĩa nhưng vẫn khả thi trong CARLA?
3. Bộ oracle tối thiểu nên ưu tiên collision, minimum distance, TTC hay thêm chỉ số nào khác?
4. Việc báo cáo một PASS/FAIL bracket có hữu ích hơn một tập các tình huống FAIL riêng lẻ không?
5. Để kết quả có giá trị với kỹ sư ADAS, evidence bundle còn thiếu thông tin gì?
6. Nguồn dữ liệu hoặc chuyên gia nào có thể giúp hiệu chỉnh hành vi xe máy trong giao thông Việt Nam?

## 14. Kết luận

Scenario Forge RAV-03 được đề xuất như một công cụ kiểm thử hẹp nhưng có thể đo lường: từ một tình huống xe máy cắt đầu đã được xác nhận, hệ thống tự động tìm vùng chuyển tiếp giữa PASS và FAIL trong CARLA, đồng thời lưu đủ bằng chứng để chạy lại và so sánh phiên bản.

Hướng tiếp cận này nhỏ hơn một nền tảng mô phỏng tổng quát nhưng phù hợp hơn với năng lực hiện tại của nhóm, tạo được demo rõ ràng và có nền tảng để phát triển thành công cụ kiểm thử hồi quy hoặc nghiên cứu giao thông hỗn hợp trong tương lai.
