"""データマニフェスト(原典 §73 / SPEC.md §7 G-13)。

raw と主要生成物について SHA-256 を残し、再実行で一致することを検算できるようにする。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from etl.config import ETL_VERSION, MANIFESTS


def sha256_of(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            block = fp.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def md5_of(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fp:
        while True:
            block = fp.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_manifest(
    dataset_id: str,
    provider: str,
    dataset: str,
    source_filename: str,
    source_path: str | Path,
    retrieved_at: str,
    output: str | None = None,
    output_path: str | Path | None = None,
    records: int = 0,
    notes: dict | None = None,
) -> Path:
    """1 データセット 1 マニフェストを ``data/manifests/<dataset_id>.json`` へ書く。"""
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    doc = {
        "dataset_id": dataset_id,
        "provider": provider,
        "dataset": dataset,
        "source_filename": source_filename,
        "retrieved_at": retrieved_at,
        "sha256": sha256_of(source_path),
        "bytes": Path(source_path).stat().st_size,
        "output": output,
        "output_sha256": sha256_of(output_path) if output_path else None,
        "records": records,
        "etl_version": ETL_VERSION,
    }
    if notes:
        doc["notes"] = notes
    dest = MANIFESTS / f"{dataset_id}.json"
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def read_manifest(dataset_id: str) -> dict:
    return json.loads((MANIFESTS / f"{dataset_id}.json").read_text(encoding="utf-8"))
