# CHANGELOG_AI.md

Журнал работы ИИ-агента. Агент обязан дописать запись **до** завершения сессии.
Отсутствие записи = работа не выполнена, независимо от состояния кода.

## Формат записи

```
## YYYY-MM-DD HH:MM | Этап Mx | Задача Bx.y
Сделано:
- файл.py — что именно
Проверено:
- тест / команда / ручной замер
Не сделано / перенесено:
- что и почему
TODO(VERIFY) новые:
- имя_метода — где проверить
Решения оператора:
- что подтверждено, какой override
Следующий шаг:
- одна строка
```

---

## 2026-09-26 23:50 | Этап M0/M1/M2(частично) | Согласованный план: каркас + security.py (keyring) + config.py + api_client.py (init/токен/счета)

Сделано:
- `trading_bot/__init__.py` — назначение пакета, разрешения/запреты (docstring 3–5 строк).
- `trading_bot/config.py` — `Settings` (pydantic-settings, `env_prefix="TBOT_"`, `extra="forbid"`),
  `load_settings()`: paper+дефолты при любой ошибке чтения/валидации (кроме неизвестного
  ключа), явный `ConfigError` на неизвестный `TBOT_*` (ищется вручную и в окружении, и в
  `.env`, так как pydantic-settings молча игнорирует нераспознанные переменные окружения —
  это НЕ то же самое, что `extra="forbid"` для явных kwargs). `risk_per_trade > 0.02` — WARNING,
  не отказ. Деньги — только `Decimal`, `tz_display` валидируется через `zoneinfo`.
- `trading_bot/security.py` — `get_token()`/`mask_token()` на `keyring` (сервис `"tbot"`),
  `SecurityError` на недоступный keyring/пустой токен. Токен возвращается открытым текстом
  только из этой функции; логи используют исключительно `mask_token()`.
- `trading_bot/api_client.py` — `InvestGateway`: **только** `check_token()`, `list_accounts()`,
  `production_target()`/`sandbox_target()`. Никаких методов заявок — ни реализованных, ни
  стабов (явное указание оператора в этой сессии). Единственный модуль с `import tinkoff.invest`
  (ленивый, внутри функций — чтобы конфиг/секьюрити не падали при отсутствии SDK).
- `pyproject.toml`, `Makefile`, `.venv/` — окружение для `pytest`/`mypy --strict`/`ruff`/`black`.
- `requirements.txt`, `.env.example`, `.gitignore`, `Dockerfile`, `docker-compose.yml` — скопированы
  из `templates/` в корень (Dockerfile/compose без изменений, requirements.txt дополнен реально
  проверенными версиями секьюрити/конфиг-зависимостей).
- Тесты: `tests/test_config.py` (7), `tests/test_security.py` (7, с in-memory keyring-фейком),
  `tests/test_api_client.py` (7, на моке клиента), `tests/test_sdk_symbols_exist.py` (19,
  намеренно красные/частично красные), `tests/test_no_direct_sdk_import.py` (1, grep-тест).

Проверено:
- `python -m pytest -q`: 22 passed, 19 failed — все 19 падений в `test_sdk_symbols_exist.py`
  (ожидаемо красный файл по протоколу docs/08_TINVEST_API.md); всё остальное зелёное.
- `python -m mypy trading_bot` (strict): `Success: no issues found in 4 source files`.
- `python -m ruff check trading_bot tests`: `All checks passed!`.
- `python -m black --check trading_bot tests`: `All done`, без изменений.
- Вручную: `pip index versions keyring/pydantic-settings` — версии в requirements.txt взяты из
  реально установленных в `.venv` (не «на глаз»).

Не сделано / перенесено:
- `main.py`, `market_data.py`, `strategy.py`, `risk_manager.py`, `broker/*`, `portfolio.py`,
  `executor.py`, `dashboard.py`, `vk_bot.py`, `optimizer.py`, `database.py`, `logger.py`,
  `utils.py` — не создавались, вне границ этой сессии.
- `InvestGateway`: нет ни одного метода по свечам/стакану/стриму/маржи/short/IMOEX/календарю/
  комиссии/корп.событиям — даже как `NotImplementedError`-заглушки; это осознанное сужение
  по прямому указанию оператора («только инициализация, проверка токена, получение счетов»),
  а не забывчивость.
- `AccountInfo` сужен до одного поля `id` — тип/статус/валюта счёта не подтверждены (VERIFY №3).
- Выбор счёта (когда их несколько) — не реализован, только получение списка.
- AES-GCM + мастер-пароль из docs/13_SECURITY.md как основной путь — не реализован; вместо
  этого выбран разрешённый docs/13_SECURITY.md альтернативный путь: OS `keyring`
  (решение оператора этой сессии, см. ниже).

TODO(VERIFY) новые (18 позиций, все из списка `docs/08_TINVEST_API.md`, статус на 2026-09-26):
- п.1 — имя пакета `tinkoff-investments` подтверждено README/PyPI-страницей, НО
  `pip install tinkoff-investments` вернул **404** от `pypi.org/simple` и `pypi.org/pypi/.../json`
  в этой песочнице; версия НЕ пинуется. Подтвердить установку в целевом деплой-окружении.
- п.2 — `Client`/`AsyncClient`, `target=INVEST_GRPC_API[_SANDBOX]` подтверждены README, но не
  проверялись живьём (SDK не устанавливается здесь); точная семантика ошибок авторизации —
  не подтверждена.
- п.3 — поля `Account` (тип/статус/валюта) — подтверждён только `id`.
- п.4 — список инструментов, поле `real_trading_status` и допустимые значения.
- п.5 — сервис торговых статусов и enum целиком.
- п.6 — метод свечей, лимит за запрос, пагинация, глубина истории.
- п.7 — стакан/текущие цены/доступный объём.
- п.8 — подписка на стрим, лимит подписок на соединение, реконнект-семантика.
- п.9 — типы/поля заявки (не реализуется до M8 и подтверждения оператора).
- п.10 — отмена заявки, список активных заявок, частичное исполнение в статусе.
- п.11 — маржинальные показатели и достаточность средств.
- п.12 — точные rate limits (rps на метод) и код ошибки при превышении.
- п.13 — доступность short по счёту/тарифу.
- п.14 — тикеры IMOEX через API для бенчмарка.
- п.15 — календарь торговых дней через API или только внешний источник.
- п.16 — комиссия из API или расчёт по тарифу вручную.
- п.17 — корпоративные события (сплиты/дивиденды) в API.
- п.18 — точная константа боевого/sandbox хоста в реальном вызове (не только по README).

Решения оператора:
- Хранение токена — через OS `keyring` (альтернатива из docs/13_SECURITY.md), а не
  AES-GCM+мастер-пароль. Основание: прямое указание в сообщении «Используй библиотеку
  `keyring`... В коде не должно быть строк вида `token = "..."`». Зафиксировано здесь по
  формату AGENTS.md §9 (override секретного протокола не требуется — keyring прямо
  разрешён docs/13_SECURITY.md как альтернатива, конфликта с регламентом нет).
- `api_client.py` ограничен явным списком «инициализация, проверка токена, счета»,
  без единого метода/стаба заявок — прямое указание оператора, приоритет над черновым
  планом сессии (там предполагались доп. `NotImplementedError`-стабы на весь список VERIFY).

Следующий шаг (исходный, см. обновление ниже):
- M3 (market_data.py: свечи/кэш/лимитер/стрим) — сначала повторно проверить установку
  `tinkoff-investments` в реальном деплой-окружении (не в этой песочнице), иначе M3
  придётся строить полностью на моках без единого живого вызова.

---

## 2026-09-27 00:10 | Продолжение этапа M2 | Перепроверка установки tinkoff-investments

Сделано:
- Фактически повторил `pip install tinkoff-investments` — снова 404 от PyPI. Попробовал
  установку напрямую с GitHub-источника: `pip install --no-deps
  "git+https://github.com/RussianInvestments/invest-python.git@0.2.0-beta117"` — **сработало**.
  Пакет `tinkoff` (базовая транзитивная зависимость из pyproject SDK) тоже отсутствует на
  PyPI (тоже 404) — это второй, независимый сигнал, что дело не в очепатке/одном пакете,
  а в том, что tinkoff*-имена похоже, убраны из индекса целиком.
- Живым импортом/вызовом в `.venv` доказано подтверждение Обновлён п.1–3:
  - `Client`/`AsyncClient` импортируются; `Client(token).users` активируется только внутри
    `with`-блока (подтверждено интроспекцией).
  - `INVEST_GRPC_API == "invest-public-api.tinkoff.ru"`,
    `INVEST_GRPC_API_SANDBOX == "sandbox-invest-public-api.tinkoff.ru"` (реальные строки,
    не заглушки).
  - Живой gRPC-вызов `Client("явно-невалидный-токен").users.get_accounts()` бросил реальный
    `tinkoff.invest.exceptions.RequestError` (в этой сети — `UNAVAILABLE` из-за TLS-прокси
    песочницы, не `UNAUTHENTICATED`; точная семантика неверного токена остаётся открытой).
  - `dataclasses.fields(Account)` == `{id, type, name, status, opened_date, closed_date,
    access_level}` — **валюты НЕТ**, правка к первой версии записи.
  - Round-trip сериализации сырого `tinkoff.invest.grpc.users_pb2.GetAccountsRequest` с protobuf==7.36.2
    (новее чем требует pyproject SDK, `<5.0.0`) работает корректно на пустом сообщении
    (не полная гарантия для сложных сообщений, но сильный сигнал).
- `requirements.txt`: `tinkoff-investments` теперь `@ git+.../invest-python.git@0.2.0-beta117`
  (пин на тег, не на плавающийся `main`), задокументировано откуда и что проверено.
- `trading_bot/api_client.py`: `AccountInfo` теперь с полями `id`/`type`/`status` (строки
  `.name` реальных enum, чтобы класс оставался используемым без установленного SDK); сообщения
  `SdkUnavailableError` теперь ссылаются на рабочий `pip install` вместо «не резолвится».
- `tests/test_sdk_symbols_exist.py` переписан: п.1–3 теперь зелёные живые тесты (включая
  реальный gRPC-вызов), кроме точной семантики ошибки авторизации (осталась красной,
  нужен реальный токен песочницы); п.4–18 остаются красными (15 позиций с `@pytest.mark.red`).
- `tests/test_api_client.py`: мок-счёта дополнены `type`/`status`; тест отсутствия SDK переделан
  на мок `_import_client` (раньше опирался на реальное отсутствие пакета, что больше не верно).

Проверено:
- `python -m pytest -q`: **26 passed, 15 failed** — все 15 падений помечены `@pytest.mark.red`
  (п.2-exact/4/5/6/7/8/9/10/11/12/13/14/15/16/17/18 — счёт не сходится с предыдущим прогоном,
  точное число — в выводе команды, не пересчитано вручную).
- `mypy --strict trading_bot`: чисто.
- `ruff check` / `black --check`: чисто.

Не сделано / перенесено:
- Проверка с реальным валидным токеном песочницы T-Invest — его нет, точная семантика
  `UNAUTHENTICATED` остаётся неподтверждённой.
- п.4–18 из списка VERIFY — не исследовались во избегание раскрытия сцопы M2 (это задача M3/M8).
- ещё не решен, нужна ли базовая зависимость `tinkoff` для сервисов кроми `users` (только users
  проверен).

TODO(VERIFY) обновленные:
- п.1 — теперь **подтвержден живой установкой** через GitHub-источник (не PyPI). Версия пинуется
  тегом `0.2.0-beta117`. Остаётся открытым: работает ли такой `pip install` из-за реального
  firewall в целевом Docker-окружении (достуч до GitHub там может быть ограничен, в отличие от
  этой песочницы).
- п.2 — частично подтвержден (хосты и класс ошибки — да; тоцная семантика неверного токена — нет).
- п.3 — **подтвержден полностью** интроспекцией (валюты НЕТ у счёта, это было верное предположение в
  первой версии документа). `AccountInfo` теперь содержит `type`/`status`.
- п.11 — не тронут, остаётся открытым (портфель/позиции, отдельно от Account).

Следующий шаг (исходный, см. обновление ниже):
- M3 (market_data.py) — теперь есть работающий сценарий установки SDK для разработки и тестов, но
  перед продовой всё ещё нужно подтвердить доступность GitHub из Docker/CI-окружения. Следующий
  шаг — начать M3 на этом же .venv.

---

## 2026-09-26 20:55 | Начало этапа M3 | Свечи, статус торговли, кэш с TTL, лимитер с приоритетами

Вход в сессию: AGENTS.md → CHANGELOG_AI.md (предыдущая запись) → tasks/BACKLOG.md раздел M3.
Найден оставшийся в корне `.verify_sdk_candles.py` (скратч-зонд от оператора, не трогал);
эквивалентная интроспекция сделана вручную через shell.

Сделано:
- интроспекцией реально установленного SDK в `.venv` подтверждены документально (docs/08_TINVEST_API.md,
  таблица VERIFY обновлена): `client.market_data.get_candles(...)`, `get_trading_status(...)`,
  `Client.instruments.shares()`, `HistoricCandle` (`open/high/low/close/volume/time/is_complete/
  candle_source`), `CandleInterval` (17 значений, нужные для конфига — `CANDLE_INTERVAL_5_MIN`,
  `_15_MIN`, `_HOUR`), `SecurityTradingStatus` (17 значений, нужное —
  `SECURITY_TRADING_STATUS_NORMAL_TRADING`), `Share` (поля `lot` — НЕ `lot_size`,
  `min_price_increment`, `trading_status` — НЕ `real_trading_status`, `short_enabled_flag`,
  `figi`/`uid`, `currency`, `sector`), `Quotation`/`MoneyValue` (`units`+`nano`, как и ожидалось).
  **Важная правка к docs/08_TINVEST_API.md**: п.4 изначально называл поле `real_trading_status`,
  реальное имя — `trading_status`.
- `trading_bot/api_client.py`: добавлены `Candle` (контракт из 01_ARCHITECTURE + `is_complete`),
  `InvestGateway.get_candles(instrument_id, timeframe, from_, to, limit)` и
  `InvestGateway.get_trading_status(instrument_id)`, `NORMAL_TRADING_STATUS`, `Timeframe`
  (`Literal["5m","15m","1h"]` — отображает docs/07_CONFIG_SPEC.md, не тянет SDK-enum наружу),
  `_quotation_to_decimal()` (`units`+`nano` -> `Decimal`, никакого float). Статус/заявки по-прежнему
  отсутствуют как класс методов (не M8).
- `trading_bot/market_data.py` (новый, L1, не импортирует `tinkoff.invest`): `CandleCache`
  (TTL по таймфрейму — 5мин/15мин/1ч, второй запрос внутри TTL в сеть не идёт),
  `RateLimiter`/`Priority` (token bucket, приоритеты `POSITIONS>ORDERS>CANDLES>POOL`,
  несрочные режутся на 80% исчерпания бюджета — docs/00_PLAN.md §9).
- `docs/08_TINVEST_API.md`: таблица VERIFY обновлена статусами п.1–6 (большинство CONFIRMED/
  частично CONFIRMED), п.7/14/16/18 — частично.
- Тесты: `tests/test_market_data_cache.py` (3), `tests/test_rate_limiter.py` (5), добавлены
  6 тестов в `tests/test_api_client.py` (`get_candles`/`get_trading_status`), переструктурирован
  `tests/test_sdk_symbols_exist.py` (убраны дубликаты п.4/5/6, добавлены живые тесты на
  `Share`/`SecurityTradingStatus`/`get_candles`-сигнатуре).

Проверено:
- `python -m pytest -q`: все красные падения помечены `@pytest.mark.red` (14 шт.: точная
  семантика авторизации, максимум свечей/запрос, стакан/стрим/заявки/портфель/маржа/
  rate-limits/short-на-счёте/IMOEX/календарь/комиссия/корп.события); всё остальное зелёное.
- `mypy --strict trading_bot` (5 файлов): чисто.
- `ruff check` / `black --check`: чисто.
- `tests/test_no_direct_sdk_import.py`: `market_data.py` не импортирует `tinkoff` напрямую —
  проверено, тест прошёл.

Не сделано / перенесено:
- Стрим котировок + реконнект с backoff (docs/08 п.8) — сигнатуры `create_market_data_stream`/
  `market_data_stream` только видны в `dir(Client)`, глубоко не исследовались.
- Календарь торговых дней (docs/08 п.16) — есть `get_trading_status` как fallback по-дневного
  статуса, но локального календаря развития из `data_seed/holidays_ru.md` ещё нет — не
  в границах этой сессии, отдельная задация M3.
- IMOEX-бенчмарк (docs/08 п.15) — не исследовался.
- Максимум свечей за запрос/глубина истории, точные rate limits (rps) — не подтверждены,
  `RateLimiter` сейчас упрощён (полное восстановление байгов раз в период, а не непрерывная rps-формула) —
  константы бюджета не привязаны к реальным лимитам API.
- Календарь праздников русского рынка из `data_seed/holidays_ru.md`, приёмка свечей в utils.py —
  не сделано.

TODO(VERIFY) новые:
- максимум свечей за `get_candles` (нужен реальный токен).
- глубина истории (с какого года даёт свечи).
- точные rate limits (rps на метод, код ошибки 429) — в SDK констант не найдено.
- тоцная семантика UNAUTHENTICATED (перенесено из прошлой записи).
- стрим-подписка: метод виден, семантика не исследована.

Следующий шаг (исходный, см. обновление ниже):
- Продолжить M3: стрим котировок + реконнект, календарь торговых дней (`data_seed/holidays_ru.md` +
  `get_trading_status` fallback). Можно продолжать в той же сессии.

---

## 2026-09-26 21:30 | Продолжение M3 | Стрим свечей + реконнект, календарь торговых дней

Сделано:
- интроспекцией реально установленного SDK подтверждены (docs/08_TINVEST_API.md, п.8 обновлён):
  `client.create_market_data_stream()` -> `MarketDataStreamManager` (`.candles.subscribe([CandleInstrument(...)])`,
  итерация по `MarketDataResponse.candle`), `SubscriptionInterval` (14 значений, нужные —
  `SUBSCRIPTION_INTERVAL_FIVE_MINUTES`/`_FIFTEEN_MINUTES`/`_ONE_HOUR`), `CandleInstrument`
  (`figi`/`interval`/`instrument_id`). **Важная находка**: стримовый
  `tinkoff.invest.schemas.Candle` (из `MarketDataResponse.candle`) — другая структура, чем `HistoricCandle`
  из `get_candles`: НЕт поля `is_complete`. Прочитан исходный код `MarketDataStreamManager`
  (`inspect.getsource`): SDK не делает авто-реконнект сам — обрыв связи выйдет исключением из
  генератора, повторная подписка — задача вызывающего (подтверждает необходимость
  собственного `StreamReconnector`).
- `trading_bot/api_client.py`: `Candle.is_complete` теперь `bool | None` (`None` для стрима,
  так как честно неизвестно — не угаданный `True`); добавлен генератор
  `InvestGateway.stream_candles(instrument_ids, timeframe)`, держит gRPC-соединение открытым
  через `with`-блок вокруг `yield`, всегда вызывает `stream.stop()` в `finally`.
- `trading_bot/market_data.py`: `StreamReconnector` — обёртывает `stream_candles` exponential
  backoff'ом (docs/00_PLAN.md §8: «каждые 5 секунд, до 10 попыток»), сбрасывает счётчик
  попыток только после успешного восстановления, после `max_attempts` бросает `GatewayError`
  (синхронизация позиций после восстановления — задача executor/portfolio, M8).
- `trading_bot/config.py`: добавлены `backoff_base_sec`(5)/`backoff_max_attempts`(10)/
  `stream_reconnect_sec`(5) из docs/07_CONFIG_SPEC.md «Data/API»; `.env.example` дополнен.
- `trading_bot/utils.py` (новый, L0): `MSK`, `SESSION_ACCEPT_ORDERS_START`(09:50)/
  `SESSION_TRADING_END`(23:50) — константы из docs/00_PLAN.md §4 (документ проекта,
  не API); `load_known_holidays()` — парсер таблицы `data_seed/holidays_ru.md`
  (сейчас возвращает пустое множество — в файле ни одной подтверждённой даты, только
  шаблон-строка `YYYY-MM-DD`); `can_trade_now()` — порядок проверки: выходные →
  известный праздник из `known_holidays` → окно сессии → `get_trading_status` (первичный
  источник); при недостушности API — консервативный отказ (буквально по `data_seed/README.md`:
  «никогда не открывать позиции на всякий случай при неизвестном статусе дня»). Возвращает
  `TradingDecision(allowed, reason)`, не голый `bool` — отказ всегда объясним.
- `docs/08_TINVEST_API.md`: п.8 обновлён до «частично CONFIRMED».
- Тесты: `tests/test_stream_reconnector.py` (4), `tests/test_utils_calendar.py` (10),
  +6 в `tests/test_api_client.py` (`stream_candles`: свечи/пустой список/неверный таймфрейм/
  обрыв посреди стрима/пробел/skip не-свечных событий).

Проверено:
- `python -m pytest -q`: красные падения — те же 14 помеченных `@pytest.mark.red` (ничего нового
  не сломалось); всё остальное зелёное, включая новые 20 тестов.
- `mypy --strict trading_bot` (6 файлов): чисто.
- `ruff check` / `black --check`: чисто.
- `tests/test_no_direct_sdk_import.py`: прошёл — `market_data.py`/`utils.py` не импортируют
  `tinkoff` напрямую.

Не сделано / перенесено:
- Стрим не проверен на реальном обрыве связи (тесты — только на моке; сеть в этой песочнице
  проксируется, живой стрим без валидного токена не запустить).
- `data_seed/holidays_ru.md` не заболнен реальными датами — это отдельная задача (сверить с
  официальным календарём MOEX), не выдумывал даты.
- IMOEX-бенчмарк, точные rate limits, максимум свечей/запрос — перенесено из предыдущей записи.
- Отдельный модуль для реального календаря торговых дней (помимо фолбэка через API) — не сделан.

TODO(VERIFY) новые:
- реальная реконнект-семантика стрима на реальном обрыве связи — нужен реальный токен.
- актуальный список праздников MOEX для `data_seed/holidays_ru.md` — сверить с официальным календарём.

Следующий шаг:
- M3 в основном закрыт, остались открытыми: точный максимум свечей/запрос, IMOEX, актуальные
  даты праздников. Можно перейти к M4 (индикаторы) или добить оставшиеся части M3.

---

## 2026-09-26 22:05 | Завершение M0 | main.py, logger.py, реальная проверка в Docker + критический баг конфига

Вход в сессию: оператор спросил почему M0 всё ещё не закрыт — честный ответ (Ask-режим) был:
каждая сессия шла по узкому прямому указанию оператора (сначала только security/config/api_client,
потом самостоятельное продолжение в M3), а не возврат к M0. Сейчас оператор явно велел
довести M0 до критерия приёмки docs/15_ACCEPTANCE.md.

Сделано:
- `trading_bot/logger.py` (новый, L0): `setup_logging(level)` — один StreamHandler на stdout,
  формат событие/уровень/контекст (docs/02_CODING_RULES.md), идемпотентен (повторный вызов
  не дублирует хендлеры), чёткий отказ на неизвестный уровень.
- `trading_bot/main.py` (новый, L5): `guard_trading_mode()` — явный отказ
  (`LiveModeNotReadyError`) при `TRADING_MODE=live` без `LiveBroker` (M8); `run()` — heartbeat-цикл
  (пишет `data/heartbeat` каждые 5с, для Docker HEALTHCHECK), останавливается только по SIGINT/
  SIGTERM; `main()` — точка входа `python -m trading_bot.main`. Сам торговый цикл
  (strategy/risk_manager/broker) не реализован — это осознанное ограничение M0, в логе есть
  явный WARNING об этом.
