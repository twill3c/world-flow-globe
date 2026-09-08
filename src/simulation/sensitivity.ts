/**
 * 感度分析(SPEC.md F-17 / 原典 §64–§67 の代わりに置くもの)。
 *
 * ## なぜ ML の予測器を置かないのか
 *
 * 学習に使える実測が無い。手元にあるのは**自分のシミュレータの出力だけ**で、
 * それで学習した予測器はシミュレータの再現以上のことを言わない(循環)。
 * 原典 §3.2 が禁じる `SIMULATION ≠ PREDICTION` を、AI の側から破ることになる。
 *
 * 代わりに置くのは**入力に対する応答の測定**である。容量減を振って、
 * 指標がどう動くかを実際に計算して見せる。ここに出る数は予測ではなく、
 * すべて `SIMULATED` —— 「この仮定でこの入力なら、この模型はこう答える」である。
 *
 * ## 運河には閉じた式がある(二経路一致のオラクル)
 *
 * 運河型のチョークポイントの容量を p% 減らすと、変わるのは**一本の辺の重みだけ**である。
 * 探索が選ぶ道は次のどちらかにしかならない。
 *
 * 1. その運河を通る道 —— 平常時の最短路。**コスト**は `D_平常 + w·(k−1)`(`k = 1/(1−p/100)`)
 * 2. 運河を通らない道 —— 100% 閉鎖したときの最短路。コストは `D_閉鎖`
 *
 * 運河以外の辺は一切変わらないので、1 の中では平常時の最短路が最良のままである。
 *
 * ## コストと距離は別のもの(ここで一度間違えた)
 *
 * 容量減は**混雑の費用**を表すのであって、船が余計に走るわけではない。
 * したがって画面に出す距離は penalty を掛けない**物理距離**であり、
 * 探索が最小化する**コスト**とは別物である。はじめ
 * `D(p) = min(コスト, コスト)` を距離と突き合わせて照合が落ちた。正しくは:
 *
 *     距離(p) = ( D_平常 + w·(k−1) ≤ D_閉鎖 ) ? D_平常 : D_閉鎖
 *
 * つまり**どちらの枝が勝つか**で距離が決まる。**容量を減らしても、代替路のコストを
 * 超えるまでは距離が 1 km も変わらない。** これが応答の折れ目である。
 *
 * ## 諦める閾値も閉じた式で出る
 *
 * 二つのコストが釣り合う点を解くと、その航路が運河を諦める容量減が求まる:
 *
 *     k* = 1 + (D_閉鎖 − D_平常) / w,   p* = 100·(1 − 1/k*)
 *
 * 海峡型にはこの式が使えない —— ゲート円は**複数のセル**を重くするので、
 * 経路が円の中で形を変えうるからである。海峡型は素直に振って測る。
 */

import type { NavGraph } from "../graph/navGraph";
import type { Chokepoint, RoutePort, RouteRecord } from "../types";
import {
  REROUTE_MIN_KM,
  runScenario,
  type RouteOutcome,
  type SimulationResult,
} from "./closure";

/** 振る容量減の水準(%)。0 は平常時なので探索を走らせない。 */
export const SWEEP_LEVELS = [0, 25, 50, 75, 90, 100] as const;

export interface SensitivityPoint {
  capacityReductionPercent: number;
  affected_routes: number;
  rerouted_routes: number;
  blocked_routes: number;
  additional_km_total: number;
  resilience: number;
  /** 運河型のときだけ: 閉じた式から出した追加距離の合計。 */
  closed_form_additional_km: number | null;
  /** 閉じた式との差(km)。運河型でだけ意味を持つ。 */
  closed_form_gap_km: number | null;
}

export interface SensitivityResult {
  chokepointId: string;
  chokepointType: Chokepoint["type"];
  points: SensitivityPoint[];
  /** 運河型で、全水準が閉じた式と一致したか。海峡型は `null`。 */
  closedFormAgrees: boolean | null;
  elapsed_ms: number;
  confidence: "SIMULATED";
}

/** 容量減 p% に対する重みの係数。 */
export function penaltyFactor(percent: number): number {
  if (percent >= 100) return Infinity;
  return 1 / (1 - percent / 100);
}

/**
 * 運河型の閉じた式。**距離**を返す(コストではない)。
 *
 * 式が当たるのは、平常時にその運河を通っている航路だけである。
 * 通っていない航路は、運河を重くしても最短路も距離も変わらない。
 */
export function canalClosedForm(
  baseline: Map<string, number>,
  closed: Map<string, number | null>,
  canalWeightKm: number,
  percent: number,
  usesCanal: (routeId: string) => boolean,
): Map<string, number | null> {
  const k = penaltyFactor(percent);
  const out = new Map<string, number | null>();
  for (const [routeId, base] of baseline) {
    if (!usesCanal(routeId)) {
      out.set(routeId, base);
      continue;
    }
    const around = closed.get(routeId) ?? null;
    const costVia = k === Infinity ? Infinity : base + canalWeightKm * (k - 1);
    const costAround = around === null ? Infinity : around;
    if (costVia === Infinity && costAround === Infinity) {
      out.set(routeId, null);
    } else if (costVia <= costAround) {
      // 運河を通り続ける。**距離は平常時のまま**(混雑は距離を伸ばさない)
      out.set(routeId, base);
    } else {
      out.set(routeId, around);
    }
  }
  return out;
}

