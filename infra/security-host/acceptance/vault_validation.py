"""Synthetic-secret vault validation (M3A-R3). Uses RANDOM synthetic values only - never a real key.

    python infra/security-host/acceptance/vault_validation.py out.json [workdir]

Runs on any machine (here: the development PC; on the runtime VM at acceptance time). It exercises
the vault contract end to end and then SEARCHES every byte it produced (vault files, log output,
captured stdout/stderr, exception text, process arguments/environment where readable) for the
synthetic secrets. POSIX-only checks (file modes, core-dump limit, /proc) are reported as
SKIPPED on Windows, never as passed.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

from asta.security.redaction import REGISTRY, RedactingFilter, SecretValue, scrub_exception
from asta.security.vault import (
    AccessDenied,
    CredentialClass,
    EncryptedFileBackend,
    ForbiddenPermission,
    Permission,
    Principal,
    PrincipalKind,
    Vault,
    VaultIdentity,
    synthetic_secret,
)

R: list[dict[str, object]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    R.append(
        {
            "check": name,
            "result": "SKIPPED" if ok is None else ("PASS" if ok else "FAIL"),
            "detail": detail,
        }
    )


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("vault_validation.json")
    work = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="asta-vault-val-"))
    )
    work.mkdir(parents=True, exist_ok=True)
    vdir = work / "vault"
    secrets = {"key": synthetic_secret(), "secret": synthetic_secret()}
    okx = {"key": synthetic_secret(), "secret": synthetic_secret(), "extra": synthetic_secret()}
    allsecrets = list(secrets.values()) + list(okx.values())
    if os.name == "posix":
        os.umask(0o077)
    be = EncryptedFileBackend(vdir, EncryptedFileBackend.generate_key())
    vault = Vault(be)
    cap = io.StringIO()
    handler = logging.StreamHandler(cap)
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("vault-validation")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)

    # lifecycle
    vault.store(VaultIdentity.BINANCE_SPOT_TESTNET, CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
                {Permission.READ, Permission.SPOT_TRADE}, {k: SecretValue(v) for k, v in secrets.items()}, "validator")  # fmt: skip
    vault.store(VaultIdentity.OKX_DEMO, CredentialClass.DEMO_OKX, {Permission.READ},
                {k: SecretValue(v) for k, v in okx.items()}, "validator")  # fmt: skip
    check("store two separate identities", vault.meta(VaultIdentity.BINANCE_SPOT_TESTNET) is not None
          and vault.meta(VaultIdentity.OKX_DEMO) is not None)  # fmt: skip
    try:
        vault.store(VaultIdentity.OKX_DEMO, CredentialClass.DEMO_OKX, {Permission.READ, Permission.WITHDRAW},
                    {"k": SecretValue(synthetic_secret())}, "validator")  # fmt: skip
        check("withdrawal permission refused", False)
    except ForbiddenPermission:
        check("withdrawal permission refused", True)
    for kind in (
        PrincipalKind.STRATEGY,
        PrincipalKind.AI_MODULE,
        PrincipalKind.UI,
        PrincipalKind.OPERATOR,
    ):
        try:
            vault.lease(Principal(kind, "x"), VaultIdentity.BINANCE_SPOT_TESTNET, "p")
            check(f"{kind} denied", False)
        except AccessDenied:
            check(f"{kind} denied", True)
    conn = Principal(PrincipalKind.CONNECTOR, "validator-connector")
    try:
        vault.lease(conn, VaultIdentity.BINANCE_SPOT_TESTNET, "p")
        check("connector without grant denied", False)
    except AccessDenied:
        check("connector without grant denied", True)
    vault.grant(conn.name, VaultIdentity.BINANCE_SPOT_TESTNET, "0" * 64)
    lease = vault.lease(conn, VaultIdentity.BINANCE_SPOT_TESTNET, "validation")
    check(
        "granted connector leases (values are masked objects)",
        all(isinstance(v, SecretValue) for v in lease.parts.values()),
    )

    # exposure: logging, str/repr, exceptions, crash text
    log.info("lease %s %r %s", lease, lease.parts, lease.parts["key"])
    for s in allsecrets:
        log.error("auth failed using %s", s)  # even a raw secret in a log argument must be scrubbed
    try:
        raise RuntimeError(f"upstream rejected key {secrets['key']} / {okx['secret']}")
    except RuntimeError as exc:
        crash = scrub_exception(exc)
        log.exception("boom")
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        print(lease)
        print(repr(lease))
    blobs: dict[str, bytes] = {
        "log": cap.getvalue().encode(),
        "crash": crash.encode(),
        "stdout": stdout.getvalue().encode(),
        "stderr": stderr.getvalue().encode(),
        "access_log": json.dumps(vault.log.rows, default=str).encode(),
    }
    for f in vdir.rglob("*"):
        if f.is_file():
            blobs[f"vault_file:{f.name}"] = f.read_bytes()
    for name, data in blobs.items():
        leaked = [i for i, s in enumerate(allsecrets) if s.encode() in data]
        check(f"no synthetic secret in {name}", not leaked, f"leaked={leaked}" if leaked else "")
    check("vault files exist and are not plaintext", any(k.startswith("vault_file") for k in blobs))
    check("access log chain verifies", vault.log.verify() == (True, None))

    # revoke + rotate
    vault.revoke(VaultIdentity.BINANCE_SPOT_TESTNET, "validator")
    try:
        vault.lease(conn, VaultIdentity.BINANCE_SPOT_TESTNET, "p")
        check("revoked credential cannot be leased", False)
    except AccessDenied:
        check("revoked credential cannot be leased", True)
    rec = be.get(VaultIdentity.BINANCE_SPOT_TESTNET)
    check(
        "revoke destroys the material",
        rec is not None and all(v == "" for v in rec["parts"].values()),
    )

    # process-level exposure (POSIX only)
    if os.name == "posix":
        env = (
            Path("/proc/self/environ").read_bytes() if Path("/proc/self/environ").exists() else b""
        )
        cmd = (
            Path("/proc/self/cmdline").read_bytes() if Path("/proc/self/cmdline").exists() else b""
        )
        check(
            "no secret in own environment/cmdline",
            not any(s.encode() in env + cmd for s in allsecrets),
        )
        modes = {f.name: stat.S_IMODE(f.stat().st_mode) for f in vdir.iterdir()}
        check(
            "vault files are not group/world accessible",
            all(m & 0o077 == 0 for m in modes.values()),
            str(modes),
        )
        check(
            "vault directory is 0700",
            stat.S_IMODE(vdir.stat().st_mode) & 0o077 == 0,
            oct(stat.S_IMODE(vdir.stat().st_mode)),
        )
        import resource

        soft, _hard = resource.getrlimit(resource.RLIMIT_CORE)
        check("core dumps disabled (RLIMIT_CORE == 0)", soft == 0, f"soft={soft}")
    else:
        for n in ("no secret in own environment/cmdline", "vault files not group/world accessible",
                  "vault directory 0700", "core dumps disabled"):  # fmt: skip
            check(n, None, "POSIX-only check; must be run on the Linux runtime VM")

    REGISTRY.clear()
    res = {
        "synthetic_secrets_generated": len(allsecrets),
        "platform": sys.platform,
        "checks": R,
        "failed": [c for c in R if c["result"] == "FAIL"],
        "skipped": [c["check"] for c in R if c["result"] == "SKIPPED"],
    }
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(
        json.dumps({"failed": len(res["failed"]), "skipped": len(res["skipped"]), "checks": len(R)})
    )  # type: ignore[arg-type]
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
