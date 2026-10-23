# 🐈 MURCLOUD 10 000 ULTRA

**MURCLOUD OS 10.0.0 Beta** — облачная операционная среда с собственным языком
программирования **MUSYA**, исполнителем браузерных команд **Service Engine**,
встроенным ИИ-агентом **MUR AI**, файловым хранилищем, удалённым Linux-ПК и приложениями.

*Powered by MUSYA Runtime & Service Engine · Developed by ARTYONIX STUDIOS.*

Всё в этом репозитории — работающий код: браузер — настоящий Chromium (Playwright),
ИИ — настоящие API Anthropic / OpenAI / OpenRouter с tool calling, MUR PC — настоящий
XFCE-рабочий стол в Docker, файлы — на диске/в S3, данные — в PostgreSQL.

```
 браузер / Android ──HTTPS + WSS──▶  FastAPI backend ─┬─ MUSYA Runtime (lexer → parser → AST → async interpreter)
   MURCLOUD Desktop (React)                           ├─ Service Engine (очередь команд → Playwright/Chromium, SSRF-фильтр)
   · Service Studio · MUR AI · Drive                  ├─ MUR AI Core (Anthropic / OpenAI / OpenRouter, инструменты, политика, аудит)
   · Browser · Terminal · PC · Gaming                 ├─ MUR Drive (PostgreSQL метаданные + диск или S3)
   · RTX Lab · Soup · Settings · Packages             ├─ Package Manager (.driver / app.yml, подтверждение разрешений)
                                                      └─ MUR PC manager ──Docker──▶ изолированные XFCE + VNC контейнеры
```

## Что внутри

