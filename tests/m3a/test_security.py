"""M3A-2/C: credential isolation + demo environment binding. SYNTHETIC secrets only."""

import ast
import copy
import json
import logging
import pickle
import re
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from asta.core.environment import Environment
from asta.security import redaction
from asta.security.binding import (
    DEMO_BINDINGS,
    BindingViolation,
    EnvironmentBinding,
    Venue,
    bind,
    canonical,
    verify_privilege_config,
)
from asta.security.redaction import MASK, REGISTRY, RedactingFilter, SecretValue, scrub_exception
from asta.security.vault import (
    AccessDenied,
    CredentialClass,
    EncryptedFileBackend,
    ForbiddenPermission,
    MemoryBackend,
    Permission,
    Principal,
    PrincipalKind,
    Vault,
    VaultError,
    VaultIdentity,
    synthetic_secret,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "asta"
CONNECTOR = Principal(PrincipalKind.CONNECTOR, "binance-demo-connector")


@pytest.fixture(autouse=True)
def _clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def make_vault(backend=None):
    v = Vault(backend or MemoryBackend())
    key, sec = synthetic_secret(), synthetic_secret()
    v.store(
        VaultIdentity.BINANCE_SPOT_TESTNET,
        CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
        {Permission.READ, Permission.SPOT_TRADE},
        {"key": SecretValue(key), "secret": SecretValue(sec)},
        "test-admin",
    )
    return v, key, sec


# ------------------------------------------------------------------ permissions / classification
@pytest.mark.parametrize(
    "bad",
    [
        Permission.WITHDRAW,
        Permission.TRANSFER,
        Permission.MARGIN,
        Permission.FUTURES,
        Permission.BORROW,
        Permission.SUBACCOUNT,
        Permission.ACCOUNT_MANAGEMENT,
    ],
)
def test_withdrawal_transfer_margin_and_other_permissions_are_refused(bad):
    v = Vault(MemoryBackend())
    with pytest.raises(ForbiddenPermission):
        v.store(
            VaultIdentity.OKX_DEMO,
            CredentialClass.DEMO_OKX,
            {Permission.READ, bad},
            {"k": SecretValue(synthetic_secret())},
            "admin",
        )
    assert v.meta(VaultIdentity.OKX_DEMO) is None
    assert any(r["action"] == "store.refused" for r in v.log.rows)


def test_empty_permissions_and_class_mismatch_are_refused():
    v = Vault(MemoryBackend())
    with pytest.raises(ForbiddenPermission):
        v.store(
            VaultIdentity.OKX_DEMO,
            CredentialClass.DEMO_OKX,
            set(),
            {"k": SecretValue("x" * 8)},
            "a",
        )
    with pytest.raises(VaultError):
        v.store(
            VaultIdentity.OKX_DEMO,
            CredentialClass.DEMO_BINANCE_SPOT_TESTNET,  # wrong class for the identity
            {Permission.READ},
            {"k": SecretValue("x" * 8)},
            "a",
        )


def test_there_is_no_production_credential_class():
    assert {c.value for c in CredentialClass} == {"DEMO_BINANCE_SPOT_TESTNET", "DEMO_OKX"}
    assert {i.name for i in VaultIdentity} == {"BINANCE_SPOT_TESTNET", "OKX_DEMO"}


# ------------------------------------------------------------------ retrieval restrictions
@pytest.mark.parametrize(
    "kind",
    [
        PrincipalKind.STRATEGY,
        PrincipalKind.AI_MODULE,
        PrincipalKind.ANALYTICS,
        PrincipalKind.OPERATOR,
        PrincipalKind.UI,
    ],
)
def test_strategies_ai_ui_and_operators_can_never_lease(kind):
    v, _k, _s = make_vault()
    with pytest.raises(AccessDenied):
        v.lease(Principal(kind, "x"), VaultIdentity.BINANCE_SPOT_TESTNET, "test")
    assert v.log.rows[-1]["action"] == "lease.denied"


def test_connector_without_a_grant_is_denied_and_with_a_grant_gets_a_masked_lease():
    v, key, sec = make_vault()
    with pytest.raises(AccessDenied):
        v.lease(CONNECTOR, VaultIdentity.BINANCE_SPOT_TESTNET, "demo order")
    v.grant(CONNECTOR.name, VaultIdentity.BINANCE_SPOT_TESTNET, "a" * 64)
    lease = v.lease(CONNECTOR, VaultIdentity.BINANCE_SPOT_TESTNET, "demo order")
    assert lease.parts["key"].reveal() == key and lease.parts["secret"].reveal() == sec
    assert lease.credential_class is CredentialClass.DEMO_BINANCE_SPOT_TESTNET
    assert lease.permissions == {Permission.READ, Permission.SPOT_TRADE}
    # a connector for another identity is still refused
    with pytest.raises(AccessDenied):
        v.lease(CONNECTOR, VaultIdentity.OKX_DEMO, "x")


def test_grant_requires_a_verified_digest():
    v, *_ = make_vault()
    with pytest.raises(AccessDenied):
        v.grant("c", VaultIdentity.BINANCE_SPOT_TESTNET, "")


def test_revoked_and_rotated_credentials():
    v, key, _ = make_vault()
    v.grant(CONNECTOR.name, VaultIdentity.BINANCE_SPOT_TESTNET, "d" * 64)
    new = synthetic_secret()
    meta = v.store(
        VaultIdentity.BINANCE_SPOT_TESTNET,
        CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
        {Permission.READ},
        {"key": SecretValue(new)},
        "admin",
    )
    assert meta.version == 2
    assert v.lease(CONNECTOR, VaultIdentity.BINANCE_SPOT_TESTNET, "p").parts["key"].reveal() == new
    v.revoke(VaultIdentity.BINANCE_SPOT_TESTNET, "admin")
    with pytest.raises(AccessDenied):
        v.lease(CONNECTOR, VaultIdentity.BINANCE_SPOT_TESTNET, "p")
    assert all(x == "" for x in v.backend.get(VaultIdentity.BINANCE_SPOT_TESTNET)["parts"].values())


def test_expired_credential_cannot_be_leased():
    from datetime import timedelta

    v = Vault(MemoryBackend(), ttl=timedelta(seconds=-1))
    v.store(
        VaultIdentity.OKX_DEMO,
        CredentialClass.DEMO_OKX,
        {Permission.READ},
        {"k": SecretValue(synthetic_secret())},
        "a",
    )
    v.grant("c", VaultIdentity.OKX_DEMO, "d" * 64)
    with pytest.raises(AccessDenied):
        v.lease(Principal(PrincipalKind.CONNECTOR, "c"), VaultIdentity.OKX_DEMO, "p")


# ------------------------------------------------------------------ audit of access
def test_access_log_is_hash_chained_tamper_evident_and_secret_free():
    v, key, sec = make_vault()
    v.grant(CONNECTOR.name, VaultIdentity.BINANCE_SPOT_TESTNET, "e" * 64)
    v.lease(CONNECTOR, VaultIdentity.BINANCE_SPOT_TESTNET, "purpose")
    with pytest.raises(AccessDenied):
        v.lease(Principal(PrincipalKind.STRATEGY, "s"), VaultIdentity.BINANCE_SPOT_TESTNET, "p")
    assert v.log.verify() == (True, None)
    blob = json.dumps(v.log.rows, default=str)
    assert key not in blob and sec not in blob
    v.log.rows[1]["payload"]["identity"] = "tampered"
    assert v.log.verify()[0] is False


# ------------------------------------------------------------------ secret masking
def test_secret_value_never_leaks_through_str_repr_format_json_pickle_copy():
    s = SecretValue("super-secret-value-123")
    for rendered in (
        str(s),
        repr(s),
        f"{s}",
        f"{s!r}",
        "%s" % s,  # noqa: UP031
        f"{s}",
        str([s]),
        str({"k": s}),
    ):
        assert "super-secret-value-123" not in rendered
    with pytest.raises(TypeError):
        json.dumps({"k": s})
    with pytest.raises(TypeError):
        pickle.dumps(s)
    with pytest.raises(TypeError):
        copy.copy(s)
    with pytest.raises(TypeError):
        copy.deepcopy(s)
    with pytest.raises(AttributeError):
        s._v = "x"
    assert s != "super-secret-value-123"


def test_logs_errors_and_crash_reports_are_scrubbed(caplog):
    v, key, sec = make_vault()  # registers the secrets
    log = logging.getLogger("m3a.test")
    log.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="m3a.test"):
        log.info("auth failed for key %s secret=%s", key, sec)
        try:
            raise RuntimeError(f"request rejected, header={key}")
        except RuntimeError as exc:
            log.exception("boom %s", exc)
            report = scrub_exception(exc)
    text = caplog.text + report
    assert key not in text and sec not in text and MASK in text
    # unregistered generic shapes are scrubbed too
    assert "abcdef123456" not in redaction.REGISTRY.scrub("password=abcdef123456")


