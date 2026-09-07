"""ETL 共通の定数とパス。

SPEC.md §5(データ源)と §11(ID 規約)にトレースする。
"""

from __future__ import annotations

from pathlib import Path

ETL_VERSION = "3.2.0"
APP_VERSION = "0.1.0"

ROOT = Path(__file__).resolve().parent.parent

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
MANIFESTS = ROOT / "data" / "manifests"

PUBLIC_DATA = ROOT / "public" / "data"
PUBLIC_BASE = PUBLIC_DATA / "base"
PUBLIC_INFRA = PUBLIC_DATA / "infrastructure"
PUBLIC_ECONOMY = PUBLIC_DATA / "economy"
PUBLIC_SIMULATION = PUBLIC_DATA / "simulation"

# SPEC.md §4 — 出荷するすべての数と線が名乗る 6 区分。
CONFIDENCE_VALUES = (
    "OBSERVED",
    "STATISTICAL",
    "INFERRED",
    "ESTIMATED",
    "SIMULATED",
    "PREDICTED",
)

# SPEC.md §7 G-02 / 原典 §71
MAX_TOTAL_MB = 30.0
MAX_FILE_MB = 80.0


def ensure_dirs() -> None:
    for d in (
        RAW,
        PROCESSED,
        MANIFESTS,
        PUBLIC_BASE,
        PUBLIC_INFRA,
        PUBLIC_ECONOMY,
        PUBLIC_SIMULATION,
    ):
        d.mkdir(parents=True, exist_ok=True)
