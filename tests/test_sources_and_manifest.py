"""データ源台帳とマニフェストの検査。

TEST_SPEC.md: T-001 / T-005 / T-006(対応要求 N-05, F-14, G-13)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.config import CONFIDENCE_VALUES, ROOT
from etl.download.manifest import sha256_of, write_manifest
from etl.download.sources import BY_ID, SOURCES, write_source_registry

SPEC = ROOT / "SPEC.md"


# --------------------------------------------------------------------------
# T-001 SHA-256 とマニフェスト(G-13)
# --------------------------------------------------------------------------


def test_t001_sha256_is_computed_from_the_file_and_is_stable(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"world flow globe")
    first = sha256_of(p)
    assert first == sha256_of(p), "同じファイルからは同じ値が出る"
    # 陽性対照: 1 バイト変えたら必ず変わる
    p.write_bytes(b"world flow globf")
    assert sha256_of(p) != first


def test_t001_manifest_roundtrip(tmp_path, monkeypatch):
    import etl.download.manifest as m

    monkeypatch.setattr(m, "MANIFESTS", tmp_path)
    src = tmp_path / "src.bin"
    src.write_bytes(b"raw bytes")
    out = tmp_path / "out.json"
    out.write_text("{}", encoding="utf-8")
    dest = m.write_manifest(
        dataset_id="SRC-999",
        provider="Test",
        dataset="Test dataset",
        source_filename="src.bin",
        source_path=src,
        retrieved_at="2026-09-07T00:00:00Z",
        output="public/data/x.json",
        output_path=out,
        records=3,
    )
    doc = json.loads(dest.read_text(encoding="utf-8"))
    assert doc["sha256"] == sha256_of(src)
    assert doc["output_sha256"] == sha256_of(out)
    assert doc["bytes"] == src.stat().st_size
    assert doc["records"] == 3


# --------------------------------------------------------------------------
# T-005 出典台帳(F-14 / 原典 §74・§77)
# --------------------------------------------------------------------------


def test_t005_every_source_declares_a_license_and_a_landing_url():
    assert len(SOURCES) >= 7
    for s in SOURCES:
        assert s.license and s.license_url, f"{s.id} にライセンス表示が無い"
        assert s.landing_url.startswith("https://"), f"{s.id} の出典ページが https でない"


def test_t005_unavailable_sources_state_a_reason():
    """取得できない出典は、必ず理由を持つ。空欄で出荷しない(原典 §3.2 null != 0)。"""
    for s in SOURCES:
        if s.download_url is None:
            assert s.unavailable_reason, f"{s.id} に未取得の理由が無い"
        else:
            assert s.unavailable_reason is None, f"{s.id} は取得できるのに理由が付いている"


def test_t005_source_ids_in_spec_and_registry_agree():
    """SPEC.md §5 の表に載る SRC-xxx と台帳の ID が一致する(SPEC-DRIFT の防止)。"""
    import re

    spec_ids = set(re.findall(r"SRC-\d{3}", SPEC.read_text(encoding="utf-8")))
    assert spec_ids == set(BY_ID), f"SPEC と台帳の差: {spec_ids ^ set(BY_ID)}"


def test_t005_registry_json_is_generated_not_handwritten(tmp_path):
    dest = write_source_registry(tmp_path / "source_registry.json")
    doc = json.loads(dest.read_text(encoding="utf-8"))
    assert {s["id"] for s in doc["sources"]} == set(BY_ID)
    # 出荷物は raw の置き場所を漏らさない
    assert all("raw_relpath" not in s for s in doc["sources"])


# --------------------------------------------------------------------------
# T-006 意味区分の enum(G-05)
# --------------------------------------------------------------------------


def test_t006_confidence_enum_matches_spec_table():
    """SPEC.md §4 の表に並ぶ confidence と、コード側の enum が一致する。"""
    import re

    text = SPEC.read_text(encoding="utf-8")
    section = text.split("## 4. データモデルの意味区分")[1].split("## 5.")[0]
    in_table = set(re.findall(r"^\| `([A-Z]+)` \|", section, flags=re.M))
    assert in_table == set(CONFIDENCE_VALUES), f"差: {in_table ^ set(CONFIDENCE_VALUES)}"


# --------------------------------------------------------------------------
# T-007 ゲートの対応(HC-157): SPEC のゲート表の全 G-xx が
#       TEST_SPEC のケース表から参照されているか、SPEC 側に未実装と書かれている
# --------------------------------------------------------------------------


def test_t007_every_gate_is_referenced_or_declared_unimplemented():
    import re

    spec = SPEC.read_text(encoding="utf-8")
    test_spec = (ROOT / "TEST_SPEC.md").read_text(encoding="utf-8")
    gate_section = spec.split("## 7. 品質ゲート")[1].split("## 8.")[0]
    gates = set(re.findall(r"^\| (G-\d{2}) \|", gate_section, flags=re.M))
    assert gates, "ゲート表が読めていない(この検査自体が働いていない)"
    referenced = set(re.findall(r"G-\d{2}", test_spec))
    unimplemented = set(re.findall(r"(G-\d{2})[^\n]*未実装", spec))
    orphans = gates - referenced - unimplemented
    assert not orphans, f"どのケースからも参照されないゲート: {sorted(orphans)}"
