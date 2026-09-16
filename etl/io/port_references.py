"""港の位置を照合する独立した出典の読み取り器(SRC-008 / SRC-009 / SRC-010)。

SRC-004(World Bank)の座標には、同名の別地点に当たったものが混ざっていた
(2026-09-17 実測: 清水・油津が北海道、塩田が福建、Houston がアラスカ)。
**出典が一つだけだと、その誤りは測れない。** ここでは別に編まれた台帳を読む。

各読み取り器は、書式についての仮定を**崩れたら例外で止まる述語**として持つ(HC-075)。
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd

from etl.config import RAW

WPI_CSV = RAW / "nga" / "UpdatedPub150.csv"
UNLOCODE_CSV = RAW / "unlocode" / "code-list.csv"
ADMIN1_ZIP = RAW / "naturalearth" / "ne_10m_admin_1_states_provinces.zip"

#: 規格上の地名部は A-Z と 2-9 だが、実物には 0 と 1 も入っている(DE9B0 など、2026-09-17 実測)
LOCODE_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}$")
#: UN/LOCODE の座標は「度 2〜3 桁 + 分 2 桁 + 方位」。例: ``3500N 13830E``
UNLOCODE_COORD_RE = re.compile(r"^(\d{2})(\d{2})([NS]) (\d{3})(\d{2})([EW])$")


class ReferenceFormatError(RuntimeError):
    """照合用の出典の書式についての仮定が崩れたときに送出する。"""


@dataclass(frozen=True)
class WpiPort:
    #: ``OID_``(一意)。``World Port Index Number`` は一意でないので使わない
    number: int
    name: str
    locode: str | None
    lon: float
    lat: float


@dataclass(frozen=True)
class UnlocodeEntry:
    locode: str
    name: str
    subdivision: str | None
    function: str
    #: (lon, lat)。座標を持たない行は None
    coords: tuple[float, float] | None

    @property
    def is_port(self) -> bool:
        """機能コードの 1 桁目が ``1`` なら港(UN/LOCODE の定義)。"""
        return self.function[:1] == "1"


def _require_columns(path: Path, header: list[str], needed: tuple[str, ...]) -> None:
    missing = [c for c in needed if c not in header]
    if missing:
        raise ReferenceFormatError(f"{path.name} に列が無い: {missing}")


def read_wpi(path: Path = WPI_CSV) -> tuple[list[WpiPort], dict[int, WpiPort]]:
    """World Port Index を読む。戻り値は (全港, OID → 港)。

    **鍵は ``World Port Index Number`` ではなく ``OID_``。** 索引番号は一意でない
    (2026-09-17 実測: 3,807 行中 2 番号が重複。46135 は Lekki と Escravos Oil Terminal
    という別の港に付いている)。訂正表が港を引用するときも OID で指す。

    UN/LOCODE 列は ``"JP SMZ"`` のように空白入り。空白を除いて 5 文字の
    UN/LOCODE の形になるものだけを採り、それ以外(空欄・``GB ME""`` など)は
    ``locode=None`` として運ぶ。**港そのものは捨てない。**
    """
    with open(path, encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        _require_columns(
            path,
            reader.fieldnames or [],
            ("OID_", "Main Port Name", "UN/LOCODE", "Latitude", "Longitude"),
        )
        ports: list[WpiPort] = []
        by_oid: dict[int, WpiPort] = {}
        for row in reader:
            oid_f = float(row["OID_"])
            if oid_f != int(oid_f):
                raise ReferenceFormatError(f"WPI の OID_ が整数でない: {row['OID_']}")
            lon, lat = float(row["Longitude"]), float(row["Latitude"])
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ReferenceFormatError(f"WPI OID {oid_f}: 座標が範囲外 ({lon}, {lat})")
            raw = row["UN/LOCODE"].replace(" ", "")
            port = WpiPort(
                number=int(oid_f),
                name=row["Main Port Name"],
                locode=raw if LOCODE_RE.match(raw) else None,
                lon=lon,
                lat=lat,
            )
            if port.number in by_oid:
                raise ReferenceFormatError(f"WPI の OID_ が一意でない: {port.number}")
            by_oid[port.number] = port
            ports.append(port)
    return ports, by_oid


def wpi_by_locode(ports: list[WpiPort]) -> dict[str, list[WpiPort]]:
    """同じ UN/LOCODE を複数の港が持つことがある(US GLC)。リストで返す。"""
    out: dict[str, list[WpiPort]] = defaultdict(list)
    for p in ports:
        if p.locode:
            out[p.locode].append(p)
    return out


#: 座標欄の壊れ方の実測(2026-09-17、座標を持つ 93,152 行を走査)。
#: 分が 60 以上の行が 275、方位の欠けた行が 1(``2444N 05045``)。
#: どちらも**どこを指すか決められない**ので座標なしとして扱い、件数を数えて返す。
#: これを超えて増えたら、読み方の仮定そのものを疑う。
MAX_BROKEN_COORD_RATIO = 0.01


def parse_unlocode_coords(text: str) -> tuple[float, float] | None:
    """``3500N 13830E`` → (138.5, 35.0)。空欄は None。

    形が崩れている(分が 60 以上・方位の欠け)ときは :class:`ReferenceFormatError`。
    呼び出し側が数えて、割合が閾値を超えたら止める。
    """
    text = text.strip()
    if not text:
        return None
    m = UNLOCODE_COORD_RE.match(text)
    if not m:
        raise ReferenceFormatError(f"UN/LOCODE の座標の形が想定と違う: {text!r}")
    lat_d, lat_m, ns, lon_d, lon_m, ew = m.groups()
    if int(lat_m) >= 60 or int(lon_m) >= 60:
        raise ReferenceFormatError(f"UN/LOCODE の座標の分が 60 以上: {text!r}")
    lat = int(lat_d) + int(lat_m) / 60
    lon = int(lon_d) + int(lon_m) / 60
    return (-lon if ew == "W" else lon, -lat if ns == "S" else lat)


def read_unlocode(path: Path = UNLOCODE_CSV) -> dict[str, UnlocodeEntry]:
    """UN/LOCODE のコード表を読む。

    ``Change`` 列が ``X``(削除予定)の行も読む —— SRC-004 がそのコードを使っていれば、
    どこを指していたかは照合に要る。

    **同じコードが複数行ある。** 二言語の名前違い(``Helsinki`` / ``Helsingfors``)で、
    2026-09-17 の実測では 139 コード。行どうしで座標が食い違うものは 0 件、
    地方区分が食い違うものは 1 件(USLEB)、機能コードが違うものは 3 件だった。
    畳み方は次のとおり:

    - 座標 … 食い違えば例外(実測 0 件なので、起きたら仮定が崩れている)
    - 地方区分 … 食い違えば「区分なし」とする。どちらかを選ばない
    - 機能 … 桁ごとの和(どちらかの行が港と言えば港)
    """
    rows_by_code: dict[str, list[dict]] = defaultdict(list)
    with_coords = 0
    broken = 0
    with open(path, encoding="utf-8", newline="") as fp:
        reader = csv.DictReader(fp)
        _require_columns(
            path,
            reader.fieldnames or [],
            ("Country", "Location", "Name", "Subdivision", "Function", "Coordinates"),
        )
        for row in reader:
            if not row["Location"]:
                # 国そのものを表す見出し行(``,JP,,.JAPAN,...``)
                continue
            code = row["Country"] + row["Location"]
            if not LOCODE_RE.match(code):
                raise ReferenceFormatError(f"UN/LOCODE の形でない: {code!r}")
            coords = None
            if row["Coordinates"].strip():
                with_coords += 1
                try:
                    coords = parse_unlocode_coords(row["Coordinates"])
                except ReferenceFormatError:
                    broken += 1
            rows_by_code[code].append({**row, "_coords": coords})
    if with_coords == 0 or broken / with_coords > MAX_BROKEN_COORD_RATIO:
        raise ReferenceFormatError(
            f"UN/LOCODE の座標欄が読めない行が多すぎる: {broken} / {with_coords}"
        )

    out: dict[str, UnlocodeEntry] = {}
    for code, rows in rows_by_code.items():
        coord_set = {r["_coords"] for r in rows if r["_coords"] is not None}
        if len(coord_set) > 1:
            raise ReferenceFormatError(f"UN/LOCODE {code}: 同じコードの行で座標が食い違う {coord_set}")
        subs = {r["Subdivision"] for r in rows}
        sub = subs.pop() if len(subs) == 1 else ""
        width = max(len(r["Function"]) for r in rows)
        function = "".join(
            next((r["Function"][i] for r in rows if i < len(r["Function"]) and r["Function"][i] != "-"), "-")
            for i in range(width)
        )
        out[code] = UnlocodeEntry(
            locode=code,
            name=rows[0]["Name"],
            subdivision=f"{code[:2]}-{sub}" if sub else None,
            function=function,
            coords=next(iter(coord_set), None),
        )
    return out


def read_admin1(path: Path = ADMIN1_ZIP) -> dict[str, list]:
    """ISO 3166-2 コード → 多角形のリスト。コードを持たない行は捨てる(照合に使えない)。"""
    gdf = gpd.read_file(f"zip://{path}", columns=["iso_3166_2", "geometry"])
    out: dict[str, list] = defaultdict(list)
    for code, geom in zip(gdf["iso_3166_2"], gdf["geometry"]):
        if code and geom is not None and not geom.is_empty:
            out[code].append(geom)
    if len(out) < 3000:
        raise ReferenceFormatError(f"Natural Earth admin-1 の区分が少なすぎる: {len(out)}")
    return out
