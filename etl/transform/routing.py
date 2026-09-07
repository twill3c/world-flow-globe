"""航行グリッド上の経路探索(SPEC.md §6.1 / F-05 / F-09 / G-06)。

## 二実装が一致しうるように作る(HC-073)

この探索は Python と TypeScript の両方で走る。**先に「一致しうるか」を確かめてから
閾値を置く。** 素朴に書くと、次の二つの理由で必ずずれる。

1. ``sin`` / ``cos`` / ``asin`` の実装が処理系ごとに違う。同じ式でも最下位ビットが揃わない
2. 最短距離が同点のとき、どちらの経路を採るかが走査順で決まる

そこで**揃える規則**を二つ置く。

- **辺の重みは表から引く。** グリッドは緯度経度の等間隔なので、辺の長さは
  「どの行から、どの行へ、経度方向に何セル」だけで決まる。行ごとに 3 通り
  (北隣・同緯度・南隣)しかないので、表は 360×3 個の倍精度で足りる。
  この表を**バイト列のまま配る**ので、両実装は同じ数から足し算を始める。
  以後に使う演算は加算と比較だけなので、結果はビット単位で一致しうる
- **同点は明示の順序で倒す。** 優先度付き待ち行列の鍵を ``(距離, セル番号)`` にし、
  緩和は ``新 < 既存`` のときだけ行う(``<=`` にしない)。セル番号は ``行 × 列数 + 列``

一致させる量は「距離」と「通過セル列」の両方である。距離だけを比べる照合は、
別の経路で偶然同じ距離に着いたときに何も言わない(HC-065)。
"""

from __future__ import annotations

import base64
import heapq
import math

import numpy as np

EARTH_R_KM = 6371.0088

#: 商業海運が年間を通じては使わない高緯度の帯。**これは地理ではなく仮定である。**
#:
#: この模型には海氷が無い。制限を置かずに探索すると、スエズ運河を閉じた瞬間に
#: **船が北極点の上を通る**(2026-09-07 実測: シンガポール→ロッテルダムの
#: 626 セルのうち 408 セルが 70 度以北、最北 89.75 度)。
#: しかもマラッカ閉鎖でもまったく同じ経路になり、**二つの閉鎖が区別できなくなる**。
#:
#: 北を 70 度で切るのは、北極海航路(ロシア沿岸 70〜77 度)が季節限定で、
#: コンテナ船の定期航路になっていないため。南を -60 度で切るのは、
#: ドレーク海峡(-56〜-58 度)を残しつつ氷山帯を外すため。
#: **切る位置は仮定なので、画面から外せるようにする**(F-16)。
POLAR_LIMIT_NORTH_DEG = 70.0
POLAR_LIMIT_SOUTH_DEG = -60.0


def apply_polar_limit(
    mask: np.ndarray,
    res: float,
    north_deg: float = POLAR_LIMIT_NORTH_DEG,
    south_deg: float = POLAR_LIMIT_SOUTH_DEG,
) -> np.ndarray:
    """高緯度の帯を航行不能にした新しいマスクを返す。

    セルの**中心の緯度**で切る。境界の扱いを両実装で揃えるため、
    ``lat > north`` / ``lat < south`` の**厳密な不等号**を使う。
    """
    out = mask.copy()
    rows = mask.shape[0]
    lats = 90.0 - (np.arange(rows) + 0.5) * res
    out[(lats > north_deg) | (lats < south_deg), :] = False
    return out


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def build_weight_table(rows: int, cols: int, res: float) -> tuple[np.ndarray, float]:
    """辺の重みの表を作る。

    戻り値は ``(table, meridional_km)``。

    - ``table[i, k]``: 行 ``i`` のセルから、行 ``i + (k - 1)`` かつ**経度が 1 セル隣**の
      セルへの距離(km)。``k`` は 0=北隣, 1=同緯度, 2=南隣。
    - ``meridional_km``: 経度が同じで行が 1 つ違うセルへの距離。緯度に依らず一定。

    範囲外(北端の北隣など)は ``nan`` を入れる。使う側が触らないことを検算できる。
    """
    lats = 90.0 - (np.arange(rows) + 0.5) * res
    table = np.full((rows, 3), np.nan, dtype=np.float64)
    for i in range(rows):
        for k, di in enumerate((-1, 0, 1)):
            j2 = i + di
            if j2 < 0 or j2 >= rows:
                continue
            table[i, k] = haversine_km(0.0, float(lats[i]), res, float(lats[j2]))
    meridional = haversine_km(0.0, 0.0, 0.0, res)
    return table, meridional


def encode_float64(arr: np.ndarray) -> str:
    """倍精度をそのままのバイト列(リトルエンディアン)で base64 にする。

    十進の文字列にすると桁で落ちうるので、**バイト列で配る**。
    """
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f8").tobytes()).decode("ascii")


