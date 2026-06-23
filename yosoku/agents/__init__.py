"""マルチエージェント基盤(部門ごとに独立したモジュール).

各「部門」を独立した Agent として実装し、共有の Bus(メッセージバス)経由で
疎結合にデータを受け渡す。部門を増やす = ここにファイルを足してトピックを
購読/発行するだけ。将来的に多数の専門エージェントを並列で動かすための土台。

  Collector(収集) → events.raw
                       → Analyst(分析) → signals.analyzed
                                            → Risk(リスク管理) → signals.approved
                                                                   → Notifier(通知)
  Predictor(予測・投機的) → predictions  ( … 将来の拡張ポイント )

詳細は ARCHITECTURE.md を参照。
"""
