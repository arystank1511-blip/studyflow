# Security review — 2026-09-13

## Status

Cloud integration is implemented, but provider provisioning, real email delivery,
database policy execution, real-account isolation checks, and a live Vercel deployment
still require account setup. Do not advertise this as a penetration-tested production service.

## Implemented protections

- Auth is delegated to Supabase email OTP. StudyFlow does not store passwords.
- Auth access tokens are verified against `/auth/v1/user`; an unverified email is rejected.
- Access and rotating refresh tokens are HttpOnly cookies. Production uses Secure and
  SameSite=Lax; tokens never enter templates, localStorage, query URLs, or application logs.
- Login clears previous drafts and rotates the CSRF token. Logout revokes the current
  Supabase session before clearing cookies. Already issued JWTs may remain usable until
  expiry in some Supabase APIs; logout is not a promise of instant JWT invalidation everywhere.
- Every POST, including login, verification, language change and logout, needs a session-bound
  CSRF token. All user text is Jinja-escaped; inline script/event handlers are forbidden by CSP.
- Cloud task operations use a server-verified owner filter. SQL policies additionally restrict
  SELECT/INSERT/UPDATE/DELETE to `auth.uid() = user_id`; owner and ID updates are not granted.
- No service-role key is accepted. No SQLite fallback is allowed in cloud mode, including outages.
- Local-only mode checks both Host and loopback remote address and binds to 127.0.0.1.
  Do not expose it through a tunnel, reverse proxy, or a shared machine account.
- Request size, task lengths, dates, priorities and statuses are validated; SQLite uses bound
  parameters, cloud requests use a fixed URL/table and allowlisted fields.
- Private HTML is no-store, including Vercel CDN cache controls. Clickjacking protection,
  MIME sniffing protection, no-referrer policy, and production HSTS are set.
- `.env`, SQLite files, `.git`, Python environments, tokens, and archives are excluded from deployment.
  Google Fonts receives font requests; no task text or auth data is sent in those requests.

## Verification

- 40 offline regression tests: task flows, unified calendar/list navigation, RU/EN, CSRF including malformed Unicode,
  verified identity, expired sessions, OTP validation, cookie flags, account owner filters,
  foreign IDs, escaping, unsafe redirects, host validation, and cloud-outage fail-closed behavior.
- Cloud tests mock Supabase; they test application behavior, not the deployed RLS engine.
  Run `verify_cloud.py` with two staging accounts to test real policies before publication.
- Dependency audit initially flagged Flask 3.1.2 (PYSEC-2026-2151) and python-dotenv 1.1.1
  (PYSEC-2026-2270). These were upgraded to 3.1.3 and 1.2.2, respectively.
- Re-run `python -m pip_audit -r requirements.txt --progress-spinner off` after updates.
  A clean result only means no known issues in that advisory database at that time.

## Deployment and remaining risks

Follow `DEPLOYMENT.md`. Real RLS, auth delivery, Secure cookies on HTTPS, and account switching
must be tested against the deployed service. Configure an SMTP sender, provider OTP quotas,
Vercel rate limits, spending alerts, backups, and preview isolation. This version has no CAPTCHA
integration or per-account task storage quota; do not broadly open signup without anti-abuse
controls. Cookie-based resend cooldown is UX only, not a security rate limiter.

The CSP still permits inline **styles** for task/progress widths (not inline JavaScript).
The app does not encrypt task text end-to-end: trusted database/project administrators can read it.
Do not store passwords, access keys, medical records, or other sensitive documents in task notes.

No review can guarantee that leaks or vulnerabilities are impossible. Keep dependencies updated,
limit project access, enable MFA for GitHub/Vercel/Supabase, and retest after security-sensitive changes.