def decode_float64(text: str, shape: tuple[int, ...]) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype="<f8").reshape(shape)


class NavGraph:
    """航行グリッドのグラフ。閉鎖の適用もここで行う。"""

    def __init__(
        self,
        mask: np.ndarray,
        table: np.ndarray,
        meridional_km: float,
        canals: list[dict],
    ) -> None:
        self.rows, self.cols = mask.shape
        self.mask = mask
        self.table = table
        self.meridional_km = meridional_km
        #: 運河: (セル a, セル b, 重み km, チョークポイント ID)
        self.canals = canals

    def node(self, i: int, j: int) -> int:
        return i * self.cols + j

    def coords(self, n: int) -> tuple[int, int]:
        return divmod(n, self.cols)

    # -- 閉鎖 ------------------------------------------------------------

    def with_closure(
        self,
        blocked_cells: set[int] | None = None,
        cell_penalty: float = 1.0,
        blocked_canals: set[str] | None = None,
        canal_penalty: float = 1.0,
    ) -> "NavGraph":
        """閉鎖を適用した新しいグラフを返す。元のグラフは変えない。

        ``*_penalty`` は重みに掛ける係数。容量が p% 減るとき ``1/(1-p/100)``。
        100% 減は係数が無限大なので、**辺・セルを落とす**方で表す。
        """
        mask = self.mask
        if blocked_cells and cell_penalty == math.inf:
            mask = mask.copy()
            for n in blocked_cells:
                i, j = self.coords(n)
                mask[i, j] = False
            blocked_cells = None
        g = NavGraph(mask, self.table, self.meridional_km, self.canals)
        g._blocked_cells = blocked_cells or set()
        g._cell_penalty = cell_penalty
        g._blocked_canals = blocked_canals or set()
        g._canal_penalty = canal_penalty
        return g

    _blocked_cells: set[int] = frozenset()  # type: ignore[assignment]
    _cell_penalty: float = 1.0
    _blocked_canals: set[str] = frozenset()  # type: ignore[assignment]
    _canal_penalty: float = 1.0

    # -- 探索 ------------------------------------------------------------

    def neighbours(self, n: int):
        """(隣接ノード, 重み) を、**決定論的な順**で返す。

        順序は di ∈ {-1,0,1} × dj ∈ {-1,0,1} の入れ子。TypeScript 側も同じ順にする。
        """
        i, j = divmod(n, self.cols)
        for di in (-1, 0, 1):
            ni = i + di
            if ni < 0 or ni >= self.rows:
                continue
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                nj = (j + dj) % self.cols
                if not self.mask[ni, nj]:
                    continue
                if dj == 0:
                    w = self.meridional_km
                else:
                    w = self.table[i, di + 1]
                m = self.node(ni, nj)
                if m in self._blocked_cells or n in self._blocked_cells:
                    w *= self._cell_penalty
                yield m, w
        for canal in self.canals:
            for a, b in ((canal["a"], canal["b"]), (canal["b"], canal["a"])):
                if a != n:
                    continue
                if canal["chokepoint_id"] in self._blocked_canals:
                    if self._canal_penalty == math.inf:
                        continue
                    yield b, canal["weight_km"] * self._canal_penalty
                else:
                    yield b, canal["weight_km"]

    def dijkstra(
        self, source: int, targets: set[int] | None = None
    ) -> tuple[dict[int, float], dict[int, int]]:
        """単一始点。``targets`` をすべて確定したら打ち切る。

        待ち行列の鍵は ``(距離, ノード番号)``。同点はノード番号の小さい方が先に出る。
        """
        dist: dict[int, float] = {source: 0.0}
        prev: dict[int, int] = {}
        done: set[int] = set()
        remaining = set(targets) if targets else None
        pq: list[tuple[float, int]] = [(0.0, source)]
        while pq:
            d, u = heapq.heappop(pq)
            if u in done:
                continue
            done.add(u)
            if remaining is not None:
                remaining.discard(u)
                if not remaining:
                    break
            for v, w in self.neighbours(u):
                if v in done:
                    continue
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    def path(self, prev: dict[int, int], source: int, target: int) -> list[int] | None:
        if target != source and target not in prev:
            return None
        out = [target]
        while out[-1] != source:
            out.append(prev[out[-1]])
        out.reverse()
        return out

    def path_length_km(self, path: list[int]) -> float:
        """経路の長さを、**辺をたどり直して**足し合わせる。

        Dijkstra が返した距離をそのまま信じない —— 経路と距離が食い違う故障
        (前任者の更新漏れ・経路復元の誤り)は、この足し直しでしか捕まらない。
        """
        total = 0.0
        for a, b in zip(path, path[1:]):
            w = None
            for v, ww in self.neighbours(a):
                if v == b and (w is None or ww < w):
                    w = ww
            if w is None:
                raise ValueError(f"経路に存在しない辺がある: {a} → {b}")
            total += w
        return total
