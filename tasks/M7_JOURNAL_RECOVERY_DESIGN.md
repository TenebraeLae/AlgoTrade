# M7 — журнал решений, отказы источника и идемпотентность paper-бара

Статус: **частично реализовано**. Миграции `0006–0010`, `PaperCycle`, `DecisionJournal`,
`PaperTickFeed` и `main.run()` содержат описанный ниже контракт. M7 **не принят**:
не выполнены пункты «Не реализовано» и «Пробелы проверки». Live-заявок этот проект
не добавляет. Несоответствия спецификации и реализации фиксируются как блокеры.

Основание: `docs/03_STRATEGY.md` §Journal, `docs/06_DB_SCHEMA.md` §decisions и
§paper_source_episodes, `docs/15_ACCEPTANCE.md` §M7.

## Подтверждённые решения оператора

1. Незавершённый paper-claim (`decisions.processing_state` = `pending` или `failed`)
   **останавливает весь paper-цикл** и требует ручной сверки журнала, сделок, позиций
   и баланса. Автоматический повтор бара, retry fill и ack запрещены.
2. Повторные отказы источника **агрегируются в один открытый эпизод** по ключу
   `(mode, ticker, stage, error_type)`: `attempt_count` увеличивается на каждую
   попытку, `first_seen`/`last_seen` и первый/последний `source_bar_ts` — UTC.
   Индивидуальная строка на попытку не создаётся. Отказы не удаляются и не
   коалесцируются молча. Эпизод закрывается (`status='recovered'`) успешным тиком
   того же тикера.

## Контракт стратегического решения

- Ключ paper-решения: `(mode='paper', ticker, source_bar_ts)`. `source_bar_ts` —
  `ts_open` последней закрытой 5m-свечи, в БД хранится как `astimezone(UTC).isoformat()`.
  Таймфрейм в ключе не хранится: `PaperCycle` фиксирует `5m`. Расширение потребует
  колонки `timeframe` и новой миграции.
- Проверка сетки (`executor.run_tick`): `bar_ts` должен быть кратен 5 минутам без
  секунд и микросекунд, иначе source-refusal `bar_grid` и остановка до решения.
- Один известный бар даёт одно завершённое решение: `no_signal`, отказ риска или
  исполненный сигнал. Повтор возвращает `PaperTickResult(already_processed=True)`
  с прежним `decision_id`, не вызывает evaluate, risk или fill; `main.run()` ack-ает.
- Под `BEGIN IMMEDIATE` сначала проверяется **любой** paper `pending/failed`, затем
  ключ бара и лишь после него `_validate_input()` снимка счёта. Даже завершённый
  дубль не скрывает незавершённый claim другого тикера.
- Для БД со старыми paper-решениями без ключа миграция 0007 фиксирует cutoff
  по времени применения 0006. Бар, завершившийся до cutoff включительно, не
  переоценивается и ack-ается как пропущенный без нового решения; его
  `PaperTickResult.decision_id=None` (нет достоверного соответствия старой строке).

### Состояния `decisions.processing_state` (фактические переходы)

| Исход | Строка | Состояние |
|---|---|---|
| `no_signal` (включая market `chaos`) | `strategy` | `complete` |
| Отказ риска (`verdict.allowed=False`) | `strategy` | `complete`, `size_lots=0` |
| Ошибка подготовки риска (`_prepare_risk`) | `strategy`, `filters_failed` += `risk_processing_error` | `failed`, fill нет |
| Разрешённый сигнал | `strategy` | `pending` до fill, затем `complete` |
| Ошибка исполнения после claim (`ValueError`, `sqlite3.Error`, `PortfolioError`) | та же строка | `failed` (если UPDATE удался), процесс останавливается |
| Ручная сверка офлайн (0010) | та же строка и append-only аудит | `pending/failed → complete`, бар принят/пропущен, fill/retry нет |

Claim `pending` коммитится до `Portfolio.open_position()`. После успешного fill
(включая нулевой) `_mark_state(...,'complete')` меняет `pending → complete` только
при ожидаемом состоянии; иное значение даёт `PaperRecoveryRequired`.

