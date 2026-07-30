# LLM-Hell — панель тестирования связки GLM 4.7 (planner) + GLM 4.7 Flash (executor)

## Context

Две модели крутятся на RunPod через vLLM (OpenAI-совместимый API): GLM 4.7 — проектировщик, GLM 4.7 Flash — исполнитель. Нужно дать ~20 тестировщикам инструмент, где они гоняют связку на своём реальном коде и реальных задачах, а мы собираем и техническую, и качественную статистику в Grafana.

Проект создаётся с нуля в пустой директории `D:\DEV\llm-hell`.

Решения, зафиксированные с пользователем:
- **Режим**: агентный цикл с реальным исполнением кода в sandbox.
- **Sandbox живёт на VPS, не на RunPod.** Панель разворачивается на отдельном VPS с root+Docker, RunPod остаётся чистым inference-эндпоинтом. Проблемы docker-in-docker внутри арендованного пода не возникает.
- **Доступ**: инвайт-коды / выданные логины, роли admin/user.
- **Импорт кода**: drag-and-drop папки + отдельные файлы/вставка сниппета.
- **Reasoning**: как именно эндпоинт им управляет — неизвестно, поэтому слой должен быть настраиваемым через админку.
- **Задачи**: только свободные (каждый приносит своё), без каталога заданий.
- **Контекст**: гибрид — файлы подтягиваются tool-вызовами, история сжимается по порогу.
- **Управление**: переключатель режима в UI (autopilot / апрув плана / пошагово).
- **Лимиты**: очередь запусков, лимиты на запуск, кил-свитч админа.
- **Стек**: Python FastAPI + React, всё поднимается `docker compose up`.

Принятые допущения (не обсуждались, меняются легко): результат работы можно скачать патчем/zip; Grafana использует два датасорса — Prometheus (тех-метрики) и Postgres (оценки/исходы/поведение).

**Язык интерфейса и пользовательских сообщений — украинский.** Все тексты в UI, серверные HTTPException-сообщения, тосты, лейблы дашбордов и т.п. пишутся на украинском (термины можно оставлять английскими, где это естественно).

