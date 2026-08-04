#!/usr/bin/env python3
"""Общие функции для безопасной работы с WordPress REST API."""

import json
import os
from pathlib import Path
from urllib.parse import urljoin

import requests


DEFAULT_TIMEOUT = 20


class WordpressError(RuntimeError):
    """Понятная ошибка конфигурации или WordPress REST API."""


def load_dotenv(path: Path) -> None:
    """Загрузить простой .env без перезаписи уже заданных переменных."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def find_repo_root(config_path: Path) -> Path:
    """Найти корень репозитория по CLAUDE.md; иначе использовать cwd."""
    for candidate in [config_path.resolve().parent, *config_path.resolve().parents]:
        if (candidate / "CLAUDE.md").is_file():
            return candidate
    return Path.cwd()


def load_wordpress_config(config_path: str) -> tuple[dict, requests.Session]:
    path = Path(config_path)
    if not path.is_file():
        raise WordpressError(f"Файл конфигурации не найден: {path}")

    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WordpressError(f"Не удалось прочитать config.json: {exc}") from exc

    wp = project.get("wordpress")
    if not isinstance(wp, dict):
        raise WordpressError("В config.json отсутствует объект wordpress")

    repo_root = find_repo_root(path)
    load_dotenv(repo_root / ".env")

    api_url = str(wp.get("api_url", "")).rstrip("/")
    site_url = str(wp.get("site_url", "")).rstrip("/")
    username_env = wp.get("username_env")
    password_env = wp.get("password_env")
    if not api_url or not site_url or not username_env or not password_env:
        raise WordpressError(
            "wordpress должен содержать site_url, api_url, username_env и password_env"
        )
    if not api_url.startswith("https://") or not site_url.startswith("https://"):
        raise WordpressError("WordPress API разрешён только через HTTPS")

    username = os.getenv(username_env)
    password = os.getenv(password_env)
    if not username or not password:
        raise WordpressError(
            f"Задайте переменные окружения {username_env} и {password_env} в локальном .env"
        )

    session = requests.Session()
    session.auth = (username, password)
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "SEO-Agency-WordPress-Publisher/1.0",
    })
    wp["api_url"] = api_url
    wp["site_url"] = site_url
    return wp, session


def api_url(wp: dict, endpoint: str) -> str:
    return urljoin(wp["api_url"] + "/", endpoint.lstrip("/"))


def request_json(session: requests.Session, method: str, url: str, **kwargs) -> dict:
    try:
        response = session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise WordpressError(f"WordPress недоступен: {exc}") from exc

    try:
        data = response.json()
    except ValueError:
        data = None

    if not response.ok:
        if isinstance(data, dict):
            code = data.get("code", "unknown_error")
            message = data.get("message", "Нет описания")
            raise WordpressError(f"WordPress API: HTTP {response.status_code}, {code}: {message}")
        raise WordpressError(f"WordPress API: HTTP {response.status_code}: {response.text[:300]}")
    if not isinstance(data, dict):
        raise WordpressError("WordPress API вернул неожиданный ответ")
    return data

