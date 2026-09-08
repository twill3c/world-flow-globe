/**
 * 感度分析の検査(SPEC.md F-17 / G-25)。
 *
 * ## オラクル: 運河の閉じた式との二経路一致
 *
 * 運河型の容量減は**一本の辺の重みしか変えない**ので、任意の p% における
 * 各航路の距離は、平常時と 100% 閉鎖時の二つから厳密に決まる:
 *
 *     距離(p) = ( D_平常 + w·(k−1) ≤ D_閉鎖 ) ? D_平常 : D_閉鎖,   k = 1/(1−p/100)
 *
 * さらに、その航路が運河を諦める容量減も閉じた式で出る:
 *
 *     k* = 1 + (D_閉鎖 − D_平常)/w,   p* = 100·(1 − 1/k*)
 *
 * 探索を実際に走らせた結果とこれらが一致することを確かめる。
 * **同じ答えに二つの経路で着く**ので、片方が壊れたときに気づける(HC-065)。
 * 閉じた式は探索を一度も呼ばないので循環していない。
 *
 * ## 許容差の出どころ(HC-073)
 *
 * 出荷している `distance_km` は**メートル単位に丸めてある**(小数 3 桁)。
 * 探索は丸めない値を返すので、両者の差は原理的に最大 0.5 m 出る。
 * したがって航路ごとの許容差は 1 m、324 本の合計では 1 km を上限とする。
 * **達成できない閾値を掲げない。**
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { NavGraph, unpackMask } from "../graph/navGraph";
import { runScenario, type SimulationResult } from "./closure";
import {
  abandonThresholdPercent,
  canalClosedForm,
  penaltyFactor,
  runSensitivity,
  SWEEP_LEVELS,
} from "./sensitivity";
import type { Chokepoint, NavGridDoc, RoutesDoc } from "../types";

const ROOT = resolve(__dirname, "..", "..");

/** 出荷値がメートル丸めなので、航路ごとは 1 m まで許す。 */
const TOL_PER_ROUTE_KM = 1e-3;
/** 324 本の合計。丸め誤差が積み上がる。 */
const TOL_TOTAL_KM = 1;

const navDoc: NavGridDoc = JSON.parse(
  readFileSync(resolve(ROOT, "public/data/infrastructure/nav_grid.json"), "utf8"),
);
const routesDoc: RoutesDoc = JSON.parse(
  readFileSync(resolve(ROOT, "public/data/infrastructure/shipping_routes.json"), "utf8"),
);
const chokeDoc = JSON.parse(
  readFileSync(resolve(ROOT, "public/data/infrastructure/chokepoints.json"), "utf8"),
) as { chokepoints: Chokepoint[] };

const mask = unpackMask(navDoc.mask_base64, navDoc.rows * navDoc.cols);
const graph = NavGraph.fromDoc(navDoc, mask);
const suez = chokeDoc.chokepoints.find((c) => c.id === "CHOKE_SUEZ")!;
const suezWeight = navDoc.canals.find((c) => c.chokepoint_id === "CHOKE_SUEZ")!.weight_km;
const usesSuez = new Set(
  routesDoc.routes.filter((r) => r.chokepoints.includes("CHOKE_SUEZ")).map((r) => r.route_id),
);
const baseline = new Map(routesDoc.routes.map((r) => [r.route_id, r.distance_km]));

/** 100% 閉鎖の結果は何度も使うので一度だけ計算する。 */
let closedRun: SimulationResult | null = null;
async function closedDistances(): Promise<Map<string, number | null>> {
  if (!closedRun) {
    closedRun = await runScenario(
      graph,
      { chokepointId: suez.id, durationDays: 1, capacityReductionPercent: 100 },
      suez,
      routesDoc.routes,
      routesDoc.ports,
      navDoc.cols,
    );
  }
  return new Map(closedRun.outcomes.map((o) => [o.route_id, o.simulated_km]));
}

