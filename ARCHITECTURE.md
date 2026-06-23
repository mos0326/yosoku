# アーキテクチャ設計図（マルチエージェント基盤）

将来的に「多数の専門エージェントが並列で動く大規模システム」へ拡張するための土台。
**部門ごとに完全独立したモジュール（ファイル）**にし、**メッセージバス**で疎結合に繋ぐ。

> 現行の本番(リアルタイム通知)は `pipeline.py` + `watch` がそのまま担当（壊していない）。
> 本ドキュメントの `agents/` は、その上位構造を整理した**次世代の実行系**（実験的）。

## 全体像（データの流れ）

```
┌─────────────┐  events.raw     ┌──────────┐  signals.analyzed  ┌──────────┐
│ Collector   │ ───────────────▶│ Analyst  │ ──────────────────▶│ Risk     │
│ 収集部門     │                 │ 分析部門  │                    │ リスク管理 │
└─────────────┘                 └──────────┘                    └────┬─────┘
   sources/* を束ねる            Claude(TieredAnalyzer)          重複/閾値/上限   │ signals.approved
                                                                        ▼
┌─────────────┐  predictions                                    ┌──────────┐
│ Predictor   │ ┄┄┄┄┄┄┄┄┄┄┄▶ (将来: 分析/通知へ)               │ Notifier │
│ 予測部門     │  ※投機的・スタブ                                │ 通知部門  │
└─────────────┘                                                 └──────────┘
                                                                    Discord
```

中央の `Bus`（`agents/bus.py`）が全部門を仲介。**部門同士は互いを直接知らない**。
トピックを購読（subscribe）/発行（publish）するだけで繋がる。

## 部門（= 独立した Agent）

| ファイル | 部門 | 入力(購読) | 出力(発行) | 役割 |
|---|---|---|---|---|
| `agents/collector.py` | 収集 | — | `events.raw` | データソースを定期取得し新規イベントを流す |
| `agents/analyst.py`   | 分析 | `events.raw` | `signals.analyzed` | Claude で「上がりそうか」を判定 |
| `agents/risk.py`      | リスク管理 | `signals.analyzed` | `signals.approved` | 重複排除・通知閾値・1日上限（過剰通知の抑制） |
| `agents/notifier.py`  | 通知 | `signals.approved` | — | Discord 等へ送信 |
| `agents/predictor.py` | 予測(投機的) | — | `predictions` | カタリスト前の有望候補をスキャン（スタブ） |
| `agents/orchestrator.py` | 司令塔 | — | — | 全部門を生成・Busで接続・並行起動 |

## 拡張のしかた（エージェントを増やす）

1. `agents/` に新しいファイルを作り、`Agent` を継承する。
2. `run()` で必要なトピックを `self.bus.subscribe(...)` し、結果を `self.bus.publish(...)`。
3. `orchestrator.build_agents()` の一覧に追加する。

これだけで「N個の専門エージェントが並列で協調する」構成へスケールできる。
例: `SentimentAgent`(SNS解析) / `TechnicalAgent`(テクニカル) / `PortfolioAgent`(建玉管理) /
`BrokerAgent`(発注API) / `AuditAgent`(記録監査) など。

## 設計の約束

- **疎結合**: 部門は Bus 経由でのみ通信。直接呼び出さない。
- **再利用**: 既存の `sources/`・`analyzer`・`notifier`・`store` をラップして使う。
- **失敗の隔離**: 各部門は例外を握りつぶしてループ継続（1部門の失敗が全体を止めない）。
- **テスト可能**: Bus は単体テスト可能。判定ロジック(`RiskAgent.evaluate` 等)は純関数的に分離。

## 実行

```bash
python -m yosoku agents            # マルチエージェント版(実験的)
python -m yosoku agents --dry-run  # 通知せず
# 本番(安定版)は従来どおり:
python -m yosoku watch
```

## 注意（リアルタイム性の限界）

公開された適時開示は「出た瞬間に全員に公開」される。通知が1〜2分後でも、
**価格はもう動き始めた後**。先回り（上がる前に買う）は構造上できない（非公開情報＝違法）。
本システムの価値は「**初動を最速で捉える**」こと。`predictor` は“出る前”を狙う投機的補助で、
当たる保証はない。
