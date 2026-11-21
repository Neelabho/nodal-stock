"""Tiny .env loader (no dependency). Real environment variables always win. The .env file is git-ignored; never commit secrets."""
from __future__ import annotations
import os
from pathlib import Path


def load_env(path: Path | None = None) -> None:
    p = path or (Path(__file__).resolve().parents[2] / ".env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
