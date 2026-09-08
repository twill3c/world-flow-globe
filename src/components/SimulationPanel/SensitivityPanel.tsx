/**
 * 感度分析(SPEC.md F-17 / 原典 §64–§67 の代わり)。
 *
 * **予測ではない。** 容量減を振って、この模型がどう答えるかを並べたものである。
 * 出る数はすべて `SIMULATED`。
 */

import { useState } from "react";
import { NavGraph } from "../../graph/navGraph";
import { runSensitivity, type SensitivityResult } from "../../simulation/sensitivity";
import type { Chokepoint, NavGridDoc, RoutesDoc } from "../../types";

interface Props {
  chokepoint: Chokepoint | undefined;
  graph: NavGraph | null;
  navGrid: NavGridDoc | null;
  routesDoc: RoutesDoc | null;
}

export default function SensitivityPanel({ chokepoint, graph, navGrid, routesDoc }: Props) {
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [result, setResult] = useState<SensitivityResult | null>(null);

  const canalWeight =
    chokepoint?.type === "CANAL" && navGrid
      ? (navGrid.canals.find((c) => c.chokepoint_id === chokepoint.id)?.weight_km ?? null)
      : null;

  const run = async () => {
    if (!graph || !routesDoc || !navGrid || !chokepoint) return;
    setRunning(true);
    setResult(null);
    setProgress({ done: 0, total: 0 });
    const r = await runSensitivity(
      graph,
      chokepoint,
      routesDoc.routes,
      routesDoc.ports,
      navGrid.cols,
      canalWeight,
      (done, total) => setProgress({ done, total }),
    );
    setResult(r);
    setRunning(false);
  };

  const stale = result && chokepoint && result.chokepointId !== chokepoint.id;

  return (
    <section className="panel sensitivity-panel" aria-label="感度分析">
      <h2>
        感度分析 <span className="badge conf-SIMULATED">模擬</span>
      </h2>
      <p className="muted">
        <strong>AI の予測器は置いていません。</strong>
        学習に使える実測が無く、自分のシミュレータの出力で学習した予測器は
        シミュレータの再現以上のことを言わないからです。代わりに、
        <strong>容量減を振って応答を実際に計算します</strong>。
      </p>

      <div className="button-row">
        <button
          type="button"
          className="primary"
          onClick={run}
          disabled={!graph || !routesDoc || !chokepoint || running}
        >
          {running
            ? progress.total > 0
              ? `走査中… ${progress.done}/${progress.total} 水準`
              : "走査中…"
            : `${chokepoint?.name_ja ?? "対象"} の応答を測る`}
        </button>
      </div>

      {stale && <p className="muted">対象を変えたので、もう一度測ってください。</p>}

      {result && !stale && (
        <>
          <ResponseChart result={result} />
          <table className="sens-table">
            <thead>
              <tr>
                <th scope="col">容量減</th>
                <th scope="col">再ルート</th>
                <th scope="col">不通</th>
                <th scope="col">追加距離</th>
              </tr>
            </thead>
            <tbody>
              {result.points.map((p) => (
                <tr key={p.capacityReductionPercent}>
                  <td>{p.capacityReductionPercent}%</td>
                  <td className="n">{p.rerouted_routes}</td>
                  <td className="n">{p.blocked_routes}</td>
                  <td className="n">{Math.round(p.additional_km_total).toLocaleString()} km</td>
                </tr>
              ))}
            </tbody>
          </table>

          {result.closedFormAgrees !== null && (
            <p className={`crosscheck ${result.closedFormAgrees ? "agree" : "disagree"}`}>
              {result.closedFormAgrees ? "二経路一致" : "二経路が食い違っている"}:
              運河型なので、この応答は探索を使わない閉じた式
              <code>距離(p) = (D平常 + w(k−1) ≤ D閉鎖) ? D平常 : D閉鎖</code>
              でも計算できます。{result.closedFormAgrees
                ? "全水準で一致しました。"
                : "食い違いは実装の欠陥です。"}
            </p>
          )}

          <p className="muted">走査時間 {Math.round(result.elapsed_ms)} ms</p>
        </>
      )}
    </section>
  );
}

/** 応答の折れ目が見えるだけの、小さな図。 */
function ResponseChart({ result }: { result: SensitivityResult }) {
  const w = 300;
  const h = 120;
  const padL = 58;
  const padB = 22;
  const padT = 10;
  const padR = 8;
  const pts = result.points;
  const maxKm = Math.max(1, ...pts.map((p) => p.additional_km_total));
  const x = (pct: number) => padL + (pct / 100) * (w - padL - padR);
  const y = (km: number) => h - padB - (km / maxKm) * (h - padB - padT);

  const path = pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.capacityReductionPercent).toFixed(1)},${y(p.additional_km_total).toFixed(1)}`).join(" ");

  return (
    <figure className="sens-chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="容量減に対する追加距離の応答">
        <line x1={padL} y1={h - padB} x2={w - padR} y2={h - padB} stroke="var(--line)" strokeWidth="1" />
        <line x1={padL} y1={padT} x2={padL} y2={h - padB} stroke="var(--line)" strokeWidth="1" />
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.8" />
        {pts.map((p) => (
          <circle
            key={p.capacityReductionPercent}
            cx={x(p.capacityReductionPercent)}
            cy={y(p.additional_km_total)}
            r="2.6"
            fill="var(--accent)"
          />
        ))}
        {[0, 50, 100].map((pct) => (
          <text key={pct} x={x(pct)} y={h - 8} fontSize="8" textAnchor="middle" fill="var(--ink-dim)">
            {pct}%
          </text>
        ))}
        {/* 縦軸のラベル。**単位は最大値の行に併記する** ——
            別行に置くと最大値と重なる(2026-09-08 の目視で発見)。 */}
        <text x={padL - 4} y={y(maxKm) + 3} fontSize="8" textAnchor="end" fill="var(--ink-dim)">
          {Math.round(maxKm).toLocaleString()} km
        </text>
        <text x={padL - 4} y={h - padB + 3} fontSize="8" textAnchor="end" fill="var(--ink-dim)">
          0
        </text>
      </svg>
      <figcaption className="muted">
        横軸 = 容量減、縦軸 = 追加距離の合計。
        <strong>折れ目より下では距離が 1 km も動きません</strong> ——
        混雑しても、代替路のほうが高いあいだは運河を使い続けるからです。
      </figcaption>
    </figure>
  );
}
