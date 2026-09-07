"""経路探索の検査(SPEC.md F-05 / F-09 / F-16 / G-06〜G-08 / G-19〜G-21)。

## オラクルの出所

| 検査 | 出所 | なぜ循環しないか |
|---|---|---|
| 経路の下界(T-401) | 測地線距離 | **経路探索を一切使わずに**計算できる |
| 経路長の再計算(T-402) | セル中心どうしの haversine の総和 | 重み表を使わずに、座標から直に足す |
| 閉鎖の単調性(T-403) | 同じ始終点の平常時と閉鎖時 | 比較対象が自分自身の別条件なので、外部の値を要らない |
| 決定論(T-404) | 同じ入力の二度の実行 | — |

**重い探索は golden の再生で置き換える。** 実際に Dijkstra を走らせるのは
決定論と単調性の確認に必要な最小限だけにする(1 回およそ 5〜8 秒)。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from etl.config import ROOT
from etl.transform.nav_grid import RESOLUTION_DEG, cell_center
from etl.transform.routes import load_graph
from etl.transform.routing import (
    POLAR_LIMIT_NORTH_DEG,
    POLAR_LIMIT_SOUTH_DEG,
    haversine_km,
)

GOLDEN = ROOT / "tests" / "fixtures" / "golden_routes.json"
ROUTES = ROOT / "public" / "data" / "infrastructure" / "shipping_routes.json"
NAV = ROOT / "public" / "data" / "infrastructure" / "nav_grid.json"


@pytest.fixture(scope="module")
def golden():
    if not GOLDEN.exists():
        pytest.skip("golden_routes.json が未生成")
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def routes():
    if not ROUTES.exists():
        pytest.skip("shipping_routes.json が未生成")
    return json.loads(ROUTES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def graph():
    if not NAV.exists():
        pytest.skip("nav_grid.json が未生成")
    g, _ = load_graph()
    return g


def _golden_by(g: dict, scenario: str) -> dict:
    return {(c["source"], c["target"]): c for c in g["cases"] if c["scenario"] == scenario}


# ---------------------------------------------------------------------------
# T-401 経路の下界(G-07)—— 非循環
# ---------------------------------------------------------------------------


def test_t401_every_route_is_at_least_as_long_as_the_geodesic(routes):
    """推定航路は、両端を結ぶ大圏より短くなりえない。

    この不等式は**経路探索の実装を一度も使わずに**確かめられる。
    破れたら、重みか経路復元のどちらかが壊れている。
    """
    assert len(routes["routes"]) > 100, "走査対象が空でないこと"
    for r in routes["routes"]:
        assert r["distance_km"] >= r["geodesic_km"] - 1e-6, (
            f"{r['route_id']}: {r['distance_km']} < 測地線 {r['geodesic_km']}"
        )


def test_t401_detour_ratio_is_reported_and_sane(routes):
    ratios = [r["detour_ratio"] for r in routes["routes"]]
    assert all(x is not None for x in ratios), "迂回率が空の航路がある(距離 0 の航路を出している)"
    assert all(x >= 1.0 - 1e-9 for x in ratios)
    # 迂回率が全部 1.0 ちょうどなら、それは陸を無視して直線を引いている
    assert max(ratios) > 1.2, "陸を避けている痕跡が無い"


def test_t401_same_cell_pairs_are_excluded_and_named(routes):
    """同じセルに同居した港の組は、0 km の航路にせず名前つきで残す。

    2026-09-07 実測: シンガポールとタンジュン・ペレパス(約 40 km)が同居する。
    """
    pairs = routes["same_cell_pairs"]
    assert isinstance(pairs, list)
    ids = {p["unlocode"] for p in routes["ports"]}
    for a, b in pairs:
        assert a in ids and b in ids
    emitted = {(r["source_port_id"], r["target_port_id"]) for r in routes["routes"]}
    for a, b in pairs:
        assert (f"PORT_{a}", f"PORT_{b}") not in emitted
    for r in routes["routes"]:
        assert r["distance_km"] > 0.0, f"{r['route_id']} の距離が 0"


def test_t401_confidence_is_separated(routes):
    """距離は INFERRED、所要日数は ESTIMATED。**同じ地物で階級が違う**(G-05)。"""
    assert routes["confidence"] == "INFERRED"
    assert routes["transit_time_confidence"] == "ESTIMATED"
    assert "実船の航跡ではない" in routes["not_a_ship_track"]
    for r in routes["routes"]:
        assert r["confidence"] == "INFERRED"
        assert r["transit_time_confidence"] == "ESTIMATED"


# ---------------------------------------------------------------------------
# T-402 golden の経路そのものを検算する(非循環)
# ---------------------------------------------------------------------------


def test_t402_golden_paths_recompute_to_the_recorded_distance(golden, graph):
    """記録された距離を、**重み表を使わずに**座標から足し直して確かめる。

    Dijkstra の答えをそのまま信じない。経路と距離が食い違う故障
    (前任者の更新漏れ・経路復元の誤り)は、この足し直しでしか捕まらない。
    """
    canal_pairs = {(c["a"], c["b"]) for c in graph.canals} | {
        (c["b"], c["a"]) for c in graph.canals
    }
    canal_weight = {
        (c["a"], c["b"]): c["weight_km"] for c in graph.canals
    } | {(c["b"], c["a"]): c["weight_km"] for c in graph.canals}

    checked = 0
    for case in golden["cases"]:
        if not case["reachable"]:
            continue
        path = case["path"]
        assert path[0] == case["source_cell"] and path[-1] == case["target_cell"]
        assert len(set(path)) == len(path), "経路に同じセルが二度現れている"
        total = 0.0
        for a, b in zip(path, path[1:]):
            (ia, ja), (ib, jb) = divmod(a, graph.cols), divmod(b, graph.cols)
            di = abs(ia - ib)
            dj = min(abs(ja - jb), graph.cols - abs(ja - jb))
            if max(di, dj) > 1:
                assert (a, b) in canal_pairs, f"隣接していないのに運河でもない: {a}→{b}"
                total += canal_weight[(a, b)]
            else:
                total += haversine_km(
                    *cell_center(ia, ja, RESOLUTION_DEG), *cell_center(ib, jb, RESOLUTION_DEG)
                )
        assert total == pytest.approx(float(case["distance_km"]), rel=1e-9), (
            f"{case['scenario']} {case['source']}→{case['target']}: "
            f"記録 {case['distance_km']} vs 足し直し {total}"
        )
        checked += 1
    assert checked >= 6, "検算した経路が少なすぎる"


def test_t402_golden_paths_stay_inside_the_polar_limit(golden, graph):
    """既定の仮定(高緯度は通らない)が、golden の全経路で守られていること。"""
    for case in golden["cases"]:
        if not case["reachable"]:
            continue
        for n in case["path"]:
            _, lat = cell_center(*divmod(n, graph.cols), RESOLUTION_DEG)
            assert POLAR_LIMIT_SOUTH_DEG <= lat <= POLAR_LIMIT_NORTH_DEG, (
                f"{case['scenario']} {case['source']}→{case['target']} が緯度 {lat} を通っている"
            )


# ---------------------------------------------------------------------------
# T-403 閉鎖の単調性(G-08)
# ---------------------------------------------------------------------------


def test_t403_closing_never_shortens_a_route(golden):
    """チョークポイントを閉じたとき、経路は**長くなるか到達不能になる**。短くならない。"""
    base = _golden_by(golden, "baseline")
    scenarios = {c["scenario"] for c in golden["cases"]} - {"baseline"}
    assert scenarios, "閉鎖シナリオが無い(この検査が働いていない)"
    compared = 0
    for scenario in sorted(scenarios):
        for key, case in _golden_by(golden, scenario).items():
            b = base[key]
            assert b["reachable"], "平常時に到達できていること(対照の前提)"
            if not case["reachable"]:
                continue
            assert float(case["distance_km"]) >= float(b["distance_km"]) - 1e-6, (
                f"{scenario} {key}: 閉鎖したのに短くなった"
            )
            compared += 1
    assert compared >= 20


def test_t403_closing_a_used_chokepoint_actually_changes_something(golden, routes):
    """**緩みすぎを止める対**(HC-041)。

    すべての閉鎖が「何も変えない」なら T-403 は緑のまま何も言わない。
    実際にどれかの経路が伸びるか不通になることを要求する。
    """
    base = _golden_by(golden, "baseline")
    changed = 0
    for case in golden["cases"]:
        if case["scenario"] == "baseline":
            continue
        b = base[(case["source"], case["target"])]
        if not case["reachable"] or float(case["distance_km"]) > float(b["distance_km"]) + 1e-6:
            changed += 1
    assert changed >= 5, f"閉鎖が経路を変えた件数が {changed} 件しかない"


def test_t403_hormuz_seals_the_persian_gulf(golden):
    """ホルムズを閉じるとジェベル・アリが外界から切れる —— 台帳の注記の実測。"""
    case = _golden_by(golden, "hormuz_closed")[("AEJEA", "NLRTM")]
    assert case["reachable"] is False


# ---------------------------------------------------------------------------
# T-404 決定論と再現(実際に探索を回す)
# ---------------------------------------------------------------------------


def test_t404_dijkstra_reproduces_the_golden_path_exactly(golden, graph):
    """同じグリッド・同じ始終点で、記録した経路と**セル列まで**一致する。"""
    case = _golden_by(golden, "baseline")[("SGSIN", "NLRTM")]
    dist, prev = graph.dijkstra(case["source_cell"], targets={case["target_cell"]})
    path = graph.path(prev, case["source_cell"], case["target_cell"])
    assert path == case["path"]
    assert graph.path_length_km(path) == pytest.approx(float(case["distance_km"]), rel=1e-12)


def test_t404_dijkstra_is_deterministic(graph, golden):
    case = _golden_by(golden, "baseline")[("ZADUR", "KRPUS")]
    runs = []
    for _ in range(2):
        _, prev = graph.dijkstra(case["source_cell"], targets={case["target_cell"]})
        runs.append(graph.path(prev, case["source_cell"], case["target_cell"]))
    assert runs[0] == runs[1]


# ---------------------------------------------------------------------------
# T-405 極域の仮定(F-16 / G-20)—— 仮定が実際に効いていることを測る
# ---------------------------------------------------------------------------


def test_t405_without_the_polar_limit_the_route_goes_over_the_arctic():
    """**仮定を外すと何が起きるか**を測って残す。

    海氷を持たない模型では、スエズ閉鎖時の最短路が北極点の上を通る
    (2026-09-07 実測: 626 セル中 408 セルが 70 度以北、最北 89.75 度)。
    この対照が無いと、極域制限が「効いている」ことを誰も確かめていないことになる。
    """
    import math as _m

    from etl.transform.chokepoints import BY_ID  # noqa: F401  (台帳の存在を確かめる)

    g_free, _ = load_graph(allow_polar=True)
    g_lim, _ = load_graph(allow_polar=False)
    doc = json.loads(ROUTES.read_text(encoding="utf-8"))
    nodes = {p["unlocode"]: p["grid_cell"][0] * g_lim.cols + p["grid_cell"][1] for p in doc["ports"]}
    src, dst = nodes["SGSIN"], nodes["NLRTM"]

    free = g_free.with_closure(blocked_canals={"CHOKE_SUEZ"}, canal_penalty=_m.inf)
    lim = g_lim.with_closure(blocked_canals={"CHOKE_SUEZ"}, canal_penalty=_m.inf)

    _, prev_f = free.dijkstra(src, targets={dst})
    _, prev_l = lim.dijkstra(src, targets={dst})
    path_f = free.path(prev_f, src, dst)
    path_l = lim.path(prev_l, src, dst)

    lat_f = max(cell_center(*divmod(n, free.cols), RESOLUTION_DEG)[1] for n in path_f)
    lat_l = max(cell_center(*divmod(n, lim.cols), RESOLUTION_DEG)[1] for n in path_l)
    assert lat_f > 80.0, f"制限を外しても北極へ行かない({lat_f}N)。この対照は前提を失っている"
    assert lat_l <= POLAR_LIMIT_NORTH_DEG
    # 制限を掛けた方が長い —— 仮定は「安い近道」を禁じている
    assert free.path_length_km(path_f) < lim.path_length_km(path_l)


def test_t405_nav_grid_declares_the_assumption_as_estimated():
    doc = json.loads(NAV.read_text(encoding="utf-8"))
    p = doc["polar_limit"]
    assert p["confidence"] == "ESTIMATED", "地理ではなく仮定であることを名乗ること"
    assert p["toggleable"] is True
    assert p["north_deg"] == POLAR_LIMIT_NORTH_DEG
    assert p["south_deg"] == POLAR_LIMIT_SOUTH_DEG
    assert "海氷" in p["rationale"]


# ---------------------------------------------------------------------------
# T-406 容量減の単調性
# ---------------------------------------------------------------------------


def test_t406_capacity_reduction_is_monotone(graph, golden):
    """容量を減らすほど、その経路のコストは増える(減らない)。"""
    case = _golden_by(golden, "baseline")[("SGSIN", "NLRTM")]
    src, dst = case["source_cell"], case["target_cell"]
    prev_cost = None
    for percent in (0, 50, 90):
        penalty = 1.0 if percent == 0 else 1.0 / (1.0 - percent / 100.0)
        g = graph.with_closure(blocked_canals={"CHOKE_SUEZ"}, canal_penalty=penalty)
        dist, _ = g.dijkstra(src, targets={dst})
        cost = dist[dst]
        if prev_cost is not None:
            assert cost >= prev_cost - 1e-6, f"容量 -{percent}% でコストが下がった"
        prev_cost = cost


# ---------------------------------------------------------------------------
# T-407 チョークポイントが航路網に現れる(G-19)
# ---------------------------------------------------------------------------


def test_t407_every_chokepoint_is_used_by_at_least_one_route(routes):
    """5 つのチョークポイントがどれも使われていなければ、閉鎖の実験ができない。

    港の選び方(outflows 上位だけ)ではパナマとホルムズが飾りになった
    (2026-09-07 実測)。地理の錨を足した後にこれが通ることを固定する。
    """
    from etl.transform.chokepoints import CHOKEPOINTS

    used: dict[str, int] = {}
    for r in routes["routes"]:
        for cid in r["chokepoints"]:
            used[cid] = used.get(cid, 0) + 1
    missing = [c.id for c in CHOKEPOINTS if used.get(c.id, 0) == 0]
    assert not missing, f"どの航路にも現れないチョークポイント: {missing}"


def test_t407_selection_rule_is_recorded_in_the_artifact(routes):
    rule = routes["selection_rule"]
    assert rule["top_by_outflows"] > 0
    assert len(rule["geographic_anchors"]) >= 5
    for a in rule["geographic_anchors"]:
        assert a["reason"], f"{a['unlocode']} に理由が無い"
    by = {p["unlocode"]: p for p in routes["ports"]}
    for a in rule["geographic_anchors"]:
        assert a["unlocode"] in by
    assert {p["selected_by"] for p in routes["ports"]} == {"outflows", "anchor"}
