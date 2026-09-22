# govardhan-electricals-backend

Pure-Python (standard library only) JSON API for the Govardhan Electricals site. Data is stored in Supabase.

## Structure

```
server.py                    HTTP layer only: routing, request/response, CORS
services/
  auth_service.py            register, login, sessions, OTP password reset
  contact_service.py         contact-form enquiries
supabase.py                  minimal Supabase (PostgREST) client
otp.py                       sends the "verification code" template webhook
security.py                  password hashing, tokens, time helpers
config.py                    env-driven settings (host, port, CORS, TTLs)
errors.py                    ApiError, shared by server.py and services/
db.sql                       run once in Supabase's SQL Editor
```

## Setup

1. Supabase Dashboard -> **SQL Editor** -> paste and run [db.sql](db.sql) (creates `users`, `sessions`, `tokens`, `otp_records`, `enquiries`).
2. In [supabase.py](supabase.py) set `SECRET_KEY` to your Supabase **secret / service_role** key (Project Settings -> API Keys), or set the `SUPABASE_SERVICE_KEY` environment variable. The publishable key alone cannot write to the private tables (Row Level Security) — `python server.py`'s startup line tells you which key type it's using.
3. Run:

```
python server.py
```

Serves on http://127.0.0.1:8000.

## Environment variables

| Var                    | Default                                          | Notes                                                        |
| ----------------------- | ------------------------------------------------ | -------------------------------------------------------------- |
| `HOST`                 | `127.0.0.1`                                      | set to `0.0.0.0` in Docker (the Dockerfile already does this)  |
| `PORT`                 | `8000`                                           |                                                                  |
| `ALLOWED_ORIGINS`      | `http://localhost:3000,http://127.0.0.1:3000`    | comma-separated browser origins allowed to call the API. An entry starting with `*.` matches any subdomain, e.g. `*.vercel.app` for Vercel previews. `ALLOWED_ORIGIN` (singular) still works for one origin. |
| `SUPABASE_SERVICE_KEY` | —                                                 | overrides `SECRET_KEY` in `supabase.py`                        |
| `OTP_TEMPLATE_URL`     | the campaign webhook already in `otp.py`         |                                                                  |

For a frontend hosted on Vercel, the API must be served over **HTTPS** — browsers block an HTTPS page from calling an `http://` endpoint (mixed content). Enable HTTPS on whatever host serves this backend, then set `ALLOWED_ORIGINS` to your Vercel domain(s), e.g. `https://your-app.vercel.app,*.vercel.app`.

## Endpoints

| Method | Path                    | Body                                              | Notes                            |
| ------ | ----------------------- | -------------------------------------------------- | --------------------------------- |
| GET    | `/api/health`           | —                                                  |                                   |
| POST   | `/api/register`         | `name, number, username, password, country_code?`  | returns `token` + `user`         |
| POST   | `/api/login`            | `username` (or `number`), `password`               | returns `token` + `user`         |
| GET    | `/api/me`               | —                                                  | `Authorization: Bearer <token>`  |
| POST   | `/api/logout`           | —                                                  | `Authorization: Bearer <token>`  |
| POST   | `/api/forgot-password`  | `identifier` (username or number)                  | sends a 6-digit OTP to the mobile |
| POST   | `/api/verify-otp`       | `identifier, otp`                                  | returns a 15-minute `reset_token` |
| POST   | `/api/reset-password`   | `reset_token, new_password`                        | logs the user out everywhere     |
| POST   | `/api/contact`          | `name, phone, message, email?`                     | saved to `enquiries`             |

OTP: 6 digits, valid 10 minutes, 5 attempts, 60 s between requests. Only hashes of OTPs and tokens are stored, never the raw values.

## Frontend

The Next.js frontend's [lib/api.js](../govardhan-electricals/lib/api.js) and [components/LoginForm.js](../govardhan-electricals/components/LoginForm.js) already call these exact paths — no frontend changes are needed after this refactor. Point it at this backend by setting `NEXT_PUBLIC_API_URL` (e.g. in `.env.local` for local dev, or your host's env vars for Vercel).
