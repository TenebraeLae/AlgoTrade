# 01_ARCHITECTURE — модули, слои, контракты

## Слои (строго вниз по зависимостям)

```
L5  main.py, dashboard.py, vk_bot.py        управление и представление
L4  executor.py, journal.py, optimizer.py   оркестрация, журнал и обучение
L3  strategy.py, risk_manager.py            решения
L2  portfolio.py, broker/{paper,live}.py    состояние и исполнение
L1  market_data.py, indicators.py, database.py, security.py  данные и учёт
L0  config.py, logger.py, utils.py          инфраструктура
```

Правило: модуль знает только о модулях ниже. `strategy.py` не знает о существовании
Tinkoff, VK, Docker, FastAPI. `indicators.py` не знает о наличии портфеля.
`risk_manager.py` не умеет отправлять заявки. Нарушение направления зависимости — этап не принят.

## Ключевые контракты (dataclass)

```python
class InstrumentInfo:   uid, ticker, name, lot_size, min_price_increment,
                        price, sector, tradable, currency
class Candle:           open, high, low, close, volume, ts_open (tz-aware UTC)
class Signal:           ticker, side, reason, indicators: dict, regime, strength, ts,
                        stop_price?: Decimal, take_price?: Decimal
class OrderRequest:     ticker, side, lots, order_type, limit_price, reason, ttl
class Position:         ticker, side, lots, avg_entry, stop, take, opened_at, journal_id
class Decision:         id, ts, ticker, regime, indicator_snapshot: dict, atr,
                        size_lots, filters_passed: list, filters_failed: list, result
class RiskVerdict:      allowed: bool, reason: str, max_lots: int, checks: list
```

У `Signal` уровни `stop_price`/`take_price` заданы для ranging (экстремум свечи и
середина BB); в trending они `None`, уровни строятся из ATR в риск-слое. Перед
расчётом лотов сырой ranging-стоп округляется по шагу инструмента от входа.

## Потоки данных

**Торговый цикл (каждая новая свеча 5m):**
```
market_data → indicators → классификатор режима → strategy → Decision/Signal
   → risk_manager.check(signal, portfolio, market) → RiskVerdict
   → journal.record(decision, risk_verdict) → decisions.id
   → executor → broker.paper|live → portfolio (trades.journal_id = decisions.id)
   → dashboard WS + VK
```

**Мониторинг позиций (2–3 секунды, из стрима):**
```
stream → portfolio → проверка стопов/тейков/таймаута 45 мин
   → executor.close → broker → portfolio → database → VK
```

**Утренний цикл:** авторизация → календарь → глобальный фильтр рынка → формирование пула
→ старт стрима.

**Вечерний цикл (23:30):** закрытие внутридневного; (23:55): отчёт дня + сравнение с IMOEX.

## Точки расширения

- `broker/base.py::Broker` — `place`, `cancel`, `positions`, `orders`. Реализации:
  `PaperBroker` (без gRPC), `LiveBroker` (только при `TRADING_MODE=live`, флаг проверяется
  в конструкторе, при live вне разрешённого окна конструктор бросает исключение).
- `strategy/base.py::Strategy` — `evaluate(candles, regime) -> Signal | None`.
  Текущая многофакторная — первая реализация.
- `notify/base.py::Notifier` — `send(text)`, `commands()`. Реализация VK, заглушка NullNotifier.

## Где что живёт

| Вопрос | Модуль | Запрет |
|---|---|---|
| Сколько лотов купить | risk_manager | strategy не считает лоты |
| Была ли сделка прибыльной | portfolio + database | executor не считает P&L |
| Можно ли торговать сейчас | utils (календарь/сессия) + status API | нигде не хардкодится время сессии |
| Изменились ли веса | optimizer + database | optimizer не вызывает broker напрямую |

## Порядок инициализации в main.py

1. logger → 2. config (с валидацией) → 3. security (расшифровать токен) →
4. database (миграции) → 5. gateway (пинг, счета) → 6. portfolio (восстановление состояния)
→ 7. market_data (лимитер, кэш) → 8. strategy+risk → 9. broker (по режиму)
→ 10. dashboard → 11. vk_bot → 12. торговый цикл.

Сбой на шагах 1–9 → выход с понятным сообщением. Сбой на 10–11 → **торговля продолжается**,
информирование деградирует (это отдельный тест).
