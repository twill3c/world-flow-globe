"""World Bank Global International Ports(SRC-004)の読み取り器。

この出典は**自分が名乗る形式を守っていない**。2026-09-07 に実測した三つの逸脱を、
それぞれ「崩れたら例外で止まる述語」として置く(HC-075 / HC-200)。

1. 拡張子は ``.geojson`` だが UTF-8 ではない(cp1252)。
   ``Bahía Blanca`` の ``í`` が 0xED として入っている。
2. JSON の閉じ括弧の**後ろに 1 行** ``System.IO.MemoryStream`` が付いている。
   サーバー側のストリーム名がそのまま焼き込まれたものと思われる。
3. 原典 §18/§135 が主キーに指定する UN/LOCODE が**一意でない**
   (2026-09-07 実測: 856 地物 / 837 種 / 衝突 19 件)。

3 について、**衝突を黙って畳んではならない**。畳んでよいのは、同じ LOCODE を持つ
地物が「運ぶつもりの全フィールド」で一致するときだけであり、一致しなければ例外で
止める。畳んだ件数は呼び出し側がマニフェストに残す。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: JSON の外側に付いている既知の末尾ゴミ。
TRAILING_SENTINEL = "System.IO.MemoryStream"

#: 出典が cp1252 で書かれていること(実測 2026-09-07)。
SOURCE_ENCODING = "cp1252"

#: 畳んでよいかを判定するときに一致を要求するフィールド。
#: ここに挙げたものが「運ぶつもりのフィールド」であり、
#: 下流(ports.geojson)へ持ち出す値と一致していなければならない。
IDENTITY_FIELDS = ("Country", "LOCODE", "Status", "Function", "outflows")

#: 座標の一致を判定する許容度(度)。出典の座標は小数第 4 位までなので、
#: 完全一致を要求してよい。1e-9 は浮動小数の再解析ゆらぎだけを吸収する。
COORD_TOLERANCE_DEG = 1e-9


class PortSourceError(RuntimeError):
    """出典の形式・意味についての仮定が崩れたときに送出する。"""


@dataclass
class PortRecord:
    locode: str
    name: str
    name_wo_diac: str
    country_name: str
    status: str
    function: str
    outflows: float
    lon: float
    lat: float


@dataclass
class PortReadResult:
    records: list[PortRecord]
    #: 読み込んだ生の地物数(畳む前)。
    raw_feature_count: int = 0
    #: 畳んだ結果として消えた地物数。``raw_feature_count - len(records)`` に等しい。
    collapsed_count: int = 0
    #: 衝突していた LOCODE(昇順)。
    collapsed_locodes: list[str] = field(default_factory=list)


def strip_trailing_sentinel(text: str) -> str:
    """JSON の外側に付いた既知の 1 行を取り除く。

    末尾に無ければ**取り除かない**。将来この逸脱が直ったときに、
    黙って別のものを削ってしまわないため。
    """
    stripped = text.rstrip()
    if stripped.endswith(TRAILING_SENTINEL):
        return stripped[: -len(TRAILING_SENTINEL)]
    return text


def _decode(raw: bytes) -> str:
    """出典のバイト列を文字列にする。

    UTF-8 で読めるなら UTF-8 を優先する(出典が直った場合に備える)。
    読めなければ実測した cp1252 で読む。どちらでも読めなければ例外。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode(SOURCE_ENCODING)
    except UnicodeDecodeError as exc:  # pragma: no cover - 出典が壊れたときだけ
        raise PortSourceError(
            f"出典を UTF-8 でも {SOURCE_ENCODING} でも復号できない: {exc}"
        ) from exc


