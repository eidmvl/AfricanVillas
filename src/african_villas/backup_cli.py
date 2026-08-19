from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def create_backup(
    data_dir: str | Path,
    backup_dir: str | Path,
    *,
    retention_days: int = 30,
) -> Path:
    source_root = Path(data_dir).expanduser().resolve()
    destination_root = Path(backup_dir).expanduser().resolve()
    database_path = source_root / "african_villas.db"
    if not database_path.is_file():
        raise FileNotFoundError(f"Database not found: {database_path}")

    destination_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_base = destination_root / f"african-villas-{timestamp}"
    stage = Path(tempfile.mkdtemp(prefix=".backup-stage-", dir=destination_root))
    try:
        with sqlite3.connect(database_path) as source, sqlite3.connect(
            stage / database_path.name
        ) as destination:
            source.backup(destination)

        for item in source_root.iterdir():
            if item.name in {database_path.name, "tmp", "cache"}:
                continue
            if item.name.endswith(("-wal", "-shm")):
                continue
            target = stage / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            elif item.is_file():
                shutil.copy2(item, target)

        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "source": str(source_root),
            "database": database_path.name,
            "retention_days": retention_days,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        archive = Path(shutil.make_archive(str(archive_base), "zip", stage))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    if retention_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        for candidate in destination_root.glob("african-villas-*.zip"):
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
            if modified < cutoff:
                candidate.unlink()
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a consistent African Villas backup")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--retention-days", type=int, default=30)
    args = parser.parse_args()
    archive = create_backup(
        args.data_dir,
        args.backup_dir,
        retention_days=max(0, args.retention_days),
    )
    print(archive)


if __name__ == "__main__":
    main()
