# Аудит интеграции с T-Invest API

_Экспортировано 17.10.2026 в 01:01:47 GMT+5 из Kodik (2.2.6)_

---

**Пользователь**

Готово — вот та же структура, но с вашими ссылками, распределёнными по разделам аудита:

---

**Роль и цель**

Ты — senior-разработчик, специализирующийся на интеграциях с биржевыми и брокерскими API. Проведи аудит нашего интеграционного кода с T-Invest API (Т-Банк Инвестиции): сравни наши наработки с официальной документацией и лучшими практиками сообщества, найди, что уже сделано правильно, что можно улучшить, а что необходимо исправить.

**Исходные материалы**

1. **Официальная документация T-Invest API** (основная точка истины):
   - Общие концепции: https://developer.tbank.ru/invest/intro/
   - Полный справочник API: https://developer.tbank.ru/invest/api
   - Полезная информация: https://developer.tbank.ru/invest/intro/useful-info/
2. **Публичные интеграции сообщества** — проанализируй подходы из этих репозиториев (структура клиента, обработка ошибок, ретраи, rate limiting, работа со стримами): https://developer.tbank.ru/invest/intro/intro/robot_contest
3. **Наш код и наработки**

**Чек-лист проверки**

По каждому пункту укажи статус наших наработок: ✅ соответствует, ⚠️ требует доработки, ❌ критично исправить.

- **Аутентификация и токены** — хранение и передача токенов, заголовки, разделение sandbox/prod: https://developer.tbank.ru/invest/intro/intro/token
- **Загрузка истории** — пагинация, диапазоны дат, корректность запросов: https://developer.tbank.ru/invest/intro/intro/load_history
- **Лимиты** — соответствие нашим запросам, троттлинг, backpressure: https://developer.tbank.ru/invest/intro/intro/limits
- **Типовые кейсы** — покрываем ли мы сценарии из примеров корректно: https://developer.tbank.ru/invest/intro/intro/cases
- **Пользовательские типы** — корректность работы с типами данных API: https://developer.tbank.ru/invest/intro/intro/faq_custom_types
- **Идентификация аккаунтов** — как мы работаем с account_id, счета, реестры: https://developer.tbank.ru/invest/intro/intro/faq_identification
- **Торговый статус** — учёт статусов и ограничений торгов: https://developer.tbank.ru/invest/intro/intro/faq_trading_status
- **Детали заявок** — корректность работы со статусами и параметрами ордеров: https://developer.tbank.ru/invest/intro/intro/orders_details
- **Протоколы** — gRPC vs REST vs Streaming: что используем, корректность стримов: https://developer.tbank.ru/invest/intro/developer/protocols/
- **Песочница** — покрытие тестами через sandbox: https://developer.tbank.ru/invest/intro/developer/sandbox/
- **Ошибки WebSocket** — обработка разрывов, переподключение, коды ошибок WS: https://developer.tbank.ru/invest/intro/developer/error-codes/errors-ws
- **Ошибки gRPC** — обработка и ретраи gRPC-ошибок: https://developer.tbank.ru/invest/intro/developer/error-codes/errors
- **HTTP-ошибки** — обработка HTTP-кодов, идемпотентность: https://developer.tbank.ru/invest/intro/developer/error-codes/http_errors
- **Дедлайны и таймауты** — согласование с рекомендациями: https://developer.tbank.ru/invest/intro/developer/deadlines
- **Устаревание API v1** — не используем ли EOL-методы: https://developer.tbank.ru/invest/intro/developer/eol_v1
- **Примеры** — сверка нашего кода с официальными примерами: https://developer.tbank.ru/invest/intro/developer/example
- **Сетевой уровень** — таймауты, keep-alive, пулы соединений: https://developer.tbank.ru/invest/intro/developer/network
- **SLA** — соответствие наших ожиданий по времени ответа: https://developer.tbank.ru/invest/intro/developer/sla
- **Производительность** — узкие места, рекомендации по ускорению: https://developer.tbank.ru/invest/intro/developer/speedup
- **Стриминг** — подписки, восстановление после разрывов, heartbeat: https://developer.tbank.ru/invest/intro/developer/stream
- **Округление валют** — кванты цен и объёмов, таблица порядков округления: https://developer.tbank.ru/invest/intro/developer/table_order_currency

**Требования к результату**

1. **Сводный отчёт**: таблица «Раздел / Что у нас / Что в документации / Статус / Рекомендация» — по каждому пункту чек-листа.
2. **Критические проблемы** — отдельным списком с приоритетом P0/P1/P2.
3. **Конкретные рекомендации** — с примерами кода «как исправить» для топ-5 проблем, а не общими словами.
4. **Что позаимствовать** — 3–5 идей из репозиториев сообщества, которые стоит внедрить, с обоснованием.
5. **Вывод** — общее состояние интеграции в одну-две фразы и план работ на следующую итерацию.

**Ограничения**

- Ссылайся на конкретные разделы документации, чтобы выводы можно было проверить.
- Не выдумывай поведение API: если в документации нет ответа — так и скажи.
- Код наших наработок не переписывай

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tasks/BACKLOG.md

Path: tasks/BACKLOG.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\.agents\skills\context-engineering\SKILL.md

Path: c:\Users\TacereBellum\.agents\skills\context-engineering\SKILL.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/15_ACCEPTANCE.md

Path: docs/15_ACCEPTANCE.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\.agents\skills\using-agent-skills\SKILL.md

Path: c:\Users\TacereBellum\.agents\skills\using-agent-skills\SKILL.md

---

**Kodik**

[Использование инструмента: glob]
Статус: Успешно
Цель: trading_bot/**/*.py

Glob_pattern: trading_bot/**/*.py

---

**Kodik**

Текущий этап — M7; задача — аудит интеграции M2/M3 и её использования в paper-runtime. Критерии сверки — `docs/15_ACCEPTANCE.md`, прежде всего квоты, история, восстановление стримов и изоляция paper/live. Код менять не буду.

Отделю подтверждённые дефекты от ещё не реализованных возможностей и непроверенных сетевых сценариев. Документацию и репозитории сообщества проверю отдельно; недоступные источники явно отмечу.

---

**Kodik**

[Использование инструмента: todo_write]
Статус: Успешно
Цель: todo_write

Todos: [
  {
    "id": "a",
    "title": "Проверить код адаптера, runtime и тестовые свидетельства",
    "status": "in_progress"
  },
  {
    "id": "b",
    "title": "Сверить официальные документы и интеграции сообщества",
    "status": "pending"
  },
  {
    "id": "c",
    "title": "Составить матрицу аудита, приоритеты и пять примеров исправлений",
    "status": "pending"
  }
]

---

**Kodik**

[Использование инструмента: sub_agent]
Статус: Успешно
Цель: sub_agent

Name: Аудит gateway и квот
Agent: reviewer

[Чат субагента: Reviewer]
Цель: Только чтение, ничего не менять/не запускать изменяющее. Проведи аудит trading_bot/api_client.py, market_data.py, history_workers.py и соответствующих tests. Ищи конкретные дефекты истории/лимитов/retry/deadline/stream, с файлами и строками, доказательствами и уровнем уверенности; отличай незавершённые M8 от дефектов. Учитывай возможные параллельные изменения. Изучи docs/08_TINVEST_API.md, docs/15_ACCEPTANCE.md. Верни компактный отчёт и топ проблем; никаких утверждений о прогоне тестов без запуска. Пользов…
Модель: Cheshire
Провайдер: kodik
Инструменты: glob, read_file, read_lints, lsp, grep, send_message

---

**Kodik**

[Использование инструмента: sub_agent]
Статус: Успешно
Цель: sub_agent

Name: Документы и сообщество T-Invest
Agent: researcher

[Чат субагента: Researcher]
Цель: Только чтение. Проверь официальную страницу https://developer.tbank.ru/invest/intro/intro/robot_contest и выбери реальные публичные репозитории оттуда, изучи конкретные исходные файлы 2–3 проектов. Нужно 3–5 заимствуемых идей по client/retry/rate-limit/stream с прямыми ссылками на исходники, отделяя хорошие/небезопасные подходы. Дополнительно официальные docs protocols,stream,network,deadlines,sla,speedup,eol_v1,example под https://developer.tbank.ru/invest/intro/developer/. Известна TLS проблема developer…
Модель: Cheshire
Провайдер: kodik
Инструменты: glob, read_file, read_lints, lsp, grep, web_fetch, web_search, send_message

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/api_client.py

Path: trading_bot/api_client.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/security.py

Path: trading_bot/security.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/00_PLAN.md

Path: docs/00_PLAN.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/TBANK_HTTPS.md

Path: docs/TBANK_HTTPS.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: pyproject.toml

Path: pyproject.toml

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Ошибка
Цель: https://developer.tbank.ru/invest/intro/intro/limits

Url: https://developer.tbank.ru/invest/intro/intro/limits

---

**Kodik**

[Использование инструмента: sub_agent]
Статус: Успешно
Цель: sub_agent