**Русского языка нет нигде в самом проекте** — ни в интерфейсе, ни в коде. Код (идентификаторы, комментарии, docstring'и, README, сообщения commit/CI и т.п.) — только на английском. Русский используется исключительно в этом плане и в диалоге с пользователем, не в артефактах проекта.

---

## Архитектура

Сервисы в `docker-compose.yml` на VPS:

| Сервис | Роль |
|---|---|
| `web` | React+Vite+TS, отдаётся nginx, он же reverse-proxy на api |
| `api` | FastAPI: auth, проекты, сессии, SSE-стрим, админка, `/metrics` |
| `worker` | агентный цикл: вызовы vLLM, tool-calls, управление sandbox-контейнерами |
| `postgres` | все данные |
| `redis` | очередь запусков, pub/sub для SSE, счётчики лимитов, cancel-канал |
| `prometheus` | скрейпит `api`, `worker` и оба vLLM `/metrics` на RunPod |
| `grafana` | дашборды, провижининг из репозитория |
| — | `llmhell-runner` — образ sandbox, контейнеры создаются `worker`'ом на лету |

`worker` монтирует `/var/run/docker.sock`. Это root-эквивалент на хосте — доступ инкапсулируется в единственный класс `DockerSandbox`, реализующий интерфейс `SandboxBackend`, чтобы позже можно было вынести в отдельный `sandboxd` без переписывания агента.

Структура репозитория:

```
llm-hell/
  docker-compose.yml   .env.example   README.md
  backend/
    app/
      api/          routers: auth, projects, sessions, runs(SSE), ratings, admin, metrics
      core/         config, security, deps, limits
      models/       SQLAlchemy
      schemas/      Pydantic
      services/
        llm/        client.py, reasoning.py, tokenizer.py, toolcalls.py
        context/    assembler.py, compactor.py, repomap.py, counter.py
        agent/      planner.py, executor.py, loop.py, tools.py, events.py
        sandbox/    base.py, docker_backend.py
        projects/   ingest.py, tree.py, diff.py
        stats/      prom.py, events.py
      workers/      run_worker.py (arq)
    alembic/  tests/  Dockerfile
  frontend/
    src/  components/ pages/ hooks/ api/ store/
    Dockerfile
  runner/Dockerfile
  prometheus/prometheus.yml
  grafana/provisioning/{datasources,dashboards}/  grafana/dashboards/*.json
  tools/mock_vllm.py
```

---

## Ключевые подсистемы

### 1. Слой моделей и reasoning (`services/llm/`)

Эндпоинты не хардкодятся — таблица `model_endpoints`: `name, base_url, api_key, model_id, role(planner|executor), ctx_window, price_per_mtok_in/out, enabled, reasoning_profile jsonb, tools_mode`.

`reasoning_profile` описывает, как уровень из UI превращается в параметры запроса и как парсить ответ:

```json
{
  "levels": {
    "off":    {"extra_body": {"chat_template_kwargs": {"thinking": {"type": "disabled"}}}},
    "low":    {"extra_body": {"chat_template_kwargs": {"thinking": {"type": "enabled"}}},
               "max_reasoning_tokens": 1024},
    "medium": {"extra_body": {"reasoning_effort": "medium"}},
    "high":   {"extra_body": {"reasoning_effort": "high"}}
  },
  "parse": {"mode": "auto", "field": "reasoning_content", "tags": ["<think>", "</think>"]}
}
```

- `parse.mode: auto` — парсер сначала читает `delta.reasoning_content` (если vLLM запущен с `--reasoning-parser`), иначе режет инлайновые теги из `content` конечным автоматом по стриму.
- В админке — кнопка **«Проверить эндпоинт»**: дёргает `/v1/models`, `/tokenize`, шлёт короткий запрос на каждом уровне и показывает, что реально вернулось (есть ли `reasoning_content`, приняты ли `extra_body`-параметры, поддерживаются ли native tool calls). Это и есть способ выяснить неизвестное без переписывания кода.
- `tools_mode`: `native` (OpenAI tools API, если vLLM с `--tool-call-parser`) либо `json_protocol` — фолбэк, где инструменты описываются в system-промпте, а модель отвечает JSON-блоком; парсер общий. **Фолбэк делаем сразу**, потому что поддержка tool-calls у эндпоинтов неизвестна.

Токенизация — через `POST /tokenize` самого vLLM (точный счёт), с кэшем по хэшу текста и эвристическим фолбэком `len/3.5` при недоступности.

### 2. Импорт кода (`services/projects/`, фронт)

- Drag-and-drop папки через `webkitdirectory`; фильтрация **на клиенте до загрузки**: `.gitignore`, `node_modules`, `.git`, `dist`, `build`, `venv`, бинарники по расширению и по нулевым байтам, файлы > 1 МБ.
- Отдельные файлы и вставка сниппета — тот же пайплайн, `source_type` различается.
- Батчевая multipart-загрузка с прогрессом; лимиты из конфига: ≤ 3000 файлов, ≤ 50 МБ суммарно, ≤ 1 МБ на файл.
- На сервере: распаковка в `/data/projects/{project_id}/workspace`, `git init` + первый коммит (даёт бесплатный `git diff` для показа изменений и скачивания патча), запись метаданных в `project_files`, построение repo-map.
- В UI — дерево файлов с чекбоксом «pinned» (закреплённые файлы никогда не вытесняются из контекста) и размером в токенах у каждого файла.

### 3. Контекст: сборка, счётчики, уплотнение (`services/context/`)

Промпт собирается сегментами, и каждый сегмент считается отдельно:

1. `system` — роль (planner/executor) + описание инструментов
2. `repo_map` — дерево с размерами + сигнатуры символов для крупных файлов
3. `pinned` — закреплённые файлы целиком
4. `retrieved` — файлы, прочитанные tool-вызовами (хранится только последняя версия каждого файла)
5. `summary` — блок сжатой истории
6. `history` — последние сообщения и результаты инструментов

**Счётчик контекста в UI**: горизонтальный бар с разбивкой по этим шести сегментам, `используется / ctx_window` в токенах и процентах, отдельным числом — reasoning-токены. Обновляется событием `context_update` в SSE-стриме. Клик по сегменту открывает context inspector — что именно ушло в промпт.

**Автоуплотнение** срабатывает при достижении порога (по умолчанию 75%, настраивается):
- вывод инструментов длиннее N строк усекается до head+tail с пометкой;
- устаревшие версии прочитанных файлов выбрасываются;
- старшие ~40% истории сжимаются отдельным вызовом planner-модели с `reasoning=off` в структурированное саммари (что сделано, что сломалось, какие решения приняты, какие файлы затронуты) и заменяются одним сообщением;
- всё пишется в таблицу `compactions` (tokens_before/after/trigger/summary) — это и метрика, и материал для отладки;
- в UI — тост «Контекст уплотнён: 84k → 31k» + ручная кнопка «Уплотнить сейчас».

### 4. Агентный цикл (`services/agent/`)

```
задача + проект
   ↓ planner (GLM 4.7, reasoning по настройке)
структурированный план: [{id, title, intent, files, done_when}]
   ↓ гейт по режиму (autopilot | approve_plan | stepwise)
executor (Flash) по шагам, tool-calling:
   read_file, list_dir, grep, apply_patch/write_file, run_command, run_tests, finish_step
   ↓ при 2 подряд провалах шага → escalate: возврат к planner с контекстом ошибки → правка плана
завершение: сводка, git diff, статус
```

- Режим выбирается перед запуском переключателем в UI и пишется в `runs.mode` (попадает в метрики).
- В `approve_plan` план показывается редактируемым — правки пользователя логируются как `user_interventions`.
- В `stepwise` каждый tool-call с побочным эффектом (`write`, `run_command`) требует подтверждения.
- Кнопка **Stop** публикует cancel в Redis; воркер прерывает стрим и убивает контейнер.
- Лимиты на запуск: `max_iterations` (по умолч. 25), `max_tokens_per_run`, `max_wall_time` (20 мин), `command_timeout` (120 с).

### 5. Sandbox (`services/sandbox/`)

- Образ `runner/Dockerfile`: Python 3.12 + Node 22 + Go + Rust, ripgrep, git, базовые пакетные менеджеры.
- Контейнер на сессию: `network_mode=none` (сеть по умолчанию выключена; включение установки зависимостей — отдельный флаг сессии с прогретым кэшем), `cpus`, `mem_limit`, `pids_limit`, `read_only` корень + `tmpfs /tmp`, non-root user, воркспейс проекта монтируется единственным rw-томом.
- Контейнер живёт, пока идёт запуск + idle-таймаут; reaper прибирает сирот при старте воркера.
- Все команды идут через `SandboxBackend.exec()` с таймаутом и усечением вывода.

**Реализовано:** `services/sandbox/base.py` (ABC `SandboxBackend`/`SandboxHandle`, `SandboxConfig`, `workspace_host_path()`) + `docker_backend.py` (`DockerSandbox` через `docker` SDK). Команды exec'аются в один долгоживущий контейнер на запуск (`sleep infinity` + `exec_run` на каждую команду), таймаут — через coreutils `timeout --signal=KILL` внутри контейнера (не только `asyncio.wait_for` снаружи), усечение вывода переиспользует `context.compactor.truncate_tool_output`. `reap_orphans()` подключён к старту воркера (`app/workers/run_worker.py`); `reap_idle()` — метод для периодического вызова из планировщика задачи 6/7 (сам по себе воркер не решает, когда его звать).

**Важный нюанс DooD:** воркер обращается к *хостовому* Docker-демону через `/var/run/docker.sock`, поэтому bind-mount воркспейса для sandbox-контейнера должен указывать на путь **хоста**, а не путь внутри контейнера api/worker. `projects_data` заменён с named volume на host bind-mount (`PROJECTS_HOST_DIR`, по умолчанию `./data/projects`), отдельно от `PROJECTS_DIR` (путь внутри api/worker). `docker-compose.yml` получил сервис `runner` с профилем `build-only` — не запускается, только собирает и тегирует образ (`docker compose build runner`).

**Сознательно упрощено:** флаг «сеть включена для установки зависимостей с прогретым кэшем» реализован только как переключение `network_mode` (none↔bridge) через `SandboxConfig.network_enabled`; отдельного механизма «прогретого кэша» (baked-in кэш npm/pip/cargo, warm volume) в плане не было достаточно детализировано для реализации — оставлено на усмотрение задачи 6/7, когда появится реальный сценарий использования.

### 6. Стриминг и события

- `GET /api/runs/{id}/events` — SSE. Типы: `token`(content), `reasoning`(отдельный канал), `tool_call_start/end`, `step_change`, `plan_ready`, `context_update`, `compaction`, `usage`, `error`, `done`.
- Redis pub/sub на fanout — несколько вкладок и переподключение переживаются; все события пишутся в БД, при реконнекте отдаётся реплей с `last_event_id`.
- Reasoning в UI — сворачиваемый блок над ответом, с живым счётчиком reasoning-токенов и таймером.

### 6b. Архитектура UI (уточнено пользователем в ходе разработки)

Рабочий экран — классический чат, а не панельный file-explorer-стиль:

- **Слева** — узкая боковая панель: переключатель/создание проектов, импорт кода (drag-and-drop/файли/сніппет), дерево файлів проєкту. Компактна, не является основным фокусом экрана.
- **По центру** — чат: список повідомлень (задача користувача, план від planner, дії executor'а, reasoning-блок, що згортається) знизу — поле вводу задачі, как в ChatGPT/Claude.
- **Панель коду** (праворуч або поверх чату) — вкладки по файлах (не по кроках виконання): кожен файл, який модель створює/редагує під час запуску, — окрема вкладка з підсвіткою синтаксису та (пізніше, задача 9) diff проти попередньої версії. Клік по файлу в дереві зліва теж відкриває вкладку в цій же панелі (перегляд поточного вмісту). Є кнопка розгорнути на весь екран (fullscreen overlay), і кнопка згорнути назад до чату.

Це визначає компоненти фронтенду: `Sidebar` (проєкти+імпорт+дерево), `ChatPanel` (задача 6), `CodePanel` з вкладками (основа закладається в задачі 3 для перегляду імпортованих файлів; задачі 6/9 наповнюють його файлами від агента та diff'ом).

**UI-кит: `@nmmty/dotmatrix`** (React 19, монохромна bitmap-дизайн-система, власний пакет користувача). Фронтенд перевели на React 19 + React DOM 19 заради peer-залежності. Ключове використання:
- `ThemeProvider`/`LayoutProvider`/`ToastProvider` — обгортка в `main.tsx`, стилі підключені через `@nmmty/dotmatrix/styles.css`.
- Layout-примітиви (`Column`/`Row`/`Grid`) з props-based стилями (`padding`, `gap`, `radius`, ...) замість ручного CSS; `style`/`className` — санкціонована відступна для того, чого немає в шкалі токенів (напр. фіксована ширина сайдбару 300px).
- `Drawer` (side="right") + вбудований у `CodeBlock` таббінг за файлами, копіюванням і **вбудованою кнопкою fullscreen** — саме це і закриває вимогу «відкривати код на весь екран» без окремої реалізації.
- `Table`/`List`/`Select`/`Input`/`PasswordInput`/`Checkbox`/`Chip`/`Badge`/`Dropdown` — адмінка, форма ендпоінтів, дерево файлів, чипи для закриття вкладок коду.
- Ручний CSS (`index.css`) зведений до мінімального ресету — усе інше через кит.

### 7. Очередь и лимиты

- `arq` поверх Redis: воркеры с общим лимитом одновременных запусков (`MAX_CONCURRENT_RUNS`, по умолч. 4). В UI — позиция в очереди и ETA.
- Лимиты на запуск (см. выше) + опциональные суточные квоты токенов на пользователя.
- Админка: список активных запусков, kill любого, включение/выключение эндпоинтов и правка их конфигов без редеплоя, глобальный «стоп приёма новых запусков».

### 8. Аутентификация

- `invites(code, role, expires_at, used_by)` — админ генерирует коды, пользователь при первом входе задаёт логин/пароль (argon2). JWT в httpOnly-cookie, refresh. Роли `admin`/`user`. Первый админ создаётся seed-скриптом из `.env`.

### 9. Статистика и Grafana

**Prometheus** (`prometheus_client` в api и worker + скрейп двух vLLM `/metrics`):
- гистограммы: `llm_ttft_seconds`, `llm_request_duration_seconds`, `llm_output_tps` — лейблы `model, role, reasoning_level`
- счётчики: `llm_tokens_total{kind=prompt|completion|reasoning}`, `llm_errors_total{type}`, `agent_tool_calls_total{tool,ok}`, `context_compactions_total`
- гейджи: `runs_active`, `queue_depth`, `sandbox_containers`
- со стороны vLLM: глубина очереди, KV-cache utilization, throughput

**Postgres-датасорс** (качество и поведение):
- `ratings` — thumbs + шкалы 1–5 (корректность плана / качество кода / следование инструкции) + комментарий, на сообщение и на запуск целиком
- `run_outcomes` — solved / tests_passed / iterations_to_success / user_interventions / stop_reason
- `events` — активность по пользователям, длительность сессий, размеры импортированных проектов, использование reasoning-уровней, число уплотнений
- стоимость — из `price_per_mtok` эндпоинта, считается в SQL

**Дашборды** (JSON в репо, провижининг):
1. *Inference health* — TTFT, tok/s, ошибки, очередь vLLM, KV-cache
2. *Quality & feedback* — оценки по моделям и reasoning-уровням, доля решённых задач, число итераций
3. *Usage & behavior* — активность тестировщиков, размеры проектов, уплотнения, режимы запуска
4. *Runs explorer* — таблица запусков с прокликом в конкретную сессию панели

---

## Файлы, которые появятся первыми (критичные)

- `docker-compose.yml`, `.env.example` — вся раскладка сервисов
- `backend/app/services/llm/reasoning.py` — маппинг уровней и парсер reasoning (сердце неизвестности про эндпоинты)
- `backend/app/services/llm/client.py` — стриминговый клиент vLLM + native/json tool-calls
- `backend/app/services/context/assembler.py`, `compactor.py`, `counter.py`
- `backend/app/services/agent/loop.py`, `tools.py`
- `backend/app/services/sandbox/docker_backend.py`
- `backend/app/models/` + первая миграция alembic
- `frontend/src/pages/Workbench.tsx` (чат + reasoning + план + дерево файлов + счётчик контекста), `ImportDialog.tsx`, `Admin*.tsx`
- `runner/Dockerfile`, `grafana/dashboards/*.json`, `tools/mock_vllm.py`

Переиспользование: своего кода в репозитории нет, поэтому опираемся на библиотеки — `openai` SDK (совместим с vLLM), `arq`, `SQLAlchemy 2 + alembic`, `prometheus_client`, `docker` SDK, `pathspec` (парс `.gitignore`), `unidiff`; на фронте — `@tanstack/react-query`, `zustand`, `monaco-editor` для просмотра файлов и диффов, `shadcn/ui`.

---

## Порядок работ

1. **Каркас**: compose, БД+миграции, auth с инвайтами, `mock_vllm.py` — чтобы разрабатывать без GPU.
2. **LLM-слой**: клиент, reasoning-профили, `/tokenize`, админка эндпоинтов с «Проверить эндпоинт». **Здесь же — прогон против реальных RunPod-эндпоинтов, чтобы закрыть вопрос про reasoning и tool-calls до того, как на нём завяжется агент.**
3. **Проекты**: импорт папки/файлов, дерево, repo-map, pinned, git-инициализация.
4. **Контекст**: ассемблер, счётчики, компактор, context inspector.
5. **Sandbox + инструменты**: образ runner, docker-бэкенд, tool-функции.
6. **Агентный цикл**: planner→executor, три режима, escalate, SSE, Stop.
7. **Очередь и лимиты**: arq, конкурентность, квоты, админ-кил-свитч.
8. **Статистика**: prometheus-метрики, формы оценок, `run_outcomes`, дашборды Grafana.
9. **Полировка UI**: diff-вьюер, скачивание патча/zip, история сессий.

---

## Проверка

**Локально, без GPU:**
1. `docker compose --profile dev up` — поднимает всё вместе с `mock_vllm` (имитирует стрим, `reasoning_content`, `/tokenize`, tool-calls; умеет режимы «нет reasoning_content», «инлайновые теги», «нет native tools» — для проверки всех веток парсера).
2. `pytest backend/tests` — юниты на парсер reasoning, ассемблер/компактор контекста (детерминированные фикстуры), tool-протокол, лимиты; интеграционные на sandbox (`echo`, таймаут, OOM, отказ сети) и на полный цикл против mock-эндпоинта.
3. Ручной e2e: инвайт → регистрация → импорт небольшого Python-репо с падающим тестом → задача «почини тест» в режиме approve_plan → план → выполнение → зелёные тесты → diff → оценка.
4. Проверка счётчика контекста: импортировать репо заведомо больше окна, убедиться, что промпт не переполняется, сработало уплотнение и запись в `compactions` появилась.

**На VPS с реальными эндпоинтами:**
5. В админке прописать оба RunPod-URL, нажать «Проверить эндпоинт», зафиксировать реальный reasoning-профиль.
6. Нагрузочный прогон: 8–10 одновременных запусков — проверить очередь, кил-свитч, что VPS не ложится.
7. Grafana `http://vps:3000` — все четыре дашборда наполняются, обе датасорса живы, метрики vLLM с RunPod скрейпятся.
