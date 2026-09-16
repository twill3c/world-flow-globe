"""港の位置の照合と訂正(SPEC.md F-03 / G-26)。

## なぜ要るか

SRC-004(World Bank)の座標を、loop_001 から loop_007 まで**一度も外部と照合せずに**
``OBSERVED`` と名乗らせていた。2026-09-17 に利用者の指摘で照合したところ、
同名の別地点に当たっている港が見つかった —— 清水(静岡)と油津(宮崎)が北海道、
塩田(深圳、航路 26 港の一つ)が福建、Houston がアラスカ、Norfolk がコネチカット。

## 何と照合するか(四つの検査)

| 検査 | 出典 | 当たる条件 |
|---|---|---|
| ``wpi`` | SRC-008 World Port Index | 同じ UN/LOCODE の港がすべて 50 km より遠い |
| ``subdivision`` | SRC-009 の地方区分 × SRC-010 の多角形 | 区分の多角形から 50 km より遠い |
| ``unlocode_coords`` | SRC-009 の座標 | 50 km より遠い(**SRC-004 と同じ座標なら数えない**) |
| ``not_a_port`` | SRC-009 の機能コード | コード表に無い、または 1 桁目が ``1``(港)でない |

``unlocode_coords`` で「同じ座標なら数えない」のは、SRC-004 の座標が UN/LOCODE の
座標の写しであることが多いから(504 港中 452 港が一致、2026-09-17 実測)。
写しとの一致は独立の証拠にならない。

``not_a_port`` は距離の検査ではない。Houston(USHKA)は UN/LOCODE でも
アラスカの Houston を指しており、座標と区分は**互いに整合している** ——
距離の三検査はどれも当たらない。誤りは「港でない地点のコードを当てた」ことにあり、
それを捉えられるのは機能コードだけである。

## 当たった港は、人が見た訂正表に載っていなければ止める

検査に当たった港は ``data/corrections/port_positions.csv`` に**一件ずつ判定を書く**。
表に無い港が当たれば例外で止める。表の ``found_by`` が実際に当たった検査と
食い違っても止める(出典が更新されて表が古くなったことを知らせる)。

判定は五種類:

- ``move`` … UN/LOCODE は港を指しているが、座標が別の場所。証拠の座標へ移す
- ``move_relink`` … UN/LOCODE も座標も同名の**港でない**地点を指している。
  国内で同名の港が一つに決まるときだけ、その港へ移す。**同定は推定**なので
  ``confidence`` を ``INFERRED`` にする
- ``relink`` … 座標は実在の港にあるが、UN/LOCODE が同名の別地点を指している。
  座標は動かさず、正しいと考えるコードを ``locode_candidate`` に残す
- ``keep`` … 照合先の側が誤っている(区分コードの体系が違う・符号の誤りなど)
- ``disputed`` … 判定できない。位置に疑義ありとして経路に使わない
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import Point
from shapely.ops import nearest_points

from etl.config import ROOT
from etl.io.port_references import (
    LOCODE_RE,
    UnlocodeEntry,
    WpiPort,
    read_admin1,
    read_unlocode,
    read_wpi,
    wpi_by_locode,
)

CORRECTIONS_CSV = ROOT / "data" / "corrections" / "port_positions.csv"

#: 「別の場所」とみなす距離。0.5 度セル一つ分(約 55 km)に揃えた。
#: SRC-004 の座標は港でなく**市の中心**を指すことが多く(ロサンゼルス 34 km、
#: ブエノスアイレス 28 km、2026-09-17 実測)、これより細かい閾値では
#: 「港の位置の粗さ」と「別の地点」が区別できない。
THRESHOLD_KM = 50.0

#: SRC-004 の座標が UN/LOCODE の座標の写しかどうか。UN/LOCODE は分単位
#: (約 1.85 km)、SRC-004 は小数第 4 位なので、丸めの差だけを吸収する。
SAME_AS_SOURCE_KM = 2.0

TRIGGERS = ("wpi", "subdivision", "unlocode_coords", "not_a_port")
ACTIONS = ("move", "move_relink", "relink", "keep", "disputed")
EARTH_R_KM = 6371.0088


class PortAuditError(RuntimeError):
    """照合の結果が訂正表と整合しないときに送出する。"""


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def distance_to_polygons_km(lon: float, lat: float, geoms: list) -> float:
    pt = Point(lon, lat)
    best = math.inf
    for g in geoms:
        if g.covers(pt):
            return 0.0
        q = nearest_points(g, pt)[0]
        best = min(best, haversine_km(lon, lat, q.x, q.y))
    return best


@dataclass
class References:
    wpi_ports: list[WpiPort]
    wpi_by_oid: dict[int, WpiPort]
    wpi_by_locode: dict[str, list[WpiPort]]
    unlocode: dict[str, UnlocodeEntry]
    admin1: dict[str, list]

    @classmethod
    def load(cls) -> "References":
        ports, by_oid = read_wpi()
        return cls(ports, by_oid, wpi_by_locode(ports), read_unlocode(), read_admin1())


@dataclass
class Evidence:
    #: 50 km 以内で一致した出典(``SRC-008`` など)と距離
    agrees: dict[str, float] = field(default_factory=dict)
    #: 50 km より遠かった検査と距離
    disagrees: dict[str, float] = field(default_factory=dict)
    not_a_port: bool = False

    @property
    def triggers(self) -> frozenset[str]:
        t = set(self.disagrees)
        if self.not_a_port:
            t.add("not_a_port")
        return frozenset(t)


def examine(
    locode: str,
    lon: float,
    lat: float,
    source_lon: float,
    source_lat: float,
    refs: References,
) -> Evidence:
    """一つの位置を四つの検査に当てる。

    ``source_lon/lat`` は SRC-004 の元の座標。UN/LOCODE の座標が
    その写しかどうかを判定するためだけに使う。
    """
    ev = Evidence()

    wpis = refs.wpi_by_locode.get(locode, [])
    if wpis:
        km = min(haversine_km(lon, lat, w.lon, w.lat) for w in wpis)
        (ev.agrees if km <= THRESHOLD_KM else ev.disagrees)[
            "SRC-008" if km <= THRESHOLD_KM else "wpi"
        ] = round(km, 1)

    entry = refs.unlocode.get(locode)
    if entry is None or not entry.is_port:
        ev.not_a_port = True
    if entry is not None:
        if entry.subdivision and entry.subdivision in refs.admin1:
            km = distance_to_polygons_km(lon, lat, refs.admin1[entry.subdivision])
            if km <= THRESHOLD_KM:
                ev.agrees["SRC-009+SRC-010:subdivision"] = round(km, 1)
            else:
                ev.disagrees["subdivision"] = round(km, 1)
        if entry.coords is not None:
            clon, clat = entry.coords
            copied = haversine_km(source_lon, source_lat, clon, clat) <= SAME_AS_SOURCE_KM
            if not copied:
                km = haversine_km(lon, lat, clon, clat)
                if km <= THRESHOLD_KM:
                    ev.agrees["SRC-009:coordinates"] = round(km, 1)
                else:
                    ev.disagrees["unlocode_coords"] = round(km, 1)
    return ev


# ---------------------------------------------------------------------------
# 訂正表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Correction:
    locode: str
    action: str
    evidence: str
    lon: float | None
    lat: float | None
    locode_candidate: str | None
    found_by: frozenset[str]
    reason: str


def read_corrections(path: Path = CORRECTIONS_CSV) -> dict[str, Correction]:
    out: dict[str, Correction] = {}
    with open(path, encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        needed = ("locode", "action", "evidence", "lon", "lat", "locode_candidate", "found_by", "reason")
        if tuple(reader.fieldnames or ()) != needed:
            raise PortAuditError(f"訂正表の列が想定と違う: {reader.fieldnames}")
        for row in reader:
            code = row["locode"]
            if code in out:
                raise PortAuditError(f"訂正表に {code} が二行ある")
            if row["action"] not in ACTIONS:
                raise PortAuditError(f"{code}: 未知の判定 {row['action']!r}")
            if not row["reason"].strip():
                raise PortAuditError(f"{code}: 理由が空")
            found = frozenset(x for x in row["found_by"].split(";") if x)
            if not found or not found <= set(TRIGGERS) | {"manual"}:
                raise PortAuditError(f"{code}: found_by が不正 {row['found_by']!r}")
            cand = row["locode_candidate"] or None
            if (row["action"] in ("move_relink", "relink")) != (cand is not None):
                raise PortAuditError(f"{code}: locode_candidate は relink 系の判定でだけ書く")
            if cand is not None and not LOCODE_RE.match(cand):
                raise PortAuditError(f"{code}: locode_candidate の形が不正 {cand!r}")
            out[code] = Correction(
                locode=code,
                action=row["action"],
                evidence=row["evidence"],
                lon=float(row["lon"]) if row["lon"] else None,
                lat=float(row["lat"]) if row["lat"] else None,
                locode_candidate=cand,
                found_by=found,
                reason=row["reason"],
            )
    return out


def resolve_evidence_coords(c: Correction, refs: References) -> tuple[float, float]:
    """``move`` 系の移し先の座標を、**出典の実物から**引く。

    SRC-008 と SRC-009 の座標は表に書き写さない(写し間違いを作らない)。
    表に座標を書くのは、手元に実物を持たない SRC-011(Wikidata の個別項目)だけ。
    """
    kind, _, ref = c.evidence.partition("#")
    if kind == "SRC-008":
        if c.lon is not None or c.lat is not None:
            raise PortAuditError(f"{c.locode}: SRC-008 の座標は表に書かない(実物から引く)")
        w = refs.wpi_by_oid.get(int(ref))
        if w is None:
            raise PortAuditError(f"{c.locode}: WPI の OID {ref} が無い")
        return w.lon, w.lat
    if kind == "SRC-009":
        if c.lon is not None or c.lat is not None:
            raise PortAuditError(f"{c.locode}: SRC-009 の座標は表に書かない(実物から引く)")
        e = refs.unlocode.get(ref)
        if e is None or e.coords is None:
            raise PortAuditError(f"{c.locode}: UN/LOCODE {ref} に座標が無い")
        return e.coords
    if kind == "SRC-011":
        if c.lon is None or c.lat is None or not ref.startswith("Q"):
            raise PortAuditError(f"{c.locode}: SRC-011 は QID と座標の両方が要る")
        return c.lon, c.lat
    raise PortAuditError(f"{c.locode}: 移し先の証拠の形が不正 {c.evidence!r}")


@dataclass
class AuditedPosition:
    lon: float
    lat: float
    #: CORRECTED / CORROBORATED / UNCORROBORATED / DISPUTED
    position_check: str
    #: 一致した出典と距離(km)。訂正した港では移し先の出典も入る
    position_evidence: dict[str, float]
    source_coordinates: tuple[float, float] | None
    locode_candidate: str | None
    position_note: str | None
    identity_inferred: bool
    action: str | None


def audit_ports(
    records: list[tuple[str, float, float]],
    refs: References,
    corrections: dict[str, Correction],
) -> dict[str, AuditedPosition]:
    """全港を照合し、訂正表を当てた結果を返す。整合しなければ例外。

    ``records`` は (UN/LOCODE, 経度, 緯度) —— SRC-004 の元の座標。
    """
    problems: list[str] = []
    out: dict[str, AuditedPosition] = {}
    seen: set[str] = set()

    for locode, slon, slat in records:
        seen.add(locode)
        ev = examine(locode, slon, slat, slon, slat, refs)
        c = corrections.get(locode)

        if c is None:
            if ev.triggers:
                problems.append(f"{locode}: 検査 {sorted(ev.triggers)} に当たったのに訂正表に無い {ev.disagrees}")
                continue
        else:
            expected = frozenset() if c.found_by == {"manual"} else c.found_by
            if ev.triggers != expected:
                problems.append(
                    f"{locode}: 訂正表の found_by={sorted(c.found_by)} と実際の検査 {sorted(ev.triggers)} が食い違う"
                )
                continue

        if c is None or c.action in ("keep", "relink", "disputed"):
            evidence = ev
            if c is not None and c.action == "relink":
                # 候補のコードで照合し直す。**候補が港であり、座標と整合すること**を確かめる
                assert c.locode_candidate is not None
                evidence = examine(c.locode_candidate, slon, slat, slon, slat, refs)
                if evidence.triggers:
                    problems.append(
                        f"{locode}: 候補 {c.locode_candidate} で照合し直しても当たる {sorted(evidence.triggers)}"
                    )
                    continue
            if c is not None and c.action == "disputed":
                check = "DISPUTED"
            else:
                check = "CORROBORATED" if evidence.agrees else "UNCORROBORATED"
            out[locode] = AuditedPosition(
                lon=slon,
                lat=slat,
                position_check=check,
                position_evidence=dict(evidence.agrees),
                source_coordinates=None,
                locode_candidate=c.locode_candidate if c else None,
                position_note=c.reason if c else None,
                identity_inferred=False,
                action=c.action if c else None,
            )
            continue

        # move / move_relink
        nlon, nlat = resolve_evidence_coords(c, refs)
        check_code = c.locode_candidate if c.action == "move_relink" else locode
        assert check_code is not None
        after = examine(check_code, nlon, nlat, slon, slat, refs)
        # 移し先は、照合できる出典すべてと整合していなければならない
        dist_triggers = after.triggers - {"not_a_port"}
        if dist_triggers:
            problems.append(f"{locode}: 移した先でも当たる {after.disagrees}")
            continue
        if c.action == "move_relink" and after.not_a_port:
            problems.append(f"{locode}: 候補 {c.locode_candidate} が UN/LOCODE で港になっていない")
            continue
        evidence = dict(after.agrees)
        # 移し先に使った出典そのものとの一致は、独立の裏づけとして数えない
        kind, _, _ = c.evidence.partition("#")
        evidence.pop({"SRC-008": "SRC-008", "SRC-009": "SRC-009:coordinates"}.get(kind, ""), None)
        evidence[c.evidence] = 0.0
        out[locode] = AuditedPosition(
            lon=nlon,
            lat=nlat,
            position_check="CORRECTED",
            position_evidence=evidence,
            source_coordinates=(slon, slat),
            locode_candidate=c.locode_candidate,
            position_note=c.reason,
            identity_inferred=c.action == "move_relink",
            action=c.action,
        )

    stale = sorted(set(corrections) - seen)
    if stale:
        problems.append(f"訂正表にあるのに SRC-004 に無い: {stale}")
    if problems:
        raise PortAuditError("港の位置の照合が訂正表と整合しない:\n  " + "\n  ".join(problems))
    return out
