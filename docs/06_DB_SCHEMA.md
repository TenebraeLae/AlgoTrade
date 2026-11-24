# 06_DB_SCHEMA — SQLite

`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`
Файл БД живёт на томе Docker, не внутри образа. Бэкап — cron-копирование раз в сутки
(см. 12_DEPLOY).

## Таблицы

### instruments
```sql
uid TEXT PRIMARY KEY, ticker TEXT, name TEXT, sector TEXT,
lot_size NUMERIC, min_price_increment TEXT, currency TEXT,
last_seen_at TEXT, is_tradable INTEGER
```
Цены и кванты — TEXT (Decimal), не REAL.

### candles_cache
```sql
ticker TEXT, timeframe TEXT, ts_open TEXT,
o TEXT, h TEXT, l TEXT, c TEXT, v REAL,
PRIMARY KEY (ticker, timeframe, ts_open)
```

### trades  — одна сделка = одна строка, никогда не обновляется целиком
```sql
id INTEGER PRIMARY KEY, journal_id INTEGER REFERENCES decisions(id),
ticker TEXT, side TEXT, lots NUMERIC, lot_size INTEGER, price TEXT, fee TEXT,
slippage TEXT, mode TEXT,                    -- paper|live
ts_open TEXT, ts_close TEXT,
exit_reason TEXT,                            -- take|stop|trail|timeout|panic|manual|eod
pnl_gross TEXT, pnl_net TEXT, pnl_pct TEXT,
broker_order_id TEXT, status TEXT,           -- open|closed|partial|orphan
balance_after TEXT
```
`price` — за одну бумагу, `lots × lot_size` — количество бумаг. Миграция 0003
оставляет `lot_size=NULL` в старых строках: фактическую лотность и корректный cash
исторических сделок нельзя восстановить автоматически. Открытая старая позиция
блокирует расчёт капитала и новые сделки до ручной сверки; подставлять `1` запрещено.

### positions
```sql
ticker TEXT PRIMARY KEY, side TEXT, lots NUMERIC, avg_entry TEXT,
stop TEXT, take TEXT, trail_state TEXT, ts_open TEXT,
mode TEXT, journal_id INTEGER, source TEXT    -- bot|restored|orphan
```

### decisions (journal решений)
```sql
id INTEGER PRIMARY KEY, ts TEXT, ticker TEXT, regime TEXT, side TEXT,
indicators TEXT,          -- JSON: EMA, RSI, MACD, BB, ADX, ATR, VWAP, объёмы, спред, гэп
filters_passed TEXT,      -- JSON array
filters_failed TEXT,      -- JSON array
size_lots NUMERIC, verdict TEXT, reason TEXT, mode TEXT,
effective_risk_pct TEXT,  -- Decimal, 0.25 означает 0.25% капитала на стопах; NULL без расчёта риска
source_bar_ts TEXT,      -- (0006) UTC ISO ts_open закрытой 5m-свечи; только paper-решения; NULL у legacy-строк
processing_state TEXT NOT NULL DEFAULT 'complete', -- (0006) complete|pending|failed
filter_categories TEXT   -- (0009) JSON version=1, categories[10]; NULL у legacy
```
Запись создаётся на сигнал, на `no_signal` (включая market `chaos`) и на отказ риска. Отказ источника в `decisions` **не пишется** — см. `paper_source_episodes`. Это главный актив проекта.

Уникальный частичный индекс `idx_decisions_paper_bar` на `(mode, ticker, source_bar_ts)` действует только при `mode='paper' AND source_bar_ts IS NOT NULL`; он защищает от второго стратегического решения по одному бару.
`pending`/`failed` — незавершённый paper-claim: после него paper-цикл останавливается до ручной сверки. Автоматический переход `pending → complete` выполняется только после успешного fill или нулевого fill. Отдельная офлайн-процедура (0010) допускает ручной переход `pending/failed → complete` с неизменяемым аудитом; без retry/fill и без изменения учёта/риска.
`filter_categories` содержит id 1–10 в порядке docs/03, имя, статус `passed|failed|not_evaluated`, массивы выполненных успешных/проваленных проверок. Для групповых №9/10 passed требует всех компонентов; любой отказ означает failed. Дополнительные ограничения остаются по именам, без новых номеров. `NULL` означает отсутствие исторических сведений, не успешную проверку.
Старые строки (до 0006) имеют `source_bar_ts=NULL` и `processing_state='complete'`; backfill не выполняется, исторический ключ из `decisions.ts` не восстанавливается.

