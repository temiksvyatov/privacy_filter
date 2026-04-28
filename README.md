# OpenAI Privacy Filter — тестовый стенд

Минимальный набор инструментов, чтобы погонять модель
[`openai/privacy-filter`](https://huggingface.co/openai/privacy-filter)
(анонсирована 22 апреля 2026, Apache-2.0): CLI, HTTP-сервис на FastAPI и
готовый набор PII-сэмплов.

> Privacy Filter — это bidirectional token-classifier (1.5B параметров,
> 50M активных), детектит 8 категорий PII: `account_number`,
> `private_address`, `private_email`, `private_person`, `private_phone`,
> `private_url`, `private_date`, `secret`. Обрабатывает до 128k токенов
> за один forward pass.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

При первом обращении модель (~2.8 GB) скачается с HuggingFace в
`~/.cache/huggingface`.

## CLI

```bash
# inline
python -m pf_tester.cli "Alice was born on 1990-01-02 and lives in Berlin."

# из файла
python -m pf_tester.cli -f some_text.txt

# через pipe
cat some_text.txt | python -m pf_tester.cli

# JSON-вывод (удобно для дальнейшего парсинга)
python -m pf_tester.cli --json "Email me at bob@acme.io"

# прогнать все встроенные сэмплы
python -m pf_tester.cli --suite

# кастомный плейсхолдер: каждый спан -> одна строка
python -m pf_tester.cli --placeholder "[REDACTED]" "Call Alice at +1 415 555 0142"

# маскировка звёздочками: длина PII сохраняется
python -m pf_tester.cli --stars "Call Alice at +1 415 555 0142"
# Call ***** at ****************

# любой другой символ-маска
python -m pf_tester.cli --mask-char "#" "Call Alice"

# фильтр по уверенности (0..1)
python -m pf_tester.cli --min-score 0.85 -f notes.txt

# Russian regex post-pass (паспорт, СНИЛС, ИНН, ОГРН, +7, .рф, …)
python -m pf_tester.cli --ru-postpass --stars -f sample_ru.txt

# offline-проверка одной regex-составляющей (без модели)
python -m pf_tester.cli --no-model --ru-postpass "ИНН 770123456789, +7 495 123-45-67"
```

Вывод по умолчанию — таблица `rich` с найденными спанами + редактированный
текст вида `[PRIVATE_PERSON] lives at [PRIVATE_ADDRESS]`.

## HTTP-сервис

```bash
uvicorn pf_tester.service:app --reload --port 8000
```

Эндпоинты:

| метод | путь             | описание                                                  |
|-------|------------------|-----------------------------------------------------------|
| GET   | `/`              | встроенный SPA-UI                                         |
| GET   | `/health`        | health-check + имя модели + статистика кэша               |
| GET   | `/samples`       | словарь встроенных тестовых текстов                       |
| POST  | `/detect`        | спаны с координатами и скором                             |
| POST  | `/redact`        | спаны + текст с заменой PII                               |
| POST  | `/redact/file`   | multipart upload, удобно для больших файлов через `curl`  |

Все JSON-эндпоинты принимают необязательные `min_score` (0..1) и
`ru_postpass` (bool). `/redact` дополнительно — `placeholder` и
`mask_char` (одиночный символ).

Пример:

```bash
curl -s localhost:8000/detect \
  -H 'content-type: application/json' \
  -d '{"text":"Alice lives in Berlin, email alice@example.com"}' | jq
```

```bash
curl -s localhost:8000/redact \
  -H 'content-type: application/json' \
  -d '{"text":"Token sk-proj-AbCd...","placeholder":"[REDACTED]"}' | jq
```

Маскировка одним символом с сохранением длины (`mask_char` приоритетнее `placeholder`):

```bash
curl -s localhost:8000/redact \
  -H 'content-type: application/json' \
  -d '{"text":"Иванов Иван, +7 495 123-45-67","mask_char":"*"}' \
  | jq -r '.redacted'
# *********** **, ****************
```

Multipart-загрузка файла (большие файлы или просто чтобы не эскейпить JSON):

```bash
curl -s localhost:8000/redact/file \
  -F file=@sample_ru.txt \
  -F mask_char='*' \
  -F ru_postpass=true \
  | jq -r '.redacted'
```

Конфиг через переменные окружения:

- `PF_MODEL` — id модели (по умолчанию `openai/privacy-filter`).
- `PF_DEVICE` — `cpu`, `cuda`, `cuda:0` и т. п.

## Docker / docker-compose

```bash
cp .env.example .env  # опционально, чтобы переопределить модель/устройство

# CPU-сборка (по умолчанию). Первый build тянет CPU-колёса torch (~700 MB).
docker compose up --build

# Прогнать встроенный suite через одноразовый CLI-контейнер
docker compose --profile cli run --rm pf-cli --suite
docker compose --profile cli run --rm pf-cli --json "Email me at bob@acme.io"

# GPU-сборка (нужен NVIDIA Container Toolkit на хосте)
docker compose --profile gpu up --build pf-service-gpu
```

Веса HuggingFace (~2.8 GB) кэшируются в named volume `hf_cache`, поэтому
повторные запуски стартуют без повторной выкачки. Healthcheck дёргает
`/health` после прогрева (`start_period: 120s`).

Эндпоинты после старта:

- `http://localhost:8000/` — встроенный web UI
- `http://localhost:8000/docs` — Swagger
- `http://localhost:8000/health`

## Web UI + публикация через Caddy

В контейнер встроен лёгкий одностраничный UI (vanilla JS, без сборки):
textarea → кнопка `Redact` → подсветка PII в исходном тексте, таблица
со спанами и редактированный текст. Никаких внешних запросов кроме
своего же API — отлично для on-prem.

Локально UI открывается на `http://localhost:8000/`. Чтобы выставить
наружу через ваш уже настроенный Caddy:

1. Прописать в `.env`:

   ```env
   DOMAIN=privacy-filter.example.com
   HOST_PORT=8000
   ```

2. Поднять сервис: `docker compose up -d --build`.

3. В вашем `Caddyfile` добавить блок (Caddy сам выпустит TLS-сертификат):

   ```caddy
   privacy-filter.example.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```

   Если Caddy и сервис в одной docker-сети — используйте имя контейнера:
   `reverse_proxy pf-tester:8000`.

Что делает переменная `DOMAIN` внутри приложения:

- добавляет `https://${DOMAIN}` (и `http://`) в CORS allow-list;
- показывается в шапке UI (`/health` отдаёт это поле).

Uvicorn запускается с `--proxy-headers --forwarded-allow-ips "*"`, поэтому
корректно отрабатывает `X-Forwarded-Proto`/`X-Forwarded-For` от Caddy.

## Тесты

```bash
pytest -q
```

Тесты не дёргают модель: редактор замокан, сервис прогоняется через
`fastapi.testclient`. Это smoke-проверки логики, а не качества модели.

## Производительность

- Внутренний кэш ответов модели (LRU по `sha256(text + min_score + ru_postpass)`).
  Размер настраивается через `PF_CACHE_SIZE` (по умолчанию 256). Видно
  в `/health` и в баннере UI как `cached`.
- `torch.inference_mode()` + `model.eval()` уменьшают накладные расходы
  относительно дефолтного режима пайплайна.
- `PF_NUM_THREADS=N` — выставит `torch.set_num_threads(N)`. На
  многоядерных CPU обычно имеет смысл выставить число физических ядер.
- Батч-инференс для нескольких текстов:

  ```python
  from pf_tester.filter import PrivacyFilter
  pf = PrivacyFilter()
  results = pf.detect_batch(["text 1", "text 2", "text 3"], batch_size=8)
  ```

- Бенчмарк-команда:

  ```bash
  python -m pf_tester.bench --runs 5
  python -m pf_tester.bench --device cpu --batch-size 4 --num-threads 8
  ```

  Печатает `chars/s` throughput и p50/p90/p99 латентность по документу.

## Русский язык

Privacy Filter обучался преимущественно на английском; на русском
recall заметно хуже, особенно по идентификаторам (паспорт, СНИЛС, ИНН,
ОГРН) и по нестандартным форматам телефонов. Чтобы закрыть этот
разрыв, есть `ru_postpass` — детерминированный regex-слой, который
запускается **после** модели и добавляет недостающие спаны без
дублирования того, что уже нашла модель.

Что ловит:

| Категория          | Что покрывает                                                  |
|--------------------|----------------------------------------------------------------|
| `account_number`   | Паспорт РФ, СНИЛС, ИНН (10/12), ОГРН (13/15), банк. карты, IBAN |
| `private_phone`    | `+7`/`8` форматы с любыми разделителями                         |
| `private_email`    | Кириллический local part и домен                                |
| `private_url`      | Кириллические TLD (`.рф`, `.москва`)                            |
| `private_date`     | `DD.MM.YYYY`, `12 января 2025 г.`                               |
| `secret`           | `sk-…`, `AKIA…`, `ghp_…`, GitHub PAT                            |

Включается флагом:

- CLI: `--ru-postpass`
- API: `"ru_postpass": true` в теле `/detect` и `/redact`
- UI: галочка «RU regex post-pass» (включена по умолчанию)

Имена ФИО на русском оставлены модели — regex по русским склонениям
даёт слишком много false positives. Если recall по именам критичен —
дальше уже файнтюн через официальный `opf train` на своих данных.

## Что стоит проверить руками

- **Code-блоки с секретами**: пасворды, API-ключи, JWT.
- **Многоязычные тексты** (особенно русский) — модель тренировалась
  преимущественно на английском, recall может проседать.
- **Длинные документы** — у модели контекст 128k, имеет смысл проверить
  on-prem на реальных логах/тикетах.
- **Кастомный плейсхолдер** vs. `[ENTITY_TYPE]` — для downstream-LLM
  обычно полезнее именно типизированный плейсхолдер.

## Структура

```
pf_tester/
  filter.py        # transformers pipeline + redact() + detect_batch()
  ru_postpass.py   # regex-слой под российские реалии
  cli.py           # CLI: inline / file / stdin / --suite / --no-model
  bench.py         # throughput + p50/p90/p99 латентность
  service.py       # FastAPI: SPA UI + JSON + multipart endpoints
  samples.py       # тестовые тексты (en + ru)
  web/             # ванильный SPA (HTML/CSS/JS) с light/dark темой
tests/
  test_redaction.py    # юнит-тесты редактора
  test_ru_postpass.py  # юнит-тесты regex-слоя
  test_service.py      # smoke-тесты HTTP с замоканной моделью
```
