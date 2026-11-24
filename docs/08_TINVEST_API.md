# 08_TINVEST_API — работа с брокером

**Этот документ намеренно неполон в части имён.** Имена методов, полей protobuf, точных
лимитов и версий пакета нет в этом пакете, потому что они меняются, а выдуманные значения
стоят денег. Всё, что ниже помечено `VERIFY`, агент обязан сверить с актуальной
документацией Tinkoff Invest API и с SDK, установленным в окружении, до написания кода.
Подтверждённое — внести сюда через PR и записать в CHANGELOG.

## Обязательный протокол

1. Все вызовы брокера — только через `api_client.InvestGateway`. Ни один другой модуль
   не импортирует SDK.
2. Не подтверждён метод → `raise NotImplementedError("TODO(VERIFY): ...")`.
3. `tests/test_sdk_symbols_exist.py` проверяет наличие каждого используемого символа
   в установленном SDK. Он **должен падать**, пока имена не подтверждены.
4. Минимум 10 позиций `TODO(VERIFY)` на выходе M2 — признак честной работы.

## Список VERIFY (заполнить агенту)

| № | Что проверить | Где | Статус |
|---|---|---|---|
| 1 | точное имя PyPI-пакета и версия | requirements.txt | **CONFIRMED** — PyPI не отдаёт пакет (404), ставится из GitHub: `pip install "tinkoff-investments @ git+https://github.com/RussianInvestments/invest-python.git@0.2.0-beta117"`. Зафиксировано в requirements.txt, проверено живым импортом. |
| 2 | способ авторизации по токену, интерактив vs production хост | api_client | **CONFIRMED для sandbox** — `Client(token, target=INVEST_GRPC_API)`, таргеты `invest-public-api.tinkoff.ru`/`sandbox-...` подтверждены SDK; 2026-10-14 запросы со скрыто введённым sandbox-токеном прошли для чтения и создания виртуального счёта. Ранее другой токен получил `UNAUTHENTICATED`, `40003` (см. документацию [ошибок токена](https://developer.tbank.ru/invest/intro/intro/token)); секреты не записывались. TLS отдельно проверен с официальным корневым сертификатом (см. ниже). |
| 3 | метод списка счётов и поля счёта (тип, статус, валюта) | accounts | **CONFIRMED** — `client.users.get_accounts()`; SDK `Account` = `id, type, name, status, opened_date, closed_date, access_level` (валюты нет). Для списка виртуальных счетов адаптер использует `SandboxClient.users.get_accounts()`; символы и поля ответа сверены с установленным SDK и проверены без сети. Исторически `Client.users.get_accounts()` до создания sandbox-счёта вернул пустой список. |
| 4 | метод списка инструментов и поле статуса торговли (документ ошибочно называл его `real_trading_status`) | instruments | **CONFIRMED, с поправкой** — `client.instruments.shares(...)`; реальное имя поля — `Share.trading_status` (не `real_trading_status`), тип `SecurityTradingStatus`. Там же есть `lot` (не `lot_size`), `min_price_increment`, `short_enabled_flag`, `figi`/`uid`, `currency`, `sector`. |
| 5 | метод/сервис торговых статусов и все варианты enum | utils | **CONFIRMED** — `client.market_data.get_trading_status(instrument_id=...)` -> `GetTradingStatusResponse.trading_status`; enum `SecurityTradingStatus`, нужное значение — `SECURITY_TRADING_STATUS_NORMAL_TRADING` (17 значений всего, см. tests/test_sdk_symbols_exist.py). Живой read-only запрос для SBER (`BBG004730N88`) вернул `SECURITY_TRADING_STATUS_NORMAL_TRADING`. |
| 6 | метод свечей: интервал, максимум свечей за запрос, пагинация, ограничения истории | market_data | частично CONFIRMED — `client.market_data.get_candles(...)`, `Services.get_all_candles(...)` и `utils.MAX_INTERVALS` подтверждены локально в SDK beta117: страницы 5m/15m ≤1 день, 1h ≤1 неделя. SDK helper скрывает несколько RPC без pacing; gateway разбивает историю сам и ограничивает каждый RPC, не только вызов helper. `HistoricCandle` = `open, high, low, close, volume, time, is_complete, candle_source` (`Quotation`: `units`+`nano`); [документация T-Invest](https://tinkoff.github.io/investAPI/marketdata/#historiccandle) подтверждает `volume` **в лотах**. Живой вызов 5m, окно 1 день, `limit=10` ранее вернул 10 свечей. Точная максимальная глубина и семантика включения границ сетевого API ещё требуют проверки; пересечение страниц и дедупликация не должны терять пограничные бары. |
| 7 | сервис рыночных данных: стакан, текущие цены, доступность объёма | market_data | частично CONFIRMED — локально проверены `MarketDataService.get_order_book(instrument_id=, depth=)` и `GetOrderBookResponse.bids/asks/orderbook_ts`, `Order.price/quantity`; [официальная документация](https://tinkoff.github.io/investAPI/marketdata/#getorderbookresponse) подтверждает стороны стакана и `quantity` в лотах. `InvestGateway.get_order_book_quote()` выбирает лучшую цену без предположения о порядке уровней и отвергает пустой/пересечённый стакан. Подлинная свежесть и доступность данных при живом вызове не проверялись. |
| 8 | стрим котировок: метод подписки, лимит подписок на соединение, реконнект-семантика | stream | частично CONFIRMED — `client.create_market_data_stream().candles.subscribe([CandleInstrument(...)])`, итерация по `MarketDataResponse.candle` (без `is_complete`, в отличие от `HistoricCandle`), `SubscriptionInterval` (14 значений). Семантика реконнекта при реальном обрыве связи не проверялась (SDK саво не реконнектится — `market_data.StreamReconnector` реализует это на уровне бота, без встроенной повторной подписки в SDK). |
| 9 | выставление заявки: типы (limit/market), поля цены/объёма, параметр «tolко пополнение» | live_broker | VERIFY — не исследовано, не реализуется до M8. |
| 10 | отмена заявки, список активных заявок, частичное исполнение в статусе | live_broker | VERIFY |
| 11 | получение позиций/портфеля: поля, доступность P&L от брокера, расхождение с локальным | portfolio | VERIFY |
| 12 | маржинальные показатели и достаточность средств перед сделкой | risk | VERIFY |
| 13 | квоты запросов и поведение при превышении | limiter | частично CONFIRMED — [официальный исходник limits.md](https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/limits.md) указывает 600 unary/мин для сервиса котировок, суммарно по методам и счетам пользователя; старая страница tinkoff.github.io с 300 gRPC/150 REST не является актуальным источником. Оператор 2026-10-16 воспроизвёл `GetCandles RESOURCE_EXHAUSTED`. В SDK beta117 `RequestError.code` — атрибут `grpc.StatusCode`, `.metadata.ratelimit_reset` — необязательное число секунд до сброса, не Unix epoch (подтверждено parser/retry manager и [официальным grpc.md](https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/grpc.md)). `RESOURCE_EXHAUSTED` включает несколько причин в errors.md, поэтому ограниченный retry по статусу — клиентская read-only политика, а не доказательство конкретной минутной квоты. Реальные квоты данного токена/тарифа и влияние других процессов ещё VERIFY; клиентская консервативная политика не выдаётся за лимит брокера. |
| 14 | доступность short (продажа без покрытия) по счёту и тарифу | strategy | частично CONFIRMED — у `Share` есть поле `short_enabled_flag` (bool); поведение на конкретном счёте/тарифе не проверялось. |
| 15 | автоматический поиск настоящего IMOEX и доступность его свечей | benchmark | **CONFIRMED локально, сеть VERIFY** — в установленном beta117 `InstrumentsService.find_instrument(query=, instrument_kind=)` и `InstrumentType.INSTRUMENT_TYPE_INDEX`; `InstrumentShort` имеет `ticker/uid/figi/instrument_kind/instrument_type` (без exchange/currency). Принимать точный IMOEX типа INDEX, не IMOEX2/ETF; неоднозначность — отказ. UID/FIGI не захардкожены. Наличие настоящего IMOEX в каталоге и доступность свежих закрытых свечей в sandbox требуют отдельного сетевого подтверждения. |
| 16 | календарь торговых дней/праздников через API или только внешний источник | utils | частично CONFIRMED — есть `instruments.trading_schedules` (виден в dir()), календарь из API не читался. Статический календарь 2026 подтверждён официальными MOEX n94172/n96571, см. `data_seed/holidays_ru.md`; специальные сессии запрещены политикой, не названы закрытиями биржи. Обязательный `get_trading_status` (п.5) не заменяется разрешением календаря. Сетевой runtime остаётся VERIFY. |
| 17 | комиссия: берётся из API или считается по тарифу вручную | fee | VERIFY |
| 18 | корпоративные события (сплиты, дивиденды) доступны ли в API | events | частично CONFIRMED — есть `instruments.get_dividends`, `instruments.get_bond_events` (видны в dir()), сплиты отдельно не найдены/не искались. |
| 19 | создание и список виртуальных счетов песочницы | api_client | **CONFIRMED локально для нового списка; прежний вызов проверен живьём** — установленный SDK помечает `SandboxService.get_sandbox_accounts()` deprecated с указанием `SandboxClient.users.get_accounts()`. `SandboxClient` импортируется из `tinkoff.invest.sandbox.client`, принудительно задаёт sandbox target; `UsersService.get_accounts()` возвращает `GetAccountsResponse.accounts` с `Account.id/type/status`. Адаптер использует новый путь для списка и прежний `client.sandbox.open_sandbox_account(name='') -> OpenSandboxAccountResponse(account_id)` только для явного открытия счёта. Исторически через deprecated-вызов до открытия список был пуст, после — один `ACCOUNT_STATUS_OPEN`; **новый путь с токеном через сеть не проверялся**. Обе операции отказывают до создания клиента, если target не равен `sandbox_target()`. |

## Автоматический каталог и поиск IMOEX (решение оператора 2026-10-16)

- `instruments.shares()` — источник кандидатов. Поля `Share.real_exchange`,
  `currency`, `otc_flag`, `api_trade_available_flag`, `for_qual_investor_flag`,
  `buy_available_flag`, `sell_available_flag`, `share_type`, а также метаданные
  лота/кванта/сектора подтверждены чтением установленного SDK beta117.
  MOEX проверяется по `RealExchange.REAL_EXCHANGE_MOEX`, а не только по валюте.
  Неоднозначный ticker→идентификатор не разрешает выбрать первую попавшуюся бумагу.
- Поиск IMOEX: `find_instrument(query='IMOEX', instrument_kind=InstrumentType.INSTRUMENT_TYPE_INDEX)`;
  не требовать `api_trade_available_flag=True` для неторгового индекса.
  Возвращённый `InstrumentShort` нельзя трактовать как полную `Share`.
- У `indicatives()` в beta117 обнаружена несогласованность исходников сериализатора
  (wrapper создаёт `InstrumentsRequest`, stub объявляет `IndicativesRequest`).
  Наличие метода само по себе не подтверждает корректный runtime; bootstrap
  использует подтверждённый поиск, а не предполагает работоспособность indicatives.
- IMOEX2 не является индексом полной доходности и не заменяет IMOEX. По
  [сообщению MOEX n103622](https://www.moex.com/n103622) с 26.09.2026 IMOEX также
  рассчитывается в дополнительные сессии; различать инструменты по времени баров
  вместо точной идентичности нельзя.
- Instruments/MarketData представлены в sandbox ([сравнение контуров](https://tinkoff.github.io/investAPI/url_difference/));
  `get_candles(instrument_id=uid, ...)` не требует создания sandbox-счёта.
  Доступность именно свежих закрытых баров IMOEX всё ещё требует сетевого прогона.
- Для будущего live каталог/пул/поиск индекса также автоматические; это не
  разрешение включать live, создавать заявки или ослаблять риск.

## Ограничения, важные для архитектуры

- **Квант цены.** Любая цена в заявке кратна `min_price_increment`. Некратная цена →
  отклонение заявки брокером. Проверка локально до отправки, тест на пограничном значении.
- **Лотность.** Объём только целыми лотами. Дробные лоты — только если подтверждено
  для конкретного инструмента; по умолчанию считать, что нельзя.
- **Время.** API отдаёт UTC. Перевод в МСК только на границе отображения.
- **Частичное исполнение** — норма, не ошибка. Отмена остатка через 60 секунд.
- **Стрим не заменяет опрос.** После реконнекта обязательна сверка позиций и заявок
  с биржи: события, пропущенные во время обрыва, не воспроизводятся.
- **Квоты/RESOURCE_EXHAUSTED и транспортный UNAVAILABLE** обрабатываются в gateway
  на каждом фактическом GetCandles RPC, включая страницы и повторы. Решение
  оператора 2026-10-17 (+05) заменяет общие 4/120 budgets: quota допускает максимум
  61 попытку всего, задержка `max(1 секунда, server ratelimit_reset)`; при отсутствии
  корректного reset — 1 секунда. Длинный конечный reset ожидается с возможностью
  отмены, а не отклоняется только из-за прежних 120 секунд. Cooldown общий для
  ожидающих MarketData-вызовов; уже начатые RPC могут завершиться.
  `UNAVAILABLE` сохраняет отдельную ограниченную политику 4 попытки/5 секунд/
  120 секунд elapsed, игнорирует quota reset metadata и при исчерпании даёт
  `CandleRequestError`, а не quota-ошибку. Авторизационные и прочие статусы не
  повторяются. `UNAVAILABLE` не доказывает причину транспорта; TLS не отключается.
  Ограниченные повторы возвращаются к той же странице; успешные страницы не
  скачиваются заново. Общий отказ квоты/транспорта/авторизации не равен отсутствию
  истории по тикеру: bootstrap останавливается до применения пула, а не исключает
  кандидата из сравнительной выборки. Пустая/неполная история остаётся отдельным
  случаем; по прямому указанию оператора она перечитывается в новом 15-минутном слоте.
- **Staged pool refresh:** локальный `iter_candle_pages` возвращает управление
  между SDK-страницами существующего GetCandles; это не новый SDK-метод и не
  отмена pacing/retry. Частичные страницы предназначены только для staging:
  до полного успешного сбора и durable apply они не разрешают новый пул.
  `discover_paper_shares(pin=False)` читает каталог без перезаписи закреплённых
  метаданных текущего runtime; feed не должен искать identity открытой позиции
  по изменившемуся тикеру. Обычный `get_candles_history` сохраняет контракт
  «полная история или исключение», без success-shaped partial fallback.
- **Отмена оператором** (`OperationCancelled`) не является `GatewayError`: она
  проходит через каталог/кэш/страницы/статус/стакан без source-refusal/исключения
  кандидата. Shared `CandleRequestTiming.should_stop` проверяется до/после SDK
  действий; `sleep` main получает пробуждаемый stop-event. Нативная отмена уже
  выполняющегося синхронного RPC не предполагается — он может завершиться,
  но следующая страница/запрос не запускается. Во время paper-транзакции нет
  проверок, способных разорвать claim/fill/complete/ack.
- Собственные интервалы/попытки/бюджет ожидания — локальная клиентская политика.
  Решение оператора 2026-10-17 (+05): один общий thread-safe лимитер для unary
  MarketData gateway (свечи/статусы/стаканы, включая retry), не более 600 стартов за
  скользящие 60 секунд; bounded workers перекрывают ожидание ответов без lock на RPC.
  Это не контроль чужих процессов/токенов и не проверка динамического тарифа.
  Официальный исходник [limits.md](https://raw.githubusercontent.com/RussianInvestments/investAPI/main/src/docs/limits.md)
  повторно прочитан 2026-10-17: 600 unary/мин именно суммарно для MarketData,
  рекомендация ≤50/с по всем счетам/токенам и IP-ограничение 1000/мин. Прямой HTTPS
  fetch страницы портала завершился CERT_AUTHORITY_INVALID; TLS не отключался.
  Квоты ордеров/стримов не изменяются и не подтверждаются этой задачей. Большой
  конечный server reset нельзя обрезать к раннему повтору; ожидание прерывается отменой.

## TLS для sandbox в этом окружении

- [T-Invest: ошибки с сертификатом](https://developer.tbank.ru/invest/intro/intro/cases) указывают на сертификаты НУЦ Минцифры РФ. [Госуслуги](https://www.gosuslugi.ru/crt) публикуют [Russian Trusted Root CA](https://gu-st.ru/content/downloads/Russian_Trusted_Root_CA.cer) и выпускающие сертификаты. Не скачивать корень из недоверенной цепочки самого API.
- 2026-10-14 у `sandbox-invest-public-api.tinkoff.ru` наблюдалась цепочка с издателем `Russian Trusted Sub CA`. `openssl s_client -servername sandbox-invest-public-api.tinkoff.ru -connect sandbox-invest-public-api.tinkoff.ru:443 -CAfile <официальный PEM> -verify_return_error -brief` вернул `Verification: OK`; отдельный `grpc.secure_channel` с `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=<путь к PEM>` перешёл в состояние ready. SHA-256 отпечаток официального корня: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`. Оба теста проводились **без токена** и не подтверждают авторизацию.
- Для Python gRPC задавать путь к проверенному PEM **только в окружении процесса** до импорта/создания gRPC-канала; не выключать проверку TLS, не менять глобальное хранилище доверия. Если цепочка/отпечаток не соответствует официальному источнику — остановиться и расследовать.
- Первый авторизованный `GetAccounts` вернул `UNAUTHENTICATED` / `40003`; после скрытого ввода нового токена sandbox успешно ответил на read-only `GetAccounts` (0 счетов), статус SBER и исторические свечи. Токены вводились только через скрытый prompt и не сохранялись. Токен, опубликованный в чате, следует отозвать/заменить.

## Тесты

- мок-реализация `InvestGateway` для всех бизнес-тестов (сеть не используется);
- тест кванта: заявка с некратной ценой не уходит;
- тест лимитера: при 80% бюджета несрочные запросы откладываются;
- тест реконнекта: после имитации обрыва позиции синхронизируются, дублей нет;
- `test_sdk_symbols_exist.py` — красный до подтверждения имён.

## Что запрещено

- Хардкодить URL, версии, «обычно у брокеров так».
- Делать предположения о полении/семантике полей без проверки.
- Игнорировать ошибки транспорта, полагая, что ретраи «сами разберутся».
- Читать P&L из ответа брокера как единственный источник — локальный учёт первичен,
  расхождение = событие `IntegrityError`.
