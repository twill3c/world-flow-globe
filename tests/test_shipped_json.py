"""出荷 JSON が**規格どおりの JSON か**を検査する(SPEC.md G-23)。

## なぜ専用の検査が要るのか

Python の ``json`` は既定で ``NaN`` / ``Infinity`` を**読み書きできる**。
これは JSON の規格(RFC 8259)には無い拡張なので、**ブラウザは構文エラーで落ちる**。

つまり:

- ETL は書ける
- pytest は読める(だから全部緑になる)
- **画面だけが壊れる**

2026-09-08 に実際に踏んだ。``eez.geojson`` に ``"iso_ter1":NaN`` が 33 個入っており、
EEZ レイヤーを点けた瞬間にアプリ全体が落ちていた。pytest 94 件・vitest 43 件は
すべて緑で、出荷ビルドも通り、配信物の検査(ホスト・サイズ)も問題なしを返していた。

**この検査は `json.loads(..., parse_constant=...)` で NaN を拒否する。**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.config import CONFIDENCE_VALUES, ROOT

PUBLIC = ROOT / "public" / "data"


def _reject_constant(name: str):
    raise AssertionError(f"JSON の規格に無い定数が入っている: {name}")


def shipped_json_files() -> list[Path]:
    return sorted(p for p in PUBLIC.rglob("*") if p.suffix in (".json", ".geojson"))


@pytest.mark.parametrize("path", shipped_json_files(), ids=lambda p: p.name)
def test_t601_shipped_json_is_strict_json(path: Path):
    """``NaN`` / ``Infinity`` / ``-Infinity`` を含まないこと。"""
    text = path.read_text(encoding="utf-8")
    json.loads(text, parse_constant=_reject_constant)


def test_t601_the_check_actually_fires():
    """**陽性対照**: この検査が NaN を実際に落とせること(HC-041)。

    出典に NaN が無いときと、検査が働いていないときは、どちらも同じ緑を返す。
    """
    with pytest.raises(AssertionError, match="規格に無い定数"):
        json.loads('{"x": NaN}', parse_constant=_reject_constant)
    # 陰性対照: 正常な JSON は通る
    assert json.loads('{"x": null}', parse_constant=_reject_constant) == {"x": None}


def test_t601_scan_is_not_empty():
    files = shipped_json_files()
    assert len(files) >= 6, f"走査対象が {len(files)} 件しかない(この検査が働いていない)"


@pytest.mark.parametrize("path", shipped_json_files(), ids=lambda p: p.name)
def test_t602_shipped_json_is_utf8_without_bom(path: Path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} に BOM がある"
    raw.decode("utf-8")


def test_t603_every_confidence_value_in_shipped_data_is_known():
    """G-05。出荷物のどこにも、綴りの違う confidence が無いこと。"""
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("confidence") and isinstance(v, str):
                    seen.add(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for path in shipped_json_files():
        walk(json.loads(path.read_text(encoding="utf-8")))
    assert seen, "confidence を一つも見つけられていない(この検査が働いていない)"
    unknown = seen - set(CONFIDENCE_VALUES)
    assert not unknown, f"未知の confidence: {sorted(unknown)}"


def test_t604_layers_json_agrees_with_the_artifacts():
    """G-05。`layers.json` が宣言した confidence と、成果物の実値が一致すること。"""
    layers = json.loads((PUBLIC / "layers.json").read_text(encoding="utf-8"))
    checked = 0
    for layer in layers["layers"]:
        rel = layer.get("path")
        if not layer["available"] or not rel:
            continue
        doc = json.loads((PUBLIC / rel).read_text(encoding="utf-8"))
        actual = doc.get("confidence")
        if actual is None:
            continue
        # PORT_THROUGHPUT は ports.geojson を読むが、宣言するのは統計側の階級。
        if layer["id"] == "PORT_THROUGHPUT":
            assert doc["outflows_confidence"] == layer["confidence"]
        else:
            assert actual == layer["confidence"], (
                f"{layer['id']}: 宣言 {layer['confidence']} / 実値 {actual}"
            )
        checked += 1
    assert checked >= 4, f"突き合わせたレイヤーが {checked} 件しかない"


def test_t605_unavailable_layers_state_a_reason():
    """F-15。出せないレイヤーは、必ず理由を持つ。"""
    layers = json.loads((PUBLIC / "layers.json").read_text(encoding="utf-8"))
    unavailable = [x for x in layers["layers"] if not x["available"]]
    assert unavailable, "出せないレイヤーが 1 つも無い(この検査が働いていない)"
    for layer in unavailable:
        assert layer.get("unavailable_reason"), f"{layer['id']} に理由が無い"
        assert len(layer["unavailable_reason"]) > 20, f"{layer['id']} の理由が短すぎる"
