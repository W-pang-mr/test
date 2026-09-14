import os
import random
import sqlite3
from datetime import date, timedelta

import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
DB_PATH = os.getenv("DB_PATH", "surprise_bot.db")

MAIN_KEYBOARD = {
    "keyboard": [
        ["🎁 جایزه روزانه", "🎲 بازی‌ها"],
        ["🧠 دانستنی", "✅ کارها"],
        ["👤 پروفایل", "🏆 جدول"],
        ["🛒 فروشگاه", "😊 حال من"],
        ["ℹ️ راهنما", "🔄 شروع دوباره"],
    ],
    "resize_keyboard": True,
}

GAMES_KEYBOARD = {
    "keyboard": [
        ["🪙 شیر یا خط", "🎯 تاس"],
        ["🔙 بازگشت"],
    ],
    "resize_keyboard": True,
}

TASKS_KEYBOARD = {
    "keyboard": [
        ["➕ کار جدید", "✅ تکمیل آخرین"],
        ["🗑 حذف آخرین", "🔙 بازگشت"],
    ],
    "resize_keyboard": True,
}

MOOD_KEYBOARD = {
    "keyboard": [
        ["😀 عالی", "🙂 خوب", "😐 معمولی"],
        ["😔 بد", "😡 افتضاح"],
        ["🔙 بازگشت"],
    ],
    "resize_keyboard": True,
}

SHOP_ITEMS = {
    "🏅 نشان برنزی": (100, "🏅"),
    "🥈 نشان نقره‌ای": (250, "🥈"),
    "🥇 نشان طلایی": (500, "🥇"),
}

MOODS = {
    "😀 عالی": "عالی",
    "🙂 خوب": "خوب",
    "😐 معمولی": "معمولی",
    "😔 بد": "بد",
    "😡 افتضاح": "افتضاح",
}

FACTS = [
    "اختاپوس سه قلب دارد و خونش آبی است.",
    "عسل تنها غذایی است که تقریباً هیچ‌وقت فاسد نمی‌شود.",
    "قلب نهنگ آبی می‌تواند به اندازهٔ یک خودرو باشد.",
    "مغز انسان حدود ۲۰ درصد انرژی بدن را مصرف می‌کند.",
    "رعدوبرق می‌تواند پنج برابر داغ‌تر از سطح خورشید باشد.",
    "زنبورها می‌توانند چهرهٔ انسان‌ها را تشخیص دهند.",
    "اثر انگشت کوالا شبیه اثر انگشت انسان است.",
    "برج ایفل در تابستان به‌دلیل گرما کمی بلندتر می‌شود.",
]


def setup_database():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            coins INTEGER NOT NULL DEFAULT 100,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            streak INTEGER NOT NULL DEFAULT 0,
            last_daily TEXT,
            mood TEXT,
            badge TEXT DEFAULT '—',
            state TEXT
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
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


def get_name(message):
    user = message.get("from", {})
    return user.get("username") or user.get("first_name") or str(message["chat"]["id"])


def ensure_user(db, chat_id, name):
    db.execute(
        "INSERT OR IGNORE INTO users (chat_id, name) VALUES (?, ?)",
        (chat_id, name),
    )
    db.execute("UPDATE users SET name = ? WHERE chat_id = ?", (name, chat_id))
    db.commit()


