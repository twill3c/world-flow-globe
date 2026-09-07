/**
 * 航行グラフとダイクストラ(TypeScript 側)。
 *
 * **Python 側(`etl/transform/routing.py`)と同じ答えを出すために書かれている。**
 * 一致させる規則は次の三つで、どれか一つでも崩すと照合(G-06)が落ちる。
 *
 * 1. **辺の重みは配られた表から引く。** `Math.sin` などを一度も呼ばない。
 *    処理系ごとに三角関数の最下位ビットが違うので、式を書き写すと必ずずれる(HC-073)。
 * 2. **近傍の走査順を揃える。** `di ∈ (-1,0,1)` が外側、`dj ∈ (-1,0,1)` が内側。`(0,0)` は飛ばす。
 * 3. **同点は明示の順序で倒す。** 優先度は `(距離, セル番号)`。緩和は `新 < 既存` のときだけ
 *    (`<=` にしない)。セル番号は `行 × 列数 + 列`。
 *
 * 以後の演算は加算と比較しかないので、両実装はビット単位で一致しうる。
 */

import type { CanalEdge, NavGridDoc } from "../types";

export interface CanalRuntime {
  a: number;
  b: number;
  weightKm: number;
  chokepointId: string;
}

export interface DijkstraResult {
  /**
   * セル番号で引く距離。到達していないセルは `Infinity`。
   *
   * **これは使い回しの作業領域である。** 次に `dijkstra` を呼ぶと上書きされる。
   * 結果は呼び出しの直後に使い切ること。持ち回るなら複製する。
   * 取り違えを黙って通さないよう、`path` は世代を検算する。
   */
  dist: Float64Array;
  /** 直前のセル番号。未到達は -1。同じく使い回し。 */
  prev: Int32Array;
  source: number;
  /** この結果を作った探索の世代。 */
  generation: number;
}

export interface ClosureSpec {
  /** 航行不能にするセル(海峡型の閉鎖)。 */
  blockedCells?: Set<number>;
  /** セルの重みに掛ける係数。`Infinity` は「落とす」。 */
  cellPenalty?: number;
  /** 落とす運河のチョークポイント ID。 */
  blockedCanals?: Set<string>;
  canalPenalty?: number;
}

/** base64 の packbits(MSB 先頭・行優先)を 1 セル 1 バイトへ展開する。 */
export function unpackMask(base64: string, cellCount: number): Uint8Array {
  const bin = atob(base64);
  const out = new Uint8Array(cellCount);
  for (let i = 0; i < cellCount; i++) {
    const byte = bin.charCodeAt(i >> 3);
    out[i] = (byte >> (7 - (i & 7))) & 1;
  }
  return out;
}

/** base64 の Float64(リトルエンディアン)を Float64Array へ戻す。 */
export function decodeFloat64(base64: string): Float64Array {
  const bin = atob(base64);
  const buf = new ArrayBuffer(bin.length);
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float64Array(buf);
}

/**
 * 探索用の作業領域を使い回す。
 *
 * 一回の閉鎖シミュレーションで 20 回前後ダイクストラを走らせる。毎回
 * 259,200 要素の配列を三本作ると、確保と GC だけで秒単位になる
 * (2026-09-08 実測: 使い回す前 2,545 ms → 後述)。
 * この探索は同期で、途中で他の探索に入れ替わることが無いので使い回して安全である。
 */
const scratch: {
  size: number;
  dist: Float64Array;
  prev: Int32Array;
  done: Uint8Array;
} = { size: 0, dist: new Float64Array(0), prev: new Int32Array(0), done: new Uint8Array(0) };

let scratchGeneration = 0;

function takeScratch(n: number) {
  if (scratch.size !== n) {
    scratch.size = n;
    scratch.dist = new Float64Array(n);
    scratch.prev = new Int32Array(n);
    scratch.done = new Uint8Array(n);
  }
  scratch.dist.fill(Infinity);
  scratch.prev.fill(-1);
  scratch.done.fill(0);
  scratchGeneration += 1;
  return scratch;
}

/**
 * 距離を鍵、同点はセル番号で倒す最小ヒープ。Python の `heapq` と同じ順序になる。
 *
 * 中身は型付き配列で、容量が足りなくなったときだけ倍にする。
 * `pop` は**オブジェクトを返さない** —— 一回の探索で数十万回呼ばれるので、
 * その都度 `{d, n}` を作ると確保だけで無視できない時間になる。
 * 取り出した値は `topDist` / `topNode` に置く。
 */
