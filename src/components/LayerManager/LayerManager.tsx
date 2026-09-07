/**
 * レイヤー管理(原典 §44・§48・§119〜§121 / SPEC.md F-06・F-07・F-15)。
 *
 * 出せないレイヤーも**消さずに並べる**。理由をその場で読めるようにする ——
 * 「無い」のか「手を抜いた」のかが利用者に分かるように。
 */

import { CATEGORY_LABEL, CONFIDENCE_LABEL, type LayerCategory, type LayerDef } from "../../types";
import { matchesSearch, useLayerStore } from "../../stores/layerStore";

const CATEGORY_ORDER: LayerCategory[] = ["BASE", "INFRASTRUCTURE", "ECONOMY", "SIMULATION", "AI"];

export default function LayerManager() {
  const {
    defs,
    presets,
    layers,
    search,
    exclusive,
    selectedLayerId,
    toggleLayer,
    setOpacity,
    setZOrder,
    applyPreset,
    setSearch,
    setExclusive,
    selectLayer,
  } = useLayerStore();

  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    items: defs.filter((d) => d.category === cat && matchesSearch(d, search)),
  })).filter((g) => g.items.length > 0);

  return (
    <section className="panel layer-manager" aria-label="レイヤー管理">
      <h2>レイヤー管理</h2>

      <div className="preset-row">
        {presets.map((p) => (
          <button key={p.id} type="button" onClick={() => applyPreset(p.id)}>
            {p.name_ja}
          </button>
        ))}
      </div>

      <div className="control-row">
        <input
          type="search"
          placeholder="レイヤーを探す…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="レイヤー検索"
        />
        <label className="exclusive">
          <input
            type="checkbox"
            checked={exclusive}
            onChange={(e) => setExclusive(e.target.checked)}
          />
          排他
        </label>
      </div>

      {grouped.length === 0 && <p className="empty">当てはまるレイヤーがありません</p>}

      {grouped.map(({ cat, items }) => (
        <div key={cat} className="layer-group">
          <h3>{CATEGORY_LABEL[cat]}</h3>
          <ul>
            {items.map((def) => (
              <LayerRow
                key={def.id}
                def={def}
                state={layers[def.id]}
                selected={selectedLayerId === def.id}
                onToggle={() => toggleLayer(def.id)}
                onOpacity={(v) => setOpacity(def.id, v)}
                onZ={(v) => setZOrder(def.id, v)}
                onSelect={() => selectLayer(selectedLayerId === def.id ? null : def.id)}
              />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

function LayerRow(props: {
  def: LayerDef;
  state: { visible: boolean; opacity: number; zOrder: number } | undefined;
  selected: boolean;
  onToggle: () => void;
  onOpacity: (v: number) => void;
  onZ: (v: number) => void;
  onSelect: () => void;
}) {
  const { def, state } = props;
  const disabled = !def.available;
  return (
    <li
      className={`layer-row${disabled ? " unavailable" : ""}${props.selected ? " selected" : ""}`}
      data-layer-id={def.id}
    >
      <div className="layer-head">
        <label className="layer-toggle">
          <input
            type="checkbox"
            checked={state?.visible ?? false}
            disabled={disabled}
            onChange={props.onToggle}
            aria-label={`${def.name_ja} の表示`}
          />
          <span className="layer-name">{def.name_ja}</span>
        </label>
        <span className={`badge conf-${def.confidence}`} title={def.confidence}>
          {CONFIDENCE_LABEL[def.confidence]}
        </span>
        <button
          type="button"
          className="info-button"
          onClick={props.onSelect}
          aria-label={`${def.name_ja} の出典`}
          aria-expanded={props.selected}
        >
          出典
        </button>
      </div>

      {disabled ? (
        <p className="unavailable-reason">
          <strong>出していません。</strong>
          {def.unavailable_reason}
        </p>
      ) : (
        <div className="layer-controls">
          <label>
            濃さ
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={state?.opacity ?? def.opacity_default}
              onChange={(e) => props.onOpacity(Number(e.target.value))}
              aria-label={`${def.name_ja} の不透明度`}
            />
            <output>{Math.round((state?.opacity ?? def.opacity_default) * 100)}%</output>
          </label>
          <label>
            重ね順
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={state?.zOrder ?? def.z_order}
              onChange={(e) => props.onZ(Number(e.target.value))}
              aria-label={`${def.name_ja} の重ね順`}
            />
            <output>{state?.zOrder ?? def.z_order}</output>
          </label>
        </div>
      )}

      {def.note && <p className="layer-note">{def.note}</p>}
    </li>
  );
}