Name: Сверка API-контрактов
Agent: researcher

[Чат субагента: Researcher]
Цель: Read-only аудит официальных требований T-Invest: token,load_history,limits,cases,faq_custom_types,faq_identification,faq_trading_status,orders_details под https://developer.tbank.ru/invest/intro/intro/ ; sandbox,error-codes/errors,error-codes/http_errors,error-codes/errors-ws,table_order_currency под /invest/intro/developer/. web_fetch developer имеет ERR_CERT_AUTHORITY_INVALID (уже проверено), не повторять массово. Найди официальные зеркала tinkoff.github.io/investAPI и github RussianInvestments/investAPI…
Модель: Auto
Провайдер: kodik
Инструменты: glob, read_file, read_lints, lsp, grep, web_fetch, web_search, send_message

---

**Kodik**

[Сообщение агента: Аудит gateway и квот -> Main agent]
Read-only audit фиксирую по текущим чтениям (api_client 1037 строк; docs08 уже содержит новую политику 600 sliding/61 quota, параллельная работа явная в CHANGELOG). Этап M3/M7; приёмка docs15:29/30. Тесты не запускаю, файлов не меняю. Доступных git/shell tools нет: полный diff/HEAD pin недоступен, отмечу ограничение и перепроверю ключевые фрагменты перед финалом.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/api_client.py

Path: trading_bot/api_client.py

---

**Kodik**

[Сообщение агента: Документы и сообщество T-Invest -> Main agent]
Read-only M2/M3 research acceptance: exact public source citations, not runtime acceptance. developer.tbank.ru robot_contest and protocols both fail web_fetch ERR_CERT_AUTHORITY_INVALID; TLS not bypassed. Official mirror https://tinkoff.github.io/investAPI/robot_contest/ fetched; contains 11 project repos, explicit community/on-own-risk warning. Reading 2–3 concrete repos and mirror developer docs next. No files changed.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: requirements.txt

Path: requirements.txt

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/market_data.py

Path: trading_bot/market_data.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot/main.py

Pattern: target|token|timeout|sandbox|GatewayError
Path: trading_bot/main.py

---

**Kodik**

