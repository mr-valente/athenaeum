# Sablier lost-expiry bug

Sablier 1.18.0 can drop an expiring session from its in-memory store without ever stopping the container. Athenaeum works around it (see [on-demand applications](../reference/sablier.md)); this page holds the upstream bug report, ready to file, and the checklist for retiring the workaround once a fixed release exists.

File the report at <https://github.com/sablierapp/sablier/issues/new?template=bug_report.md>. Everything between the two horizontal rules is the issue; the title goes in the title field. No existing issue covered it as of 2026-09-10, and `pkg/tinykv/tinykv.go` on `main` was still identical to v1.18.0.

## Issue text

**Title:** In-memory store drops an expiring session without stopping the instance when a renewed key's stale timer is popped in the same pass

---

**Describe the bug**

When one expiry pass of the in-memory session store pops both a genuinely expired timer and a *stale* timer belonging to a different key that has since been renewed, the expired key is removed from the store but `onExpire` is never called for it. Sablier logs nothing, the instance is never stopped, and because the key is gone the instance never expires later either. The only ways it stops afterwards are a fresh session seeded by `--provider.auto-warm-externally-started` (30-second reconciliation) or a manual stop.

Real-world trigger: two independent groups A and B, both with the default duration. The last request to A and *any* request to B land within the same second. Twelve hours later (or whatever the duration is) A's timer and that B request's now-stale timer are popped together, and A stays up indefinitely.

**Context**

- Sablier version: 1.18.0 (`sablierapp/sablier:1.18.0`). `pkg/tinykv/tinykv.go` on `main` is unchanged as of 2026-09-10.
- Provider: docker, Engine 29.7.2 (API 1.55), `docker.strategy: stop`.
- Reverse proxy: Caddy 2.11.4 with `sablier-caddy-plugin` v1.0.2, but the reverse proxy is not involved; the API alone reproduces it.
- Sablier running inside a container? Yes (default image, in-memory store, no `storage.file`).

**To reproduce**

Unit test against the real package. Drop it into `pkg/tinykv/` and run `go test ./pkg/tinykv/ -run TestExpiryNotificationSurvivesRenewedNeighbour -count=1`. On v1.18.0 it fails with the notification lost in roughly 35 of 40 iterations:

```go
package tinykv

import (
	"sync"
	"testing"
	"time"
)

// Two keys share an expiry pass: "a" is idle and expires; "b" was renewed just
// before its first timer landed, so its stale timer is popped in the same pass.
// onExpire must fire for "a" every time.
func TestExpiryNotificationSurvivesRenewedNeighbour(t *testing.T) {
	lost := 0
	for i := 0; i < 40; i++ {
		var mu sync.Mutex
		fired := map[string]int{}
		kv := New[int](50*time.Millisecond, func(k string, _ int) {
			mu.Lock()
			fired[k]++
			mu.Unlock()
		})
		_ = kv.Put("a", 1, 200*time.Millisecond)
		_ = kv.Put("b", 1, 200*time.Millisecond)
		time.Sleep(150 * time.Millisecond)
		_ = kv.Put("b", 2, 10*time.Second) // renew b before its first timer lands
		time.Sleep(400 * time.Millisecond)
		if _, ok := kv.Get("a"); ok {
			t.Fatalf("iteration %d: a is still readable after its expiry", i)
		}
		mu.Lock()
		if fired["a"] == 0 {
			lost++
		}
		mu.Unlock()
		kv.Stop()
	}
	if lost > 0 {
		t.Fatalf("onExpire never fired for the expired key in %d/40 runs: it was deleted from the store without notification", lost)
	}
}
```

Black-box reproduction with Docker (needs only the socket and network access to pull two images). Three consecutive runs reproduced it here; with `sleep 2` inserted before the first `req b`, the same script stops `a` correctly and logs `instance expired` once, which isolates the same-pass collision as the cause:

