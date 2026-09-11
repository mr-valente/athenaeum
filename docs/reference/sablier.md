# On-demand applications

The main Athenaeum website and Caddy remain running. Only the hosted Quacktuaries and Bernoulli services carry `sablier.enable=true`. Each has its own project-qualified group, such as `athenaeum-quacktuaries` and `athenaeum-bernoulli`.

## Policy and routing

`deploy/sablier/sablier.yaml` sets a **12-hour sliding idle session**. Every request routed through an app renews its session; the in-memory store checks for expiry about once a second (Sablier 1.18.0 accepts `sessions.expiration-interval` but does not apply it to that store). This is not a hard 12-hour lifetime from first startup. An open page that keeps polling can keep an app awake indefinitely. Background jobs and idle WebSockets alone do not renew the session.

Compose starts apps normally, including during deployment. Sablier adopts externally started containers into a default-duration session without killing them on startup. It later stops idle containers; their SQLite databases and signing keys remain on the data disk. Session timers are in memory, not a recovery artifact: restarting Sablier grants already-running managed apps a fresh default session, while sleeping apps stay stopped until requested.

**Known Sablier 1.18.0 limitation.** The in-memory store can drop an expiring session *without* stopping its container when the same expiry pass also discards a renewed neighbour's stale timer: in practice, when the last request to one app and any request to the other app landed in the same second, twelve hours earlier. The app then keeps running with no session. `auto-warm-externally-started: true` is the mitigation, not a convenience: its 30-second reconciliation scan finds a running managed app without a session and seeds a fresh default session, so the app sleeps one session late rather than never. Keep that setting on. The smoke test disables it for daemon safety and instead keeps the two apps' session clocks a tick apart. The [upstream report and follow-up checklist](../development/sablier-bug-report.md) cover filing the bug and retiring this workaround.

`deploy/caddy/apps.caddy` defines the shared app snippet, imported by both production and local entrypoints. `routes.caddy` supplies each prefix, service name and display name. The snippet redirects the bare slug without losing its query, denies the private health endpoint before wake-up, calls Sablier, and only then strips the prefix and proxies to port 8000.

- HTML GET requests receive the custom waiting page and automatically retry the original URL.
- Other requests use the blocking strategy with a one-minute readiness timeout. POST bodies, API responses and assets are not replaced by a loading document. If the app is not ready in time the request is never forwarded; the client receives Sablier's `application/problem+json` error, which the Caddy plugin relays with HTTP 200, so API callers should check the content type rather than the status alone.
- App Docker healthchecks determine readiness. The website fallback never passes through Sablier.

The edge image compiles Caddy 2.11.4 with the pinned Sablier plugin v1.0.2. The server uses `sablierapp/sablier:1.18.0`. Rebuild the edge image when changing the plugin or Caddy routing.

## Loading-page design

`deploy/sablier/themes/athenaeum.html` follows the After hours contract: ink canvas, warm ivory text, mint accents, amber keyboard focus, monospace fallback, flat borders and the shared tesseract mark. There is no looping animation, JavaScript or external asset service. The layout works on narrow screens; refresh and manual retry preserve the requested path and query.

Compose mounts `design/` as the theme's `assets/` directory. Sablier embeds the SVG into the response, so the loading page never needs to request assets from a sleeping app. The template uses `.DisplayName` and `.RefreshFrequency`; session lengths and other infrastructure details are hidden. Config/theme files are read-only mounts from the checkout. Recreate Sablier after policy, asset or theme changes so configuration and asset bundling are refreshed:

```bash
sudo docker compose --project-name athenaeum \
  --env-file /etc/athenaeum/compose.env \
  -f /opt/athenaeum/stack/compose.yaml \
  up -d --no-deps --force-recreate sablier
```

## Security and operations

Only Caddy and Sablier join the internal `control` network. Sablier publishes no host port, and the application network cannot reach its API. The server alone mounts the host Docker socket and runs as root to access it; all capabilities are dropped and its root filesystem is read-only. **A read-only socket mount does not make Docker API calls read-only.** Treat Sablier as host-privileged infrastructure. The main site and apps still run non-root and receive no socket or OCI credentials.

Run only one Sablier lifecycle manager per Docker daemon. Project-qualified groups prevent request collisions, but automatic provider discovery is daemon-wide, not restricted to that Compose project. Use a separate daemon/host for concurrent independent Sablier stacks; do not launch the local stack on a daemon already managed by another Sablier instance.

`athenaeumctl status` is passive: cleanly exited registered apps are acceptable, while missing apps, nonzero exits and unhealthy or stopped infrastructure fail. A clean manual stop looks like an idle stop; status says so rather than claiming Sablier caused it. `athenaeumctl verify` is active: it announces that it wakes apps, waits for their actual responses using the blocking route, and checks that all services are healthy afterward. Repeated external app uptime probes also keep apps awake; monitor the always-on website instead when sleep is desired.

Backups inspect existing containers, including stopped ones, and use SQLite's backup API against the data disk. Sleeping does not remove recovery metadata or require waking the app. Sablier's configuration and theme are reconstructed from Git, not a new application database.

## Validation

Run host regressions with `python3 -m unittest discover -s tests -p 'test_*.py'`. Build the edge locally and run the opt-in smoke test:

```bash
docker build -f docker/edge.Dockerfile -t athenaeum-edge:sablier-test .
python3 tests/sablier_smoke.py
```

The smoke test uses disposable fixtures, a unique network and loopback-only ports. It disables provider-wide startup adoption/stopping in its Sablier instance to avoid touching unrelated managed containers, and removes only its own fixtures. It checks Caddy adaptation, cold navigation, self-contained theme rendering, cold POST forwarding, separate group expiration and the always-on site. It does not prove real classroom persistence, native ARM execution or Oracle deployment.

Upstream references: [Caddy integration](https://sablierapp.dev/tutorials/reverse-proxies/caddy/), [custom themes](https://sablierapp.dev/how-to-guides/loading-strategies/customize-theme/), and [server configuration](https://sablierapp.dev/tutorials/configuration/).
