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

# выбрать устройство и кастомный плейсхолдер
python -m pf_tester.cli --device cpu --placeholder "***" "Call +1 415 555 0142"
```

Вывод по умолчанию — таблица `rich` с найденными спанами + редактированный
текст вида `[PRIVATE_PERSON] lives at [PRIVATE_ADDRESS]`.

## HTTP-сервис

```bash
uvicorn pf_tester.service:app --reload --port 8000
```

Эндпоинты:

| метод | путь        | описание                                       |
|-------|-------------|------------------------------------------------|
| GET   | `/health`   | health-check + имя модели                      |
| GET   | `/samples`  | словарь встроенных тестовых текстов            |
| POST  | `/detect`   | вернёт массив спанов с координатами и скором   |
| POST  | `/redact`   | вернёт спаны и текст с заменой PII             |

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

Эндпоинт после старта: `http://localhost:8000/health`,
Swagger UI: `http://localhost:8000/docs`.

## Тесты

```bash
pytest -q
```

Тесты не дёргают модель: редактор замокан, сервис прогоняется через
`fastapi.testclient`. Это smoke-проверки логики, а не качества модели.

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
  filter.py     # обёртка над transformers pipeline + редактор
  cli.py        # CLI: inline / file / stdin / suite
  service.py    # FastAPI: /health, /samples, /detect, /redact
  samples.py    # 8 готовых тест-текстов (по одному на категорию + edge cases)
tests/
  test_redaction.py  # юнит-тесты редактора (без модели)
  test_service.py    # smoke-тесты HTTP с замоканной моделью
```
