import sqlite3
import zipfile

from african_villas.backup_cli import create_backup


def test_backup_uses_sqlite_snapshot_and_keeps_documents(tmp_path) -> None:
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "african_villas.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('ready')")
    document = data_dir / "projects" / "1" / "documents" / "source.pdf"
    document.parent.mkdir(parents=True)
    document.write_bytes(b"document")
    cache_file = data_dir / "cache" / "discard.txt"
    cache_file.parent.mkdir()
    cache_file.write_text("cache", encoding="utf-8")

    archive = create_backup(data_dir, backup_dir)

    with zipfile.ZipFile(archive) as bundle:
        assert "african_villas.db" in bundle.namelist()
        assert "projects/1/documents/source.pdf" in bundle.namelist()
        assert "manifest.json" in bundle.namelist()
        assert "cache/discard.txt" not in bundle.namelist()
        extracted_db = tmp_path / "restored.db"
        extracted_db.write_bytes(bundle.read("african_villas.db"))
    with sqlite3.connect(extracted_db) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("ready",)
