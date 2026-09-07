"""チョークポイント台帳(原典 §31・§32 / SPEC.md F-04・F-08)。

必須 5 か所。**座標は表示用の参照点であって、法的境界でも航行用座標でもない**
(原典 §32 の但し書きをそのまま守る)。

## 閉鎖のしかたが 2 種類ある

- ``CANAL`` —— 人が掘った水路。航行グリッドの上では**辺**として存在する。
  閉鎖はその辺を落とす(または重みを増やす)。
- ``STRAIT`` —— 自然の海峡。グリッドの上では普通のセルの並びでしかない。
  閉鎖は**ゲート円**に入るセルを航行不能にする。

ゲート円の半径は測って決めた。半径を小さくしすぎると**閉鎖しても船が横をすり抜ける**
—— しかもその故障は、シミュレーションが正常に完了して「影響なし」と答えるので静かに通る。

**塞げる最小の半径(2026-09-07 実測、0.5 度グリッド・10 km 刻み):**

| チョークポイント | 塞げる最小 | 採用 | 他の 14 海峡への巻き添え |
|---|---|---|---|
| CHOKE_MALACCA | 40 km | 180 km | 0 件 |
| CHOKE_HORMUZ | 50 km | 140 km | 0 件 |
| CHOKE_BAB_EL_MANDEB | 20 km | 140 km | 0 件 |

**採用値は最小より大きい。** チョークポイントの閉鎖は「一本の線が切れる」ことではなく
**接近水域が使えなくなる**ことなので、最小ぎりぎりでは狭すぎる。
広げすぎていないことは、他の 14 海峡が閉じたあとも通れることで押さえる
(``tests/test_chokepoints.py`` の T-203b)。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from etl.config import PUBLIC_INFRA
from etl.download.manifest import utc_now_iso

OUT = PUBLIC_INFRA / "chokepoints.json"


@dataclass(frozen=True)
class Chokepoint:
    id: str
    code: str
    name: str
    name_ja: str
    type: str  # CANAL | STRAIT
    lat: float
    lon: float
    strategic_importance: int
    connected_seas: tuple[str, ...]
    alternative_routes: tuple[str, ...]
    #: STRAIT のときだけ意味を持つ。CANAL は辺を落とすので円は使わない。
    gate_radius_km: float | None = None
    #: CANAL のときだけ意味を持つ。落とす辺の ID。
    canal_id: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


CHOKEPOINTS: tuple[Chokepoint, ...] = (
    Chokepoint(
        id="CHOKE_SUEZ",
        code="SUEZ",
        name="Suez Canal",
        name_ja="スエズ運河",
        type="CANAL",
        lat=30.5,
        lon=32.3,
        strategic_importance=5,
        connected_seas=("Mediterranean Sea", "Red Sea"),
        alternative_routes=("CAPE_OF_GOOD_HOPE",),
        canal_id="CANAL_SUEZ",
    ),
    Chokepoint(
        id="CHOKE_MALACCA",
        code="MALACCA",
        name="Strait of Malacca",
        name_ja="マラッカ海峡",
        type="STRAIT",
        lat=2.5,
        lon=101.5,
        strategic_importance=5,
        connected_seas=("Indian Ocean", "South China Sea"),
        alternative_routes=("SUNDA", "LOMBOK"),
        gate_radius_km=180.0,
        notes=("閉じるとスンダ海峡・ロンボク海峡へ回る。原典 §32 は代替路を空欄にしているが、実際には存在する",),
    ),
    Chokepoint(
        id="CHOKE_PANAMA",
        code="PANAMA",
        name="Panama Canal",
        name_ja="パナマ運河",
        type="CANAL",
        lat=9.1,
        lon=-79.7,
        strategic_importance=5,
        connected_seas=("Pacific Ocean", "Atlantic Ocean"),
        alternative_routes=("MAGELLAN_DRAKE",),
        canal_id="CANAL_PANAMA",
    ),
    Chokepoint(
        id="CHOKE_HORMUZ",
        code="HORMUZ",
        name="Strait of Hormuz",
        name_ja="ホルムズ海峡",
        type="STRAIT",
        lat=26.6,
        lon=56.5,
        strategic_importance=5,
        connected_seas=("Persian Gulf", "Gulf of Oman"),
        alternative_routes=(),
        gate_radius_km=140.0,
        notes=("代替の海路は無い。ペルシア湾の出入口はここだけである",),
    ),
    Chokepoint(
        id="CHOKE_BAB_EL_MANDEB",
        code="BAB_EL_MANDEB",
        name="Bab el-Mandeb",
        name_ja="バブ・エル・マンデブ海峡",
        type="STRAIT",
        lat=12.6,
        lon=43.3,
        strategic_importance=5,
        connected_seas=("Red Sea", "Gulf of Aden"),
        alternative_routes=("CAPE_OF_GOOD_HOPE",),
        gate_radius_km=140.0,
        notes=("ここを閉じるとスエズ運河も使えなくなる —— 紅海が袋になるため",),
    ),
)

BY_ID = {c.id: c for c in CHOKEPOINTS}

COORDINATE_CAVEAT = (
    "座標は表示用の参照点である。法的境界でも航行用座標でもない(原典 §32)"
)


def build_chokepoints() -> dict:
    for c in CHOKEPOINTS:
        if c.type == "STRAIT" and not c.gate_radius_km:
            raise ValueError(f"{c.id}: STRAIT にはゲート半径が要る")
        if c.type == "CANAL" and not c.canal_id:
            raise ValueError(f"{c.id}: CANAL には落とす辺の ID が要る")
        if c.type not in ("CANAL", "STRAIT"):
            raise ValueError(f"{c.id}: 未知の種別 {c.type}")

    doc = {
        "version": "3.2.0",
        "layer_id": "CHOKEPOINT",
        "confidence": "OBSERVED",
        "generated_at": utc_now_iso(),
        "coordinate_caveat": COORDINATE_CAVEAT,
        "chokepoints": [
            {
                **asdict(c),
                "location": {"lat": c.lat, "lon": c.lon},
                "confidence": "OBSERVED",
            }
            for c in CHOKEPOINTS
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"chokepoints": len(CHOKEPOINTS), "bytes": OUT.stat().st_size}


if __name__ == "__main__":
    print(build_chokepoints())
