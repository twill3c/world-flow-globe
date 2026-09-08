# WORLD FLOW GLOBE

世界の港・海洋境界・**推定**航路・チョークポイントを 3D 地球儀に重ね、
「スエズを 7 日閉じたら何が起きるか」をブラウザの中だけで決定論的に計算する。

これは「世界のデータを地球儀に載せるアプリ」ではない。
**世界の物流ネットワークを公開データから再構成し、What-if を回せる地球規模のモデル**である。

---

## この地球儀の読み方

画面に出ている線・点・数には、すべて**出所の階級**が付いている。

| 表示 | 意味 | 例 |
|---|---|---|
| 観測 `OBSERVED` | 実観測・公的地理データ | 国境・EEZ・港の位置 |
| 統計 `STATISTICAL` | 統計的集計 | 港の `outflows` |
| 推定 `INFERRED` | 複数データからの推定 | **航路**(実船の航跡ではない) |
| 仮定計算 `ESTIMATED` | 数式・仮定による推定 | 所要日数(16 ノット仮定)・極域の航行制限 |
| 模擬 `SIMULATED` | What-if 計算 | 閉鎖時の再ルート・混雑 |
| 予測 `PREDICTED` | ML モデル出力 | **本バージョンでは出さない** |

**航路は実船の航跡ではない。** 港・航行可能海域・チョークポイントから
このアプリが引いた線である。実船はここを通っていない。

**値が無い欄は「—」と出す。** 0 として描かない。

---

## 何ができるか

- 国 258・EEZ 284(200NM / 重複主張 / 共同管轄を**色で区別**)・港 837 を地球儀に重ねる
- 主要 26 港のあいだの**推定航路 324 本**を引く
- チョークポイント 5 か所(スエズ・パナマ・マラッカ・ホルムズ・バブ・エル・マンデブ)を
  **1〜90 日 × 容量減 0〜100%** で閉じ、影響を受けた航路を**その場で引き直す**
- 追加距離・追加日数・港の混雑・レジリエンスを出す
- レイヤーごとに ON/OFF・濃さ・重ね順・プリセット・検索・排他表示

### 閉鎖して何が起きるか(実測)

| 経路 | 平常時 | 閉鎖時 |
|---|---|---|
| シンガポール→ロッテルダム | 16,118 km | **22,596 km**(スエズ閉鎖・喜望峰) |
| シンガポール→ロッテルダム | 16,118 km | 17,626 km(マラッカ閉鎖・スンダ/ロンボク) |
| ロサンゼルス→ロッテルダム | 14,976 km | **26,109 km**(パナマ閉鎖) |
| ジェベル・アリ→ロッテルダム | 12,083 km | **到達不能**(ホルムズ閉鎖でペルシア湾が袋になる) |

スエズを 100% 閉じると、324 本のうち **78 本**が再ルートになり、
追加距離の合計は **498,936 km**、追加日数の平均は **8.99 日**(2026-09-08 実測)。

---

## 作りながら測って分かったこと

### 海氷が無いと、スエズを閉じた船は北極点の上を通る

制限を置かずに探索したところ、スエズ閉鎖時の最短路が**北極海に入った**
(626 セル中 408 セルが 70°N 以北・最北 89.75°N)。しかもマラッカ閉鎖でも
**まったく同じ経路**になり、二つの閉鎖が区別できなくなっていた。

そこで極域の航行制限(北 70 度 / 南 -60 度)を**仮定として**置いた。
仮定なので `ESTIMATED` と名乗らせ、**画面から外せるようにしてある**。
外したときに何が起きるかはテストが対照として固定している。

### 素朴な格子はパナマ地峡に穴を開ける

セル中心が陸かどうかで航行可能を決めると、0.5 度ではパナマ地峡の両側が
267 km で繋がってしまい、**運河を閉じても船が無料で太平洋へ抜ける**。
セルを 5×5 に細分して海の割合で決めるとこれが塞がる。代償で塞がる
実在の海峡(ジブラルタル・ダーダネルス等)は、**名前と理由つきの通路**として
明示的に開けている。

### テストが全部緑のまま、画面が 1.1 fps だった

258 か国を 3D ポリゴンで描いていたため、実 GPU でも 884 ms/フレームだった。
地図を地球儀の**表面テクスチャ**に描き直して 16.6 ms(60 fps)—— 53 倍。
pytest 115 件も vitest 43 件も出荷ビルドも、この間ずっと緑だった。

