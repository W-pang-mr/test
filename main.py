import base64
import os
import sqlite3
from datetime import datetime

import requests

TELEGRAM_TOKEN = os.environ["BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
DB_PATH = os.getenv("DB_PATH", "meme_bot.db")


def setup_database():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS participants (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT,
            display_name TEXT NOT NULL,
            consented INTEGER NOT NULL DEFAULT 0,
            joined_at TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db.commit()
    return db


def telegram_post(method, data=None, files=None):
    return requests.post(
        f"{TELEGRAM_API}/{method}",
        data=data,
        files=files,
        timeout=60,
    )


def send_message(chat_id, text, reply_to=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    telegram_post("sendMessage", data=data)


def send_photo(chat_id, image_bytes, caption):
    telegram_post(
        "sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1024]},
        files={"photo": ("meme.png", image_bytes, "image/png")},
    )


def user_info(message):
    user = message.get("from", {})
    user_id = user.get("id")
    username = user.get("username")
    display_name = user.get("first_name") or username or str(user_id)
    return user_id, username, display_name


def is_group(message):
    return message.get("chat", {}).get("type") in ("group", "supergroup")


def save_consent(db, chat_id, user_id, username, display_name):
    db.execute(
        """
        INSERT INTO participants
            (chat_id, user_id, username, display_name, consented, joined_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            username = excluded.username,
            display_name = excluded.display_name,
            consented = 1,
            joined_at = excluded.joined_at
        """,
        (chat_id, user_id, username, display_name, datetime.utcnow().isoformat()),
    )
    db.commit()


def forget_user(db, chat_id, user_id):
    db.execute("DELETE FROM messages WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    db.execute("DELETE FROM participants WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    db.commit()


def has_consent(db, chat_id, user_id):
    row = db.execute(
        "SELECT consented FROM participants WHERE chat_id = ? AND user_id = ?",
        (chat_id, user_id),
    ).fetchone()
    return bool(row and row[0])


def store_message(db, chat_id, user_id, text):
    if not has_consent(db, chat_id, user_id):
        return
    clean_text = text.strip()[:500]
    if not clean_text or clean_text.startswith("/"):
        return
    db.execute(
        "INSERT INTO messages (chat_id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, user_id, clean_text, datetime.utcnow().isoformat()),
    )
    db.execute(
        """
        DELETE FROM messages
        WHERE chat_id = ? AND user_id = ? AND id NOT IN (
            SELECT id FROM messages
            WHERE chat_id = ? AND user_id = ?
            ORDER BY id DESC LIMIT 40
        )
        """,
        (chat_id, user_id, chat_id, user_id),
    )
    db.commit()


def find_target(db, message, command_parts):
    reply = message.get("reply_to_message")
    if reply and reply.get("from", {}).get("id"):
        target = reply["from"]
        return target["id"], target.get("username") or target.get("first_name") or "این کاربر"

    if len(command_parts) < 2:
        return None, None
    username = command_parts[1].lstrip("@").lower()
    row = db.execute(
        "SELECT user_id, display_name FROM participants "
        "WHERE chat_id = ? AND LOWER(username) = ? AND consented = 1",
        (message["chat"]["id"], username),
    ).fetchone()
    return row if row else (None, None)


def get_messages(db, chat_id, user_id):
    rows = db.execute(
        "SELECT text FROM messages WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT 40",
        (chat_id, user_id),
    ).fetchall()
    return [row[0] for row in reversed(rows)]


def generate_caption(display_name, messages):
    if not OPENAI_API_KEY:
        return None
    examples = "\n".join(f"- {text}" for text in messages)
    prompt = (
        "You write playful, friendly Persian meme captions for a group chat. "
        "Use only the communication style and harmless recurring jokes in the examples. "
        "Never infer or mention health, politics, religion, ethnicity, sexuality, finances, "
        "private relationships, or other sensitive traits. Do not insult or target protected traits. "
        "Make one short witty caption in Persian, maximum two lines, suitable for a consenting adult.\n\n"
        f"Person name: {display_name}\nRecent opt-in messages:\n{examples}"
    )
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": TEXT_MODEL,
            "temperature": 0.9,
            "max_tokens": 120,
            "messages": [
                {"role": "system", "content": "Return only the final Persian meme caption."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def generate_image(caption):
    if not OPENAI_API_KEY:
        return None
    prompt = (
        "Create a colorful, friendly cartoon meme image with no written words and no real person's face. "
        "Use a generic fictional character and visual humor inspired by this Persian caption. "
        "Keep it light, non-harassing, and suitable for a group chat.\n\n"
        f"Caption idea: {caption}"
    )
    response = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": IMAGE_MODEL, "prompt": prompt, "size": "1024x1024", "n": 1},
        timeout=120,
    )
    response.raise_for_status()
    item = response.json()["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        image = requests.get(item["url"], timeout=60)
        image.raise_for_status()
        return image.content
    return None


def create_meme(db, message, command_parts):
    chat_id = message["chat"]["id"]
    target_id, display_name = find_target(db, message, command_parts)
    if not target_id:
        send_message(
            chat_id,
            "برای انتخاب شخص، روی یکی از پیام‌های او ریپلای کن و /meme بفرست؛ "
            "یا بنویس /meme @username.\n\n"
            "آن شخص باید قبلاً /join را زده باشد.",
            message.get("message_id"),
        )
        return
    if not has_consent(db, chat_id, target_id):
        send_message(chat_id, "این شخص هنوز برای ساخت میم رضایت نداده است.", message.get("message_id"))
        return

    messages = get_messages(db, chat_id, target_id)
    if len(messages) < 3:
        send_message(
            chat_id,
            "برای ساخت میم حداقل ۳ پیام معمولی از این شخص لازم است. بعد از /join کمی در گروه صحبت کند.",
            message.get("message_id"),
        )
        return
    if not OPENAI_API_KEY:
        send_message(
            chat_id,
            "اسکلت ربات آماده است، اما برای تولید کپشن و تصویر باید متغیر OPENAI_API_KEY را در هاست اضافه کنی.",
            message.get("message_id"),
        )
        return

    send_message(chat_id, "⏳ دارم میم را آماده می‌کنم...", message.get("message_id"))
    try:
        caption = generate_caption(display_name, messages)
        image = generate_image(caption)
        if image:
            send_photo(chat_id, image, caption)
        else:
            send_message(chat_id, caption, message.get("message_id"))
    except Exception:
        send_message(
            chat_id,
            "تولید میم این بار موفق نشد؛ دوباره چند لحظه بعد امتحان کن.",
            message.get("message_id"),
        )


def show_help(chat_id):
    send_message(
        chat_id,
        "🎭 ربات میم گروه\n\n"
        "/join — رضایت برای ذخیرهٔ پیام‌های معمولی و ساخت میم\n"
        "/forget — حذف رضایت و تمام پیام‌های ذخیره‌شدهٔ تو\n"
        "/meme — روی پیام شخص ریپلای کن و این دستور را بفرست\n\n"
        "فقط اعضایی که خودشان /join را می‌فرستند در این قابلیت قرار می‌گیرند.",
    )


def main():
    db = setup_database()
    offset = 0

    while True:
        response = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        data = response.json()

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message or not message.get("text"):
                continue

            text = message["text"].strip()
            chat_id = message["chat"]["id"]
            user_id, username, display_name = user_info(message)
            command_parts = text.split()

            if text.startswith("/join"):
                if is_group(message):
                    save_consent(db, chat_id, user_id, username, display_name)
                    send_message(
                        chat_id,
                        f"✅ {display_name}، رضایتت ثبت شد. فقط پیام‌های معمولی بعد از این لحظه و حداکثر ۴۰ پیام برای ساخت میم استفاده می‌شوند.",
                        message.get("message_id"),
                    )
                else:
                    send_message(chat_id, "این دستور را داخل گروهی که بات در آن است بفرست.")
                continue

            if text.startswith("/forget"):
                forget_user(db, chat_id, user_id)
                send_message(chat_id, "🗑 رضایت و پیام‌های ذخیره‌شدهٔ تو حذف شد.", message.get("message_id"))
                continue

            if text.startswith("/meme"):
                if is_group(message):
                    create_meme(db, message, command_parts)
                else:
                    send_message(chat_id, "ساخت میم فقط داخل گروه انجام می‌شود.")
                continue

            if text.startswith("/help") or text.startswith("/start"):
                show_help(chat_id)
                continue

            if is_group(message):
                store_message(db, chat_id, user_id, text)


if __name__ == "__main__":
    main()
