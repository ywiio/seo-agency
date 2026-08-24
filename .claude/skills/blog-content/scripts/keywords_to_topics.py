#!/usr/bin/env python3
"""
Из ключей — в темы для блога (с анализом пробелов в контенте).

Принимает выгрузки ключевых слов из Яндекс.Вордстат и Google Keyword Planner
(а также любые CSV / Google Sheets), нормализует разные форматы частотности,
склеивает и дедуплицирует, кластеризует в темы и считает суммарный спрос по
каждой теме. Если передать инвентаризацию блога (blog_inventory.json от
analyze_blog.py) — помечает темы как covered / partial / gap.

Выбор тем и написание статьи — НЕ задача скрипта. Скрипт готовит факты;
решает и пишет агент. Python считает — модель думает.

Про источники честно: ни у Вордстата, ни у Google Keyword Planner нет
бесплатного открытого API. Рабочий путь — выгрузки:
  • Вордстат: колонки вида «Запрос» / «Показов в месяц» (или «Частотность»).
  • Google Keyword Planner: «Keyword» + «Avg. monthly searches»
    (рус.: «Ключевое слово» + «Среднее число запросов в месяц», часто диапазон
    «1 тыс. – 10 тыс.» — это поддерживается).
Колонки определяются по заголовкам автоматически.

Зависимости: requests  (gspread — опционально, только для приватных таблиц)

Пример:
  python keywords_to_topics.py \
      --keywords wordstat_export.csv google_kw.csv \
      --blog result/blog_inventory.json \
      --top 40 --out ../result
"""

import argparse
import csv
import glob
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Agency-Topics/1.0)"
TIMEOUT = 20

KEYWORD_HEADERS = ["keyword", "keywords", "ключ", "ключи", "ключевое слово",
                   "ключевые слова", "запрос", "запросы", "фраза", "фразы",
                   "phrase", "query", "kw", "name"]
VOLUME_HEADERS = ["volume", "частотность", "частота", "показов в месяц",
                  "показы", "wordstat", "ws", "avg. monthly searches",
                  "avg monthly searches", "среднее число запросов в месяц",
                  "число запросов", "frequency", "freq", "impressions", "спрос"]

STOPWORDS = set("""
и в во не на с со а но к от о об из за до по для как что это эта этот эти тот
у же ли бы или там тут все всё чем чём при про над под без the a an of to in
on for and or with at by from is are be your you we our how what why best
купить цена цены онлайн отзывы лучший лучшие топ
""".split())

# Опциональная лемматизация (pymorphy3). Если пакета нет — токены как есть.
# Сильно улучшает склейку русских словоформ: кофемашину/кофемашиной → кофемашин.
try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
    _LEMMA_CACHE = {}

    def _lemma(word):
        if word not in _LEMMA_CACHE:
            _LEMMA_CACHE[word] = _MORPH.parse(word)[0].normal_form
        return _LEMMA_CACHE[word]
except Exception:  # noqa: BLE001
    def _lemma(word):
        return word


# ───────────────────────── загрузка источников ─────────────────────────

def parse_sheet_ref(sheet):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet)
    sid = m.group(1) if m else sheet.strip()
    g = re.search(r"[#&?]gid=(\d+)", sheet)
    return sid, (g.group(1) if g else "0")


def _rows_via_gspread(sid, gid):
    import gspread
    creds = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
             or os.environ.get("GSHEETS_CREDENTIALS"))
    if not creds:
        raise RuntimeError("Приватная таблица: задай GOOGLE_APPLICATION_CREDENTIALS "
                           "(JSON сервис-аккаунта) и дай ему доступ к таблице.")
    gc = gspread.service_account(filename=creds)
    sh = gc.open_by_key(sid)
    ws = next((w for w in sh.worksheets() if str(w.id) == str(gid)), sh.sheet1)
    return ws.get_all_records()


