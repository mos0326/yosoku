# yosoku — 日本株トレーディングセンサー

上場企業の**適時開示（決算・業績修正・配当・自社株買い 等）**やニュースを
リアルタイムに取り込み、**Claude（LLM）**が「短期的に株価を押し上げる材料か」を
判定し、シグナルを **Discord** に通知する MVP です。

> ⚠️ **免責**: 本ソフトウェアは情報提供・技術検証を目的としたもので、投資助言では
> ありません。LLM の判定は誤りを含みます。実際の売買は自己責任で行ってください。

---

## 仕組み

```
                ┌─────────── 収集 ───────────┐
  TDnet 適時開示 ─┤ TdnetSource (Yanoshin API)│
  ニュース RSS  ─┤ NewsRssSource             │
                └────────────┬──────────────┘
                             ▼
                   一次キーワードフィルタ        ← コスト削減(任意)
                             ▼
                     重複排除 (SQLite)           ← 再通知を防ぐ
                             ▼
            分析: Claude messages.parse → Analysis  ← 構造化出力で判定
              ( + yfinance の直近値動きを文脈に添付 )
                             ▼
                   通知判定 (score/confidence)
                             ▼
                  Discord Webhook で通知
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
| 適時開示 | [Yanoshin TDnet WebAPI](https://webapi.yanoshin.jp/)（無料） | **主シグナル**。各開示に証券コードが付くので銘柄が確定する。準リアルタイム。 |
| ニュース | RSS（既定: NHK 経済） | 補助。銘柄特定は Claude に委ねる。フィードは差し替え可。 |
| 株価文脈 | yfinance（無料・遅延あり） | 直近の値動きを分析プロンプトに添える。取得失敗してもスキップ。 |

> **リアルタイム価格について**: 真のリアルタイム板情報は証券口座必須・有料が中心です。
> 本MVPは「決算・開示・ニュースという“材料”の検知」を主眼にし、価格はyfinanceの
> 準リアルタイム値で文脈付けします。本格運用ではJ-QuantsやkabuステーションAPI等への
> 差し替えを想定（`yosoku/sources/` に新ソースを追加するだけ）。

> **ネットワーク**: 実行環境のegressポリシーによっては一部フィードがブロックされます
> （例: 制限環境では `nhk.or.jp` が 403）。その場合はWARNINGログを出して継続します。
> 到達可能なフィードに差し替えてください。

---

## セットアップ

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # もしくは: pip install -e ".[dev]"
```

シークレットは環境変数で渡します（`.env.example` を参照）:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy
# 任意: モデル変更（既定 claude-opus-4-8）
# export YOSOKU_MODEL=claude-sonnet-4-6
```

設定は任意で YAML（`config.example.yaml` をコピーして使用）:

```bash
cp config.example.yaml config.yaml
```

---

## 使い方

```bash
# 収集だけ試す（APIキー不要・疎通確認）
python -m yosoku sources --limit 10

# 1回だけ実行。通知せずシグナルを表示（APIキーのみ必要）
python -m yosoku --config config.yaml run --dry-run

# 1回実行して Discord に通知
python -m yosoku --config config.yaml run

# 常駐してポーリング（既定300秒間隔）
python -m yosoku --config config.yaml watch --interval 300
```

`-v` でデバッグログ。`yosoku` コマンドとしてもインストールされます（`yosoku run` 等）。

---

## 設定（config.yaml）

主な項目（全項目は `config.example.yaml`）:

- `model`: 使用モデル。既定 `claude-opus-4-8`。
- `tdnet.watchlist`: 監視銘柄を絞る場合に証券コードを列挙（空＝全開示）。
- `news.feeds`: ニュースRSSのURL一覧。
- `analysis.score_threshold` / `confidence_threshold`: 通知しきい値。
- `analysis.relevance_keywords`: 開示の一次フィルタ。**空にすると全開示をLLM分析**
  （件数が多くコスト増）。既定は決算・上方修正・配当・TOB 等に限定。
- `analysis.enable_price_context`: yfinanceで値動きを添えるか。
- `analysis.notify_tickerless`: 銘柄不明のニュースでも通知するか（既定 false）。

### コストについて

既定モデル `claude-opus-4-8` は高性能な分、入出力単価が高めです。開示は1日数百件
出るため、**一次キーワードフィルタ**で分析対象を絞るのが現実的です。さらにコストを
下げたい場合は `YOSOKU_MODEL=claude-sonnet-4-6`（または `claude-haiku-4-5`）に
切り替えてください。

---

## 拡張

新しいデータソースは `yosoku/sources/base.py` の `Source` を実装し、
`RawEvent` のリストを返す `fetch()` を用意して `pipeline.build_sources()` に
追加するだけです（例: J-Quants、EDINET、kabuステーションAPI）。

通知先を増やす場合は `notifier.py` と同じ `notify(signal) -> bool` を持つ
クラスを用意し、`build_pipeline()` で差し込みます（Slack / LINE / Email 等）。

---

## テスト

ネットワーク・API不要のユニットテストを同梱しています:

```bash
pip install -e ".[dev]"
pytest -q
```

- `test_tdnet` / `test_news`: パーサのスキーマ変換
- `test_prices`: 証券コード→ティッカー変換
- `test_store`: SQLite 重複排除
- `test_analyzer`: 分析後処理（Claudeクライアントはモック）
- `test_pipeline`: 一次フィルタ・通知判定・重複排除の統合

---

## 構成

```
yosoku/
├── config.py          設定ロード(YAML + 環境変数)
├── models.py          RawEvent / Analysis / Signal
├── sources/
│   ├── tdnet.py       適時開示(Yanoshin TDnet WebAPI)
│   ├── news_rss.py    ニュースRSS
│   └── prices.py      証券コード変換 + yfinance 値動き
├── analyzer.py        Claude による判定(構造化出力)
├── notifier.py        Discord Webhook
├── store.py           重複管理(SQLite)
├── pipeline.py        収集→分析→通知のオーケストレーション
└── cli.py             コマンドライン
```
