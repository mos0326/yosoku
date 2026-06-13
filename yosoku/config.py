"""設定ロード.

設定は YAML ファイル + 環境変数で構成する。
シークレット(API キー・Webhook URL)は **環境変数のみ**から読み、
YAML には書かない方針(誤コミット防止)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

DEFAULT_MODEL = "claude-opus-4-8"

# ニュース RSS の既定フィード。無料かつ比較的安定なものを初期値にしている。
# 必要に応じて config.yaml の sources.news.feeds で差し替え/追加する。
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
    # Yanoshin TDnet WebAPI から取得する直近件数。
    limit: int = 50
    # 監視銘柄(5桁 or 4桁の証券コード)。指定すると該当コードのみ対象にする。
    watchlist: list[str] = field(default_factory=list)


@dataclass
class NewsConfig:
    enabled: bool = True
    feeds: list[str] = field(default_factory=lambda: list(DEFAULT_NEWS_FEEDS))


@dataclass
class AnalysisConfig:
    # 通知する最低スコア(0〜100、bullish 方向)。
    score_threshold: int = 60
    # 通知する最低確信度(0〜100)。
    confidence_threshold: int = 50
    # yfinance で直近の株価コンテキストを取得して分析に渡すか。
    enable_price_context: bool = True
    # 銘柄を特定できないニュースでも通知するか。
    notify_tickerless: bool = False
    # 開示の一次キーワードフィルタ(空リストで無効=全件分析)。
    relevance_keywords: list[str] = field(
        default_factory=lambda: list(DEFAULT_RELEVANCE_KEYWORDS)
    )


@dataclass
class Config:
    anthropic_api_key: Optional[str] = None
    model: str = DEFAULT_MODEL
    discord_webhook_url: Optional[str] = None
    store_path: str = "yosoku_state.db"
    poll_interval: int = 300  # watch モードのポーリング間隔(秒)
    tdnet: TdnetConfig = field(default_factory=TdnetConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)


def load_config(path: Optional[str] = None) -> Config:
    """YAML(任意) + 環境変数から Config を組み立てる。

    YAML が無くても環境変数だけで動作する。
    """
    data: dict = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    cfg = Config()
    cfg.model = data.get("model", cfg.model)
    cfg.store_path = data.get("store_path", cfg.store_path)
    cfg.poll_interval = int(data.get("poll_interval", cfg.poll_interval))

    if "tdnet" in data and data["tdnet"] is not None:
        t = data["tdnet"]
        cfg.tdnet = TdnetConfig(
            enabled=bool(t.get("enabled", True)),
            limit=int(t.get("limit", 50)),
            watchlist=[str(c) for c in (t.get("watchlist") or [])],
        )

    if "news" in data and data["news"] is not None:
        n = data["news"]
        cfg.news = NewsConfig(
            enabled=bool(n.get("enabled", True)),
            feeds=list(n.get("feeds") or DEFAULT_NEWS_FEEDS),
        )

    if "analysis" in data and data["analysis"] is not None:
        a = data["analysis"]
        cfg.analysis = AnalysisConfig(
            score_threshold=int(a.get("score_threshold", 60)),
            confidence_threshold=int(a.get("confidence_threshold", 50)),
            enable_price_context=bool(a.get("enable_price_context", True)),
            notify_tickerless=bool(a.get("notify_tickerless", False)),
            relevance_keywords=(
                list(a["relevance_keywords"])
                if a.get("relevance_keywords") is not None
                else list(DEFAULT_RELEVANCE_KEYWORDS)
            ),
        )

    # シークレットは環境変数を最優先(YAML には書かない)。
    cfg.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", cfg.anthropic_api_key)
    cfg.discord_webhook_url = os.environ.get(
        "DISCORD_WEBHOOK_URL", cfg.discord_webhook_url
    )
    # モデルは環境変数でも上書き可能にしておく。
    cfg.model = os.environ.get("YOSOKU_MODEL", cfg.model)

    return cfg
