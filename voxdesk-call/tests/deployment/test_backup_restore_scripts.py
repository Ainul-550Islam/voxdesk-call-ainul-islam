"""Backup, restore, and DR drill script verification (Step 5 R5)."""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [
    REPO_ROOT / "scripts" / "backup.sh",
    REPO_ROOT / "scripts" / "restore.sh",
    REPO_ROOT / "scripts" / "dr_drill.sh",
]


def test_backup_restore_and_dr_scripts_pass_shellcheck() -> None:
    for script in SCRIPTS:
        assert script.is_file(), f"Missing script: {script}"
    shellcheck_bin = shutil.which("shellcheck")
    if shellcheck_bin is None:
        pytest.skip("shellcheck binary not installed in test environment")
    proc = subprocess.run(
        [shellcheck_bin, *(str(p) for p in SCRIPTS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"shellcheck failed:\n{proc.stdout}\n{proc.stderr}"


def test_backup_and_restore_roundtrip_against_postgresql(tmp_path: Path) -> None:
    if shutil.which("pg_dump") is None or shutil.which("psql") is None:
        pytest.skip("postgresql-client (pg_dump/psql) not installed in test environment")

    pg_host = os.environ.get("PGHOST", "127.0.0.1")
    pg_port = os.environ.get("PGPORT", "5432")
    pg_user = os.environ.get("POSTGRES_USER", "voxdesk")
    pg_pass = os.environ.get("PGPASSWORD", "voxdesk")
    env = {
        **os.environ,
        "PGHOST": pg_host,
        "PGPORT": pg_port,
        "POSTGRES_USER": pg_user,
        "PGPASSWORD": pg_pass,
    }

    # Probe whether local PostgreSQL server is reachable
    probe = subprocess.run(
        ["psql", f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/postgres", "-Atc", "SELECT 1;"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(f"PostgreSQL server not reachable at {pg_host}:{pg_port}: {probe.stderr.strip()}")

    suffix = uuid.uuid4().hex[:8]
    src_db = f"voxdesk_bkp_src_{suffix}"
    dst_db = f"voxdesk_bkp_dst_{suffix}"
    admin_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/postgres"
    src_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{src_db}"
    dst_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{dst_db}"

    try:
        subprocess.run(["psql", admin_url, "-v", "ON_ERROR_STOP=1", "-c", f"CREATE DATABASE {src_db};"], check=True)
        subprocess.run(["psql", admin_url, "-v", "ON_ERROR_STOP=1", "-c", f"CREATE DATABASE {dst_db};"], check=True)
        subprocess.run(
            [
                "psql",
                src_url,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "CREATE TABLE dr_probe (id SERIAL PRIMARY KEY, label TEXT NOT NULL); "
                "INSERT INTO dr_probe (label) VALUES ('alpha'), ('beta'), ('gamma');",
            ],
            check=True,
        )

        # 1. Run scripts/backup.sh
        backup_env = {**env, "POSTGRES_DB": src_db}
        bkp = subprocess.run(
            ["sh", str(REPO_ROOT / "scripts" / "backup.sh"), str(tmp_path)],
            env=backup_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert bkp.returncode == 0, f"backup.sh failed:\n{bkp.stdout}\n{bkp.stderr}"
        dumps = list(tmp_path.glob("voxdesk-*.dump"))
        assert len(dumps) == 1 and dumps[0].stat().st_size > 0

        # 2. Run scripts/restore.sh into dst_db
        restore_env = {**env, "POSTGRES_DB": src_db, "RESTORE_TARGET_DB": dst_db}
        rst = subprocess.run(
            ["sh", str(REPO_ROOT / "scripts" / "restore.sh"), str(dumps[0])],
            env=restore_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert rst.returncode == 0, f"restore.sh failed:\n{rst.stdout}\n{rst.stderr}"

        # 3. Verify row count & payload parity
        rows_out = subprocess.run(
            ["psql", dst_url, "-Atc", "SELECT string_agg(label, ',' ORDER BY id) FROM dr_probe;"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert rows_out.stdout.strip() == "alpha,beta,gamma"
    finally:
        subprocess.run(["psql", admin_url, "-c", f"DROP DATABASE IF EXISTS {src_db} WITH (FORCE);"], capture_output=True)
        subprocess.run(["psql", admin_url, "-c", f"DROP DATABASE IF EXISTS {dst_db} WITH (FORCE);"], capture_output=True)
