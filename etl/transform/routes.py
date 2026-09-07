"""基準航路の生成(SPEC.md F-05 / 原典 §25・§28)。

**この線は実船の航跡ではない。** 港・航行可能海域・チョークポイントから
こちらが引いた最短経路である。したがって ``confidence = INFERRED``。
所要日数は 16 ノット仮定の割り算なので ``ESTIMATED``。**同じ地物の中で階級が違う。**

## 対象の港をどう選ぶか —— 最初の規則は測って捨てた

はじめは「``outflows`` の大きい順に 24 港、同じ国から 2 港まで」だけにした。
**測ったら使えなかった**(2026-09-07 実測)。

| 規則 | アジア | 欧州・地中海 | 南北アメリカ | 南半球 |
|---|---|---|---|---|
| 上位 24(上限なし) | 10 | 12 | 2 | 0 |
| 上位 24(国ごと 2 港) | 10 | 12 | 2 | 0 |

国ごとの上限は**何も変えなかった** —— 上限なしの上位 24 でも国は 19 か国に散っており
(最多は中国の 3 港)、偏っているのは国ではなく**海域**だった。
アメリカの 2 港はどちらも大西洋岸(ニューヨーク・サバンナ)なので、
この港集合では**パナマ運河を通る航路が 1 本も生まれない**。
5 つのチョークポイントを回すことがこのアプリの目的(原典 §51)なのに、
そのうち 2 つが飾りになる。

そこで規則を二段にした。

1. ``outflows`` の大きい順に 16 港(同じ国から 2 港まで)—— **量の代表**
2. **地理の錨**を 10 港足す —— 各港に「なぜ要るか」を書く。**海域の代表**

錨を足す目的は「チョークポイントが必ず現れるようにする」ことではなく
「地球の主要な海域が航路網に入る」ことである。結果として 5 つのチョークポイントが
すべて現れたかどうかは**測って確かめる**(SPEC.md G-19)。現れなければそう書く。
"""

from __future__ import annotations

import base64
import json
import math

import numpy as np

from etl.config import PUBLIC_INFRA
from etl.download.manifest import utc_now_iso
from etl.transform.chokepoints import CHOKEPOINTS
from etl.transform.chokepoints import OUT as CHOKE_OUT
from etl.transform.nav_grid import OUT as NAV_OUT
from etl.transform.nav_grid import RESOLUTION_DEG, cell_center
from etl.transform.ports import OUT as PORTS_OUT
from etl.transform.routing import (
    NavGraph,
    apply_polar_limit,
    decode_float64,
    haversine_km,
)

OUT = PUBLIC_INFRA / "shipping_routes.json"

TOP_BY_OUTFLOWS = 16
MAX_PORTS_PER_COUNTRY = 2

#: 地理の錨。**海域の代表**として足す港と、その理由。
#: outflows の順位では決して入らないが、これが無いと航路網が北半球の
#: アジア・欧州だけになる(2026-09-07 実測)。
GEOGRAPHIC_ANCHORS: tuple[tuple[str, str], ...] = (
    ("USLAX", "北米西岸。パナマ運河をまたぐ東西の往来がここでしか生まれない"),
    ("CAVAN", "北太平洋の北端(バンクーバー)"),
    ("MXZLO", "中米の太平洋岸(マンサニヨ)"),
    ("PABLB", "パナマ運河の太平洋口(バルボア)"),
    ("BRSSZ", "南米東岸(サントス)"),
    ("PECLL", "南米西岸(カヤオ)"),
    ("ARBUE", "南大西洋。マゼラン海峡と喜望峰の分岐点(ブエノスアイレス)"),
    ("ZADUR", "喜望峰航路 —— スエズ運河の代替路(ダーバン)"),
    ("AUSYD", "南太平洋(シドニー)"),
    ("NZAKL", "南西太平洋の端(オークランド)"),
)
CRUISE_SPEED_KNOTS = 16.0
KM_PER_NM = 1.852
DISPLAY_SIMPLIFY_DEG = 0.5
DISPLAY_PRECISION = 2


def load_graph(allow_polar: bool = False) -> tuple[NavGraph, dict]:
    """航行グラフを組む。

    ``allow_polar=False``(既定)では高緯度の帯を航行不能にする。
    **これは地理ではなく仮定**なので、切り替えられるようにしてある(SPEC.md F-16)。
    """
    doc = json.loads(NAV_OUT.read_text(encoding="utf-8"))
    rows, cols = doc["rows"], doc["cols"]
    bits = np.unpackbits(np.frombuffer(base64.b64decode(doc["mask_base64"]), dtype=np.uint8))
    mask = bits[: rows * cols].reshape(rows, cols).astype(bool)
    if not allow_polar:
        mask = apply_polar_limit(
            mask,
            doc["resolution_deg"],
            doc["polar_limit"]["north_deg"],
            doc["polar_limit"]["south_deg"],
        )
    table = decode_float64(doc["weight_table_base64"], (rows, 3))
    canals = [
        {
            "a": c["cells"][0][0] * cols + c["cells"][0][1],
            "b": c["cells"][-1][0] * cols + c["cells"][-1][1],
            "weight_km": c["weight_km"],
            "chokepoint_id": c["chokepoint_id"],
            "id": c["id"],
        }
        for c in doc["canals"]
    ]
    return NavGraph(mask, table, doc["meridional_km"], canals), doc