```sh
#!/bin/sh
# Two idle-capable containers; "a" goes idle while "b" keeps being renewed.
set -eu
NET=sablier-repro
docker network create $NET >/dev/null
for c in a b; do
  docker create --name repro-$c --network $NET \
    --label sablier.enable=true --label sablier.group=$c \
    --health-cmd 'wget -qO- http://127.0.0.1:8000/ >/dev/null' --health-interval 1s --health-retries 10 \
    python:3.13-alpine python -m http.server 8000 >/dev/null
done
docker run -d --name repro-sablier --network $NET \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  sablierapp/sablier:1.18.0 start --logging.level=debug \
  --sessions.default-duration=10s --provider.auto-stop-on-startup=false >/dev/null
docker run -d --name repro-client --network $NET alpine:3.23 sleep 300 >/dev/null
req() { docker exec repro-client wget -qO- "http://repro-sablier:10000/api/strategies/blocking?group=$1&timeout=60s" >/dev/null; }
sleep 2
req a                                   # start a; returns once healthy = a's last renewal
req b                                   # start b at once: its first timer shares a's expiry pass
for i in $(seq 1 15); do sleep 2; req b; done   # keep b alive for 30 s; a gets no requests
echo "a running after 30 s idle with a 10 s session: $(docker inspect -f '{{.State.Running}}' repro-a)  (expected: false)"
echo "'instance expired' log lines: $(docker logs repro-sablier 2>&1 | grep -c 'instance expired')  (expected: 1)"
docker rm -f repro-a repro-b repro-sablier repro-client >/dev/null
docker network rm $NET >/dev/null
```

Observed output on v1.18.0:

```
a running after 30 s idle with a 10 s session: true  (expected: false)
'instance expired' log lines: 0  (expected: 1)
```

**Expected behavior**

`onExpire` fires for every key whose current entry has expired, regardless of which other timers the same pass discards. Instance `a` is stopped about 10 s after its last request and the log shows `instance expired instance=repro-a`.

**Root cause**

