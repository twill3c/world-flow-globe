/**
 * 国と EEZ を**地球儀の表面テクスチャ**として描く。
 *
 * ## なぜ 3D ポリゴンをやめたか(2026-09-08 実測)
 *
 * globe.gl の `polygonsData` に 258 か国(頂点 63,362)を渡すと、
 * 球面に沿って天面と側面が細分され、**実 GPU(Intel Iris Xe)でも
 * 884 ms/フレーム = 1.1 fps** になった。曲率の刻みを 5 度から 24 度へ粗くしても
 * 改善はわずかで、地球儀は事実上固まったままだった。
 *
 * | 描き方 | ヘッドレス | 実 GPU |
 * |---|---|---|
 * | 3D ポリゴン(既定の曲率 5 度) | 4,565 ms/フレーム | 884 ms/フレーム |
 * | 3D ポリゴン(曲率 24 度) | 3,112 ms/フレーム | 884 ms/フレーム |
 * | 表面テクスチャ(この実装) | (下の実測を参照) | (同) |
 *
 * **この欠陥は pytest 94 件・vitest 43 件が全部緑のまま、出荷ビルドも通った状態で
 * 存在していた。** 実ブラウザで測るまで誰も気づかない類のものである(G-10)。
 *
 * 表面テクスチャなら描画は 1 枚のマップになり、描き直しはレイヤーを切り替えた
 * ときだけの一度きりになる。
 */

import * as THREE from "three";
import type { CountryProps, EezProps, FeatureCollection } from "../../types";

/** 経度 1 度あたり約 11.4 px。簡略化の許容度(0.05 度)より細かい。 */
export const TEXTURE_WIDTH = 4096;
export const TEXTURE_HEIGHT = 2048;

export interface MapStyle {
  ocean: string;
  countryFill: string;
  countryStroke: string;
  eezFill: Record<string, string>;
  eezStroke: string;
}

export interface MapLayerState {
  countries: FeatureCollection<CountryProps> | null;
  eez: FeatureCollection<EezProps> | null;
  showCountries: boolean;
  countryOpacity: number;
  showEez: boolean;
  eezOpacity: number;
  /** 小さいほど先に(下に)描く。レイヤー管理の重ね順スライダーがここに入る。 */
  countryZ: number;
  eezZ: number;
}

function project(lon: number, lat: number): [number, number] {
  return [((lon + 180) / 360) * TEXTURE_WIDTH, ((90 - lat) / 180) * TEXTURE_HEIGHT];
}

type Ring = number[][];

function drawRings(ctx: CanvasRenderingContext2D, rings: Ring[]): void {
  ctx.beginPath();
  for (const ring of rings) {
    for (let k = 0; k < ring.length; k++) {
      const pt = ring[k]!;
      const [x, y] = project(pt[0]!, pt[1]!);
      if (k === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
}

function drawGeometry(
  ctx: CanvasRenderingContext2D,
  geometry: { type: string; coordinates: unknown },
  fill: string,
  stroke: string,
  lineWidth: number,
): void {
  const polys: Ring[][] =
    geometry.type === "Polygon"
      ? [geometry.coordinates as Ring[]]
      : geometry.type === "MultiPolygon"
        ? (geometry.coordinates as Ring[][])
        : [];
  for (const rings of polys) {
    drawRings(ctx, rings);
    ctx.fillStyle = fill;
    // 穴(内側のリング)を正しく抜くため evenodd で塗る
    ctx.fill("evenodd");
    if (lineWidth > 0) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = lineWidth;
      ctx.stroke();
    }
  }
}

/**
 * 表面テクスチャを描く。**描き直しはレイヤーの切り替え時だけ**である。
 *
 * 戻り値の `elapsedMs` は、この描画にかかった実時間。画面の応答(N-04)を
 * 測るときの根拠になるので、捨てずに返す。
 */
export function paintMapTexture(
  canvas: HTMLCanvasElement,
  state: MapLayerState,
  style: MapStyle,
): { elapsedMs: number; drawn: { countries: number; eez: number } } {
  const t0 = performance.now();
  canvas.width = TEXTURE_WIDTH;
  canvas.height = TEXTURE_HEIGHT;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D コンテキストが取れない");

  ctx.fillStyle = style.ocean;
  ctx.fillRect(0, 0, TEXTURE_WIDTH, TEXTURE_HEIGHT);
  ctx.lineJoin = "round";

  const drawn = { countries: 0, eez: 0 };

  const paintEez = () => {
    if (!state.showEez || !state.eez) return;
    ctx.globalAlpha = state.eezOpacity;
    for (const f of state.eez.features) {
      const fill = style.eezFill[f.properties.pol_type] ?? "#4a7f8f";
      drawGeometry(ctx, f.geometry as never, fill, style.eezStroke, 1);
      drawn.eez++;
    }
    ctx.globalAlpha = 1;
  };

  const paintCountries = () => {
    if (!state.showCountries || !state.countries) return;
    ctx.globalAlpha = state.countryOpacity;
    for (const f of state.countries.features) {
      drawGeometry(ctx, f.geometry as never, style.countryFill, style.countryStroke, 1.5);
      drawn.countries++;
    }
    ctx.globalAlpha = 1;
  };

  // 重ね順は「小さいほど先に(下に)」。同値なら EEZ を下にする。
  if (state.eezZ <= state.countryZ) {
    paintEez();
    paintCountries();
  } else {
    paintCountries();
    paintEez();
  }

  return { elapsedMs: performance.now() - t0, drawn };
}

export function makeTexture(canvas: HTMLCanvasElement): THREE.CanvasTexture {
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}