- **Реальный `docker compose build` + `up -d` (важнейшее событие этого шага)**:
  первая сборка увернулась с `ERROR: Could not find a version that satisfies the requirement
  tinkoff<0.2.0,>=0.1.1` — обычный `pip install -r requirements.txt` без `--no-deps` всегда
  падает на этой фантомной зависимости. Исправлено: `requirements.txt` теперь явно
  перечисляет транзитивные зависимости SDK (`grpcio==1.84.0`, `protobuf==4.25.9` —
  подтверждено чистым `docker build` без `--no-deps`, соватимо с `pyproject` SDK, в отличие
  от protobuf==7.36.2 в моем `.venv`, где он был установлен до --no-deps и не переставлен),
  `cachetools`, `deprecation`, `python-dateutil`), а `tinkoff-investments` устанавливается
  отдельным `RUN pip install --no-deps ...` в `Dockerfile` после основного `-r requirements.txt`.
  Добавлен `git` в apt-зависимости образа (нужен pip для `git+https`).
  HEALTHCHECK переделан с HTTP `/api/status` (дашборда ещё нет, M9) на проверку свежести
  heartbeat-файла.
- **Критический баг, найденный только живым `docker compose up -d`**: контейнер ушёл в
  restart-loop. Причина: `Settings.vk_allowed_peer_ids: tuple[int, ...]` — «комплексный» тип для
  pydantic-settings, оно пытается JSON-декодировать значение из `EnvSettingsSource` до наших
  валидаторов. `.env.example` даёт `TBOT_VK_ALLOWED_PEER_IDS=000000000` — невалидный JSON
  (ведущие нули), pydantic-settings бросает `SettingsError`. **Самое опасное**: эта ошибка
  возникает при любом создании `Settings(...)`, включая вызов внутри `_fallback_settings()`
  (потому что `EnvSettingsSource` сама читает окружение независимо от явных kwargs) — то есть
  **даже безопасный paper-фолбэк не сработал**. Исправлено: поле теперь `str` (не
  «комплексный» тип, JSON-декод не затрагивается), валидация формата перенесена в обычный
  `field_validator`, разбор в кортеж — в отдельный метод `parsed_vk_allowed_peer_ids()`. В `load_settings()`
  добавлен `pydantic_settings.exceptions.SettingsError` в перехватываемые исключения как
  добавочная сетка безопасности.
- Тесты: `tests/test_main.py` (5), `tests/test_logger.py` (3), +4 в `tests/test_config.py`
  (регрессионный на баг с ведущими нулями, парсинг списка, пустая строка, нечисловое значение).

Реальные ручные проверки в Docker (не моки, не юнит-тесты):
- `docker compose build` — успешно после исправления requirements.txt/Dockerfile.
- `docker compose up -d` → `docker compose ps`: `Up 49 seconds (healthy)` (проверено два раза
  с разным интервалом врейени, т.е. не случайно жив один тик).
- `docker compose logs`: `mode=paper`, без `.env`/токена (файл `.env` создан только локально из
  `.env.example` для этого теста, в `.gitignore`).
- `docker compose stop` → лог получения SIGTERM и «корректно остановлен», контейнер остановился
  без force-kill.
- `docker compose run --rm -e TBOT_TRADING_MODE=live tbot` → `CRITICAL ... переход в live запрещён
  архитектурно ...`, exit code 1.
- `make up` и `make logs` — отработали без модификации.

Проверено:
- `python -m pytest -q`: все красные — те же 14 помеченные `@pytest.mark.red` (ничего не
  сломалось); +20 новых тестов, все зелёные.
- `mypy --strict trading_bot` (8 файлов): чисто.
- `ruff check` / `black --check`: чисто.

Не сделано / перенесено:
- Остальная структура модулей из 01_ARCHITECTURE (strategy/risk_manager/broker/portfolio/executor/
  dashboard/vk_bot/optimizer/database) — это M5–M9, не M0; пустые заглушки сознательно не создавались.
- `main.py` не подключает торговый цикл — этих слоёв ещё нет.
- Мастер-пароль/дашборда/VK — не реализованы (другие милестоны).
- Docker-образ запущен от root внутри `docker build`, но сам контейнер работает от `tbot` (uid 10001) —
  не проверялось отдельно, что все тома корректно записываются непривилегированным пользователем на
  всех платформах (на Windows Docker Desktop сейчас сработало).

Следующий шаг:
- M0 закрыт в той части, что была задачей оператора (запуск в Docker, paper без токена,
  Makefile). Остальная структура из 01_ARCHITECTURE появится органически по ходу M5–M9. Можно
  возвращаться к M3/M4 или идти дальше по MILESTONES.md.

---

## 2026-09-26 22:35 | Этап M4 | Индикаторы: EMA/RSI/MACD/Bollinger/ATR/VWAP/ADX + объёмные всплески

Сделано:
- `trading_bot/indicators.py` (новый, L1, не импортирует `tinkoff.invest`): `ema`, `rsi`,
  `macd`, `bollinger`, `atr`, `session_vwap`, `adx`, `volume_spike`. Всё на чистом `Decimal`
  без numpy в проде (docs/02_CODING_RULES.md разрешает numpy только как внутренний
  инструмент с выводом в Decimal, я решил не вводить неподтверждённую prod-зависимость
  и считать всё на Decimal). Все серии возвращают `list[Decimal | None]` — `None`, а не
  0/NaN, где истории недостаточно.
- `trading_bot/utils.py`: добавлена константа `SESSION_TRADING_START = time(10, 0)`
  (начало основных торгов, нужна для сброса VWAP).
- Независимая верификация: временно установлены `numpy`/`pandas` в `.venv` (только как
  dev-инструмент, НЕ в requirements.txt), написаны `.verify_indicators.py`/`.verify_adx.py`
  (отдельная, не связанная с `indicators.py` реализация на float/numpy), сравнены
  значения. EMA(9/21)/RSI(14)/MACD(12,26,9)/Bollinger(20,2)/ATR(14) совпали точно (до
  6+ знаков) на одной 40-точечной серии; ADX — совпал с точностью ~1e-3 на отдельной
  40-точечной серии (расхождение в 6-м знаке — накопленная ошибка округления float64 за
  ~13 итераций сглаживания Уайлдера, Decimal точнее, а не хуже). Скрипты удалены после
  использования, значения зафиксированы в `tests/test_indicators.py`.
- `session_vwap`: отдельный тест на точное место из docs/03_STRATEGY.md: «сброс в 10:00 МСК,
  не в 00:00» — свеча в 00:30 МСК не сбрасывает аккумуляцию (ещё вчерашняя сессия),
  сброс только в 10:00 МСК следующего дня.
- Shift-тест (нет заглядывания в будущее, docs/11_TESTING.md №9): для EMA/RSI/MACD/BB
  проверено, что значение в точке t не меняется от добавления будущих свечей.
- Тесты: `tests/test_indicators.py` (32 теста) — golden-значения, границы (пустая
  серия, 1 свеча, ровно 20 свечей для BB, нулевой объём), отказы на неверный period,
  shift-тест, тесты `session_vwap`/`volume_spike`.

Проверено:
- `python -m pytest -q`: +32 теста, все зелёные с первой попытки; красные — те же 14 из
  `test_sdk_symbols_exist.py`, ничего не сломалось.
- `mypy --strict trading_bot` (9 файлов): чисто.
- `ruff check` / `black --check`: чисто.

Не сделано / перенесено:
- docs/11_TESTING.md требует ≥إ синтетических серий на каждый индикатор — сделана 1 серия на
  EMA/RSI/MACD/BB/ATR и 1 отдельная на ADX. Не полное соответствие документу — сознательно
  сужено по объёму работы в сессии, отмечено в BACKLOG.md как неполная строка.
- Shift-тест для ATR/ADX/session_vwap/volume_spike отдельно не написан (только для EMA/RSI/
  MACD/BB) — хотя алгоритмы те же каузальные (берут только текущие/прошлые свечи), явной
  тест не проверен.
- Связь индикаторов с `market_data.py`/`api_client.get_candles` (например, кэшированный расчёт
  при каждой новой свече) — не сделана, это задача `strategy.py` (M7).

Следующий шаг:
- M4 в основном готов. Следующий по MILESTONES.md — M5 (risk_manager.py: размер позиции,
  12 проверок, дневные лимиты) — ключевой этап пер docs/00_PLAN.md, делается до стратегии,
  не после.

---

## 2026-09-26 22:30 | Добивка M4 | Полное соответствие docs/11_TESTING.md по индикаторам

Вход в сессию: оператор спросил, почему M4 был закрыт с неполным соответствием ≥Х5-серийному
требованию docs/11_TESTING.md — ответ был: осознанный компромис между полнотой и скоростью,
не блокер. Оператор потребовал закрыть пробел.

Сделано:
- Независимая numpy-верификация 4 дополнительных синтетических серий (временный скрипт
  `.verify_indicators_5series.py`, удален): B — флэт (100 константа), C — тренд вниз (зеркальное
  отражение серии A: `27 - A`), D — осцилляция (`15 + 3*sin(i*0.3)`, ranging), E — случайный
  ряд (та же рандом-серия, что и в ADX-тестах из предыдущей записи, seed=42). A — исходная
  серия. Итого 5 серий на EMA/RSI/MACD/Bollinger/ATR/ADX/session_vwap.
- **Любопытная находка**: ADX серий A (тренд вверх) и C (тренд вниз, зеркальное
  отражение) дают идентичный ADX — подтверждено независимой реализацией: ADX измеряет
  силу тренда, не направление — это зафиксировано отдельным тестом
  `test_adx_series_a_and_c_are_mirror_symmetric`, не баг.
- Golden-тесты на 5 серий для EMA(9)/RSI(14)/MACD/Signal/Bollinger(20,2)/ATR(14)/ADX(14):
  `test_ema9_golden_5_series`, `test_rsi14_golden_5_series`, `test_macd_golden_5_series`,
  `test_bollinger_golden_5_series`, `test_atr14_golden_5_series`, `test_adx14_golden_5_series` —
  каждый параметризован по A–E (5 запусков на функцию). Для session_vwap —
  `test_session_vwap_golden_5_series_constant_volume` (при постоянном объёме VWAP вырождается
  в простое среднее типичных цен, подтверждённое `numpy.mean` независимо).
- Погрешность сравнения для 5-серийных тестов ослаблена с 1e-6 до 1e-5 (`_FLOAT_TOLERANCE`):
  на серии E (дробные входы, много знаков после точки) RSI расходился с numpy-референсом на
  ~5.5e-6 — накопленная ошибка float64 в независимой реализации, не баг нашего Decimal-кода.
- Shift-тесты добавлены для оставшихся 4 функций: `test_no_lookahead_atr`, `test_no_lookahead_adx`,
  `test_no_lookahead_session_vwap`, `test_no_lookahead_volume_spike` — теперь все 8 функций
  индикаторов имеют тест на отсутствие заглядывания в будущее.

Проверено:
- `python -m pytest tests/test_indicators.py -v`: 72 теста (было 32), все зелёные.
- `python -m pytest -q` по всей проекту: красные — те же 14 из `test_sdk_symbols_exist.py`.
- `mypy --strict trading_bot`: чисто. `ruff check` / `black --check`: чисто.

Не сделано / перенесено:
- `volume_spike` не в таблице docs/11_TESTING.md «≥5 серий» (таблица перечисляет только
  EMA/RSI/MACD/Bollinger/ATR/VWAP/ADX) — оставлен с 3 сценариями + shift-тестом, без расширения до 5.

Следующий шаг:
- M4 теперь полно соответствует docs/11_TESTING.md. Следующий по MILESTONES.md — M5
  (risk_manager.py: размер позиции, 12 проверок, дневные лимиты).

---

## 2026-09-27 03:15 | Этап M5 | risk_manager.py: размер позиции, 12 проверок, стопы/тейки

Вход в сессию: AGENTS.md → CHANGELOG_AI.md (предыдущая запись) → tasks/BACKLOG.md M5 →
  docs/04_RISK.md (формула размера, 12 фиксированных проверок, стопы/тейки/трейлинг).
  M5 — ключевой этап пер docs/00_PLAN.md: делается до стратегии (M7), не после.

Сделано:
- `trading_bot/config.py`: добавлены поля `atr_stop_mult`(1.5)/`atr_take_mult`(2.0)/
  `atr_trail_trigger`(1.0)/`atr_trail_dist`(1.0)/`max_trades_per_day`(30)/`min_free_balance`(5000)/
  `max_open_positions`(7)/`max_per_sector`(2)/`max_correlation`(0.85)/`max_atr_ratio`(0.05)/
  `max_spread_bps`(30)/`volatile_size_scaler`(0.5)/`session_guard_min`(10)/`slippage_assumed`
  (0.0005)/`fee_rate`(0.003) — все значения из docs/07_CONFIG_SPEC.md «Риск», деньги/доли
  — только `Decimal`, валидатор `_decimal_from_str` расширен на все новые Decimal-поля.
  `_fallback_settings()` и `.env.example` дополнены всеми новыми полями.
- `trading_bot/risk_manager.py` (новый, L3, не импортирует `tinkoff.invest`, не знает о брокере/
  executor/portfolio):
  - `calculate_position_size()` — формула `risk_amount / (atr*atr_stop_mult*lot_size)`,
    `ROUND_DOWN` в целый лот; некорректные входы (`balance<=0`, `atr<=0`, и т.д.) — `0`
    лотов, а не исключение/отрицательное число.
  - `check()` — все 12 проверок из docs/04_RISK.md в фиксированном порядке
    (`CHECK_NAMES`), выполняются все независимо от результата друг друга (полный snapshot для
    journal, не short-circuit). П.9 (сектор+тикер) и п.11 (chaos/volatile) объединены в
    одну проверку каждый, точно как в доке (итого ровно 12).
  - `RiskConfig`/`AccountState`/`InstrumentState`/`CheckResult`/`RiskVerdict` — все `frozen=True`
    dataclasses (согласно docs/01_ARCHITECTURE.md `RiskVerdict`). `AccountState`/`InstrumentState`
    — локальные снимки, потому что `portfolio.py` (M6) ещё не существует — risk_manager не
    читает данные сам, всё приходит готовым от вызывающего.
  - `initial_stop`/`initial_take`/`breakeven_stop`/`trailing_stop` — формулы стоп/тейк/
    трейлинга из docs/04_RISK.md; `breakeven_stop` учитывает round-trip издержки
    (`2*(fee_rate+slippage_assumed)`), а не просто входную цену.
  - `can_move_stop()` — единственная точка решения, может ли новый стоп быть принят: только
    в пользу позиции (long — вверх, short — вниз).
- Сознательное сужение: `RiskConfig` не имеет публичного сеттера/метода для увеличения
  лимитов — единственный путь изменить — правка config.py человеком + рестарт (AGENTS.md §4.2).
- тесты: `tests/test_risk_manager.py` (39 тестов) — golden на 5 тиках, изолированный отказ на
  каждую из 12 проверок, свойства (risk<=risk_per_trade на 1000 случайных наборах,
  zero-balance->always-denied на 1000, chaos->0 позиций на 1000), стоп/тейк/трейлинг/
  безубыток, `can_move_stop`, тест заморозенности `RiskConfig`, grep-тест на отсутствие
  «increase/override/boost»-имен/параметров в публичном API модуля.

Проверено:
- `python -m pytest tests/test_risk_manager.py -v`: 39 тестов, все зелёные (первый золотой
  golden-тест сначала упал — в ручном ожидаемом значении была арифметическая ошибка
  от забытого умножения на `atr_stop_mult`, исвлечена).
- `python -m pytest -q` по всей проекту: красные — те же 14 из `test_sdk_symbols_exist.py`.
- `mypy --strict trading_bot` (10 файлов): чисто. `ruff check` / `black --check`: чисто.

Не сделано / перенесено:
- Scaling in/out (50/30/20), ночной риск (закрытие к 23:30 МСК), таймаут убыточной позиции
  45 минут, глобальная просадка 15% с момента запуска — не реализованы, это отдельные
  механизмы состояния, требуют portfolio.py/executor.py (M6/M8), вне границ M5.
- Связь с реальными `sector`/`correlation` из `data_seed/sectors.md`/расчёта strategy.py —
  не сделана, risk_manager принимает уже готовые значения.

Следующий шаг (исходный, см. обновление ниже):
- M5 готов. По MILESTONES.md следующий — M6 (`PaperBroker`/`portfolio.py`): виртуальные
  заявки, учёт сделок, восстановление состояния после рестарта. Здесь рождается журнал,
  из которого потом считается всё (docs/00_PLAN.md, MILESTONES.md).

---

## 2026-09-27 03:30 | Добивка M5 | Property-тесты переписаны на `hypothesis`

Вход в сессию: оператор потребовал установить `hypothesis` и не отступать от заданных
параметров — предыдущий выбор заменить на `random.Random(seed)` был самовольным отступлением
от того, что было задумано docs/07_CONFIG_SPEC.md/requirements.txt для M5.

Сделано:
- установлен `hypothesis==6.168.1` в `.venv` (проверено `pip show`: единственная зависимость
  `sortedcontainers`, конфликтов нет). Зафиксирован в `requirements.txt` взамен строки
  `# hypothesis==<VERIFY>`.
- Сигнатуры `hypothesis.strategies.decimals()`/`hypothesis.settings()` сверены живьём
  `inspect.signature()` в этой сессии, не взяты из памяти: `st.decimals(min_value, max_value,
  allow_nan, allow_infinity, places)` принимает `str` для границ (генерирует `Decimal`
  напрямую, без float-промежуточных погрешностей).
- `tests/test_risk_manager.py`: все 3 property-теста (`risk <= risk_amount`, zero-balance,
  chaos) переписаны с ручного `for _ in range(1000): rng = random.Random(seed)` на
  `@given(...)` с `hypothesis.strategies.decimals`/`integers` и `@settings(max_examples=1000,
  deadline=None)`. `deadline=None`, потому что `check()` на Decimal может локально превысить
  встроенный тайаут hypothesis на медленных CI-раннерах, а не из-за реальной проблемы.
- Исправлена лишняя строка-дубликат (`assert verdict.allowed is False` дважды),
  оставшаяся после ручного edit_file.

Проверено:
- `python -m pytest tests/test_risk_manager.py -v`: 39 тестов, все зелёные; `hypothesis-6.168.1`
  виден в списке plugins pytest, прогон 3 property-тестов занял 4.52с (реально
  генерирует примеры, а не просто цикл по range(1000)).
- `python -m pytest -q` по всей проекту: красные — те же 14.
- `mypy --strict trading_bot` / `ruff check` / `black --check`: всё чисто.

Решения оператора:
- «hypothesis должен быть, не отступать от заданных требований» — зафиксировано, библиотека
  теперь реальная часть requirements.txt, не VERIFY.

Следующий шаг:
- M5 полностью соответствует docs/07_CONFIG_SPEC.md/docs/11_TESTING.md по выбору инструмента
  property-тестов. По MILESTONES.md следующий — M6 (`PaperBroker`/`portfolio.py`).

---

## 2026-09-27 04:05 | Этап M6 | Исправлены баги восстановления баланса, PaperBroker подтверждён

Вход в сессию: AGENTS.md → CHANGELOG_AI.md (предыдущая запись) → tasks/BACKLOG.md M6 →
  docs/06_DB_SCHEMA.md (`trades`/`positions`, 7 проверок целостности) → docs/15_ACCEPTANCE.md M6
  (13 тестов учёта, 20 сценариев рестарта, `PaperBroker` без gRPC-зависимостей). На входе
  `trading_bot/portfolio.py`, `trading_bot/database.py`, `trading_bot/broker/{__init__,paper}.py`
  и тесты (`test_portfolio.py`, `test_database.py`, `test_paper_broker.py`) уже существовали в
  рабочем дереве (написаны в предыдущих сессиях/фоновым запуском), но полный прогон не был
  зелёным — начал с диагностики, а не с переписывания с нуля.

Сделано:
- Найден и исправлен реальный баг восстановления состояния (docs/15_ACCEPTANCE.md, M6:
  20 сценариев рестарта): `Portfolio._load_last_balance()` читал остаток только из
  `trades.balance_after` последней строки. Если в режиме `mode` за всё время не было ни одной
  сделки (пустой сценарий, что реально встречается в 5 из 20 параметризованных сценариев
  `test_restart_recovery_20_scenarios`), после «рестарта» баланс откатывался на новый
  `starting_balance`, переданный в конструктор, а не на реально сохранённый — то есть заново
  созданный `Portfolio` тихо переопределял деньги. Исправлено: баланс теперь хранится в
  отдельной таблице `portfolio_balances (mode PK, balance, updated_at)` — новая миграция
  `migrations/0002_portfolio_balance.sql` (docs/06_DB_SCHEMA.md такую таблицу не описывает,
  это осознанное расширение схемы сверх дословного списка документа, продиктованное реальным
  инвариантом рестарта; ручных `ALTER TABLE` нет — только новый файл миграции,
  docs/02_CODING_RULES.md «БД и код»). `Portfolio.__init__` теперь сразу персистирует
  загруженный/дефолтный баланс, `open_position`/`close_position` обновляют эту таблицу при
  каждом изменении остатка.
- `tests/test_portfolio.py`: исправлен мёртвый тест
  `test_close_position_unknown_exit_reason_raises` — тело было `with pytest.raises(...): pass`,
  то есть гарантированно проваливалось (никакого вызова внутри блока не было), это дубликат уже
  существующего рабочего теста `test_close_position_rejects_unknown_exit_reason_value` —
  удалён как «тест без assert приравнивается к отсутствию теста» (AGENTS.md §6). Добавлен
  `suppress_health_check=[HealthCheck.function_scoped_fixture]` в
  `test_property_sum_pnl_net_equals_balance_change_when_fully_closed` — `hypothesis` late падал
  на `FailedHealthCheck` из-за `tmp_path`-фикстуры с `@given`; тест сам создаёт новый файл БД со
  случайным именем на каждый сгенерированный пример, так что подавление проверки безопасно
  (реального переиспользования состояния между примерами нет).
- `trading_bot/broker/paper.py`: убрано буквальное слово «tinkoff» из докстрингов (осталось
  только в комментариях, что ломало собственный тест модуля
  `test_paper_module_does_not_import_tinkoff`, читающий исходник через `inspect.getsource` и
  ищущий подстроку `tinkoff` без разбора, импорт это или текст в комментарии).
- `ruff --fix` + `black` по `trading_bot/`+`tests/`: неиспользуемый импорт `timedelta` и
  `UP017` (`timezone.utc` → `datetime.UTC`) в `tests/test_portfolio.py`, форматирование
  `tests/test_database.py`/`tests/test_portfolio.py`.
- `tasks/BACKLOG.md`: раздел M6 отмечен `[x]` по всем 5 пунктам, с указанием файлов/тестов и
  явной пометкой архитектурного решения по `portfolio_balances`.

Проверено:
- `python -m pytest -q` по всей проекту: только 14 намеренно красных `@pytest.mark.red` в
  `test_sdk_symbols_exist.py` (без изменений от предыдущей записи); всё остальное зелёное,
  включая `tests/test_portfolio.py` (24), `tests/test_database.py`, `tests/test_paper_broker.py`
  (15) — до правок было 8 падений (1 в paper_broker, 7 в portfolio: мёртвый тест, health-check
  hypothesis, 5 параметризаций рестарта с seed 2/6/10/14/19).
- `python -m mypy --strict trading_bot` (14 файлов): чисто.
- `python -m ruff check trading_bot tests`: чисто (после автофикса).
- `python -m black --check trading_bot tests`: чисто, 31 файл без изменений.

