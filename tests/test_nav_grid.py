"""航行グリッドの検査(SPEC.md G-01 / G-07 / G-14 / G-15)。

期待値の出所:
- 海峡・地峡の通過可否は ``scripts/calibrate_grid.py`` の実測(2026-09-07)。
- 「運河が無ければ地峡は通れない」は SPEC.md §6.1 の設計そのもの。
  これは**運河という概念が意味を持つための前提**なので、検査が無ければ
  「スエズを閉じたのに何も起きない」という故障が緑のまま通る。
"""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

import pytest

from etl.config import ROOT
from etl.transform.nav_grid import OUT as NAV_OUT
from etl.transform.nav_grid import RESOLUTION_DEG, cell_center, cell_index
from etl.transform.passages import CANALS, PASSAGES

EARTH_R_KM = 6371.0088


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def unpack_mask(doc: dict):
    import base64

    import numpy as np

    raw = base64.b64decode(doc["mask_base64"])
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))
    return bits[: doc["rows"] * doc["cols"]].reshape(doc["rows"], doc["cols"]).astype(bool)


@pytest.fixture(scope="module")
def grid():
    if not NAV_OUT.exists():
        pytest.skip("nav_grid.json が未生成")
    doc = json.loads(NAV_OUT.read_text(encoding="utf-8"))
    return doc, unpack_mask(doc)


def local_path_km(mask, a, b, box, res=RESOLUTION_DEG) -> float | None:
    """枠の中だけを 8 近傍で歩いた最短距離。到達不能なら None。

    **枠を切るのは、これが「その場で通れるか」の検査だから。** 枠が無いと
    地球を一周する経路が見つかり、何を測っているのか分からなくなる
    (loop_002 で一度この誤りを踏んだ)。
    """
    nlat, nlon = mask.shape
    lon_min, lat_min, lon_max, lat_max = box
    i_hi, _ = cell_index(lon_min, lat_min, res)
    i_lo, _ = cell_index(lon_min, lat_max, res)
    _, j_lo = cell_index(lon_min, lat_min, res)
    _, j_hi = cell_index(lon_max, lat_min, res)
    i_lo, i_hi = max(0, min(i_lo, i_hi)), min(nlat - 1, max(i_lo, i_hi))
    j_lo, j_hi = max(0, min(j_lo, j_hi)), min(nlon - 1, max(j_lo, j_hi))

    src, dst = cell_index(*a, res), cell_index(*b, res)
    if not (mask[src] and mask[dst]):
        return None
    dist = {src: 0.0}
    pq = [(0.0, src)]
    while pq:
        d, cur = heapq.heappop(pq)
        if cur == dst:
            return d
        if d > dist.get(cur, math.inf):
            continue
        i, j = cur
        lon0, lat0 = cell_center(i, j, res)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if not (i_lo <= ni <= i_hi and j_lo <= nj <= j_hi) or not mask[ni, nj]:
                    continue
                lon1, lat1 = cell_center(ni, nj, res)
                nd = d + haversine_km(lon0, lat0, lon1, lat1)
                if nd < dist.get((ni, nj), math.inf) - 1e-9:
                    dist[(ni, nj)] = nd
                    heapq.heappush(pq, (nd, (ni, nj)))
    return None


