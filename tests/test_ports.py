"""港の変換の検査(SPEC.md F-03 / G-01 / G-04 / G-05 / G-18)。

期待値の出所:
- 主キーの一意性・衝突の畳み方は SPEC.md §7 G-04(HC-200)。
- 国コードの導出規則は UN/LOCODE の定義(先頭 2 文字が ISO 3166-1 alpha-2)。
- 吸着できない港の一覧は**実測**(2026-09-07)。件数は定数で書かず、
  「河川港・湖港であること」を人が確かめた台帳と突き合わせる。
"""

from __future__ import annotations

import json

import pytest

from etl.transform.nav_grid import RESOLUTION_DEG, cell_index
from etl.transform.ports import MAX_SNAP_KM
from etl.transform.ports import OUT as PORTS_OUT
from etl.transform.ports import haversine_km
from tests.test_nav_grid import unpack_mask
from etl.transform.nav_grid import OUT as NAV_OUT

#: 2026-09-07 に実測した「経路に使えない港」13 件と、その理由。
#: **一件ずつ実物を見て分類した。** すべて外洋から 0.5 度のグリッドでは
#: 届かない内陸水路の港である。件数だけでなく理由まで台帳に残すのは、
#: 将来 1 件増えたときに「また河川港か」「新しい種類の欠陥か」を区別するため。
EXPECTED_UNROUTABLE = {
    "ARROS": "パラナ川(ロサリオ)",
    "BRMAO": "アマゾン川(マナウス)",
    "BRMCP": "アマゾン川河口(サンタナ/マカパ)",
    "CAMTR": "セントローレンス川(モントリオール)",
    "CATOR": "五大湖(トロント)",
    "COLET": "アマゾン川(レティシア)",
    "PEIQT": "アマゾン川(イキトス)",
    "PYASU": "パラグアイ川(アスンシオン)",
    "RUDUD": "エニセイ川(ドゥジンカ)",
    "TRYAR": "イズミット湾の奥(ヤルムジャ)",
    "USCAV": "五大湖・エリー湖(クリーブランド)",
    "USPDP": "デラウェア川(フィラデルフィア)",
    "VEPLA": "オリノコ川(パルア)",
}