async function runAt(percent: number): Promise<SimulationResult> {
  return runScenario(
    graph,
    { chokepointId: suez.id, durationDays: 1, capacityReductionPercent: percent },
    suez,
    routesDoc.routes,
    routesDoc.ports,
    navDoc.cols,
  );
}

describe("容量減の係数", () => {
  it("T-701 0% は 1 倍、100% は無限大", () => {
    expect(penaltyFactor(0)).toBe(1);
    expect(penaltyFactor(100)).toBe(Infinity);
    expect(penaltyFactor(50)).toBeCloseTo(2, 12);
    expect(penaltyFactor(90)).toBeCloseTo(10, 12);
  });

  it("T-701b 単調に増える", () => {
    const vals = SWEEP_LEVELS.filter((p) => p < 100).map(penaltyFactor);
    for (let i = 1; i < vals.length; i++) expect(vals[i]!).toBeGreaterThan(vals[i - 1]!);
  });
});

describe("運河の閉じた式(二経路一致・G-25)", () => {
  it("T-702 探索の結果と閉じた式が一致する", { timeout: 900_000 }, async () => {
    const closed = await closedDistances();
    // 対照の前提: 100% 閉鎖で実際に何かが変わっていること
    expect(closedRun!.outcomes.filter((o) => o.status !== "UNAFFECTED").length).toBeGreaterThan(10);

    let compared = 0;
    let worst = 0;
    for (const percent of [25, 50, 75, 90]) {
      const run = await runAt(percent);
      const predicted = canalClosedForm(baseline, closed, suezWeight, percent, (id) =>
        usesSuez.has(id),
      );
      for (const o of run.outcomes) {
        const p = predicted.get(o.route_id);
        if (o.simulated_km === null) {
          expect(p).toBeNull();
          continue;
        }
        expect(p).not.toBeNull();
        worst = Math.max(worst, Math.abs(o.simulated_km - (p as number)));
        compared += 1;
      }
    }
    expect(compared).toBeGreaterThan(1000);
    expect(worst).toBeLessThan(TOL_PER_ROUTE_KM);
  });

  it("T-703 陽性対照: 運河の重みを取り違えると照合が落ちる", { timeout: 900_000 }, async () => {
    const closed = await closedDistances();
    const run = await runAt(75);
    const gapWith = (w: number) => {
      const pred = canalClosedForm(baseline, closed, w, 75, (id) => usesSuez.has(id));
      return Math.max(
        ...run.outcomes
          .filter((o) => o.simulated_km !== null)
          .map((o) => Math.abs(o.simulated_km! - (pred.get(o.route_id) as number))),
      );
    };
    // 正しい重みなら一致する(この対照が成り立つ前提)
    expect(gapWith(suezWeight)).toBeLessThan(TOL_PER_ROUTE_KM);
    // 重みを取り違えると、どの枝が勝つかが変わって落ちる
    expect(gapWith(suezWeight * 6)).toBeGreaterThan(1);
  });
});

