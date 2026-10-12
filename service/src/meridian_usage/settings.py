"""Paths and runtime settings, overridable through environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    starter_kit: Path
    environment: str
    db_path: Path

    @property
    def data_dir(self) -> Path:
        return self.starter_kit / "data"

    @property
    def environments_dir(self) -> Path:
        return self.starter_kit / "environments"

    @classmethod
    def from_env(cls) -> Settings:
        kit = Path(os.environ.get("MERIDIAN_STARTER_KIT", REPO_ROOT / "starter-kit"))
        env = os.environ.get("MERIDIAN_ENV", "dev")
        db = Path(os.environ.get("MERIDIAN_DB", REPO_ROOT / "var" / f"meridian-{env}.sqlite3"))
        return cls(starter_kit=kit, environment=env, db_path=db)