@pytest.fixture(scope="module")
def ports():
    if not PORTS_OUT.exists():
        pytest.skip("ports.geojson が未生成")
    return json.loads(PORTS_OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mask():
    if not NAV_OUT.exists():
        pytest.skip("nav_grid.json が未生成")
    return unpack_mask(json.loads(NAV_OUT.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# T-301 主キー(G-04)
# ---------------------------------------------------------------------------


def test_t301_port_ids_are_unique_and_well_formed(ports):
    ids = [f["properties"]["port_id"] for f in ports["features"]]
    assert len(ids) > 500, "走査対象が空でないこと"
    assert len(set(ids)) == len(ids)
    for pid, f in zip(ids, ports["features"]):
        assert pid == f"PORT_{f['properties']['unlocode']}"
        assert len(f["properties"]["unlocode"]) == 5


def test_t301_collapse_is_accounted_for(ports):
    """畳んだ件数が成果物から追えること(HC-200)。"""
    c = ports["collapsed_from_source"]
    assert c["raw_feature_count"] - c["collapsed_count"] == len(ports["features"])
    assert set(c["collapsed_locodes"]) <= {f["properties"]["unlocode"] for f in ports["features"]}


# ---------------------------------------------------------------------------
# T-302 国コード(推測で埋めない)
# ---------------------------------------------------------------------------


def test_t302_country_code_positive_control(ports):
    """規則が実際に効いていることを、既知の港で確かめる。

    陽性対照が無いと、全件 null でもこの検査は緑になる。
    """
    by = {f["properties"]["unlocode"]: f["properties"] for f in ports["features"]}
    known = {"SGSIN": ("SG", "SGP"), "NLRTM": ("NL", "NLD"), "JPYOK": ("JP", "JPN")}
    for locode, (a2, a3) in known.items():
        assert locode in by, f"{locode} が出典に無い(対照が成り立たない)"
        assert by[locode]["country_iso2"] == a2
        assert by[locode]["country_iso3"] == a3


def test_t302_unmapped_are_null_and_listed(ports):
    listed = set(ports["country_code"]["unmapped_locodes"])
    actual = {
        f["properties"]["unlocode"]
        for f in ports["features"]
        if f["properties"]["country_iso3"] is None
    }
    assert listed == actual, f"台帳と実体の差: {listed ^ actual}"
    # 引けなかったものが多すぎるなら、規則そのものを疑う
    assert len(actual) / len(ports["features"]) < 0.05


def test_t302_iso2_always_comes_from_the_locode(ports):
    for f in ports["features"]:
        p = f["properties"]
        assert p["country_iso2"] == p["unlocode"][:2]


# ---------------------------------------------------------------------------
# T-303 航行グリッドへの吸着(G-18)
# ---------------------------------------------------------------------------


def test_t303_routable_ports_snap_to_navigable_cells(ports, mask):
    checked = 0
    for f in ports["features"]:
        p = f["properties"]
        if not p["routable"]:
            continue
        i, j = p["grid_cell"]
        assert mask[i, j], f"{p['port_id']} が航行不能セル ({i},{j}) に吸着している"
        assert 0 <= p["snap_km"] <= MAX_SNAP_KM
        checked += 1
    assert checked > 500, "走査対象が空でないこと"


def test_t303_unroutable_ports_carry_nulls_not_zeros(ports):
    """``null`` を 0 に化けさせない(原典 §3.2)。"""
    for f in ports["features"]:
        p = f["properties"]
        if p["routable"]:
            continue
        assert p["grid_cell"] is None
        assert p["snap_km"] is None


def test_t303_unroutable_set_matches_the_reviewed_ledger(ports):
    """実測した 13 件と一致すること。**理由まで台帳にある件だけ**を許す。

    出典が更新されて増減したらここが落ちる。落ちたときに
    「また河川港か」「新しい種類の欠陥か」を人が見るための関門である。
    """
    actual = set(ports["snap"]["unroutable_locodes"])
    assert actual == set(EXPECTED_UNROUTABLE), (
        f"台帳に無い: {sorted(actual - set(EXPECTED_UNROUTABLE))} / "
        f"台帳にあるのに出ない: {sorted(set(EXPECTED_UNROUTABLE) - actual)}"
    )


def test_t303_unroutable_ports_really_are_far_from_the_sea(ports, mask):
    """陽性対照: 吸着できなかった港が、本当にどの航行可能セルからも遠いこと。

    吸着器の打ち切りが早すぎるだけ、という故障と区別する。
    """
    rows, cols = mask.shape
    for f in ports["features"]:
        p = f["properties"]
        if p["routable"]:
            continue
        lon, lat = f["geometry"]["coordinates"]
        i0, j0 = cell_index(lon, lat, RESOLUTION_DEG)
        ring = int(MAX_SNAP_KM / (RESOLUTION_DEG * 111.32 * 0.5)) + 2
        best = min(
            (
                haversine_km(
                    lon,
                    lat,
                    -180.0 + ((j0 + dj) % cols + 0.5) * RESOLUTION_DEG,
                    90.0 - (i0 + di + 0.5) * RESOLUTION_DEG,
                )
                for di in range(-ring, ring + 1)
                for dj in range(-ring, ring + 1)
                if 0 <= i0 + di < rows and mask[i0 + di, (j0 + dj) % cols]
            ),
            default=float("inf"),
        )
        assert best > MAX_SNAP_KM, (
            f"{p['port_id']} は {best:.1f} km に航行可能セルがあるのに吸着していない"
        )


# ---------------------------------------------------------------------------
# T-304 意味区分(G-05)
# ---------------------------------------------------------------------------


def test_t304_position_and_statistic_declare_different_confidence(ports):
    """位置は観測、``outflows`` は統計。**同じ地物の中で階級が違う**ことを明示する。"""
    assert ports["position_confidence"] == "OBSERVED"
    assert ports["outflows_confidence"] == "STATISTICAL"
    assert "単位" in ports["outflows_note"], "単位が不明であることを出荷物が自分で言うこと"
    for f in ports["features"]:
        assert f["properties"]["confidence"] == "OBSERVED"
        assert f["properties"]["outflows_confidence"] == "STATISTICAL"


def test_t304_coordinates_in_range(ports):
    for f in ports["features"]:
        lon, lat = f["geometry"]["coordinates"]
        assert -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0