class MinHeap {
  private dist: Float64Array;
  private node: Int32Array;
  private len = 0;
  topDist = 0;
  topNode = 0;

  constructor(capacity = 1024) {
    this.dist = new Float64Array(capacity);
    this.node = new Int32Array(capacity);
  }

  get size(): number {
    return this.len;
  }

  clear(): void {
    this.len = 0;
  }

  private less(i: number, j: number): boolean {
    const di = this.dist[i]!;
    const dj = this.dist[j]!;
    if (di !== dj) return di < dj;
    return this.node[i]! < this.node[j]!;
  }

  private swap(i: number, j: number): void {
    const d = this.dist[i]!;
    this.dist[i] = this.dist[j]!;
    this.dist[j] = d;
    const n = this.node[i]!;
    this.node[i] = this.node[j]!;
    this.node[j] = n;
  }

  push(d: number, n: number): void {
    if (this.len === this.dist.length) {
      const nd = new Float64Array(this.len * 2);
      nd.set(this.dist);
      this.dist = nd;
      const nn = new Int32Array(this.len * 2);
      nn.set(this.node);
      this.node = nn;
    }
    this.dist[this.len] = d;
    this.node[this.len] = n;
    let i = this.len++;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (!this.less(i, parent)) break;
      this.swap(i, parent);
      i = parent;
    }
  }

  pop(): void {
    this.topDist = this.dist[0]!;
    this.topNode = this.node[0]!;
    this.len -= 1;
    if (this.len > 0) {
      this.dist[0] = this.dist[this.len]!;
      this.node[0] = this.node[this.len]!;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1;
        const r = l + 1;
        let m = i;
        if (l < this.len && this.less(l, m)) m = l;
        if (r < this.len && this.less(r, m)) m = r;
        if (m === i) break;
        this.swap(i, m);
        i = m;
      }
    }
  }
}

/** ヒープも使い回す(確保と GC を避ける)。 */
const sharedHeap = new MinHeap(1 << 16);

export class NavGraph {
  readonly rows: number;
  readonly cols: number;
  readonly resolutionDeg: number;
  private readonly mask: Uint8Array;
  private readonly table: Float64Array;
  private readonly meridionalKm: number;
  private readonly canals: CanalRuntime[];
  private readonly closure: Required<ClosureSpec>;
  /** 運河の出口を素早く引くための索引。 */
  private readonly canalsByNode: Map<number, CanalRuntime[]>;

  constructor(
    rows: number,
    cols: number,
    resolutionDeg: number,
    mask: Uint8Array,
    table: Float64Array,
    meridionalKm: number,
    canals: CanalRuntime[],
    closure: ClosureSpec = {},
  ) {
    this.rows = rows;
    this.cols = cols;
    this.resolutionDeg = resolutionDeg;
    this.mask = mask;
    this.table = table;
    this.meridionalKm = meridionalKm;
    this.canals = canals;
    this.closure = {
      blockedCells: closure.blockedCells ?? new Set<number>(),
      cellPenalty: closure.cellPenalty ?? 1,
      blockedCanals: closure.blockedCanals ?? new Set<string>(),
      canalPenalty: closure.canalPenalty ?? 1,
    };
    this.canalsByNode = new Map();
    for (const c of canals) {
      for (const [from, to] of [
        [c.a, c.b],
        [c.b, c.a],
      ] as const) {
        const list = this.canalsByNode.get(from) ?? [];
        list.push({ ...c, a: from, b: to });
        this.canalsByNode.set(from, list);
      }
    }
  }

  static fromDoc(doc: NavGridDoc, mask: Uint8Array, opts: { allowPolar?: boolean } = {}): NavGraph {
    const table = decodeFloat64(doc.weight_table_base64);
    const effective = opts.allowPolar
      ? mask
      : applyPolarLimit(mask, doc.rows, doc.cols, doc.resolution_deg, doc.polar_limit.north_deg, doc.polar_limit.south_deg);
    const canals: CanalRuntime[] = doc.canals.map((c: CanalEdge) => ({
      a: c.cells[0]![0] * doc.cols + c.cells[0]![1],
      b: c.cells[c.cells.length - 1]![0] * doc.cols + c.cells[c.cells.length - 1]![1],
      weightKm: c.weight_km,
      chokepointId: c.chokepoint_id,
    }));
    return new NavGraph(
      doc.rows,
      doc.cols,
      doc.resolution_deg,
      effective,
      table,
      doc.meridional_km,
      canals,
    );
  }

