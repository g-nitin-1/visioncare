"""Restore deterministic VisionCare demo database and telemetry state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from filelock import FileLock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "mock_db.json"
DEFAULT_SEED_PATH = PROJECT_ROOT / "backend" / "mock_db.seed.json"
DEFAULT_LOG_PATH = PROJECT_ROOT / "data" / "audit_logs.jsonl"
REQUIRED_DB_FIELDS = ("products", "warranties", "rmas", "escalations")


def reset_demo_state(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    seed_path: str | Path = DEFAULT_SEED_PATH,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> dict[str, Any]:
    """Atomically restore the seed DB and remove runtime telemetry."""
    resolved_db_path = Path(db_path)
    resolved_seed_path = Path(seed_path)
    resolved_log_path = Path(log_path)
    seed = _load_seed(resolved_seed_path)
    seed_bytes = resolved_seed_path.read_bytes()

    resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{resolved_db_path}.lock"):
        _write_bytes_atomically(resolved_db_path, seed_bytes)

    telemetry_removed = False
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(f"{resolved_log_path}.lock"):
        if resolved_log_path.exists():
            resolved_log_path.unlink()
            telemetry_removed = True

    return {
        "db_path": str(resolved_db_path),
        "seed_path": str(resolved_seed_path),
        "log_path": str(resolved_log_path),
        "telemetry_removed": telemetry_removed,
        "products": len(seed["products"]),
        "warranties": len(seed["warranties"]),
        "rmas": len(seed["rmas"]),
        "escalations": len(seed["escalations"]),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore backend/mock_db.json from the canonical seed and remove "
            "the runtime JSONL telemetry log. Stop Streamlit before running."
        )
    )
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, type=Path)
    parser.add_argument("--seed-path", default=DEFAULT_SEED_PATH, type=Path)
    parser.add_argument("--log-path", default=DEFAULT_LOG_PATH, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = reset_demo_state(
        db_path=args.db_path,
        seed_path=args.seed_path,
        log_path=args.log_path,
    )
    print(f"Restored demo database: {result['db_path']}")
    print(
        "Seed records: "
        f"{result['products']} product, "
        f"{result['warranties']} warranties, "
        f"{result['rmas']} RMAs, "
        f"{result['escalations']} escalations"
    )
    if result["telemetry_removed"]:
        print(f"Removed runtime telemetry: {result['log_path']}")
    else:
        print(f"Runtime telemetry already empty: {result['log_path']}")
    return 0


def _load_seed(seed_path: Path) -> dict[str, Any]:
    with seed_path.open("r", encoding="utf-8") as handle:
        seed = json.load(handle)

    if not isinstance(seed, dict):
        raise ValueError("demo seed must be a JSON object")
    for field_name in REQUIRED_DB_FIELDS:
        if not isinstance(seed.get(field_name), dict):
            raise ValueError(f"demo seed is missing object field: {field_name}")
    if seed["rmas"] or seed["escalations"]:
        raise ValueError("demo seed must contain empty rmas and escalations")
    return seed


def _write_bytes_atomically(path: Path, payload: bytes) -> None:
    temporary_path = path.with_name(f"{path.name}.tmp")
    with temporary_path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)


if __name__ == "__main__":
    raise SystemExit(main())
