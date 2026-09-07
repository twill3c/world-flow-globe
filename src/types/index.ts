import type { Geometry } from "geojson";

/**
 * 出荷データの型。
 *
 * SPEC.md §4 の意味区分を**型の上でも守る**。`confidence` は文字列ではなく
 * 6 つの合併型にしてあるので、綴りを間違えれば型検査が落ちる。
 */

/** SPEC.md §4。出荷するすべての数と線が、このうちちょうど一つを名乗る。 */
export type Confidence =
  | "OBSERVED"
  | "STATISTICAL"
  | "INFERRED"
  | "ESTIMATED"
  | "SIMULATED"
  | "PREDICTED";

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  OBSERVED: "観測",
  STATISTICAL: "統計",
  INFERRED: "推定",
  ESTIMATED: "仮定計算",
  SIMULATED: "模擬",
  PREDICTED: "予測",
};

export const CONFIDENCE_DESCRIPTION: Record<Confidence, string> = {
  OBSERVED: "実観測・公的地理データ。誰かが測った値である",
  STATISTICAL: "統計的な集計。個々の出来事ではなく、まとめた数である",
  INFERRED: "複数のデータから、こちらが推定したもの。観測ではない",
  ESTIMATED: "数式と仮定による計算。仮定を変えれば値も変わる",
  SIMULATED: "What-if 計算の出力。起きたことではなく、起きたらどうなるかである",
  PREDICTED: "機械学習モデルの出力。本バージョンでは出荷していない",
};

export type LayerCategory = "BASE" | "INFRASTRUCTURE" | "ECONOMY" | "SIMULATION" | "AI";

export const CATEGORY_LABEL: Record<LayerCategory, string> = {
  BASE: "基盤",
  INFRASTRUCTURE: "インフラ",
  ECONOMY: "経済",
  SIMULATION: "シミュレーション",
  AI: "AI",
};

export interface LayerDef {
  id: string;
  name: string;
  name_ja: string;
  category: LayerCategory;
  renderer: string;
  visible_default: boolean;
  opacity_default: number;
  z_order: number;
  confidence: Confidence;
  data_source: string | null;
  path?: string;
  available: boolean;
  unavailable_reason?: string;
  note?: string;
}

export interface Preset {
  id: string;
  name_ja: string;
  layers: string[];
}

export interface LayersDoc {
  version: string;
  generated_at: string;
  layers: LayerDef[];
  presets: Preset[];
  default_preset: string;
}

export interface SourceDef {
  id: string;
  provider: string;
  dataset: string;
  license: string;
  license_url: string;
  landing_url: string;
  download_url: string | null;
  version: string | null;
  published: string | null;
  expected_md5: string | null;
  unavailable_reason: string | null;
  notes: string[];
}

export interface SourceRegistry {
  version: string;
  sources: SourceDef[];
}

export interface CountryProps {
  iso_a3: string;
  name: string;
  continent: string;
  region: string;
  confidence: Confidence;
}

export type EezPolType = "200NM" | "Overlapping claim" | "Joint regime";

export interface EezProps {
  mrgid: number;
  geoname: string;
  sovereign: string;
  iso_ter1: string | null;
  territory: string | null;
  pol_type: EezPolType;
  confidence: Confidence;
}

export interface PortProps {
  port_id: string;
  name: string;
  name_ascii: string;
  country_name: string;
  country_iso2: string;
  country_iso3: string | null;
  unlocode: string;
  status: string;
  function: string;
  outflows: number;
  outflows_confidence: Confidence;
  grid_cell: [number, number] | null;
  snap_km: number | null;
  routable: boolean;
  confidence: Confidence;
}

export interface FeatureCollection<P> {
  type: "FeatureCollection";
  layer_id: string;
  confidence: Confidence;
  data_source: string;
  features: { type: "Feature"; properties: P; geometry: Geometry }[];
  [extra: string]: unknown;
}

export type ChokepointType = "CANAL" | "STRAIT";

export interface Chokepoint {
  id: string;
  code: string;
  name: string;
  name_ja: string;
  type: ChokepointType;
  lat: number;
  lon: number;
  location: { lat: number; lon: number };
  strategic_importance: number;
  connected_seas: string[];
  alternative_routes: string[];
  gate_radius_km: number | null;
  canal_id: string | null;
  notes: string[];
  confidence: Confidence;
}

export interface ChokepointsDoc {
  version: string;
  layer_id: string;
  confidence: Confidence;
  coordinate_caveat: string;
  chokepoints: Chokepoint[];
}

export interface CanalEdge {
  id: string;
  chokepoint_id: string;
  name: string;
  cells: [number, number][];
  length_km: number;
  length_source: string;
  geodesic_between_cells_km: number;
  weight_km: number;
  weight_rule: string;
}

export interface PassageRecord {
  id: string;
  name: string;
  waypoints: [number, number][];
  cells: [number, number][];
  cells_opened: number;
  criterion: string;
  reason: string;
}

export interface NavGridDoc {
  layer_id: string;
  confidence: Confidence;
  data_source: string;
  generated_at: string;
  resolution_deg: number;
  rows: number;
  cols: number;
  mask_encoding: string;
  mask_base64: string;
  sea_fraction_threshold: number;
  subsample: number;
  navigable_cells: number;
  navigable_cells_before_corrections: number;
  passages: PassageRecord[];
  canals: CanalEdge[];
  polar_limit: {
    north_deg: number;
    south_deg: number;
    confidence: Confidence;
    default_applied: boolean;
    toggleable: boolean;
    rationale: string;
  };
  weight_table_encoding: string;
  weight_table_base64: string;
  meridional_km: number;
  note: string;
}

export interface RoutePort {
  port_id: string;
  unlocode: string;
  name: string;
  country_iso3: string | null;
  outflows: number;
  grid_cell: [number, number];
  selected_by: "outflows" | "anchor";
  anchor_reason: string | null;
}

export interface RouteRecord {
  route_id: string;
  source_port_id: string;
  target_port_id: string;
  route_type: string;
  distance_km: number;
  geodesic_km: number;
  detour_ratio: number;
  transit_time_days: number;
  chokepoints: string[];
  path_cells: number;
  display_segments: [number, number][][];
  confidence: Confidence;
  transit_time_confidence: Confidence;
}

export interface RoutesDoc {
  version: string;
  layer_id: string;
  confidence: Confidence;
  transit_time_confidence: Confidence;
  generated_at: string;
  not_a_ship_track: string;
  selection_rule: {
    description: string;
    top_by_outflows: number;
    max_ports_per_country: number;
    geographic_anchors: { unlocode: string; reason: string }[];
  };
  speed_assumption: { knots: number; km_per_nm: number };
  grid: { resolution_deg: number; rows: number; cols: number };
  ports: RoutePort[];
  routes: RouteRecord[];
  unreachable_pairs: [string, string][];
  same_cell_pairs: [string, string][];
  same_cell_note: string;
}
