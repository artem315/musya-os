# MURCLOUD security model

## Accounts and sessions
* Passwords: `scrypt` (N=2^15, r=8, p=1, 16-byte salt), constant-time compare, dummy
  hash for unknown users (no username timing oracle), lockout for 5 minutes after 5
  failures, password policy (8+ chars, not trivial, ≠ username).
* Sessions: 256-bit random tokens; only their SHA-256 is stored. Cookie `mur_session`
  is `HttpOnly`, `SameSite=Lax`, `Secure` in production (startup refuses production
  without `MUR_COOKIE_SECURE=1`). Bearer tokens are accepted for non-browser clients.
* CSRF: every cookie-authenticated state-changing request must carry `X-MUR-CSRF: 1`
  (a custom header cannot be sent cross-origin without passing CORS). WebSockets check
  the `Origin` header.
* The first account becomes admin; set `MUR_REGISTRATION_OPEN=0` to close sign-ups.

## Isolation between users
* Every query is scoped by `user_id`; projects, Drive items, conversations, runs,
  browser contexts, previews and PC sessions are looked up by owner (404 for others).
* Each user gets a separate Chromium `BrowserContext` (cookies, storage, downloads).
* Project file access goes through `safe_join` (no absolute paths, no `..` escape).

## Service Engine and SSRF
* Every navigation **and every sub-request** (Playwright route interception, including
  WebSockets) is checked by `NetworkPolicy`: only `http/https`, hostnames are resolved and
  rejected if *any* address is loopback/private/link-local/CGNAT/multicast/reserved or a
  metadata name. Downloads follow redirects manually and re-check every hop.
* A user's own preview server (`127.0.0.1:<port>`) is allow-listed only in that user's
  policy. Residual risk: DNS rebinding between our check and Chromium's resolution —
  for strict deployments also run the backend in a network namespace/firewall that blocks
  internal ranges.
* Downloads: size limit (`MUR_MAX_UPLOAD_MB`), sanitized names, executable extensions
  renamed `.blocked`, SHA-256 recorded, stored in MUR Drive (not executed).
* Command queue per user, per-command timeouts, cancellation, rate limits.

## MUSYA scripts
* No `eval`/`exec`; the interpreter only exposes MUSYA values and explicit host APIs.
* Permissions come from the manifest (`app.yml` / `.driver`) and are checked for every
  `service` verb, file and AI call. Sensitive permissions (`browser.download`,
  `files.write`, `ai.control`, `packages.install`) must be confirmed at install time.
* Step, depth, size and time limits; limit errors are not catchable.
* The terminal is a MUSYA REPL, **not** a system shell. Shell access exists only inside an
  isolated MUR PC container.

## MUR AI
* Real tool calling with JSON-schema validation of every argument before execution.
* Policy matrix by mode (chat/code/agent/auto) × risk (low/medium/high); high-risk actions
  (delete, download) always need explicit approval; permissions are per conversation and
  editable by the user. Denials are returned to the model as errors (no silent success).
* Web content returned to the model is wrapped as untrusted data (prompt-injection hygiene).
* Every tool call, approval and denial is written to the audit log with secrets redacted.
* Provider API keys are encrypted at rest (Fernet, key derived from `MUR_SECRET_KEY`),
  never returned to the browser (only the last 4 characters).

## MUR PC
* One container per session from a dedicated image, unprivileged user, all capabilities
  dropped, `no-new-privileges`, memory/CPU/PID limits, internal Docker network, no
  published ports, random VNC password, VNC bound to localhost inside the container.
* The browser reaches VNC only through the authenticated backend WebSocket proxy.
* The backend needs the Docker socket for this feature, which is root-equivalent:
  run it only on a dedicated host.

## Web hardening
* CSP (`default-src 'self'`, no inline scripts), `X-Frame-Options`, `nosniff`,
  `Referrer-Policy`, `Permissions-Policy`, HSTS behind HTTPS.
* Project previews are served on signed, expiring capability URLs with
  `Content-Security-Policy: sandbox` so preview pages cannot act as the user.
* Drive downloads are attachments by default; only safe media types render inline, always
  with `CSP: sandbox`.

## Secrets
No secrets live in the repository. Configure `MUR_SECRET_KEY`, database password, AI keys
and signing keys through environment variables / CI secrets (`.env.example` lists them).
