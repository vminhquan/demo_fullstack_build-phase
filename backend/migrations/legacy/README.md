# Chuỗi migration cũ (chỉ để tham khảo)

Các file `0001`–`0007` ở đây là lịch sử trước khi gom thành `versions/0001_baseline.py`.
Alembic **không** đọc thư mục này. Chúng gọi `Base.metadata.create_all()` theo model hiện tại,
nên chạy lại hôm nay sẽ không ra đúng schema của thời điểm viết.

DB đang ở các revision này được chuyển sang baseline bởi `../legacy_bridge.py`.