def test_secret_not_in_traceback_locals_or_repr_of_lease():
    v, key, _ = make_vault()
    v.grant(CONNECTOR.name, VaultIdentity.BINANCE_SPOT_TESTNET, "f" * 64)
    lease = v.lease(CONNECTOR, VaultIdentity.BINANCE_SPOT_TESTNET, "p")
    assert key not in repr(lease) and key not in str(lease)
    try:
        raise ValueError(repr(lease))
    except ValueError:
        assert key not in traceback.format_exc()


# ------------------------------------------------------------------ encryption at rest
def test_file_backend_encrypts_at_rest_detects_tampering_and_needs_the_key(tmp_path):
    key = EncryptedFileBackend.generate_key()
    v, k, s = make_vault(EncryptedFileBackend(tmp_path, key))
    files = list(tmp_path.glob("*.enc"))
    assert len(files) == 1
    raw = files[0].read_bytes()
    assert k.encode() not in raw and s.encode() not in raw and b"SYNTH" not in raw
    # wrong key
    with pytest.raises(VaultError):
        EncryptedFileBackend(tmp_path, EncryptedFileBackend.generate_key()).get(
            VaultIdentity.BINANCE_SPOT_TESTNET
        )
    # tampering
    files[0].write_bytes(raw[:-3] + b"AAA")
    with pytest.raises(VaultError):
        EncryptedFileBackend(tmp_path, key).get(VaultIdentity.BINANCE_SPOT_TESTNET)


