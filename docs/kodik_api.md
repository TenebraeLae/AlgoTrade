# Навык для документации API Т-Банка

_Экспортировано 17.10.2026 в 01:08:06 GMT+5 из Kodik (2.2.6)_

---

**Пользователь**

Создай навык, что при запросах к https://developer.tbank.ru/* используются знания из прикрепленного документа
Иначе нам постоянно будет выдавать Error fetching web content: Agent network error:

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\AppData\Local\Programs\Kodik\resources\app\out\vs\workbench\contrib\kodik\chat\common\configuration\kodik-configuration\SKILL.md

Path: c:\Users\TacereBellum\AppData\Local\Programs\Kodik\resources\app\out\vs\workbench\contrib\kodik\chat\common\configuration\kodik-configuration\SKILL.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: c:\Users\TacereBellum\.agents\skills\using-agent-skills\SKILL.md

Path: c:\Users\TacereBellum\.agents\skills\using-agent-skills\SKILL.md

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
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

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
Цель: c:\Users\TacereBellum\.kodik\plugins\superpowers\skills\writing-skills\SKILL.md

Path: c:\Users\TacereBellum\.kodik\plugins\superpowers\skills\writing-skills\SKILL.md

---

**Kodik**

[Использование инструмента: glob]
Статус: Успешно
Цель: .kodik/skills/**/SKILL.md

Glob_pattern: .kodik/skills/**/SKILL.md

---

**Kodik**

[Использование инструмента: shell]
Статус: Успешно
Цель: git status --short

---

**Kodik**

Создам навык для этого проекта: при обращениях к `https://developer.tbank.ru/*` он будет предписывать сначала читать `docs/TBANK_HTTPS.md` и использовать описанный Python HTTPS-путь, не повторяя заведомо неработающий `web_fetch`.

Текущий этап — M7; задача вспомогательная, торговый код не меняется. Критерий приёмки: корректный файл навыка, ссылка на исходный документ и сохранение проверки TLS. Обнаружение навыка самим Kodik проверю отдельно, если доступно.

---

**Kodik**

[Использование инструмента: create_file]
Статус: Успешно
Цель: .kodik/skills/reading-tbank-developer-docs/SKILL.md

Path: .kodik/skills/reading-tbank-developer-docs/SKILL.md

---

**Kodik**

[Использование инструмента: sub_agent]
Статус: Успешно
Цель: sub_agent

Name: Проверка навыка T-Bank
Agent: reviewer

[Чат субагента: Reviewer]
Цель: Независимо read-only проверь .kodik/skills/reading-tbank-developer-docs/SKILL.md против docs/TBANK_HTTPS.md и запроса пользователя: при https://developer.tbank.ru/* применять runbook, избегать повторного неработающего web_fetch, не отключать TLS. Проверь frontmatter и ссылку. Мысленно примени к 3 сценариям: нужный URL limits при наличии CA; CA отсутствует; web_fetch уже дал Agent network error. Верни конкретный порядок действий в каждом и замечания. Сеть не вызывай, файлы не изменяй. Не проверяй весь проек…
Модель: Auto
Провайдер: kodik
Инструменты: glob, read_file, read_lints, lsp, grep, send_message

---

**Kodik**

[Использование инструмента: shell]
Статус: Успешно
Цель: @'
from pathlib import Path
import re
p = Path('.kodik/skills/reading-tbank-developer-docs/SKILL.md')
s = p.read_text(encoding='utf-8')
front = s.split('---', 2)[1].strip().splitlines()
fields = dict(line.split(': ', 1) for line in front)
assert set(fields) == {'name', 'description'}
assert re.fullmatch(r'[a-z0-9-]+', fields['name'])
assert p.parent.name == fields['name']
assert fields['description'].startswith('Use when ')
assert len('\n'.join(front)) < 1024
assert all(': ' not in value and not value.startswith(('*', '&', '!', '[' , '{')) for value in fields.values())
assert Path('docs/TBANK_HTTPS.md').is_file()
assert 'https://developer.tbank.ru/*' in s
print('PASS: skill path, plain-scalar YAML frontmatter, trigger, local reference')
'@ | python -

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

