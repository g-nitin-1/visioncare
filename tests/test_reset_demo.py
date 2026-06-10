import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import reset_demo


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_committed_database_matches_canonical_seed():
    assert (
        reset_demo.DEFAULT_DB_PATH.read_bytes()
        == reset_demo.DEFAULT_SEED_PATH.read_bytes()
    )


def test_reset_restores_database_and_removes_telemetry(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"
    db_path.write_text(
        json.dumps(
            {
                "products": {"corrupted": {}},
                "warranties": {},
                "rmas": {"demo": {"rma_id": "RMA-9999"}},
                "escalations": {"demo": {"ticket_id": "ESC-9999"}},
            }
        ),
        encoding="utf-8",
    )
    log_path.write_text('{"event_type":"session_summary"}\n', encoding="utf-8")

    result = reset_demo.reset_demo_state(
        db_path=db_path,
        seed_path=reset_demo.DEFAULT_SEED_PATH,
        log_path=log_path,
    )

    assert db_path.read_bytes() == reset_demo.DEFAULT_SEED_PATH.read_bytes()
    assert not log_path.exists()
    assert result["telemetry_removed"] is True
    assert result["rmas"] == 0
    assert result["escalations"] == 0


def test_reset_is_idempotent_when_telemetry_is_missing(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"

    first = reset_demo.reset_demo_state(
        db_path=db_path,
        seed_path=reset_demo.DEFAULT_SEED_PATH,
        log_path=log_path,
    )
    second = reset_demo.reset_demo_state(
        db_path=db_path,
        seed_path=reset_demo.DEFAULT_SEED_PATH,
        log_path=log_path,
    )

    assert db_path.read_bytes() == reset_demo.DEFAULT_SEED_PATH.read_bytes()
    assert first["telemetry_removed"] is False
    assert second["telemetry_removed"] is False


def test_invalid_seed_is_rejected_without_changing_database(tmp_path):
    db_path = tmp_path / "mock_db.json"
    seed_path = tmp_path / "invalid-seed.json"
    log_path = tmp_path / "audit_logs.jsonl"
    original = {"products": {}, "warranties": {}, "rmas": {}, "escalations": {}}
    db_path.write_text(json.dumps(original), encoding="utf-8")
    seed_path.write_text(
        json.dumps(
            {
                "products": {},
                "warranties": {},
                "rmas": {"unexpected": {}},
                "escalations": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="empty rmas"):
        reset_demo.reset_demo_state(
            db_path=db_path,
            seed_path=seed_path,
            log_path=log_path,
        )

    assert read_json(db_path) == original


def test_reset_cli_supports_isolated_paths(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"
    log_path.write_text("runtime event\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/reset_demo.py",
            "--db-path",
            str(db_path),
            "--seed-path",
            str(reset_demo.DEFAULT_SEED_PATH),
            "--log-path",
            str(log_path),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Restored demo database" in completed.stdout
    assert db_path.read_bytes() == reset_demo.DEFAULT_SEED_PATH.read_bytes()
    assert not log_path.exists()
