# govardhan-electricals-backend

Pure-Python (standard library only) JSON API for the Govardhan Electricals site. Data is stored in Supabase.

## Setup

1. Supabase Dashboard -> **SQL Editor** -> paste and run [db.sql](db.sql) (creates `users`, `sessions`, `tokens`, `otp_records`, `enquiries`).
2. In [supabase.py](supabase.py) set `SECRET_KEY` to your Supabase **secret / service_role** key (Project Settings -> API Keys), or set the `SUPABASE_SERVICE_KEY` environment variable. The publishable key alone cannot access the private tables (Row Level Security).
3. Run:

```
python server.py
```

Serves on http://127.0.0.1:8000. Optional env vars: `HOST`, `PORT`, `ALLOWED_ORIGIN` (default `http://localhost:3000`), `OTP_TEMPLATE_URL`.

## Endpoints

| Method | Path                    | Body                                              | Notes                           |
| ------ | ----------------------- | ------------------------------------------------- | ------------------------------- |
| GET    | `/api/health`           | —                                                 |                                 |
| POST   | `/api/register`         | `name, number, username, password, country_code?` | returns `token` + `user`        |
| POST   | `/api/login`            | `username` (or `number`), `password`              | returns `token` + `user`        |
| GET    | `/api/me`               | —                                                 | `Authorization: Bearer <token>` |
| POST   | `/api/logout`           | —                                                 | `Authorization: Bearer <token>` |
| POST   | `/api/forgot-password`  | `identifier` (username or number)                 | sends a 6-digit OTP to the mobile |
| POST   | `/api/verify-otp`       | `identifier, otp`                                 | returns a 15-minute `reset_token` |
| POST   | `/api/reset-password`   | `reset_token, new_password`                       | logs the user out everywhere    |
| POST   | `/api/contact`          | `name, phone, message, email?`                    | saved to `enquiries`            |

OTP: 6 digits, valid 10 minutes, 5 attempts, 60 s between requests. Only hashes of OTPs and tokens are stored.