| Компонент | Где | Что реально делает |
|---|---|---|
| **MUSYA Runtime** | `musya-runtime/` | Лексер, парсер (рекурсивный спуск), AST, асинхронный интерпретатор: переменные, константы, числа/строки/bool/null, списки/словари, условия, циклы, функции и замыкания, лямбды, исключения `try/catch/finally/throw`, события `on/emit`, `spawn/await`, модули `import`, тесты `test/assert`, файловый API в песочнице, `ai.ask`, вызовы Service Engine. Лимиты шагов/глубины/памяти, никакого `eval/exec`. CLI `musya`. Валидация `.yml`/`.driver`. → [docs/MUSYA.md](docs/MUSYA.md) |
| **Service Engine** | `service-engine/` | Очередь команд на пользователя, асинхронное выполнение, тайм-ауты, отмена, события, логи; `start stop status load search click type press scroll wait screenshot download event ai tab back forward reload text html exists errors …` на Playwright/Chromium; отдельный BrowserContext на пользователя; SSRF-фильтр на каждый запрос страницы; безопасные загрузки; локальные preview-серверы. |
| **MUR AI Core** | `mur-ai/` | Агент с настоящим tool calling: файлы проекта, план, запуск `.mysya` и тестов, веб-сервер превью, управление браузером (со скриншотами, которые видит модель). Режимы Chat / Code / Agent / Auto, матрица риска, подтверждения, разрешения на диалог, аудит, редактирование секретов. Anthropic через официальный SDK (`claude-opus-5-5`, adaptive thinking, server-side fallback), OpenAI и OpenRouter через официальный SDK OpenAI. |
| **Backend** | `backend/` | FastAPI: авторизация (scrypt, сессии, CSRF, блокировка), PostgreSQL/SQLite, проекты Studio, запуск сценариев, терминал-REPL, MUR Drive (диск или S3), ключи ИИ (шифрование Fernet), пакеты, MUR PC, WebSocket-события, rate limiting, заголовки безопасности. → [docs/API.md](docs/API.md) |
| **MURCLOUD Desktop** | `frontend/` | React + TypeScript + Tailwind + Framer Motion: окна с перетаскиванием и ресайзом, панель задач, меню (Ctrl+K), уведомления, горячие клавиши, темы (неон/тёмная/светлая), обои, мобильная раскладка, сенсорное управление. |
| **MUR Service Studio** | `frontend/src/apps/studio` | Проводник, редактор CodeMirror (подсветка и автодополнение MUSYA, YAML, HTML/CSS/JS/Python), вкладки, автосохранение, диагностика, Run / Stop / Debug / Test / Save / Open / Build / Deploy / AI Fix, терминал, вывод, проблемы, Logs, живой Browser, Preview, панель MUR AI. Состояние вкладок восстанавливается после перезапуска. |
| **MUR Browser** | `BrowserApp` | Живой вид серверного Chromium, клики по картинке, ввод, прокрутка, вкладки, скриншоты в Drive. |
| **MUR Drive** | `DriveApp` | Загрузка (drag & drop), скачивание, папки, переименование/перемещение, удаление, поиск, предпросмотр текста/картинок/PDF/аудио/видео, квоты. |
| **MUR Terminal** | `TerminalApp` | MUSYA REPL с сохранением переменных + `service …`, `ls`, `cat`, `run`, `test`, `jobs`, `cancel`, `help`. |
| **MUR PC** | `remote-desktop/` + `PCApp` | Настоящий Debian + XFCE в Docker, noVNC через авторизованный WebSocket-прокси, запуск программ, CPU/RAM, переподключение, изоляция. Нужен VPS с Docker. → [remote-desktop/README.md](remote-desktop/README.md) |
| **MUR Gaming** | `gaming/` | Библиотека из 3 реально работающих игр (Мурзмейка, Мур 2048, Мур-мемори), профиль и рекорды на сервере, клавиатура/свайпы/геймпад (Gamepad API), переход в MUR PC (SGT Puzzles). |
| **MUR RTX Lab** | `RTXApp` | three.js `WebGPURenderer` с автоматическим откатом на WebGL 2, PBR, тени, отражения окружения и динамические (CubeCamera), FPS/draw calls, качество low…ultra, бенчмарк 15 с с историей. Темы «MURX 10 000» и «ULTRA GALAXY SOUP LSS» — фантастические, явно подписаны как не реальное железо. |
| **MUR Soup** | `SoupApp` | Варка мурсупа: рецепты, перемешивание жестом, счётчик порций, монеты, 7 достижений, синтезированные звуки (WebAudio), Муся / Красавчик / Артём, прогресс на сервере. |
| **MUR Settings / Packages / Monitor** | | Темы, ключи ИИ, сессии, смена пароля, журнал аудита; каталог драйверов с подтверждением чувствительных разрешений; реальные метрики сервера (psutil). |
| **Android** | `android/` | APK (Java, WebView), экран подключения с проверкой `/api/health`, только HTTPS, обработка офлайна, загрузка/скачивание файлов, иконка. → [android/README.md](android/README.md) |

## Быстрый старт (разработка)

Требования: Python 3.11+, Node 22, Chromium для Playwright.

```bash
pip install -e musya-runtime -e service-engine -e mur-ai -e "backend[s3,pc,test]"
python -m playwright install --with-deps chromium

cd frontend && npm ci && npm run build && cd ..
cd backend
MUR_FRONTEND_DIST=../frontend/dist uvicorn --factory app.main:get_app --port 8000
# → http://127.0.0.1:8000  (первый зарегистрированный пользователь — администратор)
```

Режим разработки UI с hot reload: `cd frontend && npm run dev` (прокси на backend :8000).

MUR AI: войдите → **MUR Settings → MUR AI** → добавьте ключ Anthropic / OpenAI / OpenRouter
(хранится зашифрованным), либо задайте общий ключ сервера `MUR_AI_KEY`.

Без MURCLOUD, только язык:

```bash
musya run examples/hello.mysya
musya test examples/hello.mysya
musya run examples/browser.mysya --engine     # настоящий Chromium
musya validate examples/browser.driver
```

## Развёртывание

