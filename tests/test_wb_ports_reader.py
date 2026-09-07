"""SRC-004 読み取り器の検査。

TEST_SPEC.md: T-002 / T-003 / T-004(対応要求 F-03, G-04)

期待値の出所:
- 「LOCODE が一意でない」「衝突は 19 件」は **2026-09-07 の実測**。
  ただし後述のとおり、件数そのものを期待値に固定するのは 1 ケースだけに留め、
  残りは「集合の性質」で書く(HC-016 — 外部データの数はデータが動くと壊れる)。
- 「畳んでよいのは運搬フィールドが一致するときだけ」は SPEC.md §7 G-04(HC-200)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from etl.io.wb_ports import (
    TRAILING_SENTINEL,
    PortSourceError,
    read_wb_ports,
    strip_trailing_sentinel,
)

RAW_PORTS = Path(__file__).resolve().parent.parent / "data" / "raw" / "worldbank" / "attributed_ports.geojson"

pytestmark = pytest.mark.filterwarnings("ignore")


def _feature(locode: str, name: str, lon: float, lat: float, **over) -> dict:
    props = {
        "Country": "Testland",
        "Function": "1-------",
        "LOCODE": locode,
        "Name": name,
        "NameWoDiac": name,
        "Status": "AI",
        "outflows": 1000.0,
    }
    props.update(over)
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def _fc(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _write(tmp_path: Path, doc: dict, *, sentinel: bool = False, encoding: str = "utf-8") -> Path:
    text = json.dumps(doc, ensure_ascii=False)
    if sentinel:
        text += "\n" + TRAILING_SENTINEL
    p = tmp_path / "ports.geojson"
    p.write_bytes(text.encode(encoding))
    return p


# --------------------------------------------------------------------------
# T-003 末尾ゴミの扱い(陽性対照と陰性対照を対で置く — HC-041)
# --------------------------------------------------------------------------


def test_t003_sentinel_is_stripped_only_at_end():
    """末尾にあるときだけ落とす。本文中の同じ文字列は落とさない。"""
    assert strip_trailing_sentinel('{"a":1}\n' + TRAILING_SENTINEL).strip() == '{"a":1}'
    # 陰性対照: 末尾に無ければ触らない
    body = '{"a":"' + TRAILING_SENTINEL + '"}'
    assert strip_trailing_sentinel(body) == body


def test_t003_reader_handles_sentinel_and_absence_of_sentinel(tmp_path):
    doc = _fc([_feature("AAAAA", "Alpha", 1.0, 2.0)])
    with_sent = read_wb_ports(_write(tmp_path / "a", doc, sentinel=True) if False else _write(tmp_path, doc, sentinel=True))
    assert [r.locode for r in with_sent.records] == ["AAAAA"]
    # 陰性対照: ゴミが無い(将来出典が直った)正常な JSON も読める
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    clean = read_wb_ports(_write(clean_dir, doc, sentinel=False))
    assert [r.locode for r in clean.records] == ["AAAAA"]


def test_t003_cp1252_is_decoded(tmp_path):
    """UTF-8 で読めないバイト列は cp1252 として読む。"""
    doc = _fc([_feature("ARBHI", "Bahía Blanca", -62.3, -38.7)])
    p = _write(tmp_path, doc, sentinel=True, encoding="cp1252")
    assert p.read_bytes().find(b"\xed") > 0, "この対照が意味を持つには 0xED が実際に入っていること"
    got = read_wb_ports(p)
    assert got.records[0].name == "Bahía Blanca"


# --------------------------------------------------------------------------
# T-002 主キーの衝突(G-04 / HC-200)
# --------------------------------------------------------------------------


def test_t002_identical_duplicates_are_collapsed(tmp_path):
    doc = _fc(
        [
            _feature("FITKU", "Åbo (Turku)", 22.2833, 60.45),
            _feature("FITKU", "Turku (Åbo)", 22.2833, 60.45),
            _feature("SGSIN", "Singapore", 103.84, 1.26),
        ]
    )
    res = read_wb_ports(_write(tmp_path, doc))
    assert res.raw_feature_count == 3
    assert [r.locode for r in res.records] == ["FITKU", "SGSIN"]
    assert res.collapsed_count == 1
    assert res.collapsed_locodes == ["FITKU"]
    # 名称は決定論的に選ばれる(辞書順で先に来るもの)
    assert res.records[0].name == "Turku (Åbo)"


def test_t002_positive_control_differing_coords_raise(tmp_path):
    """陽性対照: 座標が違う「衝突」は畳まず例外にする。"""
    doc = _fc(
        [
            _feature("XXXXX", "One", 10.0, 20.0),
            _feature("XXXXX", "Two", 11.0, 20.0),
        ]
    )
    with pytest.raises(PortSourceError, match="座標が一致しない"):
        read_wb_ports(_write(tmp_path, doc))


def test_t002_positive_control_differing_payload_raises(tmp_path):
    """陽性対照: 運搬フィールド(outflows)が違う「衝突」も例外にする。"""
    doc = _fc(
        [
            _feature("YYYYY", "One", 10.0, 20.0),
            _feature("YYYYY", "Two", 10.0, 20.0, outflows=2000.0),
        ]
    )
    with pytest.raises(PortSourceError, match="運搬フィールドが一致しない"):
        read_wb_ports(_write(tmp_path, doc))


def test_t002_malformed_locode_raises(tmp_path):
    doc = _fc([_feature("ABC", "Short", 1.0, 2.0)])
    with pytest.raises(PortSourceError, match="UN/LOCODE"):
        read_wb_ports(_write(tmp_path, doc))


def test_t002_out_of_range_coordinates_raise(tmp_path):
    doc = _fc([_feature("ZZZZZ", "Bad", 200.0, 2.0)])
    with pytest.raises(PortSourceError, match="座標が範囲外"):
        read_wb_ports(_write(tmp_path, doc))


# --------------------------------------------------------------------------
# T-004 実データに当てる(不変量で書く。件数の定数は 1 つだけ)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RAW_PORTS.exists(), reason="raw が未取得")
def test_t004_real_source_reads_and_is_unique():
    res = read_wb_ports(RAW_PORTS)
    locodes = [r.locode for r in res.records]
    # 不変量: 出力の主キーは一意
    assert len(set(locodes)) == len(locodes)
    # 不変量: 取りこぼしが無い(畳んだ分だけ減る)
    assert res.raw_feature_count - res.collapsed_count == len(res.records)
    # 不変量: 畳んだ LOCODE は出力に必ず 1 つずつ残っている
    assert set(res.collapsed_locodes) <= set(locodes)
    # 不変量: 座標が全件で範囲内(読み取り器が通した以上は自明だが、走査対象が空でないことも押さえる)
    assert len(res.records) > 100
    for r in res.records:
        assert -180.0 <= r.lon <= 180.0 and -90.0 <= r.lat <= 90.0


@pytest.mark.skipif(not RAW_PORTS.exists(), reason="raw が未取得")
def test_t004_real_source_collision_count_matches_measurement():
    """実測 2026-09-07: 856 地物 / 837 種 / 衝突 19 件。

    この 1 ケースだけは定数で書く。出典が更新されたら落ちてよい —— 落ちたときに
    「出典が変わった」と気づけることのほうが、黙って通ることより価値がある。
    """
    res = read_wb_ports(RAW_PORTS)
    assert res.raw_feature_count == 856
    assert len(res.records) == 837
    assert res.collapsed_count == 19