describe("運河を諦める閾値", () => {
  it(
    "T-707 閾値 ≤ p の航路の集合が、p% で再ルートになる集合と一致する",
    { timeout: 900_000 },
    async () => {
      const closed = await closedDistances();
      const predict = (limit: number) =>
        new Set(
          [...usesSuez].filter((id) => {
            const p = abandonThresholdPercent(baseline.get(id)!, closed.get(id) ?? null, suezWeight);
            return p !== null && p <= limit;
          }),
        );
      const observe = async (limit: number) =>
        new Set(
          (await runAt(limit)).outcomes
            .filter((o) => o.status === "REROUTED")
            .map((o) => o.route_id),
        );

      const p75 = predict(75);
      const p90 = predict(90);
      const a75 = await observe(75);
      const a90 = await observe(90);

      // **二つの水準で照合する。** 一つだけだと、たまたま両方空でも通る。
      expect([...a75].sort()).toEqual([...p75].sort());
      expect([...a90].sort()).toEqual([...p90].sort());

      // 対照の前提: どちらも空でなく、しかも水準で中身が変わること
      expect(p75.size).toBeGreaterThan(0);
      expect(p90.size).toBeGreaterThan(p75.size);
      // 閾値の定義から、低い水準で乗り換えた航路は高い水準でも乗り換えている
      for (const id of p75) expect(p90.has(id)).toBe(true);
    },
  );

  it("T-708 代替路が無ければ閾値は null(100 ではない)", () => {
    // ホルムズ閉鎖でジェベル・アリが到達不能になる形。**閾値は「無い」**
    expect(abandonThresholdPercent(12083, null, 227.5)).toBeNull();
    // 代替路が運河長 1 本ぶん遠いなら k*=2 → p*=50%
    expect(abandonThresholdPercent(1000, 1000 + 227.5, 227.5)).toBeCloseTo(50, 9);
    // 代替路が同じ長さなら、混雑した瞬間に乗り換える
    expect(abandonThresholdPercent(1000, 1000, 227.5)).toBe(0);
  });

  it("T-709 閾値は代替路の遠さについて単調に増え、100 に達しない", () => {
    const w = 227.5;
    const ps = [100, 300, 1000, 5000].map(
      (extra) => abandonThresholdPercent(1000, 1000 + extra, w)!,
    );
    for (let i = 1; i < ps.length; i++) expect(ps[i]!).toBeGreaterThan(ps[i - 1]!);
    expect(ps[ps.length - 1]!).toBeLessThan(100);
  });
});

describe("感度の走査", () => {
  let swept: Awaited<ReturnType<typeof runSensitivity>> | null = null;
  async function sweep() {
    if (!swept) {
      swept = await runSensitivity(
        graph,
        suez,
        routesDoc.routes,
        routesDoc.ports,
        navDoc.cols,
        suezWeight,
      );
    }
    return swept;
  }

  it("T-704 運河型は全水準で閉じた式と一致する", { timeout: 900_000 }, async () => {
    const r = await sweep();
    expect(r.chokepointType).toBe("CANAL");
    expect(r.points).toHaveLength(SWEEP_LEVELS.length);
    for (const p of r.points) {
      expect(p.closed_form_gap_km).not.toBeNull();
      expect(p.closed_form_gap_km!).toBeLessThan(TOL_TOTAL_KM);
    }
    expect(r.closedFormAgrees).toBe(true);
  });

  it("T-705 追加距離は容量減について単調に増える", { timeout: 900_000 }, async () => {
    const r = await sweep();
    const km = r.points.map((p) => p.additional_km_total);
    for (let i = 1; i < km.length; i++) {
      expect(km[i]!).toBeGreaterThanOrEqual(km[i - 1]! - TOL_TOTAL_KM);
    }
    // **応答が平らでないこと。** 全水準で同じ値なら、この分析は何も見せていない。
    expect(km[km.length - 1]!).toBeGreaterThan(km[1]! + 1000);
  });

  it(
    "T-706 応答に折れ目がある(容量を半分にしても影響は半分にならない)",
    { timeout: 900_000 },
    async () => {
      const r = await sweep();
      const by = new Map(r.points.map((p) => [p.capacityReductionPercent, p]));
      // 2026-09-08 実測: 25% と 50% では距離が 1 km も動かない
      expect(by.get(25)!.additional_km_total).toBeLessThan(TOL_TOTAL_KM);
      expect(by.get(50)!.additional_km_total).toBeLessThan(TOL_TOTAL_KM);
      // 75% で初めて動き、100% で一気に跳ぶ
      expect(by.get(75)!.additional_km_total).toBeGreaterThan(100);
      expect(by.get(100)!.additional_km_total).toBeGreaterThan(
        by.get(75)!.additional_km_total * 10,
      );
    },
  );
});