def get_user(db, chat_id):
    return db.execute(
        "SELECT chat_id, name, coins, xp, level, streak, last_daily, mood, badge, state "
        "FROM users WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()


def add_xp(db, chat_id, amount):
    user = get_user(db, chat_id)
    old_level = user[4]
    new_xp = user[3] + amount
    new_level = new_xp // 100 + 1
    db.execute(
        "UPDATE users SET xp = ?, level = ? WHERE chat_id = ?",
        (new_xp, new_level, chat_id),
    )
    db.commit()
    return new_level > old_level


def set_state(db, chat_id, state):
    db.execute("UPDATE users SET state = ? WHERE chat_id = ?", (state, chat_id))
    db.commit()


def show_welcome(chat_id):
    send_message(
        chat_id,
        "سلام! 🎉\n\n"
        "به ربات همراهت خوش آمدی. اینجا می‌توانی امتیاز جمع کنی، بازی کنی، کارهایت را مدیریت کنی و هر روز چیز تازه‌ای کشف کنی.\n\n"
        "از منوی پایین شروع کن!",
        MAIN_KEYBOARD,
    )


def daily_reward(db, chat_id):
    user = get_user(db, chat_id)
    today = date.today()
    if user[6] == today.isoformat():
        send_message(
            chat_id,
            f"🎁 جایزهٔ امروزت را قبلاً گرفته‌ای.\nفردا برگرد تا زنجیره‌ات قطع نشود!\n🔥 زنجیره فعلی: {user[5]} روز",
            MAIN_KEYBOARD,
        )
        return

    yesterday = (today - timedelta(days=1)).isoformat()
    streak = user[5] + 1 if user[6] == yesterday else 1
    reward = 50 + min(streak, 10) * 10
    new_level = add_xp(db, chat_id, 25)
    db.execute(
        "UPDATE users SET coins = coins + ?, streak = ?, last_daily = ? WHERE chat_id = ?",
        (reward, streak, today.isoformat(), chat_id),
    )
    db.commit()
    level_text = "\n🎊 تبریک! سطح جدید گرفتی." if new_level else ""
    send_message(
        chat_id,
        f"🎁 جایزهٔ روزانه دریافت شد!\n\n"
        f"💰 سکه: +{reward}\n⭐ تجربه: +25\n🔥 زنجیره: {streak} روز"
        f"{level_text}",
        MAIN_KEYBOARD,
    )


def show_profile(db, chat_id):
    user = get_user(db, chat_id)
    task_count = db.execute(
        "SELECT COUNT(*) FROM tasks WHERE chat_id = ? AND done = 0", (chat_id,)
    ).fetchone()[0]
    mood = user[7] or "ثبت نشده"
    send_message(
        chat_id,
        "👤 پروفایل تو\n\n"
        f"نام: {user[1]}\n"
        f"سطح: {user[4]}\n"
        f"⭐ تجربه: {user[3]}\n"
        f"💰 سکه: {user[2]}\n"
        f"🔥 زنجیره روزانه: {user[5]} روز\n"
        f"😊 حال ثبت‌شده: {mood}\n"
        f"🏅 نشان: {user[8]}\n"
        f"✅ کارهای باز: {task_count}",
        MAIN_KEYBOARD,
    )


def play_coin(db, chat_id):
    user = get_user(db, chat_id)
    if user[2] < 5:
        send_message(chat_id, "💰 برای این بازی حداقل ۵ سکه لازم داری.", GAMES_KEYBOARD)
        return
    result = random.choice(["شیر", "خط"])
    won = result == "شیر"
    change = 15 if won else -5
    db.execute("UPDATE users SET coins = coins + ? WHERE chat_id = ?", (change, chat_id))
    db.commit()
    add_xp(db, chat_id, 5 if won else 1)
    if won:
        message = f"🪙 نتیجه: {result}\n🎉 بردی! ۱۵ سکه گرفتی."
    else:
        message = f"🪙 نتیجه: {result}\nاین بار شانس یارت نبود؛ ۵ سکه کم شد."
    send_message(chat_id, message, GAMES_KEYBOARD)


def play_dice(db, chat_id):
    user = get_user(db, chat_id)
    if user[2] < 3:
        send_message(chat_id, "💰 برای تاس انداختن حداقل ۳ سکه لازم داری.", GAMES_KEYBOARD)
        return
    roll = random.randint(1, 6)
    reward = 30 if roll == 6 else 0
    db.execute("UPDATE users SET coins = coins - 3 + ? WHERE chat_id = ?", (reward, chat_id))
    db.commit()
    add_xp(db, chat_id, roll)
    result = f"🎯 عدد تاس: {roll}\n"
    result += "🎉 جایزهٔ ویژه: ۳۰ سکه!" if reward else "۳ سکه پرداخت شد؛ دوباره امتحان کن!"
    send_message(chat_id, result, GAMES_KEYBOARD)


def show_tasks(db, chat_id):
    tasks = db.execute(
        "SELECT id, text, done FROM tasks WHERE chat_id = ? ORDER BY done ASC, id DESC LIMIT 15",
        (chat_id,),
    ).fetchall()
    if not tasks:
        message = "✅ هنوز کاری ثبت نکرده‌ای.\n\nروی «➕ کار جدید» بزن تا اولین کار را اضافه کنی."
    else:
        lines = ["✅ فهرست کارها\n"]
        for index, (_, text, done) in enumerate(tasks, start=1):
            mark = "✅" if done else "⬜"
            lines.append(f"{index}. {mark} {text}")
        message = "\n".join(lines)
    send_message(chat_id, message, TASKS_KEYBOARD)


def add_task(db, chat_id, text):
    db.execute(
        "INSERT INTO tasks (chat_id, text, created_at) VALUES (?, ?, ?)",
        (chat_id, text[:200], date.today().isoformat()),
    )
    db.execute("UPDATE users SET state = NULL WHERE chat_id = ?", (chat_id,))
    db.commit()
    level_up = add_xp(db, chat_id, 10)
    message = "✅ کار ثبت شد و ۱۰ تجربه گرفتی."
    if level_up:
        message += "\n🎊 سطح جدید مبارک!"
    send_message(chat_id, message, TASKS_KEYBOARD)


def complete_last_task(db, chat_id):
    task = db.execute(
        "SELECT id, text FROM tasks WHERE chat_id = ? AND done = 0 ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    if not task:
        send_message(chat_id, "همهٔ کارها انجام شده‌اند یا هنوز کاری نداری.", TASKS_KEYBOARD)
        return
    db.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task[0],))
    db.commit()
    add_xp(db, chat_id, 20)
    db.execute("UPDATE users SET coins = coins + 10 WHERE chat_id = ?", (chat_id,))
    db.commit()
    send_message(chat_id, f"🎉 انجام شد: {task[1]}\n💰 ۱۰ سکه و ⭐ ۲۰ تجربه گرفتی.", TASKS_KEYBOARD)


