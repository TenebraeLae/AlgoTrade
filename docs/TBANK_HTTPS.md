# Чтение документации T-Bank при недоверенном НУЦ

## Причина и область исправления

2026-10-17 прямое чтение `https://developer.tbank.ru/invest/intro/intro/limits`
через встроенный `web_fetch` завершилось `ERR_CERT_AUTHORITY_INVALID`, через
стандартный Python HTTPS — `CERTIFICATE_VERIFY_FAILED: self-signed certificate
in certificate chain`. После добавления проверенного официального корня только
в отдельный `SSLContext` та же страница вернула HTTP 200. Наблюдаемый издатель
сертификата сервера — `Russian Trusted Sub CA`, протокол — TLS 1.3.

Это безопасный способ чтения в Python, **не исправление хранилища встроенного
`web_fetch` или браузера**. У `web_fetch` нет параметра CA bundle. Если инструмент
возвращает прежнюю ошибку, используйте приведённый ниже способ; не повторяйте
его вызов в расчёте, что настройка Python изменила доверие другого процесса.
Системное хранилище Windows, настройки Kodik и окружение бота не меняются.

## Предусловия

- Запускать из корня репозитория в PowerShell, нужен Python 3.11+.
- Локальный публичный сертификат `.tls-verify/Russian_Trusted_Root_CA.pem`
  ранее получен с официального адреса
  `https://gu-st.ru/content/downloads/Russian_Trusted_Root_CA.cer`.
  Он не является секретом, но локальная копия может отсутствовать в другом checkout.
- SHA-256 **DER-сертификата**, а не текста PEM:
  `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`.
  Источник и прежняя проверка зафиксированы в `docs/08_TINVEST_API.md`, раздел TLS.
- При отсутствии файла, несовпадении отпечатка или ошибке новой цепочки —
  остановиться. Не извлекать доверенный корень из недоверенного ответа сервера.

## Проверенное чтение страницы

Команда ничего не записывает, не использует токены и не выполняет JavaScript.
Она сохраняет стандартные доверенные CA, добавляя проверенный корень только
для этого запроса. Цепочка, срок действия и имя сервера проверяются OpenSSL.

```powershell
@'
import hashlib
import ssl
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(".tls-verify/Russian_Trusted_Root_CA.pem")
der = ssl.PEM_cert_to_DER_cert(root.read_text(encoding="ascii"))
expected = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"
if hashlib.sha256(der).hexdigest() != expected:
    raise SystemExit("CA fingerprint mismatch: HTTPS request not sent")

context = ssl.create_default_context()
context.load_verify_locations(cafile=str(root))
if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
    raise SystemExit("TLS verification must remain enabled")

url = "https://developer.tbank.ru/invest/intro/intro/limits"
with urllib.request.urlopen(url, context=context, timeout=30) as response:
    print("HTTP", response.status, response.url)
    print(response.read().decode("utf-8"))
'@ | python -
```

Успех: HTTP 200, заголовок HTML `Лимиты | T-Bank Dev Portal`, таблица сервиса
котировок содержит 600 unary-запросов в минуту. На проверенной странице также
есть суммарная рекомендация 50 запросов/с по счетам и токенам и указание
1000 запросов/мин с IP. Это опубликованные значения, не измеренная персональная
квота и не гарантия учёта запросов чужих программ.

Запрещены `verify=False`, `CERT_NONE`, `--insecure`, глобальная подмена
SSL-контекста или импорт CA в системное хранилище ради обхода ошибки.
Для общесистемного доверия нужна отдельная согласованная задача с оценкой
области доверия; для чтения этой страницы оно не требуется.
