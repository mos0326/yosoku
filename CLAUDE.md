# CLAUDE.md

このリポジトリで作業する Claude Code 向けのガイド。

## 概要

`yosoku` は日本株トレーディングセンサー。適時開示(TDnet)・ニュースを取り込み、
Claude(LLM)で「短期的に株価が上がりそうか」を判定し、Discord に通知する。

## 開発コマンド

```bash
pip install -e ".[dev]"   # 依存導入(本体 + pytest/ruff)
pytest -q                 # テスト(ネットワーク・APIキー不要)
ruff check .              # Lint
ruff check --fix .        # 自動修正
python -m yosoku sources  # 収集の疎通確認(APIキー不要)
```

Claude Code on the web では `.claude/hooks/session-start.sh` が依存を自動導入する
(有効化は `.claude/settings.json.example` を参照)。

## アーキテクチャ

```
sources/* → 一次フィルタ → 重複排除(store) → 分析(analyzer) → 通知判定(pipeline) → notifier
```

- `yosoku/sources/`  : データ取得。`Source` を実装し `fetch()`→`list[RawEvent]`。
  - `tdnet.py`  : TDnet 適時開示(Yanoshin WebAPI)。`parse_items` は純関数でテスト可。
  - `news_rss.py`: ニュース RSS(feedparser)。
  - `prices.py` : 証券コード→ティッカー変換 + 軽量現在値(`current_price`)+ yfinance 値動き。
  - `pts.py`    : PTS(夜間/寄り前)価格を株探から best-effort 取得。`parse_pts` は純関数。
  - `document.py`: 開示PDF本文の抽出(pypdf)。
- `yosoku/analyzer.py` : `TieredAnalyzer`。triage(安価) → deep(高性能+thinking) の二段階・非同期。
  `arbitrate` は通知直前の最終判定(上位モデルのセカンドオピニオン)。失敗時はフェイルオープン。
- `yosoku/pipeline.py` : 非同期オーケストレーション。`should_notify`/`passes_prefilter` は純関数。
  通知成功時に `_freeze_alert` でエントリー価格を `alerts` に凍結(答え合わせの起点)。
- `yosoku/store.py`    : SQLite。`seen`(重複)/`signals`(履歴)/`alerts`(エントリー凍結・不変)/`outcomes`(答え合わせ)。
- `yosoku/scoring.py`  : 答え合わせ。実通知の後刻リターンを採点し精度集計。
  `realized_return`/`is_anomalous`/`window_status`/`accuracy_report` は純関数。`price_fn` 差し替え可。
- `yosoku/notifier.py` : Discord Webhook。買い時/優先度/想定上昇率/PTS も埋め込む。
- `yosoku/metrics.py`  : トークン/コスト集計。
- `yosoku/backtest.py` : 過去開示で判定品質を評価。`summarize`/`score_bucket` は純関数。
- `yosoku/cli.py`      : `run` / `watch` / `sources` / `history` / `score` / `accuracy` / `backtest`。

## 設計上の約束

- **ネットワーク/外部I/Oと純粋ロジックを分離する**(例: `parse_items`, `summarize`,
  `should_notify`)。テストは純関数とモックで完結させ、ネットワーク・APIキー不要に保つ。
- **シークレットは環境変数のみ**(`ANTHROPIC_API_KEY`, `DISCORD_WEBHOOK_URL`)。
  YAML やコードに書かない。
- 失敗は握りつぶして継続(1件の取得/分析失敗で全体を止めない)。
- モデル ID は変更しない(既定: deep=`claude-opus-4-8`, triage=`claude-haiku-4-5`,
  arbiter=`claude-fable-5`)。arbiter の呼び出しに `thinking` パラメータを渡さない(常時ONのため400になる)。
- 変更後は `ruff check .` と `pytest -q` を通す。

## 拡張ポイント

- データソース追加: `sources/base.py` の `Source` を実装し `pipeline.build_sources()` へ。
  候補: J-Quants / EDINET / kabuステーションAPI。
- 通知先追加: `notify(signal)->bool` を持つクラスを作り `build_pipeline()` で差し込む。
