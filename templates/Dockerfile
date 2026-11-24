# Стартовый скелет. Агент дополняет под реальный стек (имена пакетов - VERIFY).
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev build-essential ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# непривилегированный пользователь (см. 13_SECURITY)
RUN useradd -m -u 10001 tbot && chown -R tbot:tbot /app
USER tbot

# тома: данные БД, логи, отчёты, секреты - снаружи образа
VOLUME ["/app/data", "/app/logs", "/app/reports", "/app/secrets"]

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/status').status==200 else 1)"

# точка входа - main; режим по умолчанию берётся из config и всегда paper
CMD ["python", "-m", "trading_bot.main"]
