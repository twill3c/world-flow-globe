"""航行グリッドの較正(loop_002 stage 2)。

**測るための使い捨てではなく、測り直せる形で残す。** SPEC.md §6.1 の解像度 R と
マスクの作り方は、この出力を根拠に決める(HC-135: 測る前に数値を書かない)。

## この較正で二度間違えた

1. 海峡が開いているかを「両端が同じ連結成分か」で測った。**何も測っていない** ——
   全海洋がほぼ一つの成分になる(res=1.0 で最大成分が航行可能セルの 99.0%)ので、
   地球の裏側を回っても True になる。→ **枠内の局所最短経路**で測り直した。
2. 局所経路が None のとき、「道が無い」と「端点が陸に載っている」を区別していなかった。
   res=0.25 の SUNDA_LOMBOK が BLOCK と出たのは、端点がバリ島に載っていたためである。
   → 端点の海判定を別に返すようにした。

## 二つのマスクの作り方を比べる

- ``center``: セル中心が陸ポリゴンに載らなければ航行可能(素朴)
- ``area``:   セルを N×N に細分し、海の割合が閾値以上なら航行可能

``center`` は**地峡に穴を開ける**。0.5° ではパナマ地峡が 267 km で通れてしまい、
運河を閉じても船が無料で太平洋へ抜ける。``area`` はこれを塞ぐ代わりに
狭い海峡も塞ぐので、塞がった海峡は**名前つきの通路**として明示的に開ける。

使い方:
    ./.venv/Scripts/python scripts/calibrate_grid.py
"""

from __future__ import annotations

import heapq
import json
import math
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geopandas as gpd  # noqa: E402

from etl.config import RAW  # noqa: E402

NE_ZIP = f"zip://{RAW / 'naturalearth' / 'ne_10m_admin_0_countries.zip'}"
EARTH_R_KM = 6371.0088
LOCAL_DETOUR_FACTOR = 3.0

#: 海峡: (端点 A, 端点 B, 局所探索を許す枠)。枠は迂回路を含まない大きさにする。
STRAITS: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float, float, float]]] = {
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
    "GREAT_BELT (Kattegat / Baltic)": ((11.0, 56.8), (12.5, 54.6), (9.0, 53.5, 15.0, 58.0)),
}

#: 地峡: 運河が無ければ通れないはずの対。ここが「通れる」ならグリッドが陸に穴を開けている。
ISTHMUSES: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float, float, float]]] = {
    "PANAMA": ((-79.6, 8.0), (-79.6, 9.6), (-81.5, 6.5, -77.5, 11.0)),
    "SUEZ": ((32.3, 31.6), (34.2, 27.5), (31.0, 26.0, 36.5, 33.0)),
    "KRA (Thailand)": ((98.0, 9.5), (100.5, 10.5), (97.0, 7.0, 103.0, 13.0)),
    # フロリダ半島とユトランド半島は除外した。
    # フロリダは**地峡ではない** —— 枠の中で南端(キーウェスト)を回れるので、
    #   709 km の経路は「陸に開いた穴」ではなく実在の迂回路である(直線 394 km)。
    #   半島を地峡の表に入れたのは、こちらの分類の誤りだった。
    # ユトランドはキール運河が実在するので「塞がっているべき」ではない。
    "ITALY (Rome side / Adriatic side)": ((12.0, 41.8), (14.5, 42.5), (11.0, 40.0, 16.0, 45.0)),
    "MALAY (Bangkok side / Andaman side)": ((100.0, 12.0), (96.0, 13.5), (95.0, 8.0, 103.0, 16.0)),
    "NICARAGUA": ((-87.5, 11.5), (-83.5, 11.5), (-88.5, 10.0, -82.5, 14.0)),
}

#: 面積割合マスクを作るときの、1 セルあたりの細分数(N×N)。
SUBSAMPLE = 5


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def mask_center(land, res: float) -> np.ndarray:
    nlon, nlat = int(round(360.0 / res)), int(round(180.0 / res))
    lons = -180.0 + (np.arange(nlon) + 0.5) * res
    lats = 90.0 - (np.arange(nlat) + 0.5) * res
    lg, ltg = np.meshgrid(lons, lats)
    return ~shapely.contains_xy(land, lg.ravel(), ltg.ravel()).reshape(nlat, nlon)


def sea_fraction(land, res: float, n: int = SUBSAMPLE) -> np.ndarray:
    """各セルを n×n に細分し、海である小点の割合を返す。"""
    nlon, nlat = int(round(360.0 / res)), int(round(180.0 / res))
    offs = (np.arange(n) + 0.5) / n
    acc = np.zeros((nlat, nlon), dtype=np.float32)
    for oy in offs:
        lats = 90.0 - (np.arange(nlat) + oy) * res
        for ox in offs:
            lons = -180.0 + (np.arange(nlon) + ox) * res
            lg, ltg = np.meshgrid(lons, lats)
            acc += (~shapely.contains_xy(land, lg.ravel(), ltg.ravel()).reshape(nlat, nlon)).astype(
                np.float32
            )
    return acc / (n * n)


