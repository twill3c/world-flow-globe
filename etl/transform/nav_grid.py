"""航行グリッドの生成(SPEC.md §6.1 / F-05)。

出力は ``public/data/infrastructure/nav_grid.json``。マスクはビット列を base64 で運ぶ
(0.5 度で 32,400 バイト → base64 43,200 文字)。ブラウザはこれを展開して
そのままグラフにする。

**マスクの作り方は較正で決めた**(SPEC.md §6.1.1)。セルを 5×5 に細分し、
海である小点の割合が 0.5 以上なら航行可能。素朴な中心点判定はパナマ地峡に穴を開ける。
"""

from __future__ import annotations

import base64
import json

import geopandas as gpd
import numpy as np
import shapely
from shapely.ops import unary_union

from etl.config import PUBLIC_INFRA, RAW
from etl.download.manifest import sha256_of, utc_now_iso
from etl.transform.passages import CANALS, PASSAGES

SRC_ZIP = RAW / "naturalearth" / "ne_10m_admin_0_countries.zip"
OUT = PUBLIC_INFRA / "nav_grid.json"

RESOLUTION_DEG = 0.5
SUBSAMPLE = 5
SEA_FRACTION_THRESHOLD = 0.5


def grid_shape(res: float = RESOLUTION_DEG) -> tuple[int, int]:
    return int(round(180.0 / res)), int(round(360.0 / res))


def cell_index(lon: float, lat: float, res: float = RESOLUTION_DEG) -> tuple[int, int]:
    """(lon, lat) を含むセルの (row, col)。row は北から、col は西から。"""
    nlat, nlon = grid_shape(res)
    j = int((lon + 180.0) / res)
    i = int((90.0 - lat) / res)
    # 端(lon=180, lat=-90)がはみ出すのを畳む
    return min(nlat - 1, max(0, i)), min(nlon - 1, max(0, j))


def cell_center(i: int, j: int, res: float = RESOLUTION_DEG) -> tuple[float, float]:
    return (-180.0 + (j + 0.5) * res, 90.0 - (i + 0.5) * res)


def load_land():
    gdf = gpd.read_file(f"zip://{SRC_ZIP}", columns=["ADM0_A3", "geometry"])
    land = unary_union(gdf.geometry.make_valid().values)
    shapely.prepare(land)
    return land


def sea_fraction(land, res: float = RESOLUTION_DEG, n: int = SUBSAMPLE) -> np.ndarray:
    nlat, nlon = grid_shape(res)
    offs = (np.arange(n) + 0.5) / n
    acc = np.zeros((nlat, nlon), dtype=np.float32)
    for oy in offs:
        lats = 90.0 - (np.arange(nlat) + oy) * res
        for ox in offs:
            lons = -180.0 + (np.arange(nlon) + ox) * res
            lg, ltg = np.meshgrid(lons, lats)
            sea = ~shapely.contains_xy(land, lg.ravel(), ltg.ravel()).reshape(nlat, nlon)
            acc += sea.astype(np.float32)
    return acc / (n * n)


def base_mask(land, res: float = RESOLUTION_DEG) -> np.ndarray:
    return sea_fraction(land, res) >= SEA_FRACTION_THRESHOLD


