# govardhan-electricals-backend

Pure-Python (standard library only) JSON API for the Govardhan Electricals site. Data is stored in Supabase.

## Structure

```
server.py                    HTTP layer only: routing, request/response, CORS
services/
  auth_service.py            register, login, sessions, OTP password reset
  company_service.py         companies, and each user's membership/role in them
  contact_service.py         contact-form enquiries
supabase.py                  minimal Supabase (PostgREST) client
otp.py                       sends the "verification code" template webhook
security.py                  password hashing, tokens, time helpers
config.py                    env-driven settings (host, port, CORS, TTLs)
errors.py                    ApiError, shared by server.py and services/
env.py                       loads .env.local into os.environ (stdlib only, no .env needed in deployment)
.env.local                   your local Supabase credentials — git-ignored, create this yourself
db.sql                       full schema — run once in Supabase's SQL Editor for a fresh project
migrations/
  001_companies.sql            adds companies + company_members onto an existing database
  002_company_details.sql      adds gstin, owner_name, number, metadata to companies
  003_company_updated_by.sql   adds updated_by to companies
  004_invoicing.sql            adds inventory, invoice_series, invoice_config, invoices, invoice_items
```

## Setup

1. Supabase Dashboard -> **SQL Editor** -> paste and run [db.sql](db.sql) (creates `users`, `sessions`, `tokens`, `otp_records`, `companies`, `company_members`, `enquiries`). If you already ran an earlier version of this file, just run it again — every statement is `create table if not exists` / `add column if not exists`, so it only adds what's missing and won't touch existing data.
2. Create `.env.local` in this folder (git-ignored, never committed) with:
   ```
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
   SUPABASE_SECRET_KEY=sb_secret_...
   ```
   The secret/service_role key is required — the publishable key alone cannot write to the private tables (Row Level Security). Find both under Project Settings -> API Keys. `python server.py`'s startup line tells you which key type it's actually using.
3. Run:

```
python server.py
```

Serves on http://127.0.0.1:8000.

For deployment (Coolify, etc.), set the same variables as real environment variables instead of `.env.local` — [env.py](env.py) prefers real env vars and only falls back to the file, so both work without any code changes.

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
| POST   | `/api/companies`        | `name`                                             | creates a company; creator becomes its `owner`. `Authorization: Bearer <token>` |
| GET    | `/api/companies`        | —                                                   | lists the companies the current user belongs to, with their role. `Authorization: Bearer <token>` |
| POST   | `/api/companies/members`| `company_id, identifier, role?="member"`           | adds an existing user (by username or number) to a company; caller must be `owner`/`admin` of it. `Authorization: Bearer <token>` |

**Companies:** a user can belong to multiple companies, and a company can have multiple users — `company_members` is the many-to-many join table, with a `role` (`owner`/`admin`/`member`) per membership. The user who creates a company is automatically its `owner`.

OTP: 6 digits, valid 10 minutes, 5 attempts, 60 s between requests. Only hashes of OTPs and tokens are stored, never the raw values.

## Frontend

The Next.js frontend's [lib/api.js](../govardhan-electricals/lib/api.js) and [components/LoginForm.js](../govardhan-electricals/components/LoginForm.js) already call these exact paths — no frontend changes are needed after this refactor. Point it at this backend by setting `NEXT_PUBLIC_API_URL` (e.g. in `.env.local` for local dev, or your host's env vars for Vercel).
