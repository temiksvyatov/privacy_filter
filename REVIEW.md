# Аудит проекта `privacy_filter`

Дата: 2026-04-28
Объём: ~1800 строк (Python + vanilla JS/CSS/HTML), 7 коммитов от инициала до текущего состояния.

## TL;DR

Проект — компактная, грамотно скроенная обёртка вокруг `openai/privacy-filter` (CLI + FastAPI + SPA + Docker). Архитектура чистая, разделение слоёв правильное, тестов на критическую логику достаточно. Главные слабые места: **отсутствие нормализации регистра PII и нормализации сущностей**, **гонка инициализации модели в lifespan**, **несколько багов в RU regex** (overlap-resolution и URL-правило), **CORS и upload-лимит расходятся между API и UI**, **нет structured logging, метрик и rate-limit**, **CPU-инференс блокирует event loop** (FastAPI endpoints — `def`, не `async def` + offload). Ниже — по разделам.

---

## 1. Архитектура

### 1.1 Что хорошо

- Чёткое разделение: `filter.py` (модель) ↔ `ru_postpass.py` (детерминированный слой) ↔ `service.py` (HTTP) ↔ `cli.py` (терминал) ↔ `web/` (UI). Низкая связность, замена слоёв тривиальна.
- `Span` — `@dataclass(frozen=True)`. Иммутабелен, hashable, безопасно кэшировать.
- `get_filter()` через `lru_cache(maxsize=2)` — единый процессно-локальный синглтон, поддерживает «горячую» смену девайса.
- Лayered redaction precedence (`mask_char` → `placeholder` → entity tag) — задокументирован, тесты покрывают все три пути.
- `_RedactOnly` в CLI — чистый трюк: позволяет регекс-only режим без загрузки 2.8 GB весов.
- Docker: multi-profile (cpu/cli/gpu), HF cache в named volume, `start_period: 120s` в healthcheck — учли холодный старт модели.
- `--proxy-headers --forwarded-allow-ips "*"` для работы за Caddy — корректно.

### 1.2 Недочёты

**A1. Кэш в `service.py` — глобальный `OrderedDict`, не потокобезопасен.**
`uvicorn` по умолчанию однопоточный, но при `--workers N>1` каждый воркер получит свой кэш (не критично), а при `--threads`/threadpool sync-эндпоинты могут одновременно читать/писать `_detect_cache`. `popitem(last=False)` + `move_to_end` без `Lock` → потенциальный `RuntimeError: dictionary changed size during iteration` под нагрузкой.

