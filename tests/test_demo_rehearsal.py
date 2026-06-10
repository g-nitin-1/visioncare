import json
from pathlib import Path

from scripts import rehearse_demo, reset_demo


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_scripted_demo_rehearsal_matches_visible_dashboard_actions(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"

    result = rehearse_demo.run_rehearsal(
        db_path=db_path,
        seed_path=reset_demo.DEFAULT_SEED_PATH,
        log_path=log_path,
    )
    db = read_json(db_path)

    assert result["troubleshooting"]["status"] == "troubleshooting"
    assert result["rma"]["status"] == "rma_open"
    assert result["rma"]["rma"]["rma_id"] == "RMA-0001"
    assert len(db["rmas"]) == 1
    assert result["metrics"]["total_completed_sessions"] == 1
    assert result["metrics"]["warranty_claim_count"] == 1
    assert result["metrics"]["open_rma_count"] == 1


def test_rehearsal_starts_from_clean_state_each_time(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"
    kwargs = {
        "db_path": db_path,
        "seed_path": reset_demo.DEFAULT_SEED_PATH,
        "log_path": log_path,
    }

    first = rehearse_demo.run_rehearsal(**kwargs)
    second = rehearse_demo.run_rehearsal(**kwargs)

    assert first["rma"]["rma"]["rma_id"] == "RMA-0001"
    assert second["rma"]["rma"]["rma_id"] == "RMA-0001"
    assert len(read_json(db_path)["rmas"]) == 1
    assert second["metrics"]["total_completed_sessions"] == 1
