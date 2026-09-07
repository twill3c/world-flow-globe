"""チョークポイントの検査(SPEC.md F-04 / F-08 / G-03 / G-17)。

期待値の出所:
- 5 か所の同定・座標・連結する海は原典 §32(利用者受領の仕様書)。
- **ゲート円が実際に海峡を塞ぐこと**は本ファイルが実測して確かめる。
  半径は「塞げる最小」を測って決めた値であって、どこかから写した値ではない。

この検査が無いと何が起きるか:
    ゲート円が小さすぎると、閉鎖しても船が円の横をすり抜ける。
    シミュレーションは正常に完了して「影響なし」と答えるので、
    **故障が「結果」の顔をして出てくる**。
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from etl.transform.chokepoints import BY_ID, CHOKEPOINTS
from etl.transform.chokepoints import OUT as CHOKE_OUT
from etl.transform.nav_grid import OUT as NAV_OUT
from etl.transform.nav_grid import RESOLUTION_DEG, cell_center
from tests.test_nav_grid import ISTHMUSES, STRAITS, haversine_km, local_path_km, unpack_mask

DETOUR_FACTOR = 3.0

#: どのチョークポイントが、どの海峡の通過性に効くか。
#: 左が閉じたら右が通れなくなる、という主張である。
GATE_BLOCKS_STRAIT = {
    "CHOKE_MALACCA": "MALACCA",
    "CHOKE_HORMUZ": "HORMUZ",
    "CHOKE_BAB_EL_MANDEB": "BAB_EL_MANDEB",
}


@pytest.fixture(scope="module")
def grid():
    if not NAV_OUT.exists():
        pytest.skip("nav_grid.json が未生成")
    doc = json.loads(NAV_OUT.read_text(encoding="utf-8"))
    return doc, unpack_mask(doc)


def apply_gate(mask: np.ndarray, lat: float, lon: float, radius_km: float) -> np.ndarray:
    """ゲート円に入るセルを航行不能にする。実装は TypeScript 側と同じ規則にする。"""
    out = mask.copy()
    rows, cols = mask.shape
    # 円の外接矩形だけを走査する(全球を走ると遅い)
    dlat = radius_km / 111.32
    dlon = radius_km / max(1e-6, 111.32 * math.cos(math.radians(lat)))
    i_lo = max(0, int((90.0 - (lat + dlat)) / RESOLUTION_DEG) - 1)
    i_hi = min(rows - 1, int((90.0 - (lat - dlat)) / RESOLUTION_DEG) + 1)
    j_lo = int(((lon - dlon) + 180.0) / RESOLUTION_DEG) - 1
    j_hi = int(((lon + dlon) + 180.0) / RESOLUTION_DEG) + 1
    for i in range(i_lo, i_hi + 1):
        for j in range(j_lo, j_hi + 1):
            jj = j % cols
            clon, clat = cell_center(i, jj)
            if haversine_km(lon, lat, clon, clat) <= radius_km:
                out[i, jj] = False
    return out


# ---------------------------------------------------------------------------
# T-201 台帳そのもの
# ---------------------------------------------------------------------------


def test_t201_five_required_chokepoints():
    """原典 §31 の必須 5 か所がそろっている。"""
    assert {c.id for c in CHOKEPOINTS} == {
        "CHOKE_SUEZ",
        "CHOKE_MALACCA",
        "CHOKE_PANAMA",
        "CHOKE_HORMUZ",
        "CHOKE_BAB_EL_MANDEB",
    }


def test_t201_shipped_file_carries_the_coordinate_caveat():
    """座標が参照点でしかないことを、出荷物が自分で言うこと。"""
    if not CHOKE_OUT.exists():
        pytest.skip("chokepoints.json が未生成")
    doc = json.loads(CHOKE_OUT.read_text(encoding="utf-8"))
    assert "参照点" in doc["coordinate_caveat"]
    assert doc["confidence"] == "OBSERVED"
    assert len(doc["chokepoints"]) == 5


def test_t201_type_specific_fields_are_present():
    for c in CHOKEPOINTS:
        if c.type == "STRAIT":
            assert c.gate_radius_km and c.gate_radius_km > 0
            assert c.canal_id is None
        else:
            assert c.canal_id
            assert c.gate_radius_km is None


def test_t201_referential_integrity_with_canal_ledger():
    """G-03。CANAL 型が指す辺が実在すること。"""
    from etl.transform.passages import CANAL_BY_ID

    for c in CHOKEPOINTS:
        if c.type == "CANAL":
            assert c.canal_id in CANAL_BY_ID, f"{c.id} が存在しない辺 {c.canal_id} を指している"
    # 逆向き: 台帳の運河は必ずどれかのチョークポイントから指されている
    referenced = {c.canal_id for c in CHOKEPOINTS if c.canal_id}
    assert set(CANAL_BY_ID) == referenced


# ---------------------------------------------------------------------------
# T-202 ゲートを閉じる前は通れる(陰性対照)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("choke_id", sorted(GATE_BLOCKS_STRAIT))
def test_t202_strait_is_open_before_closing(grid, choke_id):
    """対照が成り立つ前提を固定する(HC-079)。

    閉じた後に不通になることを主張するなら、**閉じる前は通れていた**ことを
    同じ場所で確かめておかなければ、その対照は何も言っていない。
    """
    _, mask = grid
    name = GATE_BLOCKS_STRAIT[choke_id]
    a, b, box = STRAITS[name]
    got = local_path_km(mask, a, b, box)
    assert got is not None and got <= DETOUR_FACTOR * haversine_km(*a, *b)


# ---------------------------------------------------------------------------
# T-203 ゲートを閉じると通れなくなる(G-17)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("choke_id", sorted(GATE_BLOCKS_STRAIT))
def test_t203_closing_the_gate_blocks_the_strait(grid, choke_id):
    _, mask = grid
    c = BY_ID[choke_id]
    closed = apply_gate(mask, c.lat, c.lon, c.gate_radius_km)
    name = GATE_BLOCKS_STRAIT[choke_id]
    a, b, box = STRAITS[name]
    got = local_path_km(closed, a, b, box)
    assert got is None, (
        f"{choke_id}: 半径 {c.gate_radius_km} km では塞げていない({got:.0f} km で抜けられる)。"
        "閉鎖しても『影響なし』と答える故障になる"
    )


@pytest.mark.parametrize("choke_id", sorted(GATE_BLOCKS_STRAIT))
def test_t203_gate_is_not_wastefully_large(grid, choke_id):
    """緩みすぎを止める対(HC-041)。

    半径を大きくすれば必ず塞がるので、``T-203`` だけでは「効いている」ことの
    証明にならない。**世界を消してはいない**ことを、別の海峡が通れたままで示す。
    """
    _, mask = grid
    c = BY_ID[choke_id]
    closed = apply_gate(mask, c.lat, c.lon, c.gate_radius_km)
    target = GATE_BLOCKS_STRAIT[choke_id]
    still_open = 0
    for name, (a, b, box) in STRAITS.items():
        if name == target:
            continue
        got = local_path_km(closed, a, b, box)
        if got is not None and got <= DETOUR_FACTOR * haversine_km(*a, *b):
            still_open += 1
    assert still_open >= len(STRAITS) - 3, (
        f"{choke_id} のゲートが広すぎる。他の海峡が {len(STRAITS)-1-still_open} 件も巻き添えで塞がった"
    )


def test_t203_closing_bab_el_mandeb_also_seals_the_red_sea(grid):
    """バブ・エル・マンデブを閉じると紅海が袋になる —— 台帳の注記が正しいことを測る。

    注記に書いた主張は、**書いただけでは何も確かめていない**。
    """
    _, mask = grid
    c = BY_ID["CHOKE_BAB_EL_MANDEB"]
    closed = apply_gate(mask, c.lat, c.lon, c.gate_radius_km)
    # 紅海の中(スエズ湾寄り)から、アデン湾へ抜けられないこと
    red_sea = (35.5, 24.0)
    gulf_of_aden = (47.0, 12.5)
    box = (32.0, 10.0, 50.0, 28.0)
    assert local_path_km(mask, red_sea, gulf_of_aden, box) is not None, "閉じる前は通れること"
    assert local_path_km(closed, red_sea, gulf_of_aden, box) is None, "閉じたら紅海が袋になること"


# ---------------------------------------------------------------------------
# T-204 運河を落とすと地峡が不通になる(CANAL 型の対)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(["PANAMA", "SUEZ"]))
def test_t204_isthmus_needs_its_canal(grid, name):
    """運河の辺はマスクに入っていないので、マスクだけでは地峡は通れない。

    T-103 と同じことを、**チョークポイント側の主張として**も置く ——
    「スエズを閉じたら地中海と紅海が切れる」という画面の説明が、
    データの上で本当かどうかはここでしか確かめられない。
    """
    _, mask = grid
    a, b, box = ISTHMUSES[name]
    assert local_path_km(mask, a, b, box) is None
