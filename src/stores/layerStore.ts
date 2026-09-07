/**
 * レイヤーの状態(原典 §44・§47・§92 / SPEC.md F-06・F-13)。
 *
 * ## 表示と計算を分ける(原典 §50)
 *
 * レイヤーを OFF にしてもシミュレーションの計算は止めない。
 * ここが持つのは**見せ方**だけで、経路や混雑の計算は `simulation` 側にある。
 */

import { create } from "zustand";
import type { LayerDef, Preset } from "../types";

export interface LayerState {
  id: string;
  visible: boolean;
  opacity: number;
  zOrder: number;
}

interface SavedView {
  layers: Record<string, { visible: boolean; opacity: number; zOrder: number }>;
  exclusive: boolean;
  camera?: { lat: number; lng: number; altitude: number };
}

const STORAGE_KEY = "world-flow-globe:view:v1";

interface LayerStore {
  defs: LayerDef[];
  presets: Preset[];
  layers: Record<string, LayerState>;
  order: string[];
  search: string;
  exclusive: boolean;
  selectedLayerId: string | null;
  initialised: boolean;

  init: (defs: LayerDef[], presets: Preset[], defaultPreset: string) => void;
  toggleLayer: (id: string) => void;
  setOpacity: (id: string, opacity: number) => void;
  setZOrder: (id: string, zOrder: number) => void;
  applyPreset: (presetId: string) => void;
  setSearch: (q: string) => void;
  setExclusive: (on: boolean) => void;
  selectLayer: (id: string | null) => void;
  saveView: (camera?: { lat: number; lng: number; altitude: number }) => void;
  visibleIds: () => string[];
}

function readSaved(): SavedView | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SavedView) : null;
  } catch {
    // localStorage が使えない文脈(プライベートウィンドウ等)でも動くこと
    return null;
  }
}

export const useLayerStore = create<LayerStore>((set, get) => ({
  defs: [],
  presets: [],
  layers: {},
  order: [],
  search: "",
  exclusive: false,
  selectedLayerId: null,
  initialised: false,

  init: (defs, presets, defaultPreset) => {
    const saved = readSaved();
    const preset = presets.find((p) => p.id === defaultPreset);
    const layers: Record<string, LayerState> = {};
    for (const d of defs) {
      const s = saved?.layers?.[d.id];
      const visibleFromPreset = preset ? preset.layers.includes(d.id) : d.visible_default;
      layers[d.id] = {
        id: d.id,
        // 出せないレイヤーは、保存された状態がどうであれ ON にしない
        visible: d.available ? (s ? s.visible : visibleFromPreset) : false,
        opacity: s ? s.opacity : d.opacity_default,
        zOrder: s ? s.zOrder : d.z_order,
      };
    }
    set({
      defs,
      presets,
      layers,
      order: [...defs].sort((a, b) => a.z_order - b.z_order).map((d) => d.id),
      exclusive: saved?.exclusive ?? false,
      initialised: true,
    });
  },

  toggleLayer: (id) => {
    const def = get().defs.find((d) => d.id === id);
    if (!def?.available) return;
    set((st) => {
      const next = { ...st.layers };
      const cur = next[id];
      if (!cur) return st;
      if (st.exclusive) {
        // 排他モード: 選んだレイヤーだけを残す(BASE の OCEAN は土台なので残す)
        for (const key of Object.keys(next)) {
          const k = next[key];
          if (!k) continue;
          next[key] = { ...k, visible: key === id || key === "OCEAN" };
        }
      } else {
        next[id] = { ...cur, visible: !cur.visible };
      }
      return { layers: next };
    });
    get().saveView();
  },

  setOpacity: (id, opacity) => {
    set((st) => {
      const cur = st.layers[id];
      if (!cur) return st;
      return { layers: { ...st.layers, [id]: { ...cur, opacity } } };
    });
    get().saveView();
  },

  setZOrder: (id, zOrder) => {
    set((st) => {
      const cur = st.layers[id];
      if (!cur) return st;
      const layers = { ...st.layers, [id]: { ...cur, zOrder } };
      return {
        layers,
        order: Object.values(layers)
          .sort((a, b) => a.zOrder - b.zOrder)
          .map((l) => l.id),
      };
    });
    get().saveView();
  },

  applyPreset: (presetId) => {
    const st = get();
    const preset = st.presets.find((p) => p.id === presetId);
    if (!preset) return;
    const next = { ...st.layers };
    for (const d of st.defs) {
      const cur = next[d.id];
      if (!cur) continue;
      next[d.id] = { ...cur, visible: d.available && preset.layers.includes(d.id) };
    }
    set({ layers: next });
    get().saveView();
  },

  setSearch: (q) => set({ search: q }),
  setExclusive: (on) => {
    set({ exclusive: on });
    get().saveView();
  },
  selectLayer: (id) => set({ selectedLayerId: id }),

  saveView: (camera) => {
    const st = get();
    const payload: SavedView = {
      layers: Object.fromEntries(
        Object.entries(st.layers).map(([k, v]) => [
          k,
          { visible: v.visible, opacity: v.opacity, zOrder: v.zOrder },
        ]),
      ),
      exclusive: st.exclusive,
      ...(camera ? { camera } : {}),
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // 保存できなくても画面は動き続ける
    }
  },

  visibleIds: () => {
    const st = get();
    return st.order.filter((id) => st.layers[id]?.visible);
  },
}));

/** レイヤー検索(原典 §120)。ID・英名・和名のいずれかに当たれば拾う。 */
export function matchesSearch(def: LayerDef, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    def.id.toLowerCase().includes(q) ||
    def.name.toLowerCase().includes(q) ||
    def.name_ja.includes(query.trim())
  );
}