## Классификация отказов

Отказы источника **не пишутся в `decisions`** и не имеют `event_kind`. Они попадают
в `paper_source_episodes` через `DecisionJournal.record_source_refusal()`.

- **Индекс (`IMOEX`/индекс режима).** Недоступность (`GatewayError`), пустая,
  недостаточная или битая история (`ValueError` из `_closed`) и устаревший бар дают
  `MarketFilterResult(paused=True, market_regime='chaos')` с `n/a` для C/T. Это
  `strategy/complete`, не отказ источника. Неполный breadth тоже даёт паузу.
- **Кандидат (`SourceRefusal`).** Отказ, при котором нельзя честно собрать тик, идёт
  в эпизод. Фактические стадии `paper_feed.py`:
  - `skip`: `ticker_snapshot` (GatewayError), `ticker_history_invalid` (ValueError из
    `_closed`), `bar_empty` и `bar_stale` — только в ожидаемую обычную сессию
    (`_regular_session_expected`). Вне сессии пустая или старая история — не отказ.
  - `stop`: `higher_timeframe_stale` (15m/1h), `quote`, `account_snapshot`,
    `correlation`. Новый бар уже подтверждён, поэтому отказ с `source_bar_ts`.
  - В `executor.run_tick`: `tick_validation` (`ValueError` из `_validate_input`,
    `stop`), `candles_5m` (битое время/пустая история), `higher_timeframe`
    (битое время) и `bar_grid` (`stop`).
- `error_type` — имя класса исключения или константа. Текст исключения, токены,
  metadata и traceback не сохраняются. `stage`/`error_type` проходят
  `_SAFE_IDENTIFIER`.
- Ошибки до выбора тикера или времени (bootstrap, невалидный `clock()`) — фатальные
  операционные ошибки запуска, в журнал тикера не пишутся.

### Эпизоды (`paper_source_episodes`)

- Первый отказ создаёт строку `status='open'`, `attempt_count=1`, `first_seen =
  last_seen`, `indicators` — JSON всех `INDICATOR_SNAPSHOT_KEYS` со значением `n/a`
  (сохраняется при открытии эпизода, не при каждой попытке).
- Повтор с тем же ключом увеличивает `attempt_count`, двигает `last_seen` (только
  вперёд), обновляет `last_source_bar_ts` и поднимает `action` до `stop`; понижения нет.
- `recover`: `DecisionJournal.resolve_source_refusals(ticker, at)` переводит в
  `recovered` все открытые эпизоды тикера с `recovered_at=at`. Вызов — в
  `main.run()` после успешного `run_tick()` (включая `already_processed`) и **до**
  ack. Закрываются все стадии тикера, а не только одна.

## Порядок записи и восстановления (фактический)

1. `PaperTickFeed.next_paper_tick()` возвращает `PaperTick`, `SourceRefusal` или
   `None`. Feed не пишет в БД и не ack-ает. Курсор сдвигается на каждом вызове.
2. `main.run()` при `SourceRefusal` вызывает `record_source_refusal()` **до** проверки
   `action`. Ошибка записи логируется как `source_refusal_persist_failed` и пробрасывается.
   `skip` — обычный `deps.sleep()`, `stop` — `PaperFeedError`, выход с кодом 1.
3. При `PaperTick`: `run_tick()` → `resolve_source_refusals()` → `ack()`. Любое
   обычное исключение логируется по классу и останавливает процесс без ack.
4. `PaperCycle.__init__` и каждый `run_tick` вызывают `_require_reconciled()` под
   `BEGIN IMMEDIATE`. Любой paper `pending/failed` в БД — `PaperRecoveryRequired`.
   Процесс останавливается, `main()` возвращает 1. Автоматической починки нет.

## Ручная сверка (0010)

