#!/usr/bin/env python3
"""Забирает новые сообщения у Telegram-бота, скачивает видео, готовит список заданий.

Состояние нигде не хранится: Telegram сам удаляет апдейты, как только мы
подтверждаем их через getUpdates с offset. Подтверждаем только после того,
как файл скачан, иначе сообщение потеряется при падении раннера.
"""
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED = {c.strip() for c in os.environ.get("TELEGRAM_ALLOWED_CHATS", "").split(",") if c.strip()}
API = f"https://api.telegram.org/bot{TOKEN}"
WORK = pathlib.Path("work")
URL_RE = re.compile(r"https?://\S+")


def api(method, **params):
    url = f"{API}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.load(resp)
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description', data)}")
    return data["result"]


def download(file_id, dest):
    """Bot API отдаёт файлы до 20 МБ — для reels хватает с запасом."""
    info = api("getFile", file_id=file_id)
    url = f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}"
    urllib.request.urlretrieve(url, dest)
    return dest


def main():
    WORK.mkdir(exist_ok=True)
    updates = api("getUpdates", timeout=0, allowed_updates=json.dumps(["message", "channel_post"]))

    jobs = []
    last_id = None
    for upd in updates:
        last_id = upd["update_id"]
        msg = upd.get("message") or upd.get("channel_post")
        if not msg:
            continue

        chat = str(msg["chat"]["id"])
        if ALLOWED and chat not in ALLOWED:
            print(f"пропускаю чат {chat}: не в списке разрешённых", file=sys.stderr)
            continue

        text = msg.get("text") or msg.get("caption") or ""
        match = URL_RE.search(text)
        source = match.group(0) if match else ""
        media = msg.get("video") or msg.get("document") or msg.get("animation") or msg.get("video_note")

        job = {
            "chat_id": chat,
            "message_id": msg["message_id"],
            "source_url": source,
            "video": None,
            "error": None,
        }

        if media:
            dest = WORK / f"{upd['update_id']}.mp4"
            try:
                download(media["file_id"], dest)
                job["video"] = str(dest)
            except Exception as exc:
                job["error"] = f"не смог скачать файл из Telegram: {exc}"
        elif not source:
            continue  # ни файла, ни ссылки — обрабатывать нечего

        jobs.append(job)

    # Подтверждаем приём: эти апдейты Telegram больше не отдаст.
    if last_id is not None:
        api("getUpdates", offset=last_id + 1, timeout=0)

    (WORK / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"заданий: {len(jobs)}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"has_jobs={'1' if jobs else '0'}\n")


if __name__ == "__main__":
    main()
