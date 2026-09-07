/**
 * 1 フレームの時間を実測する(SPEC.md N-04 / G-22 / HC-210)。
 *
 *   node scripts/measure_fps.mjs [url]
 *
 * ## ヘッドレスの数字を閾値に使ってはならない
 *
 * Playwright の既定のヘッドレスは SwiftShader(ソフトウェア描画)なので、
 * 実 GPU とはまったく別の数が出る。しかも**その比は一定ではない**
 * (2026-09-08 実測: 修正前 4,565 / 884 = 5.2 倍、修正後 357 / 16.6 = 21.5 倍)。
 * したがって「ヘッドレスで N ミリ秒以下」という閾値は意味を持たない。
 * **判定は実 GPU の値で行う。** ヘッドレスの値は参考として並べるだけにする。
 *
 * ## どの層が費用を出しているかまで測る
 *
 * 「遅い」だけでは直せない。レイヤーを消したときの値を対照として取り、
 * 費用の出どころを特定できる形で残す。
 */

import { chromium } from "playwright";

const URL_ARG = process.argv[2] ?? "http://localhost:4173/";
const FRAMES = 20;

/** 実 GPU での判定。60 fps の 2 フレーム分(33.3 ms)を上限にする。 */
const MAX_FRAME_MS_GPU = 34;

const SAMPLE_SCRIPT = `new Promise((done) => {
  const a = []; let last = performance.now(); let n = 0;
  const tick = () => {
    const now = performance.now(); a.push(now - last); last = now;
    if (++n < ${FRAMES}) requestAnimationFrame(tick); else done(a);
  };
  requestAnimationFrame(tick);
})`;

async function sample(page) {
  const t = await page.evaluate(SAMPLE_SCRIPT);
  const mean = t.reduce((x, c) => x + c, 0) / t.length;
  return { mean, max: Math.max(...t) };
}

async function withLayers(page, map) {
  await page.evaluate((m) => {
    const key = "world-flow-globe:view:v1";
    const raw = JSON.parse(localStorage.getItem(key) ?? '{"layers":{}}');
    for (const [k, v] of Object.entries(m)) {
      raw.layers[k] = { ...(raw.layers[k] ?? { opacity: 0.5, zOrder: 10 }), visible: v };
    }
    localStorage.setItem(key, JSON.stringify(raw));
  }, map);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector(".layer-row[data-layer-id='COUNTRY']", { timeout: 60_000 });
  await page.waitForTimeout(3500);
}

const GPU_ARGS = ["--enable-gpu", "--ignore-gpu-blocklist", "--use-angle=default"];

async function run(useGpu) {
  // ヘッドレスのままでも GPU のフラグを渡せば実 GPU が使える(2026-09-08 実測)。
  // 窓を出す必要は無い。
  const browser = await chromium.launch({ headless: true, args: useGpu ? GPU_ARGS : [] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(URL_ARG, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForSelector(".layer-row[data-layer-id='COUNTRY']", { timeout: 60_000 });
  await page.waitForTimeout(3500);

  const renderer = await page.evaluate(() => {
    const c = document.querySelector(".globe-host canvas");
    const gl = c?.getContext("webgl2") ?? c?.getContext("webgl");
    const dbg = gl?.getExtension("WEBGL_debug_renderer_info");
    return dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : "(不明)";
  });

  const base = await sample(page);
  // 対照: 重い層を消したときの値。費用の出どころを特定できるようにする。
  await withLayers(page, { SHIPPING_ROUTE: false, PORT: false, CHOKEPOINT: false });
  const bare = await sample(page);
  await withLayers(page, { SHIPPING_ROUTE: true, PORT: true, CHOKEPOINT: true });

  await browser.close();
  return { renderer, base, bare };
}

const gpu = await run(true);
const soft = await run(false);

console.log(`実 GPU        : ${gpu.base.mean.toFixed(1)} ms/frame(最大 ${gpu.base.max.toFixed(1)})`);
console.log(`  └ 対照(層を消す): ${gpu.bare.mean.toFixed(1)} ms/frame`);
console.log(`  renderer: ${gpu.renderer}`);
console.log(`ソフトウェア描画: ${soft.base.mean.toFixed(1)} ms/frame(参考。閾値には使わない)`);
console.log(`  比 = ${(soft.base.mean / gpu.base.mean).toFixed(1)} 倍`);

if (gpu.base.mean > MAX_FRAME_MS_GPU) {
  console.error(
    `\n落第: 実 GPU で ${gpu.base.mean.toFixed(1)} ms/frame(上限 ${MAX_FRAME_MS_GPU} ms)`,
  );
  process.exit(1);
}
console.log("\n描画性能: 問題なし");
