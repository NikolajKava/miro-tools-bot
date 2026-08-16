#!/usr/bin/env python3
"""Запись инструментов на доску Miro через REST API.

MCP-сервер Miro не принимает токены приложений (403), а приватный docs API
закрыт белым списком, поэтому каталог живёт на карточках: их REST создаёт
и удаляет без ограничений.

Использование:
    python scripts/miro_card.py list
    python scripts/miro_card.py add --title "Stop Slop" --category "Тексты" \
        --purpose "Убирает AI-запах из текста." \
        --install "git clone ... в .claude/skills/" \
        --link https://github.com/hardikpandya/stop-slop \
        --source https://www.instagram.com/reel/DagHQBPh5FG/
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.miro.com/v2"

# Цвет карточки = категория, чтобы доска читалась с одного взгляда.
COLORS = {
    "Маркетинг": "#F24726",
    "Тексты": "#FAC710",
    "Дизайн": "#8FD14F",
    "Видео": "#2D9BF0",
    "Контекст и токены": "#9510AC",
    "CLI": "#6881FF",
    "MCP": "#FF9D48",
}
DEFAULT_COLOR = "#D5D5D5"

# Сетка: 4 ячейки в ряд, в ячейке скриншот сверху и карточка под ним.
CARD_WIDTH = 320
SHOT_WIDTH = 240
GAP = 24               # между картинкой и карточкой
ORIGIN_X, ORIGIN_Y = -570, 0
STEP_X, STEP_Y = 380, 620
PER_ROW = 4


def board_id():
    url = os.environ["MIRO_BOARD_URL"]
    match = re.search(r"/board/([^/?]+)", url)
    if not match:
        raise SystemExit(f"не разобрал URL доски: {url}")
    return urllib.parse.quote(match.group(1), safe="")


def request(method, path, payload=None):
    token = os.environ["MIRO_TOKEN"]
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Miro {method} {path} → {exc.code}: {exc.read().decode()[:400]}")


def list_cards():
    """Все карточки доски — нужны для дедупликации и для выбора места под новую."""
    cards, cursor = [], None
    while True:
        query = "?limit=50" + (f"&cursor={cursor}" if cursor else "")
        page = request("GET", f"/boards/{board_id()}/items{query}&type=card")
        for item in page.get("data", []):
            cards.append({"id": item["id"], "title": item["data"].get("title", "")})
        cursor = page.get("cursor")
        if not cursor:
            return cards


def build_description(category, purpose, install, link, docs, source):
    """Карточка Miro принимает ограниченный HTML: <p>, <strong>, <a>."""
    parts = [f"<p><strong>{html.escape(category)}</strong></p>", f"<p>{html.escape(purpose)}</p>"]
    if install:
        parts.append(f"<p>Установка: {html.escape(install)}</p>")
    if link:
        parts.append(f'<p><a href="{html.escape(link)}">{html.escape(link)}</a></p>')
    if docs:
        parts.append(f'<p>Документация: <a href="{html.escape(docs)}">{html.escape(docs)}</a></p>')
    if source:
        parts.append(f'<p>Источник: <a href="{html.escape(source)}">видео</a></p>')
    return "".join(parts)


def upload_image(path, title, x, y):
    """Загружает кадр из видео как image-объект. Multipart, без urllib — через curl."""
    data = json.dumps({"title": title, "position": {"x": x, "y": y}, "geometry": {"width": SHOT_WIDTH}})
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST", f"{API}/boards/{board_id()}/images",
            "-H", f"Authorization: Bearer {os.environ['MIRO_TOKEN']}",
            "-F", f"resource=@{path}",
            "-F", f"data={data};type=application/json",
        ],
        capture_output=True, text=True, check=True,
    )
    created = json.loads(result.stdout)
    if "id" not in created:
        raise SystemExit(f"загрузка картинки не удалась: {result.stdout[:400]}")
    return created


def add(args):
    existing = list_cards()
    if any(c["title"].strip().lower() == args.title.strip().lower() for c in existing):
        print(f"дубль, пропускаю: {args.title}")
        return

    index = len(existing)
    x = ORIGIN_X + (index % PER_ROW) * STEP_X
    cell_top = ORIGIN_Y + (index // PER_ROW) * STEP_Y
    card_y = cell_top

    # Скриншот занимает верх ячейки, карточка встаёт под него.
    # Высота картинки зависит от пропорций кадра, поэтому читаем её из ответа.
    if args.screenshot:
        shot = upload_image(args.screenshot, args.title, x, cell_top)
        card_y = cell_top + shot["geometry"]["height"] / 2 + GAP + 45

    payload = {
        "data": {
            "title": args.title,
            "description": build_description(
                args.category, args.purpose, args.install, args.link, args.docs, args.source
            ),
        },
        "style": {"cardTheme": COLORS.get(args.category, DEFAULT_COLOR)},
        "position": {"x": x, "y": card_y},
        "geometry": {"width": CARD_WIDTH},
    }
    created = request("POST", f"/boards/{board_id()}/cards", payload)

    # При создании Miro иногда сдвигает карточку по своему усмотрению,
    # а PATCH позицию уважает — поэтому доводим сетку вторым запросом.
    if created.get("position", {}).get("y") != payload["position"]["y"]:
        request("PATCH", f"/boards/{board_id()}/cards/{created['id']}", {"position": payload["position"]})

    print(f"добавлено: {args.title} (id {created['id']})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="показать карточки, которые уже на доске")

    p = sub.add_parser("add", help="добавить карточку инструмента")
    p.add_argument("--title", required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--purpose", required=True)
    p.add_argument("--install", default="")
    p.add_argument("--link", default="")
    p.add_argument("--docs", default="")
    p.add_argument("--source", default="")
    p.add_argument("--screenshot", default="", help="кадр из видео, который ляжет над карточкой")

    args = parser.parse_args()
    if args.cmd == "list":
        json.dump(list_cards(), sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        add(args)


if __name__ == "__main__":
    main()
