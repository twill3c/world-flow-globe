"""``public/data/layers.json`` の生成(原典 §45・§46 / SPEC.md F-06・F-07・F-15)。

**手で書かない。** レイヤーの宣言と成果物の実体がずれると、画面が
「観測」と書きながら推定を描く、という故障になる(G-05)。
ここで宣言し、テストが成果物の実値と突き合わせる。

## 出せないレイヤーも枠として残す

原典 §45 が挙げるレイヤーのうち、本バージョンで出せないものがある。
**消さずに ``available: false`` と理由を持たせる。** 画面はそれを
「取得できていない」と表示する(F-15)。黙って消すと、
利用者からは「そのレイヤーは無い」のか「このアプリが手を抜いた」のかが分からない。
"""

from __future__ import annotations

import json

from etl.config import PUBLIC_DATA
from etl.download.manifest import utc_now_iso

OUT = PUBLIC_DATA / "layers.json"

LAYERS: list[dict] = [
    # ------------------------------------------------------------------ BASE
    {
        "id": "OCEAN",
        "name": "Ocean",
        "name_ja": "海洋",
        "category": "BASE",
        "renderer": "globe-surface",
        "visible_default": True,
        "opacity_default": 1.0,
        "z_order": 0,
        "confidence": "OBSERVED",
        "data_source": None,
        "available": True,
        "note": "地球儀そのものの面として描く。巨大な海域ポリゴンは取得しない(原典 §16)",
    },
    {
        "id": "COUNTRY",
        "name": "Countries",
        "name_ja": "国・領域",
        "category": "BASE",
        "renderer": "polygon",
        "visible_default": True,
        "opacity_default": 0.35,
        "z_order": 10,
        "confidence": "OBSERVED",
        "data_source": "SRC-001",
        "path": "base/countries.geojson",
        "available": True,
    },
    {
        "id": "TERRITORIAL_SEA",
        "name": "Territorial Seas (12NM)",
        "name_ja": "領海(12 海里)",
        "category": "BASE",
        "renderer": "polygon",
        "visible_default": False,
        "opacity_default": 0.25,
        "z_order": 20,
        "confidence": "OBSERVED",
        "data_source": "SRC-003",
        "available": False,
        "unavailable_reason": (
            "配布が氏名・所属・メールアドレスの入力と同意チェックを要する Web フォームで、"
            "機械取得できない(2026-09-07 実測)。海岸線から 12 海里の緩衝で作れば線は引けるが、"
            "それは観測ではなく推定になるので、代わりに出すことはしない"
        ),
    },
    {
        "id": "EEZ",
        "name": "Exclusive Economic Zones",
        "name_ja": "排他的経済水域",
        "category": "BASE",
        "renderer": "polygon",
        "visible_default": False,
        "opacity_default": 0.22,
        "z_order": 30,
        "confidence": "OBSERVED",
        "data_source": "SRC-002",
        "path": "base/eez.geojson",
        "available": True,
        "note": "200NM / 重複主張 / 共同管轄を色で区別する。一色で塗ると「決まっている」という嘘になる",
    },
    # -------------------------------------------------------- INFRASTRUCTURE
    {
        "id": "NAV_GRID",
        "name": "Navigable Grid",
        "name_ja": "航行可能グリッド",
        "category": "INFRASTRUCTURE",
        "renderer": "grid",
        "visible_default": False,
        "opacity_default": 0.35,
        "z_order": 40,
        "confidence": "INFERRED",
        "data_source": "SRC-001",
        "path": "infrastructure/nav_grid.json",
        "available": True,
        "note": "航路を引くときに船が通れることにしたセル。**これ自体が推定である**",
    },
    {
        "id": "SHIPPING_DENSITY",
        "name": "Shipping Density",
        "name_ja": "海運密度",
        "category": "INFRASTRUCTURE",
        "renderer": "heatmap",
        "visible_default": False,
        "opacity_default": 0.6,
        "z_order": 45,
        "confidence": "STATISTICAL",
        "data_source": "SRC-005",
        "available": False,
        "unavailable_reason": (
            "458 MB のラスタで、取得も集約も本バージョンの予算に収まらない。"
            "なお元データは 2015-01〜2021-02 の AIS 集計であって、現在の船の位置ではない"
        ),
    },
    {
        "id": "SHIPPING_ROUTE",
        "name": "Shipping Routes",
        "name_ja": "推定航路",
        "category": "INFRASTRUCTURE",
        "renderer": "line",
        "visible_default": True,
        "opacity_default": 0.75,
        "z_order": 50,
        "confidence": "INFERRED",
        "data_source": None,
        "path": "infrastructure/shipping_routes.json",
        "available": True,
        "note": "実船の航跡ではない。港・航行可能海域・チョークポイントから引いた推定(原典 §25)",
    },
    {
        "id": "PORT",
        "name": "Ports",
        "name_ja": "港",
        "category": "INFRASTRUCTURE",
        "renderer": "point",
        "visible_default": True,
        "opacity_default": 1.0,
        "z_order": 60,
        "confidence": "OBSERVED",
        "data_source": "SRC-004",
        "path": "infrastructure/ports.geojson",
        "available": True,
    },
    {
        "id": "CHOKEPOINT",
        "name": "Chokepoints",
        "name_ja": "チョークポイント",
        "category": "INFRASTRUCTURE",
        "renderer": "marker",
        "visible_default": True,
        "opacity_default": 1.0,
        "z_order": 70,
        "confidence": "OBSERVED",
        "data_source": None,
        "path": "infrastructure/chokepoints.json",
        "available": True,
        "note": "座標は表示用の参照点である。法的境界でも航行用座標でもない(原典 §32)",
    },
    # --------------------------------------------------------------- ECONOMY
    {
        "id": "PORT_THROUGHPUT",
        "name": "Port Throughput",
        "name_ja": "港の取扱量",
        "category": "ECONOMY",
        "renderer": "point-size",
        "visible_default": False,
        "opacity_default": 1.0,
        "z_order": 62,
        "confidence": "STATISTICAL",
        "data_source": "SRC-004",
        "path": "infrastructure/ports.geojson",
        "available": True,
        "note": "World Bank の outflows をそのまま使う。**単位は出典に明記が無い**ので相対量として扱う",
    },
    {
        "id": "TRADE_FLOW",
        "name": "Trade Flow",
        "name_ja": "貿易フロー",
        "category": "ECONOMY",
        "renderer": "arc",
        "visible_default": False,
        "opacity_default": 0.7,
        "z_order": 55,
        "confidence": "STATISTICAL",
        "data_source": "SRC-007",
        "available": False,
        "unavailable_reason": (
            "本バージョンでは出荷しない。国と国の貿易額(Comtrade)は、"
            "港と港の輸送量とは別の情報であり、片方から他方を作ると"
            "推定を統計に見せかけることになる(原典 §43)"
        ),
    },
    {
        "id": "CONNECTIVITY",
        "name": "Liner Shipping Connectivity",
        "name_ja": "定期船接続性",
        "category": "ECONOMY",
        "renderer": "point-size",
        "visible_default": False,
        "opacity_default": 1.0,
        "z_order": 63,
        "confidence": "STATISTICAL",
        "data_source": "SRC-006",
        "available": False,
        "unavailable_reason": "UNCTADstat の取得経路が未確立(2026-09-07 時点で HEAD が 403)",
    },
    # ------------------------------------------------------------ SIMULATION
    {
        "id": "CLOSURE",
        "name": "Closure",
        "name_ja": "閉鎖",
        "category": "SIMULATION",
        "renderer": "marker",
        "visible_default": True,
        "opacity_default": 1.0,
        "z_order": 80,
        "confidence": "SIMULATED",
        "data_source": None,
        "available": True,
    },
    {
        "id": "REROUTE",
        "name": "Reroute",
        "name_ja": "再ルート",
        "category": "SIMULATION",
        "renderer": "line",
        "visible_default": True,
        "opacity_default": 0.9,
        "z_order": 82,
        "confidence": "SIMULATED",
        "data_source": None,
        "available": True,
    },
    {
        "id": "PORT_CONGESTION",
        "name": "Port Congestion",
        "name_ja": "港の混雑",
        "category": "SIMULATION",
        "renderer": "point-color",
        "visible_default": True,
        "opacity_default": 1.0,
        "z_order": 84,
        "confidence": "SIMULATED",
        "data_source": None,
        "available": True,
    },
    # -------------------------------------------------------------------- AI
    {
        "id": "IMPACT_PREDICTION",
        "name": "Impact Prediction",
        "name_ja": "影響予測(ML)",
        "category": "AI",
        "renderer": "point-color",
        "visible_default": False,
        "opacity_default": 1.0,
        "z_order": 90,
        "confidence": "PREDICTED",
        "data_source": None,
        "available": False,
        "unavailable_reason": (
            "出荷しない。学習に使える実測が無く、手元にあるのは自分のシミュレータの"
            "出力だけである。それで学習した予測器は**シミュレータの再現以上のことを言わない**。"
            "原典 §3.2 が禁じる SIMULATION と PREDICTION の混同を、AI の側から破ることになる。"
            "代わりに決定論的な感度分析(容量減と期間を振って指標の応答を見る)を出す"
        ),
    },
]

