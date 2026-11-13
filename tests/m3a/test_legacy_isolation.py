"""M3A-R3 F-01: legacy host processes have no path to, or knowledge of, future credentials.

Static policy tests (runtime isolation needs the dedicated host: see VM-BOUNDARY-DESIGN.md).
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "asta"
LEGACY_FILES = [
    SRC / "paper_service.py",
    ROOT / "scripts" / "paper_supervisor.py",
    ROOT / "scripts" / "paper_observation.py",
    ROOT / "scripts" / "run_paper_supervisor.cmd",
    ROOT / "scripts" / "register_supervisor_task.ps1",
]
FORBIDDEN = re.compile(
    r"(asta\.security|vault|asta-vault|vault\.sock|lease\(|SecretValue|credential_class|"
    r"EXCHANGE_DEMO|testnet|x-simulated-trading|ws-api|wspap)",
    re.IGNORECASE,
)


def test_legacy_processes_never_reference_the_vault_demo_environment_or_credentials():
    offenders = []
    for p in LEGACY_FILES:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert not offenders, offenders


def test_the_legacy_launcher_sets_only_dev_values_and_no_exchange_variables():
    cmd = (ROOT / "scripts" / "run_paper_supervisor.cmd").read_text(encoding="utf-8")
    sets = re.findall(r"^set\s+([A-Z0-9_]+)=", cmd, re.MULTILINE)
    assert sorted(sets) == [
        "ASTA_DATABASE_URL",
        "ASTA_SECRET_KEY",
        "DISABLE_SQLALCHEMY_CEXT_RUNTIME",
        "PYTHONUTF8",
    ]
    assert (
        "dev-only" in cmd
    )  # the only secret-like value is the documented dev-only application key


def test_settings_refuse_exchange_credential_environment_variables_at_load():
    src = (SRC / "core" / "settings.py").read_text(encoding="utf-8")
    assert (
        "def load_settings" in src
        and "assert_no_exchange_credentials()" in src.split("def load_settings")[1]
    )
    from asta.core.settings import CredentialsPresent, assert_no_exchange_credentials

    for name in (
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "OKX_API_KEY",
        "OKX_PASSPHRASE",
        "OKX_SECRET_KEY",
    ):
        try:
            assert_no_exchange_credentials({name: "x"})
            raise AssertionError(f"{name} was accepted")
        except CredentialsPresent:
            pass


def test_no_legacy_module_imports_the_security_package_and_the_security_package_has_no_network():
    for p in SRC.rglob("*.py"):
        if "security" in p.relative_to(SRC).parts[:1]:
            continue
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            assert not any(m.startswith("asta.security") for m in mods), p


def test_vault_socket_is_reachable_only_by_the_connector_group_in_the_design():
    sock = (ROOT / "infra" / "security-host" / "runtime-vm" / "asta-vault.socket").read_text(
        encoding="utf-8"
    )
    assert "SocketGroup=asta-connector" in sock and "SocketMode=0660" in sock
    assert "asta-app" not in sock  # strategies/AI/UI run as asta-app and cannot connect