  withClosure(closure: ClosureSpec): NavGraph {
    let mask = this.mask;
    let blocked = closure.blockedCells;
    if (blocked && blocked.size > 0 && closure.cellPenalty === Infinity) {
      mask = new Uint8Array(this.mask);
      for (const n of blocked) mask[n] = 0;
      blocked = undefined;
    }
    return new NavGraph(this.rows, this.cols, this.resolutionDeg, mask, this.table, this.meridionalKm, this.canals, {
      ...closure,
      ...(blocked ? { blockedCells: blocked } : { blockedCells: new Set<number>() }),
    });
  }

  cellCenter(node: number): [number, number] {
    const i = Math.floor(node / this.cols);
    const j = node % this.cols;
    return [-180 + (j + 0.5) * this.resolutionDeg, 90 - (i + 0.5) * this.resolutionDeg];
  }

  isNavigable(node: number): boolean {
    return this.mask[node] === 1;
  }

  /**
   * 単一始点ダイクストラ。`targets` をすべて確定したら打ち切る。
   *
   * 走査順・同点規則は Python 側と同一。**ここを変えたら G-06 が落ちる。**
   */
  dijkstra(source: number, targets?: Set<number>): DijkstraResult {
    const n = this.rows * this.cols;
    // 型付き配列を使い回す。Map より速いが、**走査順も同点規則も変えていない**
    // ので、Python 側との一致(G-06)はそのままである。
    const { dist, prev, done } = takeScratch(n);
    dist[source] = 0;
    const remaining = targets ? new Set(targets) : null;
    const pq = sharedHeap;
    pq.clear();
    pq.push(0, source);

    const { blockedCells, cellPenalty, blockedCanals, canalPenalty } = this.closure;
    const hasBlocked = blockedCells.size > 0;

    while (pq.size > 0) {
      pq.pop();
      const d = pq.topDist;
      const u = pq.topNode;
      if (done[u] === 1) continue;
      done[u] = 1;
      if (remaining) {
        remaining.delete(u);
        if (remaining.size === 0) break;
      }
      const i = (u / this.cols) | 0;
      const j = u - i * this.cols;
      const uBlocked = hasBlocked && blockedCells.has(u);

      for (let di = -1; di <= 1; di++) {
        const ni = i + di;
        if (ni < 0 || ni >= this.rows) continue;
        const rowBase = ni * this.cols;
        const w0 = this.table[i * 3 + (di + 1)]!;
        for (let dj = -1; dj <= 1; dj++) {
          if (di === 0 && dj === 0) continue;
          let nj = j + dj;
          if (nj < 0) nj += this.cols;
          else if (nj >= this.cols) nj -= this.cols;
          const v = rowBase + nj;
          if (this.mask[v] !== 1 || done[v] === 1) continue;
          let w = dj === 0 ? this.meridionalKm : w0;
          if (uBlocked || (hasBlocked && blockedCells.has(v))) w *= cellPenalty;
          const nd = d + w;
          if (nd < dist[v]!) {
            dist[v] = nd;
            prev[v] = u;
            pq.push(nd, v);
          }
        }
      }

      const canals = this.canalsByNode.get(u);
      if (canals) {
        for (const c of canals) {
          let w = c.weightKm;
          if (blockedCanals.has(c.chokepointId)) {
            if (canalPenalty === Infinity) continue;
            w *= canalPenalty;
          }
          const v = c.b;
          if (done[v] === 1) continue;
          const nd = d + w;
          if (nd < dist[v]!) {
            dist[v] = nd;
            prev[v] = u;
            pq.push(nd, v);
          }
        }
      }
    }
    return { dist, prev, source, generation: scratchGeneration };
  }

