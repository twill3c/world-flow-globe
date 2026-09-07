"""データ源の台帳と取得(原典 §9・§74 / SPEC.md §5)。

**取得できないものは、取得できないと書く。** 空欄や 0 で埋めない(原典 §3.2 の ``null != 0``)。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

from etl.config import RAW, ROOT
from etl.download.manifest import md5_of, sha256_of

USER_AGENT = "world-flow-globe-etl/3.2.0 (+https://github.com/twill3c/world-flow-globe)"


@dataclass
class Source:
    id: str
    provider: str
    dataset: str
    license: str
    license_url: str
    landing_url: str
    #: 機械取得できる URL。取得経路が無い出典は None。
    download_url: str | None = None
    #: ``data/raw`` 配下の相対パス。
    raw_relpath: str | None = None
    version: str | None = None
    published: str | None = None
    #: 出典が自分で公開している検算値(あれば)。
    expected_md5: str | None = None
    #: 取得しない/できない理由。取得できる出典では None。
    unavailable_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def raw_path(self) -> Path | None:
        return RAW / self.raw_relpath if self.raw_relpath else None


SOURCES: tuple[Source, ...] = (
    Source(
        id="SRC-001",
        provider="Natural Earth",
        dataset="Admin 0 Countries (10m)",
        license="Public Domain",
        license_url="https://www.naturalearthdata.com/about/terms-of-use/",
        landing_url="https://www.naturalearthdata.com/downloads/10m-cultural-vectors/10m-admin-0-countries/",
        # 原典 §10 の直リンク(naturalearthdata.com 経由)は 2026-09-07 に HTTP 500。
        # CDN 直を使う。版は zip 内の VERSION.txt で確かめる。
        download_url="https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip",
        raw_relpath="naturalearth/ne_10m_admin_0_countries.zip",
        version="5.1.1",
        notes=[
            "原典 §10 の直リンクは 2026-09-07 時点で HTTP 500。CDN 直から取得している",
            "版は zip 内 ne_10m_admin_0_countries.VERSION.txt で検算する",
        ],
    ),
    Source(
        id="SRC-002",
        provider="Marine Regions (VLIZ)",
        dataset="World EEZ v12 (Low Resolution)",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        landing_url="https://zenodo.org/records/16355917",
        download_url="https://zenodo.org/records/16355917/files/World_EEZ_v12_20231025_LR.zip?download=1",
        raw_relpath="marineregions/World_EEZ_v12_20231025_LR.zip",
        version="v12",
        published="2023-10-25",
        expected_md5="1f3903992291fa90aa35bee579b456b5",
        notes=["Zenodo が公開する MD5 と照合できる(出典自身の公開値なので循環しない)"],
    ),
    Source(
        id="SRC-003",
        provider="Marine Regions (VLIZ)",
        dataset="World 12 Nautical Miles Zone v4",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        landing_url="https://www.marineregions.org/downloads.php",
        download_url=None,
        version="v4",
        published="2023-10-25",
        unavailable_reason=(
            "配布が氏名・所属・メールアドレスの入力と同意チェックを要する Web フォームで、"
            "機械取得できない(2026-09-07 実測)。利用者本人の個人情報を代わりに入力することはしない"
        ),
        notes=[
            "海岸線からの 12NM 緩衝で代替すると confidence が OBSERVED でなく INFERRED になるため代替しない",
        ],
    ),
    Source(
        id="SRC-004",
        provider="World Bank",
        dataset="Global International Ports",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        landing_url="https://datacatalog.worldbank.org/search/dataset/0038118/global-international-ports",
        download_url="https://datacatalogfiles.worldbank.org/ddh-published/0038118/1/DR0046414/attributed_ports.geojson",
        raw_relpath="worldbank/attributed_ports.geojson",
        notes=[
            "拡張子は .geojson だが cp1252(2026-09-07 実測)",
            "JSON の外側に 1 行 System.IO.MemoryStream が付く(2026-09-07 実測)",
            "UN/LOCODE は一意でない: 856 地物 / 837 種 / 衝突 19 件(2026-09-07 実測、HC-200)",
        ],
    ),
    Source(
        id="SRC-005",
        provider="World Bank",
        dataset="Global Shipping Traffic Density",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        landing_url="https://datacatalog.worldbank.org/search/dataset/0037580/global-shipping-traffic-density",
        download_url=None,
        unavailable_reason=(
            "458 MB のラスタで、取得も集約も本バージョンの予算に収まらない(SPEC.md §9 D-02)"
        ),
        notes=["2015-01〜2021-02 の AIS 集計であり、現在の Live AIS ではない(原典 §21)"],
    ),
    Source(
        id="SRC-006",
        provider="UN Trade and Development (UNCTAD)",
        dataset="UNCTADstat Data Hub",
        license="CC BY 3.0 IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        landing_url="https://unctadstat.unctad.org/datacentre/",
        download_url=None,
        unavailable_reason="取得経路が未確立(2026-09-07 時点で HEAD が 403、SPEC.md §9 D-03)",
    ),
    Source(
        id="SRC-007",
        provider="United Nations",
        dataset="UN Comtrade (public preview)",
        license="UN Comtrade の利用条件に従う",
        license_url="https://uncomtrade.org/docs/policy-on-use-and-dissemination-of-un-comtrade-data/",
        landing_url="https://uncomtrade.org/docs/un-comtrade-api/",
        download_url=None,
        unavailable_reason="本バージョンでは貿易フローを出荷しない(後続ループ)",
        notes=["public preview は鍵不要で応答することを 2026-09-07 に確認済み"],
    ),
)

BY_ID = {s.id: s for s in SOURCES}


def fetch(source: Source, force: bool = False, timeout: int = 600) -> Path:
    """出典を ``data/raw`` へ取得する。既にあれば取り直さない。"""
    if source.download_url is None or source.raw_path is None:
        raise RuntimeError(
            f"{source.id} は機械取得できない: {source.unavailable_reason}"
        )
    dest = source.raw_path
    if dest.exists() and not force:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(
        source.download_url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fp:
            for chunk in resp.iter_content(1 << 20):
                fp.write(chunk)
    if source.expected_md5:
        got = md5_of(tmp)
        if got != source.expected_md5:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"{source.id}: MD5 不一致 expected={source.expected_md5} got={got}"
            )
    tmp.replace(dest)
    return dest


def write_source_registry(dest: Path | None = None) -> Path:
    """原典 §74 の ``data/source_registry.json`` を台帳から生成する。

    画面(§77 の出典一覧)もこのファイルを読む。**手で二重に書かない。**
    """
    dest = dest or (ROOT / "public" / "data" / "source_registry.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "version": "3.2.0",
        "generated_by": "etl.download.sources",
        "sources": [
            {k: v for k, v in asdict(s).items() if k != "raw_relpath"} for s in SOURCES
        ],
    }
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def local_status() -> list[dict]:
    """手元の raw の実測状態を返す(取得済みか・何バイトか・SHA-256)。"""
    rows = []
    for s in SOURCES:
        p = s.raw_path
        if p and p.exists():
            rows.append(
                {
                    "id": s.id,
                    "present": True,
                    "bytes": p.stat().st_size,
                    "sha256": sha256_of(p),
                    "path": str(p.relative_to(ROOT)).replace("\\", "/"),
                }
            )
        else:
            rows.append(
                {
                    "id": s.id,
                    "present": False,
                    "reason": s.unavailable_reason or "未取得",
                }
            )
    return rows
