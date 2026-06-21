# 常時稼働(watch)用イメージ。中核機能のみ導入して軽く・確実にビルドする。
# (値動きコンテキスト/バックテスト用の yfinance は重いので含めない)
# シークレットは環境変数で渡す:
#   ANTHROPIC_API_KEY / DISCORD_WEBHOOK_URL
FROM python:3.11-slim

WORKDIR /app
COPY . .

# editable ではなく通常インストール。pip も上げておく。
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1

# config.poll_interval(既定60秒)で常時監視。
CMD ["python", "-m", "yosoku", "watch"]
