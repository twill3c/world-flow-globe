"""港の位置の照合と訂正の検査(SPEC.md F-03 / G-26、loop_008)。

期待値の出所:
- 清水(JPSMZ)が静岡県、油津(JPABU)が宮崎県、塩田(CNYTN)が広東省にあることは
  **UN/LOCODE の地方区分**(SRC-009: JP-22 / JP-45 / CN-GD)が定める。その区分の多角形は
  Natural Earth(SRC-010)。どちらも SRC-004 と訂正表の外にある出典なので循環しない。
- 検査が当たる港の集合は、2026-09-17 に SRC-004 の 837 港全域を走査した**実測**
  (当たった 63 港 + 検査では見つからず名前の突き合わせで見つけた PAMIT)。
  件数は定数で書かず、訂正表との一致という不変量で書く。
- 「訂正前の座標なら検査が当たる」ことは陰性対照(検査が働いていることの確認)。
"""

from __future__ import annotations

import json

import pytest

from etl.io.port_references import ADMIN1_ZIP, UNLOCODE_CSV, WPI_CSV
from etl.transform.ports import OUT as PORTS_OUT

REFS_PRESENT = all(p.exists() for p in (WPI_CSV, UNLOCODE_CSV, ADMIN1_ZIP))
needs_refs = pytest.mark.skipif(not REFS_PRESENT, reason="照合用の出典(SRC-008/009/010)が未取得")

POSITION_CHECKS = {"CORRECTED", "CORROBORATED", "UNCORROBORATED", "DISPUTED"}