[Использование инструмента: glob]
Статус: Успешно
Цель: tests/*{api,sdk,security,stream,concurr}*.py

Glob_pattern: tests/*{api,sdk,security,stream,concurr}*.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tests/test_security.py

Path: tests/test_security.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tests/test_sdk_symbols_exist.py

Path: tests/test_sdk_symbols_exist.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/api_client.py

Path: trading_bot/api_client.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/logger.py

Path: trading_bot/logger.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot

Pattern: stream_candles|StreamReconnector|CandleCache|RateLimiter
Path: trading_bot

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot/executor.py

Pattern: round|quant|increment
Path: trading_bot/executor.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\.agents\skills\security-review\SKILL.md

Path: c:\Users\TacereBellum\.agents\skills\security-review\SKILL.md

---

**Kodik**

[Сообщение агента: Аудит gateway и квот -> Main agent]
Важное ограничение fixed point: чтение config.py grep сейчас показывает промежуточно повреждённый файл (нет @dataclass перед CandleRequestPolicy, отсутствует max_retry_wait_sec, пустой if на строке65, повторы импортов/полей). Не объявляю самостоятельным дефектом — возможна незавершённая параллельная запись. api_client также 1037→1038 строк. Проверю повторно перед выводом; текущие выводы привязаны к фрагментам, не HEAD.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/config.py

Path: trading_bot/config.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tests/test_market_data_concurrency.py

Path: tests/test_market_data_concurrency.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/services.py

Url: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/services.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/clients.py

Url: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/clients.py

---

**Kodik**

[Использование инструмента: send_message]
Статус: Успешно
Цель: send_message

Target: 10947cc4-a830-42ce-893e-5fe4e134ac67:call_mcCSP4zESKdnm9fNPyifFKYj
Message: Моё read_file config текущие49-69 корректны: dataclass,frozen; max_retry_wait_sec120. Не считай grep контекст точным (наблюдал дубли контекста в grep executor при корректном read). api request deadline actual RPC нет: изучил remote pinned clients.py (Client accepts interceptors/options, no timeout) и services.py with_call without timeout (большой ответ truncated). Есть явный test_market_data_concurrency.py:97 ожидает quota latency не тратить elapsed, текущий _request_candles не компенсирует RPC latency. Подтверди это как потенциальную регрессию, тест не запускать.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/history_workers.py

Path: trading_bot/history_workers.py

---

**Kodik**

[Сообщение агента: Аудит gateway и квот -> Main agent]
Уточнение: повторный read_file config.py сейчас целый (@dataclass48, max_retry_wait_sec55, корректный if64–69). Предыдущее grep-свидетельство повреждения неустойчиво/промежуточно; НЕ включать в дефекты. Подтверждённые кандидаты аудита: stream теряет ID при multi-instrument, штатный EOF не reconnect, raw exc stream→reconnector logs, cache-hit обходит cancellation. Проверяю приоритеты/worker callers и отдельность quota deadline.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tests/test_stream_reconnector.py

Path: tests/test_stream_reconnector.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/metadata.py

Url: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/metadata.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/channels.py

Url: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/channels.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/market_data_stream/market_data_stream_manager.py

Url: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/market_data_stream/market_data_stream_manager.py

---

**Kodik**

[Использование инструмента: send_message]
Статус: Успешно
Цель: send_message

Target: 10947cc4-a830-42ce-893e-5fe4e134ac67:call_jipLYuGhtqJfpxRu2UoGaxlV
Message: Подтверждено pinned SDK clients.py принимает interceptors, options, app_name; channels.py secure_channel default только max_receive; services.with_call показанные методы без timeout. Нужны факты deadlines/network и community 3–5 идей. Не раздувать обзор, 2–3 исходника + официальный список достаточно.

---

**Kodik**

В коде уже есть сильные решения: единый SDK-адаптер, `Decimal`, постраничная история с дедупликацией и общий локальный лимит unary MarketData. При этом нашёл отдельные дефекты стримингового интерфейса: он теряет идентификатор инструмента, а штатное закрытие потока не вызывает реконнект.

Прямое чтение портала Т-Банка упирается в ошибку доверия TLS. Использую доступное официальное зеркало и исходники закреплённой версии SDK; актуальность зеркала относительно нового портала отмечу как ограничение. Тесты в этом аудите не запускаю — различаю прочитанные тест-кейсы и фактически подтверждённые прогоны.

---

**Kodik**

[Использование инструмента: todo_write]
Статус: Успешно
Цель: todo_write

Merge: true
Todos: [
  {
    "id": "a",
    "status": "completed"
  },
  {
    "id": "b",
    "status": "in_progress"
  }
]

---

**Kodik**

[Сообщение агента: Аудит gateway и квот -> Main agent]
Подтверждаю статически новую regression97–111: deadline=1, RPC quota продвигает clock до3; _admit sleep cooldown1 расширяет deadline лишь до2, now4 >deadline2 → CandleRequestError до второго RPC. Ровно api_client671–679 / 689–696 и 341/350–351. С высокой уверенностью нарушение отдельной quota-политики; НЕ прогон теста. Учёл вашу поправку config/grep, повреждение исключаю.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/paper_feed.py

Path: trading_bot/paper_feed.py

---

**Kodik**

[Сообщение агента: Документы и сообщество T-Invest -> Main agent]
Принято: не расширяю community обзор. Уже прочитаны pinned source 3 проектов. Главное: EIDiamond.retry 5 попыток без delay/status filtering; stream reconnect повторяет подписки, но fixed1s без bound, retry set включает CANCELLED/auth-like FAILED_PRECONDITION. roman.requestCandles tries уменьшается, но никогда не проверяется — фактически unbounded recursive retry; ATinkoffAPI exception включает token. qwertyo1 wrapper/cache полезны, но default sandbox=False, ручной __aenter__ без close в файле. Official mirror deadlines: GetCandles/GetOrderBook/GetTradingStatus рекомендованный минимум500ms (не SLA); network/stream exact developer fetch TLS fail, ищу current GitHub equivalent.

---

**Kodik**

[Сообщение агента: Аудит gateway и квот -> Main agent]
Новая параллельная запись устранила quota-latency кандидат: api_client теперь1044 строки, _request_candles rpc_started679 + deadline += monotonic()-rpc_started695 в quota branch. Из актуальных findings убрать; указать как исправлено параллельно, verification только статическая. Есть важный M3-gap: LSP RateLimiter references только tests, runtime gateway limiter не имеет Priority/80% → docs15:28 не интегрировано.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\.agents\skills\security-review\references\data-protection.md

Path: c:\Users\TacereBellum\.agents\skills\security-review\references\data-protection.md

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: trading_bot/paper_feed.py

Pattern: get_trading_status|NORMAL_TRADING_STATUS|is_complete|ThreadPool|HistoryWorkers|RateLimiter|CandleCache
Path: trading_bot/paper_feed.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\.agents\skills\security-review\languages\python.md

Path: c:\Users\TacereBellum\.agents\skills\security-review\languages\python.md

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://grpc.github.io/grpc/python/grpc.html#grpc.ClientCallDetails

Url: https://grpc.github.io/grpc/python/grpc.html#grpc.ClientCallDetails

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/api_client.py

Path: trading_bot/api_client.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/utils.py

Path: trading_bot/utils.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: tests/test_api_client.py

Pattern: negative|nano|quotation|Decimal
Path: tests/test_api_client.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/schemas.py

Url: https://raw.githubusercontent.com/RussianInvestments/invest-python/0.2.0-beta117/tinkoff/invest/schemas.py

---

**Kodik**

[Использование инструмента: glob]
Статус: Ошибка
Цель: .venv/Lib/site-packages/tinkoff/invest/{schemas.py,services.py,clients.py}

Glob_pattern: .venv/Lib/site-packages/tinkoff/invest/{schemas.py,services.py,clients.py}

---

**Пользователь**

[researcher: completed]
Только чтение. Проверь официальную страницу https://developer.tbank.ru/invest/intro/intro/robot_contest и выбери реальные публичные репозитории оттуда, изучи конкретные исходные файлы 2–3 проектов. Нужно 3–5 заимствуемых идей по client/retry/rate-limit/stream с прямыми ссылками на исходники, отделяя хорошие/небезопасные подходы. Дополнительно официальные docs protocols,stream,network,deadlines,sla,speedup,eol_v1,example под https://developer.tbank.ru/invest/intro/developer/. Известна TLS проблема developer.tbank.ru; попробуй web_fetch, если отказ — официальный https://tinkoff.github.io/investAPI/ или github Tinkoff/investAPI как вторичные официальные источники; не обходи TLS. docs/TBANK_HTTPS.md описывает доверенный способ, но менять ничего нельзя. Никаких выдуманных проверок. Верни точные факты, URL и ограничения.
Current sub-agent capacity: 1/6 active; 5 slots available.

## Источники и ограничения

Прямые `web_fetch` к `developer.tbank.ru` для `robot_contest`, `protocols`, `stream`, `network` завершились **`ERR_CERT_AUTHORITY_INVALID`**. TLS не отключал. `docs/TBANK_HTTPS.md` прочитан, но выполнить описанный Python-запрос доступными инструментами не мог. Файлы, системное доверие и журнал не изменял: назначение — только чтение.

Прочитана [официальная зеркальная таблица конкурсных проектов](https://tinkoff.github.io/investAPI/robot_contest/). Она прямо предупреждает: проекты сообщества используются **на свой страх и риск**. Оттуда выбраны и прочитаны исходники **трёх** реально публичных проектов:

- `qwertyo1/tinkoff-trading-bot`, snapshot `207e9189f31ad09349ed296a6b28145e279ff520`;
- `EIDiamond/invest-bot`, snapshot `d7cb416b52aeccf598995f803dd4abb8e753a483`;
- `roman-struchev/tinkoff-invest`, snapshot `8f161fe4fc8aa4f84750935000a0993ba6470649`.

Ниже ссылки закреплены на этих SHA. Это статический анализ, **не запуск проектов и не доказательство совместимости с установленным SDK**.

## Пять применимых идей

### 1. Единый клиентский адаптер и переиспользование клиента

**Источник:** [`qwertyo1/app/client.py`](https://github.com/qwertyo1/tinkoff-trading-bot/blob/207e9189f31ad09349ed296a6b28145e279ff520/app/client.py), класс `TinkoffClient`.

**Наблюдение:** SDK-вызовы собраны в одном wrapper; `ainit()` создаёт долгоживущий `AsyncClient`; выбор sandbox/production сосредоточен в методах адаптера.

**Заимствовать:** единую границу клиента, централизованную политику endpoint/app-name/lifecycle, а не создание соединения на каждый запрос.

**Не копировать буквально:** конструктор имеет `sandbox=False`; wrapper одновременно предоставляет реальные ордерные методы. Это **не архитектурная изоляция paper**. В прочитанном файле вручную вызывается `__aenter__`/`__enter__`, но соответствующего закрытия нет.

### 2. Кэш истории за тем же интерфейсом

**Источник:** тот же [`app/client.py`](https://github.com/qwertyo1/tinkoff-trading-bot/blob/207e9189f31ad09349ed296a6b28145e279ff520/app/client.py), `get_all_candles()`.

**Наблюдение:** caller получает единый интерфейс независимо от включения `MarketDataCache`.

**Заимствовать:** скрыть проверенный кэш завершённых исторических интервалов за gateway — сокращать повторные RPC, не повышая серверную квоту.

**Ограничение:** реализация кэша находится в зависимости и здесь не изучалась. Свежесть, полнота и потокобезопасность этим файлом не доказаны. Синхронный обход кэша внутри async-generator потенциально блокирует event loop; это вывод по структуре кода, не измерение.

### 3. Разделить диагностику ошибки и решение о повторе

**Источник:** [`EIDiamond/invest_error_decorators.py`](https://github.com/EIDiamond/invest-bot/blob/d7cb416b52aeccf598995f803dd4abb8e753a483/invest_api/invest_error_decorators.py).

**Наблюдение:** отдельный logging decorator пишет `tracking_id`, `code`, `details` и повторно возбуждает ошибку; отдельный retry decorator выполняет по умолчанию пять попыток.

**Заимствовать:** раздельные ответственность диагностики и retry-policy; сохранение полезного tracking-id.

**Небезопасная часть:** retry ловит `RequestError` без классификации статуса, без задержки и без server-reset. В [`client_service.py`](https://github.com/EIDiamond/invest-bot/blob/d7cb416b52aeccf598995f803dd4abb8e753a483/invest_api/services/client_service.py) decorator охватывает **весь** `download_historic_candle()`, поэтому ошибка поздней страницы приводит к повтору загрузки целиком. Сырые `repr/details` также нельзя переносить без sanitization.

**Рекомендуемая адаптация:** bounded retry конкретного RPC/страницы, раздельные политики временной недоступности и квоты, общий limiter для каждого фактического повтора. Это рекомендация, не готовая реализация конкурсного проекта.

### 4. После реконнекта восстановить подписки, гарантировать cleanup

**Источник:** [`EIDiamond/market_data_stream_service.py`](https://github.com/EIDiamond/invest-bot/blob/d7cb416b52aeccf598995f803dd4abb8e753a483/invest_api/services/market_data_stream_service.py), `start_async_candles_stream()`.

**Наблюдение:** каждая итерация создаёт stream, заново подписывает весь список инструментов; используется `async with`, в `finally` вызывается `stop()`.

**Заимствовать:** декларативный набор желаемых подписок, повторная подписка после разрыва, явное освобождение stream.

**Не копировать:** fixed sleep 1 секунда, отсутствие лимита реконнектов/backoff/jitter. [`utils.py`](https://github.com/EIDiamond/invest-bot/blob/d7cb416b52aeccf598995f803dd4abb8e753a483/invest_api/utils.py) разрешает повтор в том числе для `CANCELLED`, `FAILED_PRECONDITION`, `DATA_LOSS`, `UNKNOWN`. Сверки пропущенных данных/дедупликации в прочитанном stream-файле нет. Переменная stream также может оказаться не присвоенной, если создание клиента/stream завершится ошибкой до присваивания.

### 5. Разделить bootstrap истории и обработку streaming-событий

**Источники:** [`roman/CandleHistoryService.java`](https://github.com/roman-struchev/tinkoff-invest/blob/8f161fe4fc8aa4f84750935000a0993ba6470649/src/main/java/com/struchev/invest/service/candle/CandleHistoryService.java), [`CandleListenerService.java`](https://github.com/roman-struchev/tinkoff-invest/blob/8f161fe4fc8aa4f84750935000a0993ba6470649/src/main/java/com/struchev/invest/service/candle/CandleListenerService.java).

**Наблюдение:** listener стартует после history service; свеча ищется по инструменту, интервалу и времени и обновляется вместо безусловного добавления.

**Заимствовать:** отдельную bootstrap-фазу и idempotent upsert свечи по устойчивому ключу.

**Критические антипримеры:**
- `requestCandles(..., tries)` уменьшает `tries`, но **никогда не проверяет его**: повтор рекурсивный и фактически неограниченный, несмотря на исходное `12`.
- listener реконнектится рекурсивным `startToListen()` без задержки.
- В callback последовательно выполняются БД и обработка стратегии: возможна задержка чтения stream.
- [`ATinkoffAPI.java`](https://github.com/roman-struchev/tinkoff-invest/blob/8f161fe4fc8aa4f84750935000a0993ba6470649/src/main/java/com/struchev/invest/service/tinkoff/ATinkoffAPI.java) включает **token в текст исключения** при отсутствии счёта — категорически не переносить.

## Что подтверждено официальными вторичными источниками

| Тема | Прочитанный источник и точный смысл |
|---|---|
| Protocols | [api_protocols](https://tinkoff.github.io/investAPI/api_protocols/) перечисляет gRPC, gRPC-web, REST proxy, WebSocket. Это зеркало, актуальность production-endpoints отдельно не проверялась. |
| Stream | [head-marketdata](https://tinkoff.github.io/investAPI/head-marketdata/) описывает unary/bidirectional/server-side stream; сервер посылает ping, отвечать не требуется; возможны **корректирующие свечи после окончания интервала**; для закрытых свечей указан `waiting_close`. Это тематический источник, не подтверждённая копия нынешней страницы `/developer/stream`. |
| Rate-limit metadata | [grpc](https://tinkoff.github.io/investAPI/grpc/) определяет `x-ratelimit-limit`, `remaining`, `reset`; reset — **секунды до обнуления счётчика**. Здесь же `x-tracking-id` и `x-app-name`. |
| Deadlines | [deadlines](https://tinkoff.github.io/investAPI/deadlines/) рекомендует минимальные deadline: `GetCandles`, `GetOrderBook`, `GetTradingStatus` — **500 мс**, `PostOrder`/`CancelOrder` — **1500 мс**. Это **не гарантированная задержка и не рекомендуемый универсальный timeout**. |
| SLA | [sla](https://tinkoff.github.io/investAPI/sla/): срок жизни ключа идемпотентности `PostOrder`/`ReplaceOrder` — **год**; счётчик stream-соединений обновляется каждые **две минуты**. Uptime-SLA на этой странице не приведён. |
| Speedup | [speedup](https://tinkoff.github.io/investAPI/speedup/) предупреждает: клиент должен успевать вычитывать stream, иначе сервер может разорвать соединение. Параллельные запросы рекомендуются для перекрытия latency, а не увеличения квоты. Разные сервисы отражают исполнение с разной задержкой. |
| EOL | [eol_v1](https://tinkoff.github.io/investAPI/eol_v1/): поддержка старого OpenAPI прекращается с мая 2023; старый `invest-openapi` не брать за основу нового клиента. |
| Examples | [example](https://tinkoff.github.io/investAPI/example/) содержит примеры JSON-полей и ключей идемпотентности. Это не подтверждение Python SDK-сигнатур; блоки с комментариями не являются готовым строгим JSON. |

**Оставшийся пробел:** содержание нынешней `/developer/network` не прочитано; точного одноимённого документа нет в полученном [каталоге официального GitHub `src/docs`](https://api.github.com/repos/Tinkoff/investAPI/contents/src/docs?ref=main). Поэтому конкретные значения keepalive, сетевых timeout и reconnect-параметров от имени T-Bank **не утверждаю**.

Тесты, API-запросы с токеном и измерения квоты/скорости не выполнялись.

---

**Пользователь**

[reviewer: completed]
Только чтение, ничего не менять/не запускать изменяющее. Проведи аудит trading_bot/api_client.py, market_data.py, history_workers.py и соответствующих tests. Ищи конкретные дефекты истории/лимитов/retry/deadline/stream, с файлами и строками, доказательствами и уровнем уверенности; отличай незавершённые M8 от дефектов. Учитывай возможные параллельные изменения. Изучи docs/08_TINVEST_API.md, docs/15_ACCEPTANCE.md. Верни компактный отчёт и топ проблем; никаких утверждений о прогоне тестов без запуска. Пользователь просит полный аудит, код не переписывать.
Current sub-agent capacity: 1/6 active; 5 slots available.

Read-only аудит выполнен; файлы не менял, тесты и команды не запускал. Изучены три модуля, профильные тесты, `docs/08_TINVEST_API.md`, `docs/15_ACCEPTANCE.md` и вызывающие пути workers.

### Основные проблемы

| Приоритет | Дефект и доказательство | Уверенность |
|---|---|---|
| **P1** | **Сырые ошибки стрима попадают в логи.** `trading_bot/api_client.py:1043` включает `str(exc)` в `GatewayError`; `trading_bot/market_data.py:214,223` выводит его в ERROR/WARNING. Фильтр SDK в `api_client.py:62` защищает только `GetCandles`. Возможна публикация серверных details/tracking/секретных данных; нарушение AGENTS §4.7. Реальной утечки токена не наблюдал. | Высокая |
| **P2** | **Отсутствие SDK ошибочно ретраится как обрыв сети.** `SdkUnavailableError` наследуется от `GatewayError` (`api_client.py:125`), а реконнектор ловит весь `GatewayError` (`market_data.py:208`). Вопреки контракту `market_data.py:195`, отсутствие SDK вызывает backoff: при base=5/max=10 суммарное ожидание **5115 секунд** вместо немедленного отказа. | Высокая |
| **P2** | **Multi-instrument stream теряет принадлежность свечи.** Подписка принимает несколько ID (`api_client.py:973,1011–1013`), но результат `Candle` (`:1029–1036`) не содержит идентификатора — его нет и в контракте `:194–200`. Отличить события разных инструментов невозможно. `tests/test_api_client.py:542` проверяет только один ID. | Высокая |
| **P2** | **Завершение стрима без исключения навсегда останавливает реконнектор.** После EOF выполняется `return` (`market_data.py:207`), без переподписки или явного отказа. Это противоречит собственному контракту «никогда не завершается сам» (`:192`). `tests/test_stream_reconnector.py:50–54` использует конечный успешный поток и такой сценарий не выявляет. | Высокая |
| **P2 / пробел M3** | **Приоритеты и отсечение при 80% не подключены к runtime.** `RateLimiter` (`market_data.py:111–160`) по LSP используется только тестами; реальный gateway admission (`api_client.py:324–350`) не принимает приоритет. Общая квота реализована, но требование `docs/15_ACCEPTANCE.md:28` сквозным образом не выполнено. Это не отложенная ордерная функциональность M8. | Высокая |
| **P3 / латентный** | **Cache hit обходит отмену.** `market_data.py:74–79` возвращает свечи без `gateway.check_cancelled()`: после установки stop свежий кэш продолжает успешно отдавать результат, вопреки `docs/08_TINVEST_API.md:101–103`. Сейчас `CandleCache` используется только тестами, поэтому влияние на текущий runtime не установлено. | Высокая |

### Standards и Spec

- **Standards: не проходит** — небезопасный вывод исходного исключения стрима.
- **Spec: не проходит полностью** — EOF/retry стрима и отсутствие интеграции приоритетов/80%.
- **Статический просмотр истории/workers: проходит в просмотренной части** — самостоятельного подтверждённого дефекта пагинации, дедупликации и owner-thread разделения не обнаружено. Это не утверждение о прохождении тестов.
- Сверка позиций/заявок после reconnect явно отложена в M8 (`market_data.py:167–169`, `tasks/BACKLOG.md:90`). Это **незавершённое требование**, а не доказанный дефект действующего paper polling runtime. При этом общий критерий M3 `docs/15_ACCEPTANCE.md:30` ещё нельзя считать выполненным.

### Параллельные изменения и ограничения

Во время аудита `api_client.py` изменился с 1038 до 1044 строк. Найденное списание latency quota-RPC из transport deadline **устранено параллельной правкой** (`api_client.py:679,694–695`); в актуальные дефекты не включено. Соответствующий тест `tests/test_market_data_concurrency.py:97–111` прочитан, но не запускался.

Git/HEAD/diff недоступны имеющимися инструментами: отчёт привязан к последним прочитанным фрагментам, не к неизменяемому commit. Полную diff-приёмку и runtime/сетевую приёмку не заявляю.

---

**Kodik**

[Сообщение агента: Аудит gateway и квот -> Main agent]
Финальные устойчивые findings (последний api1044): 1) raw stream exception api1043→market_data214/223 logs SDK details, фильтр api62 только GetCandles; AGENTS4.7 нарушение, высокая, утечка реального токена НЕ наблюдалась. 2) market_data208 catches SdkUnavailableError (subclass GatewayError api125), вопреки doc195 ретраит отсутствие SDK вплоть до суммарно5115с при5/10. 3) multi-ID stream api973/1011–13→1029–36 возвращает Candle без ID (schema194–200), невозможно отличить инструменты; тест api542 только single. 4) EOF market_data207 навсегда return вместо reconnect (doc192); tests reconnect50 специально list finite, нет EOF regression. 5) RateLimiter priority/80% class111 refs только tests; настоящий gateway admission324–350 без priority → M3 docs15:28 не интегрировано (не M8). 6) cache market_data74–79 hit без check_cancelled, нарушает docs08:101–103; сейчас только tests callers, latent. Worker bounded callers проверены owner-state separation; убедительного дефекта истории/worker кроме вашего no-RPC-deadline не нашёл. Quota latency уже убрана параллельно.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/utils.py

Path: trading_bot/utils.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: trading_bot/broker/paper.py

Path: trading_bot/broker/paper.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: .venv/Lib/site-packages/tinkoff/invest/schemas.py

Pattern: ^class Candle\(|^class GetTradingStatusResponse|^class GetCandlesRequest
Path: .venv/Lib/site-packages/tinkoff/invest/schemas.py

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: .venv/Lib/site-packages/tinkoff/invest/services.py

Pattern: def get_candles\(
Path: .venv/Lib/site-packages/tinkoff/invest/services.py

---

**Kodik**

[Использование инструмента: agent_status]
Статус: Успешно
Цель: agent_status

---

**Kodik**

[Использование инструмента: send_message]
Статус: Успешно
Цель: send_message

Target: 10947cc4-a830-42ce-893e-5fe4e134ac67:call_BlPVI1Vps9ankv3b3MAXXYDR
Message: Заверши сейчас компактным отчётом, информации достаточно. Важен конфликт legacy/current, нужны точные URL markdown. Не называй SDK MAX_INTERVALS контрактом сервера: меньшие страницы допустимы, но не проверены count truncation. Остальные прогоны не нужны.

---

**Пользователь**

[researcher: completed]
Read-only аудит официальных требований T-Invest: token,load_history,limits,cases,faq_custom_types,faq_identification,faq_trading_status,orders_details под https://developer.tbank.ru/invest/intro/intro/ ; sandbox,error-codes/errors,error-codes/http_errors,error-codes/errors-ws,table_order_currency под /invest/intro/developer/. web_fetch developer имеет ERR_CERT_AUTHORITY_INVALID (уже проверено), не повторять массово. Найди официальные зеркала tinkoff.github.io/investAPI и github RussianInvestments/investAPI или Tinkoff/investAPI; читай первичный текст, без выдуманных чисел. Для каждого пункта краткие проверяемые требования + URL. Особое внимание history границам/MAX_INTERVALS, 600/min и 50/sec, flags api_trade_available_flag/limit_order_available_flag, Quotation отрицательным nano, accounts access_level, округлению валют vs шаг инструмента. Код не менять.
Current sub-agent capacity: 0/6 active; 6 slots available.

The code uses the SDK's `MAX_INTERVALS` as the page-duration source (matching the docs), which is the correct boundary authority. The existing doc `08_TINVEST_API.md` line 27 records "5m/15m ≤1 day, 1h ≤1 week" — which matches the *legacy* mirror, not the current one. That is a material discrepancy worth flagging. I now have everything to write the audit report.

## Аудит официальных требований T-Invest API (read-only)

Источники: официальное зеркало `RussianInvestments/investAPI` (`src/docs/*.md`, `errors/api_errors.json`, `wc/errors.md`), первичный текст. `developer.tbank.ru` не читался (известный `ERR_CERT_AUTHORITY_INVALID`, не повторялся). Код в репозитории не менялся.

### Ключевой вывод по конфликту источников
Легаси-зеркало `tinkoff.github.io/investAPI/load_history/` **устарело** и противоречит текущему официальному `load_history.md`. Свежие (актуальные) значения — из `src/docs/load_history.md` и `marketdata.md` (`CandleInterval`).

| Интервал | Легаси `tinkoff.github.io` | Актуально `investAPI/main` |
|---|---|---|
| 5 мин | ≤ 1 дня | **до недели** (лимит 2400) |
| 15 мин | ≤ 1 дня | **до 3 недель** (лимит 2400) |
| 1 час | ≤ 1 недели | **до 3 месяцев** (лимит 2400) |
| 1 день | ≤ 1 года | **до 6 лет** (лимит 2400) |
| 1 неделя | ≤ 2 лет | до 5 лет (лимит 300) |
| 1 месяц | ≤ 10 лет | до 10 лет (лимит 120) |

Источник: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/load_history.md ; https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/marketdata.md (enum `CandleInterval`). Общий потолок одного запроса: **не более 1 календарного года** (`head-marketdata.md`).

**Замечание по репозиторию (не изменение):** `docs/08_TINVEST_API.md:27` фиксирует «5m/15m ≤1 день, 1h ≤1 неделя» — это легаси-значения. Код `trading_bot/api_client.py:617` берёт длину страницы из SDK `MAX_INTERVALS[interval]`, что совпадает с актуальными значениями; текст документа 08 расходится с актуальным зеркалом и требует правки по PR-протоколу (вне рамок этой задачи).

### Проверяемые требования по пунктам

**token** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/token.md
- Токен обязателен, передаётся в `metadata` каждого unary-запроса и при установке stream-соединений; заголовок `Authorization: Bearer <token>`.
- Виды: readonly, full-access, token-на-один-счёт, sandbox. Sandbox-токен для обычных методов/стримов возвращает ошибку → `40003`.
- Срок жизни — 3 месяца с даты последнего использования; `40003` = неактуальный токен.

**load_history / MAX_INTERVALS** — см. таблицу выше. `GetCandles` — unary-метод сервиса котировок. Дата начала истории у одного инструмента может отличаться по интервалам; глубина различается по инструментам. Для дневных свечей `CANDLE_INTERVAL_DAY` значения `from`/`to` игнорируются (время суток отбрасывается). Если запросить окно короче таймфрейма — вернётся пустой массив.

**limits (600/min, 50/sec, IP 1000/min)** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/limits.md
- Сервис котировок (MarketData, gRPC+REST): **600 unary-запросов/мин** — суммарно по всем методам сервиса.
- Общая рекомендация: суммарно по всем счетам и токенам **не более 50 запросов/с**.
- Если число запросов с IP клиента **не превышает 1000 в минуту**, превышения не будет.
- Другие лимиты unary/мин: инструменты 200, счета 100, операции 200, стоп-ордера 50, песочница 200, ордера 100, `getHistory` (REST) 30, `getOrders` 200, `postOrder` 300, `cancelOrder` 100, `postOrderAsync` 450.
- Стримы: котировок 16 (грейды 1–4) / 64 (грейд 5) активных соединений; ордеров 10/16; операций 2. Внутри одного стрима котировок — **300 одновременных подписок** суммарно (свечи+стаканы+сделки); на `Info` лимита нет. Плюс рекомендация ≤50/с и IP 1000/мин. В `faq_marketdata.md` указано: >100 запросов на подписку в минуту → `SUBSCRIPTION_STATUS_TOO_MANY_REQUESTS`.
- Данные серверных полей лимитов: `GetUserTariff` → `UnaryLimit{limit_per_minute, limit_per_second, methods}` и `StreamLimit{limit, streams, open}` (users.md). Значения **могут возвращаться на 1 больше** — для переподключения стримов.

**cases** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/cases.md
- Порядок создания заявки: `FindInstrument` → `GetTradingStatus` → `GetLastPrice` → `GetInstrumentBy` (для `min_price_increment`) → проверка счёта → `PostOrder` (сохранить `order_id`) → `GetOrders` для проверки в активных.
- Раздел «Ошибки с сертификатом»: симптомы `failed to connect to all addresses`, `handshake failed`, `certificate verify failed`. Это тот же класс проблемы, что зафиксирован в `docs/TBANK_HTTPS.md`.

**faq_custom_types (Quotation, отрицательный nano)** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_custom_types.md
- `Quotation`: `units` (int64) — целая часть, **может быть отрицательной**; `nano` (int32) — дробная часть, **может быть отрицательной**. Пример: `-0.01` → `{"units": "-0", "nano": -10000000}`; `-200.20` → `{"units": "-200", "nano": -200000000}`. Значит, знак определяется `units`, а `nano` несёт знак согласованно (нельзя считать `nano` всегда неотрицательным).
- `Timestamp`: от `0001-01-01T00:00:00Z` до `9999-12-31T23:59:59.999999999Z`, всегда UTC; `nanos` 0..999999999.
- Правило из `errors.md`/`api_errors.json` (код 30017): `units` и `nanos` не могут иметь разные знаки, и модуль `nanos` должен быть < 999 999 999.

**faq_identification** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_identification.md
- Идентификаторы: ISIN, Ticker (+ `class_code` обязателен для уникальности), FIGI (устаревший, не рекомендуется), UID.
- `Instrument_Id` принимает FIGI или `instrument_uid`; поиск сначала по `instrument_uid`, потом по FIGI. Для опционов FIGI не поддерживается → основной идентификатор **UID**.
- Иерархия: brand `uid` → `asset_uid` → `position_uid` → `instrument_uid` (торговый инструмент).

**faq_trading_status** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_trading_status.md
- `SecurityTradingStatus`, 17 значений (`UNSPECIFIED`..`DEALER_NOT_AVAILABLE_FOR_TRADING`, номера 0..16 — полный список в `marketdata.md`).
- Таблица «статус → какие типы поручений разрешены». Примеры: `NORMAL_TRADING` (5) — все типы; `OPENING_PERIOD`/`CLOSING_PERIOD` — только лимит и bestprice; `BREAK_IN_TRADING`/`NOT_AVAILABLE_FOR_TRADING`/`SESSION_CLOSE` — ничего; `DARK_POOL_AUCTION` — только рыночные; `SESSION_ASSIGNED` — ничего.
- **Нормальное имя для проверки перед заявкой — `SECURITY_TRADING_STATUS_NORMAL_TRADING`** (enum), но «все типы поручений» дают и `CLOSING_AUCTION`, `DISCRETE_AUCTION`, `OPENING_AUCTION_PERIOD`, `SESSION_OPEN`. Строгая политика бота (только `NORMAL_TRADING`) строже официальной таблицы — это допустимо, но не выдаётся за требование API.

**orders_details** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/orders_details.md
- Лимитная заявка: цена ограничена `limit_up`/`limit_down`; **иногда `limitDown` > `limitUp`** (limitUp ограничивает покупку, limitDown — продажу).
- Рыночная заявка может исполниться по нескольким ценам (проскальзывание при низкой ликвидности).
- Best price (ORDER_TYPE_BESTPRICE = «лучшая цена»): брокер блокирует ~0.3% сверх суммы; при уходе цены хуже чем на 0.3% — автоотмена.
- Признак «только лучшая цена» для инструмента: `only_best_price`/`bestprice_order_available_flag` (см. `GetTradingStatusResponse`); ошибка `30103`.
- Перед выставлением проверяются: доступность счёта, достаточность активов/маржи, доступность инструмента (`getTradingSchedules`, `GetTradingStatus`).

**sandbox** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/sandbox.md ; `head-sandbox.md` ; `faq_sandbox.md`
- Счёт песочницы: `OpenSandboxAccount` (тело `{name}`), список — `GetAccounts`/`GetSandboxAccounts`, закрытие — `CloseSandboxAccount`; `SandboxPayIn` — пополнение (`MoneyValue`, только рубли). Заявки: `PostSandboxOrder`/`GetSandboxOrderState`/`CancelSandboxOrder`/`GetSandboxOrders`/`ReplaceSandboxOrder`.
- Ограничения: нет стоп-заявок и маржинальных показателей; средняя цена покупки не рассчитывается; шорты эмулируются без расходов; `GetMarginAttributes` возвращает `liquid_portfolio=стоимость портфеля`, остальные нули, `funds_sufficiency_level=2`; плечо везде = 2; комиссия 0,05%; исполнение по last price; неисполненные заявки отменяются в конце сессии; счета живут 3 месяца и могут исчезнуть.
- Только через sandbox-адрес `sandbox-invest-public-api.tinkoff.ru:443`. Сервис стоп-заявок и часть методов операций в песочнице недоступны (см. `url_difference.md`).

**errors (коды)** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/errors.md и канонический `errors/api_errors.json`
- Канонический числовой список с типом (`INVALID_ARGUMENT`/`PERMISSION_DENIED`/`UNAUTHENTICATED`/`NOT_FOUND`/`INTERNAL`/`RESOURCE_EXHAUSTED`/`FAILED_PRECONDITION`/`UNIMPLEMENTED`/`UNAVAILABLE`). Релевантные: `30052` (инструмент запрещён для API, проверяй `api_trade_available_flag`), `30078` («Incorrect minimum price increment»: остаток `price / min_price_increment` должен быть 0), `30099` (цена вне лимитов), `30068` (только лимитная заявка), `30069` (`limit` ≤ 1000), `30220` (`candle_type` нельзя вместе с `limit`), `30235` (для интервала данные за 30 дней), `30097`/`30098` (сессия/инструмент), `80001`–`80005` (`RESOURCE_EXHAUSTED`), `40002/40003/40004`, `90002` (только для квалифицированных), `90003` (цена заявки слишком высокая), `35001` (лимит sandbox-счетов).
- **`RESOURCE_EXHAUSTED` (`80002`) = «превышен лимит запросов в минуту»**, но у того же типа есть и другие причины (`80001` streams, `80003` SMS, `80004/80005`), что совпадает с записью в `docs/08_TINVEST_API.md:34`.

**http_errors** — файла `errors_http.md` в зеркале **нет** (404). Единственный найденный HTTP-контекст: заголовки ответов `x-ratelimit-limit`, `x-ratelimit-remaining`, `x-ratelimit-reset` (время в **секундах** до сброса счётчика, не epoch) — `grpc.md`. Числовые коды ошибок API — это прикладные коды (см. выше), не HTTP-статусы. **Вывод: отдельной официальной таблицы HTTP-статусов в первичном зеркале нет; не выдумывать.**

**errors-ws** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/ws/errors.md
- WebSocket-коды: `4003` INVALID_ARGUMENT, `4004` DEADLINE_EXCEEDED, `4008` RESOURCE_EXHAUSTED (превышен лимит запросов/мин), `4016` UNAUTHENTICATED.
- `stream.md`: ping во всех стримах — интервал 120000 ms.

**table_order_currency** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/table_order_currency.md (+ `points.md`, `faq_marketdata.md`)
- MarketData: Share/ETF/Currency — **в валюте**; Bond/Futures — **в пунктах**.
- `PostOrder` входящая цена лимитной заявки: Share/ETF/Currency → валюта; Bond/Futures → валюта (но с учётом `PriceType`). SHARE: цена за 1 бумагу; стоимость лота = `price * lot`.
- Формулы: Акции `price * lot`; Облигации `price/100*nominal`; Валюта `price * lot / nominal` (для JPY/AMD/KZT `nominal=100`); Фьючерсы `price / min_price_increment * min_price_increment_amount`.

**flags `api_trade_available_flag` / `limit_order_available_flag`** — `marketdata.md` (`GetTradingStatusResponse`, `TradingStatus`)
- `GetTradingStatusResponse`: `trading_status`, `limit_order_available_flag`, `market_order_available_flag`, `api_trade_available_flag`, `bestprice_order_available_flag`, `only_best_price`.
- `api_trade_available_flag=false` → торговля через API недоступна (ошибка `30052`; для OTC — `30077`). Проверять доступность торгов через API по этому флагу рекомендует `faq_marketdata.md`.
- У `Share` (instruments) есть те же флаги: `api_trade_available_flag`, `buy_available_flag`, `sell_available_flag`, `short_enabled_flag`, `for_qual_investor_flag` (зафиксировано в `docs/08_TINVEST_API.md:44-47`).
- Важно: `limit_order_available_flag`/`market_order_available_flag` есть в `TradingStatus` (стрим) и в `GetTradingStatusResponse`; в `GetTradingStatusesResponse` они идут внутри каждого `GetTradingStatusResponse`.

**accounts `access_level`** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/users.md
- `Account{id, type, name, status, opened_date, closed_date, access_level}` — **валюты у счёта нет** (это соответствует записи `docs/08_TINVEST_API.md:24`).
- `AccessLevel`: `UNSPECIFIED`(0), `FULL_ACCESS`(1), `READ_ONLY`(2), `NO_ACCESS`(3). Определяется **токеном**.
- `AccountStatus`: `NEW`(1), `OPEN`(2), `CLOSED`(3), `ALL`(4). `AccountType`: TINKOFF(1), TINKOFF_IIS(2), INVEST_BOX(3), INVEST_FUND(4), DEBIT(5), SAVING(6).
- Read-only токен при попытке операции → `40002 PERMISSION_DENIED`.

**Округление валют vs шаг инструмента** — `orders.md` (раздел «Числа с плавающей точкой»), `faq_marketdata.md`, `table_order_currency.md`
- Для заявок/котировок задан шаг цены; float небезопасен. Пример из docs: шаг `0.1`, цена `256.8` во float = `256.79998779296875`; брокер округляет **в пользу клиента**: покупка → `256.7`, продажа → `256.8`.
- Валидность цены: `price % min_price_increment == 0` (иначе `30078`). `min_price_increment` берётся из инструмента, не хардкодится.
- Для валютных инструментов учитывать `nominal` (JPY/AMD/KZT = 100) и лотность (EUR/USD кратно 1000) — `faq_marketsdata.md`, `faq.md`.

### Оговорки и остаточная неопределённость
- Все числа выше — **опубликованные** значения официального зеркала, а не измеренная персональная квота токена; динамический тариф/грейд может менять фактические лимиты.
- Страницы портала `developer.tbank.ru/invest/intro/developer/error-codes/http_errors` в зеркале нет; официального первичного текста по HTTP-статусам не найдено — не воспроизводил.
- Легаси-зеркало `tinkoff.github.io` содержит устаревшие значения `load_history` (см. конфликт выше); использовать только `RussianInvestments/investAPI/main`.
- Точная семантика включения границ `from`/`to` сетевого `GetCandles` (inclusive/exclusive) в первичном тексте не зафиксирована; код трактует диапазон как inclusive, что соответствует формулировке «Начало/Окончание запрашиваемого периода».
- Код не изменялся.

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/load_history.md

Url: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/load_history.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: .venv/Lib/site-packages/tinkoff/invest/utils.py

Path: .venv/Lib/site-packages/tinkoff/invest/utils.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: .venv/Lib/site-packages/tinkoff/invest/schemas.py

Path: .venv/Lib/site-packages/tinkoff/invest/schemas.py

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: .venv/Lib/site-packages/tinkoff/invest/services.py

Path: .venv/Lib/site-packages/tinkoff/invest/services.py

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/deadlines.md

Url: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/deadlines.md

---

**Kodik**

[Сообщение агента: Сверка API-контрактов -> Main agent]
# Аудит официальных требований T-Invest API — итог

Источники: первичный текст официального зеркала `RussianInvestments/investAPI` (`src/docs/*.md`, `errors/api_errors.json`, `ws/errors.md`). `developer.tbank.ru` не читался. Код не менялся.

## Главный конфликт: legacy vs current

| Интервал | Legacy `tinkoff.github.io` (**устарело**) | Current `RussianInvestments/investAPI` |
|---|---|---|
| 5 мин | ≤ 1 дня | **до недели** (limit 2400) |
| 15 мин | ≤ 1 дня | **до 3 недель** (limit 2400) |
| 1 час | ≤ 1 недели | **до 3 месяцев** (limit 2400) |
| 1 день | ≤ 1 года | **до 6 лет** (limit 2400) |
| 1 неделя | ≤ 2 лет | до 5 лет (limit 300) |
| 1 месяц | ≤ 10 лет | до 10 лет (limit 120) |

Точные URL markdown (primary, current):
- https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/load_history.md
- https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/marketdata.md (enum `CandleInterval`)
- Общий потолок одного запроса — 1 календарный год: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/head-marketdata.md

Legacy-источник (не использовать): https://tinkoff.github.io/investAPI/load_history/ — подтверждённо отдаёт устаревшие значения.

**Про репозиторий (наблюдение, без изменений):** `docs/08_TINVEST_API.md:27` фиксирует legacy-значения («5m/15m ≤1 день, 1h ≤1 неделя»). Это расхождение с актуальным зеркалом; требует правки текста по PR-протоколу, вне рамок этой задачи.

**Про `MAX_INTERVALS` (поправка по указанию parent):** SDK `MAX_INTERVALS` — **не серверный контракт**. Это верхняя граница helper-страницы; меньшие окна допустимы. Реальное усечение выборки по `limit`/числу свечей на стороне сервера из первичного текста не подтверждено — **не проверено**, не трактую `MAX_INTERVALS` как гарантию сервера.

## Проверяемые требования по пунктам (с URL)

- **token** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/token.md: `Authorization: Bearer <token>` в metadata каждого unary-запроса и при установке стримов; виды readonly/full-access/один-счёт/sandbox; sandbox-токен на обычных методах → `40003`; срок 3 месяца с последнего использования.
- **limits** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/limits.md: MarketData **600 unary/мин** (суммарно по методам сервиса); рекомендация **≤50 запросов/с** по всем счетам/токенам; **1000/мин с IP** — превышения не будет. Прочие: инструменты 200, счета 100, операции 200, стоп-ордера 50, песочница 200, ордера 100, `getOrders` 200, `postOrder` 300, `cancelOrder` 100, `postOrderAsync` 450, `getHistory` 30 (REST). Стримы: котировок 16 (грейды 1–4)/64 (5); в одном стриме котировок **300 подписок** суммарно (свечи+стаканы+сделки), на `Info` лимита нет. >100 запросов на подписку/мин → `SUBSCRIPTION_STATUS_TOO_MANY_REQUESTS` (https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_marketdata.md). Серверные поля лимитов: `GetUserTariff` → `UnaryLimit{limit_per_minute,limit_per_second,methods}`, `StreamLimit` (users.md; значения могут приходить на 1 больше).
- **cases** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/cases.md: порядок FindInstrument→GetTradingStatus→GetLastPrice→GetInstrumentBy→PostOrder→GetOrders; раздел про ошибки сертификата (`certificate verify failed`, `handshake failed`).
- **faq_custom_types** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_custom_types.md: `Quotation.units` (int64) и `.nano` (int32) **могут быть отрицательными**; `-0.01` → `{"units":"-0","nano":-10000000}`. `Timestamp` 0001-01-01…9999-12-31, UTC, nanos 0..999999999. Правило (код 30017, errors.md): знаки `units`/`nanos` согласованы, модуль `nanos` < 999 999 999.
- **faq_identification** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_identification.md: ticker уникален только с `class_code`; FIGI устаревший; `Instrument_Id` = UID или FIGI (поиск сначала UID); основной идентификатор — UID.
- **faq_trading_status** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/faq_trading_status.md: 17 значений `SecurityTradingStatus`; `SECURITY_TRADING_STATUS_NORMAL_TRADING` — все типы поручений; но `CLOSING_AUCTION`/`DISCRETE_AUCTION`/`OPENING_AUCTION_PERIOD`/`SESSION_OPEN` тоже допускают все типы (строгая политика бота — строже официальной таблицы).
- **orders_details** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/orders_details.md: `limit_up`/`limit_down`, **`limitDown` иногда > `limitUp`**; best price блокирует ~0.3%, автоотмена при движении хуже 0.3%; проверки счёта/активов/доступности.
- **sandbox** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/sandbox.md, head-sandbox.md, faq_sandbox.md: `OpenSandboxAccount{name}`/`SandboxPayIn`/`CloseSandboxAccount`, `PostSandboxOrder`/`GetSandboxOrderState`/`CancelSandboxOrder`/`GetSandboxOrders`/`ReplaceSandboxOrder`; нет стоп-заявок и маржи; плечо=2, комиссия 0,05%, исполнение по last price; счета живут 3 месяца; только `sandbox-invest-public-api.tinkoff.ru:443`.
- **errors** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/errors.md + канонический https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/errors/api_errors.json: `30052` (проверяй `api_trade_available_flag`), `30078` («Incorrect minimum price increment», остаток от деления на `min_price_increment` = 0), `30099`, `30068`, `30069` (`limit` ≤ 1000), `30220`, `30235`, `40002/40003/40004`, `80001–80005`, `90002/90003`, `35001`. `80002` = «превышен лимит запросов в минуту», но `RESOURCE_EXHAUSTED` включает и другие причины.
- **http_errors** — файла `errors_http.md` в зеркале **нет** (404). Единственный официальный HTTP-контекст: заголовки `x-ratelimit-limit`/`x-ratelimit-remaining`/`x-ratelimit-reset` (секунды до сброса, не epoch) — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/grpc.md. Отдельной таблицы HTTP-статусов в первичном зеркале нет; не выдумывал.
- **errors-ws** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/ws/errors.md: `4003` INVALID_ARGUMENT, `4004` DEADLINE_EXCEEDED, `4008` RESOURCE_EXHAUSTED, `4016` UNAUTHENTICATED. Ping всех стримов 120000 ms — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/stream.md
- **table_order_currency** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/table_order_currency.md (+ points.md, faq_marketdata.md): MarketData — Share/ETF/Currency в валюте, Bond/Futures в пунктах; формулы Акции `price*lot`, Облигации `price/100*nominal`, Валюта `price*lot/nominal` (JPY/AMD/KZT nominal=100), Фьючерсы `price/min_price_increment*min_price_increment_amount`.
- **flags** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/marketdata.md: в `GetTradingStatusResponse` есть `trading_status`, `limit_order_available_flag`, `market_order_available_flag`, `api_trade_available_flag`, `bestprice_order_available_flag`, `only_best_price`; `api_trade_available_flag=false` → API-торговля недоступна (`30052`), OTC → `30077`.
- **accounts `access_level`** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/users.md: `Account{id,type,name,status,opened_date,closed_date,access_level}` — **валюты нет**; `AccessLevel` UNSPECIFIED/FULL_ACCESS/READ_ONLY/NO_ACCESS (определяется токеном); readonly → `40002`.
- **округление валют vs шаг** — https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/orders.md (раздел про float): валидность `price % min_price_increment == 0`; брокер округляет в пользу клиента (покупка вниз, продажа вверх; пример 256.8 → 256.7/256.8). `min_price_increment` — из инструмента, не хардкод.

## Остаточная неопределённость
- Числа лимитов — опубликованные значения, не измеренная персональная квота токена.
- HTTP-статусы: официального первичного текста нет.
- Усечение сервером по числу свечей/`limit` в первичном тексте не зафиксировано — **не проверено**.
- Точная семантика включения границ `from`/`to` в первичном тексте не зафиксирована.

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/sla.md

Url: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/sla.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/08_TINVEST_API.md

Path: docs/08_TINVEST_API.md

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/stream.md

Url: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/stream.md

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/table_order_currency.md

Url: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/table_order_currency.md

---

**Kodik**

[Использование инструмента: web_fetch]
Статус: Успешно
Цель: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/limits.md

Url: https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/limits.md

---

**Kodik**

Обнаружилось важное расхождение источников: старое зеркало `tinkoff.github.io` и официальный репозиторий `RussianInvestments/investAPI` дают разные значения для истории и срока хранения ключей идемпотентности. В отчёте буду ссылаться на конкретный прочитанный исходник, а не смешивать эти версии.

Установленный SDK действительно использует небольшие страницы — сутки для 5m/15m и неделю для 1h. Это допустимая консервативная нарезка, а не доказательство ошибки загрузки. Также исключил из замечаний проблему учёта quota-latency: её исправила параллельная сессия во время аудита; здесь это подтверждено только чтением кода.

---

**Kodik**

[Использование инструмента: todo_write]
Статус: Успешно
Цель: todo_write

Merge: true
Todos: [
  {
    "id": "b",
    "status": "completed"
  },
  {
    "id": "c",
    "status": "in_progress"
  }
]

---

**Kodik**

[Использование инструмента: todo_write]
Статус: Успешно
Цель: todo_write

Merge: true
Todos: [
  {
    "id": "c",
    "status": "completed"
  }
]

---

**Пользователь**

Добавь в backlog исправления по этому аудиту с критериями приёмки и regression-тестами, без изменения кода.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: tasks/BACKLOG.md

Path: tasks/BACKLOG.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: docs/15_ACCEPTANCE.md

Path: docs/15_ACCEPTANCE.md

---

**Kodik**

[Использование инструмента: grep]
Статус: Успешно
Цель: CHANGELOG_AI.md

Pattern: ^## 
Path: CHANGELOG_AI.md

---

**Kodik**

Для записи в `tasks/BACKLOG.md` нужен **режим Code**: текущий режим Ask разрешает только чтение. **Файлы не изменены.** Подготовил следующие пункты; все должны остаться `[ ]` до реализации и проверки.

Этапы — M2/M3 и будущий M8. Основание приёмки: `docs/15_ACCEPTANCE.md:19–31` и `docs/15_ACCEPTANCE.md:71–78`.

## Дополнения к backlog

| Приоритет / этап | Задача | Критерии приёмки | Regression-тесты |
|---|---|---|---|
| **P1 · M2** | **Ограничить длительность каждого unary RPC** | Реальный deadline установлен на транспортном уровне, включая каталог и счета. Значения заданы конфигурацией. Retry-budget и deadline отдельного RPC не смешаны. Отмена запрещает последующие вызовы, зависший RPC не удерживает worker бесконечно. | Задержанный локальный gRPC-сервис завершается по deadline; более короткий исходный timeout не увеличивается; исчерпание бюджета не запускает новый RPC; остановка не публикует частичный пул. |
| **P1 · M1/M2** | **Закрыть небезопасные пути ввода токена и вывода ошибок** | Стандартный ввод скрытый; при невозможности скрытого ввода нет молчаливого перехода к echo. Публичные ошибки и SDK-логи используемых методов не содержат токен, сырые details или metadata. Безопасная классификация auth/transport сохраняется. | Контрольный секрет в ошибках keyring, счетов, каталога, свечей и стрима отсутствует в логах и публичном выводе; штатное маскирование сохранено; небезопасный fallback ввода отвергается. |
| **P2 · M3** | **Подключить приоритеты и порог 80% к действующему limiter** | Один общий счётчик фактических MarketData RPC, включая страницы и повторы. Фоновые запросы не расходуют срочный резерв; срочные не обходят общий cooldown и потолок 600/60s. Ордерные квоты не смешиваются с MarketData. | Конкурентный фоновый шквал + срочный запрос; точные границы 80% и 600/60s; общий cooldown для методов; отмена ожидающего запроса; отсутствие обхода лимита через retry. |
| **P2 · M3** | **Сохранить идентичность streaming-событий** | Событие содержит UID/FIGI и таймфрейм; принадлежность проверяется по подписке. Неизвестный инструмент не передаётся стратегии. SDK-объекты остаются внутри адаптера. | Два инструмента с одинаковыми временем и ценой различимы; UID-only событие корректно сопоставляется; неизвестная идентичность отклоняется. |
| **P2 · M3** | **Исправить lifecycle и классификацию отказов стрима** | EOF вызывает ограниченный реконнект, кроме явной остановки. Отсутствие SDK, ошибка конфигурации и авторизации не повторяются. Backoff ограничен и прерывается отменой; ресурсы закрываются на каждом пути. | EOF до/после данных; transient recovery и exhaustion; SDK/auth без retry; отмена во время backoff; cleanup при ошибке подписки и закрытии генератора. |
| **P2 · M3** | **Обрабатывать ACK, heartbeat и восстановление пропусков** | Отрицательный ACK не считается успешной подпиской. Watchdog обнаруживает отсутствие ping даже при заблокированном чтении. После reconnect выполняются переподписка, backfill и дедупликация; неполное восстановление не разрешает входы. | Отрицательный ACK; ping без свечей; зависший поток; gap с пересечением истории; корректирующее обновление свечи; повтор reconnect без дублей; провал backfill. |
| **P2 · M3** | **Устранить латентные дефекты CandleCache** | Cache hit соблюдает cancellation; откат часов не продлевает свежесть; незавершённые свечи не маскируются полным TTL; caller не может изменить внутренний кэш. Не подключать к runtime до проверки. | Отмена перед hit; clock rollback; граница TTL; переход незавершённой свечи в закрытую; изменение возвращённого списка не влияет на следующий результат. |
| **P2 · M2/M3** | **Переиспользовать соединения с явным lifecycle** | Каналы ограничены и закрываются при штатной остановке и ошибках. Параллельность не нарушает квоту и owner-thread владение SQLite. Выигрыш подтверждён измерением, а не предположением. | Счётчик создания/закрытия каналов; конкурентные обращения; исключение при старте; shutdown с in-flight RPC; benchmark нового и переиспользуемого канала на одинаковых данных. |
| **P2 · M3** | **Подтвердить полноту истории и актуализировать источники** | Малые страницы SDK не объявляются дефектом. Проверены полнота, границы и возможное усечение ответа. Документация различает серверные ограничения, SDK-константы и клиентскую политику; расхождения источников явно записаны. | Плотная история; границы страниц; идентичные/конфликтующие дубли; пустая и усечённая страница; системный отказ без частичной публикации. Отдельный read-only sandbox-прогон с зафиксированными параметрами. |

## Уточнения существующих задач M8

Не создавать дубликаты пунктов `tasks/BACKLOG.md:85–91`, а дополнить их критериями:

- **Счёт и торговые разрешения:** сохранить `access_level`; проверять открытый выбранный счёт, актуальный статус и флаги доступности API/типа заявки непосредственно перед отправкой.  
  **Тесты:** read-only/закрытый счёт, смена разрешений после bootstrap, отказ API при `NORMAL_TRADING`.

- **Идемпотентность и неизвестный результат:** request ID сохраняется до отправки; после неоднозначного ответа запрещён слепой повтор с новым ID; выполняется сверка.  
  **Тесты:** сервер принял заявку, ответ потерян; рестарт; повтор события исполнения; недоступная сверка блокирует новые заявки.

- **Частичное исполнение, отмена и reconciliation:** учёт каждой сделки отдельно; гонка fill/cancel не дублирует объём; после реконнекта сверяются заявки, исполнения и позиции.  
  **Тесты:** fill до/во время отмены, позднее исполнение, повтор уведомления, orphan-позиция, рестарт между записью и подтверждением.

**Общее правило закрытия:** regression подтверждён, необходимые проверки качества пройдены, результаты записаны в журнал. Сетевые сценарии отмечаются отдельно от mocks. Планирование этих задач **не разрешает M8/live** и не меняет риск-политику.

При записи следует сохранить прежние свидетельства локальных тестов M3, но явно отметить, что они не подтверждают сквозную runtime-приёмку приоритетов и стриминга.

---

**Пользователь**

Я дал тебе доступ, можешь менять Backlog

---

**Kodik**

Разрешение на изменение `BACKLOG.md` получил. Но активный режим всё ещё **Ask** — он запрещает запись файлов, включая документацию.

Переключите сессию в **Code**. Тогда внесу подготовленные задачи с критериями приёмки и regression-тестами в `tasks/BACKLOG.md`, добавлю запись в `CHANGELOG_AI.md`. Продуктовый код менять не буду, задачи оставлю незавершёнными.

---

**Пользователь**

Внеси подготовленные задачи аудита в backlog и журнал, без изменения продуктового кода.

---

**Kodik**

Активный режим по-прежнему **Ask**: разрешение вы дали, но запрет записи файлов на уровне режима остаётся. Поэтому backlog и журнал пока не изменены.

Переключите режим сессии на **Code** через интерфейс Kodik. Задача уже определена: изменить только `tasks/BACKLOG.md` и `CHANGELOG_AI.md`, не трогать продуктовый код и оставить новые задачи с `[ ]`. Повторять формулировку запроса не нужно.

---

**Пользователь**

Режим Code включил, не дури

---

**Kodik**

Понимаю. Но в переданных мне инструкциях всё ещё указан **Ask** с запретом записи — переключение интерфейса до меня не дошло. Это ограничение режима, а не отсутствие вашего разрешения.

Попробуйте открыть новую сессию сразу в Code и передать ей этот аудит. Здесь файлы не изменены.