Команда `python -m trading_bot.paper_recovery` не вызывается торговым циклом.
Перед `inspect` и `confirm` оператор обязан остановить все экземпляры runtime;
выполняется офлайн backup SQLite (с учётом WAL), сверка сделок/позиций/баланса.
`inspect` не создаёт БД и не применяет миграции. `confirm` требует id решения,
ASCII-идентификатор оператора, fingerprint, исход `no_fill` либо
`accepted_existing_fill` и четыре явных подтверждения. Нет полей свободного
текста, автоматического retry, изменения баланса, стопов, якорей или лимитов.

Под `BEGIN IMMEDIATE` перечитывается снимок, сравнивается SHA-256, валидируется
исход, в одной транзакции добавляется аудит и меняется state одного paper-claim.
`no_fill` запрещён при связанных сделках/позиции. Принятие существующего fill
проверяет базовые связи mode/ticker/side/lots, но не подменяет ручной FIFO-аудит.
Другие `pending/failed` остаются блокирующими. После рестарта тот же бар уже
обработан: решение и сделка повторно не создаются. Изменившийся fingerprint
требует нового inspect и новой сверки, не автоматической повторной команды.

SQLite-lock защищает транзакцию, но не доказывает остановку другого процесса;
stop-first — обязательное действие оператора, не обещание CLI. Подробная
инструкция: `docs/MANUAL_PAPER_RECOVERY.md`.

## Числовые категории (0009)

`DecisionJournal.record()` сохраняет все категории 1–10 по docs/03 в JSON с
версией 1. Маппинг стабильных имён находится в `decision_filters.py`; это не
повторный расчёт фильтров. `not_evaluated` не равен passed; групповые №9/10
требуют всех компонентов, failed доминирует. Дополнительные ограничения
сохраняются по именам без ложных номеров. У legacy-строк JSON остаётся NULL.

## Миграции 0006–0007 (применены в коде)

Файл `migrations/0006_paper_decision_recovery.sql`:

- `decisions`: `ADD COLUMN source_bar_ts TEXT` (nullable, без backfill),
  `ADD COLUMN processing_state TEXT NOT NULL DEFAULT 'complete' CHECK (complete|pending|failed)`.
- `idx_decisions_paper_bar` — UNIQUE `(mode, ticker, source_bar_ts) WHERE mode='paper'
  AND source_bar_ts IS NOT NULL`.
- `paper_source_episodes` с `CHECK` на `mode`, `action`, `status` и согласованность
  `recovered_at`; индексы `idx_paper_source_episode_open` (UNIQUE по ключу при
  `status='open'`) и `idx_paper_source_episode_ticker_status`.

**Чего в 0006 нет:** колонок `event_kind` и `legacy`, колонки `timeframe`, `CHECK`
на формат `source_bar_ts`, `CHECK` «стратегия обязана иметь ключ». Эти инварианты
держит Python: `database.save_decision()` требует `source_bar_ts` для
`pending/failed` и только для `mode='paper'`.

**Атомарность.** `database.apply_migrations()` выполняет `executescript("BEGIN
IMMEDIATE;\n" + sql)`, затем `INSERT INTO schema_version`, затем `commit()`. Любой
`sqlite3.Error` — `rollback()` и проброс. Файл миграции не содержит `BEGIN`/`COMMIT`.
Тест `test_failed_migration_rolls_back_schema_and_version` подтверждает откат DDL и версии.

`0007_legacy_bar_cutoff.sql` сохраняет в `paper_recovery_meta` время применения 0006
и наличие старых paper-решений без ключа на момент миграции. Для такой БД
`run_tick` пропускает старые закрытые бары (`bar_ts + 5m <= cutoff_at`) под SQLite
lock, не создавая ни решение, ни fill. Если cutoff отсутствует или нечитаем,
`PaperCycle` не стартует (`PaperRecoveryRequired`). Новые БД без старых решений
не блокируют исторические фикстуры. Пропуск до миграции не объявляется повтором
конкретной старой строки журнала: точного ключа у неё нет.

## Дневной стоп paper (0008)

