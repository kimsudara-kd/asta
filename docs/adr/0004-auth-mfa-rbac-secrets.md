# ADR-0004: Authentication, MFA, RBAC, secrets

Status: accepted (M0)

- Single-operator initially; roles `viewer`, `operator`, `reviewer`, `admin` with a static permission map (`auth.rbac`). Routes declare required permissions; default is deny.
- Passwords: Argon2id. MFA: TOTP (RFC 6238) mandatory for `operator`/`reviewer`/`admin`; login is two-step (password -> TOTP). Sessions are opaque random tokens; only the SHA-256 of the token is stored; short TTL; HttpOnly + SameSite=Strict cookie. State-changing routes require a CSRF header.
- Login throttling: lockout after repeated failures. All login, MFA, role and policy events are audited.
- Exchange credentials: none in M0. `exchange_connections` stores metadata and a *secret reference name* only (never values). The settings loader refuses to start if `*_API_KEY`/`*_SECRET`/`*_PASSPHRASE` variables are present.
- Secrets never go in Git: `.env` ignored, gitleaks in CI.