def _chain_between(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """2 セルを結ぶ 8 近傍の鎖(両端を含む)。

    手で置いた 2 点は、たいてい隣り合っていない。**隣り合っている「つもり」で
    倒すと、倒したセルが飛び石になって道にならない**(loop_002 で踏んだ)。
    """
    (i0, j0), (i1, j1) = a, b
    steps = max(abs(i1 - i0), abs(j1 - j0))
    if steps == 0:
        return [a]
    out = []
    for s in range(steps + 1):
        t = s / steps
        out.append((round(i0 + (i1 - i0) * t), round(j0 + (j1 - j0) * t)))
    return out


def _is_chain_8_connected(cells: list[tuple[int, int]]) -> bool:
    return all(
        max(abs(cells[k][0] - cells[k + 1][0]), abs(cells[k][1] - cells[k + 1][1])) <= 1
        for k in range(len(cells) - 1)
    )


def apply_passages(mask: np.ndarray, res: float = RESOLUTION_DEG) -> tuple[np.ndarray, list[dict]]:
    """通路の経路上のセルを航行可能へ倒す。

    **倒す前に二つ検算する**(HC-203)。

    1. 倒すセルの列が 8 近傍で繋がっていること。繋がっていなければ道にならない
    2. その通路が実際に何かを開けたこと。何も開けないなら台帳から外すべきである
    """
    mask = mask.copy()
    applied = []
    for p in PASSAGES:
        pts = (p.a,) + p.via + (p.b,)
        waypoints = [cell_index(lon, lat, res) for lon, lat in pts]
        cells: list[tuple[int, int]] = []
        for k in range(len(waypoints) - 1):
            seg = _chain_between(waypoints[k], waypoints[k + 1])
            cells.extend(seg if not cells else seg[1:])
        if not _is_chain_8_connected(cells):
            raise ValueError(f"{p.id}: 倒すセルの列が 8 近傍で繋がっていない: {cells}")
        was_sea = [bool(mask[c]) for c in cells]
        for c in cells:
            mask[c] = True
        opened = int(sum(1 for s in was_sea if not s))
        if opened == 0:
            raise ValueError(
                f"{p.id}: 何も開けていない。すでに通れる場所を通路として登録している"
            )
        applied.append(
            {
                "id": p.id,
                "name": p.name,
                "waypoints": [[int(i), int(j)] for i, j in waypoints],
                "cells": [[int(i), int(j)] for i, j in cells],
                "cells_opened": opened,
                "reason": p.reason,
            }
        )
    return mask, applied


def canal_edges(mask: np.ndarray, res: float = RESOLUTION_DEG) -> list[dict]:
    """運河の辺を作る。

    **マスクには一切触らない。** 以前はここで端点セルを航行可能へ倒していたが、
    パナマでは倒した 2 セルが 8 近傍で隣り合い、**運河の辺が無くても地峡を
    78 km で抜けられる**穴を自分で開けていた(loop_002 で踏んだ)。
    運河が意味を持つのは「他に道が無い」ときだけである。

    そのため、ここで二つ検算する(HC-203)。

    1. 端点セルが**すでに航行可能**であること(倒して作らない)
    2. 端点セルが 8 近傍で隣り合っていないこと(隣り合っていたら辺が無意味)
    """
    edges = []
    for c in CANALS:
        pts = (c.a,) + c.via + (c.b,)
        cells = [cell_index(lon, lat, res) for lon, lat in pts]
        for (lon, lat), cell in zip(pts, cells):
            if not mask[cell]:
                raise ValueError(
                    f"{c.id}: 端点 ({lon},{lat}) → セル {cell} が航行不能。"
                    "運河の出入口は、すでに海であるセルに置くこと"
                )
        for k in range(len(cells) - 1):
            di = abs(cells[k][0] - cells[k + 1][0])
            dj = abs(cells[k][1] - cells[k + 1][1])
            if max(di, dj) <= 1:
                raise ValueError(
                    f"{c.id}: 端点 {cells[k]} と {cells[k+1]} が 8 近傍で隣り合っている。"
                    "運河の辺を落としても船が通り抜けるので、辺が意味を持たない"
                )
        edges.append(
            {
                "id": c.id,
                "chokepoint_id": c.chokepoint_id,
                "name": c.name,
                "cells": [[int(i), int(j)] for i, j in cells],
                "length_km": c.length_km,
                "length_source": c.length_source,
            }
        )
    return edges


def build_nav_grid() -> dict:
    land = load_land()
    mask = base_mask(land)
    navigable_before = int(mask.sum())

    mask, passages = apply_passages(mask)
    canals = canal_edges(mask)
    navigable_after = int(mask.sum())

    packed = np.packbits(mask.ravel())
    doc = {
        "layer_id": "NAV_GRID",
        "confidence": "INFERRED",
        "data_source": "SRC-001",
        "generated_at": utc_now_iso(),
        "resolution_deg": RESOLUTION_DEG,
        "rows": int(mask.shape[0]),
        "cols": int(mask.shape[1]),
        "mask_encoding": "packbits-msb-first-rowmajor-base64",
        "mask_base64": base64.b64encode(packed.tobytes()).decode("ascii"),
        "sea_fraction_threshold": SEA_FRACTION_THRESHOLD,
        "subsample": SUBSAMPLE,
        "navigable_cells": navigable_after,
        "navigable_cells_before_corrections": navigable_before,
        "passages": passages,
        "canals": canals,
        "note": (
            "セルを 5x5 に細分し、海である小点の割合が 0.5 以上のセルを航行可能とした。"
            "この線は実船の航跡ではない —— 陸を避けて引いた推定である"
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    return {
        "rows": doc["rows"],
        "cols": doc["cols"],
        "navigable_cells": navigable_after,
        "cells_opened_by_passages": sum(p["cells_opened"] for p in passages),
        "canals": len(canals),
        "packed_bytes": int(packed.nbytes),
        "bytes": OUT.stat().st_size,
        "sha256": sha256_of(OUT),
    }


if __name__ == "__main__":
    print(json.dumps(build_nav_grid(), ensure_ascii=False, indent=2))
