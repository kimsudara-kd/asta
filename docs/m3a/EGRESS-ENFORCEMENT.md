# Infrastructure egress enforcement (M3A-1)

> **M3A-R1 update:** the boundary was hardened with a kernel-enforced gate (section 8). Sections 2-4 describe the original M3-A design; where they differ (the proxy now runs inside the gate's network namespace, subnets pinned, DNS allowlist-only) section 8 and `docs/m3a/VM-BOUNDARY-DESIGN.md` are authoritative. F-02 stays OPEN: no dedicated VM exists.

Status: implemented and verified for the **protected container runtime**; **not yet applied to the running M2 processes** (see section 7). Everything is version-controlled in `infra/egress/`.

## 1. Design decision

Requirement: default-deny outbound access enforced independently of ASTA application code, without disturbing BOSIA, PMS, PADF, PethHub or any other project on this PC.

Options evaluated on this machine (Windows 11 Home + Docker Desktop 29.6 on WSL2, other projects' containers and networks present):

| Option | Verdict |
|---|---|
| Windows Defender Firewall rule on the Docker backend (`vmmem`/`docker-desktop`) | **Rejected.** All containers of all projects share that backend; a rule cannot be ASTA-only and would disrupt other projects (also a shared-host security change, which M3-A may not make without escalation). |
| Host firewall rules for the M2 Windows processes | Rejected for M3-A: the M2 services are the accepted, running observation; interrupting/relaunching them is not authorized; per-process rules also need administrator rights. |
| Dedicated VM / separate physical or virtual firewall | **Preferred for production** (directive section 3), not available/authorized here (Windows 11 Home has no Hyper-V manager; no spare host). Documented as the recommended hardening before any credential. |
| **Dedicated Docker *internal* network + allowlist CONNECT proxy** | **Selected.** Docker enforces an internal network in the Linux VM (no gateway, no NAT: a process on it has no route to anything else). The only dual-homed component is the proxy, which is a small default-deny policy engine. ASTA-specific names (`asta_protected`, `asta_egress`, `asta-egress-*`), no ports published, other projects' networks untouched. |

## 2. Architecture

```
 protected runtime (future connector)          egress proxy                      Internet
 asta_protected (internal: true, no IPv6) <--> asta-egress-proxy <--> asta_egress (bridge, NAT)
        no route out, no DNS to outside          CONNECT-only, default-deny      only approved public data hosts
```

* `infra/egress/docker-compose.egress.yml` - networks + the two containers (own compose project `asta-egress`).
* `infra/egress/allowlist.json` - the allowlist (host NAME + port), profiles `runtime` and `capability_probe`; its SHA-256 is logged at proxy start.
* `infra/egress/proxy.py` - the proxy (stdlib only). `infra/egress/negative_tests.py`, `capability_probe.py` - run inside the protected container.
* Containers: non-root (65534), read-only root filesystem, `cap_drop: [ALL]`, `no-new-privileges`, `privileged: false`, PID and memory limits, `tmpfs` /tmp `noexec`, **no published ports, no Docker socket, no host mounts except two read-only single files** (test scripts) - evidence `docs/evidence/m3a/container-posture.txt`, enforced by the test `test_compose_hardening_is_declared`.

## 3. Policy enforced by the proxy (per CONNECT)

1. CONNECT only (other verbs, absolute-URI plain HTTP proxying, SOCKS handshakes: refused).
2. Target must be a DNS **name**; IPv4/IPv6 **literals refused** (direct-IP bypass), as are userinfo/port/`%` tricks and malformed names; case and trailing dot normalised.
3. (name, port) must be in the active profile of the allowlist. The runtime profile contains only `data-api.binance.vision:443`, `data-stream.binance.vision:443|9443`, `www.okx.com:443`, `ws.okx.com:443`. No trading, production-account, futures, demo-WebSocket or testnet host is in it.
4. The proxy resolves the name itself, uses **IPv4 only**, and **every** returned address must be public unicast; loopback, RFC1918, link-local (incl. `169.254.169.254` cloud metadata), CGNAT (`100.64/10`, includes `100.100.100.200`), multicast, reserved, benchmarking and unspecified ranges refuse the whole name (DNS-rebinding / sinkhole / SSRF defence). It then connects to the validated IP (pinned, no second resolution).
5. Limits: 4 KiB header cap, 64 concurrent connections, 120 s idle timeout, 10 s connect timeout.
6. Every decision is one JSON log line (UTC time, decision, profile, host, port, reason, client). No payload and no secret ever reaches the proxy (TLS is opaque to it).

Application layer (kept, defence in depth): `core/egress.py` allowlist + forbidden path fragments, `PublicHttp` (httpx, redirects not followed: a 3xx raises, test `test_public_http_never_follows_a_redirect_to_another_host`), `ExecutionGateway` default DENY.

## 4. Verification evidence (all from INSIDE `asta-protected-probe`, raw sockets - the application is bypassed on purpose)

`python /app/negative_tests.py`: **78 checks, 0 failed**, run three times (`docs/evidence/m3a/negative-tests-run1.json` had one failing check caused by my own over-broad test heuristic, fixed and disclosed; `run2-after-restart.json` after `docker restart` of both containers; `run3-final.json` after the final profile restore). Also run by pytest (`tests/m3a/test_egress.py`, which asserts the same from inside the container; skipped, not passed, if the boundary is not running).

| # | Required negative test | Result |
|---|---|---|
| 1 | Unapproved exchange hosts (kraken, app/us.okx.com, testnet in the runtime profile) | refused (403), direct connect fails |
| 2 | Production trading hosts/ports (`api.binance.com`, `api1`, `api.binance.us`, `fapi`, `www.binance.com`, `stream.binance.com:9443`, `api.okx.com`, `ws.okx.com:8443`, `wspap.okx.com`) | refused; direct fails |
| 3 | Arbitrary HTTP(S) (example.com :80/:443, httpbin, ifconfig.me, plain `GET http://`) | refused (403/405); direct fails |
| 4 | Direct external IPs (1.1.1.1:443, 8.8.8.8:53, 93.184.216.34:80, ...) | direct: no route; via proxy: `ip-literal-denied` |
| 5 | Unapproved WebSocket endpoints (wrong host or port) | refused |
| 6 | Proxy bypass (no-proxy direct to an *allowed* host, SOCKS, tunnel to the proxy itself, alternative proxy ports 1080/3129/8080/8888/9050, userinfo confusion, case/dot variants) | all fail closed |
| 7 | Host-only management endpoints (`host.docker.internal:5640` = the ASTA database on the host, `:2375`, `gateway.docker.internal`, `kubernetes.docker.internal`, `localhost`, `127.0.0.1`), Docker socket | direct: unreachable; proxy: refused; no `docker.sock` in container |
| 8 | Cloud metadata (`169.254.169.254` :80/:443, `metadata.google.internal`, `100.100.100.200`) | refused / unreachable |
| 9 | IPv6 (external, literal via proxy, `::1`) and container IPv6 addresses | blocked; container has no global IPv6 |
| 10 | Redirected destinations | target hosts refused by the same policy; app guard does not follow redirects (unit test) |
| 11 | Positive control | `CONNECT data-api.binance.vision:443` -> TLS `GET /api/v3/time` = HTTP 200 over the tunnel (public, unauthenticated). **OKX `www.okx.com`: refused by the proxy with `resolves-to-non-public:127.0.0.1`** (upstream DNS sinkhole, residual R-1) - fail closed, no OKX connectivity claimed, nothing bypassed |
| 12 | Container posture (non-root, CapEff 0, no-new-privileges, read-only rootfs, no sensitive host mounts) | pass |
| 13 | DNS from the protected network | external names do **not** resolve (`gaierror`): no DNS side channel |

Policy unit tests (`tests/m3a/test_egress.py`, 77 tests across the M3-A test files at the time of writing): 17 target-policy cases, 14 address-classification cases, DNS rebinding/sinkhole/mixed-answer cases, IPv6-answer handling, allowlist content (never an IP, never a trading or demo-WS host, runtime profile has no testnet).

Blocked attempts are logged (`docker logs asta-egress-proxy`, 100+ DENY lines during testing, each with a reason, none with payload). The log survives container restarts; it is not yet shipped to the ASTA audit chain (finding F-07 in the threat model).

**Survives restart:** both containers `restart: unless-stopped`; checks re-run green after `docker restart`. A Docker Desktop / Windows reboot was **not** exercised (UNVERIFIED).

## 5. Profile switching (controlled change)

The `capability_probe` profile adds the Binance Spot Testnet *public* hosts. It was used once for the M3A-3 probes by recreating the proxy with `ASTA_EGRESS_PROFILE=capability_probe`, then restored to `runtime` and re-tested (run3). Switching profiles is a deployment-time change reviewed through version control of `allowlist.json` and compose; there is no runtime API to change the allowlist. Future execution privileges need signed, reviewed configuration (CREDENTIAL-ISOLATION.md).

## 6. Impact on other projects

Docker state before/after (`docs/evidence/m3a/docker-before.txt` vs now): the only differences are the two ASTA containers and two ASTA networks. No other project's container, network, volume, image setting, Windows firewall rule, proxy setting, DNS setting or scheduled task was read or changed. The M2 supervisor (Task Scheduler) and both paper sessions kept running throughout.

## 7. Limitations and residual risks (honest)

1. **The running M2 services (Windows host processes) are not inside this boundary.** They keep application-level restrictions only. Moving the services into the protected runtime requires restarting them and is not authorized during the M2 observation. Gate wording "egress isolation is enforced outside application logic" is therefore met for the protected runtime and **not** for the legacy host processes. Before any credential, the connector must run only in the protected runtime.
2. **The proxy container sits on the shared Docker NAT bridge.** Docker Desktop offers no per-container firewall for it; its own egress is limited only by its code (default-deny, public IPs only) and hardening. A compromise of the proxy container is an egress compromise. Preferred hardening: dedicated VM/firewall that allows only the allowlisted names/addresses.
3. **Host-name granularity only.** A CONNECT proxy cannot see paths, headers or whether a request is authenticated. OKX serves demo and production REST from the same host, so host allowlisting cannot separate them; controls are the absence of credentials, the vault/binding contracts and application-level path/header guards (THREAT-MODEL T-03).
4. Resolution is performed by the proxy against the PC's upstream resolvers, which currently sinkhole OKX; correctness of DNS answers for allowed names is trusted only to the extent that non-public answers are refused (no DNSSEC; a malicious *public* answer for an allowed name would be accepted - TLS certificate validation by the client is the remaining control).
5. Not tested: container escape, kernel/Docker vulnerabilities, Docker Desktop reboot persistence, behaviour if the Docker backend itself is replaced, WebSocket frame-level content.
6. Port 9443 is allowed for the Binance data stream host as documented; it is not used by the M2 adapter (which uses 443) and may be removed.


## 8. M3A-R1 hardening and verification (kernel gate)

Added `infra/egress/gate/` (Alpine image, nftables + dnsmasq + jq) and moved the proxy into the gate's network namespace (`network_mode: service:egress-gate`). Result: the proxy process (non-root, no capabilities, read-only filesystem) is bound by kernel rules it cannot change: default DROP in input/output/forward (IPv6 included), outbound only TCP to addresses the gate resolved for allowlisted names (public unicast IPv4 only, 443/9443), inbound only 3128 from `172.31.250.0/24`, DNS only for allowlisted names (REFUSED otherwise), `resolv.conf` pinned to the local resolver. Full design, residual shared-kernel limits and the dedicated-VM plan: `docs/m3a/VM-BOUNDARY-DESIGN.md`.

Verification (`python infra/egress/verify_boundary.py`, evidence `docs/evidence/m3a/r1-boundary-verification.json`):
| Phase | Protected-runtime negative checks | Compromised-proxy checks (unprivileged process in the proxy's namespace) |
|---|---|---|
| baseline | 78/78 | 39/39 |
| after proxy restart | 78/78 | 39/39 |
| after gate restart (dependents recreated) | 78/78 | 39/39 |
| gate stopped | protected runtime has zero connectivity (proxy alias unresolvable): fail closed | - |
| after recovery | 78/78, `CONNECT data-api.binance.vision:443` -> 200 | 39/39 |

Compromised-proxy checks (`infra/egress/gate_tests.py`) cover direct public IPs and ports (443/80/22/25/9443), UDP DNS to public resolvers (no answer), local resolver for non-allowlisted and tunnelling-style names (REFUSED), allowed address on other ports (blocked), IPv6 (blocked, no global address), cloud metadata, the Docker host side of both bridges, the LAN router, `host.docker.internal`, and self-weakening attempts (no capabilities, raw socket refused, sysctl write refused, no `nft`/`iptables`/`ip` binary in the image). Kernel drop counters after the runs: tens of input and over a hundred output packets dropped, forward 0 (counters in the JSON), so the refusals are the kernel's, not application checks. Positive control: allowlisted name resolves via the gate, TCP 443 connects, TLS `GET /api/v3/time` returns 200.

Not done (UNVERIFIED): Docker Desktop/VM restart and Windows reboot (would interrupt all other projects and the M2 database), container-escape testing, a hypervisor-level (VM) boundary. OKX: the gate refuses the sinkholed answer, so OKX is never reachable and nothing was bypassed.
