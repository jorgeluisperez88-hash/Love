"""Send restock alerts. Every channel is optional and configured by env vars.

=====================  =====================================================
Channel                Environment variables
=====================  =====================================================
ntfy (phone push)      NTFY_TOPIC  (optional NTFY_SERVER, NTFY_TOKEN)
Discord                DISCORD_WEBHOOK_URL
Slack                  SLACK_WEBHOOK_URL
Telegram               TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Pushover               PUSHOVER_TOKEN, PUSHOVER_USER
Email (SMTP)           SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
                       EMAIL_TO (optional EMAIL_FROM)
=====================  =====================================================
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def _ntfy(title: str, body: str, url: str | None) -> None:
    topic = _env("NTFY_TOPIC")
    if not topic:
        return
    server = (_env("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": "urgent", "Tags": "video_game,rotating_light"}
    if url:
        headers["Click"] = url
        headers["Actions"] = f"view, Open store, {url}"
    if _env("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {_env('NTFY_TOKEN')}"
    requests.post(f"{server}/{topic}", data=body.encode(), headers=headers, timeout=15).raise_for_status()


def _discord(title: str, body: str, url: str | None) -> None:
    hook = _env("DISCORD_WEBHOOK_URL")
    if hook:
        requests.post(hook, json={"content": f"**{title}**\n{body}"[:1990]}, timeout=15).raise_for_status()


def _slack(title: str, body: str, url: str | None) -> None:
    hook = _env("SLACK_WEBHOOK_URL")
    if hook:
        requests.post(hook, json={"text": f"*{title}*\n{body}"}, timeout=15).raise_for_status()


def _telegram(title: str, body: str, url: str | None) -> None:
    token, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if token and chat:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": f"{title}\n{body}", "disable_web_page_preview": False},
            timeout=15,
        ).raise_for_status()


def _pushover(title: str, body: str, url: str | None) -> None:
    token, user = _env("PUSHOVER_TOKEN"), _env("PUSHOVER_USER")
    if token and user:
        data = {"token": token, "user": user, "title": title, "message": body, "priority": 1}
        if url:
            data["url"] = url
        requests.post("https://api.pushover.net/1/messages.json", data=data, timeout=15).raise_for_status()


def _email(title: str, body: str, url: str | None) -> None:
    host, to = _env("SMTP_HOST"), _env("EMAIL_TO")
    if not (host and to):
        return
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = _env("EMAIL_FROM") or _env("SMTP_USER") or "ps5-pro-tracker@localhost"
    msg["To"] = to
    msg.set_content(body)
    port = int(_env("SMTP_PORT") or 587)
    with smtplib.SMTP_SSL(host, port, timeout=20) if port == 465 else smtplib.SMTP(host, port, timeout=20) as s:
        if port != 465:
            s.starttls()
        if _env("SMTP_USER"):
            s.login(_env("SMTP_USER"), _env("SMTP_PASSWORD") or "")
        s.send_message(msg)


CHANNELS = {
    "ntfy": _ntfy,
    "discord": _discord,
    "slack": _slack,
    "telegram": _telegram,
    "pushover": _pushover,
    "email": _email,
}


def configured_channels() -> list[str]:
    checks = {
        "ntfy": ("NTFY_TOPIC",),
        "discord": ("DISCORD_WEBHOOK_URL",),
        "slack": ("SLACK_WEBHOOK_URL",),
        "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
        "pushover": ("PUSHOVER_TOKEN", "PUSHOVER_USER"),
        "email": ("SMTP_HOST", "EMAIL_TO"),
    }
    return [name for name, keys in checks.items() if all(_env(k) for k in keys)]


def send(title: str, body: str, url: str | None = None) -> list[str]:
    """Send to every configured channel. Returns the channels that failed."""
    failed = []
    for name in configured_channels():
        try:
            CHANNELS[name](title, body, url)
            log.info("alert sent via %s", name)
        except Exception as exc:
            log.error("alert via %s failed: %s", name, exc)
            failed.append(name)
    return failed