### 出荷データに JSON の規格外の `NaN` が入っていた

Python の `json` は `NaN` を読み書きできるが、**ブラウザは構文エラーで落ちる**。
`eez.geojson` に 33 個入っていて、EEZ レイヤーを点けるとアプリが落ちていた。
出荷 JSON 全件を**厳格な JSON として**読み直す検査を置いた。

詳しくは [SPEC.md](SPEC.md) と [`logs/loops/`](logs/loops/)。

---

## 出していないもの(理由つき)

| 項目 | 理由 |
|---|---|
| 領海(12NM) | 配布が氏名・所属・メールの入力を要するフォームで機械取得できない。海岸線からの緩衝で代替すると `OBSERVED` を名乗れなくなるので**代替しない** |
| 海運密度 | 458 MB のラスタで本バージョンの予算に収まらない |
| 貿易フロー(Comtrade) | 国と国の貿易額は、港と港の輸送量とは**別の情報**である。片方から他方を作ると推定を統計に見せかけることになる |
| 定期船接続性(UNCTAD) | 取得経路が未確立(HEAD が 403) |
| AI による混雑予測 | 学習に使える実測が無い。自分のシミュレータの出力で学習した予測器は**シミュレータの再現以上のことを言わない**(循環)。代わりに決定論的な感度分析を出す |

画面上でも、これらは**消さずに並べて理由を出している**。
「無い」のか「手を抜いた」のかが分かるように。

---

## 動かす

```bash
# Python ETL(必ずプロジェクト専用 venv へ)
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements-etl.txt

./.venv/Scripts/python -m etl.download            # 出典の取得・マニフェスト生成
./.venv/Scripts/python -m etl.transform.countries # 国
./.venv/Scripts/python -m etl.transform.eez       # EEZ
./.venv/Scripts/python -m etl.transform.nav_grid  # 航行グリッド
./.venv/Scripts/python -m etl.transform.chokepoints
./.venv/Scripts/python -m etl.transform.ports
./.venv/Scripts/python -m etl.transform.routes
./.venv/Scripts/python -m etl.build.layers
./.venv/Scripts/python scripts/build_golden_routes.py   # 二実装照合の golden

# 画面
npm install
npm run dev
```

### 検査

```bash
./.venv/Scripts/python -m pytest        # 115 件(ETL・データ・経路)
npm test                                # 43 件(TypeScript 側・二実装照合)
./.venv/Scripts/python harness/text_hygiene.py

npm run build
node scripts/check_shipping.mjs         # 配信ホストの許可表・サイズ
npx vite preview --port 4173 &
node scripts/inspect_browser.mjs        # 実ブラウザ検品(4 画面幅)
node scripts/measure_fps.mjs            # 描画性能(実 GPU)
```

---

## データ源

| ID | 出典 | ライセンス |
|---|---|---|
| SRC-001 | Natural Earth Admin 0 Countries 10m v5.1.1 | Public Domain |
| SRC-002 | Marine Regions World EEZ v12 | CC BY 4.0 |
| SRC-003 | Marine Regions World 12NM v4 | CC BY 4.0(**未取得**) |
| SRC-004 | World Bank Global International Ports | CC BY 4.0 |
| SRC-005 | World Bank Global Shipping Traffic Density | CC BY 4.0(**未取得**) |
| SRC-006 | UNCTADstat | CC BY 3.0 IGO(**未取得**) |
| SRC-007 | UN Comtrade | UN Comtrade 利用条件(**未取得**) |

正本は [`public/data/source_registry.json`](public/data/source_registry.json)
(`etl/download/sources.py` から生成)。チョークポイント 5 か所は本リポジトリの台帳で、
**座標は表示用の参照点であって法的境界ではない**。

## 文書

- [SPEC.md](SPEC.md) — 何を作るか・原典との差分・品質ゲート G-01〜G-24
- [TEST_SPEC.md](TEST_SPEC.md) — 検査ケースとオラクルの出所
- [AGENTS.md](AGENTS.md) — 開発規律
- [docs/ORIGIN.md](docs/ORIGIN.md) — 原典の所在と節番号の対応

## ライセンス

MIT License © 2026 坂田哲朗。データは各出典のライセンスに従う。
