"""Sauvegarde de la base (ENF-05) : copie COHÉRENTE même pendant que le système écrit (API de sauvegarde SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class BackupError(RuntimeError):
    pass


def make_backup(db_path: str | Path, dest_dir: str | Path, keep: int, now: datetime) -> Path:
    db_path, dest_dir = Path(db_path), Path(dest_dir)
    if not db_path.exists():
        raise BackupError(f"base introuvable : {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"orchestre-{now:%Y%m%dT%H%M%SZ}.db"
    if target.exists():
        raise BackupError(f"la sauvegarde existe déjà : {target}")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
        if copy.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise BackupError("la copie échoue au contrôle d'intégrité")
    except Exception:
        copy.close()
        target.unlink(missing_ok=True)
        raise
    finally:
        source.close()
        copy.close()
    for old in sorted(dest_dir.glob("orchestre-*.db"))[:-keep] if keep > 0 else []:
        old.unlink()
    return target
