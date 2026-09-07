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
| `OBSERVED` | 実観測・公的地理データ | 国境・EEZ・港の位置 |
| `STATISTICAL` | 統計的集計 | 港の `outflows` |
| `INFERRED` | 複数データからの推定 | **航路**(実船の航跡ではない) |
| `ESTIMATED` | 数式・仮定による推定 | 所要日数(16 ノット仮定) |
| `SIMULATED` | What-if 計算 | 閉鎖時の再ルート・混雑 |
| `PREDICTED` | ML モデル出力 | **本バージョンでは出さない** |

**航路は実船の航跡ではない。** 港・航行可能海域・チョークポイントから
こちらが引いた線である。実船はここを通っていない。

**値が無い欄は「—」と出す。** 0 として描かない。

## 出していないもの(理由つき)

| 項目 | 理由 |
|---|---|
| 領海(12NM) | 配布が氏名・所属・メールの入力を要するフォームで機械取得できない。海岸線からの緩衝で代替すると `OBSERVED` を名乗れなくなるので**代替しない** |
| 海運密度 | 458 MB のラスタで本バージョンの予算に収まらない |
| AI による混雑予測 | 学習に使える実測が無い。自分のシミュレータの出力で学習した予測器は**シミュレータの再現以上のことを言わない**(循環)。代わりに決定論的な感度分析を出す |

詳細は [SPEC.md](SPEC.md) §9「原典との差分」。

---

## 開発

```bash
# Python ETL(必ずプロジェクト専用 venv へ — HC-171)
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements-etl.txt

./.venv/Scripts/python -m etl.download      # 出典の取得・マニフェスト生成
./.venv/Scripts/python -m pytest            # 検査
```

## データ源

| ID | 出典 | ライセンス |
|---|---|---|
| SRC-001 | Natural Earth Admin 0 Countries 10m v5.1.1 | Public Domain |
| SRC-002 | Marine Regions World EEZ v12 | CC BY 4.0 |
| SRC-003 | Marine Regions World 12NM v4 | CC BY 4.0(未取得) |
| SRC-004 | World Bank Global International Ports | CC BY 4.0 |
| SRC-005 | World Bank Global Shipping Traffic Density | CC BY 4.0(未取得) |
| SRC-006 | UNCTADstat | CC BY 3.0 IGO(未取得) |
| SRC-007 | UN Comtrade | UN Comtrade 利用条件(未取得) |

正本は `public/data/source_registry.json`(`etl/download/sources.py` から生成)。

## 文書

- [SPEC.md](SPEC.md) — 何を作るか・原典との差分・品質ゲート
- [TEST_SPEC.md](TEST_SPEC.md) — 検査ケースとオラクルの出所
- [AGENTS.md](AGENTS.md) — 開発規律
- [docs/ORIGIN.md](docs/ORIGIN.md) — 原典の所在と節番号の対応

## ライセンス

MIT License © 2026 坂田哲朗。データは各出典のライセンスに従う。
