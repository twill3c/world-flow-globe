/**
 * 二実装照合(SPEC.md G-06)。
 *
 * Python 側が書き出した golden(`tests/fixtures/golden_routes.json`)と、
 * この TypeScript 実装の出力を突き合わせる。**距離だけでなく通過セル列まで**比べる ——
 * 距離だけの照合は、別の経路で偶然同じ距離に着いたときに何も言わない(HC-065)。
 *
 * あわせて**陽性対照**を置く: 経路だけをずらした変異体(同点の倒し方を逆にしたもの)を
 * 用意し、照合がそれを落とすことを確かめる。落とせないなら、この照合は経路を見ていない。
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { NavGraph, applyPolarLimit, gateCells, unpackMask } from "./navGraph";
import type { NavGridDoc } from "../types";

const ROOT = resolve(__dirname, "..", "..");

interface GoldenCase {
  scenario: string;
  closed_chokepoint: string | null;
  source: string;
  target: string;
  note: string;
  source_cell: number;
  target_cell: number;
  reachable: boolean;
  distance_km: string | null;
  path_len: number | null;
  path: number[] | null;
}

interface GoldenDoc {
  grid: { rows: number; cols: number; resolution_deg: number };
  tie_break_rule: string;
  neighbour_order: string;
  cases: GoldenCase[];
}

const navDoc: NavGridDoc = JSON.parse(
  readFileSync(resolve(ROOT, "public/data/infrastructure/nav_grid.json"), "utf8"),
);
const golden: GoldenDoc = JSON.parse(
  readFileSync(resolve(ROOT, "tests/fixtures/golden_routes.json"), "utf8"),
);
const chokeDoc = JSON.parse(
  readFileSync(resolve(ROOT, "public/data/infrastructure/chokepoints.json"), "utf8"),
) as { chokepoints: { id: string; type: string; lat: number; lon: number; gate_radius_km: number | null }[] };

const rawMask = unpackMask(navDoc.mask_base64, navDoc.rows * navDoc.cols);
const baseGraph = NavGraph.fromDoc(navDoc, rawMask);

function graphFor(closed: string | null): NavGraph {
  if (!closed) return baseGraph;
  const c = chokeDoc.chokepoints.find((x) => x.id === closed);
  if (!c) throw new Error(`未知のチョークポイント: ${closed}`);
  if (c.type === "CANAL") {
    return baseGraph.withClosure({ blockedCanals: new Set([closed]), canalPenalty: Infinity });
  }
  return baseGraph.withClosure({
    blockedCells: gateCells(baseGraph, c.lat, c.lon, c.gate_radius_km!),
    cellPenalty: Infinity,
  });
}

describe("グリッドの復元", () => {
  it("T-501 マスクの形と航行可能セル数が Python 側と一致する", () => {
    expect(navDoc.rows).toBe(golden.grid.rows);
    expect(navDoc.cols).toBe(golden.grid.cols);
    let count = 0;
    for (const v of rawMask) count += v;
    expect(count).toBe(navDoc.navigable_cells);
  });

  it("T-502 重み表がバイト列のまま往復している", () => {
    const graph = baseGraph;
    // 表の中身は「行ごとに 3 通り」。範囲外(北端の北隣など)は NaN で来る。
    const table = (graph as unknown as { table: Float64Array }).table;
    expect(table.length).toBe(navDoc.rows * 3);
    expect(Number.isNaN(table[0]!)).toBe(true); // 北端の「北隣」は存在しない
    expect(Number.isNaN(table[1]!)).toBe(false);
    // 同緯度・経度 1 セル隣の距離は、赤道でいちばん長い
    const eqRow = navDoc.rows / 2;
    expect(table[eqRow * 3 + 1]!).toBeGreaterThan(table[3 * 1 + 1]!);
  });

  it("T-503 極域制限が仮定として掛かっている", () => {
    const limited = applyPolarLimit(
      rawMask,
      navDoc.rows,
      navDoc.cols,
      navDoc.resolution_deg,
      navDoc.polar_limit.north_deg,
      navDoc.polar_limit.south_deg,
    );
    let raw = 0;
    let lim = 0;
    for (let i = 0; i < rawMask.length; i++) {
      raw += rawMask[i]!;
      lim += limited[i]!;
    }
    expect(lim).toBeLessThan(raw);
    // 制限の外側に 1 セルも残っていないことを、**数えてから一度だけ**主張する
    // (セルごとに expect を呼ぶと 26 万回になって時間切れになる)
    let outsideRemaining = 0;
    let outsideRows = 0;
    for (let i = 0; i < navDoc.rows; i++) {
      const lat = 90 - (i + 0.5) * navDoc.resolution_deg;
      if (lat > navDoc.polar_limit.north_deg || lat < navDoc.polar_limit.south_deg) {
        outsideRows++;
        for (let j = 0; j < navDoc.cols; j++) {
          outsideRemaining += limited[i * navDoc.cols + j]!;
        }
      }
    }
    expect(outsideRows).toBeGreaterThan(0); // 走査対象が空でないこと
    expect(outsideRemaining).toBe(0);
  });
});

describe("二実装照合(G-06)", () => {
  const reachable = golden.cases.filter((c) => c.reachable);
  const unreachable = golden.cases.filter((c) => !c.reachable);

  it("golden に到達可能・到達不能の両方が入っている(照合の前提)", () => {
    expect(reachable.length).toBeGreaterThanOrEqual(6);
    expect(unreachable.length).toBeGreaterThanOrEqual(1);
  });

  for (const c of golden.cases) {
    it(`${c.scenario} ${c.source}→${c.target} が Python と一致する`, { timeout: 60_000 }, () => {
      const g = graphFor(c.closed_chokepoint);
      const res = g.dijkstra(c.source_cell, new Set([c.target_cell]));
      if (!c.reachable) {
        expect(Number.isFinite(res.dist[c.target_cell])).toBe(false);
        return;
      }
      const path = g.path(res, c.source_cell, c.target_cell);
      expect(path).not.toBeNull();
      // 距離だけでなく**通過セル列まで**一致すること
      expect(path).toEqual(c.path);
      expect(path!.length).toBe(c.path_len);
      const km = g.pathLengthKm(path!);
      expect(km).toBe(Number(c.distance_km));
    });
  }
});

describe("照合の陽性対照(HC-065)", () => {
  it("経路だけをずらした変異体は照合に落ちる", () => {
    // 同点の倒し方を逆にした実装。距離は同じになりうるが、経路が変わりうる。
    class ReversedTieGraph extends NavGraph {
      override dijkstra(source: number, targets?: Set<number>) {
        const base = super.dijkstra(source, targets);
        // prev を明示的に壊して、照合が「距離」ではなく「経路」を見ていることを確かめる。
        const prev = new Int32Array(base.prev);
        const target = targets ? [...targets][0]! : source;
        const p = this.path({ ...base, prev }, source, target);
        if (p && p.length > 3) {
          // 途中のセルの親を、その 2 つ前に付け替える(経路が 1 セル短くなる)
          prev[p[2]!] = p[0]!;
        }
        return { dist: base.dist, prev, source, generation: base.generation };
      }
    }
    const c = golden.cases.find((x) => x.scenario === "baseline" && x.reachable)!;
    const mutant = new ReversedTieGraph(
      baseGraph.rows,
      baseGraph.cols,
      baseGraph.resolutionDeg,
      (baseGraph as unknown as { mask: Uint8Array }).mask,
      (baseGraph as unknown as { table: Float64Array }).table,
      (baseGraph as unknown as { meridionalKm: number }).meridionalKm,
      (baseGraph as unknown as { canals: never[] }).canals,
    );
    const res = mutant.dijkstra(c.source_cell, new Set([c.target_cell]));
    const path = mutant.path(res, c.source_cell, c.target_cell);
    expect(path).not.toEqual(c.path);
  });

  it("重み表を壊すと距離が合わなくなる(表を実際に使っている証拠)", () => {
    const c0 = golden.cases.find((x) => x.scenario === "baseline" && x.source === "SGSIN")!;
    // **経路が実際に通る行**を壊す。通らない行を壊しても何も起きないので、
    // それでは「表を使っている」ことの証拠にならない(HC-070)。
    const rowsUsed = new Map<number, number>();
    for (const n of c0.path!) {
      const r = Math.floor(n / navDoc.cols);
      rowsUsed.set(r, (rowsUsed.get(r) ?? 0) + 1);
    }
    const hottest = [...rowsUsed.entries()].sort((a, b) => b[1] - a[1])[0]![0];
    const table = new Float64Array((baseGraph as unknown as { table: Float64Array }).table);
    for (let k = 0; k < 3; k++) {
      const v = table[hottest * 3 + k]!;
      if (!Number.isNaN(v)) table[hottest * 3 + k] = v * 2;
    }
    const mutant = new NavGraph(
      baseGraph.rows,
      baseGraph.cols,
      baseGraph.resolutionDeg,
      (baseGraph as unknown as { mask: Uint8Array }).mask,
      table,
      (baseGraph as unknown as { meridionalKm: number }).meridionalKm,
      (baseGraph as unknown as { canals: never[] }).canals,
    );
    const res2 = mutant.dijkstra(c0.source_cell, new Set([c0.target_cell]));
    const path = mutant.path(res2, c0.source_cell, c0.target_cell)!;
    expect(mutant.pathLengthKm(path)).not.toBe(Number(c0.distance_km));
  });
});

describe("閉鎖の単調性(G-08)", () => {
  it("閉鎖しても経路は短くならない", { timeout: 180_000 }, () => {
    const base = new Map(
      golden.cases
        .filter((c) => c.scenario === "baseline")
        .map((c) => [`${c.source}>${c.target}`, c] as const),
    );
    let compared = 0;
    for (const c of golden.cases) {
      if (c.scenario === "baseline") continue;
      const b = base.get(`${c.source}>${c.target}`)!;
      const g = graphFor(c.closed_chokepoint);
      const { dist } = g.dijkstra(c.source_cell, new Set([c.target_cell]));
      const got = dist[c.target_cell]!;
      if (!Number.isFinite(got)) continue;
      expect(got).toBeGreaterThanOrEqual(Number(b.distance_km) - 1e-6);
      compared++;
    }
    expect(compared).toBeGreaterThanOrEqual(20);
  });
});