def delete_last_task(db, chat_id):
    task = db.execute(
        "SELECT id, text FROM tasks WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
    ).fetchone()
    if not task:
        send_message(chat_id, "کاری برای حذف وجود ندارد.", TASKS_KEYBOARD)
        return
    db.execute("DELETE FROM tasks WHERE id = ?", (task[0],))
    db.commit()
    send_message(chat_id, f"🗑 حذف شد: {task[1]}", TASKS_KEYBOARD)


def show_shop(db, chat_id):
    user = get_user(db, chat_id)
    lines = [f"🛒 فروشگاه\n\n💰 موجودی: {user[2]} سکه\n"]
    for item, (price, badge) in SHOP_ITEMS.items():
        lines.append(f"{item} — {price} سکه")
    lines.append("\nبرای خرید، نام دقیق نشان را ارسال کن.")
    set_state(db, chat_id, "shop")
    send_message(chat_id, "\n".join(lines), MAIN_KEYBOARD)


def buy_item(db, chat_id, text):
    if text not in SHOP_ITEMS:
        return False
    user = get_user(db, chat_id)
    price, badge = SHOP_ITEMS[text]
    if user[2] < price:
        send_message(chat_id, f"برای خرید {text} به {price} سکه نیاز داری.", MAIN_KEYBOARD)
        return True
    db.execute(
        "UPDATE users SET coins = coins - ?, badge = ?, state = NULL WHERE chat_id = ?",
        (price, badge, chat_id),
    )
    db.commit()
    send_message(chat_id, f"🎉 {text} خریداری شد و در پروفایلت قرار گرفت!", MAIN_KEYBOARD)
    return True


def show_leaderboard(db, chat_id):
    users = db.execute(
        "SELECT name, level, xp, coins FROM users ORDER BY xp DESC, coins DESC LIMIT 10"
    ).fetchall()
    lines = ["🏆 جدول برترین‌ها\n"]
    for index, user in enumerate(users, start=1):
        lines.append(f"{index}. {user[0]} — سطح {user[1]} — ⭐ {user[2]}")
    send_message(chat_id, "\n".join(lines), MAIN_KEYBOARD)


