# 12_DEPLOY — сервер и запуск

## Требования

1 ядро / 1 ГБ RAM — минимум, 2 ядра / 2–4 ГБ — комфортно, 20 ГБ SSD, Ubuntu 22.04/24.04.
Главное — аптайм и стабильный канал, а не частота процессора: бот работает 24/7,
торгует 10:00–23:50 МСК, а падение с открытыми позициями стоит денег.
Сервер в России (пинг до API брокера, оплата локальными картами), дата-центр Мск/СПб.

## Шаги

```bash
ssh root@IP
apt update && apt upgrade -y
curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh
apt install docker-compose-plugin ufw -y

# код
git clone <repo> /opt/tbot     # или scp -r trading_bot/ root@IP:/opt/tbot

# каталоги данных (вне образа, чтобы жили между пересборками)
mkdir -p /opt/tbot/{data,logs,reports,secrets}

cd /opt/tbot
docker compose up -d --build
docker logs -f tbot
```

`docker-compose.yml`: `restart: unless-stopped`, тома data/logs/reports/secrets,
порт 8080 наружу только через reverse proxy.

## Доступ к дашборду

Порт 8080 напрямую в интернет не открывается. nginx как reverse proxy + basic-auth,
домен + Let's Encrypt:
```bash
apt install nginx certbot python3-certbot-nginx -y
# location / -> proxy_pass http://127.0.0.1:8080; proxy_http_version 1.1;
# + заголовки Upgrade для WebSocket
certbot --nginx -d bot.example.ru
```
Без домена — SSH-туннель: `ssh -L 8080:localhost:8080 root@IP`, открыть localhost:8080.

## Firewall
```bash
ufw allow 22/tcp && ufw allow 443/tcp && ufw enable
```
22 — лучше по ключу и с ограничением по IP.

## Проверка после запуска

- [ ] `docker ps` — статус `Up`, контейнер переживает `docker compose restart` и рестарт сервера.
- [ ] Режим **PAPER** на экране (первым делом проверить).
- [ ] `docker logs tbot` — без ошибок, миграции БД применены.
- [ ] БД на томе: после `docker compose down` + `up` история сделок сохранилась.
- [ ] VK-уведомление о старте пришло; `/status` отвечает; `/panic` сработал на тестовой бумаге.
- [ ] `ping api-invest.<brokersite>` < 50 мс.
- [ ] Бэкап БД отрабатывает вручную (`cp` тома).
- [ ] Второй экземпляр не стартует (lock-файл).

## Резервные копии
```bash
# /etc/cron.daily/tbot-backup
sqlite3 /opt/tbot/data/tbot.db ".backup /opt/tbot/backup/tbot-$(date +%F).db"
find /opt/tbot/backup -mtime +14 -delete
```
Файловая копия живой SQLite ненадёжна — использовать `.backup`.

## Обновление версии
```bash
cd /opt/tbot && git pull
docker compose build && docker compose up -d
# состояние восстановится из тома; проверить positions == позиции брокера в /status
```
Обновление не в торговую сессию (после 23:55 или до 09:50 МСК).

## Runbook аварий

| Симптом | Действие |
|---|---|
| Позиции в минусе, бот не реагирует | `/panic` из VK; затем `docker logs` |
| Контейнер в цикле рестартов | `docker logs`, откат образа, позиции проверить на бирже вручную |
| Дашборд не открывается, торги идут | торговля живёт без UI — чинить nginx/порт, паниковать рано |
| VK молчит | торговля продолжается; проверить VK-токен, лимиты, peer_id |
| Расхождение позиций с биржей | остановить (`/stop`), сверить вручную, пометить orphan, решить: закрыть или принять |
| Потеря сети на сервере | бот не ставит новых заявок; при восстановлении — авто-синхронизация; проверить стопы |
| Сомнение в корректности | `/stop` + закрыть позиции по рынку в приложении брокера. Человек важнее автоматики |

## Что НЕ делать

- Не запускать от root внутри контейнера (user в Dockerfile).
- Не оставлять 8080 без авторизации — это кнопка PANIC и доступ к счёту.
- Не класть токены в compose-файл и в git.
- Не обновлять образ посреди сессии «на живую».
- Не доверять «оно само перезапустится» без проверки: рестарт проверяется тестовым `kill`.
