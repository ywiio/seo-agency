#!/usr/bin/env python3
"""
Инвентаризация блога: какие статьи уже есть.

Краулит раздел блога (в пределах пути стартового URL), собирает по каждой
статье URL, заголовок, H1, дату публикации (если размечена) и значимые
термины. Результат нужен keywords_to_topics.py для анализа пробелов и агенту
для предложения внутренних ссылок.

Зависимости: requests, beautifulsoup4

Пример:
  python analyze_blog.py https://site.ru/blog/ --max-pages 200 --out ../result
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; SEO-Agency-Blog/1.0)"
TIMEOUT = 15
STOPWORDS = set("""
и в во не на с со а но к от о об из за до по для как что это эта этот эти тот
у же ли бы или там тут все всё чем чём при про над под без the a an of to in
on for and or with at by from is are be your you we our
""".split())

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


def normalize(url):
    url, _ = urldefrag(url)
    return url.rstrip("/")


def same_domain(url, root):
    return urlparse(url).netloc.lower().lstrip("www.") == root.lstrip("www.")


def page_terms(soup):
    for t in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        t.decompose()
    words = [w.lower().strip(".,:;!?()«»\"'—-")
             for w in soup.get_text(" ", strip=True).split()]
    words = [w for w in words if len(w) > 3 and w not in STOPWORDS and not w.isdigit()]
    words = [_lemma(w) for w in words]
    return [t for t, _ in Counter(words).most_common(25)], len(words)


def published_date(soup):
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content"):
        return meta["content"]
    t = soup.find("time")
    if t and (t.get("datetime") or t.get_text(strip=True)):
        return t.get("datetime") or t.get_text(strip=True)
    return None


def crawl(start, max_pages, delay):
    root = urlparse(start).netloc.lower()
    base_path = urlparse(start).path.rstrip("/") or "/"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    seen = {normalize(start)}
    queue = deque([normalize(start)])
    articles, diag = [], {"crawled": 0, "skipped_nonarticle": 0}

    while queue and len(articles) < max_pages:
        url = queue.popleft()
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            diag["crawled"] += 1
            if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", ""):
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            # собрать ссылки для обхода (только внутри раздела блога)
            for a in soup.find_all("a", href=True):
                href = normalize(urljoin(url, a["href"]))
                if (href.startswith("http") and same_domain(href, root)
                        and base_path in urlparse(href).path and href not in seen):
                    seen.add(href)
                    queue.append(href)

            h1 = soup.find("h1")
            terms, wc = page_terms(BeautifulSoup(r.text, "html.parser"))
            # эвристика «это статья»: есть H1 и достаточно текста
            if not h1 or wc < 200:
                diag["skipped_nonarticle"] += 1
                continue
            title = soup.title.string.strip() if soup.title and soup.title.string else None
            articles.append({
                "url": r.url,
                "title": title,
                "h1": h1.get_text(" ", strip=True),
                "published": published_date(soup),
                "word_count": wc,
                "tokens": terms,
            })
            print(f"  [{len(articles):>3}] {h1.get_text(strip=True)[:70]}", file=sys.stderr)
            time.sleep(delay)
        except requests.RequestException:
            continue
    return articles, diag


def main():
    ap = argparse.ArgumentParser(description="Инвентаризация существующих статей блога")
    ap.add_argument("url", help="URL раздела блога, напр. https://site.ru/blog/")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--out", default="../result")
    args = ap.parse_args()

    start = args.url if args.url.startswith("http") else "https://" + args.url
    print(f"Инвентаризация блога: {start}", file=sys.stderr)
    articles, diag = crawl(start, args.max_pages, args.delay)

    payload = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "blog_url": start,
        "articles_found": len(articles),
        "diagnostics": diag,
        "articles": articles,
    }
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "blog_inventory.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nНайдено статей: {len(articles)}\nJSON: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
