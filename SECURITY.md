# Security review — 2026-09-13

## Status

Supabase is provisioned and the task schema has been executed. Database checks confirmed
enabled/forced RLS, four policies, no anonymous SELECT/INSERT, and no owner UPDATE grant.
Real-account isolation tests and a live Vercel deployment still require setup.
Do not advertise this as a penetration-tested production service.

## Implemented protections

- Auth is delegated to Supabase: password, one-time email code, password reset by code,
  and optional Google sign-in with PKCE. Passwords and codes are forwarded over HTTPS and
  are never stored in the StudyFlow database, session, logs or rendered HTML.
- Email confirmation is required: new accounts are confirmed with a code from the mailbox,
  and sessions without `email_confirmed_at` are rejected. Account identity is always the
  server-validated UUID, never the email address.
- Signup, code requests and password reset answer the same way whether or not an address is
  registered, so the forms cannot be used to discover accounts. A repeated code request
  within 60 seconds is refused before it reaches the provider (Supabase applies its own limits too).
- The Google flow stores the PKCE verifier only in the signed session and uses it once, so a
  code obtained in another browser cannot sign anyone in (login CSRF).
- Access tokens are verified against `/auth/v1/user`. New passwords require at least
  15 characters and at most 72 UTF-8 bytes to avoid bcrypt truncation. The provider's
  minimum length is also 15; changing a password requires the current password.
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

- Offline regression suite (`python -m unittest -v`): task flows, unified calendar/list navigation, RU/EN, CSRF including malformed Unicode,
  server-validated identity, expired sessions, password signup/login and validation, cookie flags, account owner filters,
  foreign/out-of-range IDs, malformed cloud configuration, escaping, unsafe redirects, host validation, and cloud-outage fail-closed behavior.
- Cloud tests mock Supabase; they test application behavior, not the deployed RLS engine.
  Run `verify_cloud.py` with two staging accounts to test real policies before publication.
- Dependency audit initially flagged Flask 3.1.2 (PYSEC-2026-2151) and python-dotenv 1.1.1
  (PYSEC-2026-2270). These were upgraded to 3.1.3 and 1.2.2, respectively.
- Re-run `python -m pip_audit -r requirements.txt --progress-spinner off` after updates.
  A clean result only means no known issues in that advisory database at that time.

## Deployment and remaining risks

Follow `DEPLOYMENT.md`. Real RLS, password authentication, Secure cookies on HTTPS, and account switching
must be tested against the deployed service. Configure provider signup/password quotas,
Vercel rate limits, spending alerts, backups, and preview isolation. This version has no CAPTCHA
integration or per-account task storage quota; do not broadly open signup without anti-abuse
controls. Supabase's direct Auth API must also remain protected by provider throttling.
Accounts created before email confirmation was enabled were auto-confirmed by Supabase and
are not proof of mailbox ownership; review them before relying on email identity (for example
before linking Google accounts by email).

The CSP still permits inline **styles** for task/progress widths (not inline JavaScript).
The app does not encrypt task text end-to-end: trusted database/project administrators can read it.
Do not store passwords, access keys, medical records, or other sensitive documents in task notes.

No review can guarantee that leaks or vulnerabilities are impossible. Keep dependencies updated,
limit project access, enable MFA for GitHub/Vercel/Supabase, and retest after security-sensitive changes.