# ------------------------------------------------------------------ environment binding
def test_only_demo_environment_bindings_exist():
    for env in (Environment.PAPER, Environment.BACKTEST):
        for venue in Venue:
            with pytest.raises(BindingViolation):
                bind(venue, env)
    with pytest.raises(BindingViolation):
        EnvironmentBinding(
            Venue.BINANCE, Environment.PAPER, "x.example", (), CredentialClass.DEMO_OKX
        )
    assert not [e for e in Environment if e.name.upper() == "LIVE"]
    b = bind(Venue.BINANCE, Environment.EXCHANGE_DEMO)
    with pytest.raises(FrozenInstanceError):
        b.rest_host = "api.binance.com"  # type: ignore[misc]


@pytest.mark.parametrize(
    "path",
    [
        "https://api.binance.com/api/v3/order",
        "//evil.example/x",
        "/\\evil.example",
        "relative/path",
        "/api/../../etc/passwd",
        "/a@evil.example/x",
        "/x#frag",
        "/x y",
        "/x\r\nHost: evil",
    ],
)
def test_url_override_and_host_switching_are_rejected(path):
    for b in DEMO_BINDINGS.values():
        with pytest.raises(BindingViolation):
            b.url(path)


def test_bound_urls_always_use_the_bound_host():
    assert (
        bind(Venue.BINANCE, Environment.EXCHANGE_DEMO).url("/api/v3/time")
        == "https://testnet.binance.vision/api/v3/time"
    )
    okx = bind(Venue.OKX, Environment.EXCHANGE_DEMO)
    assert okx.url("/api/v5/public/time").startswith("https://www.okx.com/")
    assert ("x-simulated-trading", "1") in okx.required_headers  # shared host: header decides
    with pytest.raises(BindingViolation):
        okx.ws_url(
            "ws.okx.com", "/ws/v5/private"
        )  # production WS host is not part of the demo binding
    assert okx.ws_url("wspap.okx.com", "/ws/v5/private").startswith("wss://wspap.okx.com")


def test_redirects_unexpected_hosts_and_credential_class_mismatch_are_rejected():
    b = bind(Venue.BINANCE, Environment.EXCHANGE_DEMO)
    with pytest.raises(BindingViolation):
        b.reject_redirect(302, "https://api.binance.com/")
    b.reject_redirect(200, None)
    with pytest.raises(BindingViolation):
        b.check_response("https://api.binance.com/api/v3/time")
    b.check_response("https://testnet.binance.vision/api/v3/time")
    v = Vault(MemoryBackend())
    v.store(
        VaultIdentity.OKX_DEMO,
        CredentialClass.DEMO_OKX,
        {Permission.READ},
        {"k": SecretValue(synthetic_secret())},
        "a",
    )
    v.grant("c", VaultIdentity.OKX_DEMO, "d" * 64)
    lease = v.lease(Principal(PrincipalKind.CONNECTOR, "c"), VaultIdentity.OKX_DEMO, "p")
    with pytest.raises(BindingViolation):
        b.check_credential(lease)  # OKX demo credential on a Binance binding
    bind(Venue.OKX, Environment.EXCHANGE_DEMO).check_credential(lease)


# ------------------------------------------------------------------ signed, reviewed configuration
def _keys(n):
    ks = [Ed25519PrivateKey.generate() for _ in range(n)]
    pub = {
        f"rev{i}": k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        for i, k in enumerate(ks)
    }
    return ks, pub