def load_one_source(src):
    """CSV-файл, директория с CSV, публичная/приватная Google-таблица."""
    if os.path.isdir(src):
        rows = []
        for f in sorted(glob.glob(os.path.join(src, "*.csv"))):
            rows += load_one_source(f)
        return rows
    if os.path.isfile(src):
        with open(src, encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            return list(csv.DictReader(f, dialect=dialect))
    # Google Sheets
    sid, gid = parse_sheet_ref(src)
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    if r.status_code == 200 and "html" not in r.headers.get("Content-Type", "").lower():
        r.encoding = "utf-8"
        return list(csv.DictReader(io.StringIO(r.text)))
    return _rows_via_gspread(sid, gid)


def detect_col(headers, candidates):
    norm = {h: h.strip().lower() for h in headers}
    for h, low in norm.items():
        if low in candidates:
            return h
    # частичное совпадение (заголовки Google бывают длинными)
    for h, low in norm.items():
        if any(c in low for c in candidates):
            return h
    return None


# ───────────────────────── нормализация частотности ─────────────────────────

def _scale(token):
    t = token.strip().lower()
    mult = 1
    if "млн" in t or re.search(r"\bm\b", t):
        mult = 1_000_000
    elif "тыс" in t or re.search(r"\bk\b", t) or t.endswith("k"):
        mult = 1000
    num = re.search(r"\d+(?:[.,]\d+)?", t)
    if not num:
        return None
    return float(num.group(0).replace(",", ".")) * mult


def parse_volume(val):
    if val is None:
        return None
    s = str(val).strip().replace("\u00a0", " ").replace("\u202f", " ")
    if not s:
        return None
    low = s.lower()
    # Плоское целое с разделителями тысяч: "1 234", "12,345"
    if (not re.search(r"[–—]|\bto\b|\.\.\.|тыс|млн|[km]\b", low)
            and re.fullmatch(r"\d{1,3}(?:[ ,]\d{3})+|\d+", s)):
        return int(re.sub(r"[ ,]", "", s))
    # Диапазон: "1 тыс. – 10 тыс.", "100 - 1000"
    parts = [p for p in re.split(r"\s*(?:–|—|-|\.\.\.|to)\s*", s) if re.search(r"\d", p)]
    if len(parts) == 2:
        a, b = _scale(parts[0]), _scale(parts[1])
        if a is not None and b is not None:
            return int(round((a + b) / 2))
    v = _scale(s)
    return int(round(v)) if v is not None else None


def tokens(text):
    out = []
    for w in re.split(r"[\s/_,.;:!?()«»\"'—\-]+", (text or "").lower()):
        w = w.strip()
        if len(w) > 2 and w not in STOPWORDS and not w.isdigit():
            out.append(_lemma(w))
    return out


# ───────────────────────── сбор и дедуп ключей ─────────────────────────

def collect_keywords(sources):
    merged = {}            # keyword -> {keyword, volume, sources}
    diag = {"sources": [], "rows_total": 0, "without_volume": 0}
    for src in sources:
        try:
            rows = load_one_source(src)
        except Exception as e:  # noqa: BLE001
            diag["sources"].append({"src": src, "error": str(e)})
            continue
        if not rows:
            diag["sources"].append({"src": src, "rows": 0})
            continue
        headers = list(rows[0].keys())
        kw_col = detect_col(headers, KEYWORD_HEADERS)
        vol_col = detect_col(headers, VOLUME_HEADERS)
        diag["sources"].append({"src": src, "rows": len(rows),
                                 "keyword_col": kw_col, "volume_col": vol_col})
        if not kw_col:
            continue
        label = os.path.basename(str(src)) or str(src)
        for row in rows:
            kw = (row.get(kw_col) or "").strip().lower()
            if not kw:
                continue
            vol = parse_volume(row.get(vol_col)) if vol_col else None
            diag["rows_total"] += 1
            if vol is None:
                diag["without_volume"] += 1
            cur = merged.get(kw)
            if cur:
                # дубль в нескольких источниках — берём максимум частотности
                if vol is not None and (cur["volume"] is None or vol > cur["volume"]):
                    cur["volume"] = vol
                cur["sources"].add(label)
            else:
                merged[kw] = {"keyword": kw, "volume": vol, "sources": {label}}
    out = [{"keyword": v["keyword"], "volume": v["volume"],
            "sources": sorted(v["sources"])} for v in merged.values()]
    diag["unique_keywords"] = len(out)
    return out, diag


# ───────────────────────── кластеризация в темы ─────────────────────────

def salient(tok_list, generic):
    cand = [t for t in tok_list if t not in generic]
    return max(cand, key=len) if cand else None


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_topics(keywords, sim):
    items = []
    for k in keywords:
        toks = tokens(k["keyword"])
        if toks:
            items.append((k, set(toks)))

    # Документная частота токенов: «общие» головы (напр. «кофемашина», когда вся
    # выгрузка про кофемашины) не должны схлопывать разные подтемы в один кластер.
    df = defaultdict(int)
    for _, toks in items:
        for t in toks:
            df[t] += 1
    n = len(items)
    generic = {t for t, c in df.items() if n >= 4 and c >= max(3, 0.35 * n)}

    items.sort(key=lambda x: -(x[0]["volume"] or 0))  # высокочастотные = головы
    clusters = []
    for k, toks in items:
        sal = salient(toks, generic)
        best, best_score = None, 0.0
        for c in clusters:
            score = jaccard(toks, c["core"])
            # общий специфичный (не-generic) токен — достаточный повод склеить
            if sal and sal in c["core"] and score < sim:
                score = sim
            if score > best_score:
                best, best_score = c, score
        if best and best_score >= sim:
            best["members"].append(k)
            best["volume"] += (k["volume"] or 0)
        else:
            clusters.append({"head": k["keyword"], "core": set(toks),
                             "members": [k], "volume": k["volume"] or 0})
    return clusters


# ───────────────────────── анализ пробелов ─────────────────────────

def coverage(cluster, blog_articles):
    if not blog_articles:
        return {"status": "unknown", "article": None, "overlap": 0.0}
    core = cluster["core"]
    if not core:
        return {"status": "unknown", "article": None, "overlap": 0.0}
    # Доля токенов темы, покрытых статьёй, а не Jaccard: core (1-3 слова)
    # против словаря статьи (топ-25 терминов) — их объединение почти всегда
    # огромное относительно core, и обычный Jaccard занижает почти до нуля.
    best_art, best_score = None, 0.0
    for art in blog_articles:
        art_tokens = set(art.get("tokens", []))
        score = len(core & art_tokens) / len(core)
        if score > best_score:
            best_score, best_art = score, art
    status = "covered" if best_score >= 0.75 else "partial" if best_score >= 0.4 else "gap"
    return {"status": status,
            "article": best_art.get("url") if best_art else None,
            "overlap": round(best_score, 3)}


# ───────────────────────── main ─────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Ключи → темы для блога + анализ пробелов")
    ap.add_argument("--keywords", nargs="+", required=True,
                    help="CSV/папка/Google Sheets выгрузки из Вордстат и Google KP")
    ap.add_argument("--blog", help="blog_inventory.json от analyze_blog.py (для gap-анализа)")
    ap.add_argument("--top", type=int, default=40, help="Сколько тем вернуть")
    ap.add_argument("--similarity", type=float, default=0.5,
                    help="Порог склейки ключей в тему (0..1)")
    ap.add_argument("--samples", type=int, default=8, help="Ключей-примеров на тему")
    ap.add_argument("--out", default="../result")
    args = ap.parse_args()

    print(f"Источники ключей: {args.keywords}", file=sys.stderr)
    keywords, diag = collect_keywords(args.keywords)
    print(f"  уникальных ключей: {diag['unique_keywords']} "
          f"(без частотности: {diag['without_volume']})", file=sys.stderr)

    blog_articles = []
    if args.blog and os.path.isfile(args.blog):
        with open(args.blog, encoding="utf-8") as f:
            blog_articles = json.load(f).get("articles", [])
        print(f"  статей в блоге: {len(blog_articles)}", file=sys.stderr)

    clusters = cluster_topics(keywords, args.similarity)
    clusters.sort(key=lambda c: -c["volume"])

    topics = []
    for c in clusters[:args.top]:
        members = sorted(c["members"], key=lambda m: -(m["volume"] or 0))
        topics.append({
            "topic": c["head"],
            "total_volume": c["volume"],
            "keyword_count": len(members),
            "sample_keywords": [{"keyword": m["keyword"], "volume": m["volume"]}
                                for m in members[:args.samples]],
            "coverage": coverage(c, blog_articles),
        })

    gaps = [t for t in topics if t["coverage"]["status"] in ("gap", "unknown")]
    summary = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "unique_keywords": diag["unique_keywords"],
        "topics_found": len(clusters),
        "topics_returned": len(topics),
        "gap_topics": len(gaps),
        "sources_diag": diag["sources"],
    }
    payload = {"summary": summary, "topics": topics}

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "topics.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("\nТоп-тем по спросу:", file=sys.stderr)
    for t in topics[:10]:
        cov = t["coverage"]["status"]
        print(f"  [{cov:<7}] {t['total_volume']:>8} — {t['topic']} "
              f"({t['keyword_count']} ключей)", file=sys.stderr)
    print(f"\nJSON: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
