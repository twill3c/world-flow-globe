/**
 * 閉鎖シミュレーション(原典 §51〜§63 / SPEC.md F-08〜F-12)。
 *
 * **ここが出す数はすべて `SIMULATED` である。** 起きたことではなく、
 * 「この仮定のもとで起きたらどうなるか」である。予測(`PREDICTED`)ではない ——
 * 原典 §3.2 が禁じる混同を、名前の上でも守る。
 */

import { NavGraph, gateCells, splitAntimeridian } from "../graph/navGraph";
import type { Chokepoint, RoutePort, RouteRecord } from "../types";

export interface ScenarioSpec {
  chokepointId: string;
  /** 1〜90 日(原典 §52)。 */
  durationDays: number;
  /** 0〜100%。100% は「落とす」。 */
  capacityReductionPercent: number;
}

export interface RouteOutcome {
  route_id: string;
  source_port_id: string;
  target_port_id: string;
  baseline_km: number;
  baseline_days: number;
  /** 到達不能なら `null`。**0 にしない。** */
  simulated_km: number | null;
  simulated_days: number | null;
  additional_km: number | null;
  additional_days: number | null;
  status: "UNAFFECTED" | "REROUTED" | "BLOCKED";
  segments: [number, number][][];
}

export interface PortCongestion {
  port_id: string;
  baseline_load: number;
  additional_load: number;
  capacity: number;
  utilization: number;
  band: "NORMAL" | "ELEVATED" | "HIGH" | "OVER_CAPACITY";
}

export interface SimulationResult {
  scenario: ScenarioSpec;
  outcomes: RouteOutcome[];
  congestion: PortCongestion[];
  metrics: {
    affected_routes: number;
    rerouted_routes: number;
    blocked_routes: number;
    additional_km_total: number;
    additional_days_mean: number | null;
    ports_over_capacity: number;
    resilience: number;
  };
  elapsed_ms: number;
  confidence: "SIMULATED";
}

/** 原典 §62 の 4 区分。 */
export function congestionBand(u: number): PortCongestion["band"] {
  if (u > 1.0) return "OVER_CAPACITY";
  if (u >= 0.8) return "HIGH";
  if (u >= 0.6) return "ELEVATED";
  return "NORMAL";
}

const KNOTS = 16.0;
const KM_PER_NM = 1.852;

export function transitDays(km: number): number {
  return km / (KNOTS * KM_PER_NM * 24.0);
}

/**
 * 次のマクロタスクまで制御を返す。
 *
 * `setTimeout(0)` を使わない —— 入れ子になると 4 ms 以上に丸められ、
 * 20 回まわすだけで無視できない時間になる(2026-09-08 実測: 総時間が
 * 2,373 ms から 4,838 ms へ**倍増した**)。`MessageChannel` は丸められない。
 */
function yieldToUi(): Promise<void> {
  return new Promise((resolve) => {
    const ch = new MessageChannel();
    ch.port1.onmessage = () => {
      ch.port1.close();
      resolve();
    };
    ch.port2.postMessage(null);
  });
}

function penaltyFor(percent: number): number {
  if (percent >= 100) return Infinity;
  return 1 / (1 - percent / 100);
}

/**
 * シナリオを走らせる。
 *
 * 影響を受けうる航路だけを引き直す —— 平常時にそのチョークポイントを
 * 通っていなかった航路は、閉鎖しても最短路が変わらない
 * (閉鎖は辺を消すか重くするだけなので、使っていない辺の変化は効かない)。
 */
