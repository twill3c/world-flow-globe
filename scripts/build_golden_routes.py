"""二実装照合(G-06)の golden を作る。

Python 側の探索結果を、**距離だけでなく通過セル列まで**書き出す。
TypeScript 側の vitest が同じファイルを読んで突き合わせる。

## なぜセル列まで比べるのか(HC-065)

距離だけを比べる照合は、**別の経路で偶然同じ距離に着いたとき**に何も言わない。
このグリッドは同点が実在する(南北の辺は緯度に依らず等長なので、
経路の途中で北へ 1・南へ 1 を入れ替えても長さが変わらない場面がある)。
だから経路も比べ、**同点をどちらへ倒すかの規則**を両実装で揃える。

## 何を golden にするか

- 平常時の 6 経路(チョークポイントの使われ方が異なるものを選ぶ)
- スエズ閉鎖時の同じ経路(閉鎖が経路を変えることまで固定する)

使い方:
    ./.venv/Scripts/python scripts/build_golden_routes.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.transform.chokepoints import BY_ID  # noqa: E402
from etl.transform.nav_grid import RESOLUTION_DEG, cell_center  # noqa: E402
from etl.transform.routes import load_graph  # noqa: E402
from etl.transform.routing import haversine_km  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "golden_routes.json"

#: 照合に使う経路。**チョークポイントの使われ方が違うもの**を選ぶ。
CASES = [
    ("SGSIN", "NLRTM", "シンガポール→ロッテルダム: マラッカ・バブ・スエズを通る主要幹線"),
    ("SGSIN", "USLAX", "シンガポール→ロサンゼルス: 太平洋横断。運河を使わない"),
    ("USLAX", "NLRTM", "ロサンゼルス→ロッテルダム: パナマ運河"),
    ("AEJEA", "NLRTM", "ジェベル・アリ→ロッテルダム: ホルムズ・バブ・スエズ"),
    ("BRSSZ", "AUSYD", "サントス→シドニー: 南半球・喜望峰かホーン岬か"),
    ("ZADUR", "KRPUS", "ダーバン→釜山: インド洋横断"),
]

#: 閉鎖のシナリオ。``None`` は平常時。
SCENARIOS = [
    ("baseline", None),
    ("suez_closed", "CHOKE_SUEZ"),
    ("malacca_closed", "CHOKE_MALACCA"),
    ("panama_closed", "CHOKE_PANAMA"),
    ("bab_closed", "CHOKE_BAB_EL_MANDEB"),
    ("hormuz_closed", "CHOKE_HORMUZ"),
]


def gate_cells(graph, choke_id: str) -> set[int]:
    c = BY_ID[choke_id]
    if c.type != "STRAIT":
        return set()
    out = set()
    rows, cols = graph.rows, graph.cols
    dlat = c.gate_radius_km / 111.32
    dlon = c.gate_radius_km / max(1e-6, 111.32 * math.cos(math.radians(c.lat)))
    i_lo = max(0, int((90.0 - (c.lat + dlat)) / RESOLUTION_DEG) - 1)
    i_hi = min(rows - 1, int((90.0 - (c.lat - dlat)) / RESOLUTION_DEG) + 1)
    j_lo = int(((c.lon - dlon) + 180.0) / RESOLUTION_DEG) - 1
    j_hi = int(((c.lon + dlon) + 180.0) / RESOLUTION_DEG) + 1
    for i in range(i_lo, i_hi + 1):
        for j in range(j_lo, j_hi + 1):
            jj = j % cols
            lon, lat = cell_center(i, jj, RESOLUTION_DEG)
            if haversine_km(c.lon, c.lat, lon, lat) <= c.gate_radius_km:
                out.add(i * cols + jj)
    return out


def main() -> int:
    graph, nav = load_graph()
    routes_doc = json.loads(
        (Path(__file__).resolve().parent.parent / "public" / "data" / "infrastructure" / "shipping_routes.json").read_text(
            encoding="utf-8"
        )
    )
    nodes = {p["unlocode"]: p["grid_cell"][0] * graph.cols + p["grid_cell"][1] for p in routes_doc["ports"]}

    cases = []
    for scenario, choke_id in SCENARIOS:
        if choke_id is None:
            g = graph
        else:
            c = BY_ID[choke_id]
            if c.type == "CANAL":
                g = graph.with_closure(blocked_canals={choke_id}, canal_penalty=math.inf)
            else:
                g = graph.with_closure(blocked_cells=gate_cells(graph, choke_id), cell_penalty=math.inf)
        for src_l, dst_l, note in CASES:
            src, dst = nodes[src_l], nodes[dst_l]
            dist, prev = g.dijkstra(src, targets={dst})
            path = g.path(prev, src, dst) if dst in dist else None
            cases.append(
                {
                    "scenario": scenario,
                    "closed_chokepoint": choke_id,
                    "source": src_l,
                    "target": dst_l,
                    "note": note,
                    "source_cell": src,
                    "target_cell": dst,
                    "reachable": path is not None,
                    # 17 桁の十進で書くと倍精度を往復できる
                    "distance_km": repr(float(g.path_length_km(path))) if path else None,
                    "path_len": len(path) if path else None,
                    "path": path,
                }
            )
            print(f"  {scenario:16s} {src_l}->{dst_l}: {'到達不能' if path is None else f'{g.path_length_km(path):.3f} km / {len(path)} セル'}")

    doc = {
        "generated_at": routes_doc["generated_at"],
        "grid": {"rows": graph.rows, "cols": graph.cols, "resolution_deg": RESOLUTION_DEG},
        "nav_grid_note": (
            "TypeScript 側は public/data/infrastructure/nav_grid.json を読み、"
            "同じ重み表・同じ近傍順・同じ同点規則で探索して、この距離と経路に一致すること"
        ),
        "tie_break_rule": "優先度は (距離, セル番号)。緩和は 新 < 既存 のときだけ。セル番号 = 行 × 列数 + 列",
        "neighbour_order": "di ∈ (-1,0,1) の外側ループ、dj ∈ (-1,0,1) の内側ループ。(0,0) は除く",
        "cases": cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT} ({OUT.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
