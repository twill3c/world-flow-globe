/**
 * 画面の組み立て(SPEC.md §8)。
 *
 * データは**遅延して読む**。EEZ は 2.4 MB あり、既定では消えているので、
 * 利用者が点けたときに初めて取りに行く(N-04 の初期表示 5 秒に効く)。
 */

import { Suspense, lazy, useCallback, useEffect, useState } from "react";

// 地球儀は three.js と globe.gl を引き連れてくる(出荷 JS の大半)。
// **殻を先に出す。** これを静的 import にすると、レイヤー管理もフッタも
// 1.9 MB の解析が終わるまで出てこない(2026-09-08 実測で 5.5 秒)。
const GlobeView = lazy(() => import("./components/Globe/GlobeView"));
import LayerManager from "./components/LayerManager/LayerManager";
import DataInfo from "./components/DataInfo/DataInfo";
import Footer from "./components/Footer/Footer";
import SimulationPanel from "./components/SimulationPanel/SimulationPanel";
import { useLayerStore } from "./stores/layerStore";
import {
  loadChokepoints,
  loadCountries,
  loadEez,
  loadLayers,
  loadNavGrid,
  loadPorts,
  loadRoutes,
  loadSources,
} from "./data/loader";
import { unpackMask } from "./graph/navGraph";
import type {
  Chokepoint,
  ChokepointsDoc,
  CountryProps,
  EezProps,
  FeatureCollection,
  NavGridDoc,
  PortProps,
  RoutesDoc,
  SourceDef,
} from "./types";

