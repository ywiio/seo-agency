#!/usr/bin/env python3
"""Создать или безопасно обновить черновик страницы WordPress."""

import argparse
import json
import sys
from pathlib import Path

from wp_common import WordpressError, api_url, load_wordpress_config, request_json


ALLOWED_FIELDS = {
    "title", "slug", "content", "excerpt", "parent", "featured_media",
    "meta", "template", "menu_order",
}


def load_page(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WordpressError(f"Не удалось прочитать входной JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WordpressError("Входной JSON должен быть объектом")
    if not str(data.get("title", "")).strip():
        raise WordpressError("Обязательное поле title пустое")
    if not str(data.get("content", "")).strip():
        raise WordpressError("Обязательное поле content пустое")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Создание черновика WordPress")
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", required=True, help="JSON с данными страницы")
    parser.add_argument("--page-id", type=int, help="ID существующей страницы-черновика")
    parser.add_argument(
        "--allow-published-update", action="store_true",
        help="Разрешить превратить опубликованную страницу в черновик перед обновлением",
    )
    parser.add_argument("--dry-run", action="store_true", help="Проверить payload без запроса")
    args = parser.parse_args()

    try:
        wp, session = load_wordpress_config(args.config)
        source = load_page(args.input)
        page_id = args.page_id or source.get("id")
        if page_id is not None and (not isinstance(page_id, int) or page_id <= 0):
            raise WordpressError("id страницы должен быть положительным целым числом")

        payload = {key: source[key] for key in ALLOWED_FIELDS if key in source}
        payload.update({"status": "draft", "comment_status": "closed", "ping_status": "closed"})

        if args.dry_run:
            print(json.dumps({
                "dry_run": True,
                "site_url": wp["site_url"],
                "page_id": page_id,
                "payload": payload,
            }, ensure_ascii=False, indent=2))
            return 0

        if page_id:
            existing = request_json(
                session, "GET", api_url(wp, f"pages/{page_id}"), params={"context": "edit"}
            )
            if existing.get("status") not in {"draft", "pending", "auto-draft"}:
                if not args.allow_published_update:
                    raise WordpressError(
                        f"Страница {page_id} имеет статус {existing.get('status')!r}. "
                        "Обновление остановлено; требуется --allow-published-update"
                    )
            endpoint = f"pages/{page_id}"
            action = "updated"
        else:
            endpoint = "pages"
            action = "created"

        page = request_json(session, "POST", api_url(wp, endpoint), json=payload)
        result = {
            "ok": True,
            "action": action,
            "id": page.get("id"),
            "status": page.get("status"),
            "slug": page.get("slug"),
            "link": page.get("link"),
            "edit_link": f"{wp['site_url']}/wp-admin/post.php?post={page.get('id')}&action=edit",
        }
        if result["status"] != "draft":
            raise WordpressError(f"WordPress вернул неожиданный статус: {result['status']}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except WordpressError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

