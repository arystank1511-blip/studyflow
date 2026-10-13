# StudyFlow on Vercel + Supabase

Cloud integration is implemented; a live deployment is not complete until the checks below pass.
Do not treat offline mocks as proof that a real database has RLS enabled.

## 1. Supabase (new project)

1. Create a project in your own account. Choose a suitable region and store the database password in your password manager, not in Git or chat. Stay on a free plan unless you decide otherwise.
2. Execute `supabase/schema.sql` once in the SQL Editor. It creates a dedicated table, constraints, and owner-only row-level policies. It intentionally fails if that table already exists, instead of overwriting data.
3. Email addresses are verified with one-time codes. In Authentication → Sign In / Providers → Email turn **Confirm email** ON, keep anonymous sign-ins and manual identity linking disabled.
4. Set minimum password length to 15 in the Email provider and keep Auth rate limits enabled.
5. **Custom SMTP is required.** Supabase's built-in sender only delivers to project team members and at a very low hourly rate. In Authentication → Emails → SMTP Settings connect a provider (for example Resend, Brevo or Postmark) with a verified sending domain.
6. **Email templates must contain the code.** StudyFlow asks for a code instead of following a link. In Authentication → Emails → Templates put `{{ .Token }}` into **Confirm signup**, **Magic Link** and **Reset Password**, e.g. `Ваш код StudyFlow: <b>{{ .Token }}</b>. Он действует 1 час.` Remove the `{{ .ConfirmationURL }}` link so nobody follows a link that this app does not handle.
7. Optional Google sign-in: create an OAuth client in Google Cloud, enable the Google provider in Supabase with it, add `https://<your-domain>/auth/callback` to Authentication → URL Configuration → Redirect URLs, then set `STUDYFLOW_GOOGLE_LOGIN=1` on Vercel. Leave it unset until all of that is done; the button stays hidden.
8. Get the project URL and **publishable** key (or legacy **anon** key). This app deliberately rejects a service-role/secret key. The service-role key bypasses RLS and must not be used.
9. Set the Auth Site URL to the final HTTPS Vercel URL. Do not add wildcard redirect URLs.

StudyFlow forwards passwords and codes only to Supabase over HTTPS; Supabase manages password hashing, codes and auth sessions. They never enter the application database, cookies, templates or logs. Sign-in options: password, one-time email code (creates the account on first use), password reset by code, and Google (PKCE) when enabled.

### Study timer and timetable scanning

- Run `supabase/study_sessions.sql` once in the SQL Editor. Until then the Study page says time cannot be saved; nothing else breaks.
- Run `supabase/calendar_feed.sql` once for calendar subscriptions (Progress → Subscribe in your calendar). Only a SHA-256 hash of each secret link is stored; calendar apps read unfinished tasks through the `studyflow_calendar_feed` function without signing in. Until the file is run, the Progress page says the subscription is not connected.
- For timetable photos, create an API key at console.anthropic.com (billing required) and add it on Vercel as `ANTHROPIC_API_KEY` (Production). Set a monthly spend limit in the Anthropic console. Photos are sent to Anthropic only to read the text and are not stored by StudyFlow. Without the key the upload form is hidden.

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
- Verify signup with the emailed code, sign-in by code, password reset by code, wrong and expired codes, duplicate signup (must look identical to a new one), wrong passwords, provider throttling, Google sign-in if enabled, and logout followed by another account's login, including on a phone.
- Confirm HTTPS, private/no-store HTML responses, HttpOnly/Secure/SameSite cookies, no authentication tokens in HTML, and no publicly readable `.env` or database files.
- Verify Tasks & plan, Progress, the legacy `/planner` alias, and status forms with CSP enabled. Inline JavaScript handlers are not allowed.
- Review Supabase Security Advisor. Confirm RLS on `studyflow_tasks`, no anonymous grants, no extra permissive policies, and no service-role key in Vercel or frontend code.
- Configure Vercel Firewall rate limits for `/login` and `/register`, provider Auth quotas, spending alerts, and signup monitoring. Provider-side password/signup throttling is essential: process-local or cookie limits would be bypassable and would not protect Supabase's direct Auth API.
- CAPTCHA (Cloudflare Turnstile, optional but recommended for a public site): in Cloudflare → Turnstile add a widget for your domain (mode "Managed"). Put its **secret key** into Supabase → Authentication → Attack Protection → Enable Captcha protection (provider Turnstile), then set the **site key** on Vercel as `TURNSTILE_SITE_KEY` and redeploy. Do both together: Supabase without the site key rejects every sign-in; the site key without Supabase protection only shows a check that nobody verifies.
- Select and test a backup/restore policy supported by your database plan. Account deletion/export and operational alerting remain follow-up work.

## References

- [Vercel Flask deployment](https://vercel.com/docs/frameworks/backend/flask)
- [Supabase password authentication](https://supabase.com/docs/guides/auth/passwords)
- [Supabase Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase Auth rate limits](https://supabase.com/docs/guides/auth/rate-limits)
- [Supabase SMTP limits](https://supabase.com/docs/guides/auth/auth-smtp)

No automated checklist guarantees the absence of vulnerabilities. Retest after dependency, policy, auth, or deployment changes.
