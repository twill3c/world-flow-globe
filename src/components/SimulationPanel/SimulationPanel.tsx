/**
 * チョークポイント・シミュレータとタイムライン(原典 §98〜§101 / SPEC.md F-08〜F-12)。
 *
 * **出す数はすべて「模擬」の印を付ける。** 予測ではない。
 */

import { useMemo, useState } from "react";
import { NavGraph } from "../../graph/navGraph";
import { runScenario, type SimulationResult } from "../../simulation/closure";
import type {
  Chokepoint,
  FeatureCollection,
  NavGridDoc,
  PortProps,
  RoutesDoc,
} from "../../types";

const DURATION_PRESETS = [1, 3, 7, 14, 30, 60, 90];
const CAPACITY_PRESETS = [10, 25, 50, 75, 90, 100];

interface Props {
  chokepoints: Chokepoint[];
  navGrid: NavGridDoc | null;
  navMask: Uint8Array | null;
  routesDoc: RoutesDoc | null;
  ports: FeatureCollection<PortProps> | null;
  onResult: (r: {
    closedIds: string[];
    rerouted: { route_id: string; segments: [number, number][][] }[];
    congestion: Record<string, number>;
  }) => void;
}

export default function SimulationPanel(props: Props) {
  const [target, setTarget] = useState("CHOKE_SUEZ");
  const [duration, setDuration] = useState(7);
  const [capacity, setCapacity] = useState(100);
  const [allowPolar, setAllowPolar] = useState(false);
  const [day, setDay] = useState(0);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [result, setResult] = useState<SimulationResult | null>(null);

  const ready = props.navGrid && props.navMask && props.routesDoc;

  const graph = useMemo(() => {
    if (!props.navGrid || !props.navMask) return null;
    return NavGraph.fromDoc(props.navGrid, props.navMask, { allowPolar });
  }, [props.navGrid, props.navMask, allowPolar]);

  const run = async () => {
    if (!graph || !props.routesDoc || !props.navGrid) return;
    const choke = props.chokepoints.find((c) => c.id === target);
    if (!choke) return;
    setRunning(true);
    setProgress({ done: 0, total: 0 });
    const r = await runScenario(
      graph,
      { chokepointId: target, durationDays: duration, capacityReductionPercent: capacity },
      choke,
      props.routesDoc.routes,
      props.routesDoc.ports,
      props.navGrid.cols,
      (done, total) => setProgress({ done, total }),
    );
    setResult(r);
    setDay(Math.min(duration, 7));
    setRunning(false);
    props.onResult({
      closedIds: [target],
      rerouted: r.outcomes
        .filter((o) => o.status === "REROUTED")
        .map((o) => ({ route_id: o.route_id, segments: o.segments })),
      congestion: Object.fromEntries(r.congestion.map((c) => [c.port_id, c.utilization])),
    });
  };

  const reset = () => {
    setResult(null);
    props.onResult({ closedIds: [], rerouted: [], congestion: {} });
  };

  const active = result ? day <= result.scenario.durationDays : false;

  return (
    <section className="panel simulation-panel" aria-label="チョークポイント・シミュレータ">
      <h2>チョークポイント・シミュレータ</h2>

      <label className="field">
        対象
        <select value={target} onChange={(e) => setTarget(e.target.value)}>
          {props.chokepoints.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name_ja}({c.type === "CANAL" ? "運河" : "海峡"})
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        期間 <output>{duration} 日</output>
        <input
          type="range"
          min={1}
          max={90}
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
          aria-label="閉鎖期間(日)"
        />
      </label>
      <div className="chip-row">
        {DURATION_PRESETS.map((d) => (
          <button key={d} type="button" onClick={() => setDuration(d)} aria-label={`${d} 日`}>
            {d}
          </button>
        ))}
      </div>

      <label className="field">
        容量減 <output>{capacity}%</output>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={capacity}
          onChange={(e) => setCapacity(Number(e.target.value))}
          aria-label="容量減(%)"
        />
      </label>
      <div className="chip-row">
        {CAPACITY_PRESETS.map((c) => (
          <button key={c} type="button" onClick={() => setCapacity(c)} aria-label={`${c} パーセント`}>
            {c}%
          </button>
        ))}
      </div>

      <label className="assumption">
        <input
          type="checkbox"
          checked={allowPolar}
          onChange={(e) => setAllowPolar(e.target.checked)}
        />
        北極海を通れることにする
      </label>
      <p className="assumption-note">
        <strong>この模型に海氷はありません。</strong>
        既定では北緯 70 度より高い海と南緯 60 度より低い海を通れないことにしています。
        外すと、スエズを閉じた船が<strong>北極点の上を通ります</strong>
        (実測: 626 セル中 408 セルが 70°N 以北・最北 89.75°N)。
        <span className="badge conf-ESTIMATED">仮定計算</span>
      </p>

      <div className="button-row">
        <button type="button" className="primary" onClick={run} disabled={!ready || running}>
          {running
            ? progress.total > 0
              ? `計算中… ${progress.done}/${progress.total} 港`
              : "計算中…"
            : "シミュレーションを実行"}
        </button>
        <button type="button" onClick={reset} disabled={!result}>
          もどす
        </button>
      </div>

      {result && (
        <>
          <div className="timeline">
            <label className="field">
              Day <output>{day}</output>
              <input
                type="range"
                min={0}
                max={90}
                value={day}
                onChange={(e) => setDay(Number(e.target.value))}
                aria-label="タイムライン(日)"
              />
            </label>
            <p className={`timeline-state ${active ? "closed" : "open"}`}>
              Day {day}: {target} は{active ? "閉鎖中" : "開通"}
              {active ? `(残り ${result.scenario.durationDays - day} 日)` : ""}
            </p>
          </div>

          <Metrics result={result} active={active} />
          <CongestionTable result={result} active={active} />
        </>
      )}
    </section>
  );
}