export async function runScenario(
  graph: NavGraph,
  scenario: ScenarioSpec,
  chokepoint: Chokepoint,
  routes: RouteRecord[],
  ports: RoutePort[],
  cols: number,
  onProgress?: (done: number, total: number) => void,
): Promise<SimulationResult> {
  const t0 = performance.now();
  const penalty = penaltyFor(scenario.capacityReductionPercent);

  const closed =
    chokepoint.type === "CANAL"
      ? graph.withClosure({
          blockedCanals: new Set([chokepoint.id]),
          canalPenalty: penalty,
        })
      : graph.withClosure({
          blockedCells: gateCells(graph, chokepoint.lat, chokepoint.lon, chokepoint.gate_radius_km!),
          cellPenalty: penalty,
        });

  const nodeOf = new Map(
    ports.map((p) => [p.port_id, p.grid_cell[0] * cols + p.grid_cell[1]] as const),
  );

  const affected = routes.filter((r) => r.chokepoints.includes(chokepoint.id));
  const bySource = new Map<string, RouteRecord[]>();
  for (const r of affected) {
    const list = bySource.get(r.source_port_id) ?? [];
    list.push(r);
    bySource.set(r.source_port_id, list);
  }

  const outcomes: RouteOutcome[] = [];
  for (const r of routes) {
    if (!r.chokepoints.includes(chokepoint.id)) {
      outcomes.push({
        route_id: r.route_id,
        source_port_id: r.source_port_id,
        target_port_id: r.target_port_id,
        baseline_km: r.distance_km,
        baseline_days: r.transit_time_days,
        simulated_km: r.distance_km,
        simulated_days: r.transit_time_days,
        additional_km: 0,
        additional_days: 0,
        status: "UNAFFECTED",
        segments: r.display_segments,
      });
    }
  }

  // **一港ぶん引くたびに制御を返す。** 全部を一息で回すと、その間ページが固まる。
  // 総時間はほとんど変わらないが、地球儀は回せるし進捗も出る
  // (2026-09-08 実測: 26 港の網でスエズ閉鎖に約 2.4 秒。SPEC.md N-04 の
  //  2 秒には届いていない —— 辺の緩和が 2,800 万回あるため)。
  let doneSources = 0;
  let lastReport = 0;
  for (const [sourceId, list] of bySource) {
    await yieldToUi();
    doneSources += 1;
    // 進捗の描き直しは 200 ms に一度で足りる。毎回 setState すると
    // React の再描画のほうが探索より重くなる。
    const now = performance.now();
    if (now - lastReport > 200 || doneSources === bySource.size) {
      lastReport = now;
      onProgress?.(doneSources, bySource.size);
    }
    const src = nodeOf.get(sourceId);
    if (src === undefined) continue;
    const targets = new Set(list.map((r) => nodeOf.get(r.target_port_id)!).filter((x) => x !== undefined));
    const res = closed.dijkstra(src, targets);
    for (const r of list) {
      const dst = nodeOf.get(r.target_port_id)!;
      const d = res.dist[dst]!;
      if (!Number.isFinite(d)) {
        outcomes.push({
          route_id: r.route_id,
          source_port_id: r.source_port_id,
          target_port_id: r.target_port_id,
          baseline_km: r.distance_km,
          baseline_days: r.transit_time_days,
          // 到達できないので値が無い。**0 にしない**(原典 §3.2)
          simulated_km: null,
          simulated_days: null,
          additional_km: null,
          additional_days: null,
          status: "BLOCKED",
          segments: [],
        });
        continue;
      }
      const path = closed.path(res, src, dst)!;
      const km = closed.pathLengthKm(path);
      const pts = path.map((n) => closed.cellCenter(n));
      outcomes.push({
        route_id: r.route_id,
        source_port_id: r.source_port_id,
        target_port_id: r.target_port_id,
        baseline_km: r.distance_km,
        baseline_days: r.transit_time_days,
        simulated_km: km,
        simulated_days: transitDays(km),
        additional_km: km - r.distance_km,
        additional_days: transitDays(km) - r.transit_time_days,
        status: km > r.distance_km + 1e-6 ? "REROUTED" : "UNAFFECTED",
        segments: splitAntimeridian(pts),
      });
    }
  }

  const congestion = computeCongestion(outcomes, ports);
  const rerouted = outcomes.filter((o) => o.status === "REROUTED");
  const blocked = outcomes.filter((o) => o.status === "BLOCKED");
  const addKm = rerouted.reduce((s, o) => s + (o.additional_km ?? 0), 0);
  const addDays = rerouted.length
    ? rerouted.reduce((s, o) => s + (o.additional_days ?? 0), 0) / rerouted.length
    : null;

  // 原典 §101。**暫定の指標であることを画面に書く。**
  const total = outcomes.length || 1;
  const impact = (rerouted.length * 0.5 + blocked.length) / total;
  const resilience = Math.max(0, 1 - impact);

  return {
    scenario,
    outcomes,
    congestion,
    metrics: {
      affected_routes: rerouted.length + blocked.length,
      rerouted_routes: rerouted.length,
      blocked_routes: blocked.length,
      additional_km_total: addKm,
      additional_days_mean: addDays,
      ports_over_capacity: congestion.filter((c) => c.band === "OVER_CAPACITY").length,
      resilience,
    },
    elapsed_ms: performance.now() - t0,
    confidence: "SIMULATED",
  };
}

/**
 * 混雑(原典 §61〜§63)。
 *
 * **容量は観測値ではない。** 平常時にその港へ入ってくる航路の本数を「基準の荷」とし、
 * その 1.5 倍を容量と仮定する。仮定を変えれば数も変わるので `SIMULATED` である。
 */
export const CAPACITY_FACTOR = 1.5;

export function computeCongestion(
  outcomes: RouteOutcome[],
  ports: RoutePort[],
): PortCongestion[] {
  const baseline = new Map<string, number>();
  const extra = new Map<string, number>();
  for (const p of ports) baseline.set(p.port_id, 0);

  for (const o of outcomes) {
    for (const id of [o.source_port_id, o.target_port_id]) {
      baseline.set(id, (baseline.get(id) ?? 0) + 1);
    }
  }
  for (const o of outcomes) {
    if (o.status !== "REROUTED" || o.additional_days === null) continue;
    // 遠回りになった分だけ、その航路が港に居座る時間が伸びる、という単純な模型。
    const load = o.additional_days / Math.max(1e-6, o.baseline_days);
    for (const id of [o.source_port_id, o.target_port_id]) {
      extra.set(id, (extra.get(id) ?? 0) + load);
    }
  }

  const out: PortCongestion[] = [];
  for (const p of ports) {
    const base = baseline.get(p.port_id) ?? 0;
    const add = extra.get(p.port_id) ?? 0;
    const capacity = Math.max(1, base * CAPACITY_FACTOR);
    const utilization = (base + add) / capacity;
    out.push({
      port_id: p.port_id,
      baseline_load: base,
      additional_load: add,
      capacity,
      utilization,
      band: congestionBand(utilization),
    });
  }
  return out.sort((a, b) => b.utilization - a.utilization);
}
