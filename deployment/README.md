# Controlled deployment package

These are reviewable templates, not an executed installation. They do not create
accounts, obtain certificates, change DNS/network permissions, or deploy a service.
The recorded Tencent host is not currently verified or authorized by this work.

## Required contract

- Linux with an approved unprivileged account, working required namespaces,
  Bubblewrap and libseccomp; verify `docs/SANDBOX-RUNTIME.md` positive attack tests
- Single application process and scheduler, loopback `127.0.0.1:8765` only
- Dedicated runtime image root, read-only approved code/models, private data root
- One exact public HTTPS origin, `MARKITDOWN_COOKIE_SECURE=1`, production mode
- TLS terminates at a same-host, controlled proxy; preserve the configured Host
  and Origin. The app never trusts arbitrary Forwarded headers or client IPs
- Proxy per-IP + server-wide limits, upload time/size limits and connection caps
- Host memory/process/disk capacity verified with concurrent synthetic jobs;
  logical application storage reservations are not disk or RSS hard quotas
- Body-free logs, independent backup/journal retention policy, monitored failures

The app deliberately aggregates authentication IP limiting behind a loopback
proxy, rather than trusting unverified forwarded client IP. The proxy applies
per-client limits. A trusted-IP forwarding feature would need separate design.

## Installation/upgrade sequence for an authorized operator

1. Freeze the exact release commit and dependency lock files, review notices.
2. Build/export the parser runtime and verify its digest and required capability
   tests before installing the app; do not weaken a failed sandbox to get online.
3. Install the app into a versioned directory and provision an empty private data
   directory. The eventual administrator runs interactive `bootstrap-admin` and
   enters their own password. Existing data must never be reset to regain access.
4. Review hostname/certificates in nginx and application settings together. Run
   nginx's syntax test before enabling the proxy. Do not enable wildcard hosts or
   remove CSRF/Origin checks. Proxy and certificate tooling are external to this
   repository; this change does not exercise them on a live server.
5. Test login, invitation, both permitted profiles, copy/download, cancellation,
   isolation, cross-owner denial, expiry and backup/restore using synthetic data.
6. Record results and obtain separate rollout approval before directing real
   users to the service. No application code test certifies filing/legal duties.

## Recovery and rollback

Stop admissions at the proxy, stop the service, and confirm the worker lock can
be acquired before taking an offline backup. Never copy only an active SQLite
main file. Keep the recovery journal newer than the snapshot. Restore into a
new empty directory, apply later deletions/deactivations, invalidate sessions
and invitations, and run verification before switching the configured data path.
Do not automatically revert to an old database on a code rollback: it can revive
files/permissions and discard usage counts. Prefer the previous compatible code
on current data; if a schema downgrade is needed, use the tested restore route.
Keep the previous directory until the owner accepts recovery, with its separately
approved retention policy. Record observed restore duration; no RTO/RPO promised.

## References

- [nginx request limits](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)
- [nginx proxy header behavior](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)
- [SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)
