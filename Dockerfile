# Стартовый скелет. Агент дополняет под реальный стек (имена пакетов - VERIFY).
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

# Пакет tinkoff-investments ставится из GitHub (requirements.txt) — pip требует git.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev build-essential ca-certificates tzdata git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# tinkoff-investments не ставится с PyPI (см. requirements.txt) и объявляет несуществующую
# зависимость `tinkoff` в своём pyproject — поэтому ставится отдельно с --no-deps, когда
# все его реальные транзитивные зависимости уже установлены строкой выше.
RUN pip install --no-cache-dir --no-deps \
    "tinkoff-investments @ git+https://github.com/RussianInvestments/invest-python.git@0.2.0-beta117"

COPY . .

# непривилегированный пользователь (см. 13_SECURITY)
RUN useradd -m -u 10001 tbot && chown -R tbot:tbot /app
USER tbot

# тома: данные БД, логи, отчёты, секреты - снаружи образа
VOLUME ["/app/data", "/app/logs", "/app/reports", "/app/secrets"]

EXPOSE 8080

# Дашборда (M9) ещё нет — healthcheck смотрит на свежий heartbeat из main.py
# (обновляется каждые 5с), а не на HTTP /api/status.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import sys,time; mtime=__import__('os').path.getmtime('/app/data/heartbeat'); sys.exit(0 if time.time()-mtime<30 else 1)"

# точка входа - main; режим по умолчанию берётся из config и всегда paper
CMD ["python", "-m", "trading_bot.main"]
