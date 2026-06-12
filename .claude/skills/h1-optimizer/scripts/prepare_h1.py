#!/usr/bin/env python3
"""
Подготовка данных для проработки H1.

Делает ДВЕ вещи и кладёт результат в JSON, который потом читает агент:
  1) Анализирует страницу: текущий H1, title, H2, тело текста, диагностика.
  2) Тянет ключевые слова из Google Sheets (или локального CSV) и отбирает
     релевантные именно этой странице.

Написание H1 — НЕ задача этого скрипта. Скрипт собирает факты; формулирует
H1 агент (Claude) по инструкции в SKILL.md. Python парсит — модель думает.

Источник ключей (--sheet) принимает:
  • ссылку на Google Sheets (лист должен быть открыт «всем по ссылке»);
  • голый ID таблицы;
  • путь к локальному .csv (удобно для теста и оффлайна).

Колонки в таблице определяются автоматически по заголовкам (рус/англ):
  ключ:        keyword / ключ / запрос / фраза / phrase / query
  частотность: volume / частотность / wordstat / frequency / impressions
  url:         url / страница / page / адрес      (опционально)
Можно переопределить через --keyword-col / --volume-col / --url-col.

Зависимости: requests, beautifulsoup4

Пример:
  python prepare_h1.py https://site.ru/uslugi/seo \
      --sheet "https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0" \
      --top 15 --out ../result
"""

import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Agency-H1/1.0)"
TIMEOUT = 20

KEYWORD_HEADERS = ["keyword", "keywords", "ключ", "ключи", "ключевое слово",
                   "ключевые слова", "запрос", "запросы", "фраза", "фразы",
                   "phrase", "query", "kw"]
VOLUME_HEADERS = ["volume", "частотность", "частота", "wordstat", "ws",
                  "frequency", "freq", "impressions", "показы", "спрос"]
URL_HEADERS = ["url", "урл", "страница", "page", "адрес", "ссылка", "link"]

# Слова, которые не считаем смысловыми при оценке релевантности
STOPWORDS = set("""
и в во не на с со а но к от о об из за до по для как что это эта этот эти тот
the a an of to in on for and or with at by from is are be your you we our
""".split())


# ───────────────────────── анализ страницы ─────────────────────────

def analyze_page(url: str) -> dict:
    page = {
        "url": url, "final_url": None, "status_code": None,
        "lang": None, "title": None, "meta_description": None,
        "h1_list": [], "h1_count": 0, "h2_list": [],
        "word_count": 0, "text_excerpt": "", "top_terms": [],
        "flags": [], "error": None,
    }
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, allow_redirects=True)
        page["status_code"] = r.status_code
        page["final_url"] = r.url
        if r.status_code >= 400:
            page["flags"].append("page_unreachable")
            return page

        soup = BeautifulSoup(r.text, "html.parser")
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            page["lang"] = html_tag["lang"]

        if soup.title and soup.title.string:
            page["title"] = soup.title.string.strip()
        desc = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
        if desc and desc.get("content"):
            page["meta_description"] = desc["content"].strip()

        page["h1_list"] = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
        page["h1_count"] = len(page["h1_list"])
        page["h2_list"] = [h.get_text(" ", strip=True) for h in soup.find_all("h2")][:15]

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        words = text.split()
        page["word_count"] = len(words)
        page["text_excerpt"] = " ".join(words[:400])

        # Топ значимых терминов — подсказка о реальной теме страницы
        terms = [w.lower().strip(".,:;!?()«»\"'—-") for w in words]
        terms = [w for w in terms if len(w) > 3 and w not in STOPWORDS and not w.isdigit()]
        page["top_terms"] = [t for t, _ in Counter(terms).most_common(20)]

        # Диагностика
        if page["h1_count"] == 0:
            page["flags"].append("no_h1")
        elif page["h1_count"] > 1:
            page["flags"].append("multiple_h1")
        if (page["h1_list"] and page["title"]
                and page["h1_list"][0].strip().lower() == page["title"].strip().lower()):
            page["flags"].append("h1_equals_title")
        if page["word_count"] < 50:
            page["flags"].append("js_heavy_suspected")  # вероятно, контент рисует JS
    except requests.RequestException as e:
        page["error"] = str(e)
        page["flags"].append("page_unreachable")
    return page


# ───────────────────────── ключевые слова ─────────────────────────

