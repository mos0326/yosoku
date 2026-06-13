# yosoku — 日本株トレーディングセンサー

上場企業の**適時開示（決算・業績修正・配当・自社株買い 等）**やニュースを
リアルタイムに取り込み、**Claude（LLM）**が「短期的に株価を押し上げる材料か」を
判定し、シグナルを **Discord** に通知する高性能センサーです。

> ⚠️ **免責**: 本ソフトウェアは情報提供・技術検証を目的としたもので、投資助言では
> ありません。LLM の判定は誤りを含みます。実際の売買は自己責任で行ってください。

## ハイライト

- ⚡ **非同期・並列**: 複数ソースを並行取得し、分析はセマフォで並列実行。
- 🧠 **二段階分析**: 安価モデル（haiku）で**全件トリアージ** → 有望なものだけ
  高性能モデル（opus 4.8）＋ **adaptive thinking** で精査。精度とコストを両立。
- 📄 **開示本文を解析**: TDnet の開示PDFを抽出し、見出しだけでなく**実数値**で判断。
- 💰 **コスト可観測**: 実行ごとにトークン・概算コストを集計。
- 🗃 **履歴 & バックテスト**: 全シグナルを保存。過去レンジで**判定品質を検証**できる。

---

## 仕組み

```
        ┌─────────── 収集(並列) ───────────┐
TDnet ──┤ TdnetSource (Yanoshin API)        │
News  ──┤ NewsRssSource (RSS)               │
        └───────────────┬───────────────────┘
                        ▼
              一次キーワードフィルタ            ← LLM前にコスト削減(任意)
                        ▼
                重複排除 (SQLite)               ← 再通知を防ぐ
                        ▼
        ┌── 分析: TieredAnalyzer (非同期/並列) ──┐
        │  triage  : haiku で全候補を粗くスコア   │
        │   └─昇格→ deep: opus 4.8 + thinking     │
        │            + 開示PDF本文 + 直近値動き    │
        └───────────────┬───────────────────────┘
                        ▼
              通知判定 (score/confidence)
                        ▼
          Discord 通知 + シグナル履歴(SQLite)
```

判定結果（`Analysis`）は構造化出力で次の形に拘束されます:

| フィールド | 内容 |
|---|---|
| `is_relevant` | 特定1銘柄の売買シグナルとして意味があるか |
| `ticker` | Yahoo Finance ティッカー（例 `7203.T`） |
| `direction` | `bullish` / `bearish` / `neutral` |
| `score` | -100〜+100（上昇期待の強さ） |
| `confidence` | 0〜100（確信度） |
| `horizon` | `intraday` / `days` / `weeks` |
| `rationale`, `key_factors` | 日本語の理由と材料 |

`direction == bullish` かつ `score`・`confidence` がしきい値以上のときに通知します。

---

## データソースと注意点

