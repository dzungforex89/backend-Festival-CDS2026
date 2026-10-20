# ContractFlow Backend

MVP mô phỏng kiểm tra điều kiện hợp đồng cho Festival Chuyển đổi số 2026. Repo độc lập: FastAPI, SQLAlchemy 2, Alembic, JWT, PostgreSQL 16; có SQLite để demo local.

## Chạy local trên Windows

Yêu cầu Python 3.12+ (Docker dùng đúng 3.12).

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Đổi JWT_SECRET và DEMO_PASSWORD trong .env khi cần.
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m app.seed
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Swagger: http://localhost:8000/docs. `POST /auth/login` trả bearer token. Bấm Authorize và nhập token để thử API.

Các tài khoản demo dùng mật khẩu `DEMO_PASSWORD` trong `.env`:

| Username | Role | Phạm vi |
| --- | --- | --- |
| admin | ADMIN | Toàn bộ, đánh giá hàng loạt, xuất CSV |
| auditor | AUDITOR | Toàn bộ, đọc dữ liệu/audit và xuất báo cáo |
| buyer | BUYER | B009 |
| supplier | SUPPLIER | S021 |
| approver | APPROVER | A025, tổ chức B009 |

Đăng nhập demo mặc định: `ContractFlowDemo2026!`. Seed idempotent, không ghi đè dữ liệu đã tồn tại; thay `DEMO_PASSWORD` không thay mật khẩu tài khoản đã được seed.

## PostgreSQL / Docker

Thêm `POSTGRES_PASSWORD` vào `.env`, đặt JWT_SECRET ngẫu nhiên và DEMO_PASSWORD. Với mật khẩu PostgreSQL trong URL, dùng ký tự chữ/số hoặc URL-encode ký tự đặc biệt.

```powershell
docker compose up --build -d
```

Container tự chạy migration và import pack. PostgreSQL không mở cổng ra host; API ở cổng 8000. Dữ liệu được lưu trong volume `pgdata`. Nếu frontend đặt ở domain khác, bổ sung domain vào `CORS_ORIGINS` của dịch vụ API trong compose.

## Chức năng / API

- `GET /contracts`, `GET /milestones?contract_id=CT0001`: phạm vi theo tổ chức trong JWT, không tin role/actor từ client.
- `GET /milestones/{id}`: điều kiện, events, kết quả live, first-match trace và lịch sử đã lưu.
- `POST /milestones/{id}/events`: tự lấy actor từ phiên; kiểm quyền và reference dispute; tự đánh giá, lưu audit.
- `POST /milestones/{id}/evaluate`: lưu kết quả; fingerprint và unique constraint chống nhân bản khi retry/concurrent.
- `POST /evaluate-all`: ADMIN; lưu toàn bộ.
- `GET /rules`: 128 rule, chỉ đọc trong MVP; không chạy biểu thức bằng `eval`.
- `GET /parties`, `GET /audit`: ADMIN/AUDITOR.
- `GET /export/submission.csv`: giữ nguyên thứ tự, contract_id, milestone_id và 405 dòng; lưu audit của lần xuất.

Tạo event cần `event_type`, `document_id`, `idempotency_key`; optional `reference_event_id`, `event_note`. Gửi lại cùng key/nội dung trả event cũ; dùng cùng key với nội dung khác trả 409. Không có endpoint chuyển tiền.

## Rule engine & các giả định công khai