def parse_sheet_ref(sheet: str):
    """Из ссылки/ID вытащить (sheet_id, gid)."""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet)
    sheet_id = m.group(1) if m else sheet.strip()
    g = re.search(r"[#&?]gid=(\d+)", sheet)
    gid = g.group(1) if g else "0"
    return sheet_id, gid


def _rows_via_gspread(sheet_id: str, gid: str) -> list:
    """Приватная таблица: читаем через сервис-аккаунт (gspread).

    Нужны: pip install gspread, и переменная окружения
    GOOGLE_APPLICATION_CREDENTIALS (или GSHEETS_CREDENTIALS) с путём к JSON
    ключу сервис-аккаунта, у которого есть доступ к таблице.
    """
    import gspread  # опционально; импорт здесь, чтобы не делать зависимостью
    creds = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
             or os.environ.get("GSHEETS_CREDENTIALS"))
    if not creds:
        raise RuntimeError(
            "Таблица приватная. Либо открой доступ «всем по ссылке», либо "
            "укажи путь к JSON сервис-аккаунта в GOOGLE_APPLICATION_CREDENTIALS "
            "и дай этому аккаунту доступ к таблице.")
    gc = gspread.service_account(filename=creds)
    sh = gc.open_by_key(sheet_id)
    ws = next((w for w in sh.worksheets() if str(w.id) == str(gid)), sh.sheet1)
    return ws.get_all_records()