| ソース | 取得元 | 備考 |
|---|---|---|
| 適時開示 | [Yanoshin TDnet WebAPI](https://webapi.yanoshin.jp/)（無料） | **主シグナル**。各開示に証券コードが付く。準リアルタイム。PDF本文も精査で取得。 |
| ニュース | RSS（既定: NHK 経済） | 補助。銘柄特定は Claude に委ねる。フィードは差し替え可。 |
| 株価文脈 | yfinance（無料・遅延あり） | 直近の値動きを精査プロンプトに添える。取得失敗してもスキップ。 |

> **リアルタイム価格について**: 真のリアルタイム板情報は証券口座必須・有料が中心です。
> 本システムは「決算・開示・ニュースという“材料”の検知」を主眼にし、価格は yfinance の
> 準リアルタイム値で文脈付けします。本格運用では J-Quants / kabuステーションAPI 等への
> 差し替えを想定（`yosoku/sources/` に新ソースを追加するだけ）。

> **ネットワーク**: 実行環境の egress ポリシーによっては一部フィード/PDFがブロックされます。
> その場合は WARNING ログを出して継続します（致命的でない）。到達可能な経路に差し替えてください。

---

## セットアップ

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"     # もしくは: pip install -r requirements.txt
```

シークレットは環境変数で渡します（`.env.example` 参照）:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy
# 任意: モデル変更
# export YOSOKU_MODEL=claude-opus-4-8         # 精査モデル
# export YOSOKU_TRIAGE_MODEL=claude-haiku-4-5 # トリアージモデル
```

設定は任意で YAML（`config.example.yaml` をコピー）:

```bash
cp config.example.yaml config.yaml
```

---

## 使い方

```bash
# 収集だけ試す（APIキー不要・疎通確認）
python -m yosoku sources --limit 10

# 1回だけ実行。通知せずシグナル＋コストを表示（APIキーのみ必要）
python -m yosoku --config config.yaml run --dry-run

# 1回実行して Discord に通知
python -m yosoku --config config.yaml run

# 常駐してポーリング（既定300秒間隔）
python -m yosoku --config config.yaml watch --interval 300

# 過去シグナルの一覧
python -m yosoku history --limit 20

# バックテスト: 過去レンジを分析し、その後のリターンで判定品質を評価
python -m yosoku backtest 20260601 20260607 --horizon 5
```

`-v` でデバッグログ。`yosoku` コマンドとしてもインストールされます。

---

## 二段階分析とコスト

開示は1日数百件出ます。全件を高性能モデルに通すと高コストなので:

1. **triage**（既定 `claude-haiku-4-5`, 安価）で全候補を粗くスコアリング。
2. `is_relevant` かつ `triage_escalate_score`(既定30) 以上のものだけ
   **deep**（既定 `claude-opus-4-8` + adaptive thinking + 開示本文）で精査。

さらに `relevance_keywords`（既定 ON）で LLM 前に明らかな非材料を落とします。
実行ごとに `usage:` 行で各モデルのトークンと概算コスト($)を表示します。

| 調整 | 効果 |
|---|---|
| `relevance_keywords: []` | 一次フィルタ無効＝より純粋に LLM 判定（コスト増） |
| `two_stage: false` | 全件を deep モデルで分析（高コスト・最精度） |
| `YOSOKU_MODEL=claude-sonnet-4-6` | 精査モデルを安価側に |
| `deep_thinking: false` | 拡張思考オフで高速・低コスト |
| `enable_web_context: true` | Web検索で文脈強化（実験的・コスト増） |

---

## 設定

主な項目（全項目は `config.example.yaml`）。`analysis` 配下に二段階・並列・本文抽出の
ノブが揃っています（`two_stage` / `triage_model` / `concurrency` / `fetch_document` 等）。

### コスト目安

`claude-opus-4-8` は $5/$25 per 1M tok、`claude-haiku-4-5` は $1/$5 per 1M tok。
トリアージは見出し中心で1件あたり数百トークン、精査は本文込みで数千トークン程度。
二段階＋一次フィルタにより、大半の開示は安価なトリアージだけで処理されます。

---

## 拡張

- **データソース追加**: `yosoku/sources/base.py` の `Source` を実装し
  `pipeline.build_sources()` に追加（例: J-Quants、EDINET、kabuステーションAPI）。
- **通知先追加**: `notify(signal) -> bool` を持つクラスを作り `build_pipeline()` で差し込む
  （Slack / LINE / Email 等）。

---

## テスト・Lint

```bash
pip install -e ".[dev]"
pytest -q        # 37件。ネットワーク・APIキー不要
ruff check .
```

- `test_tdnet` / `test_news`: パーサのスキーマ変換
- `test_document`: 開示PDF本文の抽出（実PDFを生成して検証）
- `test_prices`: 証券コード→ティッカー変換
- `test_store`: SQLite 重複排除・シグナル履歴
- `test_metrics`: トークン/コスト集計
- `test_analyzer`: 二段階分析（Async Claude クライアントはモック）
- `test_backtest`: スコア帯別の集計
- `test_pipeline`: 一次フィルタ・並列実行・通知判定・重複排除の統合

CI（`.github/workflows/ci.yml`）で push/PR ごとに ruff + pytest を実行します。

---

## Claude Code on the web

`.claude/hooks/session-start.sh` が依存を自動導入します。有効化するには
`.claude/settings.json.example` を `.claude/settings.json` にコピーしてください。

---

## デプロイ(常駐 / 定期実行)

- **常駐**: VPS/コンテナで `python -m yosoku watch`。
- **定期実行**: `.github/workflows/sensor.yml`（テンプレート）。状態DBを actions/cache で
  引き継ぎ再通知を防止。Secrets に `ANTHROPIC_API_KEY` / `DISCORD_WEBHOOK_URL` を登録。

---

## 構成

```
yosoku/
├── config.py          設定(YAML + 環境変数)
├── models.py          RawEvent / Analysis / Signal
├── sources/
│   ├── tdnet.py       適時開示(Yanoshin TDnet WebAPI)
│   ├── news_rss.py    ニュースRSS
│   ├── prices.py      証券コード変換 + yfinance 値動き
│   └── document.py    開示PDF本文の抽出(pypdf)
├── analyzer.py        二段階・非同期の Claude 判定
├── research.py        Web検索による追加文脈(任意)
├── metrics.py         トークン/コスト集計
├── notifier.py        Discord Webhook
├── store.py           SQLite(重複 + 履歴)
├── pipeline.py        収集→分析→通知のオーケストレーション(非同期)
├── backtest.py        判定品質の検証
└── cli.py             コマンドライン
```