def select_ports() -> list[dict]:
    """二段の規則で港を選ぶ。どちらの段で入ったかを ``selected_by`` に残す。"""
    doc = json.loads(PORTS_OUT.read_text(encoding="utf-8"))
    routable = {
        f["properties"]["unlocode"]: f["properties"]
        for f in doc["features"]
        if f["properties"]["routable"]
    }
    cand = sorted(routable.values(), key=lambda p: (-p["outflows"], p["unlocode"]))

    per_country: dict[str, int] = {}
    out: list[dict] = []
    chosen: set[str] = set()
    for p in cand:
        key = p["country_iso3"] or p["country_iso2"]
        if per_country.get(key, 0) >= MAX_PORTS_PER_COUNTRY:
            continue
        per_country[key] = per_country.get(key, 0) + 1
        out.append({**p, "selected_by": "outflows", "anchor_reason": None})
        chosen.add(p["unlocode"])
        if len(out) >= TOP_BY_OUTFLOWS:
            break

    for locode, reason in GEOGRAPHIC_ANCHORS:
        if locode not in routable:
            # 錨が出典から消えたら黙って飛ばさない。台帳の方を直させる。
            raise ValueError(f"地理の錨 {locode} が出典に無い、または経路に使えない")
        if locode in chosen:
            continue
        out.append({**routable[locode], "selected_by": "anchor", "anchor_reason": reason})
        chosen.add(locode)
    return out


def transit_days(distance_km: float, knots: float = CRUISE_SPEED_KNOTS) -> float:
    return distance_km / (knots * KM_PER_NM * 24.0)