def load_rows(sheet: str) -> list:
    """Список строк-словарей из локального CSV, публичного или приватного листа."""
    if os.path.isfile(sheet):
        with open(sheet, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    sheet_id, gid = parse_sheet_ref(sheet)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    r = requests.get(csv_url, headers={"User-Agent": USER_AGENT},
                     timeout=TIMEOUT, allow_redirects=True)

    # Публичный лист отдаёт CSV; приватный редиректит на HTML-страницу логина.
    ctype = r.headers.get("Content-Type", "")
    if r.status_code == 200 and "html" not in ctype.lower():
        r.encoding = "utf-8"
        return list(csv.DictReader(io.StringIO(r.text)))

    # Не CSV → пробуем авторизованный путь (приватная таблица)
    try:
        return _rows_via_gspread(sheet_id, gid)
    except ImportError:
        raise RuntimeError(
            "Таблица недоступна по публичной ссылке. Открой доступ «всем по "
            "ссылке», либо `pip install gspread` и настрой сервис-аккаунт "
            "(GOOGLE_APPLICATION_CREDENTIALS).")


def detect_col(headers, candidates, override=None):
    if override:
        for h in headers:
            if h.strip().lower() == override.strip().lower():
                return h
    for h in headers:
        if h.strip().lower() in candidates:
            return h
    return None


def parse_volume(val):
    if val is None:
        return None
    s = re.sub(r"[^\d]", "", str(val))
    return int(s) if s else None


def tokens(text):
    out = []
    for w in re.split(r"[\s/_,.;:!?()«»\"'—-]+", (text or "").lower()):
        w = w.strip()
        if len(w) > 2 and w not in STOPWORDS and not w.isdigit():
            out.append(w)
    return out


def relevance(kw_tokens, page_token_set):
    """Доля токенов ключа, присутствующих на странице (с лёгким учётом морфологии)."""
    if not kw_tokens:
        return 0.0
    hits = 0
    for kt in kw_tokens:
        if kt in page_token_set:
            hits += 1
        elif len(kt) >= 5 and any(pt.startswith(kt[:5]) for pt in page_token_set):
            hits += 0.5
    return hits / len(kw_tokens)


def select_keywords(rows, page, top, overrides):
    result = {"source_rows": len(rows), "matched_by": None,
              "keywords": [], "flags": []}
    if not rows:
        result["flags"].append("sheet_empty")
        return result

    headers = list(rows[0].keys())
    kw_col = detect_col(headers, KEYWORD_HEADERS, overrides.get("keyword"))
    vol_col = detect_col(headers, VOLUME_HEADERS, overrides.get("volume"))
    url_col = detect_col(headers, URL_HEADERS, overrides.get("url"))

    if not kw_col:
        result["flags"].append("keyword_column_not_found")
        result["available_headers"] = headers
        return result
    result["columns"] = {"keyword": kw_col, "volume": vol_col, "url": url_col}

    page_url = (page.get("final_url") or page.get("url") or "").rstrip("/")
    page_path = urlparse(page_url).path.rstrip("/")

    # 1) Если есть колонка URL и она матчит страницу — берём эти строки
    matched = []
    if url_col:
        for row in rows:
            cell = (row.get(url_col) or "").strip().rstrip("/")
            if not cell:
                continue
            if cell == page_url or (page_path and urlparse(cell).path.rstrip("/") == page_path):
                matched.append(row)
        if matched:
            result["matched_by"] = "url_column"

    # 2) Иначе — релевантность к содержимому страницы
    if not matched:
        result["matched_by"] = "content_relevance"
        page_tok = set(tokens(" ".join(
            filter(None, [page.get("title"), " ".join(page.get("h1_list", [])),
                          " ".join(page.get("h2_list", [])), page.get("text_excerpt"),
                          " ".join(page.get("top_terms", []))]))))
        scored = []
        for row in rows:
            kw = (row.get(kw_col) or "").strip()
            if not kw:
                continue
            score = relevance(tokens(kw), page_tok)
            scored.append((score, row))
        scored.sort(key=lambda x: -x[0])
        matched = [row for score, row in scored if score > 0] or [row for _, row in scored]
        relevance_map = {id(row): score for score, row in scored}
    else:
        relevance_map = {}

    out = []
    for row in matched:
        kw = (row.get(kw_col) or "").strip()
        if not kw:
            continue
        out.append({
            "keyword": kw,
            "volume": parse_volume(row.get(vol_col)) if vol_col else None,
            "relevance": round(relevance_map.get(id(row), 1.0), 3),
        })

    # Сортировка зависит от способа отбора:
    #  • url_column — все строки уже релевантны странице → сортируем по частотности;
    #  • content_relevance — релевантность важнее объёма, иначе высокочастотный
    #    но слабо относящийся к теме ключ всплывёт наверх.
    if result["matched_by"] == "content_relevance":
        out.sort(key=lambda k: (-(k["relevance"] or 0), -(k["volume"] or 0)))
    else:
        out.sort(key=lambda k: (-(k["volume"] or 0), -(k["relevance"] or 0)))
    result["keywords"] = out[:top]
    if not result["keywords"]:
        result["flags"].append("no_keywords_matched")
    return result


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Подготовка данных для проработки H1")
    ap.add_argument("url", help="URL страницы")
    ap.add_argument("--sheet", required=True,
                    help="Ссылка на Google Sheets, ID таблицы или путь к .csv")
    ap.add_argument("--top", type=int, default=15, help="Сколько ключей отобрать")
    ap.add_argument("--keyword-col", help="Имя колонки с ключами (если автоопределение промахнулось)")
    ap.add_argument("--volume-col", help="Имя колонки с частотностью")
    ap.add_argument("--url-col", help="Имя колонки с URL страницы")
    ap.add_argument("--out", default="../result", help="Папка для JSON")
    args = ap.parse_args()

    url = args.url if args.url.startswith("http") else "https://" + args.url
    print(f"Анализ страницы: {url}", file=sys.stderr)
    page = analyze_page(url)

    print(f"Ключи из: {args.sheet}", file=sys.stderr)
    keywords = {"source_rows": 0, "keywords": [], "flags": []}
    try:
        rows = load_rows(args.sheet)
        keywords = select_keywords(rows, page, args.top, {
            "keyword": args.keyword_col, "volume": args.volume_col, "url": args.url_col})
    except requests.RequestException as e:
        keywords["flags"].append("sheet_unreachable")
        keywords["error"] = str(e)
        print(f"  ! не удалось прочитать таблицу: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        keywords["flags"].append("sheet_parse_error")
        keywords["error"] = str(e)
        print(f"  ! ошибка разбора таблицы: {e}", file=sys.stderr)

    payload = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "page": page,
        "keywords": keywords,
    }
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "h1_input.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\nГотово.", file=sys.stderr)  # noqa: F541
    print(f"  H1 сейчас: {page['h1_list'] or '— нет —'}", file=sys.stderr)
    print(f"  title:     {page['title']}", file=sys.stderr)
    print(f"  флаги:     {page['flags'] + keywords.get('flags', [])}", file=sys.stderr)
    print(f"  ключей:    {len(keywords['keywords'])} "
          f"(matched_by={keywords.get('matched_by')})", file=sys.stderr)
    print(f"  JSON:      {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