PRESETS: list[dict] = [
    {
        "id": "WORLD_OVERVIEW",
        "name_ja": "世界の概観",
        "layers": ["OCEAN", "COUNTRY", "PORT", "SHIPPING_ROUTE", "CHOKEPOINT"],
    },
    {"id": "MARITIME", "name_ja": "海運", "layers": ["OCEAN", "COUNTRY", "SHIPPING_ROUTE", "PORT", "CHOKEPOINT", "NAV_GRID"]},
    {"id": "EEZ", "name_ja": "海洋境界", "layers": ["OCEAN", "COUNTRY", "EEZ"]},
    {"id": "TRADE", "name_ja": "取扱量", "layers": ["OCEAN", "COUNTRY", "PORT", "PORT_THROUGHPUT"]},
    {"id": "CHOKEPOINTS", "name_ja": "チョークポイント", "layers": ["OCEAN", "COUNTRY", "CHOKEPOINT", "SHIPPING_ROUTE"]},
    {
        "id": "SIMULATION",
        "name_ja": "シミュレーション",
        "layers": ["OCEAN", "COUNTRY", "SHIPPING_ROUTE", "PORT", "CHOKEPOINT", "CLOSURE", "REROUTE", "PORT_CONGESTION"],
    },
]


def build_layers() -> dict:
    ids = [x["id"] for x in LAYERS]
    if len(set(ids)) != len(ids):
        raise ValueError("レイヤー ID が一意でない")
    for layer in LAYERS:
        if layer["available"] and layer.get("path") is None and layer["renderer"] not in (
            "globe-surface",
            "marker",
            "line",
            "point-color",
        ):
            raise ValueError(f"{layer['id']}: available なのに読む先が無い")
        if not layer["available"] and not layer.get("unavailable_reason"):
            raise ValueError(f"{layer['id']}: 出せない理由が書かれていない")
    for preset in PRESETS:
        unknown = set(preset["layers"]) - set(ids)
        if unknown:
            raise ValueError(f"プリセット {preset['id']} が未知のレイヤーを指す: {sorted(unknown)}")

    doc = {
        "version": "3.2.0",
        "generated_at": utc_now_iso(),
        "generated_by": "etl.build.layers",
        "layers": LAYERS,
        "presets": PRESETS,
        "default_preset": "WORLD_OVERVIEW",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "layers": len(LAYERS),
        "available": sum(1 for x in LAYERS if x["available"]),
        "unavailable": sum(1 for x in LAYERS if not x["available"]),
        "presets": len(PRESETS),
        "bytes": OUT.stat().st_size,
    }


if __name__ == "__main__":
    print(json.dumps(build_layers(), ensure_ascii=False, indent=2))
