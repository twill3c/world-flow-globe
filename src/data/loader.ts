/**
 * 静的データの読み込み(原典 §5・§91 / SPEC.md N-01)。
 *
 * **外部ホストを一度も呼ばない。** 読むのは自分の `public/data` だけである。
 * 課金経路もサーバー関数も持たないので、通常の閲覧で外部データ取得の失敗は起きない。
 */

import type {
  ChokepointsDoc,
  CountryProps,
  EezProps,
  FeatureCollection,
  LayersDoc,
  NavGridDoc,
  PortProps,
  RoutesDoc,
  SourceRegistry,
} from "../types";

const BASE = `${import.meta.env.BASE_URL ?? "/"}data/`;

async function loadJson<T>(relPath: string): Promise<T> {
  const url = `${BASE}${relPath}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`データを読めない: ${relPath}(HTTP ${res.status})`);
  }
  return (await res.json()) as T;
}

/** 同じファイルを二度取りに行かない。 */
const cache = new Map<string, Promise<unknown>>();

function cached<T>(relPath: string): Promise<T> {
  const hit = cache.get(relPath);
  if (hit) return hit as Promise<T>;
  const p = loadJson<T>(relPath);
  cache.set(relPath, p);
  return p;
}

export const loadLayers = () => cached<LayersDoc>("layers.json");
export const loadSources = () => cached<SourceRegistry>("source_registry.json");
export const loadCountries = () =>
  cached<FeatureCollection<CountryProps>>("base/countries.geojson");
export const loadEez = () => cached<FeatureCollection<EezProps>>("base/eez.geojson");
export const loadPorts = () =>
  cached<FeatureCollection<PortProps>>("infrastructure/ports.geojson");
export const loadChokepoints = () =>
  cached<ChokepointsDoc>("infrastructure/chokepoints.json");
export const loadNavGrid = () => cached<NavGridDoc>("infrastructure/nav_grid.json");
export const loadRoutes = () => cached<RoutesDoc>("infrastructure/shipping_routes.json");
