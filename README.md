# SEO-агентство на Claude Code — стартовый скелет

AI-first каркас по мотивам доклада «Автоматизация SEO-агентства».
Архитектура: **skills → projects → agent → data**. Внутри — один полностью
рабочий скилл (`technical-audit`) и всё, чтобы поверх него наращивать
библиотеку.

## Структура

```
seo-agency/
├── CLAUDE.md                       ← правила агентства (агент читает их всегда)
├── requirements.txt
├── .claude/
│   └── skills/
│       ├── technical-audit/        ← рабочий скилл
│       │   ├── SKILL.md            ← инструкция для агента
│       │   ├── scripts/run_audit.py← краулер (вся тяжёлая работа здесь)
│       │   └── result/             ← сюда падают отчёты
│       └── h1-optimizer/           ← рабочий скилл: проработка H1 под ключи
│           ├── SKILL.md            ← 10/10 промпт по H1 + самопроверка
│           ├── scripts/prepare_h1.py ← анализ страницы + ключи из Google Sheets
│           └── result/
│       └── blog-content/           ← рабочий скилл: темы для блога + статья
│           ├── SKILL.md            ← 3 фазы: темы → пробелы → статья
│           ├── scripts/analyze_blog.py      ← инвентаризация статей блога
│           ├── scripts/keywords_to_topics.py← Вордстат+Google → темы + gap-анализ
│           └── result/
│       └── wordpress-publisher/    ← черновики страниц через WordPress REST API
│           ├── SKILL.md
│           └── scripts/
├── projects/
│   └── example-client/
│       ├── config.json             ← домен, цели, доступы клиента
│       └── data/
└── agent/                          ← оркестратор/планировщик (следующий шаг)
```

## Установка (10 минут)

1. Установите Claude Code: см. https://docs.claude.com/en/docs/claude-code/overview
2. Поставьте зависимости скриптов:
   ```bash
   pip install -r requirements.txt
   ```
3. Запустите Claude Code из корня репозитория:
   ```bash
   cd seo-agency
   claude
   ```
   Скиллы из `.claude/skills/` подхватятся автоматически. Скилл
   `technical-audit` доступен и как команда `/technical-audit`, и сработает
   сам, когда вы попросите «проверь сайт».

## Как пользоваться

Просто напишите агенту по-человечески:

> Сделай техаудит https://site.ru, максимум 80 страниц, и дай приоритетный
> план что чинить.

Агент: запустит `run_audit.py` → прочитает `result/audit.json` →
приоритизирует проблемы по влиянию на трафик → положит отчёт в
`result/audit-report.md`.

Скрипт можно гонять и вручную, без агента:

```bash
cd .claude/skills/technical-audit/scripts
python run_audit.py https://site.ru --max-pages 100 --out ../result
```

Что проверяется по каждой странице: код ответа и редиректы, индексируемость
(noindex), title, meta description, H1, canonical, объём текста, alt у
картинок, время ответа, внутренние/внешние ссылки.

## Скилл `h1-optimizer` — проработка H1 под ключи из Google Sheets

> Проработай H1 для https://site.ru/uslugi/seo, ключи в этой таблице:
> https://docs.google.com/spreadsheets/d/.../edit#gid=0

Агент: запустит `prepare_h1.py` (он проанализирует страницу и подтянет ключи
из таблицы) → прочитает `result/h1_input.json` → определит тему и интент,
выберет главный ключ → даст 3–5 вариантов H1, прогонит их через встроенную
самопроверку и порекомендует один → положит отчёт в `result/h1-report.md`.

Источник ключей гибкий: публичная Google-таблица («всем по ссылке»),
приватная (через сервис-аккаунт `gspread`) или локальный `.csv`. Колонки с
ключом/частотностью/URL определяются по заголовкам автоматически (рус/англ).
Вручную:

```bash
cd .claude/skills/h1-optimizer/scripts
python prepare_h1.py https://site.ru/uslugi/seo \
    --sheet "https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0" \
    --top 15 --out ../result
```

## Скилл `blog-content` — темы для блога + написание статьи

> Собери контент-план для блога https://site.ru/blog/, ключи в этих выгрузках:
> wordstat.csv и google_kw.csv. Потом напиши статью под топовую тему-пробел.

Три фазы: (1) `analyze_blog.py` инвентаризирует существующие статьи; (2)
`keywords_to_topics.py` сливает выгрузки из Вордстат и Google Keyword Planner,
кластеризует в темы по спросу и помечает `gap` / `partial` / `covered`
относительно блога; (3) агент выбирает темы и пишет статью по встроенному
брифу с самопроверкой. Источники ключей — CSV, папка с CSV или Google Sheets
(Вордстат и Google форматы частотности понимаются оба, включая диапазоны вида
«1 тыс. – 10 тыс.»).

```bash
cd .claude/skills/blog-content/scripts
python analyze_blog.py https://site.ru/blog/ --max-pages 200 --out ../result
python keywords_to_topics.py --keywords wordstat.csv google_kw.csv \
    --blog ../result/blog_inventory.json --top 40 --out ../result
```

Опционально `pip install pymorphy3` заметно улучшает кластеризацию русских
ключей (склеивает словоформы: кофемашину/кофемашиной → одна тема).

## Принцип оркестрации (главное из доклада)

Дорогая модель **думает**, дешёвая **пишет**, Python **парсит**. Краулер не
обращается к LLM вообще — он отдаёт чистый JSON, а модель тратится только на
анализ и приоритизацию. Так 100 страниц аудита стоят копейки, а не доллары.

## Как добавить следующий скилл

1. Выберите задачу, которая бесит сильнее всего (сбор семантики, мета-теги,
   анализ конкурентов, пресейл).
2. Скопируйте структуру:
   ```bash
   mkdir -p .claude/skills/<name>/scripts .claude/skills/<name>/result
   ```
3. Напишите `SKILL.md`. Главное — `description` с явными триггерами: Claude
   склонен «недотриггерить» скиллы, поэтому перечисляйте, по каким словам и
   ситуациям его звать.
4. Тяжёлую работу — в `scripts/`. Закоммитьте в Git: скилл стал активом.

## Дорожная карта (agent/)

Папка `agent/` пока пустая — это следующий шаг, когда скиллов станет 3–5:

- `orchestrator.py` — принимает задачу, выбирает нужные скиллы.
- `scheduler.py` — запуск по крону (ночной аудит всех проектов).
- вебхук от CRM/доски → «передвинул карточку → запустился скилл».
- `budget_control.py` — лимиты по расходам на API.

Не стройте оркестратор сразу. Сначала 3–5 рабочих скиллов — потом мозг
поверх них.

## Скилл `wordpress-publisher` — черновики в WordPress

Скилл проверяет подключение, создаёт или обновляет только черновики страниц и
загружает изображения в медиатеку через стандартный WordPress REST API.
Настройки сайта и названия переменных окружения лежат в
`projects/<client>/config.json`; реальные доступы — только в локальном `.env`:

```env
KOMFORT3_WP_USERNAME=claude_api
KOMFORT3_WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

Проверка подключения без записи:

```bash
python .claude/skills/wordpress-publisher/scripts/test_connection.py \
  --config projects/komfort3.by/config.json
```

Создание черновика из JSON:

```bash
python .claude/skills/wordpress-publisher/scripts/create_draft.py \
  --config projects/komfort3.by/config.json --input page.json
```

## Безопасность

Реальные ключи (GSC, API) **не коммитьте**. Держите в `.env` или секретах,
ссылайтесь на них из `config.json`. `.gitignore` уже исключает `.env`,
результаты аудитов и клиентские данные.
