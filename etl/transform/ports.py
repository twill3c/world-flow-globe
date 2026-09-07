"""SRC-004 → ``public/data/infrastructure/ports.geojson``(SPEC.md F-03)。

## 国コードは名前から引かない

出典が持っているのは国の**名前**(``"Bahamas"``, ``"Korea, Republic of"``)であって
ISO コードではない。名前で照合すると、表記ゆれのぶんだけ静かに外れる。

代わりに **UN/LOCODE の先頭 2 文字**を使う。UN/LOCODE は定義上
「ISO 3166-1 alpha-2 の国コード + 3 文字の地名コード」なので、
国コードは**識別子そのものに入っている**。これを Natural Earth の
``ISO_A2_EH → ISO_A3_EH`` で alpha-3 に写す。

引けなかったものは ``null`` のまま運び、件数と一覧をマニフェストに残す。
**推測で埋めない。**

## 航行グリッドへの吸着

港の座標は岸にあるので、0.5 度のセルでは陸のセルに落ちることが多い。
いちばん近い航行可能セルへ吸着させ、**吸着距離を港ごとに残す**。
遠すぎて吸着できなかった港は ``routable: false`` として運ぶ ——
黙って落とすと、経路が引けない理由が画面から分からなくなる。
"""

from __future__ import annotations

import base64
import json
import math

import geopandas as gpd
import numpy as np

from etl.config import PUBLIC_INFRA, RAW
from etl.download.manifest import utc_now_iso, write_manifest
from etl.io.wb_ports import read_wb_ports
from etl.transform.geo import write_feature_collection
from etl.transform.nav_grid import OUT as NAV_OUT
from etl.transform.nav_grid import RESOLUTION_DEG, cell_center, cell_index

SRC = RAW / "worldbank" / "attributed_ports.geojson"
NE_ZIP = RAW / "naturalearth" / "ne_10m_admin_0_countries.zip"
OUT = PUBLIC_INFRA / "ports.geojson"
COORD_PRECISION = 4

#: 吸着を許す最大距離(km)。これを超えたら「経路に使えない港」とする。
#: 0.5 度セルの対角はおよそ 78 km なので、2 セル分を上限にした。
MAX_SNAP_KM = 160.0

EARTH_R_KM = 6371.0088


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def load_alpha2_to_alpha3() -> dict[str, str]:
    """Natural Earth から alpha-2 → alpha-3 の対応を作る。

    同じ alpha-2 を持つ行が複数ある(フランスとクリッパートン島など)が、
    **alpha-3 の値は一致する**ことを 2026-09-07 に実測した。
    一致しないものが来たら例外にする —— 黙ってどちらかを採らない。
    """
    gdf = gpd.read_file(f"zip://{NE_ZIP}", columns=["ISO_A2_EH", "ISO_A3_EH", "geometry"])
    out: dict[str, str] = {}
    for a2, a3 in zip(gdf["ISO_A2_EH"], gdf["ISO_A3_EH"]):
        if not a2 or a2 == "-99" or not a3 or a3 == "-99":
            continue
        if a2 in out and out[a2] != a3:
            raise ValueError(f"alpha-2 {a2} が複数の alpha-3 を指す: {out[a2]} / {a3}")
        out[a2] = a3
    return out


def load_nav_mask() -> np.ndarray:
    doc = json.loads(NAV_OUT.read_text(encoding="utf-8"))
    bits = np.unpackbits(np.frombuffer(base64.b64decode(doc["mask_base64"]), dtype=np.uint8))
    return bits[: doc["rows"] * doc["cols"]].reshape(doc["rows"], doc["cols"]).astype(bool)


