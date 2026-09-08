/**
 * 出荷物の検査(SPEC.md N-01・N-02・N-03 / G-11)。
 *
 *   node scripts/check_shipping.mjs
 *
 * 見るのは**手元でビルドした木ではなく `dist/`**、つまり実際に配られるものである
 * (HC-062: 手元でビルドが通ることと、配られる木でビルドが通ることは別)。
 *
 * ## 外部ホストの検査は「許可表」で行う
 *
 * 「fetch の宛先が外部かどうか」を静的に判定するのは難しい。代わりに
 * **配信物に現れるホスト名を全部数え、理由つきの許可表と突き合わせる**。
 * 新しいホストが増えたら落ちる。減る分には落とさない(依存の版で消えることがある)。
 *
 * 許可表に載っているものの多くは three.js のシェーダ出典表示などの
 * **文字列であって、取りに行く先ではない**。それでも表に載せるのは、
 * 「知らないホストが増えたこと」を検出できるようにするためである。
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const DIST = join(ROOT, "dist");

/** 配信物に現れてよいホストと、その理由。 */
const ALLOWED_HOSTS = {
  "github.com": "フッタのリンク先(規約 5 項目)",
  "app-menu-amber.vercel.app": "フッタの App Menu",
  "claude.ai": "フッタの解説アーティファクト 2 本",
  "react.dev": "React が例外メッセージに埋め込む説明ページ(取りに行かない)",
  "www.w3.org": "SVG / XML の名前空間 URI(取りに行かない)",
  "www.shadertoy.com": "three.js のシェーダの出典表示(コメント文字列)",
  "www.ellak.gr": "three.js のシェーダの出典表示(コメント文字列)",
  "www.magenta.gr": "three.js のシェーダの出典表示(コメント文字列)",
  "creativecommons.org": "出典のライセンス表示",
  "zenodo.org": "出典ページのリンク",
  "unctadstat.unctad.org": "出典ページのリンク",
  "uncomtrade.org": "出典ページのリンク",
  "datacatalog.worldbank.org": "出典ページのリンク",
  "datacatalogfiles.worldbank.org": "ETL が使う取得先(画面からは呼ばない)",
  "www.marineregions.org": "出典ページのリンク",
  "www.naturalearthdata.com": "出典ページのリンク",
  "naciscdn.org": "ETL が使う取得先(画面からは呼ばない)",
};

const MAX_TOTAL_DATA_MB = 30;
const MAX_FILE_MB = 80;

const problems = [];
const notes = [];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

// --- 1. dist が存在し、データが入っていること ------------------------------
let files;
try {
  files = walk(DIST);
} catch {
  console.error("dist/ がありません。先に `npm run build` を走らせてください。");
  process.exit(1);
}

const dataFiles = files.filter((f) => f.includes(`${"data"}${process.platform === "win32" ? "\\" : "/"}`));
const REQUIRED = [
  "layers.json",
  "source_registry.json",
  "countries.geojson",
  "eez.geojson",
  "ports.geojson",
  "chokepoints.json",
  "nav_grid.json",
  "shipping_routes.json",
];
for (const need of REQUIRED) {
  if (!dataFiles.some((f) => f.endsWith(need))) problems.push(`配信物に ${need} が無い`);
}
notes.push(`data ファイル ${dataFiles.length} 件`);

// --- 2. サイズ(N-03 / G-02) ----------------------------------------------
let total = 0;
for (const f of dataFiles) {
  const mb = statSync(f).size / 1024 / 1024;
  total += mb;
  if (mb >= MAX_FILE_MB) problems.push(`${f} が ${mb.toFixed(1)} MB(単一ファイル上限 ${MAX_FILE_MB} MB)`);
}
if (total >= MAX_TOTAL_DATA_MB) problems.push(`data 合計 ${total.toFixed(2)} MB(上限 ${MAX_TOTAL_DATA_MB} MB)`);
notes.push(`data 合計 ${total.toFixed(2)} MB`);

// --- 3. ホストの許可表(N-01 / G-11) --------------------------------------
const codeFiles = files.filter((f) => f.endsWith(".js") || f.endsWith(".css") || f.endsWith(".html"));
if (codeFiles.length === 0) problems.push("走査対象の JS/CSS が無い(この検査が働いていない)");
const found = new Map();
for (const f of codeFiles) {
  const text = readFileSync(f, "utf8");
  for (const m of text.matchAll(/https?:\/\/([a-zA-Z0-9._-]+)/g)) {
    const host = m[1];
    found.set(host, (found.get(host) ?? 0) + 1);
  }
}
const unknown = [...found.keys()].filter((h) => !(h in ALLOWED_HOSTS));
if (unknown.length > 0) {
  problems.push(`許可表に無いホストが配信物に現れた: ${unknown.join(", ")}`);
}
notes.push(`配信物に現れるホスト ${found.size} 種(許可表 ${Object.keys(ALLOWED_HOSTS).length} 種)`);

// --- 4. サーバー側の入口が無いこと(N-02) ---------------------------------
for (const forbidden of ["api", "middleware.js", "middleware.ts"]) {
  if (files.some((f) => f.includes(`${"dist"}${process.platform === "win32" ? "\\" : "/"}${forbidden}`))) {
    problems.push(`配信物に ${forbidden} がある(課金経路ゼロの前提が崩れる)`);
  }
}

// --- 5. 陽性対照: 検査が実際に撃てること ----------------------------------
{
  const probe = "https://example.invalid/x";
  const host = probe.match(/https?:\/\/([a-zA-Z0-9._-]+)/)[1];
  if (host in ALLOWED_HOSTS) problems.push("陽性対照が壊れている(example.invalid が許可表にある)");
}

for (const n of notes) console.log(`  ${n}`);
if (problems.length > 0) {
  console.error("\n出荷物の検査に落ちました:");
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log("出荷物の検査: 問題なし");
