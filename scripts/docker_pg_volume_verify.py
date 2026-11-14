#!/usr/bin/env python3
"""Non-destructive logical-restore check of PostgreSQL data volumes (M3-B-R5 workstream B).

    python3 scripts/docker_pg_volume_verify.py VOLUME:IMAGE [VOLUME:IMAGE ...] [--out report.json]

For every VOLUME it: (1) COPIES it into a scratch volume (the source is mounted read-only and never
started, written or modified); (2) starts a throw-away server of IMAGE on the copy, with ``--network none``;
(3) dumps each database logically; (4) restores the dump into a second, empty throw-away server of the same
IMAGE; (5) compares table counts and exact row counts between the copy and the restore; (6) removes ONLY the
scratch containers/volumes it created (names start with ``r5``; never a prune). Output: counts only, no data.
Requires a running Docker daemon. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time

COUNT = (
    "create function pg_temp.total_rows() returns bigint language plpgsql as $f$ declare r record; n bigint; "
    "t bigint := 0; begin for r in select table_schema s, table_name x from information_schema.tables "
    "where table_schema not in ('pg_catalog','information_schema') and table_type='BASE TABLE' loop "
    "execute format('select count(*) from %I.%I', r.s, r.x) into n; t := t + n; end loop; return t; end $f$; "
    "select (select count(*) from information_schema.tables where table_schema not in "
    "('pg_catalog','information_schema') and table_type='BASE TABLE') || '|' || pg_temp.total_rows()"
)


def d(*a: str, inp: bytes | None = None, check: bool = False) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["docker", *a], input=inp, capture_output=True, check=check)  # noqa: S603,S607


def wait_ready(c: str, secs: int = 90) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if d("exec", c, "pg_isready", "-q").returncode == 0:
            time.sleep(1)
            return d("exec", c, "pg_isready", "-q").returncode == 0
        time.sleep(1)
    return False


def psql(c: str, user: str, db: str, sql: str) -> str:
    r = d("exec", c, "psql", "-U", user, "-d", db, "-Atq", "-c", sql)
    return r.stdout.decode().strip() if r.returncode == 0 else "ERR:" + r.stderr.decode()[:160]


def find_superuser(scratch: str, image: str) -> str:
    """Name the cluster's superuser role through single-user mode on the COPY (server not running)."""
    sql = b"select rolname from pg_roles where rolsuper order by oid;\n"
    cmd = "g=$(command -v gosu || command -v su-exec); exec $g postgres postgres --single -D /var/lib/postgresql/data postgres"
    r = d(
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "-v",
        f"{scratch}:/var/lib/postgresql/data",
        image,
        "sh",
        "-c",
        cmd,
        inp=sql,
    )
    m = re.search(r'rolname = "([^"]+)"', r.stdout.decode(errors="replace"))
    return m.group(1) if m else ""


def verify(vol: str, image: str, n: int) -> dict[str, object]:
    out: dict[str, object] = {"volume": vol, "image": image}
    scratch, c1, c2 = f"r5s-{n}", f"r5pg-{n}", f"r5pgr-{n}"
    try:
        ver = (
            d("run", "--rm", "-v", f"{vol}:/d:ro", "alpine:3.20", "cat", "/d/PG_VERSION")
            .stdout.decode()
            .strip()
        )
        out["pg_version"] = ver
        if d("volume", "create", scratch).returncode:
            return out | {"result": "FAIL", "why": "cannot create scratch volume"}
        cp = d(
            "run",
            "--rm",
            "-v",
            f"{vol}:/from:ro",
            "-v",
            f"{scratch}:/to",
            "alpine:3.20",
            "sh",
            "-c",
            "cp -a /from/. /to/",
        )
        if cp.returncode:
            return out | {"result": "FAIL", "why": "copy failed"}
        user = find_superuser(scratch, image)
        if not user:
            return out | {"result": "FAIL", "why": "could not determine the superuser role"}
        d(
            "run",
            "-d",
            "--name",
            c1,
            "--network",
            "none",
            "-v",
            f"{scratch}:/var/lib/postgresql/data",
            image,
        )
        if not wait_ready(c1):
            return out | {
                "result": "FAIL",
                "why": "server on the copy did not start",
                "log": d("logs", "--tail", "3", c1).stderr.decode()[-200:],
            }
        dbs = [
            x
            for x in psql(
                c1,
                user,
                "postgres",
                "select datname from pg_database where not datistemplate and datname<>'postgres'",
            ).splitlines()
            if x
        ]
        out["databases"] = len(dbs)
        d(
            "run",
            "-d",
            "--name",
            c2,
            "--network",
            "none",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            image,
        )
        if not wait_ready(c2):
            return out | {"result": "FAIL", "why": "restore target did not start"}
        res = []
        for db in dbs:
            dump = d("exec", c1, "pg_dump", "-U", user, "-Fc", "-d", db)
            if dump.returncode:
                res.append(
                    {
                        "db": "?",
                        "result": "FAIL",
                        "why": "pg_dump failed " + dump.stderr.decode()[:100],
                    }
                )
                continue
            psql(c2, "postgres", "postgres", 'create database "r5target"')
            rs = d(
                "exec",
                "-i",
                c2,
                "pg_restore",
                "-U",
                "postgres",
                "-d",
                "r5target",
                "--no-owner",
                "--no-privileges",
                inp=dump.stdout,
            )
            src, dst = psql(c1, user, db, COUNT), psql(c2, "postgres", "r5target", COUNT)
            ok = rs.returncode == 0 and src == dst and not src.startswith("ERR")
            res.append(
                {
                    "dump_bytes": len(dump.stdout),
                    "restore_exit": rs.returncode,
                    "tables|rows_source": src,
                    "tables|rows_restored": dst,
                    "result": "PASS" if ok else "FAIL",
                    "restore_errors": rs.stderr.decode()[:120] if rs.returncode else "",
                }
            )
            psql(c2, "postgres", "postgres", 'drop database "r5target"')
        out["dbs"] = res
        out["result"] = (
            "PASS"
            if res and all(x["result"] == "PASS" for x in res)
            else ("EMPTY" if not res else "FAIL")
        )
        return out
    finally:
        for c in (c1, c2):
            d(
                "rm", "-f", "-v", c
            )  # -v: also remove the anonymous volume the image declares (no leftovers)
        d("volume", "rm", scratch)  # only the scratch volume this run created


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    outp = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if outp and outp in args:
        args.remove(outp)
    results = [verify(*a.split(":", 1), i) for i, a in enumerate(args, 1)]
    for r in results:
        print(json.dumps(r))
    if outp:
        open(outp, "w", encoding="utf-8").write(json.dumps(results, indent=1))
    return 0 if all(r.get("result") == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