### paper_manual_reconciliations (0010)
```sql
id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL UNIQUE REFERENCES decisions(id),
previous_state TEXT CHECK (pending|failed), ts TEXT, operator TEXT, fingerprint TEXT,
confirmations TEXT, outcome TEXT CHECK (no_fill|accepted_existing_fill), snapshot TEXT
```
Аудит подтверждения оператором: UTC-время, исходный state, SHA-256 согласованного
снимка SQLite, структурированные отметки остановки runtime/проверки сделок,
позиций и баланса, полный снимок до изменения. UPDATE/DELETE таблицы запрещены
триггерами. В одной транзакции `BEGIN IMMEDIATE` выполняются повторное чтение,
проверка fingerprint, INSERT аудита и UPDATE ровно одного paper-claim в complete.
Бары не удаляются и не переисполняются; остальные claims продолжают блокировать
цикл. Инструкция и ограничения: `docs/MANUAL_PAPER_RECOVERY.md`.

### paper_source_episodes (0006)
```sql
id INTEGER PRIMARY KEY, mode TEXT CHECK (paper|live), ticker TEXT, stage TEXT,
error_type TEXT, action TEXT CHECK (skip|stop), first_seen TEXT, last_seen TEXT,
attempt_count INTEGER CHECK (> 0), first_source_bar_ts TEXT, last_source_bar_ts TEXT,
indicators TEXT,         -- JSON всех ключей снимка со значением n/a, при открытии эпизода
status TEXT CHECK (open|recovered), recovered_at TEXT
```
Один открытый эпизод на `(mode, ticker, stage, error_type)` — UNIQUE-индекс при `status='open'`. Повтор увеличивает `attempt_count` и обновляет `last_seen` и `last_source_bar_ts`; `action` поднимается до `stop` и не понижается. `recovered_at` задан ровно при `status='recovered'`. Все времена — UTC ISO. Сырой текст исключения не хранится: `stage` и `error_type` — безопасные идентификаторы.

### paper_recovery_meta (0007)
```sql
singleton INTEGER PRIMARY KEY CHECK (singleton = 1), cutoff_at TEXT NOT NULL,
has_legacy_paper_decisions INTEGER NOT NULL CHECK (has_legacy_paper_decisions IN (0, 1))
```
Маркер фиксирует время применения 0006 и наличие старых paper-решений без bar-key
при миграции. Если такие строки есть, `PaperCycle` не переоценивает бары с
`bar_ts + 5m <= cutoff_at`; старые строки не получают выдуманный ключ. Пропущенный
старый бар ack-ается без новой сделки/решения (`decision_id=None`). Повреждённый
или отсутствующий маркер блокирует запуск до ручной сверки.

### paper_equity_anchors (0005) и paper_daily_stop_latches (0008)
```sql
paper_equity_anchors(anchor_type TEXT, anchor_key TEXT, equity TEXT, created_at TEXT,
                     PRIMARY KEY(anchor_type, anchor_key))
paper_daily_stop_latches(trading_day TEXT PRIMARY KEY, triggered_at TEXT,
                         capital_at_stops TEXT, threshold_equity TEXT)
```
`daily`-якорь `E_day0` фиксируется один раз на подтверждённый торговый день МСК;
`starting`-якорь `E_start` — один раз на счёт. При отсутствии свечи открытия
нового дня прежний якорь не заменяется текущим капиталом. Строка дневного стопа
появляется при `B <= (1−DAILY_LOSS_LIMIT) × E_day0`, никогда не удаляется/не
перезаписывается; новый подтверждённый день использует новый ключ. Денежные
значения — текст Decimal, время срабатывания — UTC ISO.

