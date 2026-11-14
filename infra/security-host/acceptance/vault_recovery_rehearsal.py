"""Synthetic-only vault BACKUP/RECOVERY rehearsal (M3-B-R5 workstream D). Stdlib + the project's mock vault.

    python3 infra/security-host/acceptance/vault_recovery_rehearsal.py [report.json]

Rehearses, with RANDOM SYNTHETIC values only (the mock vault refuses anything else), the recovery path a real
backend must also have: back up the encrypted vault files, lose the vault directory, restore from the backup and
the separately kept key-encryption key (KEK), and prove: records and versions survive, a granted connector can lease
again, a WRONG KEK and a TAMPERED file are refused, a restored vault can rotate and revoke (revocation destroys
the material), and no plaintext secret is present in any backup or vault file. No exchange credential exists or is
created; nothing leaves the temporary directory, which is removed at the end. This rehearses the PROCEDURE with the
mock backend; it does not prove a real backend (OpenBao), custody, or the VM-side restore.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from asta.security.redaction import SecretValue
from asta.security.vault import (
    AccessDenied,
    CredentialClass,
    EncryptedFileBackend,
    Permission,
    Principal,
    PrincipalKind,
    Vault,
    VaultError,
    VaultIdentity,
    synthetic_secret,
)

R: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    print(("PASS " if ok else "FAIL ") + name)


def exposed(paths: list[Path], secrets: list[str]) -> list[str]:
    hits = []
    for base in paths:
        for f in base.rglob("*"):
            if f.is_file():
                blob = f.read_bytes()
                hits += [f"{f.name}:{s[:8]}" for s in secrets if s.encode() in blob]
                if b"SYNTH-" in blob:
                    hits.append(f"{f.name}:SYNTH-marker")
    return hits


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    work = Path(tempfile.mkdtemp(prefix="asta-vault-recovery-"))
    try:
        vdir, backup, offline = work / "vault", work / "backup", work / "kek-offline"
        offline.mkdir(mode=0o700)
        kek = EncryptedFileBackend.generate_key()
        (offline / "kek.key").write_bytes(
            kek
        )  # the KEK lives ONLY here (simulated offline custody)
        secrets = {"key": synthetic_secret(), "secret": synthetic_secret()}
        okx = {"key": synthetic_secret(), "secret": synthetic_secret(), "extra": synthetic_secret()}
        allsecrets = list(secrets.values()) + list(okx.values())
        vault = Vault(EncryptedFileBackend(vdir, kek))
        vault.store(VaultIdentity.BINANCE_SPOT_TESTNET, CredentialClass.DEMO_BINANCE_SPOT_TESTNET, {Permission.READ, Permission.SPOT_TRADE}, {k: SecretValue(v) for k, v in secrets.items()}, "rehearsal")  # fmt: skip
        vault.store(VaultIdentity.OKX_DEMO, CredentialClass.DEMO_OKX, {Permission.READ}, {k: SecretValue(v) for k, v in okx.items()}, "rehearsal")  # fmt: skip
        before = {
            i: vault.meta(i) for i in (VaultIdentity.BINANCE_SPOT_TESTNET, VaultIdentity.OKX_DEMO)
        }
        check("1 two synthetic identities stored", all(before.values()))

        shutil.copytree(vdir, backup)  # 2 back up the ENCRYPTED files only (never the KEK)
        check(
            "2 backup contains no plaintext secret and no KEK",
            not exposed([backup], allsecrets) and not list(backup.glob("kek*")),
        )

        shutil.rmtree(vdir)  # 3 disaster: the vault directory is lost
        check("3 vault directory lost", not vdir.exists())

        shutil.copytree(backup, vdir)  # 4 restore with the offline KEK
        restored = Vault(EncryptedFileBackend(vdir, (offline / "kek.key").read_bytes()))
        after = {i: restored.meta(i) for i in before}
        check(
            "4 records, versions and permissions survive the restore",
            all(
                after[i]
                and before[i]
                and after[i].version == before[i].version
                and after[i].permissions == before[i].permissions
                for i in before
            ),
        )  # type: ignore[union-attr]
        conn = Principal(PrincipalKind.CONNECTOR, "rehearsal-connector")
        restored.grant(conn.name, VaultIdentity.BINANCE_SPOT_TESTNET, "0" * 64)
        lease = restored.lease(conn, VaultIdentity.BINANCE_SPOT_TESTNET, "rehearsal")
        check(
            "5 a granted connector can lease again and receives the same synthetic values",
            [v.reveal() for v in lease.parts.values()] == list(secrets.values()),
        )

        try:
            Vault(EncryptedFileBackend(vdir, EncryptedFileBackend.generate_key())).meta(
                VaultIdentity.OKX_DEMO
            )
            check("6 a wrong KEK is refused", False)
        except VaultError:
            check("6 a wrong KEK is refused", True)

        victim = next(vdir.glob("*.enc"))
        raw = bytearray(victim.read_bytes())
        raw[len(raw) // 2] ^= 1
        victim.write_bytes(bytes(raw))
        try:
            Vault(EncryptedFileBackend(vdir, kek)).meta(VaultIdentity.BINANCE_SPOT_TESTNET)
            Vault(EncryptedFileBackend(vdir, kek)).meta(VaultIdentity.OKX_DEMO)
            check("7 a tampered vault file is detected", False)
        except VaultError:
            check("7 a tampered vault file is detected", True)
        shutil.copy(next(backup.glob(victim.name)), victim)  # restore the good copy again

        fresh = Vault(EncryptedFileBackend(vdir, kek))
        v2 = fresh.store(VaultIdentity.BINANCE_SPOT_TESTNET, CredentialClass.DEMO_BINANCE_SPOT_TESTNET, {Permission.READ}, {"key": SecretValue(synthetic_secret())}, "rotation")  # fmt: skip
        check(
            "8 a restored vault can rotate (version increments)",
            v2.version == before[VaultIdentity.BINANCE_SPOT_TESTNET].version + 1,
        )  # type: ignore[union-attr]
        fresh.revoke(VaultIdentity.OKX_DEMO, "rehearsal")
        fresh.grant(conn.name, VaultIdentity.OKX_DEMO, "0" * 64)
        try:
            fresh.lease(conn, VaultIdentity.OKX_DEMO, "after revoke")
            check("9 a revoked identity can never be leased", False)
        except AccessDenied:
            check("9 a revoked identity can never be leased", True)
        rec = EncryptedFileBackend(vdir, kek).get(VaultIdentity.OKX_DEMO)
        check(
            "10 revocation destroyed the material",
            rec is not None and all(v == "" for v in rec["parts"].values()),
        )
        check(
            "11 no plaintext secret in the live vault or the backup at the end",
            not exposed([vdir, backup], allsecrets),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failed = [r for r in R if r["result"] != "PASS"]
    report = {
        "rehearsal": "synthetic-only, mock backend",
        "real_credentials": False,
        "checks": R,
        "passed": len(R) - len(failed),
        "failed": len(failed),
    }
    if out:
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{report['passed']}/{len(R)} passed; temp directory removed: {not work.exists()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