def main_ocean_cells(mask: np.ndarray) -> set[int]:
    """世界の海として繋がっているセルの集合を返す(運河の辺を含む)。

    **「近くに海がある」と「そこから世界へ出られる」は別のことである。**
    はじめは吸着できたかどうかだけで「経路に使える港」を決めていたが、
    バンクーバーが**孤立した内海のセル**に吸着し、他の 25 港すべてから
    到達不能になった(2026-09-07 実測)。近さは連結性を意味しない。
    """
    from collections import deque

    rows, cols = mask.shape
    doc = json.loads(NAV_OUT.read_text(encoding="utf-8"))
    extra: dict[int, list[int]] = {}
    for c in doc["canals"]:
        a = c["cells"][0][0] * cols + c["cells"][0][1]
        b = c["cells"][-1][0] * cols + c["cells"][-1][1]
        extra.setdefault(a, []).append(b)
        extra.setdefault(b, []).append(a)

    seen = np.zeros(mask.shape, dtype=bool)
    best: set[int] = set()
    for si in range(rows):
        for sj in range(cols):
            if not mask[si, sj] or seen[si, sj]:
                continue
            comp: set[int] = set()
            q = deque([(si, sj)])
            seen[si, sj] = True
            while q:
                i, j = q.popleft()
                comp.add(i * cols + j)
                for di in (-1, 0, 1):
                    ni = i + di
                    if ni < 0 or ni >= rows:
                        continue
                    for dj in (-1, 0, 1):
                        if di == 0 and dj == 0:
                            continue
                        nj = (j + dj) % cols
                        if mask[ni, nj] and not seen[ni, nj]:
                            seen[ni, nj] = True
                            q.append((ni, nj))
                for m in extra.get(i * cols + j, ()):
                    mi, mj = divmod(m, cols)
                    if mask[mi, mj] and not seen[mi, mj]:
                        seen[mi, mj] = True
                        q.append((mi, mj))
            if len(comp) > len(best):
                best = comp
    return best


def snap_to_navigable(
    mask: np.ndarray, lon: float, lat: float, max_km: float = MAX_SNAP_KM
) -> tuple[tuple[int, int] | None, float | None]:
    """いちばん近い航行可能セルを返す。決定論的に選ぶ(同距離なら row, col の小さい方)。"""
    rows, cols = mask.shape
    i0, j0 = cell_index(lon, lat, RESOLUTION_DEG)
    if mask[i0, j0]:
        clon, clat = cell_center(i0, j0, RESOLUTION_DEG)
        return (i0, j0), haversine_km(lon, lat, clon, clat)

    best: tuple[int, int] | None = None
    best_km = math.inf
    # 半径を広げながら環状に探す。max_km / セル幅ぶんまで。
    max_ring = int(max_km / (RESOLUTION_DEG * 111.32 * 0.5)) + 2
    for ring in range(1, max_ring + 1):
        found_any = False
        for di in range(-ring, ring + 1):
            for dj in range(-ring, ring + 1):
                if max(abs(di), abs(dj)) != ring:
                    continue
                i, j = i0 + di, (j0 + dj) % cols
                if i < 0 or i >= rows or not mask[i, j]:
                    continue
                found_any = True
                clon, clat = cell_center(i, j, RESOLUTION_DEG)
                d = haversine_km(lon, lat, clon, clat)
                if d < best_km - 1e-9 or (
                    abs(d - best_km) <= 1e-9 and best is not None and (i, j) < best
                ):
                    best, best_km = (i, j), d
        # 一つ内側の環まで見終わっていれば、それ以上外は近くなりえない
        if found_any and best is not None and best_km <= ring * RESOLUTION_DEG * 111.32 * 0.5:
            break
    if best is None or best_km > max_km:
        return None, None
    return best, best_km


