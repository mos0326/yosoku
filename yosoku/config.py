"""設定ロード.

設定は YAML ファイル + 環境変数で構成する。
シークレット(API キー・Webhook URL)は **環境変数のみ**から読み、
YAML には書かない方針(誤コミット防止)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

# 精査(deep)に使う既定モデル。最高性能を優先。
DEFAULT_MODEL = "claude-opus-4-8"
# 全件トリアージに使う安価・高速モデル。
DEFAULT_TRIAGE_MODEL = "claude-haiku-4-5"
# 最終判定(通知直前のセカンドオピニオン)に使うモデル。Opus より上位の Fable を
# 通知候補(1日数件)だけに使う。単価は Opus の2倍だが件数が少ないため影響は小さい。
DEFAULT_ARBITER_MODEL = "claude-fable-5"

# ニュース RSS の既定フィード。無料かつ比較的安定なものを初期値にしている。
DEFAULT_NEWS_FEEDS = [
    "https://www.nhk.or.jp/rss/news/cat5.xml",  # NHK ニュース(経済)
    "https://news.yahoo.co.jp/rss/topics/business.xml",  # Yahoo!ニュース 経済トピックス
]

# 開示の一次フィルタに使う既定キーワード。ここに引っかかった開示だけを
# LLM 分析に回してコストを抑える(空にすると全件を分析する)。
# タイトルの部分一致。ノイズは安価なトリアージ(haiku)が弾くので、
# 「上がりうる材料」は広めに拾う方針。
DEFAULT_RELEVANCE_KEYWORDS = [
    # 業績・決算系
    "決算",
    "業績予想",
    "上方修正",
    "下方修正",
    "月次",
    "最高益",
    "黒字",
    "増益",
    # 株主還元系
    "配当",
    "増配",
    "復配",
    "自己株式",
    "自社株買",
    "株式分割",
    "優待",
    # 資本・再編系
    "提携",
    "公開買付",
    "TOB",
    "M&A",
    "MBO",
    "買収",
    "子会社化",
    "株式交換",
    "出資",
    "プライム",
    # 事業・材料系
    "新製品",
    "受注",
    "契約締結",
    "共同開発",
    "ライセンス",
    "特許",
    "治験",
    "承認",
]


@dataclass
class TdnetConfig:
    enabled: bool = True
    # 1回のポーリングで見る開示件数。決算集中日の15時台は1分間に50件を超える
    # ことがあるため、取りこぼし防止で広めに取る(重複は seen で弾くので安全)。
    limit: int = 150
    watchlist: list[str] = field(default_factory=list)


@dataclass
class NewsConfig:
    # 報道(M&A観測・提携報道など)は開示より先に動くことがあるため既定オン。
    # 件数は少なめのフィードに限定しており、トリアージ(haiku)コストは軽微。
    enabled: bool = True
    feeds: list[str] = field(default_factory=lambda: list(DEFAULT_NEWS_FEEDS))


@dataclass
class AnalysisConfig:
    # --- 通知しきい値 ---
    score_threshold: int = 60
    confidence_threshold: int = 50

    # --- 文脈づけ ---
    enable_price_context: bool = True  # yfinance の直近値動きを添える
    fetch_document: bool = True  # 開示PDF本文を抽出して精査に渡す
    max_document_chars: int = 6000  # 本文の最大文字数
    enable_web_context: bool = False  # Web検索で追加文脈(実験的)

    # --- 最終判定(通知直前のセカンドオピニオン) ---
    # 通知条件を満たしたシグナルだけを上位モデルが最終ゲートとして精査し、
    # 承認/却下と買い推奨の微修正を行う。失敗時はそのまま通知(フェイルオープン)。
    enable_arbiter: bool = True
    arbiter_model: str = DEFAULT_ARBITER_MODEL

    # --- 二段階分析(triage -> deep) ---
    two_stage: bool = True
    triage_model: str = DEFAULT_TRIAGE_MODEL
    # トリアージで is_relevant かつ score がこの値以上なら精査へ昇格。
    triage_escalate_score: int = 30
    deep_thinking: bool = True  # 精査で adaptive thinking を使う
    # 速報優先: 明確な好材料を一次判定(高速)で即通知する。精度重視のため既定OFF
    # (OFFだと有望分は本文PDF+現在値+推論つきの精査に回り、買い時/想定上昇率も精緻化)。
    fast_alert: bool = False

    # --- 一次キーワードフィルタ ---
    relevance_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_RELEVANCE_KEYWORDS)
    )

    # --- 実行制御 ---
    concurrency: int = 6  # 同時に走らせる分析数
    max_retries: int = 4  # Anthropic クライアントの再試行回数
    request_timeout: float = 60.0  # 1 リクエストのタイムアウト(秒)

    # --- デイリーピック(1日の最低通知件数の保証) ---
    # daily_pick_time(JST)を過ぎても当日の通知が min_daily_alerts に満たない場合、
    # 当日分析した中から基準未達/最終判定見送りの上位候補を「補欠」として通知する。
    min_daily_alerts: int = 2
    # JST。引け後の開示ラッシュが一巡した頃。YAML では "19:00" と引用符で囲むこと。
    # 注意: active_window を絞る場合はこの時刻が窓内に入るようにする(窓外では発火しない)。
    daily_pick_time: str = "19:00"
    daily_pick_min_score: int = 30  # これ未満の候補は補欠にも採用しない

    notify_tickerless: bool = False  # 銘柄不明のニュースでも通知するか
    # 東京プロマーケットのみ上場(一般売買しにくい)の銘柄は通知しない。
    exclude_pro_market: bool = True
    # 通知時に現在値を取得して載せるか。
    fetch_price_on_alert: bool = True


@dataclass
class Config:
    anthropic_api_key: str | None = None
    model: str = DEFAULT_MODEL  # 精査(deep)モデル
    discord_webhook_url: str | None = None
    store_path: str = "yosoku_state.db"
    poll_interval: int = 60  # watch の監視間隔(秒)。最速通知のため短め。
    # 稼働時間帯(JST, 例 "08:00-23:30")。None なら常時。引け後の開示が多いので
    # 9-15 に絞らず広めを推奨(15:00以降に決算/上方修正/自社株買いが集中する)。
    active_window: str | None = None
    weekdays_only: bool = True  # 土日は休む(開示が出ない)
    tdnet: TdnetConfig = field(default_factory=TdnetConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def load_config(path: str | None = None) -> Config:
    """YAML(任意) + 環境変数から Config を組み立てる。"""
    data: dict = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    cfg = Config()
    cfg.model = data.get("model", cfg.model)
    cfg.store_path = data.get("store_path", cfg.store_path)
    cfg.poll_interval = _as_int(data.get("poll_interval"), cfg.poll_interval)
    cfg.active_window = data.get("active_window", cfg.active_window)
    cfg.weekdays_only = bool(data.get("weekdays_only", cfg.weekdays_only))

    if data.get("tdnet"):
        t = data["tdnet"]
        cfg.tdnet = TdnetConfig(
            enabled=bool(t.get("enabled", True)),
            limit=_as_int(t.get("limit"), 150),
            watchlist=[str(c) for c in (t.get("watchlist") or [])],
        )

    if data.get("news"):
        n = data["news"]
        cfg.news = NewsConfig(
            enabled=bool(n.get("enabled", True)),
            feeds=list(n.get("feeds") or DEFAULT_NEWS_FEEDS),
        )

    if data.get("analysis"):
        a = data["analysis"]
        ac = AnalysisConfig()
        ac.score_threshold = _as_int(a.get("score_threshold"), ac.score_threshold)
        ac.confidence_threshold = _as_int(
            a.get("confidence_threshold"), ac.confidence_threshold
        )
        ac.enable_price_context = bool(
            a.get("enable_price_context", ac.enable_price_context)
        )
        ac.fetch_document = bool(a.get("fetch_document", ac.fetch_document))
        ac.max_document_chars = _as_int(
            a.get("max_document_chars"), ac.max_document_chars
        )
        ac.enable_web_context = bool(a.get("enable_web_context", ac.enable_web_context))
        ac.enable_arbiter = bool(a.get("enable_arbiter", ac.enable_arbiter))
        ac.arbiter_model = a.get("arbiter_model", ac.arbiter_model)
        ac.two_stage = bool(a.get("two_stage", ac.two_stage))
        ac.triage_model = a.get("triage_model", ac.triage_model)
        ac.triage_escalate_score = _as_int(
            a.get("triage_escalate_score"), ac.triage_escalate_score
        )
        ac.deep_thinking = bool(a.get("deep_thinking", ac.deep_thinking))
        ac.fast_alert = bool(a.get("fast_alert", ac.fast_alert))
        ac.concurrency = _as_int(a.get("concurrency"), ac.concurrency)
        ac.max_retries = _as_int(a.get("max_retries"), ac.max_retries)
        ac.request_timeout = float(a.get("request_timeout", ac.request_timeout))
        ac.min_daily_alerts = _as_int(a.get("min_daily_alerts"), ac.min_daily_alerts)
        # YAML で 19:00 を引用符なしで書くと六十進整数(1140)になるため必ず文字列化する。
        ac.daily_pick_time = str(a.get("daily_pick_time", ac.daily_pick_time))
        ac.daily_pick_min_score = _as_int(
            a.get("daily_pick_min_score"), ac.daily_pick_min_score
        )
        ac.notify_tickerless = bool(a.get("notify_tickerless", ac.notify_tickerless))
        ac.exclude_pro_market = bool(a.get("exclude_pro_market", ac.exclude_pro_market))
        ac.fetch_price_on_alert = bool(
            a.get("fetch_price_on_alert", ac.fetch_price_on_alert)
        )
        if a.get("relevance_keywords") is not None:
            ac.relevance_keywords = list(a["relevance_keywords"])
        cfg.analysis = ac

    # シークレット/モデルは環境変数を優先。
    cfg.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
    cfg.discord_webhook_url = os.environ.get(
        "DISCORD_WEBHOOK_URL", cfg.discord_webhook_url
    )
    if os.environ.get("YOSOKU_MODEL"):
        cfg.model = os.environ["YOSOKU_MODEL"]
    if os.environ.get("YOSOKU_TRIAGE_MODEL"):
        cfg.analysis.triage_model = os.environ["YOSOKU_TRIAGE_MODEL"]
    if os.environ.get("YOSOKU_ARBITER_MODEL"):
        cfg.analysis.arbiter_model = os.environ["YOSOKU_ARBITER_MODEL"]

    return cfg