export default function App() {
  const initLayers = useLayerStore((s) => s.init);
  const layers = useLayerStore((s) => s.layers);
  const initialised = useLayerStore((s) => s.initialised);

  const [sources, setSources] = useState<SourceDef[]>([]);
  const [countries, setCountries] = useState<FeatureCollection<CountryProps> | null>(null);
  const [eez, setEez] = useState<FeatureCollection<EezProps> | null>(null);
  const [ports, setPorts] = useState<FeatureCollection<PortProps> | null>(null);
  const [chokeDoc, setChokeDoc] = useState<ChokepointsDoc | null>(null);
  const [navGrid, setNavGrid] = useState<NavGridDoc | null>(null);
  const [navMask, setNavMask] = useState<Uint8Array | null>(null);
  const [routesDoc, setRoutesDoc] = useState<RoutesDoc | null>(null);
  const [selectedPort, setSelectedPort] = useState<PortProps | null>(null);
  const [selectedChoke, setSelectedChoke] = useState<Chokepoint | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [closedIds, setClosedIds] = useState<string[]>([]);
  const [rerouted, setRerouted] = useState<
    { route_id: string; segments: [number, number][][] }[]
  >([]);
  const [congestion, setCongestion] = useState<Record<string, number>>({});

  // 起動時: 軽いものだけ読む
  useEffect(() => {
    (async () => {
      try {
        // 第一段: 画面の骨格に要るものだけ。ここが出るまでが「初期表示」である。
        const [l, s] = await Promise.all([loadLayers(), loadSources()]);
        initLayers(l.layers, l.presets, l.default_preset);
        setSources(s.sources);

        // 第二段: 地球儀に載せるもの。届いた順に画面へ足す。
        loadCountries().then(setCountries);
        loadChokepoints().then(setChokeDoc);
        loadPorts().then(setPorts);
        loadNavGrid().then((nav) => {
          setNavGrid(nav);
          setNavMask(unpackMask(nav.mask_base64, nav.rows * nav.cols));
        });
        loadRoutes().then(setRoutesDoc);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [initLayers]);

  // EEZ は点いたときに読む。
  // **ここの失敗でアプリ全体を落とさない。** 一枚のレイヤーが読めないことと、
  // アプリが使えないことは別である(2026-09-08 に、eez.geojson の JSON 破損で
  // 画面全体が「データを読めませんでした」になった。HC-210)。
  const [layerErrors, setLayerErrors] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!layers["EEZ"]?.visible || eez || layerErrors["EEZ"]) return;
    loadEez()
      .then(setEez)
      .catch((e) =>
        setLayerErrors((prev) => ({ ...prev, EEZ: e instanceof Error ? e.message : String(e) })),
      );
  }, [layers, eez, layerErrors]);

  const onSimulation = useCallback(
    (result: {
      closedIds: string[];
      rerouted: { route_id: string; segments: [number, number][][] }[];
      congestion: Record<string, number>;
    }) => {
      setClosedIds(result.closedIds);
      setRerouted(result.rerouted);
      setCongestion(result.congestion);
    },
    [],
  );

  if (error) {
    return (
      <div className="fatal">
        <h1>データを読めませんでした</h1>
        <p>{error}</p>
        <p>
          このアプリは静的データだけで動きます。通常の閲覧でここに来ることはありません。
        </p>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>WORLD FLOW GLOBE</h1>
        <p className="tagline">
          世界の物流ネットワークを公開データから組み直し、チョークポイントを閉じてみる
        </p>
      </header>

      <main className="stage">
        <div className="globe-column">
          <Suspense fallback={<p className="loading">地球儀を読み込み中…</p>}>
            <GlobeView
            countries={countries}
            eez={eez}
            ports={ports}
            chokepoints={chokeDoc?.chokepoints ?? []}
            routes={routesDoc?.routes ?? []}
            navGrid={navGrid}
            navMask={navMask}
            closedChokepointIds={closedIds}
            rerouted={rerouted}
            congestionByPortId={congestion}
              onSelectPort={setSelectedPort}
              onSelectChokepoint={setSelectedChoke}
            />
          </Suspense>
          {!initialised && <p className="loading">読み込み中…</p>}
          {Object.entries(layerErrors).map(([id, msg]) => (
            <p key={id} className="layer-error" role="status">
              {id} のデータを読めませんでした({msg})。
              他のレイヤーはそのまま使えます。
            </p>
          ))}
          <Selection port={selectedPort} choke={selectedChoke} caveat={chokeDoc?.coordinate_caveat} />
        </div>

        <aside className="side-column">
          <LayerManager />
          <SimulationPanel
            chokepoints={chokeDoc?.chokepoints ?? []}
            navGrid={navGrid}
            navMask={navMask}
            routesDoc={routesDoc}
            ports={ports}
            onResult={onSimulation}
          />
          <DataInfo sources={sources} manifests={{}} />
        </aside>
      </main>

      <Footer />
    </div>
  );
}

function Selection(props: {
  port: PortProps | null;
  choke: Chokepoint | null;
  caveat: string | undefined;
}) {
  if (!props.port && !props.choke) return null;
  if (props.choke) {
    const c = props.choke;
    return (
      <div className="selection-card">
        <h3>
          {c.name_ja} <span className="muted">{c.name}</span>
        </h3>
        <p>
          種別: {c.type === "CANAL" ? "運河" : "海峡"} / 連結する海:{" "}
          {c.connected_seas.join(" ・ ")}
        </p>
        <p>
          代替路:{" "}
          {c.alternative_routes.length > 0 ? c.alternative_routes.join(" ・ ") : "登録なし"}
        </p>
        {c.notes.map((n) => (
          <p key={n} className="muted">
            {n}
          </p>
        ))}
        {props.caveat && <p className="muted">{props.caveat}</p>}
      </div>
    );
  }
  const p = props.port!;
  return (
    <div className="selection-card">
      <h3>
        {p.name} <span className="muted">{p.unlocode}</span>
      </h3>
      <p>
        国: {p.country_name}
        {p.country_iso3 ? `(${p.country_iso3})` : "(ISO3 引けず)"}
      </p>
      <p>
        outflows: {p.outflows.toLocaleString()} <span className="badge conf-STATISTICAL">統計</span>
        <br />
        <span className="muted">単位は出典に明記が無いため相対量として扱う</span>
      </p>
      <p>
        経路に使える: {p.routable ? "はい" : "いいえ"}
        {p.routable && p.snap_km !== null ? `(格子への吸着 ${p.snap_km.toFixed(1)} km)` : ""}
      </p>
    </div>
  );
}
