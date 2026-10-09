# Log triển khai bước 2: Tìm vị trí xe máy tạt đầu ô tô

## Mục tiêu

Bước 2 tạo nguồn site có kiểm tra topology khi Bridge đang kết nối CARLA, rồi để `agent2` lọc site theo các snapshot map người dùng chọn. Không suy ra hai làn kề nhau chỉ từ tọa độ waypoint trong catalog cũ.

## Thay đổi đã thực hiện

| Nơi thay đổi | Nội dung |
| --- | --- |
| `bridge/scenario_forge_bridge/cut_in_sites.py` | Tìm cặp làn Driving kề nhau, cùng chiều; theo dõi hai làn qua đoạn đường liên tục tối thiểu 60 m và từ chối đoạn rẽ nhánh, đứt quãng hoặc khác cao độ. Site có hai làn, hai pose mốc, chiều dài, nhãn vị trí và `verification_source=carla_topology`. |
| `bridge/scenario_forge_bridge/carla_catalog.py` | Xuất `cut_in_sites` cùng mỗi catalog khi sync. File này vẫn chạy trực tiếp được bằng Python của ScenarioRunner. |
| `bridge/scripts/build.sh`, `bridge/scripts/build.ps1` | Đóng gói thêm module tìm site để bản CLI build trên Linux/Windows có thể chạy file sync trong môi trường ScenarioRunner. |
| `backend/app/modules/catalog/contract.py`, `agent/app/contracts/catalog_v1.py`, `agent2/app/catalog/models.py` | Bổ sung cùng cấu trúc `CatalogCutInSite` vào hợp đồng `catalog.v1`. `cut_in_sites=None` nghĩa là catalog cũ chưa có chỉ mục; `[]` nghĩa là Bridge đã kiểm tra nhưng không thấy site phù hợp. |
| `backend/app/modules/catalog/service.py` | Giữ nguyên content hash cho catalog cũ thiếu `cut_in_sites`; catalog có chỉ mục site mới nhận hash theo nội dung mới. |
| `agent2/app/catalog/find_sites.py` | `find_cut_in_sites(selected_snapshots, location_constraints)` chỉ lấy site đã có trong snapshot được chọn, lọc tất cả nhãn địa điểm yêu cầu và chiều dài tối thiểu 60 m; trả site gắn với snapshot cùng lỗi cụ thể theo map. |

## Quy tắc tìm và lọc

Bridge kiểm tra làn xe máy và làn ô tô là hai làn Driving khác nhau trên cùng road/section, cùng hướng theo dấu lane ID và góc yaw, kề nhau theo CARLA waypoint API. Bridge đi tiếp từng 5 m trên cả hai làn, yêu cầu mỗi bước có đúng một nhánh và cặp làn vẫn kề nhau. Khi đạt ít nhất 60 m, Bridge ghi site vào catalog. Các site gần nhau trên cùng cặp làn được giảm mật độ theo khoảng 20 m.

`agent2` không truy cập CARLA trong bước lọc. Nếu người dùng không nói địa điểm, tất cả site hợp lệ trên các map được chọn đều có thể được lấy để bước chọn ngẫu nhiên dùng sau này. Nếu có nhãn địa điểm, site phải có đủ các nhãn đó. Nhãn `junction` chỉ biểu thị nút giao CARLA nói chung; nó **không** được coi là `intersection_4way` (ngã tư). Hiện Bridge chưa phân loại số nhánh của nút giao, nên yêu cầu ngã tư sẽ không được giả vờ thỏa mãn bằng nhãn `junction`.

Mỗi map có thể trả một trong bốn mã lỗi: `SITE_INDEX_MISSING` (catalog cũ, cần sync lại), `NO_VERIFIED_SITES` (đã kiểm tra nhưng không thấy site), `LOCATION_NOT_AVAILABLE` (không khớp địa điểm), `INSUFFICIENT_SITE_LENGTH` (đúng địa điểm nhưng đoạn đường quá ngắn). Map khác vẫn có thể có site và tiếp tục được dùng.

## Kiểm tra

- `agent2/tests/test_find_sites.py`: 4 test qua. Kiểm tra nhiều map, gắn snapshot, phân biệt catalog cũ với chỉ mục rỗng, không nhận nhầm ngã tư và lọc chiều dài.
- `bridge/tests/test_cut_in_sites.py` cùng `bridge/tests/test_carla_catalog.py`: 11 test qua. Kiểm tra đoạn đường hợp lệ, ngược chiều, rẽ nhánh, quá ngắn, đứt quãng và chạy file sync trực tiếp bằng Python riêng.
- `backend/tests/unit/test_catalog_and_generation.py`: 31 test qua. Bao gồm kiểm tra hash của catalog cũ và catalog có chỉ mục mới.
- `ruff check` cho các file Bridge đã sửa và `git diff --check`: qua.

Các test Bridge dùng CARLA giả lập. Chưa chạy sync với CARLA thật trên Windows, chưa kiểm chứng site trong ScenarioRunner và chưa có bộ sinh XOSC ở bước 2. Khi không có site hợp lệ, hệ thống trả lý do theo map thay vì tạo tọa độ hoặc xác nhận khả năng chạy chưa được kiểm tra.