**A2. Sync-эндпоинты блокируют event loop.**
`/detect` и `/redact` объявлены как `def` (не `async def`) → FastAPI переносит их в threadpool (default 40 потоков). Под параллельной нагрузкой инференс на CPU будет мешать сам себе (GIL отпускается torch'ом частично). Лучше явный `run_in_executor` с ограниченным семафором (`asyncio.Semaphore(N)` где N = число физических ядер для CPU, 1-2 для одной GPU).

**A3. Lifespan: prewarm без обработки ошибок.**
```155:158:pf_tester/service.py
async def _lifespan(app: FastAPI):
    get_filter(MODEL_NAME, DEVICE)
    yield
```
Если модель не скачивается (нет интернета, протух токен HF) — приложение упадёт на старте без понятного лога. Добавить `try/except` + structured warning + `liveness=ok / readiness=down` разделение.

**A4. `_pf()` ловит `Exception` в боевом пути.**
```151:155:pf_tester/service.py
def _pf() -> PrivacyFilter:
    try:
        return get_filter(MODEL_NAME, DEVICE)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}") from exc
```
После `lifespan` модель уже загружена и `lru_cache` вернёт её мгновенно. `try/except` тут — мёртвый код в норме и опасный в патологии (отдаст полное сообщение исключения наружу — info disclosure).

**A5. CORS allow-list расходится с upload-валидацией.**
В `service.py` `MAX_UPLOAD_BYTES` = 5 MB, в `web/app.js` хардкод `5 * 1024 * 1024`. При смене env-переменной UI продолжит врать пользователю. Отдавать лимит через `/health` и читать в JS.

**A6. Нет single source of truth для taxonomy.**
Список 8 категорий (`account_number`, `private_*`, `secret`) дублируется в README, ru_postpass, web (через `data-entity`), но нигде не вынесен в Enum/Literal. Добавление 9-й категории = правка 4 мест.

**A7. `pf_tester/__init__.py` не экспортирует `ru_postpass` и `get_filter`.**
Пользователь как библиотеки вынужден писать `from pf_tester.filter import get_filter`. Минор, но фасад напрашивается.

**A8. `service.py` смешивает HTTP-слой и cache-логику.**
`_cache_key`, `_detect_cached` живут прямо в модуле сервиса. Вынести в `pf_tester/cache.py` (LRU + thread-lock + метрики hit/miss) — упростит замену на Redis позже.

---

## 2. Производительность

### 2.1 Что хорошо

- `torch.inference_mode()` + `model.eval()` — корректное снятие autograd оверхеда.
- `detect_batch` с `pipeline(texts, batch_size=N)` — реальный батч-инференс, не цикл.
- `PF_NUM_THREADS` → `torch.set_num_threads(N)` — управление CPU-тредами есть.
- LRU-кэш по hash(text+min_score+ru_postpass) — с большим recurrent traffic дает мгновенный ответ.
- `bench.py` считает p50/p90/p99 + chars/s — годный инструмент для регрессий.

### 2.2 Недочёты

**P1. Хеш-ключ кэша = sha256 от полного текста.**
Для 128k-токенового документа это лишняя работа на каждый запрос. Дешевле `hashlib.blake2b(digest_size=16)` — в 2–3× быстрее sha256, коллизий за всю жизнь сервиса не наберётся.

**P2. `json.dumps` для cache key — оверкилл.**
Простая конкатенация `f"{text}|{round(min_score,4)}|{int(ru_postpass)}"` дешевле и достаточна (хешируем дальше).

**P3. Кэш не учитывает идентичные тексты разной нормализации.**
`"  text  "` и `"text"` дадут разные ключи. Для UI-сценария (paste) часто будет промах. Опциональный `text.strip()` перед хешированием — спорно (изменит индексы спанов), но для cache lookup можно пробовать обе версии.

**P4. `set_num_threads` вызывается каждый раз при `__init__`.**
`PrivacyFilter()` создаётся в lifespan один раз — ок. Но `lru_cache(maxsize=2)` в `get_filter` означает, что переключение device создаст второй инстанс и снова прибьёт глобальный thread count. Должно быть idempotent + warning при разных значениях.

**P5. Нет early-exit на пустой/слишком короткий текст в сервисе.**
`detect("")` сейчас вернёт `[]`, но кэш всё равно создаст запись. И сами пустые/очень короткие запросы спокойно проходят дальше — разумно ввести нижний порог (например, `len(text) >= 1` явно, плюс защита от 100k запросов с одинаковым `"a"`).

**P6. Нет ограничения по длине текста в `/detect`/`/redact` (JSON путь).**
`/redact/file` ограничен 5 MB, JSON — нет. Передать 50 MB JSON-строкой можно. Добавить проверку `len(text.encode())` или `Field(max_length=…)` в pydantic.

**P7. RU postpass выполняется на каждом тексте линейно по `_RULES`.**
12 паттернов × `re.finditer` × длина текста. Для 128k символа это десятки мс на CPU. Можно компилировать union-regex для категорий, где порядок не критичен (e.g. все "secret"-паттерны в одном `|`-чейне).

**P8. Bench не делает `torch.cuda.synchronize()` для GPU.**
`time.perf_counter()` на CUDA вернёт время до конца kernel-launch, а не до завершения. Под `--device cuda` цифры будут оптимистичнее реальности.

**P9. Нет prometheus/OTel метрик.**
`/health` отдаёт размер кэша, но не hit-rate, latency-percentile, queue depth. Для on-prem прода это must-have.

**P10. UI — синхронный `fetch` без AbortController.**
Если пользователь жмёт `Redact` дважды подряд — оба запроса полетят. Нужен AbortController для отмены предыдущего.

---

## 3. Функциональность

### 3.1 Что хорошо

- Покрытие категорий и edge cases (паспорт, СНИЛС, ИНН 10/12, ОГРН 13/15, IBAN, кредитки, RU-телефоны с разделителями) — реально продумано.
- `mask_char` сохраняет длину (важно для логов с фиксированными колонками) — фича редкая в подобных тулзах.
- UI: drag&drop, light/dark, copy/download, samples из API — для on-prem-демки этого достаточно.
- Suite `--suite` + `/samples` endpoint = единый источник данных для CLI/UI/тестов.

### 3.2 Баги и недоработки

**F1. Bug: overlap-резолвер в `ru_postpass` не транзитивен.**
```139:163:pf_tester/ru_postpass.py
for entity, rx in _RULES:
    for m in rx.finditer(text):
        ...
        if any(_spans_overlap(s, start, end) for s in existing):
            continue
        if any(_spans_overlap(s, start, end) for s in additions):
            continue
        additions.append(...)
```
Внутри одного правила `re.finditer` уже даёт неперекрывающиеся матчи. Но между правилами — порядок в `_RULES` важен, а текущий порядок создаёт тонкие баги:
- IBAN правило `[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}` идёт первым → перехватывает у credit card, ок.
- НО: OGRN(15) идёт **раньше** INN(12), потому что `_RULES` в правильном порядке. Проверьте — комментарий говорит "OGRN before INN", но фактически OGRN-правило `\d{15}|\d{13}` сработает только если 15-значное число не задето IBAN/CC ранее. Цифровое поле `4111 1111 1111 1111` (16 цифр без пробелов) не поймается ни OGRN, ни INN — попадёт в credit card. Хорошо. Но: 13-значный массив цифр посреди текста (например, штрих-код) будет помечен как OGRN. False positive весьма реалистичен.
- Регулярка для INN `\d{12}|\d{10}` — **нет проверки контрольной суммы**. Любые 10/12 цифр подряд = "account_number". Очень шумно. Минимум — добавить контекстный якорь (`ИНН`, `INN`, `СНИЛС`, `паспорт` etc. в лукэхеде/перед матчем) для строгого режима.

**F2. Bug: URL-regex поедает trailing punctuation.**
```83:94:pf_tester/ru_postpass.py
r"(?:https?://|www\.)[^\s<>\"',;]+?(?=[\s<>\"',;]|[.!?](?:\s|$)|$)|"
r"[A-Za-zА-Яа-я0-9\-]+\.(?:рф|москва|com|ru|net|org|io|dev|info|biz)"
r"(?:/[^\s<>\"',;]*?(?=[.!?](?:\s|$)|[\s<>\"',;]|$))?",
```
- `mail.ru` после email уже может быть схвачено email-правилом — overlap проверка спасёт, но тест `test_postpass_returns_sorted_spans` использует именно `mail@example.ru` — `example.ru` не зацепится потому что email-правило сработало раньше (в `_RULES` оно действительно перед URL). Хрупко: достаточно поменять порядок и тест упадёт.
- TLD-список захардкожен. `.app`, `.dev`, `.ai`, `.xyz`, `.tech` отсутствуют. `.рус`, `.онлайн` тоже.
- Bare host без TLD из списка не ловится: `intranet.local` пройдёт мимо.

**F3. Bug: `mask_char` валидируется в трёх местах, по-разному.**
- pydantic `Field(min_length=1, max_length=1)` в `RedactRequest`.
- Ручная проверка `if mask_char is not None and len(mask_char) != 1` в `/redact/file`.
- Внутри `redact()` — `if len(mask_char) != 1: raise ValueError`.
Три источника правды → разные коды ошибок (422 / 400 / 500). Унифицировать через pydantic-валидатор + перехват `ValueError` → 422.

**F4. Bug: `pipeline.aggregation_strategy="simple"` обрезает токены.**
HF token-classification с `simple` agg может вернуть `word` с ведущим пробелом или sub-token-маркерами в зависимости от токенизатора. UI и tests предполагают, что `text` в Span — точная подстрока input'а, но это не гарантировано (особенно для BPE/WordPiece). Лучше использовать `text[start:end]` напрямую вместо `item["word"]`. Исправление однострочное:
```python
spans.append(Span(entity=entity, text=text[item["start"]:item["end"]], ...))
```

**F5. Bug: `_run_one` в CLI не пробрасывает `min_score` корректно при `--no-model`.**
```106:110:pf_tester/cli.py
def _detect(pf, text, args):
    spans = [] if pf is None else pf.detect(text, min_score=args.min_score)
    if args.ru_postpass or pf is None:
        spans = ru_postpass_apply(text, spans)
    return spans
```
`ru_postpass_apply` назначает спанам `score=0.95` (`_REGEX_SCORE`). Если `args.min_score > 0.95` — все regex-спаны будут отброшены… но они НЕ отбрасываются, потому что фильтрация по `min_score` происходит только в `pf.detect()`. Regex spans проходят фильтр. Несимметрия. Либо документировать, либо применить `min_score` после postpass.

**F6. Bug: telephone regex может матчиться внутри INN.**
Хотя комментарий гласит "anchored by left word-boundary plus a mandatory + or whitespace so we don't grab the first 11 digits of a 12-digit INN" — фактически паттерн начинается с `(?:(?<=^)|(?<=[\s,;:()\[\]<>\"']))(?:\+7|8|7)`. Префикс `7` без `+` сработает на `7700123456789` (если перед `7` стоит whitespace). Тест `test_postpass_catches_passport_snils_inn` именно про это — там `ИНН 770123456789`, где перед `7` пробел. Сейчас тест проходит потому, что INN-правило `\d{12}` ловит всю строку и phone-правило получает overlap → пропускает. Но если убрать INN — phone схватит первые 11 цифр. Хрупко.

**F7. UI: `<span class="pii"::after content: attr(data-entity)>` показывает entity в superscript всегда.**
Для длинных текстов с десятками PII — это визуальный шум. Стоит сделать опцией (toggle).

**F8. UI: drop-zone не валидирует MIME.**
`accept=".txt,.md,..."` стоит на input file, но drag&drop принимает что угодно (даже бинари). `readAsText` потом покажет мусор.

**F9. UI: `loadSamples()` молча падает.**
`catch { /* ignore */ }` — пользователь не понимает, почему dropdown пустой. Логировать в console + сообщение в status.

**F10. Тестов мало для качества фильтра.**
`tests/` — smoke + redaction logic + regex. Нет:
- интеграционного теста на полный pipeline (модель + postpass) хотя бы на 1-2 синтетических кейсах с замоканной моделью.
- негативных тестов (fp на бенигн-текстах: артикулы товаров, ID транзакций).
- бенч-теста на «не медленнее X» для регрессии.

**F11. CLI: `--suite` + `--no-model` несовместимы (молча).**
В `main()` при `--no-model` без `--ru-postpass` exit 2, ок. Но `--suite --no-model --ru-postpass` пройдёт и для каждого сэмпла прогонит только regex. README не упоминает, что это допустимо.

**F12. README: `pf_tester/cli.py` поддерживает `--num-threads`, README говорит про `PF_NUM_THREADS` env.**
Работает оба варианта (env читается в `_tune_torch`), но в README раздел "Производительность" упоминает только env. Минор.

---

## 4. Безопасность

**S1. CORS `allow_origins=*` де-факто.**
`allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", *DOMAIN_origins]`, `allow_methods=["GET","POST"]`. ОК для on-prem, но у `/redact/file` нет CSRF-токена, и если приложение опубликовано — любой сайт под тем же доменом может POST'ить файлы. Не критично пока сервис сам по себе, но стоит явно прокомментировать.

**S2. Нет rate-limit / size-limit на JSON-эндпоинты.**
Отправил 100 параллельных запросов с длинным текстом — модель уйдёт в очередь, OOM на CPU. `slowapi` или middleware с `asyncio.Semaphore` решает.

**S3. `_pf()` пробрасывает `str(exc)` в HTTP detail.**
Сообщение HF может содержать пути на диск, имя пользователя, токены окружения. Логировать полностью, наружу — generic message.

**S4. Multipart upload пишет всё в память (`await file.read()`).**
5 MB ок, но если кто-то поднимет `PF_MAX_UPLOAD_BYTES` до 100 MB — RAM. Стрим + лимит на чанках безопаснее.

**S5. UI отправляет cleartext PII по незашифрованному `http://` если запущено локально.**
Минор для on-prem, но в README стоит явно сказать «запускать через Caddy с TLS, локально только для dev».

**S6. Нет аудита запросов.**
Сервис, который видит PII, не логирует, кто что прислал и какие категории найдены (хотя бы счётчики). Compliance-релевантно.

---

## 5. Качество кода

**Q1. Дублирование `Span`-конвертации между `detect()` и `detect_batch()`.**
20 строк копипасты. Вынести `_raw_to_spans(raw, min_score)`.

**Q2. `_RedactOnly` в CLI — явный hack.**
Объект имитирует `PrivacyFilter` через `PrivacyFilter.redact(self, ...)` (вызов unbound method с чужим self). Работает, потому что `redact()` не использует `self._pipe`. Хрупко: любое использование `self.something` в `redact` сломает. Лучше — вынести `redact` в module-level функцию, оба класса будут её звать.

**Q3. Отсутствие `from __future__ import annotations` в `samples.py` и `__init__.py`.**
В остальных модулях есть, в этих нет. Минор, но нарушает консистентность.

**Q4. Type hints неполные.**
`health() -> dict[str, object]` — `object` слишком широк, лучше `TypedDict`.
`_RedactOnly.redact` без типов вообще.

**Q5. Нет ruff/black/mypy конфига.**
1800 строк без линтера. CI из коробки нет (`.github/workflows/`).

**Q6. `pyproject.toml` отсутствует.**
Только `requirements.txt`. Нельзя сделать `pip install -e .` или собрать wheel. Для проекта с CLI (`python -m pf_tester.cli` → можно бы `pf-tester` entry-point) — просится.

**Q7. Версии в `requirements.txt` — только нижние границы.**
`transformers>=4.46.0` — через год может сломаться. Pin верхней границы major-версии (`<5.0`) или `requirements.lock` для воспроизводимости.

---

## 6. Приоритезированный список улучшений

### Критично (баги, безопасность)

1. **F4** — `Span.text = text[start:end]`, не `item["word"]`. Однострочник, риск некорректных границ.
2. **F1** — добавить контекст-якоря для INN/OGRN/паспорта (как минимум опциональный strict-режим).
3. **A1** — Lock на `_detect_cache` или `cachetools.LRUCache` (он thread-safe).
4. **S2** — rate-limit / `asyncio.Semaphore` перед `pipeline(...)`.
5. **F3** — унификация валидации `mask_char` через pydantic.
6. **P6** — `Field(max_length=…)` на `text` в `DetectRequest`.

### Сильно желательно (производительность, UX)

7. **A2** — async обёртка с `run_in_executor` + bounded executor.
8. **P9** — prometheus middleware (`/metrics`) или OTel.
9. **A3** — структурированный лог + readiness/liveness.
10. **P7** — union-regex per entity-class.
11. **F5** — применять `min_score` после postpass.
12. **F2** — расширить TLD-список + bare host без TLD по контексту.
13. **P10** — AbortController в UI.

### Косметика / хорошие практики

14. **A6** — `class Entity(StrEnum)`.
15. **A8** — вынести cache в отдельный модуль.
16. **Q1** — `_raw_to_spans` хелпер.
17. **Q2** — module-level `redact()`.
18. **Q5/Q6** — `pyproject.toml` + ruff + mypy + GitHub Actions CI.
19. **F10** — добавить тесты на FP/regression.
20. **P8** — `torch.cuda.synchronize()` в bench.
21. **F11/F12** — синхронизация README с фактическим API.

---

## 7. Итог

Проект — хороший пример «маленькая зрелая обёртка»: на ~1800 LoC влезли модель, regex-аугментация, CLI, HTTP, SPA UI и Docker-обвязка. Структура и стиль — выше среднего для тулинговых репозиториев.

Основные риски сейчас:
- **корректность спанов** (F4) и **regex false positives** (F1, F2);
- **отсутствие ограничителей нагрузки** (S2, P6);
- **гонки в кэше** (A1) при `--workers > 1` или нагрузке.

Закрыть критичный список (1–6 в §6) — день работы. После этого проект готов к on-prem развёртыванию с минимальным сопровождением.
