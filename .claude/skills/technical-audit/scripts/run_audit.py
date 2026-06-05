#!/usr/bin/env python3
"""
Технический SEO-аудит сайта.

Краулит сайт в пределах одного домена (BFS, с ограничением по числу страниц)
и собирает по каждой странице базовые технические сигналы: коды ответа,
title/description, H1, canonical, индексируемость, alt у картинок, битые
внутренние ссылки и время ответа.

Тяжёлую работу делает этот скрипт. Агент (Claude) потом читает result/audit.json,
приоритизирует проблемы по влиянию на трафик и пишет отчёт. Не гоняем дорогую
модель на парсинг — она думает, Python парсит.

Зависимости: requests, beautifulsoup4  (см. requirements.txt в корне репо)

Запуск:
    python run_audit.py https://site.ru --max-pages 100 --out ../result
"""

import argparse
import csv
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Agency-Audit/1.0)"
TIMEOUT = 15


def normalize(url: str) -> str:
    """Убираем якорь и нормализуем URL для дедупликации."""
    url, _ = urldefrag(url)
    return url.rstrip("/")


def same_domain(url: str, root_netloc: str) -> bool:
    netloc = urlparse(url).netloc.lower().lstrip("www.")
    return netloc == root_netloc.lstrip("www.")


def analyze_page(url: str, session: requests.Session) -> dict:
    """Скачать одну страницу и вытащить технические сигналы + список ссылок."""
    rec = {
        "url": url,
        "status_code": None,
        "final_url": None,
        "redirected": False,
        "response_time_ms": None,
        "content_type": None,
        "indexable": None,
        "title": None,
        "title_len": 0,
        "meta_description": None,
        "desc_len": 0,
        "h1_count": 0,
        "h1_first": None,
        "canonical": None,
        "canonical_self": None,
        "word_count": 0,
        "images_total": 0,
        "images_no_alt": 0,
        "internal_links": 0,
        "external_links": 0,
        "issues": [],
        "error": None,
    }
    links = []
    try:
        t0 = time.perf_counter()
        resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        rec["response_time_ms"] = round((time.perf_counter() - t0) * 1000)
        rec["status_code"] = resp.status_code
        rec["final_url"] = resp.url
        rec["redirected"] = normalize(resp.url) != normalize(url)
        rec["content_type"] = resp.headers.get("Content-Type", "").split(";")[0]

        if rec["status_code"] >= 400:
            rec["issues"].append(f"HTTP {rec['status_code']}")
            return rec, links
        if "html" not in rec["content_type"]:
            return rec, links

        soup = BeautifulSoup(resp.text, "html.parser")

        # Индексируемость
        robots = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "robots"})
        noindex = bool(robots and "noindex" in robots.get("content", "").lower())
        rec["indexable"] = not noindex
        if noindex:
            rec["issues"].append("noindex в meta robots")

        # Title
        if soup.title and soup.title.string:
            rec["title"] = soup.title.string.strip()
            rec["title_len"] = len(rec["title"])
            if rec["title_len"] < 30:
                rec["issues"].append(f"короткий title ({rec['title_len']})")
            elif rec["title_len"] > 60:
                rec["issues"].append(f"длинный title ({rec['title_len']})")
        else:
            rec["issues"].append("нет title")

        # Description
        desc = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "description"})
        if desc and desc.get("content"):
            rec["meta_description"] = desc["content"].strip()
            rec["desc_len"] = len(rec["meta_description"])
            if rec["desc_len"] > 160:
                rec["issues"].append(f"длинный description ({rec['desc_len']})")
        else:
            rec["issues"].append("нет meta description")

        # H1
        h1s = soup.find_all("h1")
        rec["h1_count"] = len(h1s)
        if h1s:
            rec["h1_first"] = h1s[0].get_text(strip=True)[:120]
        if rec["h1_count"] == 0:
            rec["issues"].append("нет H1")
        elif rec["h1_count"] > 1:
            rec["issues"].append(f"несколько H1 ({rec['h1_count']})")

        # Canonical
        can = soup.find("link", attrs={"rel": lambda v: v and "canonical" in [x.lower() for x in v]})
        if can and can.get("href"):
            rec["canonical"] = urljoin(url, can["href"])
            rec["canonical_self"] = normalize(rec["canonical"]) == normalize(rec["final_url"])
        else:
            rec["issues"].append("нет canonical")

        # Текст
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        rec["word_count"] = len(soup.get_text(" ", strip=True).split())
        if rec["word_count"] < 200:
            rec["issues"].append(f"мало текста ({rec['word_count']} слов)")

        # Картинки без alt
        imgs = soup.find_all("img")
        rec["images_total"] = len(imgs)
        rec["images_no_alt"] = sum(1 for i in imgs if not i.get("alt", "").strip())
        if rec["images_no_alt"]:
            rec["issues"].append(f"{rec['images_no_alt']} img без alt")

        # Ссылки
        root_netloc = urlparse(url).netloc.lower()
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if not href.startswith(("http://", "https://")):
                continue
            if same_domain(href, root_netloc):
                rec["internal_links"] += 1
                links.append(normalize(href))
            else:
                rec["external_links"] += 1

        if rec["response_time_ms"] > 1500:
            rec["issues"].append(f"медленный ответ ({rec['response_time_ms']} мс)")

    except requests.RequestException as e:
        rec["error"] = str(e)
        rec["issues"].append("ошибка запроса")
    return rec, links