def reset_user(db, chat_id):
    db.execute(
        "UPDATE users SET coins = 100, xp = 0, level = 1, streak = 0, last_daily = NULL, "
        "mood = NULL, badge = '—', state = NULL WHERE chat_id = ?",
        (chat_id,),
    )
    db.execute("DELETE FROM tasks WHERE chat_id = ?", (chat_id,))
    db.commit()
    send_message(chat_id, "🔄 پروفایل و کارها از اول شروع شدند.", MAIN_KEYBOARD)


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
            text = message["text"]
            ensure_user(db, chat_id, get_name(message))
            user = get_user(db, chat_id)

            if user[9] == "add_task" and text not in ("🔙 بازگشت", "/start"):
                add_task(db, chat_id, text)
                continue
            if user[9] == "shop" and text not in ("🔙 بازگشت", "/start"):
                if buy_item(db, chat_id, text):
                    continue
                db.execute("UPDATE users SET state = NULL WHERE chat_id = ?", (chat_id,))
                db.commit()

            if text == "/start":
                show_welcome(chat_id)
            elif text in ("🎁 جایزه روزانه", "/daily"):
                daily_reward(db, chat_id)
            elif text in ("🎲 بازی‌ها", "/games"):
                send_message(chat_id, "🎲 یک بازی انتخاب کن:", GAMES_KEYBOARD)
            elif text in ("🪙 شیر یا خط", "/coin"):
                play_coin(db, chat_id)
            elif text in ("🎯 تاس", "/dice"):
                play_dice(db, chat_id)
            elif text in ("🧠 دانستنی", "/fact"):
                fact = random.choice(FACTS)
                add_xp(db, chat_id, 3)
                send_message(chat_id, f"🧠 دانستنی امروز\n\n{fact}\n\n⭐ ۳ تجربه گرفتی.", MAIN_KEYBOARD)
            elif text in ("✅ کارها", "/tasks"):
                show_tasks(db, chat_id)
            elif text == "➕ کار جدید":
                set_state(db, chat_id, "add_task")
                send_message(chat_id, "متن کار جدیدت را در یک پیام بفرست:", TASKS_KEYBOARD)
            elif text == "✅ تکمیل آخرین":
                complete_last_task(db, chat_id)
            elif text == "🗑 حذف آخرین":
                delete_last_task(db, chat_id)
            elif text in ("👤 پروفایل", "/profile"):
                show_profile(db, chat_id)
            elif text in ("🏆 جدول", "/top"):
                show_leaderboard(db, chat_id)
            elif text in ("🛒 فروشگاه", "/shop"):
                show_shop(db, chat_id)
            elif text in MOODS:
                db.execute("UPDATE users SET mood = ? WHERE chat_id = ?", (MOODS[text], chat_id))
                db.commit()
                add_xp(db, chat_id, 5)
                send_message(chat_id, f"😊 حال امروزت ثبت شد: {MOODS[text]}\n⭐ ۵ تجربه گرفتی.", MAIN_KEYBOARD)
            elif text in ("😊 حال من", "/mood"):
                send_message(chat_id, "امروز حالت چطور است؟", MOOD_KEYBOARD)
            elif text in ("ℹ️ راهنما", "/help"):
                send_message(
                    chat_id,
                    "ℹ️ راهنما\n\n"
                    "🎁 هر روز جایزه بگیر.\n"
                    "🎲 با بازی‌ها سکه جمع کن.\n"
                    "🧠 دانستنی بخوان و تجربه بگیر.\n"
                    "✅ کارهایت را ثبت و تکمیل کن.\n"
                    "🛒 سکه‌ها را در فروشگاه خرج کن.\n"
                    "👤 پیشرفتت را در پروفایل ببین.\n"
                    "🏆 با بقیه رقابت کن.",
                    MAIN_KEYBOARD,
                )
            elif text in ("🔄 شروع دوباره", "/reset"):
                reset_user(db, chat_id)
            elif text == "🔙 بازگشت":
                set_state(db, chat_id, None)
                send_message(chat_id, "به منوی اصلی برگشتی.", MAIN_KEYBOARD)
            else:
                send_message(chat_id, "از دکمه‌های منو استفاده کن یا /help را بفرست.", MAIN_KEYBOARD)


if __name__ == "__main__":
    main()