# 較正で使ったのと同じ端点・枠(scripts/calibrate_grid.py)。
STRAITS = {
    "MALACCA": ((103.6, 1.2), (97.0, 6.5), (94.0, -2.0, 106.0, 9.0)),
    "GIBRALTAR": ((-4.5, 36.0), (-7.0, 36.0), (-9.0, 34.0, -2.0, 38.0)),
    "BAB_EL_MANDEB": ((42.0, 15.0), (45.5, 12.0), (40.0, 10.0, 48.0, 17.0)),
    "HORMUZ": ((51.0, 27.0), (58.5, 24.5), (49.0, 22.0, 60.0, 30.0)),
    "DOVER": ((3.0, 51.8), (-1.5, 49.8), (-3.0, 48.5, 5.0, 53.5)),
    "BOSPHORUS_DARDANELLES": ((31.0, 43.0), (25.0, 39.0), (23.0, 38.0, 34.0, 45.0)),
    "TAIWAN": ((121.5, 26.0), (118.0, 22.0), (117.0, 21.0, 123.0, 27.0)),
    "SUNDA": ((107.0, -5.0), (105.0, -8.0), (103.0, -10.0, 110.0, -3.0)),
    "LOMBOK": ((115.7, -7.5), (115.7, -9.5), (114.0, -11.0, 118.0, -6.0)),
    "SKAGERRAK_KATTEGAT": ((9.5, 58.0), (11.5, 56.8), (7.0, 54.0, 14.0, 60.0)),
    "KOREA_TSUSHIMA": ((129.5, 34.0), (131.5, 36.5), (127.0, 32.0, 134.0, 38.0)),
    "MOZAMBIQUE": ((41.5, -15.0), (41.0, -23.0), (33.0, -27.0, 50.0, -10.0)),
    "MAGELLAN_DRAKE": ((-67.0, -55.5), (-75.0, -55.5), (-78.0, -58.0, -63.0, -52.0)),
    "BERING": ((-172.0, 62.0), (-172.0, 68.0), (-180.0, 58.0, -165.0, 71.0)),
    "GREAT_BELT": ((11.0, 56.8), (12.5, 54.6), (9.0, 53.5, 15.0, 58.0)),
}

ISTHMUSES = {
    "PANAMA": ((-79.6, 8.0), (-79.6, 9.6), (-81.5, 6.5, -77.5, 11.0)),
    "SUEZ": ((32.3, 31.6), (34.2, 27.5), (31.0, 26.0, 36.5, 33.0)),
    "KRA": ((98.0, 9.5), (100.5, 10.5), (97.0, 7.0, 103.0, 13.0)),
    "ITALY": ((12.0, 41.8), (14.5, 42.5), (11.0, 40.0, 16.0, 45.0)),
    "MALAY": ((100.0, 12.0), (96.0, 13.5), (95.0, 8.0, 103.0, 16.0)),
    "NICARAGUA": ((-87.5, 11.5), (-83.5, 11.5), (-88.5, 10.0, -82.5, 14.0)),
}

DETOUR_FACTOR = 3.0


# ---------------------------------------------------------------------------
# T-101 グリッドそのもの
# ---------------------------------------------------------------------------


def test_t101_grid_shape_and_mask_length(grid):
    doc, mask = grid
    assert doc["resolution_deg"] == RESOLUTION_DEG
    assert mask.shape == (doc["rows"], doc["cols"])
    assert int(mask.sum()) == doc["navigable_cells"]
    # 走査対象が空でないこと(この検査自体が働いていることの確認)
    assert doc["navigable_cells"] > 100_000


def test_t101_confidence_is_inferred(grid):
    """G-05。航路の素材は観測ではない。"""
    doc, _ = grid
    assert doc["confidence"] == "INFERRED"


# ---------------------------------------------------------------------------
# T-102 海峡が通れる(G-14)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(STRAITS))
def test_t102_strait_is_passable(grid, name):
    _, mask = grid
    a, b, box = STRAITS[name]
    direct = haversine_km(*a, *b)
    got = local_path_km(mask, a, b, box)
    assert got is not None, f"{name}: 枠内で到達できない"
    assert got <= DETOUR_FACTOR * direct, f"{name}: {got:.0f} km > {DETOUR_FACTOR}×{direct:.0f} km"


# ---------------------------------------------------------------------------
# T-103 地峡は通れない(G-15)—— 運河という概念が意味を持つための前提
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ISTHMUSES))
def test_t103_isthmus_is_not_passable(grid, name):
    _, mask = grid
    a, b, box = ISTHMUSES[name]
    got = local_path_km(mask, a, b, box)
    assert got is None, (
        f"{name}: グリッドが陸に穴を開けている({got:.0f} km で抜けられる)。"
        "運河を閉じても船が通ってしまう"
    )


def test_t103_canal_endpoints_are_not_adjacent_in_mask(grid):
    """運河の両端セルがマスクの上で繋がっていたら、運河の辺は無意味になる。

    **これは T-103 とは別の故障を捕まえる。** 地峡そのものが塞がっていても、
    運河の出入口として倒したセルどうしが 8 近傍で隣り合えば、
    運河の辺を落としても船はそこを通り抜ける。
    """
    _, mask = grid
    for canal in CANALS:
        pts = (canal.a,) + canal.via + (canal.b,)
        cells = [cell_index(lon, lat) for lon, lat in pts]
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        pad = 3.0
        box = (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)
        got = local_path_km(mask, canal.a, canal.b, box)
        assert got is None, (
            f"{canal.id}: 運河の両端がマスクだけで {got:.0f} km で繋がっている。"
            f"端点セル {cells} が近すぎる"
        )