def crawl(start_url: str, max_pages: int, delay: float) -> list:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    root_netloc = urlparse(start_url).netloc.lower()

    seen = {normalize(start_url)}
    queue = deque([normalize(start_url)])
    results = []

    while queue and len(results) < max_pages:
        url = queue.popleft()
        rec, links = analyze_page(url, session)
        results.append(rec)
        print(f"  [{len(results):>3}/{max_pages}] {rec['status_code']} "
              f"{len(rec['issues'])} проблем — {url}", file=sys.stderr)
        for link in links:
            if link not in seen and same_domain(link, root_netloc):
                seen.add(link)
                queue.append(link)
        time.sleep(delay)
    return results


def write_outputs(results: list, out_dir: str, start_url: str) -> dict:
    import os
    os.makedirs(out_dir, exist_ok=True)

    issue_counts = {}
    for r in results:
        for iss in r["issues"]:
            key = iss.split(" (")[0]
            issue_counts[key] = issue_counts.get(key, 0) + 1

    summary = {
        "site": start_url,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "pages_crawled": len(results),
        "pages_with_issues": sum(1 for r in results if r["issues"]),
        "issue_breakdown": dict(sorted(issue_counts.items(), key=lambda x: -x[1])),
    }
    payload = {"summary": summary, "pages": results}

    json_path = os.path.join(out_dir, "audit.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    csv_path = os.path.join(out_dir, "audit.csv")
    cols = ["url", "status_code", "indexable", "title_len", "desc_len",
            "h1_count", "word_count", "images_no_alt", "response_time_ms",
            "issues"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in results:
            row = [r.get(c) for c in cols[:-1]] + ["; ".join(r["issues"])]
            w.writerow(row)

    return {"json": json_path, "csv": csv_path, "summary": summary}


def main():
    ap = argparse.ArgumentParser(description="Технический SEO-аудит сайта")
    ap.add_argument("url", help="Стартовый URL, напр. https://site.ru")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--delay", type=float, default=0.3, help="Пауза между запросами, сек")
    ap.add_argument("--out", default="../result", help="Папка для отчётов")
    args = ap.parse_args()

    start = args.url if args.url.startswith("http") else "https://" + args.url
    print(f"Аудит {start} (до {args.max_pages} страниц)...", file=sys.stderr)
    results = crawl(start, args.max_pages, args.delay)
    out = write_outputs(results, args.out, start)

    s = out["summary"]
    print(f"\nГотово. Страниц: {s['pages_crawled']}, с проблемами: "
          f"{s['pages_with_issues']}", file=sys.stderr)
    print(f"Топ проблем: {s['issue_breakdown']}", file=sys.stderr)
    print(f"JSON: {out['json']}\nCSV:  {out['csv']}", file=sys.stderr)


if __name__ == "__main__":
    main()