1. First-match theo priority: open dispute → rejection → unauthorized required event → thiếu event → thiếu quorum/vai trò → penalty → release → hold.
2. Rule phải đủ bộ 8, đúng expression được hỗ trợ, active và stop-processing. Cấu hình thiếu hoặc biểu thức chưa biết: HOLD.
3. Kiểm actor ACTIVE, party type và role, buyer/supplier đúng hợp đồng, approver thuộc buyer, có quyền và đủ hạn mức. Approval đếm actor duy nhất và đủ các role được yêu cầu. Rejection hợp lệ vẫn chặn milestone không yêu cầu approval.
4. `allowed_event_types` trong pack chỉ dùng từ vựng GOODS cho mọi loại hợp đồng. Mapping minh bạch: UAT/requirements → ACCEPTANCE; kickoff → CONTRACT_SIGNED; service activation/completion và go-live → DELIVERY; dispute opened/resolved → DISPUTE. Vẫn kiểm đúng BUYER/SUPPLIER của hợp đồng.
5. Duplicate: cùng event_type/document_id/actor/status/reference trong **5 phút**; giữ event sớm nhất. Không gộp event không có document_id. Không dùng duplicate để tăng quorum. Resolution tham chiếu duplicate của dispute cũng được chuẩn hóa.
6. Completion là ngày muộn nhất của required evidence hợp lệ và approvals được tính. Ngày trễ tính sau grace. Decimal, chia tỷ lệ cho 100, làm tròn HALF_UP hai chữ số; cap theo max%. HOLD/REJECT có payable=0.
7. **DATA_PACK_REPLAY mặc định**: xét toàn bộ event của pack, kể cả timestamp sau snapshot. Pack có các event năm 2027 và decision mẫu sử dụng chúng. Hàm Python `evaluate(..., as_of=datetime_aware)` hỗ trợ AS_OF, bỏ qua event tương lai so với cutoff. Không coi milestone không có nhãn là bình thường.
8. Datetime không có timezone trong pack được hiểu là UTC+7. Live event do server ghi UTC.
9. `confidence_score` là chỉ báo cấu hình có được hỗ trợ/đủ dữ liệu, không phải xác suất AI hay thước đo pháp lý.
10. API trả quyết định live khi đọc; chỉ thao tác lưu đánh giá, thêm event và xuất báo cáo mới ghi lịch sử. Cùng input trả audit cũ; thay đổi input tạo trace mới. Đây là mô phỏng, không có ledger thanh toán thực tế.
11. Mapping status: HOLD → ON_HOLD; REJECT → REJECTED; RELEASE → RELEASED; PENALTY → RELEASED_WITH_PENALTY. Hai trạng thái RELEASED là trạng thái mô phỏng đủ điều kiện, không xác nhận đã chuyển tiền.

## Kiểm thử và xuất bài nộp offline

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m app.export
```

`output/submission_output.csv`, `.audit.json`, `.report.json` được tạo hoàn toàn từ 5 CSV đầu vào, không đọc labels. Fixtures mẫu chỉ nằm trong `tests/fixtures`. Kết quả replay ban đầu: 348 RELEASE, 36 HOLD, 6 REJECT, 15 PENALTY; 20/20 mẫu khớp decision/reason/payable/penalty. 36 test gồm scope JWT, chống giả actor, idempotency, ưu tiên dispute, duplicate approval, sai trạng thái, cap/grace và cấu trúc CSV.

## Cấu trúc và giới hạn MVP

- `app/engine.py`: pure rule engine; `main.py`: API/RBAC; `seed.py`: import idempotent; `export.py`: replay offline.
- `migrations/`: Alembic schema version 0001; `data/`: pack độc lập; `tests/`: scenario và API tests.
- Lưu record có khóa kép kind/id, payload JSONB trên PostgreSQL, JSON trên SQLite; audit và user là bảng riêng, unique fingerprint. Đây là mô hình ingestion giữ nguyên pack; chưa chuẩn hóa thành các bảng hợp đồng/milestone có FK nghiệp vụ.
- Audit append-only qua API, chưa có trigger chống sửa bởi DBA, hash chain hoặc RLS PostgreSQL. API kiểm phạm vi ở tầng ứng dụng.
- Chưa có upload file thực (chỉ document_id), CRUD hợp đồng/rule/user, refresh/revoke JWT, rate limit login, email, real escrow hoặc AI trích điều khoản. Token hết hạn sau 2 giờ, lưu sessionStorage phía client. Không dùng cho production khi chưa bổ sung các kiểm soát này.
- Tài liệu chính thức: [FastAPI JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/), [SQLAlchemy 2](https://docs.sqlalchemy.org/en/20/orm/quickstart.html).
