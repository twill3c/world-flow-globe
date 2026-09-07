/**
 * 出典パネルと凡例(原典 §75〜§77・§123 / SPEC.md F-07・F-14)。
 *
 * レイヤーを選ぶと、その線が**何なのか**を出す ——
 * 出典・版・ライセンス・取得日・意味区分。
 */

import { CONFIDENCE_DESCRIPTION, CONFIDENCE_LABEL, type Confidence } from "../../types";
import type { LayerDef, SourceDef } from "../../types";
import { useLayerStore } from "../../stores/layerStore";

interface Props {
  sources: SourceDef[];
  manifests: Record<string, { retrieved_at?: string; records?: number }>;
}

export default function DataInfo({ sources, manifests }: Props) {
  const { defs, selectedLayerId } = useLayerStore();
  const def = defs.find((d) => d.id === selectedLayerId) ?? null;
  const src = def?.data_source ? sources.find((s) => s.id === def.data_source) : null;

  return (
    <section className="panel data-info" aria-label="出典">
      <h2>出典</h2>
      {!def && (
        <>
          <p className="hint">レイヤーの「出典」を押すと、その線の出所が出ます。</p>
          <ConfidenceLegend />
        </>
      )}

      {def && (
        <div className="source-card">
          <h3>{def.name_ja}</h3>
          <dl>
            <dt>意味区分</dt>
            <dd>
              <span className={`badge conf-${def.confidence}`}>
                {CONFIDENCE_LABEL[def.confidence]}
              </span>
              <span className="conf-desc">{CONFIDENCE_DESCRIPTION[def.confidence]}</span>
            </dd>
            {src ? (
              <>
                <dt>提供</dt>
                <dd>{src.provider}</dd>
                <dt>データセット</dt>
                <dd>{src.dataset}</dd>
                <dt>版</dt>
                <dd>{src.version ?? "—"}</dd>
                <dt>公開</dt>
                <dd>{src.published ?? "—"}</dd>
                <dt>ライセンス</dt>
                <dd>
                  <a href={src.license_url} target="_blank" rel="noreferrer noopener">
                    {src.license}
                  </a>
                </dd>
                <dt>取得</dt>
                <dd>{manifests[src.id]?.retrieved_at ?? "—"}</dd>
                <dt>出典ページ</dt>
                <dd>
                  <a href={src.landing_url} target="_blank" rel="noreferrer noopener">
                    {src.landing_url}
                  </a>
                </dd>
              </>
            ) : (
              <>
                <dt>出所</dt>
                <dd>
                  {def.available
                    ? "このレイヤーは本アプリの計算結果です(外部の出典を持ちません)"
                    : "—"}
                </dd>
              </>
            )}
          </dl>
          {def.note && <p className="layer-note">{def.note}</p>}
          {!def.available && def.unavailable_reason && (
            <p className="unavailable-reason">
              <strong>出していません。</strong>
              {def.unavailable_reason}
            </p>
          )}
          <UnavailableSourceNote src={src} />
        </div>
      )}

      <details className="all-sources">
        <summary>すべての出典({sources.length} 件)</summary>
        <ul>
          {sources.map((s) => (
            <li key={s.id}>
              <strong>{s.id}</strong> {s.provider} — {s.dataset}
              <br />
              <a href={s.landing_url} target="_blank" rel="noreferrer noopener">
                {s.license}
              </a>
              {s.unavailable_reason && (
                <span className="muted">(未取得: {s.unavailable_reason})</span>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function UnavailableSourceNote({ src }: { src: SourceDef | null | undefined }) {
  if (!src?.unavailable_reason) return null;
  return <p className="unavailable-reason">出典側: {src.unavailable_reason}</p>;
}

export function ConfidenceLegend() {
  const order: Confidence[] = [
    "OBSERVED",
    "STATISTICAL",
    "INFERRED",
    "ESTIMATED",
    "SIMULATED",
    "PREDICTED",
  ];
  return (
    <div className="legend">
      <h3>この地球儀の読み方</h3>
      <p className="legend-lead">
        線・点・数には、すべて<strong>出所の階級</strong>が付いています。
      </p>
      <ul>
        {order.map((c) => (
          <li key={c}>
            <span className={`badge conf-${c}`}>{CONFIDENCE_LABEL[c]}</span>
            <span className="conf-desc">{CONFIDENCE_DESCRIPTION[c]}</span>
          </li>
        ))}
      </ul>
      <p className="legend-warn">
        <strong>航路は実船の航跡ではありません。</strong>
        港・航行可能海域・チョークポイントから、このアプリが引いた線です。
      </p>
      <p className="legend-warn">
        値が無い欄は「—」と出します。<strong>0 として描きません。</strong>
      </p>
    </div>
  );
}

export function layerSourceId(def: LayerDef): string | null {
  return def.data_source;
}
