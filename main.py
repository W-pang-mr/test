import os
import random
import sqlite3

import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
DB_PATH = os.getenv("DB_PATH", "world_war.db")

COUNTRIES = {
    "اتحاد آفتاب": "ارتش سریع و تهاجمی",
    "جمهوری شمال": "دفاع قدرتمند و منابع بیشتر",
    "امپراتوری شرق": "ارتش متعادل و منظم",
    "ائتلاف غرب": "فناوری پیشرفته و حمله دقیق",
}

MAIN_KEYBOARD = {
    "keyboard": [
        ["🌍 انتخاب کشور", "📊 وضعیت"],
        ["⚔️ حمله", "🏆 رتبه‌بندی"],
        ["ℹ️ راهنما", "🔄 شروع دوباره"],
    ],
    "resize_keyboard": True,
}

COUNTRY_KEYBOARD = {
    "keyboard": [
        ["اتحاد آفتاب", "جمهوری شمال"],
        ["امپراتوری شرق", "ائتلاف غرب"],
        ["🔙 بازگشت"],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}


def setup_database():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            chat_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            country TEXT,
            troops INTEGER NOT NULL DEFAULT 100,
            supplies INTEGER NOT NULL DEFAULT 50,
            score INTEGER NOT NULL DEFAULT 0,
            battles INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.commit()
    return db


def send_message(chat_id, text, reply_markup=None):
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    requests.post(f"{API}/sendMessage", json=data, timeout=10)


def get_player(db, chat_id, username):
    player = db.execute(
        "SELECT chat_id, username, country, troops, supplies, score, battles "
        "FROM players WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()

    if player is None:
        db.execute(
            "INSERT INTO players (chat_id, username) VALUES (?, ?)",
            (chat_id, username),
        )
        db.commit()
        player = db.execute(
            "SELECT chat_id, username, country, troops, supplies, score, battles "
            "FROM players WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    elif player[1] != username:
        db.execute(
            "UPDATE players SET username = ? WHERE chat_id = ?",
            (username, chat_id),
        )
        db.commit()

    return player


def show_status(db, chat_id):
    player = db.execute(
        "SELECT username, country, troops, supplies, score, battles "
        "FROM players WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    country = player[1] or "هنوز انتخاب نشده"
    send_message(
        chat_id,
        "📊 وضعیت فرماندهی\n\n"
        f"فرمانده: {player[0]}\n"
        f"کشور: {country}\n"
        f"🪖 نیروها: {player[2]}\n"
        f"📦 تدارکات: {player[3]}\n"
        f"⭐ امتیاز: {player[4]}\n"
        f"⚔️ نبردها: {player[5]}",
        MAIN_KEYBOARD,
    )


def show_leaderboard(db, chat_id):
    players = db.execute(
        "SELECT username, country, score FROM players "
        "WHERE country IS NOT NULL ORDER BY score DESC, battles DESC LIMIT 10"
    ).fetchall()

    if not players:
        send_message(chat_id, "🏆 هنوز کسی کشوری انتخاب نکرده است.", MAIN_KEYBOARD)
        return

    lines = ["🏆 جدول رتبه‌بندی جهانی\n"]
    for index, (username, country, score) in enumerate(players, start=1):
        lines.append(f"{index}. {username} — {country} — ⭐ {score}")
    send_message(chat_id, "\n".join(lines), MAIN_KEYBOARD)


def attack(db, chat_id):
    player = db.execute(
        "SELECT country, troops, supplies, score, battles FROM players WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()

    if not player[0]:
        send_message(chat_id, "اول از بخش «🌍 انتخاب کشور» یک کشور انتخاب کن.", MAIN_KEYBOARD)
        return
    if player[1] < 10:
        send_message(chat_id, "نیرویت برای حمله کافی نیست. از «🔄 شروع دوباره» استفاده کن.", MAIN_KEYBOARD)
        return
    if player[2] < 5:
        send_message(chat_id, "تدارکات کافی نداری و نمی‌توانی حمله کنی.", MAIN_KEYBOARD)
        return

    enemy_power = random.randint(45, 90)
    attack_power = random.randint(40, 100) + player[1] // 10
    troop_loss = random.randint(4, 14) if attack_power >= enemy_power else random.randint(12, 25)
    new_troops = max(0, player[1] - troop_loss)
    new_supplies = player[2] - 5
    new_score = player[3] + 20 if attack_power >= enemy_power else max(0, player[3] - 5)
    new_battles = player[4] + 1

    db.execute(
        "UPDATE players SET troops = ?, supplies = ?, score = ?, battles = ? WHERE chat_id = ?",
        (new_troops, new_supplies, new_score, new_battles, chat_id),
    )
    db.commit()

    if attack_power >= enemy_power:
        result = (
            "✅ پیروزی!\n"
            f"دشمن را شکست دادی و ۲۰ امتیاز گرفتی.\n"
            f"تلفات نیروهای تو: {troop_loss}"
        )
    else:
        result = (
            "❌ شکست خوردی!\n"
            f"دشمن مقاومت کرد و ۵ امتیاز از دست دادی.\n"
            f"تلفات نیروهای تو: {troop_loss}"
        )

    send_message(
        chat_id,
        f"⚔️ گزارش نبرد\n\n{result}\n\n"
        f"نیروهای باقی‌مانده: {new_troops}\n"
        f"تدارکات باقی‌مانده: {new_supplies}",
        MAIN_KEYBOARD,
    )


def reset_player(db, chat_id):
    db.execute(
        "UPDATE players SET country = NULL, troops = 100, supplies = 50, "
        "score = 0, battles = 0 WHERE chat_id = ?",
        (chat_id,),
    )
    db.commit()
    send_message(
        chat_id,
        "🔄 بازی از اول شروع شد. حالا یک کشور انتخاب کن.",
        MAIN_KEYBOARD,
    )


def main():
    db = setup_database()
    offset = 0

    while True:
        response = requests.get(
            f"{API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        data = response.json()

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message or not message.get("text"):
                continue

            chat_id = message["chat"]["id"]
            user = message.get("from", {})
            username = user.get("username") or user.get("first_name") or str(chat_id)
            text = message["text"]
            get_player(db, chat_id, username)

            if text == "/start":
                send_message(
                    chat_id,
                    "🌍 به بازی جنگ جهانی آزمایشی خوش آمدی!\n\n"
                    "یک کشور خیالی انتخاب کن، ارتشت را بساز و برای گرفتن امتیاز بجنگ.",
                    MAIN_KEYBOARD,
                )
            elif text in ("🌍 انتخاب کشور", "/country"):
                countries = "\n".join(f"• {name}: {description}" for name, description in COUNTRIES.items())
                send_message(chat_id, f"کشورت را انتخاب کن:\n\n{countries}", COUNTRY_KEYBOARD)
            elif text in COUNTRIES:
                db.execute("UPDATE players SET country = ? WHERE chat_id = ?", (text, chat_id))
                db.commit()
                send_message(
                    chat_id,
                    f"🎖 کشور تو شد: {text}\n{COUNTRIES[text]}\n\nحالا می‌توانی حمله کنی.",
                    MAIN_KEYBOARD,
                )
            elif text in ("📊 وضعیت", "/status"):
                show_status(db, chat_id)
            elif text in ("⚔️ حمله", "/attack"):
                attack(db, chat_id)
            elif text in ("🏆 رتبه‌بندی", "/top"):
                show_leaderboard(db, chat_id)
            elif text in ("ℹ️ راهنما", "/help"):
                send_message(
                    chat_id,
                    "ℹ️ راهنما\n\n"
                    "۱. یک کشور انتخاب کن.\n"
                    "۲. با دکمهٔ حمله بجنگ.\n"
                    "۳. با پیروزی امتیاز بگیر.\n"
                    "۴. از وضعیت و رتبه‌بندی استفاده کن.\n\n"
                    "این نسخه آزمایشی است و قوانین بازی در آینده کامل‌تر می‌شوند.",
                    MAIN_KEYBOARD,
                )
            elif text in ("🔄 شروع دوباره", "/reset"):
                reset_player(db, chat_id)
            elif text == "🔙 بازگشت":
                send_message(chat_id, "به منوی اصلی برگشتی.", MAIN_KEYBOARD)
            else:
                send_message(chat_id, "از دکمه‌های منو استفاده کن یا /help را بفرست.", MAIN_KEYBOARD)


if __name__ == "__main__":
    main()