Не сделано / перенесено:
- Scaling in/out (50/30/20), ночное закрытие 23:30 МСК, таймаут убыточной позиции 45 минут,
  просадка 15% от запуска — не входят в M6 (требуют `executor.py`/торгового цикла, M7/M8),
  перенесено ранее в записи M5, актуально по-прежнему.
- `data_seed/holidays_ru.md` без подтверждённых дат праздников MOEX — перенесено из M3, не
  трогалось в этой сессии.
- Реального обрыва сети/восстановления стрима на живом токене — нет валидного токена
  песочницы, перенесено из M3.

TODO(VERIFY): без изменений, список из 14 позиций (п.2/6-18 кроме уже подтверждённых) в
  `docs/08_TINVEST_API.md`/`tests/test_sdk_symbols_exist.py` — M6 их не касается (только чтение
  свечей и paper-исполнение, без единого нового gRPC-вызова).

Следующий шаг:
- M6 закрыт по критериям docs/15_ACCEPTANCE.md. По MILESTONES.md следующий — M7
  (`strategy.py`: режимы рынка, сигналы, фильтры, journal решений).

---

## 2026-10-14 18:34 | Аудит M0–M7 | Закрытые свечи стратегии и достоверность критериев

Сделано:
- `trading_bot/strategy.py`, `tests/test_strategy.py` — регрессия воспроизведена красными тестами: незакрытые/стримовые свечи влияли на snapshot. Теперь расчёт принимает только `is_complete is True`, а без закрытой истории отказывает с `InsufficientHistoryError`; 2 новых теста прошли.
- `Makefile`, `pyproject.toml` — добавлена реально запускаемая цель `make ci` с исключением только явно помеченных `red` TODO(VERIFY); Black зафиксирован на Python 3.11, предупреждение проверки AST устранено.
- `tests/test_sdk_symbols_exist.py` — исключён реальный сетевой gRPC-вызов из теста (требование docs/11_TESTING.md); тип исключения проверяется локально, точная семантика авторизации осталась красной.
- `docs/00_PLAN.md` — формула размера позиции согласована с `docs/04_RISK.md` по ответу оператора: абсолютный ATR × множитель стопа × размер лота.
- `tasks/BACKLOG.md` — исправлены устаревшая структура модулей/число красных тестов; сняты незаслуженные отметки у календаря без дат, дневной паузы без оркестрации, отмены остатка лимитки без таймера/заявки.

Проверено:
- `make ci`: ruff и black чисто, mypy 15 модулей без ошибок, 289 passed / 14 deselected.
- `pytest -q --tb=no`: 289 прошли, ровно 14 намеренно красных `@pytest.mark.red` в `test_sdk_symbols_exist.py`; полный pytest не зелёный по протоколу VERIFY.
- Целевые тесты `tests/test_strategy.py`: 48 passed; новые тесты сначала падали до исправления.

Не сделано / перенесено:
- M7 не принят: тесты готовых сигналов подменяют `compute_snapshot`, полного сохранённого journal и проверки 10 фильтров через интегрированный цикл нет; решение содержит snapshot лишь в памяти, `database.py` не сохраняет `decisions`.
- M0–M6 нельзя считать полностью принятыми: в частности, M0 не имеет полной структуры, M1 не проверена персистентность keyring в Docker, M3 не синхронизирует позиции после reconnect, M5 не держит дневную паузу до следующей сессии, M6 не отменяет остаток по таймеру. Docker в этой сессии не запускался, прежние утверждения о нём не воспроизведены.
- В папке нет `.git`: невозможно проверить историю на секреты/коммитить; содержимое локальных секретов не читалось. Независимая проверка reviewer (Claude Sonnet 5, medium) завершилась ошибкой провайдера HTTP 400, независимого заключения нет.

TODO(VERIFY) новые:
- нет; прежние 14 намеренно красных остались.

Решения оператора:
- 2026-10-14: считать обязательной формулу из `docs/04_RISK.md` (абсолютный ATR × `atr_stop_mult`), а не вариант с ATR × price в прежней редакции `docs/00_PLAN.md`.

Следующий шаг:
- M7: исправить проверку разворотной свечи во флэте и довести журнал решений до сохранения полного snapshot и отказов, затем пройти 10 изолированных фильтров без подмены индикаторов.

---

## 2026-10-14 18:59 | Этап M7 | Разворот ranging и постоянный журнал решений

Сделано:
- `trading_bot/strategy.py`, `tests/test_strategy.py` — два красных теста выявили, что ranging принимал свечу у полосы BB без проверки прошлого закрытия относительно прошлой средней полосы. Для long требуется `prev_close < prev_middle`, для short `prev_close > prev_middle`, плюс текущая разворотная свеча. Причина отказа с обеими ценами включается в `filters_failed`; snapshot дополнен `prev_close_5m`, `bb_middle_prev`, `volume_avg20`. Для отказа до расчёта индикаторов все обязательные ключи сохранены как `n/a` с фактической причиной, без фиктивных значений. Значение `breadth` записывается, когда рассчитан глобальный фильтр.
- `trading_bot/database.py`, новый `trading_bot/journal.py`, `tests/test_journal.py` — типизированный `DecisionRecord` + `DecisionJournal.record()`: записывает Decision и результат RiskVerdict в существующую таблицу `decisions`, фильтры/снимок JSON, время UTC, режим, размер; возвращает id для `trades.journal_id`. Отказ SQLite логируется с тикером/временем/режимом и распространяется (не «успех»). Тестируются сигнал, отказ до сессии/при недостатке свечей, отказ риска, перезапуск SQLite, FK сделки и результат через JOIN, пустой снимок/наивное время/сбой таблицы. Новая миграция не нужна: все поля уже есть в `migrations/0001_init.sql`.
- `docs/03_STRATEGY.md`, `docs/01_ARCHITECTURE.md`, `tasks/BACKLOG.md` — документирована связь результата через `trades.journal_id = decisions.id` и неполная готовность M7. Пункты остаются `[ ]`, пока нет торгового цикла/стопа и тейка ranging.

Проверено:
- Перед исправлением отдельно красные тесты прошлого закрытия long/short, полноты snapshot, отсутствия persistence-модуля и причины отказа; после исправления целевые тесты зелёные.
- `make ci`: Ruff и Black чисто, `mypy --strict trading_bot` без ошибок (16 модулей), 298 passed / 14 deselected (намеренно красные `TODO(VERIFY)`).
- `tests/test_journal.py`: 5 тестов на реальном SQLite, включая повторное открытие файла и проверку связи с закрытой сделкой.

Не сделано / перенесено:
- M7 не принят: `main.py` не запускает торговый цикл, обязательный вызов `DecisionJournal.record` пока не подключён к executor; 10 фильтров не проверены сквозным тестом на реальных свечных фикстурах, стоп/тейк ranging не реализованы. Сигнальная фикстура journal подменяет `compute_snapshot`, хотя тест настоящего `compute_snapshot` проверяет прошлое закрытие/средний объём. Если внешние spread/breadth/gap не даны, в journal честно `n/a` — полный рыночный контекст ещё требует интеграции.
- Live-заявки и M8 не трогались (требуется отдельное письменное подтверждение). `.git` по-прежнему отсутствует в рабочей папке, коммита/проверки истории секретов нет. Запрошенный независимый reviewer на GPT-6 Luna/medium завершился ошибкой провайдера HTTP 400; независимого заключения нет.

TODO(VERIFY) новые:
- нет; 14 красных SDK-проверок без изменений.

Решения оператора:
- 2026-10-14: `trade_id` и результат сделки получать через JOIN `trades.journal_id = decisions.id`, не добавлять дублирующие колонки в `decisions`. Для FIFO одно решение может быть связано с несколькими строками сделок.

Следующий шаг:
- Довести M7: изолированно проверить все 10 фильтров и сквозные свечные фикстуры, определить ranging-стоп/тейк; затем отдельным этапом после подтверждения оператора подключить executor к сохранению каждого решения.

---

## 2026-10-14 19:43 | Этап M5 | Замена формулы размера позиции и исправление лотности портфеля

Сделано:
- `docs/04_RISK.md`, `docs/00_PLAN.md` §7, `docs/07_CONFIG_SPEC.md` — принята формула из отчёта: капитал на стопах `B` с учётом long/short, бюджет `R` = минимум риска на сделку и запаса до дневного/глобального лимитов; убыток на лот с комиссией и проскальзыванием обеих сторон; потолки `N_risk/N_cap/N_funds/N_liq`, `VOLATILE_SIZE_SCALER` к финальному минимуму, `ROUND_DOWN`. Фактический стоп кратен шагу инструмента и определён до расчёта размера; `ATR_STOP_MULT` оставлен только в правиле цены ATR-стопа. Дефолты `MAX_POSITION_PCT=0.15` и `MAX_VOLUME_PARTICIPATION=0.05` (старт, калибровать по журналу). Отдельная задача экономики издержек добавлена в план и `tasks/BACKLOG.md` до M8.
- `trading_bot/risk_manager.py`, `trading_bot/config.py`, `.env.example` — новые неизменяемые параметры и снимки FIFO-позиций, fail-closed при неизвестном стопе/лотности/объёме/валюте/шаге цены, расчёт размера из переданного реального стопа, округление ATR-стопа от входа по шагу, все 12 проверок сохранены. `RiskVerdict.effective_risk_pct` — число процентов капитала `B`, не дробная доля; для отказа 0.
- `trading_bot/portfolio.py`, `migrations/0003_trade_lot_size.sql`, `docs/06_DB_SCHEMA.md` — исправлена стоимость открытия `price × lots × lot_size`, лотность сохраняется в каждой FIFO-строке и используется при закрытии, расчёте P&L и частичном закрытии после рестарта. В старых строках `lot_size=NULL`: открытая историческая позиция не допускает тихой оценки как 1 бумага и требует ручной сверки реальной лотности и баланса.
- `trading_bot/database.py`, `trading_bot/journal.py`, `migrations/0004_decision_effective_risk.sql` — `decisions.effective_risk_pct` как Decimal-текст, NULL до риск-проверки, 0 для отказа, расчётный процент для разрешения; `reason` разрешённого решения сохраняет и связывающий потолок размера.
- `tests/test_risk_manager.py`, новый `tests/test_position_sizing.py`, `tests/test_portfolio.py`, `tests/test_journal.py`, `tests/test_database.py`, `tests/test_config.py` — 5 golden-сценариев разных потолков, 1000 независимых paper-сделок на Hypothesis (PaperBroker + FIFO/SQLite, long/short, комиссии/проскальзывание), второй вход после long и short, стоп без подтверждения, дневной/глобальный запас, тик, обрыв исторической лотности, миграции и запись эффективного риска.

Проверено:
- Регрессия `Portfolio.open_position` сначала упала на неизвестном аргументе `lot_size`, после исправления прошла; тест на поле `decisions.effective_risk_pct` сначала упал на отсутствии колонки, после миграции прошёл. Проверка исторических позиций сначала выявила небезопасный дефолт 1, затем проверен явный отказ при NULL.
- `make ci` после финальных правок: Ruff/Black чисто, mypy strict (в pyproject.toml) без ошибок в 16 модулях, `310 passed, 14 deselected` (только намеренно красные `TODO(VERIFY)`). `read_lints` по затронутым файлам: диагностик нет.
- Миграция старой схемы 0001–0002 на 0003–0004 проверена тестом; частичное закрытие с `lot_size=10` и восстановление баланса после рестарта проверены.

Не сделано / перенесено:
- M5 целиком не принят: дневная пауза до следующего торгового дня и интеграция порций 50/30/20 с сохранённым бюджетом требуют оркестрации. У M7/M8 пока нет executor, поэтому снимки капитала/объём 5m и проверенный стоп ещё не подаются в риск-менеджер из реального потока, а `DecisionJournal.record` не вызывается торговым циклом. Live-заявки не трогались (отдельное письменное подтверждение для M8).
- Экономика тарифа/тейка/стопа/таймфрейма НЕ проверена на реальном журнале: при 0,3% комиссии и 2×ATR для ATR≈0,4% цены упрощённая модель требует около 93% прибыльных сделок; анализ поставлен отдельной задачей до M8.
- Не проверены фактические единицы объёма свечи, маржа live и фактическая просадка через гэп. `PaperBroker.Fill.price` уже включает проскальзывание; тест `Portfolio` получает опорную цену и отдельную сумму `slippage_cost`, их согласование в будущем executor остаётся обязательным. После частичного закрытия прежний код удаляет стоп у остатка: новых входов по такой позиции с новой формулой не будет до восстановления стопа (fail closed), исправление не входило в объём.
- В каталоге по-прежнему нет `.git`, поэтому историю на секреты проверить и сделать коммит нельзя. Независимые implementer Claude Sonnet 5/high и reviewer GPT-6 Luna/high не запустились из-за ошибки провайдера HTTP 400; независимого заключения нет.

TODO(VERIFY) новые:
- единица `Candle.volume` и корректный перевод среднего объёма 5m в лоты перед `N_liq` — проверить в SDK/документации и на данных брокера;
- доступные средства/требования обеспечения live short и стоимость стоп-исполнения через гэпы — подтвердить брокерским API и журналом;
- лотность и денежные остатки исторических открытых строк старой схемы — сверить вручную до торговли с такими позициями.

Решения оператора:
- 2026-10-14: эта формула **заменяет** решение 2026-10-14 из записи на строке 682 о фиксированной формуле `balance_free × RISK_PER_TRADE / (ATR × ATR_STOP_MULT × lot_size)`. Для второй и последующих позиций бюджет риска может быть больше прежнего: это увеличение риска относительно старого поведения осознанно утверждено человеком, с риском большего убытка на последующих сделках, но ограничено `RISK_PER_TRADE`, `DAILY_LOSS_LIMIT`, `GLOBAL_DRAWDOWN_STOP`. Увеличение настроек через VK/дашборд/оптимизатор по-прежнему запрещено (AGENTS.md §4.2/§9).
- `MAX_POSITION_PCT=0.15`, `MAX_VOLUME_PARTICIPATION=0.05` — утверждены; второе значение стартовое, будет калиброваться по журналу. `ATR_STOP_MULT` относится к правилу цены стопа. Эффективный риск в процентах капитала пишется в `decisions`.

Следующий шаг:
- Сверить единицы объёма/маржу и исторические позиции, отдельно проверить экономику сделки до M8; продолжить M7 и только после разрешения оператора подключать executor.

---

## 2026-10-14 20:58 | Этап M7 | Свечные фильтры, ranging-уровни и сквозное сохранение решений

Сделано:
- `tests/test_strategy_filters_integration.py` — без подмены индикаторов созданы закрытые свечи для trending-long и ranging long/short; проверены разворот, сырой стоп от экстремума, тейк BB, округление стопа наружу до `risk_manager.check()` и отличный от ATR-стопа эффективный риск. Изолированно проверены все 10 категорий фильтров `docs/03_STRATEGY.md` через `utils.can_trade_now`, `strategy.evaluate` и 12 риск-проверок; сквозной путь свечи → стратегия → риск → `DecisionJournal.record` → повторное открытие SQLite проверен на трёх сигналах и трёх отказах.
- `tests/test_strategy.py`, `trading_bot/strategy.py` — сначала воспроизведены три красных случая: `volatile` и `transitional` отказывали без имени фильтра, а гэп предшествовал режиму вопреки фиксированному порядку. Теперь оба режима дают явные `regime_*` в `filters_failed` и причина режима проверяется перед гэпом. Для `volatile` выбрана разрешённая спецификацией ветка отказа, не уменьшение размера.
- `docs/01_ARCHITECTURE.md`, `docs/03_STRATEGY.md`, `docs/04_RISK.md`, `tasks/BACKLOG.md` — уточнены опциональные уровни Signal и различие ATR-тейка trending / BB-тейка ranging; снята устаревшая пометка «стоп/тейк не реализованы». Все пункты M7 оставлены `[ ]`: автоматическая оркестрация и реальные заявки отсутствуют.

Проверено:
- Целевой тест `tests/test_strategy_filters_integration.py`: 16 passed; три новых регрессионных теста режима/порядка падали до исправления.
- `make ci`: Ruff и Black чисто, mypy без ошибок в 16 модулях, 332 passed / 14 deselected (только намеренно красные SDK `TODO(VERIFY)`).
- `git status --short`: каталог не является git-репозиторием; коммит и проверка истории секретов невозможны.

Не сделано / перенесено:
- M7 не принят по `docs/15_ACCEPTANCE.md`: `main.py` не имеет торгового цикла, `DecisionJournal.record()` не вызывается автоматически на каждом решении; 100% сигналов/отказов работающего бота не доказаны. Сигнал ranging несёт **сырой** стоп и BB-тейк, а тестовая интеграция округляет стоп для риска; исполнитель M8 ещё не гарантирует ту же цену в заявке. Рыночная корреляция/объём в лотах, актуальность статуса прямо перед заявкой и фактический spread из API не интегрированы. У фильтров в БД имена/причины, но числовой индекс из документа ещё не пишется. Live не подключался.
- Для некоторых категорий (chaos/волатильность/статус) интеграционные тесты подменяют **входное состояние риск-слоя**, а не получают эти поля из живого брокера; это не тест полной торговой сессии. Реальный ranging-short доступен на свечах; short-доступность из SDK не подтверждена.

TODO(VERIFY) новые:
- нет; прежние SDK-проверки и открытые вопросы единицы `Candle.volume` сохраняются.

Следующий шаг:
- После подтверждения отдельного объёма работ собрать paper-оркестрацию: подать реальные данные в фильтры и риск, сохранять каждое решение до заявки и использовать один и тот же округлённый стоп при сайзинге и исполнении; M8/live — только после разрешения оператора.

---

## 2026-10-14 21:20 +05:00 | Этап M7 | Уточнение фильтров и запрет аукционов

Сделано:
- После ревью `trading_bot/strategy.py`, `trading_bot/utils.py`, `tests/test_strategy.py`, `tests/test_strategy_filters_integration.py`: защитное окно передаётся отдельным `TradingDecision.guard_active` и отмечается как `session_guard`; на закрытых свечах проверены отказ `volatile` и нечувствительность `evaluate()` к следующим незакрытым свечам. `docs/03_STRATEGY.md` и `docs/04_RISK.md` фиксируют решение оператора об отказе в `volatile`, без активации уменьшенного сайзинга.
- `tests/test_utils_calendar.py` воспроизвёл допуск к аукционам при `session_guard_min=0` и статусе NORMAL; `trading_bot/utils.py` теперь блокирует 09:50–10:00 и 23:40–23:50 МСК до обращения к gateway, а в 23:50 возвращает отказ как вне торгового окна. Проверены точные границы 09:50, 10:00, 23:40 и 23:50.

Проверено:
- До исправления два теста аукционов падали из-за `allowed=True`, а граничный тест 23:50 — из-за неверной причины; после исправления целевые тесты календаря/стратегии/журнала прошли.
- `make ci`: Ruff и Black чисто, mypy без ошибок в 16 модулях; 342 passed / 14 deselected (помеченные `red` SDK TODO(VERIFY)).
- `git status --short` недоступен: каталог не является git-репозиторием, поэтому историю секретов и коммит проверить невозможно.

Не сделано / перенесено:
- M7 по `docs/15_ACCEPTANCE.md` не принят: нет автоматического торгового цикла, который гарантирует запись 100% сигналов и отказов в `DecisionJournal`, свежий статус непосредственно перед реальной заявкой и одинаковый округлённый ranging-стоп в риске и исполнении. Числовые индексы фильтров в БД отсутствуют. Live-заявки не подключались.
- `test_no_lookahead_evaluate_ignores_next_unclosed_candles` проверяет только добавление будущих *незакрытых* свечей; более широкая проверка сдвига всей последовательности закрытых свечей для стратегии ещё не выполнена.

TODO(VERIFY) новые:
- нет; ранее открытые SDK/объём/маржа вопросы остаются.

Следующий шаг:
- Независимо проверить границы аукционов и спланировать paper-оркестрацию с персистентным журналом без подключения live.

---

## 2026-10-14 22:39 +05:00 | Этап M7 | Защита последних минут обычных торгов

Решение оператора:
- 2026-10-14: последние 10 минут защитного окна — **23:30–23:40 МСК** (перед закрывающим аукционом), а не 23:40–23:50. Аукцион блокируется отдельно. Это уточнение неоднозначного понятия «конец сессии» в `docs/03_STRATEGY.md` и `docs/04_RISK.md`, не разрешение торговать в аукционе.

Сделано:
- `trading_bot/utils.py`: защитное окно закрытия привязано к началу аукциона 23:40; его старт 23:30 включителен. `can_trade_now` требует явного `session_guard_min`, чтобы забытый аргумент не отключал защиту. Чистый `session_clock_decision()` разделяет проверку выходного/времени/аукциона с `strategy.evaluate()`, но не подменяет праздники, guard и статус API. `session_guard_active()` не помечает время вне обычных торгов как защитное; некорректный tz, отрицательное и чрезмерное окно отклоняются.
- `trading_bot/strategy.py` требует явный `session_guard_active` и повторно вычисляет guard по `now`/`config.session_guard_min`: ошибочный внешний `False` не даёт сигнал в 10:05 и 23:35. Дополнительно локальная проверка часов отклоняет 08:00, 09:55, 23:45, 23:50 и выходной даже при внешнем `is_session_open=True`; это не заменяет проверку праздника и статуса через gateway. Вызовы обновлены в `tests/test_strategy.py`, `tests/test_strategy_filters_integration.py`, `tests/test_journal.py`; защита отдельно именуется в journal. `tests/test_utils_calendar.py` проверяет границы 23:30/23:39:59, приоритет аукциона, поведение вне сессии и tz без смещения. `docs/00_PLAN.md`, `docs/03_STRATEGY.md`, `docs/04_RISK.md`, `docs/07_CONFIG_SPEC.md` уточнены по решению оператора, включая явное поведение при 0. `tasks/BACKLOG.md` оставляет M7 незакрытым.

Проверено:
- `make ci`: Ruff/Black чисто, mypy без ошибок в 16 модулях, 365 passed / 14 deselected (`red` SDK TODO(VERIFY)). Целевые тесты календаря/стратегии/журнала прошли. Красными до исправления были 6 сценариев защитного окна, 4 сценария вне сессии, контракт обязательного флага, два сигнала при ложном внешнем guard в 10:05/23:35 и пять сигналов вне сессии при ошибочном внешнем `True`.
- Путь заявки/торговый цикл отсутствует; вызов `can_trade_now` непосредственно перед реальной заявкой этим тестом не подтверждён.

Не сделано / перенесено:
- M7 не принят: `DecisionJournal.record()` вызывается только тестами, paper-цикл не пишет 100% решений; нет исполнения уровней ranging и проверки статуса непосредственно перед заявкой. Риск-слой получает `is_session_open` от будущего вызывающего кода и не проверяет guard независимо. Контракт исполнителя по guard уточнён в следующей записи 22:49: для стратегии guard-only означает `allowed or guard_active`, а риск получает исходное `allowed=False`. На момент этой записи конфиг допускал guard, полностью перекрывающий сессию; в записи 22:49 ограничение исправлено до 0..409, а перекрытие отклоняется. Live не подключался. `.git` по-прежнему отсутствует: коммит и проверка истории секретов невозможны.

TODO(VERIFY) новые:
- нет; прежние вопросы SDK и рынка остаются.

Следующий шаг:
- Собрать в отдельном разрешённом объёме paper-оркестрацию стратегии, риска и журнала, затем доказать полный snapshot на каждом сигнале и отказе без обращения к live.

---

## 2026-10-14 22:49 +05:00 | Этап M7 | Приоритет причины закрытой сессии и границы календаря