def build_ports() -> dict:
    res = read_wb_ports(SRC)
    a2a3 = load_alpha2_to_alpha3()
    mask = load_nav_mask()
    ocean = main_ocean_cells(mask)
    cols = mask.shape[1]

    features = []
    unmapped: list[str] = []
    unroutable: dict[str, str] = {}
    snap_km: list[float] = []

    for rec in res.records:
        alpha2 = rec.locode[:2]
        iso3 = a2a3.get(alpha2)
        if iso3 is None:
            unmapped.append(rec.locode)

        cell, dist = snap_to_navigable(mask, rec.lon, rec.lat)
        # 吸着できただけでは足りない。**そのセルから世界の海へ出られること**まで要る。
        in_ocean = cell is not None and (cell[0] * cols + cell[1]) in ocean
        routable = in_ocean
        if cell is None:
            unroutable[rec.locode] = "上限内に航行可能セルが無い(内陸水路)"
        elif not in_ocean:
            unroutable[rec.locode] = "吸着先が孤立した水域で、世界の海と繋がっていない"
        else:
            snap_km.append(dist)
        if not routable:
            cell, dist = None, None

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "port_id": f"PORT_{rec.locode}",
                    "name": rec.name,
                    "name_ascii": rec.name_wo_diac,
                    # 出典の国名は加工せずそのまま運ぶ
                    "country_name": rec.country_name,
                    "country_iso2": alpha2,
                    # 引けなければ null。推測で埋めない
                    "country_iso3": iso3,
                    "unlocode": rec.locode,
                    "status": rec.status,
                    "function": rec.function,
                    # 出典のフィールド名のまま運ぶ。単位は出典に明記が無い
                    "outflows": rec.outflows,
                    "outflows_confidence": "STATISTICAL",
                    "grid_cell": list(cell) if cell else None,
                    "snap_km": round(dist, 2) if dist is not None else None,
                    "routable": routable,
                    "confidence": "OBSERVED",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(rec.lon, COORD_PRECISION), round(rec.lat, COORD_PRECISION)],
                },
            }
        )

    ids = [f["properties"]["port_id"] for f in features]
    if len(set(ids)) != len(ids):
        raise ValueError("port_id が一意でない")

    write_feature_collection(
        OUT,
        features,
        layer_id="PORT",
        confidence="OBSERVED",
        source_id="SRC-004",
        extra={
            "coordinate_precision": COORD_PRECISION,
            "position_confidence": "OBSERVED",
            "outflows_confidence": "STATISTICAL",
            "outflows_note": (
                "出典 World Bank のフィールド名のまま運んでいる。"
                "単位は出典に明記が無いため、無次元の相対量として扱うこと"
            ),
            "snap": {
                "max_snap_km": MAX_SNAP_KM,
                "grid_resolution_deg": RESOLUTION_DEG,
                "unroutable_count": len(unroutable),
                "unroutable_locodes": sorted(unroutable),
                "unroutable_reasons": {k: unroutable[k] for k in sorted(unroutable)},
            },
            "country_code": {
                "method": "UN/LOCODE の先頭 2 文字(ISO 3166-1 alpha-2)→ Natural Earth ISO_A3_EH",
                "unmapped_count": len(unmapped),
                "unmapped_locodes": sorted(unmapped),
            },
            "collapsed_from_source": {
                "raw_feature_count": res.raw_feature_count,
                "collapsed_count": res.collapsed_count,
                "collapsed_locodes": res.collapsed_locodes,
            },
        },
    )

    stats = {
        "ports": len(features),
        "raw_feature_count": res.raw_feature_count,
        "collapsed_count": res.collapsed_count,
        "unmapped_country": len(unmapped),
        "unroutable": len(unroutable),
        "snap_km_median": round(float(np.median(snap_km)), 2) if snap_km else None,
        "snap_km_max": round(max(snap_km), 2) if snap_km else None,
        "bytes": OUT.stat().st_size,
    }
    write_manifest(
        dataset_id="SRC-004",
        provider="World Bank",
        dataset="Global International Ports",
        source_filename=SRC.name,
        source_path=SRC,
        retrieved_at=utc_now_iso(),
        output="public/data/infrastructure/ports.geojson",
        output_path=OUT,
        records=len(features),
        notes={"transform": stats, "unmapped_locodes": sorted(unmapped)},
    )
    return stats


if __name__ == "__main__":
    print(json.dumps(build_ports(), ensure_ascii=False, indent=2))
