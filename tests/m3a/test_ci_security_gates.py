"""The hosted CI definition keeps the security checks mandatory (static checks; CI itself has never run)."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github/workflows/ci.yml").read_text()


def test_every_mandatory_job_exists_and_the_aggregate_requires_all_of_them():
    for job in ("backend", "security", "egress-boundary", "web", "required"):
        assert re.search(rf"^  {re.escape(job)}:", CI, re.M), job
    assert "needs: [backend, security, egress-boundary, web]" in CI
    for job in ("backend", "security", "egress-boundary", "web"):
        assert f'needs.{job}.result }}}}" = success' in CI, job


def test_the_security_checks_are_present_pinned_and_not_optional():
    for needed in (
        "tests/live_lock",
        "tests/orders/test_gateway_boundary.py",
        "verify_frozen_evidence.py",
        "review_manifest.py --verify",
        "pip-audit",
        "secret_scan.py",
        "gitleaks/gitleaks-action",
        'GITLEAKS_VERSION: "8.30.1"',
        "verify_boundary.py",
        "ci_skip_guard.py",
        "pnpm audit",
    ):
        assert needed in CI, needed
    assert "continue-on-error" not in CI
    assert "|| true" not in CI and "if: false" not in CI


def test_least_privilege_defaults():
    assert re.search(r"^permissions:\n  contents: read", CI, re.M)
    assert CI.count("persist-credentials: false") >= 4
    assert "secrets." not in CI.replace("secrets.GITHUB_TOKEN", "")  # no other secret is referenced
    assert (
        "pull_request_target" not in CI
        and "write" not in re.findall(r"permissions:[^\n]*\n(?:  \S+: \w+\n)+", CI)[0]
    )


def test_the_skip_guard_rejects_an_unreviewed_skip_and_accepts_the_reviewed_ones(tmp_path):
    ok = tmp_path / "ok.log"
    ok.write_text(
        "SKIPPED [3] tests/contracts/test_live_public.py:30: set ASTA_RUN_NETWORK_TESTS=1\nSKIPPED [1] tests/m3a/test_provisioning_artifacts.py:134: PowerShell not available\n"
    )
    bad = tmp_path / "bad.log"
    bad.write_text("SKIPPED [2] tests/m3a/test_security_host_config.py:131: Docker not available\n")

    def run(p):
        return subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "scripts/ci_skip_guard.py"), str(p)],
            capture_output=True,
            text=True,
        )  # noqa: S603

    assert run(ok).returncode == 0
    r = run(bad)
    assert r.returncode == 1 and "Docker not available" in r.stdout