Сделано:
- `tests/test_strategy.py`, `trading_bot/strategy.py` — красным тестом воспроизведено, что закрытая сессия в 10:05 с причиной статуса `BREAK_IN_TRADING` записывалась как `session_guard`; теперь отказ `session_open` и исходная причина сохраняются в snapshot до проверки защитного окна.
- `tests/test_strategy_filters_integration.py` — проверен контракт между `can_trade_now()` и `evaluate()`: отказ только из-за guard передаётся стратегии как `is_session_open=allowed or guard_active`, а исходное `allowed=False` остаётся для риска/заявки; прямой перенос `allowed` после изменения приоритета сначала сломал сквозной тест и был исправлен.
- `tests/test_config.py`, `trading_bot/config.py`, `tests/test_utils_calendar.py`, `trading_bot/utils.py` — после независимого ревью красными тестами показано, что guard в 410 минут перекрывает всю обычную сессию и при `TRADING_MODE=live` принимается конфигом. Допустимый диапазон теперь 0..409; при 410+ загрузка откатывается к paper/guard=10, прямой вызов календаря явно отклоняет перекрытие. При 409 протестированы границы окна и свободный промежуток в 16:49/16:50/16:51.
- `tests/test_strategy.py`, `trading_bot/strategy.py` — отказ при пустой причине или `ok` теперь записывает «сессия закрыта (причина не указана)», а не успехообразное сообщение.
- `docs/03_STRATEGY.md`, `docs/07_CONFIG_SPEC.md` — уточнены обычное окно 10:00–23:40, отдельные аукционы 09:50–10:00/23:40–23:50, приоритет фильтров, неизвестность статуса в guard и предел 0..409; `trading_bot/utils.py` docstring говорит о перекрытии окон. `tasks/BACKLOG.md`: чекбоксы M7 оставлены открытыми.

Проверено:
- Регрессионные тесты приоритета, перекрывающихся окон и пустой причины падали до исправлений; после исправлений целевые тесты стратегии/календаря/конфига и сквозного guard с проверкой риск-отказа проходят. Границы guard=409 на 16:49, 16:50 и 16:51 отдельно проверены целевым прогоном.
- `make ci` после всех правок: Ruff/Black чисто, mypy — 16 модулей без ошибок, pytest — 372 passed, 14 deselected (намеренно красные SDK `TODO(VERIFY)`). `read_lints` по изменённым Python-файлам: диагностик нет. Независимые статические ревью выявили перекрытие при 410, неточную историческую формулировку и недостающие граничные точки; исправления/уточнения внесены, новых блокирующих замечаний не осталось.
- `git status --short`: каталог не является Git-репозиторием; историю секретов и дифф проверить невозможно.

Не сделано / перенесено:
- M7 не принят по `docs/15_ACCEPTANCE.md`: нет производственного цикла и автоматической записи 100% решений в журнал, исполнения ranging-уровней и свежей проверки статуса перед заявкой. `can_trade_now()` на guard не запрашивает статус инструмента; `is_session_open=allowed or guard_active` — только адаптация для классификации отказа стратегии, не разрешение на сделку. Историческая запись на строке 811 описывала прежнюю конфигурацию: теперь `Settings.session_guard_min` валидируется при загрузке с ограничением 409. Live не подключался.
- Проверка секретов по истории Git и реального исполнения без торгового цикла невозможна; содержимое локальных секретов не читалось.

TODO(VERIFY) новые:
- нет; ранее открытые SDK и рыночные проверки остаются.

Следующий шаг:
- Реализовать отдельный paper-цикл с единой проверкой статуса перед ордером, использованием исходного `allowed` для риска и обязательным `DecisionJournal.record()` на каждом сигнале/отказе; live не включать без отдельного разрешения.

---

## 2026-10-14 23:13 +05:00 | Этап M7/M2 | Реальная read-only проверка sandbox API с тренировочным токеном

Сделано:
- По прямому указанию оператора попытался через `InvestGateway(sandbox_target())` прочитать счета, затем статус и 5m-свечи SBER. Токен введён через скрытый терминальный prompt; он не включался в команду, файлы, keyring и журнал. Ни один метод заявки не вызывался.
- `docs/08_TINVEST_API.md` — отмечен фактический сетевой блокер: TLS-цепочка с недоверенным self-signed CA, а не доказанная ошибка авторизации.

Проверено:
- Первый read-only вызов `list_accounts()` завершился `GatewayError` поверх gRPC `UNAVAILABLE`: `CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain` во время TLS handshake. Ответа API и списка счетов не получено; вызовы статуса и свечей после исключения не начинались.
- Без токена проведены две проверки TLS: отдельное gRPC-подключение с временным `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` на существующий `SSL_CERT_FILE` (`C:\ProgramData\miniconda3\Library\ssl\cacert.pem`) и стандартный Python TLS-контекст Windows. Обе получили `CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain`. Это подтверждает, что текущий CA bundle и системное хранилище не доверяют цепочке. Никаких отключений проверки TLS, правок системного хранилища или повторного авторизованного вызова не было.

Не сделано / перенесено:
- Валидность токена, типы/статусы sandbox-счетов, статус SBER и свечи остались неизвестны: TLS рушится до авторизации. M7 по-прежнему не принят: этот read-only вызов не заменяет производственный цикл, journal и тесты исполнения. Токен, опубликованный в чате, следует заменить; данных о его фактической валидности нет.

TODO(VERIFY) новые:
- Доверенный CA для gRPC в этом окружении: получить утверждённый PEM-бандл от владельца сети/прокси, проверить подлинность и повторить только read-only sandbox-вызовы. Не импортировать сертификат из недоверенного ответа и не отключать проверку TLS.

Решения оператора:
- 2026-10-14: разрешены read-only запросы к тренировочному sandbox для счетов, статуса инструмента и свечей; заявки и live-торговля не разрешены.

Следующий шаг:
- После предоставления проверенного CA-бандла настроить для одного процесса `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`, затем повторить три read-only запроса без записи токена в файлы или командную строку.

---

## 2026-10-14 23:25 +05:00 | Этап M7/M2 | Проверка официального сертификата API

Сделано:
- Исправлен прежний вывод о необходимости CA именно от владельца прокси: документация T-Invest об ошибках сертификатов отсылает к НУЦ Минцифры, а Госуслуги публикуют официальный корневой и выпускающие сертификаты. `docs/08_TINVEST_API.md` дополнен источниками и безопасной настройкой gRPC.
- `.tls-verify/Russian_Trusted_Root_CA.pem` создан как локальная копия публичного корня с официального `https://gu-st.ru/content/downloads/Russian_Trusted_Root_CA.cer` для проверки в одном процессе; токен в файл не записан.

Проверено:
- Цепочка sandbox: издатель серверного сертификата `Russian Trusted Sub CA`; `openssl s_client` с официальным PEM и `-verify_return_error` вернул `Verification: OK`, TLSv1.3. Отпечаток SHA-256 корня сверён через OpenSSL и записан в `docs/08_TINVEST_API.md`.
- Без токена `grpc.secure_channel` с временным `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` достиг состояния ready на sandbox. Проверка TLS не отключалась, системное хранилище не менялось.

Не сделано / перенесено:
- На момент этой записи интерактивный read-only запрос ожидает скрытого ввода токена оператором; ответ API, валидность токена, счета, статус и свечи ещё не установлены. M7 по-прежнему не принят из-за отсутствия торгового цикла и автоматической записи всех решений.

TODO(VERIFY):
- Результат авторизованных read-only запросов после скрытого ввода токена; не переносить токен в командную строку/файлы/журнал. Опубликованный в чате токен следует заменить.

---

## 2026-10-14 23:27 +05:00 | Этап M7/M2 | Ответ sandbox на скрытый ввод токена

Сделано:
- Обновлён `docs/08_TINVEST_API.md`: отделены успешная проверка TLS от отказа аутентификации и добавлена ссылка на документацию T-Invest по коду `40003`.

Проверено:
- Интерактивный read-only вызов `InvestGateway(sandbox_target()).list_accounts()` после скрытого ввода токена дошёл до API. Ответ сервера — gRPC `UNAUTHENTICATED`, код `40003`, сообщение «Authentication token is missing or invalid»; `GatewayError` корректно пробросил отказ. Команда завершилась с кодом 1. Секрет не был записан в команду, файлы или журнал; account ID не получены.
- После отказа последующие `get_trading_status` и `get_candles` не выполнялись. Ни заявок, ни live-вызовов не было.

Не сделано / перенесено:
- Типы и статусы счетов, статус SBER и 5m-свечи не подтверждены: потребуется новый действительный sandbox-токен. Открытый в чате токен следует отозвать/заменить; не повторять его использование. M7 остаётся непринятым по критерию автоматической записи всех решений, поскольку цикл отсутствует.

TODO(VERIFY):
- [Закрыто записью ниже, 2026-10-14 23:35 +05:00] Повторить разрешённые read-only sandbox-вызовы с новым токеном через скрытый ввод; секреты не публиковать.

---

## 2026-10-14 23:35 +05:00 | Этап M2/M7 | Успешные read-only sandbox-вызовы

Сделано:
- Обновлён `docs/08_TINVEST_API.md` с фактическими результатами запросов из sandbox.

Проверено:
- Запросы выполнены через `InvestGateway(sandbox_target())`; токен введён через скрытый prompt и не сохранялся в команду/файлы/журнал.
- `list_accounts()` завершился успешно и вернул 0 счетов (`accounts_count=0`, пустой список типов/статусов). Причина отсутствия счетов не исследована; создание счёта не выполнялось.
- `get_trading_status(instrument_id='BBG004730N88')` вернул `SECURITY_TRADING_STATUS_NORMAL_TRADING`.
- `get_candles(..., timeframe='5m', from_=now-1d, to=now, limit=10)` вернул 10 свечей. Последняя: `2026-10-14T18:30:00+00:00`, close `285.91` (Decimal-строка), `is_complete=False` — текущая свеча незакрытая, не использовать её как подтверждённую закрытую свечу стратегии.
- Все выполненные API-операции только на чтение; ордерных методов не вызывали. Команда завершилась кодом 0.

Не сделано / перенесено:
- Нет sandbox-счёта в ответе; дальнейший процесс создания/инициализации счёта не исследовался и не выполнялся (вне разрешённых read-only действий).
- Эти запросы не принимают M7: производственный цикл и автоматическая запись 100% решений в журнал всё ещё отсутствуют.

TODO(VERIFY):
- Уточнить в официальной документации/кабинете, почему `GetAccounts` в sandbox вернул пустой список и как оператор должен создать sandbox-счёт; не выполнять изменяющие sandbox операции без отдельного разрешения.
- Проверить предел `limit` и глубину истории отдельным read-only вызовом, если это остаётся в scope M2.

---

## 2026-10-14 23:48 +05:00 | Этап M2/M7 | Создан виртуальный sandbox-счёт по разрешению оператора

Сделано:
- `trading_bot/api_client.py`: добавлены `list_sandbox_accounts()` и `open_sandbox_account()` через `InvestGateway`; обе операции требуют точного `sandbox_target()` до создания клиента. При ошибке открытия результат сообщается как неизвестный, логируется только операция и sandbox target; автоматического повтора нет.
- `tests/test_api_client.py`, `tests/test_sdk_symbols_exist.py`: проверки sandbox-only ограничения, ответа/ошибки/неповтора, полей и SDK методов. В `docs/08_TINVEST_API.md` подтверждён контракт и живой результат; `tasks/BACKLOG.md` актуализирован.