`Put` pushes a new timer for every renewal and leaves the previous timer in the heap ([`tinykv.go#L210-L222`](https://github.com/sablierapp/sablier/blob/v1.18.0/pkg/tinykv/tinykv.go#L210-L222)). `expireFunc` pops every timer whose time has passed into an `expired` candidate map keyed by instance name, then revalidates candidates against the store's *current* entry ([`tinykv.go#L347-L358`](https://github.com/sablierapp/sablier/blob/v1.18.0/pkg/tinykv/tinykv.go#L347-L358)):

```go
REVAL:
	for k := range expired {
		newVal, ok := kv.kv[k]
		if !ok ||
			newVal.timeout == nil ||
			!newVal.expired() {
			delete(expired, k)
			goto REVAL
		}
		delete(kv.kv, k)
	}
	go notifyExpirations(expired, kv.onExpire)
```

With `expired = {a, b}` where `a` is genuinely expired and `b`'s current entry was renewed, the outcome depends on map iteration order:

1. `a` is visited first: its store entry is deleted (`delete(kv.kv, a)`), and the loop continues to `b`.
2. `b`'s current entry is not expired, so `b` is removed from `expired` and `goto REVAL` restarts the loop.
3. The restarted loop visits `a` again. `kv.kv[a]` is now missing (`!ok`), so `a` is removed from `expired` too.
4. `notifyExpirations` runs with an empty map. `a` is gone from the store and from the heap; nothing will ever stop it.

If `b` happens to be visited first the pass works. Go's small-map iteration makes the bad order the common one (the unit test loses about 7 in 8), so in practice this is close to deterministic whenever the two timers share a pass. The in-memory store runs this pass every second (`inmemory.go` constructs `tinykv.New(1*time.Second, nil)`), so "share a pass" means "the two requests were within about a second of each other, one session duration ago".

**Suggested fix**

Validate all candidates first, then delete; deleting from a map during `range` is defined behaviour in Go, so no restart is needed:

```diff
--- a/pkg/tinykv/tinykv.go
+++ b/pkg/tinykv/tinykv.go
@@ -344,15 +344,15 @@
 			expired[last.key] = entry.value
 		}
 	}
-REVAL:
+	// Drop candidates whose current entry was renewed (or removed) since the
+	// popped timer was pushed; deleting during range is safe and needs no restart.
 	for k := range expired {
-		newVal, ok := kv.kv[k]
-		if !ok ||
-			newVal.timeout == nil ||
-			!newVal.expired() {
+		current, ok := kv.kv[k]
+		if !ok || current.timeout == nil || !current.expired() {
 			delete(expired, k)
-			goto REVAL
 		}
+	}
+	for k := range expired {
 		delete(kv.kv, k)
 	}
 	go notifyExpirations(expired, kv.onExpire)
```

With this patch the new test passes 40/40 and the existing `pkg/tinykv` tests still pass.

**Additional context**

- Workaround: `--provider.auto-warm-externally-started=true`. Its reconciliation scan treats the orphaned running instance as externally started (it has no session) and seeds a fresh default-duration session, so the instance stops one session late instead of never.
- Separate observation while reading this code, probably worth its own issue: `sessions.expiration-interval` is parsed and documented as the expiry check interval, but `setupStorage` calls `inmemory.NewInMemory()`, which hard-codes a 1 s interval; the configured value is never passed to the store.

---

## After the fix lands upstream

Do these in the Athenaeum checkout once a Sablier release includes the fix. Confirm first that the release's `pkg/tinykv/tinykv.go` no longer has the `goto REVAL` revalidation, or that the changelog references the issue; do not assume from the version number alone.

1. **Bump the pinned server image.** Change `sablierapp/sablier:1.18.0` to the fixed version in `compose.yaml` (the `sablier` service) and in `tests/sablier_smoke.py` (both the pull list and the `create('sablier', …)` call). Update the version mentioned in `docs/reference/architecture.md` (table row and the "fifth infrastructure service" sentence) and `docs/reference/sablier.md`. Check the release notes for changed CLI flags or API responses; the smoke test passes `--provider.*` and `--sessions.*` flags and the Caddy plugin relies on the `X-Sablier-Session-Status` header.
2. **Decide whether the Caddy plugin also moves.** `docker/edge.Dockerfile` pins `sablier-caddy-plugin@v1.0.2`. Only bump it if the new server needs it; a plugin change requires rebuilding and publishing the edge image, a separate release step.
3. **Remove the workaround from the smoke test.** Delete the `time.sleep(3)` and its comment in `tests/sablier_smoke.py`, between the Quacktuaries JSON request and the Bernoulli POST. That gap is the only thing hiding the bug from the test, so the test becomes the regression check: it must now pass without the gap.
4. **Retire the documentation of the limitation.** Remove the "Known Sablier 1.18.0 limitation" paragraph from `docs/reference/sablier.md`, the parenthetical about the store race in `skills/athenaeum-app/SKILL.md`, and the trailing comment on the `auto-warm-externally-started` assertion in `tests/sablier.test.ts`. Keep `auto-warm-externally-started: true` itself: it is still how Compose-started and reboot-restarted apps get adopted into a session. Then delete this page and its entry in `docs/README.md`, or keep it if the issue is still open for other users.
5. **Re-check `sessions.expiration-interval`.** If the release also fixes the store ignoring it, decide whether to restore `expiration-interval: 20s` in `deploy/sablier/sablier.yaml` (with sessions measured in hours the default 20 s is fine either way) and correct the "about once a second" sentence in `docs/reference/sablier.md`.
6. **Validate locally.** From the checkout:

   ```bash
   docker build -f docker/edge.Dockerfile -t athenaeum-edge:sablier-test .
   python3 tests/sablier_smoke.py && python3 tests/sablier_smoke.py
   ATHENAEUM_TEST_AGE=<path-to-age> python3 -m unittest discover -s tests -p 'test_*.py'
   npm run verify
   git diff --check
   ```

   Run the smoke test at least twice; before the fix it failed on nearly every run without the gap, so two clean passes are meaningful. Also run the black-box script above against the new image once; it should print `false` and `1`.
7. **Deploy through the usual manual path** in [daily usage](../guides/3-daily-usage.md): commit and push, then on the host `athenaeumctl repo sync`, `athenaeumctl docker pull --deploy`, `athenaeumctl verify`. Recreating Sablier discards its in-memory timers: apps that are running get a fresh one-hour default session through auto-warm until `verify` renews each at its tier, so expect them to sleep on the new schedule rather than their previous one. A later `athenaeumctl status` showing the apps as "stopped cleanly" after an idle stretch is the production confirmation.
