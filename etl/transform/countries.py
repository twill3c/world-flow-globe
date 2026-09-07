"""SRC-001 → ``public/data/base/countries.geojson``(SPEC.md F-01)。

簡略化の許容度は較正で決めた(``scripts/calibrate_simplify.py``、2026-09-07 実測)。
tol=0.05 度で 2.77 MB・面積の相対変化 -0.00112。座標は小数 3 桁(約 111 m)まで残す ——
簡略化の誤差(約 5 km)より 1 桁以上細かいので、桁を落として形は変わらない。
"""

from __future__ import annotations

import geopandas as gpd

from etl.config import PUBLIC_BASE, RAW
from etl.download.manifest import utc_now_iso, write_manifest
from etl.transform.geo import (
    assert_coordinates_in_range,
    count_vertices,
    geometry_to_geojson,
    polygonal_only,
    write_feature_collection,
)

SRC_ZIP = RAW / "naturalearth" / "ne_10m_admin_0_countries.zip"
SIMPLIFY_TOLERANCE_DEG = 0.05
COORD_PRECISION = 3
OUT = PUBLIC_BASE / "countries.geojson"


def build_countries() -> dict:
    gdf = gpd.read_file(
        f"zip://{SRC_ZIP}",
        columns=["ADM0_A3", "NAME", "CONTINENT", "REGION_UN", "geometry"],
    )
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # 出典側の主キーが一意であることを、使う前に確かめる(HC-200)。
    dup = gdf["ADM0_A3"].duplicated().sum()
    if dup:
        raise ValueError(f"ADM0_A3 が一意でない: 重複 {dup} 件")

    gdf["geometry"] = gdf.geometry.make_valid().apply(polygonal_only)
    before_v = int(sum(count_vertices(g) for g in gdf.geometry))
    gdf["geometry"] = (
        gdf.geometry.simplify(tolerance=SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        .make_valid()
        .apply(polygonal_only)
    )

    features = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        assert_coordinates_in_range(geom, f"country {row['ADM0_A3']}")
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "iso_a3": row["ADM0_A3"],
                    "name": row["NAME"],
                    "continent": row["CONTINENT"],
                    "region": row["REGION_UN"],
                    "confidence": "OBSERVED",
                },
                "geometry": geometry_to_geojson(geom, COORD_PRECISION),
            }
        )

    write_feature_collection(
        OUT,
        features,
        layer_id="COUNTRY",
        confidence="OBSERVED",
        source_id="SRC-001",
        extra={
            "simplify_tolerance_deg": SIMPLIFY_TOLERANCE_DEG,
            "coordinate_precision": COORD_PRECISION,
        },
    )
    after_v = int(sum(count_vertices(g) for g in gdf.geometry))
    stats = {
        "features": len(features),
        "vertices_before_simplify": before_v,
        "vertices_after_simplify": after_v,
        "bytes": OUT.stat().st_size,
    }
    write_manifest(
        dataset_id="SRC-001",
        provider="Natural Earth",
        dataset="Admin 0 Countries (10m)",
        source_filename=SRC_ZIP.name,
        source_path=SRC_ZIP,
        retrieved_at=utc_now_iso(),
        output="public/data/base/countries.geojson",
        output_path=OUT,
        records=len(features),
        notes={"transform": stats, "simplify_tolerance_deg": SIMPLIFY_TOLERANCE_DEG},
    )
    return stats


if __name__ == "__main__":
    print(build_countries())