def _simplify(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker(表示用)。**距離の計算には使わない。**"""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        x0, y0 = points[lo]
        x1, y1 = points[hi]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1e-12
        best, best_d = -1, -1.0
        for k in range(lo + 1, hi):
            x, y = points[k]
            d = abs(dy * (x - x0) - dx * (y - y0)) / norm
            if d > best_d:
                best, best_d = k, d
        if best_d > tol:
            keep[best] = True
            stack.append((lo, best))
            stack.append((best, hi))
    return [p for p, k in zip(points, keep) if k]


def _split_antimeridian(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """日付変更線をまたぐところで線を切る。

    切らずに描くと、**地球儀を横切る一本の直線**になって嘘の航路が出る。
    """
    segs: list[list[tuple[float, float]]] = [[]]
    for k, (lon, lat) in enumerate(points):
        if k and abs(lon - points[k - 1][0]) > 180.0:
            segs.append([])
        segs[-1].append((lon, lat))
    return [s for s in segs if len(s) >= 2]


def chokepoints_used(graph: NavGraph, path: list[int], gates: list[dict]) -> list[str]:
    """経路が使ったチョークポイントを、**経路そのものから**判定する。

    運河は「連続する 2 ノードが運河の両端で、しかも 8 近傍でない」ことで見分ける。
    海峡はゲート円に入るセルが経路にあることで見分ける。
    """
    used: list[str] = []
    canal_pairs = {
        (c["a"], c["b"]): c["chokepoint_id"] for c in graph.canals
    } | {(c["b"], c["a"]): c["chokepoint_id"] for c in graph.canals}
    for a, b in zip(path, path[1:]):
        cid = canal_pairs.get((a, b))
        if cid and cid not in used:
            used.append(cid)
    for g in gates:
        for n in path:
            i, j = graph.coords(n)
            lon, lat = cell_center(i, j, RESOLUTION_DEG)
            if haversine_km(g["lon"], g["lat"], lon, lat) <= g["radius_km"]:
                if g["id"] not in used:
                    used.append(g["id"])
                break
    return used


def build_routes() -> dict:
    graph, nav = load_graph()
    ports = select_ports()
    gates = [
        {"id": c.id, "lat": c.lat, "lon": c.lon, "radius_km": c.gate_radius_km}
        for c in CHOKEPOINTS
        if c.type == "STRAIT"
    ]

    nodes = {p["unlocode"]: p["grid_cell"][0] * graph.cols + p["grid_cell"][1] for p in ports}
    by_locode = {p["unlocode"]: p for p in ports}
    targets = set(nodes.values())

    routes = []
    unreachable = []
    same_cell: list[list[str]] = []
    for src_locode in sorted(nodes):
        src = nodes[src_locode]
        dist, prev = graph.dijkstra(src, targets=set(targets))
        for dst_locode in sorted(nodes):
            if dst_locode <= src_locode:
                continue
            dst = nodes[dst_locode]
            if dst == src:
                # 同じセルに吸着した港どうし。距離 0 の「航路」は航路ではないので
                # 出さない。**黙って落とさず、組を名前つきで残す**
                same_cell.append([src_locode, dst_locode])
                continue
            if dst not in dist:
                unreachable.append([src_locode, dst_locode])
                continue
            path = graph.path(prev, src, dst)
            if path is None:
                unreachable.append([src_locode, dst_locode])
                continue
            # Dijkstra の答えをそのまま信じず、辺をたどり直して足す
            length = graph.path_length_km(path)
            pts = [cell_center(*graph.coords(n), RESOLUTION_DEG) for n in path]
            simplified = _simplify(pts, DISPLAY_SIMPLIFY_DEG)
            segs = _split_antimeridian(simplified)
            # 下界(G-07)は**経路の両端セルの中心どうし**の測地線距離で測る。
            # 港の実座標で測ると、吸着距離のぶんだけ下界が経路より長くなりうる。
            a_lon, a_lat = cell_center(*graph.coords(src), RESOLUTION_DEG)
            b_lon, b_lat = cell_center(*graph.coords(dst), RESOLUTION_DEG)
            geo = haversine_km(a_lon, a_lat, b_lon, b_lat)
            routes.append(
                {
                    "route_id": f"ROUTE_{src_locode}_{dst_locode}",
                    "source_port_id": f"PORT_{src_locode}",
                    "target_port_id": f"PORT_{dst_locode}",
                    "route_type": "OCEAN",
                    "distance_km": round(length, 3),
                    "geodesic_km": round(geo, 3),
                    "detour_ratio": round(length / geo, 4) if geo > 0 else None,
                    "transit_time_days": round(transit_days(length), 3),
                    "chokepoints": chokepoints_used(graph, path, gates),
                    "path_cells": len(path),
                    "display_segments": [
                        [[round(lon, DISPLAY_PRECISION), round(lat, DISPLAY_PRECISION)] for lon, lat in s]
                        for s in segs
                    ],
                    "confidence": "INFERRED",
                    "transit_time_confidence": "ESTIMATED",
                }
            )

    doc = {
        "version": "3.2.0",
        "layer_id": "SHIPPING_ROUTE",
        "confidence": "INFERRED",
        "transit_time_confidence": "ESTIMATED",
        "generated_at": utc_now_iso(),
        "not_a_ship_track": (
            "この線は実船の航跡ではない。港・航行可能海域・チョークポイントから"
            "引いた推定の最短経路である(原典 §25)"
        ),
        "selection_rule": {
            "description": (
                "二段。(1) outflows の大きい順に 16 港(同じ国から 2 港まで)= 量の代表。"
                "(2) 地理の錨 10 港 = 海域の代表。錨が無いと航路網が北半球の"
                "アジア・欧州だけになり、パナマ運河を通る航路が 1 本も生まれない"
                "(2026-09-07 実測)"
            ),
            "top_by_outflows": TOP_BY_OUTFLOWS,
            "max_ports_per_country": MAX_PORTS_PER_COUNTRY,
            "geographic_anchors": [
                {"unlocode": lo, "reason": why} for lo, why in GEOGRAPHIC_ANCHORS
            ],
        },
        "speed_assumption": {"knots": CRUISE_SPEED_KNOTS, "km_per_nm": KM_PER_NM},
        "grid": {"resolution_deg": RESOLUTION_DEG, "rows": nav["rows"], "cols": nav["cols"]},
        "ports": [
            {
                "port_id": p["port_id"],
                "unlocode": p["unlocode"],
                "name": p["name"],
                "country_iso3": p["country_iso3"],
                "outflows": p["outflows"],
                "grid_cell": p["grid_cell"],
                "selected_by": p["selected_by"],
                "anchor_reason": p["anchor_reason"],
            }
            for p in ports
        ],
        "routes": routes,
        "unreachable_pairs": unreachable,
        "same_cell_pairs": same_cell,
        "same_cell_note": (
            "0.5 度のセルに同居した港の組。距離 0 km の航路は航路ではないので出さない"
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    ratios = [r["detour_ratio"] for r in routes if r["detour_ratio"]]
    return {
        "ports": len(ports),
        "routes": len(routes),
        "unreachable_pairs": len(unreachable),
        "same_cell_pairs": len(same_cell),
        "detour_ratio_median": round(float(np.median(ratios)), 3) if ratios else None,
        "detour_ratio_max": round(max(ratios), 3) if ratios else None,
        "bytes": OUT.stat().st_size,
    }


if __name__ == "__main__":
    print(json.dumps(build_routes(), ensure_ascii=False, indent=2))