  path(result: DijkstraResult, source: number, target: number): number[] | null {
    // **使い回しの作業領域を、別の探索の後で読んでいないこと。**
    // 黙って違う経路を返す故障を、ここで例外にする。
    if (result.prev === scratch.prev && result.generation !== scratchGeneration) {
      throw new Error(
        `古い探索結果から経路を復元しようとしている(世代 ${result.generation} / 現在 ${scratchGeneration})`,
      );
    }
    if (target !== source && result.prev[target] === -1) return null;
    const out = [target];
    let guard = 0;
    while (out[out.length - 1] !== source) {
      const p = result.prev[out[out.length - 1]!]!;
      if (p === -1 || ++guard > 1_000_000) return null;
      out.push(p);
    }
    out.reverse();
    return out;
  }

  /**
   * 経路の長さを、**辺をたどり直して**足し合わせる。
   *
   * ダイクストラが返した距離をそのまま信じない。経路と距離が食い違う故障は
   * この足し直しでしか捕まらない(Python 側と同じ規律)。
   */
  pathLengthKm(path: number[]): number {
    let total = 0;
    for (let k = 0; k + 1 < path.length; k++) {
      const a = path[k]!;
      const b = path[k + 1]!;
      const ia = Math.floor(a / this.cols);
      const ja = a % this.cols;
      const ib = Math.floor(b / this.cols);
      const jb = b % this.cols;
      const di = ib - ia;
      const dj = jb - ja;
      const wrapDj = Math.min(Math.abs(dj), this.cols - Math.abs(dj));
      if (Math.abs(di) <= 1 && wrapDj <= 1) {
        total += wrapDj === 0 ? this.meridionalKm : this.table[ia * 3 + (di + 1)]!;
      } else {
        const canal = (this.canalsByNode.get(a) ?? []).find((c) => c.b === b);
        if (!canal) throw new Error(`経路に存在しない辺がある: ${a} → ${b}`);
        total += canal.weightKm;
      }
    }
    return total;
  }
}

/**
 * 極域の航行制限(SPEC.md §6.1.2 / F-16)。**地理ではなく仮定である。**
 *
 * セル中心の緯度で切る。境界の扱いを Python 側と揃えるため、厳密な不等号を使う。
 */
export function applyPolarLimit(
  mask: Uint8Array,
  rows: number,
  cols: number,
  resolutionDeg: number,
  northDeg: number,
  southDeg: number,
): Uint8Array {
  const out = new Uint8Array(mask);
  for (let i = 0; i < rows; i++) {
    const lat = 90 - (i + 0.5) * resolutionDeg;
    if (lat > northDeg || lat < southDeg) {
      out.fill(0, i * cols, (i + 1) * cols);
    }
  }
  return out;
}

const EARTH_R_KM = 6371.0088;

/** 表示・検算用の測地線距離。**探索の重みには使わない**(表から引く)。 */
export function haversineKm(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = p2 - p1;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_R_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** 海峡型チョークポイントのゲート円に入るセル。Python 側と同じ規則。 */
export function gateCells(
  graph: NavGraph,
  lat: number,
  lon: number,
  radiusKm: number,
): Set<number> {
  const out = new Set<number>();
  const dlat = radiusKm / 111.32;
  const dlon = radiusKm / Math.max(1e-6, 111.32 * Math.cos((lat * Math.PI) / 180));
  const res = graph.resolutionDeg;
  const iLo = Math.max(0, Math.floor((90 - (lat + dlat)) / res) - 1);
  const iHi = Math.min(graph.rows - 1, Math.floor((90 - (lat - dlat)) / res) + 1);
  const jLo = Math.floor((lon - dlon + 180) / res) - 1;
  const jHi = Math.floor((lon + dlon + 180) / res) + 1;
  for (let i = iLo; i <= iHi; i++) {
    for (let j = jLo; j <= jHi; j++) {
      const jj = ((j % graph.cols) + graph.cols) % graph.cols;
      const node = i * graph.cols + jj;
      const [clon, clat] = graph.cellCenter(node);
      if (haversineKm(lon, lat, clon, clat) <= radiusKm) out.add(node);
    }
  }
  return out;
}

/** 日付変更線をまたぐところで線を切る。切らないと地球儀を横切る嘘の直線が出る。 */
export function splitAntimeridian(points: [number, number][]): [number, number][][] {
  const segs: [number, number][][] = [[]];
  for (let k = 0; k < points.length; k++) {
    const p = points[k]!;
    const prev = k > 0 ? points[k - 1]! : null;
    if (prev && Math.abs(p[0] - prev[0]) > 180) segs.push([]);
    segs[segs.length - 1]!.push(p);
  }
  return segs.filter((s) => s.length >= 2);
}
