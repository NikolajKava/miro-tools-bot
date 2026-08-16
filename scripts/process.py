#!/usr/bin/env python3
"""Прогоняет каждое задание через Claude Code в headless-режиме и отвечает в Telegram."""
import json
import os
import pathlib
import subprocess
import sys
import urllib.parse
import urllib.request

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BOARD = os.environ["MIRO_BOARD_URL"]
API = f"https://api.telegram.org/bot{TOKEN}"
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT_SECONDS", "1800"))

PROMPT = """Разбери видео и добавь найденные инструменты на Miro-доску.

Файл видео: {video}
Ссылка на источник: {source}
Доска: {board}

Действуй по скилу tiktok-tools из .claude/skills/ этого репозитория.

Режим неинтерактивный — вопросов задавать некому:
- ничего не спрашивай, решай сам;
- шаг «показать список до записи» пропусти, пиши сразу на доску;
- если официальный источник инструмента не подтвердился, всё равно добавь карточку
  с пометкой ⚠️ и перечисли кандидатов, но ссылку не выдумывай;
- если файла видео нет, попробуй скачать по ссылке через yt-dlp; не вышло — скажи об этом;
- тяжёлые установки (torch, whisper) не делай, обойдись кадрами и подписью.

В конце выдай короткий отчёт на русском: что добавлено, что пропущено как дубль,
что осталось неподтверждённым. Без markdown-заголовков, это уйдёт в Telegram."""


def send(chat_id, text):
    """Telegram режет сообщения на 4096 символов."""
    for chunk in (text[i:i + 3900] for i in range(0, len(text), 3900)):
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(f"{API}/sendMessage", data=data)
        try:
            urllib.request.urlopen(req, timeout=60).read()
        except Exception as exc:
            print(f"не смог отправить ответ в Telegram: {exc}", file=sys.stderr)


def run_claude(job):
    prompt = PROMPT.format(
        video=job["video"] or "не приложен",
        source=job["source_url"] or "не указана",
        board=BOARD,
    )
    cmd = [
        "claude", "-p", prompt,
        "--permission-mode", "bypassPermissions",
        "--output-format", "json",
        "--mcp-config", "mcp.json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=JOB_TIMEOUT)
    if proc.returncode != 0:
        return f"Claude завершился с ошибкой ({proc.returncode}):\n{proc.stderr[-1500:]}"
    try:
        return json.loads(proc.stdout).get("result", proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout[-3000:]


def main():
    jobs = json.loads(pathlib.Path("work/jobs.json").read_text(encoding="utf-8"))
    if not jobs:
        print("заданий нет")
        return

    for job in jobs:
        chat = job["chat_id"]
        if job["error"]:
            send(chat, f"⚠️ {job['error']}\n\nЕсли видео больше 20 МБ, Telegram не отдаёт его боту — пришли ссылку.")
            continue

        send(chat, "Взял в работу, разбираю видео…")
        try:
            report = run_claude(job)
        except subprocess.TimeoutExpired:
            report = f"Обработка не уложилась в {JOB_TIMEOUT // 60} минут и была прервана."
        except Exception as exc:
            report = f"Сломалось на обработке: {exc}"
        send(chat, f"{report}\n\nДоска: {BOARD}")


if __name__ == "__main__":
    main()
