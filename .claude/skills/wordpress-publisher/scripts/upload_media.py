#!/usr/bin/env python3
"""Загрузить изображение в медиатеку WordPress без публикации страницы."""

import argparse
import json
import mimetypes
import sys
from pathlib import Path

from wp_common import WordpressError, api_url, load_wordpress_config, request_json


ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка изображения в WordPress")
    parser.add_argument("--config", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--alt", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--caption", default="")
    args = parser.parse_args()

    try:
        wp, session = load_wordpress_config(args.config)
        path = Path(args.file)
        if not path.is_file():
            raise WordpressError(f"Файл изображения не найден: {path}")
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in ALLOWED_MIME:
            raise WordpressError(f"Неподдерживаемый тип файла: {mime or 'неизвестен'}")

        with path.open("rb") as stream:
            media = request_json(
                session,
                "POST",
                api_url(wp, "media"),
                files={"file": (path.name, stream, mime)},
                data={
                    "alt_text": args.alt,
                    "title": args.title or path.stem,
                    "caption": args.caption,
                },
            )
        print(json.dumps({
            "ok": True,
            "id": media.get("id"),
            "source_url": media.get("source_url"),
            "edit_link": f"{wp['site_url']}/wp-admin/post.php?post={media.get('id')}&action=edit",
        }, ensure_ascii=False, indent=2))
        return 0
    except WordpressError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

