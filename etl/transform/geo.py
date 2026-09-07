"""地理データ変換の共通部品。

出荷する GeoJSON は**自前で書く**。理由は二つ。

1. 座標の桁数を制御したいから。ドライバ任せだと 15 桁が出て、簡略化の許容度
   (0.05 度 ≈ 5 km)からすれば意味の無い桁でファイルが太る。
2. プロパティの並びと欠損の扱いを固定したいから(``null`` を 0 に化けさせない)。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import shapely
import shapely.geometry


def polygonal_only(geom):
    """``make_valid`` が返す GeometryCollection から面の部分だけを取り出す。

    ``make_valid`` は自己交差の修復で線分を副産物として返すことがある。
    表示用のポリゴンに線が混ざると面積も描画も意味が変わるので、面だけを残す。
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


def count_vertices(geom) -> int:
    if geom is None or geom.is_empty:
        return 0
    return int(shapely.get_coordinates(geom).shape[0])


def clean_optional(value):
    """出典の欠損を ``None`` にする。

    **pandas の欠損は ``float('nan')`` で、Python では真である。**
    ``value if value else None`` と書くと NaN がそのまま通り、
    ``json.dumps`` が規格外の ``NaN`` を書く。実際に踏んだ(HC-210)。
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _round_coords(obj, nd: int):
    if isinstance(obj, (int, float)):
        return round(float(obj), nd)
    if isinstance(obj, (list, tuple)):
        return [_round_coords(v, nd) for v in obj]
    return obj


def geometry_to_geojson(geom, precision: int) -> dict | None:
    if geom is None or geom.is_empty:
        return None
    mapping = shapely.geometry.mapping(geom)
    mapping["coordinates"] = _round_coords(mapping["coordinates"], precision)
    return mapping


def assert_coordinates_in_range(geom, label: str) -> None:
    """SPEC.md §7 G-01。範囲外があればその場で例外にする。"""
    if geom is None or geom.is_empty:
        return
    xs, ys = shapely.get_coordinates(geom).T
    if xs.size == 0:
        return
    if xs.min() < -180.0 or xs.max() > 180.0 or ys.min() < -90.0 or ys.max() > 90.0:
        raise ValueError(
            f"{label}: 座標が範囲外 lon[{xs.min()},{xs.max()}] lat[{ys.min()},{ys.max()}]"
        )


def write_feature_collection(
    dest: str | Path,
    features: list[dict],
    *,
    layer_id: str,
    confidence: str,
    source_id: str,
    extra: dict | None = None,
) -> Path:
    """FeatureCollection を書く。

    ``layer_id`` / ``confidence`` / ``source_id`` を必ず載せる。
    画面はここを読んで「この線は何なのか」を表示する(SPEC.md §4)。
    """
    doc = {
        "type": "FeatureCollection",
        "layer_id": layer_id,
        "confidence": confidence,
        "data_source": source_id,
        "features": features,
    }
    if extra:
        doc.update(extra)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # **`allow_nan=False` を外さない。** 既定の `json.dumps` は欠損値を
    # `NaN` と書く。これは JSON の規格に無いので、Python は読めてもブラウザは
    # 構文エラーで落ちる —— つまり **pytest が全部緑のまま、画面だけが壊れる**。
    # 2026-09-08 に実際に踏んだ(eez.geojson に NaN が 33 個。HC-210 の記録)。
    dest.write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        encoding="utf-8",
    )
    return dest
