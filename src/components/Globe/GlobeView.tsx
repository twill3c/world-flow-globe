/**
 * 3D 地球儀(原典 §93・§94 / SPEC.md F-01〜F-05)。
 *
 * ## 外部ホストを呼ばない(N-01)
 *
 * globe.gl の作例は地球のテクスチャを CDN から読むが、**それはしない**。
 * 海面は単色のマテリアルで塗り、その上に国・EEZ・航路を重ねる。
 * これで課金経路も外部依存もゼロのまま「海の上に陸がある」絵になる。
 *
 * ## Z 順は高度で表す
 *
 * globe.gl はポリゴンを一つの配列で受け取るので、レイヤーごとの重なりは
 * **地表からの高さ**で表す。レイヤー管理の Z 順スライダーはこの高さを動かす。
 */

import { useEffect, useMemo, useRef } from "react";
import Globe from "globe.gl";
import type { GlobeInstance } from "globe.gl";
import * as THREE from "three";
import type {
  Chokepoint,
  CountryProps,
  EezProps,
  FeatureCollection,
  NavGridDoc,
  PortProps,
  RouteRecord,
} from "../../types";
import { useLayerStore } from "../../stores/layerStore";
import { makeTexture, paintMapTexture } from "./mapTexture";

const OCEAN_COLOR = "#0a1b2c";
const COUNTRY_CAP = "#2f5c7e";
const COUNTRY_STROKE = "#6ea6c9";

/** EEZ は種別で塗り分ける。一色で塗ると「決まっている」という嘘になる。 */
const EEZ_COLOR: Record<string, string> = {
  "200NM": "#2f7f8f",
  "Overlapping claim": "#b4553f",
  "Joint regime": "#8a6fbe",
};

/** 航行可能グリッドを描くときの間引き。**実際のグリッドは 1 セルおきに全部ある。** */
export const NAV_GRID_STRIDE = 3;

interface Props {
  countries: FeatureCollection<CountryProps> | null;
  eez: FeatureCollection<EezProps> | null;
  ports: FeatureCollection<PortProps> | null;
  chokepoints: Chokepoint[];
  routes: RouteRecord[];
  navGrid: NavGridDoc | null;
  navMask: Uint8Array | null;
  closedChokepointIds: string[];
  rerouted: { route_id: string; segments: [number, number][][] }[];
  congestionByPortId: Record<string, number>;
  onSelectPort: (p: PortProps | null) => void;
  onSelectChokepoint: (c: Chokepoint | null) => void;
}

type PointDatum = {
  __layer: string;
  lat: number;
  lng: number;
  color: string;
  radius: number;
  altitude: number;
  label: string;
  port?: PortProps;
};

type PathDatum = {
  __layer: string;
  coords: [number, number][];
  color: string;
  stroke: number;
  dash: number;
  label: string;
};

function hexToRgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/** 混雑度の 4 区分(原典 §62)。**測っていない量に色を付けない** —— これは計算した値である。 */
export function congestionColor(u: number): string {
  if (u > 1.0) return "#e0463c";
  if (u >= 0.8) return "#e8873a";
  if (u >= 0.6) return "#e3c34a";
  return "#5fbf7f";
}

export function congestionBand(u: number): string {
  if (u > 1.0) return "OVER_CAPACITY";
  if (u >= 0.8) return "HIGH";
  if (u >= 0.6) return "ELEVATED";
  return "NORMAL";
}

