#!/usr/bin/env python3
"""Проверить авторизацию и права пользователя WordPress без записи данных."""

import argparse
import json
import sys

from wp_common import WordpressError, api_url, load_wordpress_config, request_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка WordPress REST API")
    parser.add_argument("--config", required=True, help="Путь к projects/<client>/config.json")
    args = parser.parse_args()

    try:
        wp, session = load_wordpress_config(args.config)
        user = request_json(session, "GET", api_url(wp, "users/me"), params={"context": "edit"})
        result = {
            "ok": True,
            "site_url": wp["site_url"],
            "api_url": wp["api_url"],
            "user_id": user.get("id"),
            "username": user.get("slug") or user.get("name"),
            "capabilities": {
                key: bool(user.get("capabilities", {}).get(key))
                for key in ("edit_pages", "publish_pages", "upload_files")
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["capabilities"]["edit_pages"]:
            print("Ошибка: у пользователя нет права edit_pages", file=sys.stderr)
            return 2
        return 0
    except WordpressError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