/**
 * その航路が運河を諦める容量減(%)。閉じた式から直に出る。
 *
 * 代替路が無い(100% 閉鎖で到達不能になる)航路は、どれだけ混雑しても
 * 運河を使い続けるので `null` を返す。**100 と書かない。**
 */
export function abandonThresholdPercent(
  baselineKm: number,
  closedKm: number | null,
  canalWeightKm: number,
): number | null {
  if (closedKm === null || !Number.isFinite(closedKm)) return null;
  const kStar = 1 + (closedKm - baselineKm) / canalWeightKm;
  if (!Number.isFinite(kStar) || kStar <= 1) return 0;
  return 100 * (1 - 1 / kStar);
}

function distancesOf(outcomes: RouteOutcome[]): Map<string, number | null> {
  return new Map(outcomes.map((o) => [o.route_id, o.simulated_km]));
}

function additionalKmTotal(
  simulated: Map<string, number | null>,
  baseline: Map<string, number>,
): number {
  let total = 0;
  for (const [routeId, d] of simulated) {
    const b = baseline.get(routeId);
    if (b === undefined || d === null) continue;
    // 探索側と**同じ閾値**で数える。片方だけ緩いと、二経路一致が
    // 丸めの差のぶんだけ食い違う。
    if (d > b + REROUTE_MIN_KM) total += d - b;
  }
  return total;
}

/**
 * 容量減を振って、指標の応答を測る。
 *
 * **予測ではない。** 「この入力なら、この模型はこう答える」を並べたものである。
 */
export async function runSensitivity(
  graph: NavGraph,
  chokepoint: Chokepoint,
  routes: RouteRecord[],
  ports: RoutePort[],
  cols: number,
  canalWeightKm: number | null,
  onProgress?: (done: number, total: number) => void,
): Promise<SensitivityResult> {
  const t0 = performance.now();
  const baseline = new Map(routes.map((r) => [r.route_id, r.distance_km]));
  // 平常時にこのチョークポイントを通っている航路。閉じた式はここにだけ当たる。
  const usesChokepoint = new Set(
    routes.filter((r) => r.chokepoints.includes(chokepoint.id)).map((r) => r.route_id),
  );

  const points: SensitivityPoint[] = [];
  const results = new Map<number, SimulationResult>();
  const levels = [...SWEEP_LEVELS].filter((p) => p > 0);

  let done = 0;
  for (const percent of levels) {
    onProgress?.(done, levels.length);
    const r = await runScenario(
      graph,
      { chokepointId: chokepoint.id, durationDays: 1, capacityReductionPercent: percent },
      chokepoint,
      routes,
      ports,
      cols,
    );
    results.set(percent, r);
    done += 1;
  }
  onProgress?.(done, levels.length);

  const closed = results.get(100);
  const closedDistances = closed ? distancesOf(closed.outcomes) : null;

  let agrees: boolean | null = chokepoint.type === "CANAL" ? true : null;

  for (const percent of SWEEP_LEVELS) {
    if (percent === 0) {
      points.push({
        capacityReductionPercent: 0,
        affected_routes: 0,
        rerouted_routes: 0,
        blocked_routes: 0,
        additional_km_total: 0,
        resilience: 1,
        closed_form_additional_km: 0,
        closed_form_gap_km: 0,
      });
      continue;
    }
    const r = results.get(percent)!;
    let closedForm: number | null = null;
    let gap: number | null = null;
    if (chokepoint.type === "CANAL" && canalWeightKm !== null && closedDistances) {
      const predicted = canalClosedForm(
        baseline,
        closedDistances,
        canalWeightKm,
        percent,
        (id) => usesChokepoint.has(id),
      );
      closedForm = additionalKmTotal(predicted, baseline);
      gap = Math.abs(closedForm - r.metrics.additional_km_total);
      // km 単位で 1 m 以上ずれたら不一致とする。
      if (gap > 1e-3) agrees = false;
    }
    points.push({
      capacityReductionPercent: percent,
      affected_routes: r.metrics.affected_routes,
      rerouted_routes: r.metrics.rerouted_routes,
      blocked_routes: r.metrics.blocked_routes,
      additional_km_total: r.metrics.additional_km_total,
      resilience: r.metrics.resilience,
      closed_form_additional_km: closedForm,
      closed_form_gap_km: gap,
    });
  }

  return {
    chokepointId: chokepoint.id,
    chokepointType: chokepoint.type,
    points,
    closedFormAgrees: agrees,
    elapsed_ms: performance.now() - t0,
    confidence: "SIMULATED",
  };
}