export default function GlobeView(props: Props) {
  const host = useRef<HTMLDivElement | null>(null);
  const globeRef = useRef<GlobeInstance | null>(null);
  const lastPaint = useRef<{ elapsedMs: number; drawn: { countries: number; eez: number } } | null>(null);
  const layers = useLayerStore((s) => s.layers);
  const saveView = useLayerStore((s) => s.saveView);

  const on = (id: string) => layers[id]?.visible ?? false;
  const alpha = (id: string) => layers[id]?.opacity ?? 1;
  const zOf = (id: string) => (layers[id]?.zOrder ?? 0) / 4000;

  // ---- 地球儀の生成(一度だけ) -------------------------------------------
  useEffect(() => {
    if (!host.current || globeRef.current) return;
    const g = new Globe(host.current)
      .backgroundColor("#05101b")
      .showAtmosphere(true)
      .atmosphereColor("#6fa8c8")
      .atmosphereAltitude(0.14);
    const mat = g.globeMaterial() as THREE.MeshPhongMaterial;
    mat.color = new THREE.Color(OCEAN_COLOR);
    mat.emissive = new THREE.Color("#03121f");
    mat.emissiveIntensity = 0.35;
    mat.shininess = 4;
    g.pointOfView({ lat: 20, lng: 60, altitude: 2.4 }, 0);
    globeRef.current = g;

    const onResize = () => {
      if (!host.current) return;
      g.width(host.current.clientWidth).height(host.current.clientHeight);
    };
    onResize();
    window.addEventListener("resize", onResize);

    const controls = g.controls() as { addEventListener?: (t: string, f: () => void) => void };
    let timer: number | undefined;
    controls.addEventListener?.("end", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => saveView(g.pointOfView()), 400);
    });

    return () => {
      window.removeEventListener("resize", onResize);
    };
  }, [saveView]);

  // ---- 国 / EEZ は地球儀の表面テクスチャとして描く -----------------------
  // 3D ポリゴンで描くと実 GPU でも 884 ms/フレームになった(mapTexture.ts の注記)。
  const mapCanvas = useRef<HTMLCanvasElement | null>(null);
  const mapTexture = useRef<THREE.CanvasTexture | null>(null);

  useEffect(() => {
    const g = globeRef.current;
    if (!g) return;
    if (!mapCanvas.current) mapCanvas.current = document.createElement("canvas");
    const stats = paintMapTexture(
      mapCanvas.current,
      {
        countries: props.countries,
        eez: props.eez,
        showCountries: on("COUNTRY"),
        countryOpacity: alpha("COUNTRY"),
        showEez: on("EEZ"),
        eezOpacity: alpha("EEZ"),
        countryZ: layers["COUNTRY"]?.zOrder ?? 10,
        eezZ: layers["EEZ"]?.zOrder ?? 30,
      },
      {
        ocean: OCEAN_COLOR,
        countryFill: COUNTRY_CAP,
        countryStroke: COUNTRY_STROKE,
        eezFill: EEZ_COLOR,
        eezStroke: "#8fd0dd",
      },
    );
    lastPaint.current = stats;
    const mat = g.globeMaterial() as THREE.MeshPhongMaterial;
    if (!mapTexture.current) {
      mapTexture.current = makeTexture(mapCanvas.current);
      mat.map = mapTexture.current;
      mat.color = new THREE.Color("#ffffff");
    } else {
      mapTexture.current.needsUpdate = true;
    }
    mat.needsUpdate = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.countries, props.eez, layers]);

  // ---- 点(港 / 航行グリッド) -------------------------------------------
  const points = useMemo<PointDatum[]>(() => {
    const out: PointDatum[] = [];
    if (props.navGrid && props.navMask && on("NAV_GRID")) {
      const a = alpha("NAV_GRID");
      const { rows, cols, resolution_deg: res } = props.navGrid;
      for (let i = 0; i < rows; i += NAV_GRID_STRIDE) {
        for (let j = 0; j < cols; j += NAV_GRID_STRIDE) {
          if (!props.navMask[i * cols + j]) continue;
          out.push({
            __layer: "NAV_GRID",
            lat: 90 - (i + 0.5) * res,
            lng: -180 + (j + 0.5) * res,
            color: hexToRgba("#3d7f9c", a * 0.55),
            radius: 0.12,
            altitude: 0.001 + zOf("NAV_GRID") * 0.2,
            label: "",
          });
        }
      }
    }
    if (props.ports && on("PORT")) {
      const a = alpha("PORT");
      const byThroughput = on("PORT_THROUGHPUT");
      const congestionOn = on("PORT_CONGESTION");
      const maxOut = props.ports.features.reduce(
        (m, f) => Math.max(m, f.properties.outflows),
        1,
      );
      for (const f of props.ports.features) {
        const p = f.properties;
        const [lng, lat] = f.geometry.type === "Point"
          ? (f.geometry.coordinates as [number, number])
          : [0, 0];
        const u = props.congestionByPortId[p.port_id];
        const color = congestionOn && u !== undefined
          ? hexToRgba(congestionColor(u), a)
          : p.routable
            ? hexToRgba("#ffd166", a)
            : hexToRgba("#8b8b8b", a * 0.8);
        const radius = byThroughput
          ? 0.14 + 0.7 * Math.sqrt(p.outflows / maxOut)
          : 0.16;
        out.push({
          __layer: "PORT",
          lat,
          lng,
          color,
          radius,
          // 高さを持たせると点が「棒」に見える(2026-09-08 の目視で発見)。
          altitude: 0.002 + zOf("PORT") * 0.2,
          label: p.name,
          port: p,
        });
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.ports, props.navGrid, props.navMask, props.congestionByPortId, layers]);

  // ---- 線(航路 / 再ルート) ---------------------------------------------
  const paths = useMemo<PathDatum[]>(() => {
    const out: PathDatum[] = [];
    const rerouted = new Set(props.rerouted.map((r) => r.route_id));
    if (on("SHIPPING_ROUTE")) {
      const a = alpha("SHIPPING_ROUTE");
      for (const r of props.routes) {
        // 再ルートされた航路は、平常時の線を破線にして「もう通れない」を見せる
        const blocked = rerouted.has(r.route_id);
        for (const seg of r.display_segments) {
          out.push({
            __layer: "SHIPPING_ROUTE",
            coords: seg,
            color: blocked ? hexToRgba("#7a8f9e", a * 0.55) : hexToRgba("#66c2ff", a),
            stroke: blocked ? 0.4 : 0.55,
            dash: blocked ? 0.6 : 0,
            label: `${r.route_id}(${Math.round(r.distance_km).toLocaleString()} km)`,
          });
        }
      }
    }
    if (on("REROUTE")) {
      const a = alpha("REROUTE");
      for (const r of props.rerouted) {
        for (const seg of r.segments) {
          out.push({
            __layer: "REROUTE",
            coords: seg,
            color: hexToRgba("#ff9f5a", a),
            stroke: 0.85,
            dash: 0,
            label: `${r.route_id}(再ルート)`,
          });
        }
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.routes, props.rerouted, layers]);

  // ---- 環(チョークポイント) --------------------------------------------
  const rings = useMemo(() => {
    if (!on("CHOKEPOINT")) return [];
    const closed = new Set(props.closedChokepointIds);
    return props.chokepoints.map((c) => ({
      lat: c.lat,
      lng: c.lon,
      color: closed.has(c.id) ? "#e0463c" : "#ffd166",
      maxR: closed.has(c.id) ? 6 : 4,
      speed: closed.has(c.id) ? 3 : 1.2,
      choke: c,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.chokepoints, props.closedChokepointIds, layers]);

  // ---- 反映 ---------------------------------------------------------------
  useEffect(() => {
    const g = globeRef.current;
    if (!g) return;
    g.pointsData(points)
      .pointLat((d) => (d as PointDatum).lat)
      .pointLng((d) => (d as PointDatum).lng)
      .pointColor((d) => (d as PointDatum).color)
      .pointRadius((d) => (d as PointDatum).radius)
      .pointAltitude((d) => (d as PointDatum).altitude)
      .pointLabel((d) => (d as PointDatum).label)
      .onPointClick((d) => {
        const pt = d as PointDatum;
        props.onSelectPort(pt.port ?? null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points]);

  useEffect(() => {
    const g = globeRef.current;
    if (!g) return;
    g.pathsData(paths)
      .pathPoints((d) => (d as PathDatum).coords)
      .pathPointLat((p) => (p as [number, number])[1])
      .pathPointLng((p) => (p as [number, number])[0])
      .pathColor((d: object) => (d as PathDatum).color)
      .pathStroke((d) => (d as PathDatum).stroke)
      .pathDashLength((d) => ((d as PathDatum).dash > 0 ? 0.04 : 1))
      .pathDashGap((d) => (d as PathDatum).dash)
      .pathTransitionDuration(0)
      .pathLabel((d) => (d as PathDatum).label);
  }, [paths]);

  useEffect(() => {
    const g = globeRef.current;
    if (!g) return;
    g.ringsData(rings)
      .ringLat((d: object) => (d as { lat: number }).lat)
      .ringLng((d: object) => (d as { lng: number }).lng)
      .ringColor((d: object) => () => (d as { color: string }).color)
      .ringMaxRadius((d: object) => (d as { maxR: number }).maxR)
      .ringPropagationSpeed((d: object) => (d as { speed: number }).speed)
      .ringRepeatPeriod(900);
    g.labelsData(rings)
      .labelLat((d: object) => (d as { lat: number }).lat)
      .labelLng((d: object) => (d as { lng: number }).lng)
      .labelText((d: object) => (d as { choke: Chokepoint }).choke.name_ja)
      .labelSize(1.1)
      .labelDotRadius(0.35)
      .labelColor((d: object) => (d as { color: string }).color)
      .labelAltitude(0.02)
      .onLabelClick((d: object) => props.onSelectChokepoint((d as { choke: Chokepoint }).choke));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rings]);

  return <div className="globe-host" ref={host} data-testid="globe-host" />;
}