# ---------------------------------------------------------------------------
# T-104 台帳の緩みすぎを止める(HC-041 の「除外リストの対」)
# ---------------------------------------------------------------------------


def test_t104_every_passage_actually_opened_something(grid):
    """登録した通路は、実際に閉じていた場所を開けたものだけであること。

    開けるものが何も無い通路が台帳に残っていると、台帳が嘘になる。
    """
    doc, _ = grid
    by_id = {p["id"]: p for p in doc["passages"]}
    assert set(by_id) == {p.id for p in PASSAGES}
    for pid, rec in by_id.items():
        assert rec["cells_opened"] > 0, f"{pid} は何も開けていない。台帳から外すこと"


def test_t104_passages_are_few(grid):
    """通路は例外であって仕組みではない。増えたら較正をやり直す合図にする。

    2026-09-07 の較正で必要だったのは 2 件(GIBRALTAR / BOSPHORUS_DARDANELLES)。
    """
    doc, _ = grid
    assert len(doc["passages"]) <= 4, "通路が増えすぎている。解像度かマスクの作り方を見直すこと"


def test_t104_canals_declare_length_and_source(grid):
    doc, _ = grid
    assert {c["chokepoint_id"] for c in doc["canals"]} == {"CHOKE_SUEZ", "CHOKE_PANAMA"}
    for c in doc["canals"]:
        assert c["length_km"] > 0
        assert c["length_source"], f"{c['id']} に長さの出所が無い"


# ---------------------------------------------------------------------------
# T-105 サイズ(G-02)
# ---------------------------------------------------------------------------


def test_t105_public_data_total_size():
    from etl.config import MAX_FILE_MB, MAX_TOTAL_MB

    root = ROOT / "public" / "data"
    files = [p for p in root.rglob("*") if p.is_file()]
    assert files, "public/data が空(この検査が働いていない)"
    total = sum(p.stat().st_size for p in files)
    for p in files:
        mb = p.stat().st_size / 1024 / 1024
        assert mb < MAX_FILE_MB, f"{p.name} が {mb:.1f} MB"
    assert total / 1024 / 1024 < MAX_TOTAL_MB, f"合計 {total/1024/1024:.1f} MB"


# ---------------------------------------------------------------------------
# T-106 出荷 GeoJSON の座標範囲(G-01)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", ["base/countries.geojson", "base/eez.geojson"])
def test_t106_shipped_geojson_coordinates_in_range(rel):
    path = ROOT / "public" / "data" / rel
    if not path.exists():
        pytest.skip(f"{rel} が未生成")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["type"] == "FeatureCollection"
    assert doc["features"], "地物が空"
    seen = 0

    def walk(coords):
        nonlocal seen
        if isinstance(coords[0], (int, float)):
            lon, lat = coords[0], coords[1]
            assert -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0, f"{rel}: ({lon},{lat})"
            seen += 1
            return
        for c in coords:
            walk(c)

    for f in doc["features"]:
        walk(f["geometry"]["coordinates"])
    assert seen > 1000, "座標を数えられていない(この検査が働いていない)"


def test_t106_shipped_geojson_declares_confidence():
    from etl.config import CONFIDENCE_VALUES

    for rel in ("base/countries.geojson", "base/eez.geojson"):
        path = ROOT / "public" / "data" / rel
        if not path.exists():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["confidence"] in CONFIDENCE_VALUES
        for f in doc["features"]:
            assert f["properties"]["confidence"] in CONFIDENCE_VALUES


def test_t106_eez_reports_rows_dropped_by_the_source():
    """出典側の空 geometry を、黙って落とさず名前ごと残していること。"""
    path = ROOT / "public" / "data" / "base" / "eez.geojson"
    if not path.exists():
        pytest.skip("eez.geojson が未生成")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "empty_in_source" in doc
    assert len(doc["features"]) + len(doc["empty_in_source"]) == 285, (
        "出典 285 行(2026-09-07 実測)の内訳が合わない"
    )
    for rec in doc["empty_in_source"]:
        assert rec["geoname"], "落とした行に名前が無い"
