from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class BackupResult:
    source: Path
    destination: Path
    copied: bool


def backup_file(source: str, backup_dir: str = "data/backups") -> BackupResult:
    source_path = Path(source)
    destination_dir = Path(backup_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / f"{source_path.stem}_{timestamp}{source_path.suffix}"
    if not source_path.exists():
        return BackupResult(source=source_path, destination=destination, copied=False)
    shutil.copy2(source_path, destination)
    return BackupResult(source=source_path, destination=destination, copied=True)