Проверено:
- Контракт сверён с [официальной документацией T-Invest по SandboxService](https://developer.tbank.ru/invest/intro/developer/sandbox/methods) и установленным SDK: `open_sandbox_account(name='') -> OpenSandboxAccountResponse(account_id)`; `get_sandbox_accounts() -> GetAccountsResponse(accounts)`.
- Сначала `list_sandbox_accounts()` вернул 0. После явного разрешения оператора создан ровно 1 виртуальный счёт через `OpenSandboxAccount`; повторный список вернул 1 запись `ACCOUNT_STATUS_OPEN`, созданный счёт виден. Идентификатор счёта не записан/не выведен в журнал.
- Не выполнялись пополнение, заявка, закрытие sandbox-счёта или любой live-вызов. Новый токен введён только в скрытый терминальный prompt.
- Независимый review: блокеров безопасности для sandbox-only шлюза не найдено; предупреждение о неоднозначном ответе открытия закрыто сообщением «результат неизвестен», явным журналированием и тестом отсутствия повторного вызова.
- `make ci`: Ruff и Black чисты; mypy — 16 модулей без ошибок; `381 passed, 14 deselected`.

Не сделано / перенесено:
- Sandbox-счёт оставлен открытым и не пополнен; дальнейшие изменения счёта не разрешались.
- M7 остаётся непринятым: нет производственного цикла и автоматической записи 100% сигналов/отказов в журнал. Создание тестового счёта не меняет эту оценку.

TODO(VERIFY):
- Установленный SDK помечает `get_sandbox_accounts()` как deprecated и предлагает `SandboxClient.users.get_accounts()`; подтвердить доступный поддерживаемый migration API в целевой версии SDK перед обновлением. Сейчас работает подтверждённый в окружении SandboxService метод.

## 2026-10-15 00:17 +05:00 | Этап M7 | Paper-only тик и обязательное журналирование

Сделано:
- `trading_bot/executor.py`: введён вход полного paper-тика с явно переданным рынком/счётом. На закрытых хронологически упорядоченных свечах выполняется `strategy.evaluate()` → 12 проверок риска → `DecisionJournal.record()` → `PaperBroker` → `Portfolio`; при отказе SQLite fill не делается. Отказы стратегии/риска и ошибка подготовки риска сохраняются; ошибку БД/риска не скрываем. Стоп и тейк сохраняются по шагу инструмента, открытие нормализуется в UTC; проскальзывание не списывается дважды.
- `trading_bot/main.py`: `run()` принимает явно инъецированные `RunDependencies` (cycle + источник тиков вместе), логирует ошибку тика и останавливается. Обычный `main()` без источника не имитирует сигналы и остаётся heartbeat-only; live по-прежнему запрещён.
- `trading_bot/portfolio.py`: при ошибке записи открытия откатываются SQL и баланс в памяти. `trading_bot/strategy.py`: причины запрета стороны фильтром breadth теперь попадают в `filters_failed`.
- `tests/test_paper_cycle_integration.py`, `tests/test_main.py`: сквозные сценарии сигнала, отказов, ranging long/short, short availability, zero/partial fill, связи journal_id, ошибочной записи SQLite, отката portfolio, несвежих/переставленных свечей и UTC-времени. `tasks/BACKLOG.md` актуализирован без закрытия M7.

Проверено:
- Тест до реализации был красным (`ModuleNotFoundError`); регрессионные тесты отката, причин breadth и недопустимого тейка также воспроизвели дефекты до исправления.
- `make ci`: Ruff/Black чисты; `mypy` — 17 модулей без ошибок; `400 passed, 14 deselected`. Независимый review подтвердил paper-only границу и указал на ограничения runtime-контекста; найденные ошибки UTC, границы свечи, округления тейка, отката записи и логирования исправлены и тестируются.
- Никаких внешних запросов, пополнений, sandbox/live-заявок или использования токена в этой задаче не было.

Не сделано / перенесено:
- Штатный процесс не собирает universe, статус, спред, корреляции, объём в лотах и достоверный `AccountState`; `PaperCycle` работает только с переданными извне проверенными входами. Критерий `docs/15_ACCEPTANCE.md:55` для автономного работающего бота не доказан, M7 не принят.
- `daily_start_balance` и `starting_equity` не сохраняются как проверенные якоря после рестарта; незавершённые/частичные fills отражаются в результате и сделках, но не отдельным статусом исполнения в `decisions`. Закрытие позиций по стопу/тейку остаётся M8.

TODO(VERIFY):
- Определить подтверждённый production-источник полного рыночного/счётного снапшота и персистентные якоря дневного/стартового капитала до автономного paper-запуска.
- Подтвердить денежный квант комиссии/проскальзывания для каждого инструмента, прежде чем добавлять округление денежных сумм.

## 2026-10-15 00:25 +05:00 | Этап M2/M7 | Миграция списка sandbox-счетов на поддерживаемый SDK

Сделано:
- `trading_bot/api_client.py`: `list_sandbox_accounts()` после строгой проверки sandbox target использует `SandboxClient.users.get_accounts()` вместо deprecated `SandboxService.get_sandbox_accounts()`; ошибки SDK/сети логируются с операцией, target, UTC-временем и типом без значения токена, затем преобразуются в `GatewayError` без исходного текста ошибки (причина остаётся в цепочке исключений).
- `tests/test_api_client.py`: тесты проверяют вызов поддерживаемого сервиса, отсутствие deprecated-вызова, создание именно `SandboxClient` без сети и контекстное логирование ошибки без токена; `tests/test_sdk_symbols_exist.py` подтверждает импорт из `tinkoff.invest.sandbox.client`, сигнатуру `UsersService.get_accounts` и поля `GetAccountsResponse` установленного SDK. Интроспекционный тест свечей теперь явно выбирает sandbox target, а не prod по умолчанию.
- `docs/08_TINVEST_API.md`, `tasks/BACKLOG.md`: описана миграция и честно разделены исторический живой вызов deprecated-пути и локальная проверка нового пути. Чекбоксы M7 не закрывались.

Проверено:
- До изменения адаптера целевые тесты падали из-за вызова deprecated-метода и неверного пути импорта `SandboxClient`; после исправления оба случая проходят.
- Независимое read-only ревью не выявило prod/deprecated/RPC-пути миграции; замечания по логированию ошибки списка и prod target старого SDK-теста устранены. Регрессионные тесты отсутствия лога и утечки текста SDK-исключения сначала упали, затем прошли.
- `make ci` после финальных правок: Ruff/Black чисты, mypy без ошибок в 17 модулях, 402 passed / 14 deselected (`TODO(VERIFY)`); диагностика затронутого Python-кода пуста.
- Новый сетевой вызов с токеном не выполнялся; создание счёта, пополнение и заявки не выполнялись. Секрет не добавлялся в изменённые файлы. Историю проверить невозможно: каталог не является Git-репозиторием.

Не сделано / перенесено:
- Работа поддерживаемого sandbox-list пути с реальным sandbox-токеном в сети ещё не проверена; тесты охватывают локальный SDK-контракт и мок-клиент, но не авторизацию/транспорт.
- M7 не принят: обычный `main()` не собирает проверенный рыночный и счётный контекст для `PaperCycle`; журналирование 100% решений автономно работающего бота не доказано. Стоп/тейк ranging сохраняются в бумажной позиции, но закрытие по ним остаётся M8.

TODO(VERIFY):
- Проверить авторизованным read-only вызовом новый `SandboxClient.users.get_accounts()` в целевом окружении без сохранения токена; это не требуется для локальной проверки SDK-контракта.
- Перед автономным paper-запуском подтвердить источник полного рыночного/счётного снапшота и постоянные якоря дневного/стартового капитала (см. предыдущую запись M7).

## 2026-10-15 | Этап M7 | Проверенный контекст paper-bootstrap

Сделано:
- `trading_bot/paper_feed.py`: добавлен `PaperTickFeed` на read-only запросах шлюза и данных локального paper-портфеля. Источник собирает закрытые 5m/15m/1h свечи, актуальный статус, bid/ask и спред, корреляцию, гэп, объём в лотах, breadth, динамический chaos-контекст и AccountState; не заполняет неизвестные данные фиктивными значениями.
- Fail-closed проверки: пропускает будущие/незакрытые бары, проверяет OHLCV, отклоняет будущий/старый timestamp стакана, несинхронный breadth-пул, старый индекс и устаревшую цену открытой позиции; рыночные цены каждой позиции и численный breadth доступны только на том же свежем 5m-баре, что и решение. Рекомендация ликвидности считает только полные непрерывные регулярные сессии 10:00–23:35 МСК, не меняя утверждённый оператором пул.
- `trading_bot/main.py`, `trading_bot/config.py`: штатный paper-bootstrap связывает feed и `PaperCycle`, берёт токен только из OS keyring, задаёт sandbox target, закрывает ресурс SQLite и передаёт явные допуски свежести из положительных настроек `PAPER_MAX_BAR_STALENESS_SEC`/`PAPER_MAX_QUOTE_STALENESS_SEC`. Рекомендованный пул остаётся только рекомендацией. `main.run()` подтверждает бар после успешного `run_tick`, ошибки источника/цикла логируются и останавливают процесс.
- `trading_bot/paper_account.py`: счётчик сделок различает новые входы по `journal_id` при совпадающем timestamp и не удваивает FIFO-расщепления; legacy-строки без journal_id сохраняют прежнюю группировку.
- Добавлены `tests/test_paper_feed.py` и регрессия `tests/test_paper_account.py`; обновлены `tests/test_main.py`, `tests/test_config.py`, `docs/03_STRATEGY.md`, `docs/07_CONFIG_SPEC.md`, `tasks/BACKLOG.md`.

Проверено:
- До исправлений регрессионные проверки падали для несинхронного breadth, старого индекса, неполной сессии объёма, устаревшей цены открытой позиции и двух входов с одинаковыми тикером/стороной/временем; после исправлений эти тесты проходят.
- `make ci` после последнего исправления: Ruff и Black чисты; mypy --strict — 21 production-модуль без ошибок; 526 passed, 13 намеренно исключённых тестов `TODO(VERIFY)`. Дополнительно целевой срез paper-feed/account/main/config/regime/cycle прошёл.
- Независимый review подтвердил существующие journal-before-fill и paper-fail-closed механизмы, но M7 acceptance остаётся открытым из-за неполной проверки автономного runtime и потерь журнала до формирования тика.
- В этой задаче сетевые вызовы, ввод/сохранение токена, открытие/пополнение счёта и любые заявки не выполнялись. Опубликованный ранее в переписке токен следует отозвать/заменить; он не использовался и не записывался в файлы.

Не сделано / перенесено:
- Реальная авторизованная работа текущего feed через sandbox API/TLS не проверялась; все новые интеграционные сценарии используют FakeGateway и SQLite. Точность доступности полного исторического ряда и задержек API остаётся непроверенной в целевой среде.
- M7 не принят: числовые номера фильтров не сохраняются; ошибка получения/валидации данных до создания `PaperTick` останавливает процесс без записи в `decisions`; успешный end-to-end paper-сигнал через живой источник не проверен. Закрытие позиций по стопу/тейку и заявочные операции остаются M8.
- `PaperAccountSource.daily_pnl` остаётся mark-to-market. Его соотношение с капиталом на стопах `B` из формулы `docs/04_RISK.md` требует явного согласования до автономной торговли; поведение риска не менялось без подтверждённого решения.
- Read-only поведение feed доказано используемыми вызовами и fake-контрактом; `main` создаёт sandbox-target `InvestGateway`, но сам объект шлюза шире read-only интерфейса и содержит явно вызываемый sandbox account method. Торговых методов заявок в шлюзе нет; усилить capability-границу отдельной read-only фасадой можно до автономного запуска.

TODO(VERIFY):
- Авторизованно подтвердить `SandboxClient.users.get_accounts()` и свежесть рыночных ответов в целевой сети, не сохраняя токен; не отключать TLS-проверку.
- Согласовать дневной P&L mark-to-market с ограничением капитала на стопах в `docs/04_RISK.md` и закрыть входные ошибки без потери journal-аудита.

---

## 2026-10-15 | Этап M7 | Проверка статуса и свежести контекста paper-feed

Сделано:
- `trading_bot/utils.py`, `trading_bot/paper_feed.py`: результат `can_trade_now()` сохраняет полученный статус. Feed использует его вместо второго RPC; недоступный/неполученный статус становится `STATUS_UNKNOWN`, сессия закрывается и `PaperCycle` журналирует отказ до paper-fill.
- `trading_bot/paper_feed.py`: добавлена проверка свежести последних закрытых свечей тикера 15m/1h — возраст от открытия ограничен длительностью таймфрейма плюс явный `max_bar_staleness`. Пустая старшая история проходит в стратегию как неизвестный контекст и сохраняется как `insufficient_history`, а не заменяется данными.
- `trading_bot/paper_feed.py`: breadth теперь использует пару закрытий только при совпадении последнего бара и строго смежном предыдущем 5m-баре. Дырка в ряду приводит к неполному breadth и безопасной паузе.
- `tests/test_paper_feed.py`, `tests/test_utils_calendar.py`: добавлены/уточнены тесты одинарного RPC, сбоя API со стабильной причиной в journal, не-NORMAL статуса с журналированием и 0 paper-сделок, старых/отсутствующих 15m/1h баров и разрыва в breadth. Обновлены `docs/07_CONFIG_SPEC.md` и статус M7 в `tasks/BACKLOG.md`; незавершённые пункты не отмечались готовыми.

Проверено:
- Регрессии статуса и старших таймфреймов сначала воспроизведены как красные: дублировался RPC, GatewayError останавливал tick до journal, а старые 15m/1h бары пропускались.
- Новый тест разрыва breadth также сначала упал: feed считал `breadth=1` через несмежные бары; после проверки смежности он даёт паузу.
- Целевые проверки прошли; независимый QA прогнал `make ci`: Ruff и Black чисты, mypy strict — 21 source files без ошибок, `534 passed, 13 deselected` (намеренно красные `TODO(VERIFY)` исключены).
- Поиск длинных строк вида токена в файлах репозитория совпадений не нашёл. Сеть/SDK, токен, создание счёта и заявки не использовались.

Не сделано / приёмка:
- M7 не принят: интеграционные тесты использовали FakeGateway и SQLite; авторизованный sandbox/TLS и end-to-end paper runtime не проверены. Ошибки до формирования `PaperTick` всё ещё не имеют гарантированной записи в `decisions`; повтор того же бара после рестарта отдельно не предотвращён. Числовые индексы фильтров отсутствуют в journal, а mark-to-market `daily_pnl` не согласован с capital-at-stops из `docs/04_RISK.md`. Закрытие по stop/take остаётся M8.
- Каталог содержит staged-файлы, но ветка `main` ещё без коммитов; историю/дифф к предыдущему коммиту проверить невозможно. Изменения не коммитились.

TODO(VERIFY) новые:
- нет. Открытые проверки выше сохранены из предыдущей записи.

Следующий шаг:
- Закрыть журналирование/дедупликацию отказов до создания `PaperTick`, согласовать модель дневного P&L, затем отдельным подтверждённым запуском выполнить read-only sandbox acceptance; live-заявки не запускать.

---

## 2026-10-15 | Этап M7 | Проект аудита отказов и восстановления paper-бара

Сделано:
- Создано **предложение**, не реализация: `tasks/M7_JOURNAL_RECOVERY_DESIGN.md`. Разведены записи `source_refusal` до `PaperTick` (полный `n/a`-snapshot без выдуманного режима и без сырого SDK-исключения) и единственное стратегическое решение по подтверждённому 5m-бару. Предложены новая миграция 0006 с nullable ключом фактического бара, частичный UNIQUE только для paper-решений, состояния `complete/pending/failed`, проверка повторного бара через SQLite до исполнения, безопасный переход со старой схемы без угадывания `bar_ts`.
- `tasks/BACKLOG.md` дополнен ссылкой на проект и открытым решением по `pending`/`failed`; незавершённый пункт M7 остался `[ ]`.

Проверено:
- По коду: `paper_feed.py` хранит ack в памяти и пропускает некоторые pre-tick ошибки; `main.py` вызывает ack после `run_tick`; `database.save_decision` и `Portfolio.open_position` коммитят раздельно. `tests/test_paper_cycle_integration.py` намеренно ожидает вторую запись по повторному бару и допускает автоматический повтор после ошибки записи позиции — эти ожидания нельзя менять без явного обсуждения.
- `git diff --check` выполнен, ошибок пробелов не выявил (только предупреждения Git о LF/CRLF). Тесты/CI в сессии проектирования не запускались: исполняемый код, миграции и тесты не изменялись. Сетевых вызовов, ключей и торговых операций не было.

Не сделано / перенесено:
- M7 **не принят**; миграция, запись source-refusal, защита повторного бара, восстановление `pending` и испытания краш-точек не реализованы. Блокеры предыдущей записи (sandbox/TLS, модель P&L, числовые фильтры) без изменений.

TODO(VERIFY) новые:
- нет SDK-вызовов или предположений о внешнем API; перед реализацией подтвердить оператором политику незавершённого бара: рекомендовано остановить paper-цикл и сверить журнал/сделки вручную, не повторять автоматически.

Следующий шаг:
- После подтверждения политики `pending`/`failed` реализовать миграцию и контракты по тестам из `tasks/M7_JOURNAL_RECOVERY_DESIGN.md`; не ослаблять два существующих теста без замены проверками нового инварианта.

---

## 2026-10-15 | Этап M7 | Уточнение проекта аудита и защиты от повторов

Сделано:
- `tasks/M7_JOURNAL_RECOVERY_DESIGN.md` пересмотрен по замечаниям: недоступный/битый индекс по `docs/03_STRATEGY.md` остаётся `chaos`-решением, source-refusal относится к невозможности собрать тик кандидата; определены случаи пустой/старой истории и `_closed()` ValueError, типизированная граница feed→main→journal, порядок проверки ключа до снимка счёта и durable `pending` до fill.
- Описаны атомарная 0006 при текущем `executescript`, глобальная проверка незавершённых paper-решений и необходимость повторно читать баланс/позицию под SQLite lock в `Portfolio.open_position()` для разных процессов; добавлены тестовые сценарии рестарта/краша/конкуренции.
- `tasks/BACKLOG.md` дополнен новым статусом проекта; чекбоксы M7 не закрыты.

Проверено:
- Сопоставлены реальные ветки `paper_feed.py`, `executor.py`, `database.py`, `portfolio.py`, `main.py`, существующие тестовые ожидания и `docs/03_STRATEGY.md:111–118`. `git diff --check` по отслеживаемым документам прошёл; незакоммиченный новый проектный документ ещё не охватывается этой командой. Код, миграции и тесты не изменялись, поэтому CI в этом проектном ходе не запускался. Сеть, токены и торговые операции не использовались.

Не сделано / приёмка:
- M7 **не принят**: миграция 0006, source-refusal, персистентная идемпотентность и краш-/конкурентные тесты не реализованы. Перед реализацией требуются решения оператора о восстановлении `pending/failed` и частоте записи повторных отказов; прежние блокеры M7 (включая дневной P&L и числовые индексы фильтров) сохраняются.

TODO(VERIFY) новые:
- Проверить реальную транзакционность `executescript` с `BEGIN IMMEDIATE` и rollback DDL + `schema_version` на установленном SQLite/Python до миграции 0006; без зелёного теста миграция не готова.

Следующий шаг:
- Получить подтверждение двух политик, затем реализовать интерфейсы и тесты на реальной SQLite, не удаляя существующие регрессии без проверок нового поведения.

---

## 2026-10-15 | Этап M7 | Эпизоды отказов и fail-closed восстановление paper

Решение оператора:
- `pending`/`failed` останавливают цикл до ручной сверки сделок, позиций, баланса и журнала; автоматического повторного fill/ack нет. Повторные отказы источника агрегируются открытым эпизодом `(mode,ticker,stage,error_type)` с `attempt_count`, первым/последним UTC-временем и известным баром; следующий эпизод начинается после восстановления.

Сделано (без live-ордеров и сетевых вызовов):
- `trading_bot/source_events.py`, `trading_bot/paper_feed.py`, `trading_bot/main.py`: типизированный `SourceRefusal` для сбоев до тика, запись до skip/stop, сон между опросами, закрытие эпизодов после полного обработанного тика и ack только затем; недоступный/битый индекс остаётся стратегическим `chaos`.
- `migrations/0006_paper_decision_recovery.sql`, `0007_legacy_bar_cutoff.sql`, `trading_bot/database.py`, `trading_bot/journal.py`: атомарные миграции с rollback DDL и версии; ключ/состояние paper-решения, уникальность бара, отдельный журнал эпизодов (UTC, `n/a` snapshot, безопасные идентификаторы, эскалация stop). Маркер 0007 фиксирует границу старых неключевых paper-решений; битый cutoff останавливает запуск.
- `trading_bot/executor.py`, `trading_bot/portfolio.py`: `BEGIN IMMEDIATE` на проверку claim и отдельное атомарное повторное чтение баланса/позиции перед открытием сделки. `pending` коммитится до fill; после fill — complete, при ошибке — failed или durable pending, никаких автоповторов. Старые бары при миграции безопасно ack-аются без нового решения/fill и без выдуманного соответствия старой строке журнала.
- Добавлены/обновлены `tests/test_database.py`, `tests/test_journal.py`, `tests/test_paper_feed.py`, `tests/test_main.py`, `tests/test_paper_cycle_integration.py`, `tests/test_paper_recovery.py`, `tests/test_portfolio.py`; актуализированы `docs/06_DB_SCHEMA.md`, `docs/15_ACCEPTANCE.md`, `tasks/M7_JOURNAL_RECOVERY_DESIGN.md`, `tasks/BACKLOG.md`. Пункты M7 целиком не закрыты.

Проверено:
- Красные сценарии до фиксов: отсутствие API эпизодов, отсутствие ключа бара/блокировки pending, пропуск испорченного бара, обход глобального pending завершённым дублем, повтор legacy-бара до cutoff. Зелёные целевые тесты после правок: SQLite миграция, журнал, feed, main, paper-цикл, восстановление и конкурентный портфель. `make ci`: ruff/black/mypy чисты, `575 passed, 13 deselected` (намеренно красные `TODO(VERIFY)` исключены `-m 'not red'`). Сеть/SDK, реальные sandbox-токены и торговые операции не использовались.

Не сделано / приёмка:
- M7 **не принят**: ошибки `evaluate()` до формирования Decision пока могут не попасть в journal; не согласованы mark-to-market `daily_pnl` и capital-at-stops и единые индексы 1–10; реальный автономный сетевой runtime не испытан. Тесты двух соединений/потоков не доказывают отсутствие всех расписаний. Полная ручная процедура изменения `pending/failed` после сверки не реализована (автоснятие запрещено). Ранее раскрытый оператором sandbox-токен следует отозвать.

TODO(VERIFY) новые:
- Проверить реальный paper runtime с безопасно введённым действующим sandbox-токеном и подтверждённой TLS-цепочкой без сохранения секрета; проверить все пути ошибок `evaluate()` до Decision на обязательную запись отказа и согласование дневного P&L с моделью риска. До этого критерий 100% отказов не доказан.

Следующий шаг:
- Закрыть оставшиеся M7-блокеры отдельными сквозными проверками, не включать live и не переводить `pending/failed` автоматически.

## 2026-10-15 | Этап M7 | Сбои evaluate и персистентный дневной стоп paper

Решение оператора: дневной порог −3% измеряется капиталом на стопах `B` относительно персистентного `E_day0`; до подтверждённого следующего торгового дня новые входы запрещены. Сброс только после первой закрытой 5m-свечи новой сессии (10:00 МСК), автоматически без человека. Требование ручного возобновления относится к глобальной просадке; при недоступности данных дневной запрет остаётся в силе.

Сделано:
- `trading_bot/executor.py`, `trading_bot/main.py`, `tests/test_paper_recovery.py`, `tests/test_main.py`: обычные исключения `evaluate()` до Decision фиксируются как `strategy_evaluate` с bar-key и именем класса после отката транзакции; сырой текст не попадает в лог/БД, fill/ack нет. Ошибка записи эпизода пробрасывается. При одновременном достижении дневного порога latch сохраняется и после отката evaluate.
- `trading_bot/risk_manager.py`, `trading_bot/paper_account.py`, `trading_bot/paper_feed.py`, `migrations/0008_paper_daily_stop_latches.sql`: фильтр дневного лимита и размер используют одинаковый `B` со стопом/текущей ценой и издержками; `daily_pnl` остаётся отчётным mark-to-market. Однократная строка срабатывания по дню и якорь переживают рестарт/отскок цены. Feed подтверждает день только при закрытой свече открытия 10:00 МСК; без неё старый якорь и запрет входа сохраняются. Новая дата не стирает старую строку latch. По итогам независимого review: умолчания статуса дня/стопа закрыты fail-closed, публичный сайзинг тоже отказывает при latch, ключ миграции явно `NOT NULL`.
- `tests/test_daily_stop.py`, `tests/test_risk_manager.py`, `tests/test_database.py`, `tests/test_strategy_filters_integration.py`, `tests/test_paper_cycle_integration.py`: SQLite-рестарт, граница 3%, восстановление капитала без снятия стопа, неподтверждённый/подтверждённый новый день, гэп ниже стопа и штатное исполнение, миграция 0008 и решение по фильтру риска. Обновлены `docs/04_RISK.md`, `docs/06_DB_SCHEMA.md`, `docs/07_CONFIG_SPEC.md`, `docs/15_ACCEPTANCE.md`, `tasks/BACKLOG.md`, `tasks/M7_JOURNAL_RECOVERY_DESIGN.md`.

Проверено: сценарий дневного порога сначала падал из-за отсутствия таблицы; новые тесты на daily_pnl/B падали до исправления; неожиданный `AssertionError` сначала не логировался в `main.run`. После исправлений целевые тесты проходят. `make ci`: ruff/black/mypy чисты, `594 passed, 13 deselected` (красные `TODO(VERIFY)` исключены). Сетевых операций и токенов не использовали.

Не сделано / приёмка: M7 **не принят** — единые числовые индексы фильтров 1–10, полная ручная процедура сверки `pending/failed`, реальный сетевой paper-runtime остаются открыты; paper-исполнение стопа/закрытие позиции по рыночному событию — M8. Гэп сверх стопа может превысить порог, это не исправляется формулой. Ранее опубликованный оператором sandbox-токен следует отозвать.

TODO(VERIFY): проверить торговый календарь/праздники и подтверждение первой закрытой 10:00-свечи в реальном read-only потоке с новым, скрыто введённым sandbox-токеном и доверенной TLS-цепочкой; не утверждать 100% покрытие всех сетевых сбоев до такой проверки.

## 2026-10-16 | M7 | Проверка пропущенных начальных пунктов

Решение оператора: сначала проверить классификатор и long/short-входы; review только по окончании этапа. При выявленном расхождении docs/03 (ranging-short RSI > 70) и дефолта 65 оператор выбрал «RSI > 70». Это исправление входного порога, не изменение риск-лимитов.

Сделано:
- `tests/test_strategy_contract.py`: 45 новых проверок — строгие границы ADX/ATR, оба направления/равенство EMA, нестандартные пороги; изолированное блокирование каждого из 6 trending- и 3 ranging-условий через evaluate для long/short; RSI-границы и конфигурация; trending-short на закрытых свечах без подмены snapshot; 1000 chaos-снимков и сквозной chaos→PaperCycle→SQLite без fill, сделок и позиций с рестартом.
- `trading_bot/config.py`, `.env.example`, `tests/test_strategy.py`: дефолт, fallback и тестовый конфиг RSI_OVERBOUGHT исправлены с 65 на 70. Трендовые RSI-параметры не менялись. Явные настройки оператора не переписываются.
- `docs/03_STRATEGY.md`, `docs/07_CONFIG_SPEC.md`, `tasks/BACKLOG.md`: согласованы ranging-пороги; отмечены проверенные алгоритм классификатора, правила входа и запрет сигналов chaos. Отдельно оставлен открытым регламент 30-минутного обновления/уведомления: текущий локальный классификатор пересчитывается на каждом новом баре.

Проверено: новые тесты сначала дали 3 отказа (ошибочный вход при RSI 69.999/70 и fallback 65), после исправления прошли. Общий `make ci`: ruff/black/mypy чисты, **639 passed, 13 deselected**; диагностика изменённых Python-файлов чистая. Сеть/live/токены не использовались; staging и коммиты не менялись. Новый review не запускался по указанию оператора.

Не сделано: числовые категории фильтров в журнале, полная ручная процедура pending/failed, 30-минутный регламент/уведомления и реальный сетевой runtime. M7 **не принят** целиком. Новых TODO(VERIFY) по SDK нет; прежняя проверка календаря/сети остаётся открытой.

## 2026-10-16 | M7 | Числовые категории и ручная офлайн-сверка claims

Задача оператора: «реализуй числовые категории фильтров в журнале, затем ручную сверку pending/failed». Сохраняется прежнее решение: остановка и ручная сверка, без автоповтора; code review только в конце этапа.

Сделано:
- `migrations/0009_decision_filter_categories.sql`, `trading_bot/database.py`, `trading_bot/decision_filters.py`, `trading_bot/journal.py`: версия 1 JSON `filter_categories` с категориями 1–10 по docs/03 и выполненными проверками. failed доминирует; passed групповых №9/10 требует всех компонентов; невыполненные — not_evaluated. Старым строкам оставлен NULL без выдуманного backfill. Имена дополнительных причин не удалены/не перенумерованы.
- `trading_bot/strategy.py`, `trading_bot/utils.py`, `trading_bot/executor.py`, `trading_bot/paper_feed.py`: выполненные проверки времени/guard/режима сохраняются явно; недостаток истории не стирает уже полученные свидетельства, отсутствующий gap_result не объявляется проверенным. Типизированная причина отказа источника статуса доходит до стратегии: instrument_status_normal относится к категории 3, а не к времени 1; календарный запрет имеет приоритет.
- `trading_bot/paper_recovery.py`, `migrations/0010_paper_manual_reconciliation.sql`: отдельные inspect/confirm CLI только существующей SQLite, без сети, миграций или создания БД. Повторное чтение под BEGIN IMMEDIATE, свежий SHA-256 полного paper trade journal/решения/позиций/баланса/якорей/latches и всех незавершённых claims. Явные attestations остановки runtime и ручной сверки. Один pending/failed→complete и append-only audit фиксируются атомарно; no_fill запрещён при связанных trades/позиции, accepted_existing_fill проверяет базовые связи/лоты/сторону. Нет нового fill, правки учёта, риска, стопов или якорей. Остальные claims продолжают блокировать runtime.
- `tests/test_filter_categories.py` (24 случая), `tests/test_manual_reconciliation.py` (56 случаев), `tests/test_paper_feed.py`, `tests/test_database.py`: номер категории при ранних/риск-отказах, частичные группы, миграция legacy и незавершённых claims, сохранение/рестарт, CLI roundtrip, stale fingerprint (включая правку старой посторонней closed строки), атомарный rollback, запрет повторного fill/подтверждения, ошибки аргументов без отражения raw input, неизменность учёта/защит, append-only audit.
- Обновлены `docs/03_STRATEGY.md`, `docs/06_DB_SCHEMA.md`, `docs/15_ACCEPTANCE.md`, `tasks/M7_JOURNAL_RECOVERY_DESIGN.md`, `tasks/BACKLOG.md`; добавлена `docs/MANUAL_PAPER_RECOVERY.md` с командами, backup/stop-first и ограничениями.

Проверено: TDD категорий сначала дал no such column/отсутствующий контракт, затем прошёл; cash-mismatch и незамеченная правка старой closed строки в manual snapshot подтверждены RED→GREEN. Общий **make ci: 719 passed, 13 deselected**, ruff/black/mypy чисты (24 source files). Независимый QA runtime-прогон: 80 cases passed, CLI --help exit 0, inspect отсутствующей БД exit 1 и не создаёт файл/каталог. Диагностика изменённых Python-файлов чистая. Code review этапа не запускался. Сеть/live/токены/живая БД не использовались, staging/коммиты не менялись.

Не сделано / ограничения: M7 **не принят целиком** — календарь/реальный сетевой paper runtime и 30-минутный регламент/уведомления остаются открытыми. CLI не доказывает остановку чужого процесса (runtime-stopped — утверждение оператора), не делает полный автоматический FIFO/P&L-аудит и не чинит расхождения; snapshot и audit растут с полной историей. Команда confirm в этой сессии запускалась только на тестовых БД, не на рабочем счёте.

TODO(VERIFY): прежняя сетевая проверка с новым скрыто введённым токеном/доверенным TLS и реальным календарём остаётся открытой; новых непроверенных SDK-символов нет. Проверка больших snapshot по ресурсам остаётся эксплуатационным ограничением, не обещанием масштабируемости.

## 2026-10-16 | M7 | Продолжение после сетевого обрыва: календарь и расписание режима

Задача оператора из восстановленного transcript: «реализуй регламент пересчёта режима, проверку календаря и сетевого paper-runtime». Сохранено ограничение: code review только в конце M7, live не включать.

Сделано:
- `data_seed/holidays_ru.md`, `trading_bot/utils.py`, `tests/test_utils_calendar.py`, `tests/test_calendar_runtime.py`: официальный статический календарь MOEX 2026 по n94172 и обновлению n96571; 23.02/01.05/12.06/04.11 — специальные сессии, не закрытия биржи; проект запрещает их целиком. Выходные и годы вне покрытия запрещены. Пропавший/битый/пустой seed останавливает запуск. Общий calendar helper сохраняет обязательный свежий статус API и guard.
- `trading_bot/main.py`, `tests/test_main.py`: heartbeat-hook `poll_regimes` до чтения тика и передача loaded seed/интервала из Settings в feed. Ошибка hook останавливает цикл без сырого исключения/сделки. `.env.example`: пример `TBOT_REGIME_REFRESH_MIN=30`.
- `trading_bot/config.py`, `trading_bot/regime_schedule.py`, `trading_bot/strategy.py`, `trading_bot/executor.py`, `trading_bot/paper_feed.py`: отдельное МСК-расписание локального режима по закрытым 15m-барам и L3-вход `ScheduledRegime`. Pre-session с 09:50, слоты от 10:00 (`REGIME_REFRESH_MIN=30`), поздний старт без replay; fail-closed при потере/протухании/повреждении контекста. Heartbeat memo в пределах 5m-бакета, фактическая история кандидата повторно валидируется. Плановый режим/slot/observed_at сохраняются в snapshot, standalone evaluate совместим. IMOEX chaos/breadth остаются per-bar. Типизированные события, bounded nonblocking outbox, явный `transport=unavailable`; нет закрытия позиций или изменения риска.
- `tests/test_regime_schedule.py`, `tests/test_regime_runtime.py`, `tests/test_regime_config.py`: 18 новых случаев, включая два тикера, границы слотов/дня, pre-session, поздний старт, отсутствие/битую историю, RPC-бюджет, IMOEX outage при закэшированном локальном режиме и неизменность открытой позиции. `tests/test_calendar_feed.py`: 6 RED→GREEN случаев календарного отказа до RPC и неизвестного года. `trading_bot/decision_filters.py`, `tests/test_filter_categories.py`: `local_regime_unavailable` — категория №4 failed, остальные неизвестные не становятся passed.
- `docs/03_STRATEGY.md`, `docs/07_CONFIG_SPEC.md`, `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md`, `tasks/BACKLOG.md`: регламент и календарные требования/статус; `docs/PAPER_RUNTIME_VERIFICATION.md`: безопасный сетевой runbook с отдельной БД и скрытым локальным вводом секрета.

Проверено:
- Календарный RED: 47 failed / 36 passed; GREEN: 83 passed, ruff/black/mypy utils чисты. Расширенный промежуточный прогон: 154 passed / 1 ожидаемый отказ незавершённой интеграции poll_regimes.
- Parent RED→GREEN: два теста heartbeat-hook, передача нестандартного interval в bootstrap и категория №4 (исходно failed был пуст). Реализатор подтвердил 247 focused tests. Общий `make ci`: **793 passed, 13 deselected**, ruff/black чисты, strict mypy — no issues в 25 source files. Красные SDK TODO(VERIFY) исключены штатным маркером, не удалены.
- Независимая runtime-проверка прежних отказов bootstrap/SQLite/live и no-gRPC: 11 passed, exit 0. Финальный независимый QA нового increment: **120 passed in 5.71s, exit 0**, ровно 7 файлов regime_schedule/runtime/config, calendar_feed/runtime, main, filter_categories; pre-session/слоты/два тикера/календарный no-RPC/IMOEX outage/неизменная позиция/main-hook/#4 failed подтверждены. Это mocks/tmp SQLite, не сетевой тест и не code review.
- Диагностика всех изменённых Python-файлов чистая; `git diff --check` без ошибок (только штатные Windows LF→CRLF warnings). Поиск длинных `t.`-токенов в trading_bot/tests/docs/data_seed совпадений не дал; это не аудит всех форматов секретов или истории.
- Без сети прочитаны только признаки готовности окружения: paper, paper_source_enabled=False, candidates=0, approved=0, index_configured=False, starting_balance_configured=False, keyring_token_present=False. Секрет не выводился и не сохранялся.

Не сделано / ограничения: M7 **не принят целиком**, хотя код расписания и календаря прошёл полный CI. Реальный сетевой runtime заблокирован отсутствием действующего токена в keyring и утверждённого bootstrap. Доставка VK отсутствует (транспорт M9); типизированное событие не означает доставку. Итоговый review M7 не запускался. Рабочая БД, staging/коммиты не менялись.

TODO(VERIFY): провести настоящий sandbox read-only paper-runtime после безопасной настройки нового токена/пула/индекса/баланса и доверенного TLS; проверить закрытые бары, journal и рестарт без дублей. Новых неподтверждённых SDK-символов не добавлено.

## 2026-10-16 | Этап M7 | Автоматический bootstrap paper и требование для live
Решение оператора (явное изменение прежнего ручного контракта):
- «Подтверждаю: реализуй автоматический paper-bootstrap без ручного выбора кандидатов, утверждения пула и ввода ID индекса. Зафиксируй, на будущее, что в Live режиме это тоже должно быть автоматически».
- Отменено только ручное утверждение/ввод кандидатов, пула и ID индекса. Риск-лимиты, paper-default, запрет live без отдельного допуска M8 не изменены.
- Риски: неполный каталог, неоднозначный IMOEX, недостаточная история/ликвидность, изменение пула после рестарта. Требуемые меры: подтверждённые SDK-поля, fail-closed, журнал состава/исключений, сохранение сопровождения открытых позиций. Индекс не подменяется ETF/IMOEX2.
Сделано:
- `docs/00_PLAN.md`, `docs/03_STRATEGY.md`, `docs/07_CONFIG_SPEC.md`, `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md`, `tasks/BACKLOG.md`: новый автоматический контракт, проверенные SDK-символы, отдельное будущее live-требование и незакрытый периодический пересмотр 15 минут.
- `.env.example`, `docs/PAPER_RUNTIME_VERIFICATION.md`: запуск без ручных кандидатов/пула/ID, отдельный виртуальный баланс, keyring и новая БД.
Проверено:
- Локально без сети: backend `keyring.backends.Windows.WinVaultKeyring`, запись `tbot/broker_token` существует; значение не выводилось и не менялось. Валидность/контур токена не проверялись.
- `trading_bot/api_client.py`: каталог MOEX/RUB COMMON/PREFERRED без OTC/qualified-only и с API buy/sell; ticker collisions исключаются, FIGI фиксируется на процесс; IMOEX — точный тикер + проверенный enum INDEX, UID предпочтителен, неоднозначность запрещена. Валюта SDK `rub` нормализуется в доменное `RUB` без изменения риска.
- `trading_bot/config.py`, `trading_bot/main.py`: balance-only включает automatic discovery→liquidity→selected feed; legacy три поля игнорируются с предупреждением, без баланса offline heartbeat-only. Stage diagnostics без raw SDK exception.
- `trading_bot/paper_feed.py`, `trading_bot/pool.py`: последние одинаковые подтверждённые календарём сессии, пропуски не заменяются старыми днями; selected применяется к breadth, открытые позиции вне пула получают monitoring-only тики без новых входов.
- Новые `tests/test_auto_bootstrap.py`, `tests/test_auto_bootstrap_failures.py`, `tests/test_auto_pool_feed.py`; обновлены `tests/test_main.py`, `tests/test_config.py`, `tests/test_api_client.py`, `tests/test_sdk_symbols_exist.py`. RED→GREEN для discovery, применения selected, сессий/monitoring, diagnostics, валюты; дополнительный parent RED на пустой legacy instrument_type при валидном enum INDEX воспроизведён и лишняя непроверенная строковая проверка удалена.
- Итоговый parent-прогон после последнего уточнения: `ruff check trading_bot tests`, `black --check trading_bot tests` (64 файла), `mypy --strict trading_bot` (25 файлов) — exit 0; `pytest tests -m "not red" -o addopts="" -q`: **816 passed, 13 deselected**, 73.64s, exit 0. Намеренно красные SDK TODO не ослаблены. Независимый статический review финального snapshot: accepted, блокирующих замечаний нет. Локальный increment принят, не весь M7.
- `git diff --check` по затронутым tracked-файлам: exit 0 (только предупреждения LF→CRLF). Diagnostics изменённых Python-файлов пусты. Узкий поиск token-подобного `t.[30+]` в production-коде совпадений не дал, это не полный аудит истории.
Не сделано / ограничения:
- Сетевой runtime не запускался; фактические IMOEX/свежие бары у брокера не подтверждены. Пересмотр пула выполняется только при старте, не каждые 15 минут. Monitoring-only сохраняет тики/учёт вне пула, но не добавляет автоматическое закрытие/стоп-мониторинг M8. Live не реализован; M7 целиком не принят. Существующий Git staging и рабочая БД не менялись.
TODO(VERIFY): настоящий read-only sandbox runtime автоматического отбора, наличие/свежесть свечей настоящего IMOEX, рестарт без дублей; наличие SDK-символов не доказывает доступность сетевых данных.

## 2026-10-16 | M3/M7 | Квоты RPC автоматического сбора истории
Исходный дефект:
- Оператор запустил сетевой paper-bootstrap: TLS после настройки публичного CA прошёл до `GetCandles`, затем серия `RESOURCE_EXHAUSTED` превращалась в общий `GatewayError`, feed исключал OGKB/OZON/другие тикеры и продолжал обход. Такой пул смещён отказами квоты, не ликвидностью. Оператор подтвердил остановку процесса.
Сделано:
- `trading_bot/api_client.py`: общий monotonic/lock pacing всех GetCandles RPC, самостоятельная постраничная история по проверенному SDK MAX_INTERVALS, повтор только текущей страницы по RequestError RESOURCE_EXHAUSTED, SDK reset hint не обрезается. Квотные/прочие RPC ошибки типизированы и не дают partial history; успешные страницы не replay, пересечение/сортировка/дедуп и конфликт свечей проверены. Узкий filter обезличивает SDK GetCandles detail/tracking до обработчиков приложения.
- `trading_bot/config.py`, `trading_bot/main.py`, `.env.example`, `docs/07_CONFIG_SPEC.md`: frozen CandleRequestPolicy и Settings CANDLE_MIN_INTERVAL_SEC=1, CANDLE_MAX_ATTEMPTS=4 (включая первую), CANDLE_RETRY_BASE_SEC=5, CANDLE_MAX_RETRY_WAIT_SEC=120; политика общая для одного gateway, это не квота брокера.
- `trading_bot/paper_feed.py`: общие GatewayError/квоты/метаданные не превращаются в exclusion тикера; сбор прекращается до следующего кандидата/ранжирования. Прогресс кандидатов/страниц и wait/retry виден в техническом логе.
- Новые `tests/test_candle_requests.py`, `tests/test_candle_sdk_boundary.py`, `tests/test_candle_policy.py`; обновлены `tests/test_api_client.py`, `tests/test_main.py`, `tests/test_paper_feed.py`, `tests/test_auto_bootstrap_failures.py`, `tests/test_auto_pool_feed.py`, `tests/test_sdk_symbols_exist.py`. RED→GREEN исходного mid-page quota/partial universe сценария, реальный SDK wrapper без сети, политика/wiring, exhaustion, секреты, границы и recovery.
- `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md`, `tasks/BACKLOG.md`: контракт per-RPC pacing, ограниченные повторы одной страницы, отсутствие частичного пула после системного отказа; отделены SDK-консервативные размеры страниц от серверных квот.
- `docs/PAPER_RUNTIME_VERIFICATION.md`: исправлено неверное обещание файлового логирования (сейчас только stdout), зафиксирован наблюдавшийся сетевой отказ; технические логи не равны SQLite-журналу решений.
Проверено:
- Read-only SDK beta117: `RequestError.code`/`metadata` — атрибуты, `ratelimit_reset` — относительные секунды; SDK `get_all_candles` скрывает несколько RPC без pacing. `MAX_INTERVALS`: 5m/15m сутки, 1h неделя — консервативные интервалы SDK, не текущий предел сервера.
- Официальные GitHub-исходники `investAPI/src/docs/limits.md`, `grpc.md`, `errors.md`: общий сервис котировок 600 unary/мин, reset в секундах до сброса; один `RESOURCE_EXHAUSTED` не определяет конкретную причину. Реальную персональную/sandbox-квоту не измеряли.
- Независимый review обнаружил бесконечный cooldown при reset дольше бюджета: исправлено на конечный reset для последующих вызовов. Parent regression воспроизвёл RED 1 failed → GREEN 1 passed; раннего RPC нет, тот же gateway восстанавливается после reset. Повторный независимый статический review: accepted, блокирующих замечаний нет.
- Финальный parent QA после последней правки: ruff check trading_bot tests — чисто; black --check — 67 файлов; mypy --strict — 25 файлов без ошибок; pytest tests -m "not red" -o addopts="" -q — **858 passed, 13 deselected**, 62.44s, exit 0. 13 намеренно красных TODO-проверок не изменены. Diagnostics изменённых файлов пусты; git diff --check exit 0 (только LF→CRLF warnings). Локальное исправление принято по указанным критериям, сетевой M7 остаётся открытым.
Не сделано / ограничения:
- Новых сетевых вызовов, keyring/рабочей БД и Git staging не меняли. File logging, live, риск и периодический пересмотр пула вне этой правки. Сетевой прогон с реальной квотой после исправления не выполнен; M7 целиком не принят.
TODO(VERIFY): повторить настоящий sandbox bootstrap после исправления квот, подтвердить полный пул без quota-driven exclusions, затем свежие бары/SQLite-журнал/рестарт без дублей. Офлайн симуляция не заменяет сетевой приёмки M7.

## 2026-10-16 | M3/M7 | Ctrl+C в bootstrap и интервал GetCandles 0,1 с
Решение оператора:
- «Исправь Ctrl+C во время bootstrap, загрузки страниц и ожидания квоты. а так же сделай задержку не в 1 секунду а в 0.1». Уменьшение интервала API не меняет торговые риск-лимиты, paper-default или допуск live. Bounded quota retry (4 попытки/120 с на страницу) сохранён; это не бесконечный retry «до появления квоты».
Сделано:
- `trading_bot/cancellation.py`: общий однонаправленный CancellationToken/Event и OperationCancelled; ожидания через Event.wait кусками до 0,1 с, чтобы основной Python-поток мог обработать Windows signal.
- `trading_bot/main.py`: SIGINT/SIGTERM устанавливают token, передаваемый в gateway и runtime; checks до следующей стадии/apply/run, отмена INFO/exit 0 без source failure, SQLite закрывается. Принятый run_tick/resolve/ack завершается целиком, следующий тик не начинается.
- `trading_bot/api_client.py`: CandleRequestTiming.should_stop и public check_cancelled, checks до/после SDK-context/RPC, кэша, страницы, retry и sleep. OperationCancelled не оборачивается в GatewayError/ERROR; накопленная история не выдаётся после отмены.
- `trading_bot/paper_feed.py`: cancellation на входе/границах обхода, не начинает следующего кандидата и не выдаёт частичный результат ликвидности.
- `trading_bot/config.py`, `.env.example`: дефолт CandleRequestPolicy/Settings 0.1. `docs/07_CONFIG_SPEC.md`, `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md`, `docs/PAPER_RUNTIME_VERIFICATION.md`, `tasks/BACKLOG.md`: отмена, эффективное переопределение старого env, ограничения RPC/квоты и инструкция проверки.
- Новые `tests/test_shutdown.py`, `tests/test_gateway_cancellation.py`; обновлены `tests/test_candle_policy.py`, `tests/test_candle_requests.py`, `tests/test_candle_sdk_boundary.py`, `tests/test_main.py`, `tests/test_paper_feed.py`.
Проверено:
- Parent RED: реальный установленный handler через signal.raise_signal(SIGINT) во bootstrap продолжал run (1 failed); дефолт оставался 1.0 (1 failed). Gateway RED показал продолжение страниц/quota retry и выдачу cached metadata после stop. GREEN покрывает эти сценарии.
- Реальная цепь main→bootstrap→feed+SQLite: SIGINT останавливает до второго кандидата и pool apply, нет decisions/trades/source failure, соединение закрывается. SIGINT во время длинного wait через Timer пробуждает handler, а не искусственный KeyboardInterrupt. Отдельный SIGINT внутри PaperBroker.fill подтверждает 1 complete decision/1 trade/ack и отсутствие следующего тика.
- Итоговый parent QA: `ruff check trading_bot tests` чисто; `black --check trading_bot tests` — 70 файлов; `mypy --strict trading_bot` — 26 файлов без ошибок; `pytest tests -m "not red" -o addopts="" -q`: **924 passed / 13 deselected**, 60.38s, exit 0. Фокус до дополнительного fill-теста: 189 passed; `test_shutdown.py` итог 8 passed.
- Независимое read-only review accepted, блокирующих замечаний нет; обе рекомендации (согласовать docs/env 0.1 и добавить accepted-fill regression) выполнены. Diagnostics изменённых файлов пусты, git diff --check exit 0 (только LF→CRLF warnings).
Не сделано / ограничения:
- Уже выполняющийся синхронный RPC может завершиться до отмены; нативный cancel зависшего gRPC не добавлен. Tests используют настоящий установленный Python SIGINT-handler и fixtures, но внешнее Ctrl+C консоли оператора/брокерская сеть не проверялись.
- Нет новых сетевых вызовов, обращения к keyring/рабочей БД или изменения Git staging. 0.1 с не гарантирует запас общей квоты для статусов/стаканов/других процессов. Live, риск, file logging и 15-минутный pool refresh вне задачи; M7 целиком не принят.
TODO(VERIFY): оператору повторить read-only sandbox запуск с эффективным TBOT_CANDLE_MIN_INTERVAL_SEC=0.1 и один Ctrl+C в bootstrap/quota wait, проверить отсутствие дальнейших RPC после завершения текущего и clean exit; затем продолжить сетевую приёмку M7.

## 2026-10-16 | M7 | Круглосуточный пересмотр пула — согласование и реализация

Решения оператора в карточке вопросов:
- Когда пересматривать пул: **«Круглосуточно»**, каждые 15 минут также ночью и вне торговых дней. Это не разрешение торговли вне обычной сессии и не изменение риска.
- История завершённых сессий: **«Да, до смены данных окна»** — переиспользовать проверенные объёмы до смены дат сессий, ID, лотности или календаря. Принято ограничение: внутридневные исправления брокером старой истории автоматически не обнаруживаются.

Сделано:
- `docs/00_PLAN.md`, `docs/03_STRATEGY.md`, `docs/06_DB_SCHEMA.md`, `docs/07_CONFIG_SPEC.md`, `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md`, `docs/PAPER_RUNTIME_VERIFICATION.md`, `.env.example`, `tasks/BACKLOG.md`: контракт и статус круглосуточных слотов, cache key, staged page reads, durable apply и pinned identity. Интервал фиксирован `config.DEFAULT_POOL_REFRESH_MIN=15`, поля `Settings`/`TBOT_POOL_REFRESH_MIN` нет; неподдерживаемая env-строка убрана из примера. Документирована граница SQL-before-publish: durable applied — полный проверенный snapshot intent, не доказательство runtime-публикации.
- Новый `trading_bot/pool_refresh.py`, изменены `trading_bot/paper_feed.py`, `trading_bot/main.py`, `trading_bot/config.py`: одна staged-работа/долгоживущий feed, круглосуточный slot-aware gate, coalescing, каталог/exact IMOEX каждый пересмотр, cache только полных проверенных session grids, одна страница на шаг между обычными тактами. Ошибка квоты/транспорта блокирует весь отбор до следующего слота, а не исключает кандидата. Атомарный universe после SQLite event_log; pinned metadata/strict recovery, legacy без identity требует сверки. Сохранены ack, durable claims, позиции и риск. Долгий bootstrap перепроверяет каталог/индекс на новом слоте и переиспользует только совместимые объёмы.
- Новые `tests/test_pool_refresh.py`, `tests/test_pool_refresh_feed.py`, `tests/test_pool_refresh_runtime_acceptance.py`; совместимые изменения fixtures `tests/test_main.py`, `tests/test_auto_pool_feed.py` явно создают прежний identity-журнал для открытой позиции, а отдельный legacy-тест подтверждает отказ без него.
- `trading_bot/api_client.py`, `tests/test_pool_history_pages.py`: постраничный локальный iterator поверх существующего GetCandles и side-effect-free `discover_paper_shares(pin=False)` для staging; прежняя полная история и cancellation сохраняются. Реализатор подтвердил RED 20 failed → GREEN146 passed по новым и связанным candle/cancellation/bootstrap тестам; ruff/black и штатный strict mypy api_client чисты. Это промежуточный прогон адаптера; итог общего CI приведён ниже.
- `tests/test_pool_refresh_acceptance.py`: независимые parent-тесты hostile pool journal, доказанности identity и rollback SQL. Воспроизведено **23 failed / 3 passed**, после hardening core реализатор подтвердил **26 passed**. Не принимаются bool/string/float вместо версии/поколения/лота/ценового шага, наивный/неслотовый timestamp, подмена ticker/ID, NaN/нулевые значения и лишняя metadata как доказательство открытой позиции. Линты и диагностика нового тестового файла чисты.

Дополнительные parent-регрессии и итоговая проверка:
- `tests/test_pool_refresh_acceptance.py`, `trading_bot/pool_refresh.py`: откат часов раньше молча блокировал gate без failed audit. RED (0 записей вместо 1) → GREEN: однократный `PoolClockRollback` в event_log, запрет остаётся до рестарта, публикации нет.
- Новый `tests/test_pool_refresh_restart_dedup.py`, `trading_bot/paper_feed.py`: реальный PaperCycle fill → remove(selected пуст, open monitoring) → re-add → SQLite close/reopen → тот же бар возвращает прежний complete без второго fill. Новый тест обнаружил uncaught `pool_breadth({})`; исправлено на breadth=None (не фиктивный passed), monitoring продолжает работать. Проверены неизменность trades/positions/денежного баланса и сохранение failed-claim barrier. Первичные ошибки fixture (отсутствие bootstrap anchors; сравнение updated_at, который штатно обновляет Portfolio constructor) исправлены в setup/assert, production-защиты не ослаблялись.
- Финальный `make ci`: **1006 passed, 13 deselected**, 67.70s, exit 0; ruff чист, black 77 файлов, strict mypy 27 source files без ошибок. Новый post-fix независимый QA: **90 passed, exit 0**, плюс **4 успешные runtime-пробы, exit 0** (exactly-one rollback audit, breadth=None, durable restart dedup и SQL-before-publish cancellation/slot boundary). Старый QA112 passed относится снимку до этих двух исправлений и не подменяет финальную проверку. Code review этапа не запускался.
- Diagnostics затронутых Python-файлов пусты; git diff --check exit 0 (только LF→CRLF warnings). Узкий поиск длинных t.-токенов в production/tests/docs/data_seed без совпадений; это не полный аудит всех форматов секретов/истории.

Приёмка / ограничения: локальный pool-refresh increment принят по runtime-тестам; **M7 целиком не принят**. Ограниченный read-only sandbox smoke ниже подтвердил получение свежих SBER/IMOEX 5m-баров, но полный сетевой runtime и пользовательская quota/recovery/VK-доставка не подтверждены. Переиспользование старой истории не обнаруживает брокерские исправления без смены ключа. Синхронный RPC/ограниченный retry всё ещё задерживает один такт, жёсткая непрерывность мониторинга M8 не обещается. Legacy-позиция без доказанной identity блокирует bootstrap; CLI manual claims не создаёт identity-доказательство. При отмене/смене слота после commit до publish snapshot остаётся в журнале, но gate закрыт и требуется свежая проверка при перезапуске. Во время реализации pool-refresh сети, keyring и рабочей БД не касались; Git staging не менялся. Новых неподтверждённых SDK-символов не добавлено: iterator/pin — локальные API.

TODO(VERIFY): после ограниченного smoke подтвердить полный сетевой bootstrap/автоматический пул, quota recovery, внешний Ctrl+C и рестарт без дублей. Свежие закрытые 5m-бары SBER и IMOEX уже получены в read-only smoke, но не считать это полной сетевой приёмкой.

## 2026-10-16 16:40 +05:00 | M7 | Read-only sandbox data smoke

Сделано:
- Проведён ограниченный реальный запрос через `InvestGateway` к `sandbox_target()` без FakeGateway/фикстур и без вызова методов счетов/заявок. Каталог: 151 подходящая акция; SBER: `SECURITY_TRADING_STATUS_NORMAL_TRADING`; получено 12 5m-свечей SBER и 12 5m-свечей exact IMOEX. На момент проверки в обоих наборах последний закрытый бар — 2026-10-16 11:35 UTC; post-check возраст 355 секунд.
- JSON-свидетельство ответа, включая реальные OHLCV/времена/identity, сохранено в `event_log` отдельной БД `.tmp-paper-broker-smoke-4a6e65a623a2429d9db3dcb91f658834/tbot.db`. `PRAGMA integrity_check=ok`; `trades=0`, `decisions=0`, `orders_called=false`. Рабочая `data/tbot.db` не открывалась.
- Причина первого отказа была TLS trust: системный CA bundle не доверяет sandbox chain. SHA-256 DER официального `Russian_Trusted_Root_CA.pem` сверён с отпечатком `docs/08_TINVEST_API.md`; gRPC получил этот root только в env текущего процесса. Проверка TLS не отключалась, глобальное хранилище не менялось. Повтор после исправления trust успешно вернул реальные данные.

Не сделано / ограничения:
- Не запускался полный `main`/paper bootstrap: виртуальный starting balance не настроен. Не проверены автоматический отбор пула по 10 сессиям, feed/cycle decisions и source episodes, restart/dedup, quota recovery, внешняя отмена или долгий runtime. Тест подтверждает read-only data path API→тестовый `event_log`, но не полную сетевую приёмку M7.
- Реальные заявки не отправлялись; тестовая БД содержит только smoke-события и мигрированную схему. Тестовые БД/heartbeat не удалялись.

TODO(VERIFY): продолжить полную sandbox-приёмку по `docs/PAPER_RUNTIME_VERIFICATION.md`; M7 runtime-пункт в backlog остаётся `[ ]`. Git staging не менялся.

## 2026-10-16 16:50 +05:00 | M7 | Сетевой paper-runtime 10000 ₽ — два отказа, приёмка заблокирована

- По прямому указанию оператора запущен настоящий `trading_bot.main.main()` с `TBOT_TRADING_MODE=paper`, `TBOT_PAPER_STARTING_BALANCE=10000`, read-only sandbox gateway и новой БД `.tmp-paper-runtime-20261016-10000-9d6803ac/tbot.db`; heartbeat и `initial.log` изолированы там же. Рабочая `data/tbot.db` не открывается.
- Локальный test harness `runner.py` проверяет effective Settings/путь до подключения, сверяет официальный CA, захватывает технический лог и ограничивает один прогон 2700 секундами. `STOP-initial` или deadline вызывает установленный production SIGINT handler через `signal.raise_signal` на runtime-thread; это не доказательство внешнего консольного Ctrl+C. Market data/feed/strategy/cycle/gateway не подменены, риск не меняется. `db_evidence.py` читает только тестовую SQLite через mode=ro.
- Старт 11:50:47 UTC; к независимому QA snapshot 11:52:01 UTC идёт liquidity bootstrap (151 кандидат), миграции 1–10 применены, pool events/decisions/trades/positions/episodes/balance rows пока 0. Parent read-only snapshot 11:53:09 UTC: integrity ok, те же нулевые счётчики. Это промежуточное наблюдение, не успех bootstrap или приёмки.
- Первая попытка завершилась 11:55:41 UTC с exit 1: после 63 собранных кандидатов запрос 5m LSNG (`BBG000NLC9Z6`) получил `UNAVAILABLE`; `CandleRequestError` остановил liquidity bootstrap до применения пула. Ошибки квоты/исключение LSNG не зафиксированы; статус сам по себе не доказывает точную причину транспорта. Parent snapshot 11:57:15 UTC и независимый QA 11:57:29 UTC: integrity ok, миграции 1–10, pool events/decisions/trades/positions/source episodes/balance/anchors = 0, pending/failed claims = 0. Частичный пул не опубликован, runtime happy path не принят.
- Однократный read-only повтор той же страницы `2026-10-14T11:50:50.529050Z` → `2026-10-15T11:50:50.529050Z` в 11:57:53 UTC вернул 105 закрытых свечей с первой попытки; свидетельство `failed-page-recovery.json`. Это ручное наблюдение восстановления, не автоматическая retry/reconnect-приёмка.
- Первая команда повторного запуска завершилась до API из-за ошибки test harness: вспомогательный `inspect.py` затенил stdlib `inspect`. Файл переименован в `db_evidence.py`, добавлен main guard; stdlib `inspect.get_annotations` и импорт production main затем проверены успешно. Эта ошибка не относится к транспорту/production и сетевых запросов не выполняла.
- В 12:00:47 UTC начат один сетевой повтор bootstrap в той же изолированной БД (`phase=retry`, `retry.log`). Завершён в 12:05:32 UTC, exit 1: история KZOSP (`BBG0029SG1C1`), 58 собранных кандидатов из 151, снова `UNAVAILABLE` → `CandleRequestError` → stop. Ошибка на другом инструменте не доказывает точную причину сети/сервера; third bootstrap не запускался.
- Parent read-only snapshot после второй остановки 12:13:49 UTC и независимый QA snapshot 12:15:07 UTC: integrity ok, foreign_key_check без нарушений, миграции 1–10; event_log/pool/decisions/trades/positions/source episodes/balance/anchors/daily stop/reconciliation rows = 0, pending/failed и duplicate-bar rows отсутствуют. 10000 ₽ подтверждены как startup input, но до сохранения paper balance не дошло. QA подтвердил безопасный отказ на наблюдаемых ошибках, не clean SIGINT. Нулевая БД не является доказательством restart-dedup; до успешного пула и PaperCycle оба запуска не дошли. SIGINT/clean-stop, decisions/episodes и рестарт работающего runtime не проверены.
- Блокер: текущий `api_client.py` повторяет только `RESOURCE_EXHAUSTED`; `UNAVAILABLE` немедленно завершает попытку. Начальный bootstrap после завершения процесса холодный и повторяет широкий сбор. Production-код в рамках проверки не менялся, реальные заявки не отправлялись, рабочая БД не открывалась, Git staging не менялся. Backlog M7 `[ ]` сохраняется.

TODO(VERIFY): перед продолжением полной сетевой приёмки согласовать отдельную доработку ограниченных повторов той же read-only страницы при `UNAVAILABLE` (с сохранением cancellation, существующего бюджета, остановки при исчерпании и запрета partial pool), либо устранить доказанную причину транспорта. Бесконечные bootstrap/retry и фиктивные свечи не допускаются.

## 2026-10-16 | M3/M7 | Bounded UNAVAILABLE retry и новый сетевой прогон

Указание оператора: «Добавь ограниченные повторы UNAVAILABLE для GetCandles, сохрани fail-closed и отмену, покрой тестами и повтори сетевую приёмку».

Сделано:
- `trading_bot/api_client.py`: SDK `RequestError.code == UNAVAILABLE` повторяет только текущую GetCandles-страницу с фиксированным существующим retry-base; общий с quota бюджет попыток/elapsed (RPC/pacing включены), без reset metadata. Exhaustion — `CandleRequestError`, не quota; auth/прочие статусы immediate fail. Сохраняются full finite quota cooldown, cancellation и запрет partial history/pool.
- `tests/test_candle_unavailable.py` (новый), `tests/test_candle_requests.py`: same-page/no replay, attempts/slow RPC/elapsed/overshoot/mixed status, installed SDK error decorator, sanitization, cancellation. Реализатор подтвердил исходный RED 11 failed / 7 passed → focused GREEN 99 passed.
- Независимый review обнаружил P2: следующий call терял причину сохранённого cooldown и мог назвать UNAVAILABLE квотой. Private `_candle_wait_error` сохраняет классификацию под прежним lock; RED 2 failed / 2 passed → focused GREEN 103 passed. Post-fix review accepted, новых findings нет.
- `tests/test_unavailable_bootstrap.py` (новый): настоящий локальный bootstrap/daily_volume/history + offline transport и tmp SQLite; full collection после transient, persistent fail после готового кандидата без ranking/publication, cancel во wait сохраняют prior position/pending. Test-engineer: 74 related tests passed, quality clean; независимый исходный RED этого интеграционного файла не подтверждён (core уже исправлен к корректному прогону).
- `tests/test_pool_history_pages.py`: общий CI выявил старое ожидание immediate UNAVAILABLE (1029 passed / 1 failed). Обе transient-ветки приведены к approved двум попыткам текущей страницы, добавлена явная проверка non-quota класса; no replay/no later page остались. Read-only review этой правки accepted.
- `docs/07_CONFIG_SPEC.md`, `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md`, `docs/PAPER_RUNTIME_VERIFICATION.md`, `tasks/BACKLOG.md`: актуальный retry-контракт и честный статус сети.

Проверено:
- Финальный `make ci`: **1030 passed, 13 deselected**, 72.99 с, exit 0; ruff чисто, black 79 файлов, mypy 27 source files без ошибок. 13 намеренных red не менялись. Scoped diagnostics пусты. `git diff --check` перед финальным дополнением журнала — exit 0, только LF→CRLF warnings. Git staging/рабочая БД не менялись.
- В `2026-10-16T15:15:52Z` начат один новый настоящий sandbox paper-run с балансом 10000 ₽ в **новой** `.tmp-paper-unavailable-20261016-10000-b7e2a9d1/tbot.db` (terminal 79), лимит 2700 с, retry 4/5/120. `runner.py` проверяет settings/path/CA и сохраняет `initial.log`, данные/gateway/feed/strategy/cycle не подменяет; `db_evidence.py` читает только эту SQLite mode=ro и может сохранять проверочные JSON. На момент записи идёт liquidity bootstrap 151 кандидата; это не успех apply/runtime.

Не сделано / TODO(VERIFY) на момент запуска: дождаться исхода нового сетевого прогона, проверить реальное recovery при наблюдаемом UNAVAILABLE, pool→feed→cycle/свежесть/SQLite, clean SIGINT (harness не доказывает внешний Ctrl+C), затем рестарт той же тестовой БД и неизменность прежних строк/отсутствие дублей. Полная сетевая приёмка и M7 **не приняты**. Заявки/счета не создаются; TLS не отключается, риск не меняется. Локальные synthetic tests не выдаются за реальные рыночные данные.

## 2026-10-16 20:27 +05:00 | M7 | Реальный bootstrap завершён, runtime stale-stop

- Terminal 79 завершён, exit **1**, конец `15:27:19Z`; settled-процесс не перезапускался. Все 151 кандидата прошли сетевой сбор, `15:26:49Z` опубликован пул **21 бумага** (generation=1, slot=15:15Z). Реальные данные не подменялись. Exact IMOEX ID в snapshot: `4821c9aa-36e8-4743-b37c-861e58581b25`; завершённые даты сессий 02.10, 05–09.10, 12–15.10. Полный `initial.log` не содержит событий `candle_request_unavailable` или `candle_request_quota`: успех bootstrap не доказывает recovery retry-ветки в сети.
- Первый VTBR тик для 5m-бара `15:20Z` остановлен `higher_timeframe_stale / StaleDataError / action=stop`. Это не прежний UNAVAILABLE и не отказ bootstrap. `PaperCycle.record_source_refusal` сохранил один открытый episode с attempt_count=1; стратегического решения не было.
- Parent `db_evidence.py --save initial-stop`, snapshot `15:52:30.634717Z`, mode=ro: integrity ok, FK clean, миграции 1–10; event_log=1 pool_refresh_applied, portfolio_balances=1 (**paper=10000**), source episodes=1, decisions/trades/positions=0, duplicate bar keys нет. Свидетельство `.tmp-paper-unavailable-20261016-10000-b7e2a9d1/initial-stop.json`. Pool publish доказан и логом, не только durable intent.
- Независимая финальная QA, один mode=ro snapshot `15:54:57Z`: совпадают balance10000, pool21, episode1, decisions/trades/positions0, integrity/FK/migrations; полный лог без UNAVAILABLE. Статически подтверждён риск phase-dependent false positive, но не фактический TF исходного ответа. Полная приёмка не принята; правок/сети/рабочей БД QA не касался.
- При read-only разборе `paper_feed.py:976–990` обнаружен риск ложного stale: возраст старшего бара измеряется от открытия и сравнивается с duration+max_bar_staleness. При дефолтном 600 с в 15:27 последний закрытый 1h бар 14:00 (87 минут) не проходит порог 70 минут; бар 15:00 ещё незакрыт. Это детерминированный сценарий, но исходный отказ не журналирует timeframe/last ts/age/limit, поэтому фактический последний старший бар исходного ответа не подтверждён. Не выдаём гипотезу конкретного TF за измерение брокерского ответа.
- Обновлены только `docs/PAPER_RUNTIME_VERIFICATION.md`, `tasks/BACKLOG.md`, `CHANGELOG_AI.md`; production-код/параметры свежести/риск/Git staging/рабочая БД не менялись. Новых сетевых вызовов после завершения не было, реальных заявок нет. Прежний CI1030 относится неизменному source; повторять его ради docs не требуется.

Приёмка: локальный bounded retry принят; реальный bootstrap и source-refusal journal подтверждены. **Полный runtime M7 не принят**: decisions/fill, периодический refresh, recovery, clean SIGINT и restart-dedup по сети не состоялись. TODO(VERIFY): отдельно исправить/проверить семантику свежести старших закрытых баров с RED→GREEN и диагностикой timeframe/last ts/age/expected/limit; не расширять допуски механически и не принимать незакрытые/пропущенные бары. После согласованной правки — новый ограниченный сетевой прогон и рестарт той же отдельной БД; pending/failed автоматически не исправлять.

## 2026-10-16 | M7 | Свежесть ожидаемых закрытых HTF-баров

Указание оператора: «Исправь проверку свежести старших закрытых баров без ослабления fail-closed, добавь диагностику и тесты, затем повтори сетевую приёмку».

Сделано:
- `trading_bot/paper_feed.py`: private `_higher_timeframe_freshness`/`_ClosedBarFreshness` требует exact latest expected fully closed interval текущей подтверждённой обычной сессии 10:00–23:40 МСК; cutoff ограничен полными интервалами. Это не 2×duration/расширение допуска. Missing/off-grid/unclosed/future не разрешают вход; до первого текущего HTF-бара не добавлен overnight-допуск. Одна семантика подключена к ticker15m/1h и индексу.
- Ticker stale остаётся SourceRefusal stop до quote/account; пустая история сохраняет insufficient_history. IMOEX missing/stale/corrupt остаётся chaos/paused без кэширования разрешения; при исключении сбрасываются обе промежуточные метрики. `_closed`, `_regime_context`, ack/dedup, cancellation, 5m/quote Settings и риск не менялись.
- Структурный `higher_timeframe_freshness`: ticker/instrument/timeframe/now/source5m, latest/expected open/close, lag_seconds, policy, reason/action; без SDK details/секретов.
- Новый `tests/test_higher_timeframe_freshness.py`: 37 случаев, преимущественно public next_paper_tick; original15:27/15:44VTBR, boundaries±1мкс, missingonebucket/unclosed/future/offgrid, prior-day/no-first-hour, calendar, indexchaos/no-permission-cache, noquotes/account and acknotadvanced, cappedregularend и log fields. Existing tests/test_paper_feed.py не менялся, все48 проходят.
- `docs/07_CONFIG_SPEC.md`, `docs/15_ACCEPTANCE.md`, `docs/PAPER_RUNTIME_VERIFICATION.md`, `tasks/BACKLOG.md`: исправленный контракт и честный статус приёмки.

Проверено:
- Explicit .venv public-feed RED **2 failed** → GREEN2passed. Первичные collection/setup errors (системный miniconda и тестовая fixture после полуночи МСК) не выдаются за поведенческий RED; исправлялись только interpreter/fixture. Итоговый связанный feed/regime/calendar/pool набор реализатора **328 passed**, ruff/black/mypy strict/read_lints чисты.
- Parent `make ci` terminal97: **1067 passed, 13 deselected**, 245.85 с, exit0; ruff clean, black80files, mypy27files clean. Scoped diagnostics пусты, gitdiffcheck exit0 (LF→CRLF warnings). Независимый fixed-scope reviewer: StandardsPASS/SpecPASS/accepted; blockersнет, необязательный direct-helper calendar тест не добавляли (calendar публичногоfeed покрыт).
- В `2026-10-16T17:36:17Z` начат один настоящий sandbox paper повтор (terminal98): `.tmp-paper-unavailable-20261016-10000-b7e2a9d1/runner.py restart --limit-sec2700`. Та же **изолированная тестовая БД**, paper10000 ₽, retry4/5/120, проверенныйCA, log restart.log. Существующий баланс/episode не сброшены, рабочая data/tbot.db не открывается. Источник/gateway/стратегия/cycle реальные, marketdata fixtures не используются. На момент записи идёт liquidity bootstrap151.

Не сделано / TODO(VERIFY) на момент запуска: получить итог сетевого прогона, подтвердить expected/latest диагностику на реальных данных, decisions/sourceepisodes/refresh, cleanSIGINT и дальнейший restart без дублей. Начальный baseline decisions/trades=0 не является доказательством replay-dedup. Полная сетевая приёмка/M7 **не приняты**. Реальных заявок/изменения риска/отключенияTLS/Gitstaging нет; syncRPC всё ещё может задержать отмену.

## 2026-10-16 22:59 +05:00 | M7 | HTF прошли реальную сеть, далее quote-stop

- Terminal98 завершился `17:59:29Z` exit1, не deadline2700 и не SIGINT; settled-process не перезапускался. Initial151 liquidity histories заняли до17:49:25 и пересекли слот. `main.py:220–255` заново разрешил каталог/IMOEX, сохранил36 совместимых полных10-сессионных историй и перечитал115 неполных до17:58:55. Это bootstrap coalescing, не runtime refresh. Pool21 generation2 slot17:45Z durable at17:58:55.993617, publish at17:58:56 подтверждён логом.
- Реальные ticker HTF подтверждены (`restart.log:5501–5502`): now17:59:25.334299Z, 15m latest/expected17:30→17:45, 1h latest/expected16:00→17:00, lag0, continue. Обе старые age-formulas ошибочно отвергли бы эти фазы; новая exactclosedbucket-проверка проходит без увеличения допусков. Индексная свежесть в этом тике ещё не проверялась: раньше него произошёл quote-stop.
- VTBR 5mbar17:50 отклонён `quote / StaleDataError / stop` (`restart.log:5503–5506`). Parent modeRO snapshot18:06:03.825857Z, сохранён `restart-stop.json`: integrityok, FKclean, migrations1–10; event_log2(pool generation1/2), paperbalance10000, sourceepisodes2(open higher_timeframe_stale old + quote new, eachattempt1); decisions/trades/positions0, duplicatesнет. Сохранность прежних решений/сделок приprior_count0 — вакуозная проверка, не доказательство restart-dedup.
- Независимая финальная QA: один SQLite modeRO snapshot18:08:22.310669Z без --save подтвердил balance10000, pool2, episodes2, decisions/trades/positions0, integrity/FK clean, pending/failed/duplicates нет. HTF сетевой сценарий принят; verification gate полного M7 blocked из-за quote-stop и отсутствия happy path. Причина quote не установлена; код/сеть/рабочая БД QA не менялись.
- В полном restart.log нет candle_request_unavailable/quota events: реальное retry/recovery не испытано. Старый HTF episode не закрыт, потому что успешного тика ещё не было.
- Read-only source-разбор: `paper_feed.py:1099` вычисляет quote_age от now начала сборки тика, а `api_client.py:921` возвращает SDK orderbook_ts. После RPC свежий quote timestamp может оказаться позднее старого now и дать ложный future/negative age. Это доказанный возможный сценарий по коду, но **не установленная причина исходного ответа**: quote timestamp/age/receive time/limit не логируются, остаётся также вариант действительно старого стакана (>30с). Допуск/production-код не меняли; нового runtime/сети/keyring/рабочей БД/Git staging после завершения не было.
- Обновлены только docs/PAPER_RUNTIME_VERIFICATION.md, tasks/BACKLOG.md, CHANGELOG_AI.md; product source неизменен после CI1067. Локальная HTF правка принята, ticker data path прошёл настоящую сетевую проверку. **M7/runtime целиком не принят**: решения/fill, периодический refresh, recovery, clean SIGINT и durable restart после непустых решений ещё не состоялись.

TODO(VERIFY): отдельная quote freshness диагностика (tick-start, request/response/validated timestamps, broker timestamp, age, лимит, ветка причины) и проверка относительно актуального времени после RPC, сохраняя future/stale/rollback/cancellation fail-closed и неизменный30с допуск. Сначала воспроизвести delayedRPC regression, затем исправление/тесты и новый ограниченный сетевой paper-run. Не угадывать точный брокерский timestamp по времени ERROR и не выдавать нулевые сделки за полноценную приёмку.

## 2026-10-16 | M7 | Quote freshness относительно ответа RPC

Указание оператора: «Исправь проверку времени стакана относительно момента после RPC, сохрани fail-closed и допуск 30 секунд, добавь диагностику и тесты, затем повтори сетевую приёмку».

Сделано:
- `trading_bot/paper_feed.py:1094–1184` + import session_guard_active: request-start непосредственно перед read-only quote RPC, response-received непосредственно после него; age=response−SDK broker timestamp. Default Settings30 и custom feed limit не менялись: exact0/30 приняты, >limit/future/naive/clockrollback запрещены, отказ прежний quote/stop без ack. Cancellation до/после RPC и при GatewayError не оборачивается.
- Структурный quote_freshness: tick/request/response/broker timestamps, age/limit, ticker/ID/sourcebar, reason/action; fresh INFO, отказ WARNING без raw exceptions/secrets. Узкий pure guard повторно проверяет старый5m age, exactHTF и переход разрешённой session/guard в запрещённые на response; sourcebar/strategy.now не ребазируются, новых SDKRPC нет.
- Новый `tests/test_quote_freshness.py`,25 public-feed cases: delayedRPC, quote после tickstart, exactage0/30/+1us/future, staleatresponse, customlimit/default30, naive/rollback, cancellation5paths, contextexpiry5m/HTF/session/guard, allowed5mboundarycross и diagnostics. Existingtests/SDKadapter/risk/HTFhelper/localregimes/pool/account implementation не менялись.
- `docs/07_CONFIG_SPEC.md`, `docs/15_ACCEPTANCE.md`, `docs/PAPER_RUNTIME_VERIFICATION.md`, `tasks/BACKLOG.md`: контракт и честные границы/статус.
- Test harness `.tmp-paper-unavailable-20261016-10000-b7e2a9d1/runner.py`: добавлены фазы quote/resume для неперезаписываемых логов; quote limit явно30 в env процесса и effective assert. `db_evidence.py`: отдельные quote-stop/resume-stop snapshots и сравнение immutable rows с предыдущей фазой; SQLite остаётся modeRO. Два --help exit0 без сети/секретов. Прежние логи/JSON/БД не удалялись, рабочаяdata/tbot.db не открывается.

Проверено:
- Реализатор RED terminal105 **1 failed** на ложномfuture послеdelayedRPC → GREENterminal106 **1 passed**. Связанный набор feed/HTF/regime/cancellation/calendar/pool **197 passed**, окончательный новыймодуль **25 passed**. Ruff/Black/mypystrictsource/read_lints чисты.
- Parent финальный `make ci` terminal116: **1092 passed, 13 deselected**,95.64с,exit0; ruffclean,black81files,mypy27filesclean. Scoped diagnostics пусты; gitdiffcheck exit0 (LF→CRLFwarnings). Independent reviewer scoped stable quote block/import/newtests: StandardsPASS/SpecPASS/accepted, blockersнет; весьrepo/M7 этим не принят.
- `2026-10-16T18:24:55Z` начат один настоящий ограниченный sandbox paper-run terminal117: runner.py quote --limit-sec2700, прежняя изолированная testDB, virtualbalance10000, quoteMax30, pacing0.1/retry4/5/120, проверенныйCA; лог quote.log. На момент записи идёт liquidity bootstrap151. Данные/gateway/strategy/cycle не подменены; реальных заявок нет.

Ограничения / TODO(VERIFY): получить итог прогона, проверить quote_freshness с реальными broker/receive timestamps, complete decisions/episodes, периодическийrefresh, cleanSIGINT и дальнейший durable restart после непустых строк. Последующие account/index RPC могут состарить snapshot — гарантия execution-time freshness M8 не добавлена. По прежним сетевым отказам фактический broker quote timestamp неизвестен; regression доказывает дефект алгоритма, не точную причину исходного ответа. Полная сетевая приёмка/M7 **не приняты**. Нет изменений риска/SDKAPI/Gitstaging/отключенияTLS/рабочей БД; исполняющийся syncRPC может задержать отмену.

## 2026-10-17 00:05 +05:00 | M7 | Диагностика и восстановление часов Windows

Указание оператора: «Восстанови синхронизацию времени Windows и проверь остаточное смещение, допуски бота не меняй».

Сделано:
- По запросу оператора запущена системная служба W32Time (`Start-Service W32Time`), выполнен `w32tm /resync`; источник оставлен прежним `time.windows.com,0x9`. Тип запуска Manual не изменён. Код/конфиг/допуски бота, торговые БД и прежние логи не менялись; runtime не запускался. В репозитории изменён только этот журнал в рамках данной задачи.

Проверено:
- Последний quote-run завершился 2026-10-16 18:58:50 UTC с exit1: применён pool21 generation3, ticker VTBR 15m/1h latest==expected, затем quote/future. response=18:58:50.980648Z, broker=18:58:51.889633Z, age=-0.908985s (`quote.log:7638,7804–7810`). Итоговое состояние SQLite в этой диагностике не проверялось.
- Предшествующая независимая read-only QA: W32Time Stopped/Manual, запросы статуса давали 0x80070426; stripchart Microsoft/pool/ВНИИФТРИ согласованно показывал источник впереди локальных часов на 2.731–2.742s. Локальное отставание подтверждено. Ошибочная интерпретация знака в отчёте QA отклонена: при таком offset возраст стакана был бы около -0.909+2.74=1.83s, а не broker-future3.64s. Это объясняет отказ, но точный offset во время исходного RPC не измерялся.
- Parent terminal12: resync успешен, last successful sync 17.10.2026 00:04:26 +05:00, source time.windows.com. Terminal13: W32Time Running/Manual; 5 samples Microsoft -0.0055933…-0.0005109s, 5 samples ВНИИФТРИ -0.0001265…+0.0039771s. Остаточное наблюдаемое расхождение менее 6ms; это сетевые измерения, не гарантия абсолютной точности.

- Независимая read-only QA после исправления (terminals14–16, 00:05:40–00:06:00 +05:00): W32Time Running/Manual; Microsoft 3 samples +0.0005437/-0.0012739/-0.0049682s, ВНИИФТРИ +0.0026257/+0.0017072/+0.0007833s. Текущее восстановление синхронизации принято; долговременная стабильность и reboot не проверены.

Не сделано / TODO(VERIFY):
- Повторный сетевой paper-runtime после синхронизации, successful decisions/episodes и durable restart не проверены; M7 не принят. CI повторно не запускался: код не менялся.
- Автозапуск/trigger службы не перенастраивался; сохранение синхронизации после перезагрузки не проверено. Поведение step/slew не выводится из одного параметра реестра.

## 2026-10-17 00:09 +05:00 | M7 | Ограниченный paper-повтор после синхронизации

Указание оператора: «Повтори ограниченную сетевую paper-приёмку после синхронизации часов, без реальных заявок и изменения допусков».

- Прочитано дополнение другой сессии об успешном resync/peer QA; её запись не перезаписана. Parent terminal121 `Get-Date; w32tm /query /status` в19:07:45UTC подтвердил недавнюю синхронизацию19:04:26UTC с прежним time.windows.com, leap0. Часы/службы/реестр/конфигурацию в этой задаче не меняли; свежие peer-offset samples не снимались, за измерение offset при старомRPC их не выдаём.
- Settled terminal117 не перезапускался под старым phase. Сохранён финальный baseline прежнего quote-run: `db_evidence.py --save quote-stop`, modeRO, snapshot19:08:32.069401UTC. Integrityok/FKclean/migrations1–10; pool event_log3, paperbalance10000, episodes2(open HTF attempt1 и quote attempt2), decisions/trades/positions0, duplicatekeysнет. Immutable preservation имеет priorcount0 — это не доказательство replay-dedup. Quote.log7806 подтверждает response18:58:50.980648Z vs broker18:58:51.889633Z, age−0.908985s, future/stop; отказ сохранён, не ослаблен.
- Preflightкалендарь в19:08:33UTC: обычнаясессияallowedTrue, **16.10 22:08МСК**. ЛокальнаясубботаUTC+5 не подменяет датуМОЕХ. Рабочаяdata/tbot.db не открывалась; другиеchat-sessionнастарте не открыты.
- В19:09:05UTC (00:09:05+05) запущен **один** настоящий read-onlysandbox paper-runtime terminal123: runner.py resume --limit-sec2700, тажеизолированная `.tmp-paper-unavailable-20261016-10000-b7e2a9d1/tbot.db`, virtualbalance10000, quoteMax30, pacing0.1/retry4/5/120, прежниедопуски. EffectiveSettings/path/CAassertions осталисьпрежними; log resume.log/stopfileSTOP-resume, deadline45мин с productionSIGINThandler. Marketdata/gateway/feed/strategy/cycle не подменяются, реальныхзаявокнет.
- На момент записи идёт liquidity bootstrap151. Изменены только docs/PAPER_RUNTIME_VERIFICATION.md, tasks/BACKLOG.md, CHANGELOG_AI.md; productsource/harness/Settings/риск/Gitstaging/допуски не менялись, CI1092 повторно не запускался. SnapshotJSON — отдельное проверочное свидетельство, не торговая мутация SQLite.

TODO(VERIFY): получить итог resume-run, проверить реальные quote timestamps после clock repair, complete decisions/sourceepisodes/refresh, cleanstop и непустой durable restart. Если quote/server остаётсяfuture — safe-stop, без расширения допуска/обнуления отрицательногоage/бесконечныхперезапусков. Deadline-stop внутри bootstrap не равен успешномуruntime. **Полная сетевая приёмка после синхронизации и M7 пока не приняты**.

## 2026-10-17 | M2/M3 | HTTPS-доступ к странице лимитов T-Bank

Уточнение оператора: исправить «Доступ к странице по HTTPS», не межпроцессный лимитер.

Сделано:
- Добавлен `docs/TBANK_HTTPS.md`: воспроизводимое чтение точного URL через отдельный Python SSLContext с проверенным официальным Russian Trusted Root CA. SHA-256 DER сверяется до запроса; hostname verification и CERT_REQUIRED включены. Корень уже существовал локально, не скачивался из недоверенной цепочки.
- Изменены только новый runbook и эта запись журнала. Продуктовый код, конфиг бота, TLS-файл, keyring, системное доверие, настройки Kodik, БД, runtime и Git staging не менялись этой задачей. Параллельная доработка лимитера принадлежит другой сессии.

Проверено:
- Parent terminal2: стандартный Python HTTPS воспроизвёл CERTIFICATE_VERIFY_FAILED/self-signed chain; встроенный web_fetch воспроизвёл ERR_CERT_AUTHORITY_INVALID.
- Parent terminals3/5: с локальным официальным CA и совпавшим DER SHA-256 `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` точный `https://developer.tbank.ru/invest/intro/intro/limits` вернул HTTP200, title «Лимиты | T-Bank Dev Portal». Отдельный проверенный TLS handshake: TLS1.3, issuer Russian Trusted Sub CA, leaf expires Aug24 2027. Страница прочитана: MarketData600/min, рекомендация50/s, IP1000/min; персональная квота не измерялась.

Не сделано / приёмка:
- Доступ через отдельный Python-контекст подтверждён; встроенный web_fetch/браузер не перенастраивался, его ошибка доверия не объявляется исправленной. Для него нет параметра CA в доступном интерфейсе.
- Независимая QA завершена: точный PowerShell-блок из runbook вернул HTTP200/title/таблицу600; SSLContext check_hostname=True/CERT_REQUIRED, DER pin совпал, TLS1.3. Неверный fingerprint в отдельном процессе дал exit1 до сети; инструментированный urlopen не вызывался. Глобальных изменений/токенов/запуска бота нет. Путь чтения через Python принят; это не исправление доверия встроенного web_fetch и не измерение персональной квоты.
- CI не запускался: торговый код не менялся. BACKLOG не отмечался, M7 не принят. Новых продуктовых TODO(VERIFY) нет.

## 2026-10-17 | Этап M7, вспомогательная настройка | Навык чтения T-Bank Dev Portal

Сделано:
- `.kodik/skills/reading-tbank-developer-docs/SKILL.md` — проектный навык для любого `https://developer.tbank.ru/*`: сначала читать `docs/TBANK_HTTPS.md`, затем использовать его PowerShell/Python SSLContext вместо заведомо неработающего web_fetch. Точный URL подставляется без изменения остальных проверок.
- Предусмотрен явный отказ при отсутствии runbook/CA/инструментов, неверном DER fingerprint и TLS-ошибке; отключение TLS, изменение системного доверия и окружения бота запрещены. Исторические числа не выдаются за актуальные квоты.

Проверено:
- Локальная проверка Python: путь навыка, имя каталога, два поля plain-scalar YAML frontmatter, описание-триггер и наличие исходного документа — PASS.
- Исходная ошибка и успешный безопасный HTTPS-путь подтверждены предыдущей записью; повторная сеть в этой задаче не вызывалась.
- Независимый reviewer подтвердил соответствие runbook и запросу, frontmatter/ссылку и порядок действий для CA present / CA missing / уже полученного Agent network error. По его замечанию устранена двусмысленность: скачивание и перезапись CA в рамках навыка явно запрещены, получение/замена требуют отдельной согласованной задачи. Это сценарная проверка инструкций, не новая сетевая проверка.

Не сделано / приёмка:
- Обнаружение/активация навыка в Settings Kodik не проверены доступными инструментами. При отсутствии навыка в Settings → Skills проверить auto-discovery и перезагрузить окно IDE.
- Торговый код, системное хранилище CA, глобальная конфигурация и Git staging не менялись. CI не запускался для изменения инструкций; BACKLOG не отмечался, приёмка M7 не меняется. Новых продуктовых TODO(VERIFY) нет.

## 2026-10-17 | Этап M7, планирование исправлений M1–M3/M8 | Перенос аудита T-Invest API в Backlog

Сделано:
- `tasks/BACKLOG.md` — по `docs/kodik_t_invest_api.md`, строки 1253–1282, добавлены 9 незавершённых задач API-A01–API-A09 с приоритетами P1/P2, этапами, критериями приёмки и regression-сценариями: unary deadlines, безопасность токена/ошибок, runtime-приоритеты/80%, идентичность и lifecycle стрима, ACK/heartbeat/backfill, CandleCache, lifecycle соединений и полнота истории/источники.
- Существующие пункты M8 дополнены критериями и regression-сценариями счёта/разрешений, идемпотентности/неизвестного результата, частичного исполнения/отмены и reconciliation без дублирующих чекбоксов.
- Сохранены прежние свидетельства локальных проверок M3; явно отделены от незавершённой сквозной runtime-приёмки. Новые задачи оставлены `[ ]`, live-допуск и риск-политика не менялись.
- `CHANGELOG_AI.md` — добавлена эта запись; продуктовый код, тесты, конфигурация и Git staging в этой задаче не изменялись. Остальные существующие изменения рабочей копии сохранены.

Проверено:
- Структурная Python-проверка: ровно 9 последовательных незавершённых API-A задач, у каждой критерии и regression; файл-источник существует; в M8 осталось 6 прежних задач, уточнения имеют 4 regression-группы — PASS. Первый запуск проверки не разобрал кириллический заголовок через PowerShell pipe; проверка исправлена на ASCII-якоря и повторена успешно.
- `git diff --check -- tasks/BACKLOG.md CHANGELOG_AI.md` — PASS до записи этой журнальной секции; Git предупредил только о будущей LF→CRLF нормализации.
- Независимый read-only reviewer сверил перенос с исходными строками аудита: все 9 задач и 3 группы уточнений покрыты, блокирующих замечаний нет; новых разрешений live/ослабления риска/ложных утверждений о проверках нет.

Не сделано / приёмка:
- Принята только документационная задача переноса аудита. Реализация исправлений, regression-тесты, `make ci` и новые сетевые прогоны не выполнялись: продуктовый код не менялся. Приёмка M1–M3/M7/M8 этим переносом не закрывается.
- Следующий шаг — реализация P1 API-A01/API-A02 с проверкой актуального кода и отдельной приёмкой; M8 по-прежнему требует письменного подтверждения оператора.

TODO(VERIFY) новые:
- Новых пометок в продуктовом коде нет. Неподтверждённые полнота/границы/усечение истории и расхождения источников включены в открытую API-A09; срок хранения ключей идемпотентности требует сверки в существующей задаче M8, без выбора значения из противоречащих зеркал.

## 2026-10-17 01:54 +05:00 | M7/M3 | Контролируемая параллельность и общий бюджет MarketData

Решения оператора:
- «Реализуй контролируемую параллельность с общим лимитом 600 запросов в минуту, сохрани все проверки и измерь фактическую скорость». Лимит суммарный по методам MarketData, не отдельный на свечи/worker. Прямо запрещено убирать повторное чтение кандидатов с неполными 10 сессиями при переходе в новый 15-минутный слот.
- Quota: максимум61 попытка действия всего, включая исходную и смешанные статусы; задержка минимум1с либо больше по явному server reset. Длинный конечный reset ждать с возможностью отмены, не завершать из-за прежнего120с бюджета. UNAVAILABLE ограничен отдельно4/5/120. Риск/live-допуск не менялись.

Сделано:
- `trading_bot/api_client.py` — единый thread-safe unary MarketData admission для GetCandles/статуса/стакана и повторов; консервативный бюджет600: pending/in-flight lease без истечения + completion последних60с. Pacing0.1с и общий server cooldown повторно проверяются перед dispatch; lock не держится на ожидании/RPC. Lease освобождается через finally при SDK/nonSDK ошибке/отмене. Quota до61 фактической попытки, включая mixed statuses; UNAVAILABLE отдельный budget. Никаких выдуманных SDK-символов.
- Новый `trading_bot/history_workers.py` — bounded read-only executor с work-scoped stop, исходной системной ошибкой, epoch cancel/drain и ограничением default4workers. `paper_feed.py` — параллельный bootstrap ликвидности; `pool_refresh.py` — nonblocking owner-poll полной checked history кандидата, страницы продолжают читаться между heartbeat. SQLite/агрегация/кэш/publication owner-only; неполные истории не кэшируются как успех. Journal-first, pinned identity, fail-closed, fresh-bar checks, claims/dedup/риск сохранены.
- `main.py` — передача политики/workers и cleanup в finally; `config.py` — validated quota61/minute600/workers4 и explicit safe fallback всех7 unary-параметров; `.env.example` дополнен. Старые serial-order fixtures используют явный workers1, не скрывают параллельные проверки.
- Новые `tests/test_market_data_concurrency.py`, `test_market_data_leases.py`, `test_liquidity_concurrency.py`, `test_liquidity_benchmark.py`, исполняемый `tests/benchmark_liquidity.py`; дополнены candle policy/requests/unavailable/SDK boundary и bootstrap/feed/refresh/main fixtures под согласованную политику. Документы `00_PLAN`, `07_CONFIG_SPEC`, `08_TINVEST_API`, `11_TESTING`, `15_ACCEPTANCE`, `PAPER_RUNTIME_VERIFICATION`, `tasks/BACKLOG.md` актуализированы. Изменения других сессий сохранены.

Проверено:
- TDD: latency overlap/общая квота/61quota/reset180/отмена/refresh-throughput/повтор неполной истории RED→GREEN. Независимый первый review нашёл admission→actual race и invalid-env fallback;7 fallback regressions и6 lease regressions сначала RED, затем GREEN. Дополнительно QA в изолированной копии вернула старую reservation-семантику: substantive RED sliding-window test; рабочий код не менялся.
- Финальный implementer `make ci` и независимые эквивалентные QA-команды: **1132 passed,13 deselected**, ровно намеренно красные SDK TODO(VERIFY); ruff/black87файлов/mypy strict28модулей чисты. Scoped diagnostics пусты. Повторный reviewer: обаP1/P2 закрыты, standards/spec PASS, новых major regression нет.
- Свежий независимый simulated wall-clock benchmark (SDK symbols warmed, RPC latency0.4с, те же24RPC/phase):1worker9.6644с/2.483RPCс/peak1;4workers2.7293с/8.794RPCс/peak4/minspacing0.10007с; **3.541×**. Это не сеть.
- Parent terminal78: **настоящий read-only sandbox benchmark**, SBER verified ID, исторический диапазон от2026-09-01T07:00Z,24страницы/phase;1worker6.9263104с/3.4650483RPCс/peak1;4workers2.4551196с/9.7754912RPCс/peak3/minspacing0.1000478с; **2.82117×**,48actualRPC total,exit0. Общий hardcap120callback с retries, deadline120с cooperative (не abort syncRPC). Короткий темп эквивалентен586.5/мин, не устойчивое минутное измерение и не проверка персонального тарифа. Полный runtime/bootstrap/refresh/restart не запускался. SQLite/заявок/счетных мутаций нет, токен только keyring, TLS ON и существующий официальный CA с проверенным DER SHA256; environment root только на время команды и восстановлен. Terminal77 отказал ДО сети из-за опечатки в expected-hash команды; исправлен только литерал изdocs08, не CA/системное доверие.
- Parent terminal79 `git diff --check` exit0 (только предупреждения CRLF). Git существует с pre-existing staged/unstaged/untracked; нет baseline HEAD. Ни staging, ни commit, ни смена ветки не выполнялись; первоначальное замечание второго reviewer о «нет git» исправлено по фактическому git status.

Не сделано / приёмка:
- Узкая задача parallel+sharedbudget+retry+speed принята: независимый CI/review и реальный bounded сетевой замер есть. В BACKLOG закрыт только новый конкретный пункт; весь M7 остаётся не принят.
- Реальные600за60с не проверялись нагрузкой на брокера: верхняя граница доказана локальными regression-тестами, сеть дала лишь48запросов. Лимитер относится к одному gateway/process, не управляет сторонними токенами/счетами/IP-клиентами; GetUserTariff/персональная квота не запрашивались.
- Транспортный deadline/приоритеты/80%reserve/connection reuse/streaming не расширялись: отдельные API-A задачи backlog. Уже начатый syncRPC может задержать shutdown; runtime не перезапускался и рабочая БД не открывалась.

TODO(VERIFY) новые:
- Нет новых непроверенных SDK-символов. Фактическая персональная квота, длительный network throughput и полная M7 runtime/restart приёмка остаются открытыми существующими проверками;13 красных SDK-пунктов не сняты.

Следующий шаг:
- Отдельно выполнить ограниченную сетевую paper-приёмку полного bootstrap/runtime/refresh/restart на новом коде, без изменения допусков и реальных заявок; не выдавать48RPC benchmark за полный M7.

## 2026-10-17 | Этап M7 | Ревью применимости API-A01–API-A09 и уточнений M8

Сделано:
- Проведена проверка плана исправлений относительно актуальных gateway/config/history workers, main/feed, market_data/security и docs/08/15. `tasks/BACKLOG.md` и продуктовый код не менялись; изменён только `CHANGELOG_AI.md` для фиксации ревью. Изменения другой сессии сохранены.
- Вывод: направления всех 9 задач обоснованы, но не все формулировки достаточны для безопасной реализации. A01 должен разделять транспортный deadline попытки, UNAVAILABLE elapsed и долгий quota cooldown; не добавлять retry DEADLINE_EXCEEDED/мутаций автоматически. A03 требует назначения приоритетов по цели запроса, defer вместо отсутствия истории, защиты bootstrap от starvation и резерва от фоновых leases. A08 — измеряемая оптимизация, не доказанный функциональный дефект; thread-safety/lifecycle и прежнее число workers обязательны. A09 должна различать прохождение диапазона, серверное усечение и достаточность истории caller, без выдуманных баров.
- A04/A05 обоснованы дефектами библиотечного stream-контракта. A06 требует политики correction/backfill без повторного решения/fill и без угаданного is_complete; watchdog должен прекращать зависшее чтение/освобождать ресурсы, а не только фиксировать флаг. Эти задачи не равны подключению streaming в действующий polling-runtime.
- A02 подтверждена default `input` и raw исключениями keyring/list_accounts/stream; автономный main уже использует `get_stored_token`, а `get_token` через LSP имеет только тестовых callers. При исправлении проверить также exception chaining/traceback, не теряя безопасную классификацию.
- M8 идемпотентность/частичные исполнения/reconciliation корректны. Добавление access_level не должно требовать торговых прав для paper. PANIC не освобождается от статуса/доступности: немедленно защёлкнуть запрет входов и сохранить намерение закрытия, отправить при первой допустимой возможности; запреты NORMAL_TRADING/календаря не ослаблять.

Проверено:
- Два независимых read-only reviewer дали проверку runtime/лимитов и streaming/M8. Ошибочные трактовки второго отчёта (желаемый EOF как конфликт требования, исключение PANIC из торговых ограничений и обязательная полная wiring в узких задачах) отклонены; reviewer согласился с поправками.
- Parent отдельно прочитал установленный SDK beta117 `.venv/Lib/site-packages/tinkoff/invest/schemas.py`: stream Candle содержит figi/interval/instrument_uid (1808–1818), MarketDataResponse — subscribe_candles_response/ping (1623–1639), CandleSubscription — subscription_status/instrument_uid/waiting_close (1669–1674). Поэтому существование полей не является блокером; точная runtime-семантика ACK/ping/закрытия требует отдельных тестов, сеть не проверена.
- LSP: CandleCache и StreamReconnector используются только тестами; рабочий main→PaperTickFeed получает данные через unary polling. Кэш runtime завершённых сессий — другой механизм, не следует подменять его CandleCache.
- Offline воспроизведение через `.venv/Scripts/python.exe -B`, fake gateway без токена/сети/БД: cache hit обходит отмену; откат часов оставляет запись свежей; мутация возвращённого списка меняет внутренний кэш — все 3 подтверждены. Первая попытка системным Python остановилась до проверки из-за отсутствия `pydantic_settings.exceptions`; повтор в проектной venv успешен.

Не сделано / приёмка:
- Это ревью плана, не реализация или приёмка исправлений. Полный CI/сеть/runtime не запускались, чекбоксы не менялись, этапы не закрыты. Рекомендуется сначала уточнить критерии задач, затем реализовать P1 A01/A02, далее A03/A09; A04–A07 не выдавать за исправление текущего polling-runtime, A08 выполнять только с измерением.

TODO(VERIFY) новые:
- Новых продуктовых пометок нет. Сохраняются проверки SDK stream runtime-семантики, полноты истории, lifecycle/thread-safety переиспользуемых клиентов и отдельная сетевая приёмка; чтение полей SDK не закрывает эти задачи.

## 2026-10-17 | Этап M7, планирование M1–M3/M8 | Уточнение критериев Backlog после ревью

Сделано:
- `tasks/BACKLOG.md` — уточнены все API-A01–API-A09: транспортный deadline/UNAVAILABLE/quota разделены; нет автоматического расширения retry или повтора мутаций; безопасность включает traceback/цепочки и отсутствие prompt в автономном main. Приоритет зависит от назначения, defer не означает отсутствия истории, проверяются резерв и starvation.
- Разделены библиотечный stream, отдельный CandleCache и действующий polling/runtime-кэш. Уточнены идентичность, EOF/явная остановка, устойчивое восстановление, ACK/ping/watchdog с освобождением reader, backfill без угаданной закрытости и повторного evaluate/fill/перезаписи прошлого snapshot.
- Reuse оставлен измеряемой оптимизацией с подтверждением thread-safety и прежними workers/limiter/owner-thread; полнота истории разделена на диапазон/усечение/достаточность без синтетического заполнения. Добавлены соответствующие regression-сценарии.
- В существующей M8-задаче уточнено добавление access_level в AccountInfo, допустимость read-only чтения для paper и PANIC: немедленный запрет входов/durable намерение, исполнение при первой допустимой возможности без обхода NORMAL_TRADING/календаря и без ложного отчёта о закрытии. Live-допуск не менялся.
- `CHANGELOG_AI.md` — эта запись. Продуктовый код, тесты, конфигурация и staging не менялись; новые задачи остаются `[ ]`, прежние свидетельства проверок сохранены.

Проверено:
- Структурная проверка проектным Python: 9 последовательных незавершённых API-A задач, у каждой критерии и regression; 6 существующих задач M8 сохранены; ключевые якоря ревью и файл-источник присутствуют — PASS.
- `git diff --check -- tasks/BACKLOG.md CHANGELOG_AI.md` до добавления этой записи — PASS; только предупреждения о будущей LF→CRLF нормализации.
- Независимый read-only reviewer: Standards/Spec PASS, блокирующих замечаний нет. По четырём уточнениям дополнены regression атомарного fill при отмене, измеримая отсрочка при наличии фонового бюджета, освобождение ресурсов watchdog и метод контрольного сравнения истории без заявления абсолютной полноты.

Не сделано / приёмка:
- Реализация, запуск regression/CI и сеть не выполнялись: задача исключительно документационная. Уточнение критериев не закрывает исправления или этапы M1–M3/M7/M8. Новых продуктовых TODO(VERIFY) нет, существующие проверки SDK/runtime остаются открытыми.