[Использование инструмента: edit_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

[Использование инструмента: agent_status]
Статус: Успешно
Цель: agent_status

---

**Kodik**

Создан проектный навык: [SKILL.md](.kodik/skills/reading-tbank-developer-docs/SKILL.md:1).

Для `https://developer.tbank.ru/*` он предписывает:
- сначала читать `docs/TBANK_HTTPS.md`;
- использовать описанный Python HTTPS-путь вместо `web_fetch`;
- сохранять проверку сертификата и прекращать запрос при отсутствии CA или несовпадении отпечатка.

Формат навыка и ссылка на документ проверены. Независимая проверка ещё выполняется.

**Активация в Kodik пока не подтверждена:** проверьте Settings → Skills и включённое автообнаружение. Если навык не появился, перезагрузите окно IDE. Саму сетевую ошибку `web_fetch` навык не исправляет — он задаёт альтернативный путь чтения.

---

**Пользователь**

[reviewer: completed]
Независимо read-only проверь .kodik/skills/reading-tbank-developer-docs/SKILL.md против docs/TBANK_HTTPS.md и запроса пользователя: при https://developer.tbank.ru/* применять runbook, избегать повторного неработающего web_fetch, не отключать TLS. Проверь frontmatter и ссылку. Мысленно примени к 3 сценариям: нужный URL limits при наличии CA; CA отсутствует; web_fetch уже дал Agent network error. Верни конкретный порядок действий в каждом и замечания. Сеть не вызывай, файлы не изменяй. Не проверяй весь проект.
Current sub-agent capacity: 0/6 active; 6 slots available.

## Verdict

**SKILL.md is internally correct and consistent with `docs/TBANK_HTTPS.md`, the user request, and AGENTS.md.** One wording risk on the CA-failure path (see Spec axis, S1) is the only substantive remark. No scope creep, no fabricated claims.

Files inspected (read-only, no network, no edits): `.kodik/skills/reading-tbank-developer-docs/SKILL.md` (29 lines), `docs/TBANK_HTTPS.md` (74 lines), plus cross-references in `docs/08_TINVEST_API.md` and existence of `.tls-verify/Russian_Trusted_Root_CA.pem`.

## Axis 1 — Standards

**PASS** (with 2 minor notes).

- **Frontmatter (SKILL.md:1–4):** valid YAML, closed by `---` at line 4, exactly `name` + `description`. `name` = `reading-tbank-developer-docs` matches the directory name and the registered skill name. `description` (line 3) names the trigger domain (`https://developer.tbank.ru/*`), the activity (reading/citing T-Bank Invest docs) and the three error signals (`Agent network error`, `ERR_CERT_AUTHORITY_INVALID`, `CERTIFICATE_VERIFY_FAILED`). Trigger conditions are concrete, so the skill is discoverable.
- **Reference/link:** SKILL.md:8 points to `docs/TBANK_HTTPS.md` "от корня текущего репозитория". The target exists and is exactly that path. No other link or reference in SKILL.md — nothing dangling. `.tls-verify/Russian_Trusted_Root_CA.pem` (referenced at SKILL.md:14 and TBANK_HTTPS.md:21,46) also exists in this checkout.
- **Consistency with runbook:** SKILL.md:13 correctly says "выполняя … через доступный shell" rather than hardcoding a tool name; SKILL.md:23–26 reproduce the runbook's forbidden actions (`verify=False`, `CERT_NONE`, `--insecure`, global SSL-context replacement, Windows CA import) without contradicting TBANK_HTTPS.md:70–73.
- **AGENTS.md alignment:** SKILL.md:15 matches §3 ("не извлекать доверенный корень из недоверенного ответа"); SKILL.md:28 matches §4.7 (no tokens/keyring needed) and TBANK_HTTPS.md:33 ("не использует токены").
- **Fingerprint cross-check (passed):** the DER SHA-256 at TBANK_HTTPS.md:26 (`D2:6D:2D:…:31`) equals the Python `expected` string at TBANK_HTTPS.md:48 (`d26d2d02…cf31`) and equals the value recorded in `docs/08_TINVEST_API.md:123`. Three-way consistent; the hex-lowercase vs colon-uppercase difference is cosmetic and intentional (Python comparison needs lowercase hex).
- Note (minor, non-blocking): SKILL.md:2–3 omits `allowed-tools`/`license`, which is fine for the declared optional-fields style and matches other skills in `.kodik/skills`.
- Note (minor, non-blocking): SKILL.md:13 says "Для других доменов этот навык не применяется" — correct scoping; TBANK_HTTPS.md:12–15 makes the same point for `web_fetch`, so there is no drift.

## Axis 2 — Spec (user request: runbook on `developer.tbank.ru/*`, no repeated failing `web_fetch`, no TLS disable)

**PASS with one wording risk.**

- *Runbook on the domain:* SKILL.md:8 + 12 make the runbook mandatory before the first network call. Satisfied.
- *No repeated failing `web_fetch`:* SKILL.md:12 ("Не начинай с `web_fetch`…") and SKILL.md:23 ("Повторять `web_fetch` после настройки Python" → wrong; use a separate Python trust context). Satisfied, and the rationale (separate trust stores per process) matches TBANK_HTTPS.md:13–15.
- *No TLS disable:* SKILL.md:24 forbids the four known bypass forms. Satisfied; no "temporary workaround" escape hatch is offered.
- **S1 (wording risk, the only substantive remark) — SKILL.md:15:** "…либо TLS-проверка не прошла — останови этот путь и сообщи конкретный блокер. Не извлекай доверенный корень из недоверенной серверной цепочки и не скачивай замену без отдельной проверки официального источника." The negative clause `не скачивай замену без отдельной проверки` is a permissive conditional: it can be read as allowing a re-download of the root from the official source after "verification". Read together with step 4's `останови этот путь`, this is ambiguous. TBANK_HTTPS.md:28–29 is strictly stronger — "При отсутствии файла, несовпадении отпечатка или ошибке новой цепочки — **остановиться**." Impact: a reader could re-fetch a replacement root mid-task, or silently retry after a fingerprint mismatch, instead of reporting the blocker. Recommendation: reword SKILL.md:15 to the runbook's strict form, e.g. "…останови и сообщи блокер; файл CA не перезаписывай и не скачивай в рамках этого навыка — замена корня требует отдельной согласованной задачи." (Note: AGENTS.md §1 forbids me from editing, so this is a remark only, not a change.)
- No missing requirements: all three user constraints are covered; nothing in the request is unaddressed. No scope creep: SKILL.md stays read-only and explicitly declines to fix `web_fetch`/browser trust (SKILL.md:28, TBANK_HTTPS.md:12–16) and does not grant extra tool permissions.
- Historical vs fresh distinction (SKILL.md:17, 26) is honored: TBANK_HTTPS.md:64–68 labels its quota figures as published values, not a measured personal quota, matching AGENTS.md §3.

## Three scenarios — concrete order of actions

**Scenario A — target URL is the limits page, CA present.**
1. Read `docs/TBANK_HTTPS.md` first; do **not** call `web_fetch` (SKILL.md:12, TBANK_HTTPS.md:12–15).
2. From repo root, confirm `.tls-verify/Russian_Trusted_Root_CA.pem` exists (TBANK_HTTPS.md:21–24, 46).
3. Run the runbook PowerShell block (TBANK_HTTPS.md:37–62), substituting only `url` for the exact target (hostname must stay `developer.tbank.ru`; keep path/query) — SKILL.md:13. The script self-checks the DER SHA-256 (TBANK_HTTPS.md:48–50) and asserts `check_hostname`/`CERT_REQUIRED` (TBANK_HTTPS.md:54–55) before sending.
4. Verify `HTTP 200`, final URL, and the page heading/table content; do not treat a truncated read as success (SKILL.md:16). Cite the source URL and separate fresh content from the runbook's historical observations (SKILL.md:17).
5. If the script exits with `CA fingerprint mismatch` or `TLS verification must remain enabled`, treat it as the blocker path (Scenario B) — no request was sent.

**Scenario B — CA file absent (or fingerprint mismatch).**
1. Do not attempt `web_fetch` as a fallback; it has no CA-bundle parameter (TBANK_HTTPS.md:13–15).
2. Stop this path and report the concrete blocker: missing/mismatched `.tls-verify/Russian_Trusted_Root_CA.pem` (SKILL.md:15; TBANK_HTTPS.md:28–29).
3. Do **not** extract the trusted root from the server's untrusted chain (SKILL.md:15; TBANK_HTTPS.md:29) and do **not** import a CA into the Windows store or touch bot env (SKILL.md:25; TBANK_HTTPS.md:16, 71–73).
4. Do not present the runbook's historical quota numbers as the current answer; state that the page could not be read and, per SKILL.md:17, invent no SDK methods/fields/limits.
5. *Gap vs the user request:* the skill gives no local (offline) fallback, e.g. the official raw `limits.md` mirror that `docs/08_TINVEST_API.md:113–116` already used successfully. Because the user framed this as "when `https://developer.tbank.ru/*` apply the runbook", omitting the offline mirror is a defensible scoping choice, but it is worth noting as an explicit non-goal if the CA is missing.
6. Correction to my own first read: I initially treated the PKCS#7/JAR root as an alternative source; it is not, because the runbook pins the DER of the `.cer`, and `ssl.PEM_cert_to_DER_cert` expects a single PEM certificate. PKCS#7 would need separate extraction/verification — so it is not a supported fallback here.

**Scenario C — `web_fetch` already returned `Agent network error`.**
1. Do not retry `web_fetch`; a Python-side CA setup cannot change another process's trust store (SKILL.md:12, 23; TBANK_HTTPS.md:13–15).
2. Do not conclude the cause: `Agent network error` alone does not prove a TLS/CA problem (SKILL.md:12, last sentence) — the runbook's confirmed evidence is `ERR_CERT_AUTHORITY_INVALID` / `CERTIFICATE_VERIFY_FAILED` (TBANK_HTTPS.md:5–8), a different signature.
3. Proceed with the Python runbook path (Scenario A steps 1–4).
4. If the runbook path also fails, report both signals separately as the blocker; do not conflate them, and do not disable TLS to "get past" either (SKILL.md:24; TBANK_HTTPS.md:70–71).

## Summary

- Standards: **PASS** (valid frontmatter, correct skill name, existing reference, consistent with TBANK_HTTPS.md / AGENTS.md §3, §4.7; 2 minor non-blocking notes).
- Spec: **PASS** on all three user constraints; **one wording risk** at SKILL.md:15 (weak "не скачивай … без проверки" vs the runbook's strict stop) plus an optional note that no offline fallback is defined for the CA-missing case.
- No files changed, no network used, project not reviewed beyond the referenced documents.

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: .kodik/skills/reading-tbank-developer-docs/SKILL.md

Path: .kodik/skills/reading-tbank-developer-docs/SKILL.md

---

**Kodik**

[Использование инструмента: edit_file]
Статус: Успешно
Цель: .kodik/skills/reading-tbank-developer-docs/SKILL.md

Path: .kodik/skills/reading-tbank-developer-docs/SKILL.md

---

**Kodik**

[Использование инструмента: read_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

[Использование инструмента: edit_file]
Статус: Успешно
Цель: CHANGELOG_AI.md

Path: CHANGELOG_AI.md

---

**Kodik**

Независимая проверка завершена: навык соответствует документу и запросу.

Устранена одна двусмысленность: при отсутствии или неверном отпечатке CA навык теперь явно запрещает скачивать либо перезаписывать сертификат — нужно остановиться и сообщить причину.

Результат записан в `CHANGELOG_AI.md`. Активация в интерфейсе Kodik остаётся непроверенной.