def cell_of(lon: float, lat: float, res: float) -> tuple[int, int]:
    nlon, nlat = int(round(360.0 / res)), int(round(180.0 / res))
    j = min(nlon - 1, max(0, int((lon + 180.0) / res)))
    i = min(nlat - 1, max(0, int((90.0 - lat) / res)))
    return i, j


def center_of(i: int, j: int, res: float) -> tuple[float, float]:
    return (-180.0 + (j + 0.5) * res, 90.0 - (i + 0.5) * res)


def local_probe(mask, res, a, b, box) -> dict:
    """枠内の局所最短経路。端点が陸に載っている場合と道が無い場合を**区別して**返す。"""
    nlat, nlon = mask.shape
    lon_min, lat_min, lon_max, lat_max = box
    i1, _ = cell_of(lon_min, lat_min, res)
    i2, _ = cell_of(lon_min, lat_max, res)
    _, j1 = cell_of(lon_min, lat_min, res)
    _, j2 = cell_of(lon_max, lat_min, res)
    i_lo, i_hi = max(0, min(i1, i2)), min(nlat - 1, max(i1, i2))
    j_lo, j_hi = max(0, min(j1, j2)), min(nlon - 1, max(j1, j2))

    src, dst = cell_of(*a, res), cell_of(*b, res)
    a_sea, b_sea = bool(mask[src]), bool(mask[dst])
    direct = haversine_km(*a, *b)
    if not (a_sea and b_sea):
        return {
            "direct_km": round(direct, 1),
            "endpoint_a_sea": a_sea,
            "endpoint_b_sea": b_sea,
            "local_path_km": None,
            "reachable": None,
            "passable": None,
            "note": "端点がセル中心の判定で陸。判定不能",
        }

    dist = {src: 0.0}
    pq = [(0.0, src)]
    found = None
    while pq:
        d, cur = heapq.heappop(pq)
        if cur == dst:
            found = d
            break
        if d > dist.get(cur, math.inf):
            continue
        i, j = cur
        lon0, lat0 = center_of(i, j, res)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if not (i_lo <= ni <= i_hi and j_lo <= nj <= j_hi) or not mask[ni, nj]:
                    continue
                lon1, lat1 = center_of(ni, nj, res)
                nd = d + haversine_km(lon0, lat0, lon1, lat1)
                if nd < dist.get((ni, nj), math.inf) - 1e-9:
                    dist[(ni, nj)] = nd
                    heapq.heappush(pq, (nd, (ni, nj)))
    return {
        "direct_km": round(direct, 1),
        "endpoint_a_sea": True,
        "endpoint_b_sea": True,
        "local_path_km": round(found, 1) if found is not None else None,
        "reachable": found is not None,
        "passable": bool(found is not None and found <= LOCAL_DETOUR_FACTOR * direct),
    }


def evaluate(mask, res: float) -> dict:
    straits = {k: local_probe(mask, res, a, b, box) for k, (a, b, box) in STRAITS.items()}
    isth = {k: local_probe(mask, res, a, b, box) for k, (a, b, box) in ISTHMUSES.items()}
    nav = int(mask.sum())
    return {
        "grid": list(mask.shape),
        "cells_total": int(mask.size),
        "cells_navigable": nav,
        "navigable_ratio": round(nav / mask.size, 4),
        "packed_bytes": int(np.packbits(mask.ravel()).nbytes),
        "straits": straits,
        "isthmuses": isth,
        "straits_blocked": sorted(k for k, v in straits.items() if v["passable"] is False),
        "straits_undecidable": sorted(k for k, v in straits.items() if v["passable"] is None),
        "isthmuses_leaking": sorted(k for k, v in isth.items() if v["passable"] is True),
        "isthmuses_undecidable": sorted(k for k, v in isth.items() if v["passable"] is None),
    }


def main() -> int:
    print("Natural Earth を読み、陸のユニオンを作る…")
    gdf = gpd.read_file(NE_ZIP, columns=["ADM0_A3", "geometry"])
    land = unary_union(gdf.geometry.make_valid().values)
    shapely.prepare(land)

    report: dict = {
        "measured_at": "2026-09-07",
        "local_detour_factor": LOCAL_DETOUR_FACTOR,
        "subsample": SUBSAMPLE,
        "variants": {},
    }

    for res in (0.5, 0.25):
        key = f"center@{res}"
        report["variants"][key] = evaluate(mask_center(land, res), res)
        v = report["variants"][key]
        print(
            f"  {key}: 航行可 {v['cells_navigable']:,} packed {v['packed_bytes']:,} B "
            f"塞がった海峡 {v['straits_blocked']} 漏れた地峡 {v['isthmuses_leaking']}"
        )

    for res in (0.5,):
        frac = sea_fraction(land, res)
        for thr in (0.4, 0.5, 0.6):
            key = f"area>={thr}@{res}"
            report["variants"][key] = evaluate(frac >= thr, res)
            v = report["variants"][key]
            print(
                f"  {key}: 航行可 {v['cells_navigable']:,} packed {v['packed_bytes']:,} B "
                f"塞がった海峡 {v['straits_blocked']} 漏れた地峡 {v['isthmuses_leaking']}"
            )

    out = Path(__file__).resolve().parent.parent / "data" / "processed" / "grid_calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
