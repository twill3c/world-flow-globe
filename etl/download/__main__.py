"""`python -m etl.download` — 出典を取得し、マニフェストと出典台帳を書く。

取得できない出典は**理由つきで飛ばす**。黙って空にしない(原典 §3.2 ``null != 0``)。
"""

from __future__ import annotations

import argparse
import json
import zipfile

from etl.config import ensure_dirs
from etl.download.manifest import utc_now_iso, write_manifest
from etl.download.sources import SOURCES, fetch, local_status, write_source_registry


def _verify_natural_earth_version(path, expected: str) -> str:
    """配布物自身が申告する版を読む(非循環のオラクル)。"""
    with zipfile.ZipFile(path) as z:
        got = z.read("ne_10m_admin_0_countries.VERSION.txt").decode("utf-8").strip()
    if got != expected:
        raise RuntimeError(f"Natural Earth の版が違う: expected={expected} got={got}")
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description="出典の取得とマニフェスト生成")
    ap.add_argument("--force", action="store_true", help="既にある raw も取り直す")
    args = ap.parse_args()

    ensure_dirs()
    retrieved_at = utc_now_iso()

    for src in SOURCES:
        if src.download_url is None:
            print(f"skip  {src.id}  — {src.unavailable_reason}")
            continue
        path = fetch(src, force=args.force)
        note: dict = {}
        if src.id == "SRC-001":
            note["version_txt"] = _verify_natural_earth_version(path, src.version or "")
        if src.expected_md5:
            note["md5_verified_against_source"] = src.expected_md5
        if src.notes:
            note["source_quirks"] = src.notes
        write_manifest(
            dataset_id=src.id,
            provider=src.provider,
            dataset=src.dataset,
            source_filename=path.name,
            source_path=path,
            retrieved_at=retrieved_at,
            notes=note or None,
        )
        print(f"ok    {src.id}  {path.stat().st_size:,} B  {path}")

    reg = write_source_registry()
    print(f"registry → {reg}")
    print(json.dumps(local_status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
