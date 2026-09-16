from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..db import Database


@dataclass
class GuiContext:
    config_path: Path
    db_path: Path

    def open_db(self) -> Database:
        return Database(self.db_path)
