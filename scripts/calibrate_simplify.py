"""表示用ポリゴンの簡略化許容度を測る(loop_002 stage 2)。

配信サイズと形の崩れの釣り合いを、**数字を書く前に**測る(HC-135)。

崩れの尺度は面積の相対変化にする。頂点数だけでは「どれだけ形が変わったか」が言えない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geopandas as gpd  # noqa: E402
import shapely  # noqa: E402
import shapely.geometry  # noqa: E402

from etl.config import RAW  # noqa: E402

NE_ZIP = f"zip://{RAW / 'naturalearth' / 'ne_10m_admin_0_countries.zip'}"
EEZ_ZIP = (
    f"zip://{RAW / 'marineregions' / 'World_EEZ_v12_20231025_LR.zip'}"
    "!World_EEZ_v12_20231025_LR/eez_v12_lowres.shp"
)

TOLERANCES = (0.0, 0.01, 0.02, 0.05, 0.1, 0.2)


def count_vertices(geom) -> int:
    if geom is None or geom.is_empty:
        return 0
    return int(shapely.get_coordinates(geom).shape[0])


def polygonal_only(geom):
    """make_valid が返す GeometryCollection から面の部分だけを取り出す。

    ``make_valid`` は自己交差の修復で線分を副産物として返すことがある。
    表示用のポリゴンに線が混ざると、面積の比較も描画も意味が変わる。
    """
    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if geom.geom_type == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not parts:
            return shapely.geometry.Polygon()
        return shapely.union_all(parts)
    return shapely.geometry.Polygon()


def measure(gdf: gpd.GeoDataFrame, name: str, keep_cols: list[str], out: dict) -> None:
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.make_valid().apply(polygonal_only)
    base_area = float(gdf.geometry.area.sum())
    base_v = int(sum(count_vertices(g) for g in gdf.geometry))
    rows = {}
    tmp = Path(__file__).resolve().parent.parent / "data" / "processed" / "_simplify_probe.geojson"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    for tol in TOLERANCES:
        g2 = gdf.copy()
        if tol > 0:
            g2["geometry"] = g2.geometry.simplify(tolerance=tol, preserve_topology=True).make_valid().apply(polygonal_only)
        g2 = g2[keep_cols + ["geometry"]]
        g2 = g2[~g2.geometry.is_empty]
        tmp.unlink(missing_ok=True)
        g2.to_file(tmp, driver="GeoJSON")
        size = tmp.stat().st_size
        v = int(sum(count_vertices(g) for g in g2.geometry))
        area = float(g2.geometry.area.sum())
        rows[str(tol)] = {
            "features": int(len(g2)),
            "vertices": v,
            "vertex_ratio": round(v / base_v, 4),
            "bytes": size,
            "mb": round(size / 1024 / 1024, 3),
            "area_rel_change": round((area - base_area) / base_area, 6),
            "invalid": int((~g2.geometry.is_valid).sum()),
        }
        print(f"  {name} tol={tol}: {size/1024/1024:6.2f} MB v={v:>8,} 面積変化 {rows[str(tol)]['area_rel_change']:+.5f}")
    tmp.unlink(missing_ok=True)
    out[name] = {"baseline_vertices": base_v, "tolerances": rows}


def main() -> int:
    out: dict = {"measured_at": "2026-09-07", "note": "面積は EPSG:4326 の度^2。相対変化だけを見る"}

    print("countries…")
    ne = gpd.read_file(NE_ZIP, columns=["ADM0_A3", "NAME", "CONTINENT", "REGION_UN", "geometry"])
    measure(ne, "countries", ["ADM0_A3", "NAME", "CONTINENT", "REGION_UN"], out)

    print("eez…")
    eez = gpd.read_file(EEZ_ZIP, columns=["MRGID", "GEONAME", "SOVEREIGN1", "ISO_TER1", "POL_TYPE", "geometry"])
    measure(eez, "eez", ["MRGID", "GEONAME", "SOVEREIGN1", "ISO_TER1", "POL_TYPE"], out)

    dest = Path(__file__).resolve().parent.parent / "data" / "processed" / "simplify_calibration.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