def _feature_to_record(feat: dict) -> PortRecord:
    props = feat.get("properties")
    geom = feat.get("geometry")
    if not isinstance(props, dict) or not isinstance(geom, dict):
        raise PortSourceError(f"Feature の形が想定と違う: {feat!r}")
    if geom.get("type") != "Point":
        raise PortSourceError(f"港は Point でなければならない: {geom.get('type')!r}")
    coords = geom.get("coordinates")
    if not (isinstance(coords, list) and len(coords) == 2):
        raise PortSourceError(f"coordinates の形が想定と違う: {coords!r}")
    lon, lat = float(coords[0]), float(coords[1])
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise PortSourceError(f"座標が範囲外: lon={lon} lat={lat}")

    missing = [k for k in ("Country", "LOCODE", "Name", "Status", "Function") if k not in props]
    if missing:
        raise PortSourceError(f"必須プロパティ欠落: {missing}")

    locode = str(props["LOCODE"]).strip().upper()
    if len(locode) != 5 or not locode.isalnum():
        raise PortSourceError(f"UN/LOCODE の形が想定と違う(5 桁英数): {locode!r}")

    return PortRecord(
        locode=locode,
        name=str(props["Name"]).strip(),
        name_wo_diac=str(props.get("NameWoDiac", props["Name"])).strip(),
        country_name=str(props["Country"]).strip(),
        status=str(props["Status"]).strip(),
        function=str(props["Function"]).strip(),
        outflows=float(props["outflows"]),
        lon=lon,
        lat=lat,
    )


def _identity_key(rec: PortRecord) -> tuple:
    return (rec.country_name, rec.locode, rec.status, rec.function, rec.outflows)


def _collapse(records: list[PortRecord]) -> PortReadResult:
    """LOCODE ごとに畳む。運搬フィールドが一致しなければ例外(HC-200)。

    名称(``Name`` / ``NameWoDiac``)は一致を要求しない。実測した 19 件の衝突は
    すべて二言語の順序違い(``Åbo (Turku)`` / ``Turku (Åbo)``)だったので、
    名称だけは**辞書順で先に来るものを採る**という決定論的な規則で選ぶ。
    """
    by_locode: dict[str, list[PortRecord]] = {}
    for rec in records:
        by_locode.setdefault(rec.locode, []).append(rec)

    out: list[PortRecord] = []
    collapsed: list[str] = []
    for locode in sorted(by_locode):
        group = by_locode[locode]
        if len(group) > 1:
            head = group[0]
            for other in group[1:]:
                if _identity_key(head) != _identity_key(other):
                    raise PortSourceError(
                        f"LOCODE {locode} が衝突しているが運搬フィールドが一致しない: "
                        f"{_identity_key(head)} vs {_identity_key(other)}"
                    )
                if (
                    abs(head.lon - other.lon) > COORD_TOLERANCE_DEG
                    or abs(head.lat - other.lat) > COORD_TOLERANCE_DEG
                ):
                    raise PortSourceError(
                        f"LOCODE {locode} が衝突しているが座標が一致しない: "
                        f"({head.lon},{head.lat}) vs ({other.lon},{other.lat})"
                    )
            collapsed.append(locode)
            chosen = min(group, key=lambda r: (r.name, r.name_wo_diac))
        else:
            chosen = group[0]
        out.append(chosen)

    return PortReadResult(
        records=out,
        raw_feature_count=len(records),
        collapsed_count=len(records) - len(out),
        collapsed_locodes=collapsed,
    )


def read_wb_ports(path: str | Path) -> PortReadResult:
    """SRC-004 を読み、LOCODE で一意な港の一覧を返す。"""
    raw = Path(path).read_bytes()
    text = strip_trailing_sentinel(_decode(raw))
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PortSourceError(
            f"末尾ゴミを外しても JSON として読めない({path}): {exc}"
        ) from exc

    if doc.get("type") != "FeatureCollection":
        raise PortSourceError(f"FeatureCollection ではない: {doc.get('type')!r}")
    feats = doc.get("features")
    if not isinstance(feats, list) or not feats:
        raise PortSourceError("features が空、または配列でない")

    records = [_feature_to_record(f) for f in feats]
    return _collapse(records)