function Metrics({ result, active }: { result: SimulationResult; active: boolean }) {
  const m = result.metrics;
  const dash = (v: number | null, f: (x: number) => string) => (v === null ? "—" : f(v));
  return (
    <div className="metrics">
      <h3>
        影響指標 <span className="badge conf-SIMULATED">模擬</span>
      </h3>
      {!active && (
        <p className="muted">
          この日は開通しているので、平常時の値に戻ります(下の数は閉鎖中のものです)。
        </p>
      )}
      <dl>
        <dt>影響を受けた航路</dt>
        <dd>
          {m.affected_routes} / {result.outcomes.length}
        </dd>
        <dt>再ルート</dt>
        <dd>{m.rerouted_routes}</dd>
        <dt>到達不能になった航路</dt>
        <dd>{m.blocked_routes}</dd>
        <dt>追加距離の合計</dt>
        <dd>{Math.round(m.additional_km_total).toLocaleString()} km</dd>
        <dt>追加日数の平均</dt>
        <dd>{dash(m.additional_days_mean, (x) => `${x.toFixed(2)} 日`)}</dd>
        <dt>容量超過の港</dt>
        <dd>{m.ports_over_capacity}</dd>
        <dt>レジリエンス</dt>
        <dd>{m.resilience.toFixed(3)}</dd>
      </dl>
      <p className="muted">
        レジリエンスは暫定の指標です(原典 §101)。
        <code>1 −(再ルート×0.5 + 不通)/ 総数</code> で計算しています。
        <strong>測った量ではなく、この式が定義した量です。</strong>
      </p>
      <p className="muted">計算時間 {Math.round(result.elapsed_ms)} ms</p>
    </div>
  );
}

function CongestionTable({ result, active }: { result: SimulationResult; active: boolean }) {
  const rows = result.congestion.filter((c) => c.additional_load > 0).slice(0, 8);
  return (
    <div className="congestion">
      <h3>
        港の混雑 <span className="badge conf-SIMULATED">模擬</span>
      </h3>
      {rows.length === 0 ? (
        <p className="muted">荷が寄った港はありません。</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>港</th>
              <th>利用率</th>
              <th>区分</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.port_id} className={active ? "" : "inactive"}>
                <td>{c.port_id.replace("PORT_", "")}</td>
                <td>{c.utilization.toFixed(2)}</td>
                <td>
                  <span className={`band band-${c.band}`}>{c.band}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">
        容量は観測値ではありません。平常時にその港へ入る航路の本数の
        {" "}<strong>1.5 倍</strong>を容量と仮定しています。仮定を変えれば数も変わります。
      </p>
    </div>
  );
}
