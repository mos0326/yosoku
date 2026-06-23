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

# ニュース RSS の既定フィード。無料かつ比較的安定なものを初期値にしている。
DEFAULT_NEWS_FEEDS = [
    "https://www.nhk.or.jp/rss/news/cat5.xml",  # NHK ニュース(経済)
]

# 開示の一次フィルタに使う既定キーワード。ここに引っかかった開示だけを
# LLM 分析に回してコストを抑える(空にすると全件を分析する)。
DEFAULT_RELEVANCE_KEYWORDS = [
    "決算",
    "業績予想",
    "上方修正",
    "下方修正",
    "配当",
    "増配",
    "復配",
    "自己株式",
    "自社株買",
    "株式分割",
    "業務提携",
    "資本提携",
    "公開買付",
    "TOB",
    "M&A",
    "新製品",
    "受注",
]


@dataclass
class TdnetConfig:
    enabled: bool = True
    limit: int = 50
    watchlist: list[str] = field(default_factory=list)


@dataclass
class NewsConfig:
    # 既定はオフ。ニュースは特定1銘柄のシグナルになりにくく、件数だけ多く
    # コストを消費しがちなため。必要なら config で enabled: true にする。
    enabled: bool = False
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

    # --- 二段階分析(triage -> deep) ---
    two_stage: bool = True
    triage_model: str = DEFAULT_TRIAGE_MODEL
    # トリアージで is_relevant かつ score がこの値以上なら精査へ昇格。
    triage_escalate_score: int = 30
    deep_thinking: bool = True  # 精査で adaptive thinking を使う
    # 速報優先: 明確な好材料は重い精査を待たず一次判定(高速)で即通知する。
    fast_alert: bool = True

    # --- 一次キーワードフィルタ ---
    relevance_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_RELEVANCE_KEYWORDS)
    )

    # --- 実行制御 ---
    concurrency: int = 6  # 同時に走らせる分析数
    max_retries: int = 4  # Anthropic クライアントの再試行回数
    request_timeout: float = 60.0  # 1 リクエストのタイムアウト(秒)

    notify_tickerless: bool = False  # 銘柄不明のニュースでも通知するか


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
            limit=_as_int(t.get("limit"), 50),
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
        ac.notify_tickerless = bool(a.get("notify_tickerless", ac.notify_tickerless))
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

    return cfg