**VPS (рекомендуется, всё включая MUR PC)** — Docker Compose с PostgreSQL и Caddy (автоматический HTTPS):

```bash
cp .env.example .env        # MUR_DOMAIN, MUR_SECRET_KEY, POSTGRES_PASSWORD
docker compose up -d --build
# MUR PC:
docker build -t murcloud/mur-pc:10.0 remote-desktop/
DOCKER_GID=$(getent group docker | cut -d: -f3) docker compose -f docker-compose.yml -f docker-compose.pc.yml up -d --build
```

**Render** — `render.yaml` (Blueprint): Docker web service + PostgreSQL + диск 10 ГБ,
`MUR_SECRET_KEY` генерируется. Нужен план ≥ 2 ГБ RAM (Chromium). MUR PC на Render
недоступен (нет Docker) — приложение честно об этом сообщает; используйте VPS.

**Render Free (бесплатно, без диска)** — `render-free.yaml` (New → Blueprint → Blueprint Path
`render-free.yaml`) или вручную с переменными `MUR_STORAGE=database`, `MUR_LOW_MEMORY=1`,
`MUR_ENV=production`, `MUR_COOKIE_SECURE=1`, `MUR_SECRET_KEY` (Generate), `DATABASE_URL`.
На бесплатном плане нет диска и файловая система стирается при каждом засыпании, поэтому
всё постоянное хранится в PostgreSQL: аккаунты, диалоги ИИ, файлы MUR Drive и копии проектов
Studio и пакетов (восстанавливаются при старте). Нужна бесплатная база, которая не истекает,
например [Neon](https://neon.tech) — вставьте её строку подключения в `DATABASE_URL`
(`sslmode`/`channel_binding` поддерживаются). Ограничения Free: 512 МБ памяти (один браузер
Service Engine), засыпание после 15 минут без запросов и ~1 минута на пробуждение,
файлы до 20 МБ, 300 МБ на пользователя (бесплатная база Neon — 0.5 ГБ).

**Android** — `cd android && ./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`.
GitHub Actions собирает APK на каждый push; тег `v*` создаёт релиз с APK, архивом исходников
и образами в GHCR (подпись — из секретов `MUR_KEYSTORE_*`).

Основные переменные: `MUR_ENV=production`, `MUR_SECRET_KEY` (≥32 символов), `DATABASE_URL`,
`MUR_COOKIE_SECURE=1`, `MUR_DATA_DIR`, `MUR_STORAGE=local|s3|database` (+ `MUR_S3_*`, `AWS_*`), `MUR_LOW_MEMORY`,
`MUR_REGISTRATION_OPEN`, `MUR_AI_PROVIDER/KEY/MODEL`, `MUR_PC_ENABLED`, `MUR_MAX_UPLOAD_MB`,
`MUR_USER_QUOTA_MB`, `MUR_RATE_LIMIT`. Полный список — `backend/app/config.py`, `.env.example`.

## Тесты

| Набор | Команда | Что проверяет |
|---|---|---|
| MUSYA Runtime | `pytest musya-runtime/tests` | лексер, парсер, интерпретатор, песочница, лимиты, импорты, манифесты |
| Service Engine | `pytest service-engine/tests` | настоящий Chromium: load/search/click/type/press/scroll/wait/screenshot/download, вкладки, очередь, отмена, тайм-ауты, SSRF, разрешения, `.mysya` → браузер |
| MUR AI | `pytest mur-ai/tests` | агент строит сайт, запускает сервер, открывает, кликает, делает скриншот; подтверждения; режимы; отказы; формат запросов к API Anthropic/OpenAI |
| Backend | `cd backend && pytest` | авторизация, CSRF, блокировка, проекты, запуск, терминал, WebSocket, Drive и изоляция, превью, ключи, ИИ-сценарий с подтверждениями, пакеты, rate limit, перезапуск без потери данных, S3 (moto), MUR PC в Docker |
| Backend на PostgreSQL | `MUR_TEST_DATABASE_URL=postgresql://… pytest tests/test_api.py` | то же на PostgreSQL |
| Frontend | `cd frontend && npm run typecheck && npm test && npm run build` | типы, логика игр/утилит, сборка |
| E2E (браузер) | `MUR_E2E_URL=http://127.0.0.1:8000 pytest e2e` (сервер с `MUR_AI_ALLOW_SCRIPTED=1`) | критерии готовности через настоящий UI |
| Android | `cd android && ./gradlew assembleDebug testDebugUnitTest lintDebug` | сборка APK, unit-тесты, lint |

Для e2e-теста ИИ используется помеченный «Test double (scripted, not an AI)» провайдер,
включаемый только флагом `MUR_AI_ALLOW_SCRIPTED=1`: он заменяет модель фиксированным
сценарием, а все инструменты (файлы, сервер, Chromium, скриншоты, подтверждения) работают
по-настоящему. В production он недоступен.

## Ограничения (честно)

* **MUR PC** требует сервер с Docker (VPS); на Render/PaaS недоступен. Доступ backend к Docker
  socket эквивалентен root на хосте — используйте выделенную машину.
* **MUR AI** работает только с вашим ключом провайдера (или ключом оператора); оплата по ключу.
* **Android APK** собран и проверен (unit-тесты, lint, aapt), но в этой среде нет эмулятора —
  на устройстве не запускался. Release-APK без секретов подписи — неподписанный.
* Rate limiter и очередь Service Engine живут в памяти одного процесса — для нескольких
  инстансов нужен Redis/sticky sessions; запускайте один backend-процесс.
* Схема БД создаётся автоматически (`create_all`), миграций Alembic пока нет.
* SSRF-защита проверяет DNS на каждый запрос; против DNS-rebinding в строгих средах добавьте
  сетевую изоляцию (см. [docs/SECURITY.md](docs/SECURITY.md)).
* Терминал — MUSYA REPL, а не системный shell (shell есть только внутри MUR PC).
* MUR Gaming содержит только 3 браузерные игры + SGT Puzzles внутри MUR PC; AAA-игры не поддерживаются.
* Проекты Studio хранятся на постоянном диске (`MUR_DATA_DIR`); без диска (Render Free) — копией в PostgreSQL (`MUR_STORAGE=database`, файлы до 5 МБ, до 50 МБ на проект).

## Структура

```
musya-os/
├── musya-runtime/         MUSYA: musya_runtime/{lexer,parser,interpreter}, manifest, CLI, tests/
├── service-engine/        service_engine/{browser,commands,events,permissions}, engine, host, preview, tests/
├── mur-ai/                mur_ai/{providers,agents,tools,safety}, tests/
├── backend/               FastAPI app (routers, services, db, security, storage), tests/
├── frontend/              React desktop + все приложения
├── android/               Android-клиент (Gradle)
├── remote-desktop/        образ MUR PC (Debian + XFCE + VNC)
├── packages/              каталог драйверов/пакетов (.driver, app.yml)
├── examples/              hello.mysya, browser.mysya, app.yml, browser.driver
├── e2e/                   браузерные e2e-тесты критериев готовности
├── docs/                  MUSYA.md, API.md, SECURITY.md
├── deploy/Caddyfile
├── Dockerfile · docker-compose.yml · docker-compose.pc.yml · render.yaml · render-free.yaml · .env.example
└── .github/workflows/     ci.yml, release.yml
```

Python-пакеты лежат в `<папка>/<пакет>/…` (например `musya-runtime/musya_runtime/lexer/`),
потому что имена с дефисом нельзя импортировать. Файлы `Commands.dat`, `murx.yml`, `okak.html`
— исходные наброски проекта, сохранены без изменений.

---

Муся — пользователь · Красавчик — помощник · Артём — разработчик ARTYONIX STUDIOS. Мур! 🐈