@pytest.fixture(scope="module")
def ports():
    if not PORTS_OUT.exists():
        pytest.skip("ports.geojson が未生成")
    return json.loads(PORTS_OUT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_locode(ports):
    return {f["properties"]["unlocode"]: f for f in ports["features"]}


@pytest.fixture(scope="module")
def refs():
    if not REFS_PRESENT:
        pytest.skip("照合用の出典が未取得")
    from etl.transform.port_audit import References

    return References.load()


@pytest.fixture(scope="module")
def source_records():
    from etl.io.wb_ports import read_wb_ports
    from etl.transform.ports import SRC

    if not SRC.exists():
        pytest.skip("SRC-004 が未取得")
    return [(r.locode, r.lon, r.lat) for r in read_wb_ports(SRC).records]


# ---------------------------------------------------------------------------
# T-305a 検査が働いていること(陰性対照・陽性対照)
# ---------------------------------------------------------------------------


@needs_refs
def test_t305a_checks_fire_on_the_uncorrected_positions(refs, source_records):
    """訂正前の SRC-004 の座標に当てると、利用者が指摘した誤りを検査が捉えること。"""
    from etl.transform.port_audit import examine

    src = {code: (lon, lat) for code, lon, lat in source_records}
    expect = {
        # 区分の多角形(JP-22 静岡)と WPI の清水港の両方から遠い
        "JPSMZ": {"subdivision", "wpi"},
        # WPI に油津は無い。区分(JP-45 宮崎)だけが捉える
        "JPABU": {"subdivision"},
        "CNYTN": {"subdivision", "wpi", "unlocode_coords"},
        # 座標も区分も同じアラスカの Houston を指す。距離の検査は当たらず、機能コードだけが捉える
        "USHKA": {"not_a_port"},
    }
    for code, triggers in expect.items():
        lon, lat = src[code]
        ev = examine(code, lon, lat, lon, lat, refs)
        assert ev.triggers == triggers, f"{code}: {sorted(ev.triggers)} {ev.disagrees}"


@needs_refs
def test_t305a_checks_stay_quiet_on_known_good_ports(refs, source_records):
    """陽性対照: 正しい位置の主要港では何も当たらないこと。全部当たる検査は何も測っていない。"""
    from etl.transform.port_audit import examine

    src = {code: (lon, lat) for code, lon, lat in source_records}
    # 対照は照合の結果を見てから選んだ(2026-09-17)。SGSIN は照合できる出典が無く
    # (WPI はシンガポールを SGKEP などに分けて持つ)、CNSHA は SRC-004 に無いので使えない。
    for code in ("NLRTM", "JPYOK", "KRPUS", "DEHAM", "BRSSZ"):
        assert code in src, f"{code} が SRC-004 に無い(対照が成り立たない)"
        lon, lat = src[code]
        ev = examine(code, lon, lat, lon, lat, refs)
        assert not ev.triggers, f"{code}: {sorted(ev.triggers)} {ev.disagrees}"
        assert ev.agrees, f"{code}: どの出典とも照合できていない"


# ---------------------------------------------------------------------------
# T-305b 訂正表と整合しなければ止まること
# ---------------------------------------------------------------------------


@needs_refs
def test_t305b_the_reviewed_table_matches_the_full_scan(refs, source_records):
    """837 港全域で、検査に当たった港と訂正表が過不足なく一致すること。"""
    from etl.transform.port_audit import audit_ports, read_corrections

    audit_ports(source_records, refs, read_corrections())  # 整合しなければ例外


@needs_refs
def test_t305b_unreviewed_trigger_stops_the_build(refs, source_records):
    from etl.transform.port_audit import PortAuditError, audit_ports, read_corrections

    table = read_corrections()
    del table["JPSMZ"]
    with pytest.raises(PortAuditError, match="JPSMZ"):
        audit_ports(source_records, refs, table)


@needs_refs
def test_t305b_stale_found_by_stops_the_build(refs, source_records):
    from dataclasses import replace

    from etl.transform.port_audit import PortAuditError, audit_ports, read_corrections

    table = read_corrections()
    table["CNYTN"] = replace(table["CNYTN"], found_by=frozenset({"wpi"}))
    with pytest.raises(PortAuditError, match="CNYTN"):
        audit_ports(source_records, refs, table)


@needs_refs
def test_t305b_a_move_to_a_wrong_place_stops_the_build(refs, source_records):
    """移し先が照合と食い違えば止まること。訂正表そのものの誤りを捉える。"""
    from dataclasses import replace

    from etl.transform.port_audit import PortAuditError, audit_ports, read_corrections

    table = read_corrections()
    # 清水を別の Wikidata 座標(北海道の清水町のあたり)へ「訂正」したことにする
    table["JPSMZ"] = replace(
        table["JPSMZ"], evidence="SRC-011#Q0", lon=142.88, lat=43.0
    )
    with pytest.raises(PortAuditError, match="JPSMZ"):
        audit_ports(source_records, refs, table)


# ---------------------------------------------------------------------------
# T-305c 出荷物の位置(G-26)
# ---------------------------------------------------------------------------


@needs_refs
def test_t305c_reported_ports_are_in_their_unlocode_subdivision(by_locode, refs):
    """利用者が指摘した港と、航路 26 港に入っていた塩田が、定義上の区分の中にあること。"""
    from etl.transform.port_audit import distance_to_polygons_km

    expect = {
        "JPSMZ": "JP-22",  # 静岡県
        "JPABU": "JP-45",  # 宮崎県
        "CNYTN": "CN-GD",  # 広東省
        "USHKA": "US-TX",  # 候補 USHOU の区分(テキサス州)
    }
    for code, sub in expect.items():
        lon, lat = by_locode[code]["geometry"]["coordinates"]
        km = distance_to_polygons_km(lon, lat, refs.admin1[sub])
        assert km <= 10.0, f"{code} が {sub} から {km:.1f} km"


@needs_refs
def test_t305c_no_shipped_port_is_far_from_its_references_unless_reviewed(ports, refs):
    """出荷した座標そのものを照合し直す。ETL の中の検査とは別に、出荷物に対して回す。"""
    from etl.transform.port_audit import examine

    for f in ports["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        slon, slat = p["source_coordinates"] or (lon, lat)
        code = p["locode_candidate"] or p["unlocode"]
        ev = examine(code, lon, lat, slon, slat, refs)
        far = ev.triggers - {"not_a_port"}
        if far:
            assert p["position_note"], f"{p['port_id']}: {ev.disagrees} なのに判定が無い"
            assert p["position_check"] in ("CORROBORATED", "UNCORROBORATED", "DISPUTED")
            assert p["source_coordinates"] is None, f"{p['port_id']}: 移した先が照合と食い違う {ev.disagrees}"


def test_t305c_every_port_declares_its_position_check(ports):
    counts: dict[str, int] = {}
    for f in ports["features"]:
        p = f["properties"]
        assert p["position_check"] in POSITION_CHECKS, p["port_id"]
        counts[p["position_check"]] = counts.get(p["position_check"], 0) + 1
        corrected = p["position_check"] == "CORRECTED"
        assert (p["source_coordinates"] is not None) == corrected, p["port_id"]
        if corrected:
            assert p["position_note"], f"{p['port_id']}: 訂正したのに理由が無い"
        if p["position_check"] == "CORROBORATED":
            assert p["position_evidence"], f"{p['port_id']}: 裏づけの出典が空"
        if p["position_check"] == "UNCORROBORATED":
            assert not p["position_evidence"], p["port_id"]
    audit = ports["position_audit"]
    assert audit["counts"] == counts, "マニフェストの件数が地物と食い違う"
    assert set(audit["corrected_locodes"]) == {
        f["properties"]["unlocode"] for f in ports["features"] if f["properties"]["position_check"] == "CORRECTED"
    }


def test_t305c_corrected_ports_do_not_count_their_own_source_as_corroboration(ports):
    """移し先に使った出典との一致(0 km)を、別の裏づけとして二重に数えないこと。"""
    for f in ports["features"]:
        p = f["properties"]
        if p["position_check"] != "CORRECTED":
            continue
        cited = [k for k in p["position_evidence"] if "#" in k]
        assert len(cited) == 1, p["port_id"]
        kind = cited[0].split("#")[0]
        if kind == "SRC-008":
            assert "SRC-008" not in p["position_evidence"], p["port_id"]


# ---------------------------------------------------------------------------
# T-305d 疑義・推定の扱い
# ---------------------------------------------------------------------------


def test_t305d_disputed_ports_are_not_routable(ports):
    disputed = [f["properties"] for f in ports["features"] if f["properties"]["position_check"] == "DISPUTED"]
    assert disputed, "疑義のある港が 0 件なら、この検査は何も確かめていない"
    for p in disputed:
        assert p["routable"] is False, p["port_id"]
        assert p["grid_cell"] is None and p["snap_km"] is None
        assert p["position_note"]


def test_t305d_inferred_identity_is_declared(ports):
    """同名の港へ移した(同定が推定の)港だけが ``INFERRED`` を名乗ること。"""
    for f in ports["features"]:
        p = f["properties"]
        inferred = p["position_check"] == "CORRECTED" and p["locode_candidate"] is not None
        assert p["confidence"] == ("INFERRED" if inferred else "OBSERVED"), p["port_id"]


def test_t305d_routes_use_the_corrected_positions(by_locode):
    """航路の港の座標が、訂正後の港の座標と一致すること(古い座標で航路を引いていない)。"""
    from etl.transform.routes import OUT as ROUTES_OUT

    if not ROUTES_OUT.exists():
        pytest.skip("shipping_routes.json が未生成")
    doc = json.loads(ROUTES_OUT.read_text(encoding="utf-8"))
    ports = {p["unlocode"]: p for p in doc["ports"]}
    assert "CNYTN" in ports, "塩田が航路の港から外れた(選定が変わったなら規則を見直す)"
    for code, p in ports.items():
        props = by_locode[code]["properties"]
        assert p["grid_cell"] == props["grid_cell"], code