Дневной допуск использует капитал на стопах `B` из того же расчёта, что размер
позиции, а не отчётный mark-to-market `daily_pnl`. При `B <= (1−L) × E_day0`
`PaperCycle` сохраняет одну неизменяемую запись в `paper_daily_stop_latches` до
стратегического решения (или отдельно после rollback при ошибке `evaluate`).
На следующих тиках и после рестарта наличие строки запрещает новые входы, даже
если цены восстановились. Сброс не меняет старую строку: `PaperAccountSource`
использует новый ключ дня только после подтверждённой закрытой 10:00 5m-свечи
из `PaperTickFeed` и достоверных цен открытия всех открытых позиций. Без
подтверждения остаётся старый якорь, статус `trading_day_confirmed=False`
запрещает входы. Ручное возобновление — только для глобальной просадки.

## Оставшиеся ограничения

Исключение `evaluate()` до `Decision` теперь сохраняется как эпизод
`strategy_evaluate` с ключом закрытого бара и именем класса ошибки после
отката транзакции; исходное исключение пробрасывается, ack/fill нет. При сбое
записи эпизода ошибка SQLite видна, а не маскируется успехом.

- **Межпроцессный барьер** опирается на durable `pending/failed`, SQLite lock и
  повторное чтение баланса в `Portfolio.open_position()`; тесты двух соединений
  подтверждают отказ устаревшему снимку другого тикера и блокировку, пока
  первый процесс держит pending до fill. Это не доказательство всех расписаний.
- **Обязательный ключ для `complete`.** DB допускает `complete` без `source_bar_ts`;
  `run_tick` всегда передаёт ключ, но прямой вызов `DecisionJournal.record()` — нет.

## Пробелы проверки

Существующие тесты (имена из `tests/`):

- `test_database.py`: идемпотентность, откат после DDL (`test_failed_migration_rolls_back_schema_and_version`).
  Тест миграции старой БД с legacy-строкой и маркером cutoff —
  `test_legacy_cutoff_marks_only_databases_with_old_paper_decisions`.
- `test_paper_recovery.py`: повтор после рестарта, блокировка при `pending/failed`
  (`test_incomplete_claim_blocks_start_without_fill`), два соединения на один бар,
  глобальная блокировка другим баром, миграционный cutoff и битый маркер,
  разные тикеры/соединения, падение после commit портфеля без повтора fill,
  дедупликация `no_signal` и отказа риска.
- `test_journal.py`: одна эпизодная строка на ключ, инкремент `attempt_count`, раздельные
  ключи по тикеру/стадии/ошибке/режиму, `resolve` только своего тикера, UTC, naive
  `observed_at` отклоняется, эскалация `stop` без понижения, ошибка записи видна,
  сырой текст исключения не принимается.
- `test_paper_feed.py`: отказы по стадиям `skip`/`stop`, индекс `chaos` при GatewayError
  и битой истории, round-robin, ack-память.
- `test_main.py`: ack только после `run_tick`, при ошибке журнала ack нет.
- `test_filter_categories.py`: ранние отказы/неисполненные проверки, групповые
  категории, соответствие номерам риска, реальный paper-fill/рестарт, legacy NULL.
- `test_manual_reconciliation.py`: inspect/confirm CLI, stale fingerprint,
  атомарный откат audit/state, запрет live/complete/повторного fill, неполные
  подтверждения, append-only аудит и неизменность учёта/дневных стопов.

Перед приёмкой нужны общий `make ci`, проверка типизации и внешняя сверка
ограничений M7. Сквозные тесты main на `SourceRefusal`/`stop`, ошибки `evaluate`
и отказ SQLite добавлены в `tests/test_main.py`/`tests/test_paper_recovery.py`;
персистентный дневной стоп, переход дня и гэп — в `tests/test_daily_stop.py`.
Сетевой runtime ими не проверяется.

## Связь с критерием M7

`docs/15_ACCEPTANCE.md` §M7 требует 100% отказов с записью и snapshot. Эпизодная
модель сохраняет каждую попытку как инкремент `attempt_count`, а `n/a`-snapshot — при
открытии эпизода. Это решение оператора, а не ослабление критерия; критерий уточнён
в `docs/15`.