CFG = {"environment": "EXCHANGE_DEMO", "venue": "BINANCE", "permissions": ["READ", "SPOT_TRADE"]}


def test_privilege_config_needs_two_distinct_valid_reviewer_signatures():
    ks, pub = _keys(3)
    sigs = {f"rev{i}": ks[i].sign(canonical(CFG)) for i in range(2)}
    assert len(verify_privilege_config(CFG, sigs, pub)) == 64
    with pytest.raises(BindingViolation):
        verify_privilege_config(CFG, {"rev0": sigs["rev0"]}, pub)  # one signer
    with pytest.raises(BindingViolation):
        verify_privilege_config(
            CFG, {"rev0": sigs["rev0"], "rev0b": sigs["rev0"]}, pub
        )  # same key twice
    tampered = {**CFG, "permissions": ["READ", "SPOT_TRADE", "WITHDRAW"]}
    with pytest.raises(BindingViolation):
        verify_privilege_config(tampered, sigs, pub)  # signatures do not cover the change
    withdraw = {**CFG, "permissions": ["READ", "WITHDRAW"]}
    s2 = {f"rev{i}": ks[i].sign(canonical(withdraw)) for i in range(2)}
    with pytest.raises(BindingViolation):
        verify_privilege_config(withdraw, s2, pub)  # validly signed but forbidden content
    prod = {**CFG, "environment": "LIVE"}
    s3 = {f"rev{i}": ks[i].sign(canonical(prod)) for i in range(2)}
    with pytest.raises(BindingViolation):
        verify_privilege_config(prod, s3, pub)
    outsider = Ed25519PrivateKey.generate()
    with pytest.raises(BindingViolation):
        verify_privilege_config(
            CFG, {"rev0": sigs["rev0"], "rev1": outsider.sign(canonical(CFG))}, pub
        )


# ------------------------------------------------------------------ isolation from the rest of ASTA
ISOLATED = (
    "research",
    "risk",
    "paper",
    "market_data",
    "exchanges",
    "core",
    "audit",
    "db",
    "api",
    "auth",
)


def test_nothing_outside_the_security_package_imports_it():
    offenders = []
    for p in SRC.rglob("*.py"):
        top = p.relative_to(SRC).parts[0]
        if top == "security":
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            if any(m.startswith("asta.security") for m in mods):
                offenders.append(str(p.relative_to(SRC)))
    assert not offenders, offenders


def test_security_package_imports_only_core_audit_and_itself():
    bad = []
    for p in (SRC / "security").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                parts = m.split(".")
                if parts[0] == "asta" and parts[1] not in ("core", "audit", "security"):
                    bad.append(f"{p.name}: {m}")
                if parts[0] in ("httpx", "websockets", "requests", "aiohttp", "socket", "urllib3"):
                    bad.append(f"{p.name}: network import {m}")
    assert not bad, bad


# ------------------------------------------------------------------ no credentials anywhere
REAL_KEY_SHAPES = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9]{64}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})(?![A-Za-z0-9])"
)


def test_no_exchange_key_shaped_literals_or_env_files_in_the_repo():
    root = SRC.parent.parent
    for forbidden in (".env", ".env.local", ".env.production"):
        assert not (root / forbidden).exists(), forbidden
    offenders = []
    for base in (SRC, root / "apps" / "web" / "src", root / "scripts", root / "infra"):
        for p in base.rglob("*"):
            if p.suffix not in (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".mjs",
                ".json",
                ".yml",
                ".yaml",
                ".cmd",
                ".ps1",
            ):
                continue
            if "node_modules" in p.parts or p.name == "pnpm-lock.yaml":
                continue
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for m in REAL_KEY_SHAPES.finditer(txt):
                if (
                    re.fullmatch(r"[a-f0-9-]{36}", m.group(1))
                    or "sha" in txt[max(0, m.start() - 20) : m.start()].lower()
                ):
                    continue  # uuids / hashes
                offenders.append(f"{p.relative_to(root)}: {m.group(1)[:12]}...")
    assert not offenders, offenders[:10]


def test_frontend_never_references_credentials_or_browser_secret_storage():
    web = SRC.parent.parent / "apps" / "web" / "src"
    bad = []
    for p in web.rglob("*"):
        if p.suffix in (".ts", ".tsx"):
            txt = p.read_text(encoding="utf-8")
            if re.search(
                r"api[_-]?secret|apikey|secret[_-]?key|passphrase|localStorage\.setItem\([^)]*(key|secret|token)",
                txt,
                re.I,
            ):
                bad.append(str(p.relative_to(web)))
    assert not bad, bad