### day_stats
```sql
date TEXT PRIMARY KEY, pnl TEXT, pnl_pct TEXT, trades INTEGER,
wins INTEGER, losses INTEGER, max_dd TEXT, imoex_pct TEXT, alpha TEXT,
stopped_by_limit INTEGER, balance_start TEXT, balance_end TEXT
```

### optimizer_runs
```sql
id INTEGER PRIMARY KEY, ts TEXT, level INTEGER, trigger TEXT,
window_from TEXT, window_to TEXT, changes TEXT,    -- JSON: что и почему
metrics_before TEXT, metrics_after TEXT,
status TEXT,                                        -- applied|proposed|rejected|rolled_back
backup_tag TEXT
```

### proposals (для уровня 3, только ручное применение)
```sql
id INTEGER PRIMARY KEY, ts TEXT, params TEXT, sharpe_new TEXT, sharpe_old TEXT,
forward_pnl TEXT, forward_winrate TEXT, status TEXT,  -- pending|approved|rejected|expired
decided_at TEXT
```

### secrets_meta
```sql
name TEXT PRIMARY KEY, cipher BLOB, kdf_salt BLOB, kdf_iters INTEGER,
created_at TEXT, hint TEXT     -- только маска вида t.xxxx…9f3a
```
Нигде не хранится сам токен в открытом виде и мастер-пароль.

### event_log
```sql
ts TEXT, level TEXT, module TEXT, event TEXT, ctx TEXT, notify_vk INTEGER
```

Для пересмотра paper-пула `ctx` содержит versioned JSON: generation/слот,
UTC-время, outcome, подтверждённые даты сессий, выбранный состав/monitoring,
metadata и закреплённые instrument ID, основания/исключения и threshold.
`Decimal` записывается строками; секретов/сырого SDK exception здесь нет,
`notify_vk=0` не означает доставку уведомления. Успешная запись фиксируется
до публикации состава в feed, SQL-ошибка запрещает применение нового пула.
Это отдельный журнал выбора пула, не `decisions` и не фиктивная сделка.
Рестарт восстанавливает identity открытых позиций из подтверждённой записи,
но допуск входов всё равно требует свежего пересмотра; legacy без доказанной
identity не переназначается по тикеру автоматически.

`event='pool_refresh_applied'` означает, что **полный проверенный snapshot уже
закоммичен**, но не доказывает публикацию в текущем процессе. Если после SQL
и до publish пришла отмена или сменилась граница слота, запись остаётся durable,
а старый universe не заменяется и gate закрыт. Это не частичный staging:
рестарт использует запись как свидетельство identity, но требует свежий отбор.
`pool_refresh_failed` фиксирует отказ; откат часов также записывается один раз
и оставляет допуск закрытым до перезапуска процесса.

## 7 проверок целостности (обязательные тесты)

1. На каждый закрытый `trades.status='closed'` есть соответствующая `decisions`.
2. Сумма `pnl_net` по всем закрытым = разница `balance_end` первого и последнего дня.
3. `positions.lots` > 0 и равна сумме неоткрытых-закрытых частей по тикеру.
4. Нет двух открытых позиций с одним тикером.
5. У каждого закрытия есть `exit_reason` из разрешённого списка.
6. В `decisions.indicators` нет NULL и пустых JSON при вердикте, отличном от отказа.
7. `mode` во всех записях одного запуска одинаков (paper не смешивается с live в одной серии).

## Миграции

Нумерованные файлы `migrations/0001_init.sql` … `0010_paper_manual_reconciliation.sql`.
Каждая миграция выполняется в одной транзакции `BEGIN IMMEDIATE`: DDL, запись
`schema_version` и `COMMIT`; при ошибке откатываются и DDL, и версия. Файлы
миграций не содержат `BEGIN`/`COMMIT`. Миграция 0006 не содержит `event_kind`,
0007 сохраняет границу старых баров; 0008 добавляет персистентные факты дневного
стопа; 0009 добавляет nullable JSON категорий без backfill, 0010 — аудит ручной
сверки с append-only триггерами. Применение идемпотентное; ручных
`ALTER TABLE` в рантайме нет.
