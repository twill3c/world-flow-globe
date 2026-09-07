"""SRC-002 → ``public/data/base/eez.geojson``(SPEC.md F-02)。

**200NM / 重複主張 / 共同管轄を区別して運ぶ。** 出典の ``POL_TYPE`` は
2026-09-07 の実測で、**出典の 285 行**に対して
200NM 229 件 / Overlapping claim 35 件 / Joint regime 21 件。
これを一色で塗ると「どの国の海か決まっている」という嘘になる。

**出荷されるのは 284 件になる。** Overlapping claim のうち 1 件
(MRGID 48998 Perejil Island: Spain / Morocco)が**出典の時点で geometry を持たない**ため。
落とした 1 件は成果物の ``empty_in_source`` に名前ごと残す —— 数だけが減って
理由が分からない状態を作らない。

簡略化は較正で tol=0.1 度(``scripts/calibrate_simplify.py``、面積の相対変化 +0.00018)。
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

SRC_ZIP = RAW / "marineregions" / "World_EEZ_v12_20231025_LR.zip"
SRC_LAYER = "World_EEZ_v12_20231025_LR/eez_v12_lowres.shp"
SIMPLIFY_TOLERANCE_DEG = 0.1
COORD_PRECISION = 3
OUT = PUBLIC_BASE / "eez.geojson"

#: 出典の POL_TYPE をそのまま運ぶ。**独自の言い換えをしない** ——
#: 「重複主張」を「A 国の EEZ」に丸めた瞬間、この地図は政治的な主張になる。
KNOWN_POL_TYPES = {"200NM", "Overlapping claim", "Joint regime"}


def build_eez() -> dict:
    gdf = gpd.read_file(
        f"zip://{SRC_ZIP}!{SRC_LAYER}",
        columns=["MRGID", "GEONAME", "SOVEREIGN1", "ISO_TER1", "TERRITORY1", "POL_TYPE", "geometry"],
    )
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    dup = gdf["MRGID"].duplicated().sum()
    if dup:
        raise ValueError(f"MRGID が一意でない: 重複 {dup} 件")

    seen = set(gdf["POL_TYPE"].dropna().unique())
    unknown = seen - KNOWN_POL_TYPES
    if unknown:
        # 黙って通さない。新しい区分が来たら、色と説明を決めてから通す。
        raise ValueError(f"未知の POL_TYPE: {sorted(unknown)}")

    # 出典の時点で geometry が空の行を先に数え、**名前を控えてから**除く。
    # 2026-09-07 実測: 285 行のうち 1 行(MRGID 48998
    # "Overlapping claim Perejil Island: Spain / Morocco")が頂点 0 で届く。
    # 黙って落とすと、成果物が 284 件になった理由が誰にも分からなくなる。
    empty_mask = gdf.geometry.isna() | gdf.geometry.is_empty
    empty_in_source = [
        {"mrgid": int(r["MRGID"]), "geoname": r["GEONAME"], "pol_type": r["POL_TYPE"]}
        for _, r in gdf[empty_mask].iterrows()
    ]
    gdf = gdf[~empty_mask].copy()

    gdf["geometry"] = gdf.geometry.make_valid().apply(polygonal_only)
    before_v = int(sum(count_vertices(g) for g in gdf.geometry))
    # 簡略化の前に空になったものがあれば、それは**こちらの処理**が消したことになる。
    lost_by_make_valid = int((gdf.geometry.isna() | gdf.geometry.is_empty).sum())
    if lost_by_make_valid:
        raise ValueError(f"make_valid で {lost_by_make_valid} 件の面が消えた")
    gdf["geometry"] = (
        gdf.geometry.simplify(tolerance=SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
        .make_valid()
        .apply(polygonal_only)
    )

    lost_by_simplify = int((gdf.geometry.isna() | gdf.geometry.is_empty).sum())
    if lost_by_simplify:
        raise ValueError(f"簡略化で {lost_by_simplify} 件の面が消えた。許容度を下げること")

    features = []
    counts: dict[str, int] = {}
    for _, row in gdf.iterrows():
        geom = row.geometry
        assert_coordinates_in_range(geom, f"eez MRGID={row['MRGID']}")
        pol = row["POL_TYPE"]
        counts[pol] = counts.get(pol, 0) + 1
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "mrgid": int(row["MRGID"]),
                    "geoname": row["GEONAME"],
                    "sovereign": row["SOVEREIGN1"],
                    # 出典が空欄のところは None のまま運ぶ(0 や "" に化けさせない)
                    "iso_ter1": row["ISO_TER1"] if row["ISO_TER1"] else None,
                    "territory": row["TERRITORY1"] if row["TERRITORY1"] else None,
                    "pol_type": pol,
                    "confidence": "OBSERVED",
                },
                "geometry": geometry_to_geojson(geom, COORD_PRECISION),
            }
        )

    write_feature_collection(
        OUT,
        features,
        layer_id="EEZ",
        confidence="OBSERVED",
        source_id="SRC-002",
        extra={
            "simplify_tolerance_deg": SIMPLIFY_TOLERANCE_DEG,
            "coordinate_precision": COORD_PRECISION,
            "pol_type_counts": counts,
            "empty_in_source": empty_in_source,
        },
    )
    stats = {
        "features": len(features),
        "rows_in_source": len(features) + len(empty_in_source),
        "empty_in_source": empty_in_source,
        "vertices_before_simplify": before_v,
        "vertices_after_simplify": int(sum(count_vertices(g) for g in gdf.geometry)),
        "pol_type_counts": counts,
        "bytes": OUT.stat().st_size,
    }
    write_manifest(
        dataset_id="SRC-002",
        provider="Marine Regions (VLIZ)",
        dataset="World EEZ v12 (Low Resolution)",
        source_filename=SRC_ZIP.name,
        source_path=SRC_ZIP,
        retrieved_at=utc_now_iso(),
        output="public/data/base/eez.geojson",
        output_path=OUT,
        records=len(features),
        notes={"transform": stats, "simplify_tolerance_deg": SIMPLIFY_TOLERANCE_DEG},
    )
    return stats


if __name__ == "__main__":
    print(build_eez())
