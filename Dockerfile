# 常時稼働(watch)用イメージ。最速通知のために 60 秒間隔で監視する。
# シークレットは環境変数で渡す:
#   docker run -e ANTHROPIC_API_KEY=... -e DISCORD_WEBHOOK_URL=... yosoku
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1

# config.poll_interval(既定60秒)で常時監視。間隔を変えるなら --interval を付ける。
CMD ["python", "-m", "yosoku", "watch"]
