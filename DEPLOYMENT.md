# StudyFlow on Vercel + Supabase

Cloud integration is implemented; a live deployment is not complete until the checks below pass.
Do not treat offline mocks as proof that a real database has RLS enabled.

## 1. Supabase (new project)

1. Create a project in your own account. Choose a suitable region and store the database password in your password manager, not in Git or chat. Stay on a free plan unless you decide otherwise.
2. Execute `supabase/schema.sql` once in the SQL Editor. It creates a dedicated table, constraints, and owner-only row-level policies. It intentionally fails if that table already exists, instead of overwriting data.
3. This deployment intentionally uses email/password without email verification, explicitly approved by the owner. In Authentication → Sign In / Providers disable **Confirm email**, keep **Email** enabled, and keep anonymous sign-ins and manual identity linking disabled. A Supabase `email_confirmed_at` value is automatically assigned in this mode and does not establish mailbox ownership. Do not enable social login or merge identities by email without a separate verification/migration design.
4. Set minimum password length to 15 and require the current password for updates in the Email provider. Keep provider Auth rate limits enabled. No SMTP is required for these password-only signup/login routes. Confirmation, magic links and password-recovery mail are not offered; do not promise account recovery based solely on an unverified email address.
5. Get the project URL and **publishable** key (or legacy **anon** key). This app deliberately rejects a service-role/secret key. The service-role key bypasses RLS and must not be used.
6. Set the Auth Site URL to the final HTTPS Vercel URL. No wildcard redirect URLs are needed for password login.

StudyFlow forwards passwords only to Supabase over HTTPS; Supabase manages password hashing and auth sessions. Passwords never enter the application database, cookies, templates or logs. There is no self-service recovery in this version. Anyone can register an unused address they do not own, so treat it only as a login identifier.

## 2. Local cloud smoke test

Copy `.env.example` to `.env` and set `STUDYFLOW_MODE=cloud`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and a random `STUDYFLOW_SECRET_KEY` (at least 32 characters). Generate it locally, never paste it into a public issue or chat. Restart `app.py`.

Without cloud configuration the local app remains in clearly labeled, loopback-only SQLite mode. Existing SQLite tasks are never automatically attached to the first person who signs in.

## 3. Vercel

1. Import `arystank1511-blip/studyflow` from GitHub. Give the Vercel integration access to this repository only where possible.
2. The repository root contains `app.py` and `vercel.json`; choose Flask. Use Python 3.12 (see `pyproject.toml`). Build command: `python build.py`. Leave Output Directory at the framework default. The build copies only static assets to `public/static`. Functions use Frankfurt (`fra1`) to match the Supabase project region.
3. Add the following **server environment variables**, separately for Production and any isolated Preview environment:
   - `STUDYFLOW_MODE=cloud`
   - `SUPABASE_URL`
   - `SUPABASE_PUBLISHABLE_KEY`
   - `STUDYFLOW_SECRET_KEY` — a new random secret, at least 32 characters
   - `STUDYFLOW_ALLOWED_HOSTS` — exact custom hostnames if any, comma-separated without schemes. Vercel's `VERCEL_URL` and `VERCEL_PROJECT_PRODUCTION_URL` are added automatically.
4. Never set `STUDYFLOW_DEBUG=1`, never upload `.env`, local databases, tokens, or ZIP backups. `.gitignore` and `.vercelignore` exclude these. Vercel always enables cloud mode and Secure cookies, even if someone mistakenly sets local mode.
5. Use a separate Supabase project for previews, or keep previews deployment-protected. Do not expose production secrets to builds from untrusted forks.
6. Deploy, visit `/health` (configuration check, **not** a database readiness check), then sign in and create/edit/complete/delete a synthetic task.

Missing configuration fails closed: private routes return 503, never the old SQLite workspace.

## 4. Required pre-publication checks

- Sign in with two staging test accounts. Privately set `TEST_ACCESS_TOKEN_A` and `TEST_ACCESS_TOKEN_B` to their access tokens, then run `python verify_cloud.py`. This checks the actual database without application owner filters and cleans up its synthetic tasks. Never log or commit those tokens.
- Verify signup, duplicate signup, wrong passwords, password length enforcement, provider throttling, and logout followed by another account's login, including on a phone. Confirm there are no OTP, social login or misleading password-recovery buttons.
- Confirm HTTPS, private/no-store HTML responses, HttpOnly/Secure/SameSite cookies, no authentication tokens in HTML, and no publicly readable `.env` or database files.
- Verify Tasks & plan, Progress, the legacy `/planner` alias, and status forms with CSP enabled. Inline JavaScript handlers are not allowed.
- Review Supabase Security Advisor. Confirm RLS on `studyflow_tasks`, no anonymous grants, no extra permissive policies, and no service-role key in Vercel or frontend code.
- Configure Vercel Firewall rate limits for `/login` and `/register`, provider Auth quotas, spending alerts, and signup monitoring. Provider-side password/signup throttling is essential: process-local or cookie limits would be bypassable and would not protect Supabase's direct Auth API.
- This version does not integrate CAPTCHA. For a broadly promoted public site, add Supabase-supported CAPTCHA before removing deployment protection; do not enable it without adding the matching client token flow.
- Select and test a backup/restore policy supported by your database plan. Account deletion/export and operational alerting remain follow-up work.

## References

- [Vercel Flask deployment](https://vercel.com/docs/frameworks/backend/flask)
- [Supabase password authentication](https://supabase.com/docs/guides/auth/passwords)
- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase Auth rate limits](https://supabase.com/docs/guides/auth/rate-limits)
- [Supabase SMTP limits](https://supabase.com/docs/guides/auth/auth-smtp)

No automated checklist guarantees the absence of vulnerabilities. Retest after dependency, policy, auth, or deployment changes.
