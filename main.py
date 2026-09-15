import os
import json
import random
import time
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
DATA_FILE = "players.json"

COLLECT_COOLDOWN = 60 * 60          # هر یک ساعت یک بار جمع‌آوری منابع
ATTACK_COOLDOWN = 60 * 15           # هر ۱۵ دقیقه یک حمله
SOLDIER_COST = 10                   # هزینه هر سرباز (طلا)
START_GOLD = 1000
START_SOLDIERS = 50
START_OIL = 500

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ذخیره‌سازی داده (JSON ساده)
# ---------------------------------------------------------------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_player(data, user_id, name=None, username=None):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "name": name or "بی‌نام",
            "username": username or "",
            "country": f"کشور {name or 'ناشناس'}",
            "gold": START_GOLD,
            "soldiers": START_SOLDIERS,
            "oil": START_OIL,
            "wins": 0,
            "losses": 0,
            "last_collect": 0,
            "last_attack": 0,
            "created_at": time.time(),
        }
        save_data(data)
    else:
        # به‌روزرسانی نام/یوزرنیم در صورت تغییر
        if name:
            data[uid]["name"] = name
        if username:
            data[uid]["username"] = username
    return data[uid]


def power_score(p):
    return p["soldiers"] * 3 + p["gold"] // 10 + p["oil"] // 5


def fmt_time(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h} ساعت و {m} دقیقه"
    if m:
        return f"{m} دقیقه و {s} ثانیه"
    return f"{s} ثانیه"


# ---------------------------------------------------------------------------
# دستورات
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    save_data(data)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👤 پروفایل", callback_data="profile")],
            [InlineKeyboardButton("💰 جمع‌آوری منابع", callback_data="collect")],
            [InlineKeyboardButton("🪖 خرید سرباز", callback_data="army_menu")],
            [InlineKeyboardButton("🏆 برترین‌ها", callback_data="leaderboard")],
            [InlineKeyboardButton("❓ راهنما", callback_data="help")],
        ]
    )

    await update.message.reply_text(
        f"🌍 به بازی «جنگ جهانی» خوش اومدی، فرمانده {p['name']}!\n\n"
        f"کشور تو: {p['country']}\n"
        f"طلا: {p['gold']} | سرباز: {p['soldiers']} | نفت: {p['oil']}\n\n"
        "با استفاده از دکمه‌های زیر می‌تونی منابع جمع کنی، ارتش بسازی و به دیگران حمله کنی.",
        reply_markup=keyboard,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 راهنمای بازی جنگ جهانی\n\n"
        "/start - شروع بازی و ثبت کشور\n"
        "/profile - نمایش وضعیت کشور تو\n"
        "/collect - جمع‌آوری منابع (هر ۱ ساعت یک‌بار)\n"
        "/army <تعداد> - خرید سرباز (هر سرباز ۱۰ طلا)\n"
        "/attack - حمله به یک بازیکن دیگر (روی پیامش ریپلای کن یا از /attack@username استفاده کن)\n"
        "/leaderboard - جدول برترین فرمانده‌ها\n\n"
        "هدف: با جمع‌آوری منابع، ساختن ارتش قوی‌تر و حمله هوشمندانه، قدرتمندترین کشور جهان بشو!"
    )
    if update.message:
        await update.message.reply_text(text)
    else:
        await update.callback_query.edit_message_text(text)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    save_data(data)

    text = (
        f"👤 پروفایل فرمانده {p['name']}\n"
        f"🏳️ کشور: {p['country']}\n\n"
        f"💰 طلا: {p['gold']}\n"
        f"🪖 سرباز: {p['soldiers']}\n"
        f"🛢️ نفت: {p['oil']}\n"
        f"⚔️ قدرت کل: {power_score(p)}\n\n"
        f"✅ پیروزی‌ها: {p['wins']}\n"
        f"❌ شکست‌ها: {p['losses']}"
    )
    if update.message:
        await update.message.reply_text(text)
    else:
        await update.callback_query.edit_message_text(text)


async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)

    now = time.time()
    remaining = COLLECT_COOLDOWN - (now - p["last_collect"])
    if remaining > 0:
        text = f"⏳ هنوز زوده! {fmt_time(remaining)} دیگه صبر کن تا دوباره منابع جمع کنی."
    else:
        gold_gain = random.randint(100, 300)
        oil_gain = random.randint(50, 150)
        p["gold"] += gold_gain
        p["oil"] += oil_gain
        p["last_collect"] = now
        save_data(data)
        text = (
            f"💰 منابع جمع‌آوری شد!\n"
            f"+{gold_gain} طلا | +{oil_gain} نفت\n\n"
            f"موجودی فعلی: {p['gold']} طلا | {p['oil']} نفت"
        )

    if update.message:
        await update.message.reply_text(text)
    else:
        await update.callback_query.edit_message_text(text)


async def army_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"🪖 خرید سرباز\n"
        f"هزینه هر سرباز: {SOLDIER_COST} طلا\n\n"
        f"برای خرید بنویس:\n/army <تعداد>\nمثال: /army 20"
    )
    if update.message:
        await update.message.reply_text(text)
    else:
        await update.callback_query.edit_message_text(text)


async def army(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)

    if not context.args:
        await update.message.reply_text("لطفاً تعداد سرباز رو مشخص کن. مثال: /army 20")
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("عدد معتبر وارد کن. مثال: /army 20")
        return

    if amount <= 0:
        await update.message.reply_text("تعداد باید بیشتر از صفر باشه.")
        return

    cost = amount * SOLDIER_COST
    if p["gold"] < cost:
        await update.message.reply_text(
            f"طلای کافی نداری! برای {amount} سرباز به {cost} طلا نیاز داری اما فقط {p['gold']} طلا داری."
        )
        return

    p["gold"] -= cost
    p["soldiers"] += amount
    save_data(data)

    await update.message.reply_text(
        f"✅ {amount} سرباز جدید استخدام شد!\n"
        f"هزینه: {cost} طلا\n\n"
        f"سربازان فعلی: {p['soldiers']} | طلای باقی‌مانده: {p['gold']}"
    )


async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    attacker = get_player(data, user.id, user.first_name, user.username)

    now = time.time()
    remaining = ATTACK_COOLDOWN - (now - attacker["last_attack"])
    if remaining > 0:
        await update.message.reply_text(
            f"⏳ ارتش تو هنوز خسته‌ست! {fmt_time(remaining)} دیگه صبر کن تا دوباره حمله کنی."
        )
        return

    # تعیین هدف: با ریپلای روی پیام یا با یوزرنیم در آرگومان
    target_id = None
    target_name = None

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        if target_user.id == user.id:
            await update.message.reply_text("نمی‌تونی به خودت حمله کنی!")
            return
        target_id = str(target_user.id)
        target_name = target_user.first_name
        get_player(data, target_user.id, target_user.first_name, target_user.username)
    elif context.args:
        uname = context.args[0].lstrip("@").lower()
        for uid, p in data.items():
            if p.get("username", "").lower() == uname:
                target_id = uid
                target_name = p["name"]
                break
        if not target_id:
            await update.message.reply_text(
                "بازیکن پیدا نشد. باید حداقل یک‌بار با /start ربات رو استارت کرده باشه."
            )
            return
    else:
        await update.message.reply_text(
            "برای حمله، روی پیام یک بازیکن ریپلای کن و /attack بزن،\n"
            "یا بنویس: /attack @username"
        )
        return

    defender = data[target_id]

    if attacker["soldiers"] < 5:
        await update.message.reply_text("حداقل به ۵ سرباز نیاز داری تا بتونی حمله کنی! از /army استفاده کن.")
        return

    # محاسبه نتیجه نبرد
    attack_power = attacker["soldiers"] * random.uniform(0.8, 1.3)
    defense_power = defender["soldiers"] * random.uniform(0.8, 1.3)

    attacker["last_attack"] = now

    if attack_power > defense_power:
        stolen_gold = min(defender["gold"], random.randint(50, 250))
        lost_soldiers_def = max(1, int(defender["soldiers"] * random.uniform(0.05, 0.15)))
        lost_soldiers_atk = max(0, int(attacker["soldiers"] * random.uniform(0.02, 0.08)))

        defender["gold"] -= stolen_gold
        defender["soldiers"] = max(0, defender["soldiers"] - lost_soldiers_def)
        attacker["gold"] += stolen_gold
        attacker["soldiers"] = max(0, attacker["soldiers"] - lost_soldiers_atk)
        attacker["wins"] += 1
        defender["losses"] += 1

        save_data(data)
        await update.message.reply_text(
            f"⚔️ پیروزی! تو به {target_name} حمله کردی و پیروز شدی!\n\n"
            f"💰 غنیمت: {stolen_gold} طلا\n"
            f"🪖 تلفات تو: {lost_soldiers_atk} سرباز\n"
            f"🪖 تلفات حریف: {lost_soldiers_def} سرباز"
        )
    else:
        lost_soldiers_atk = max(1, int(attacker["soldiers"] * random.uniform(0.1, 0.2)))
        attacker["soldiers"] = max(0, attacker["soldiers"] - lost_soldiers_atk)
        attacker["losses"] += 1
        defender["wins"] += 1

        save_data(data)
        await update.message.reply_text(
            f"💥 شکست! حمله تو به {target_name} با شکست مواجه شد.\n\n"
            f"🪖 تلفات تو: {lost_soldiers_atk} سرباز"
        )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data:
        text = "هنوز هیچ فرمانده‌ای ثبت نشده!"
    else:
        ranked = sorted(data.values(), key=power_score, reverse=True)[:10]
        lines = ["🏆 برترین فرمانده‌های جهان\n"]
        for i, p in enumerate(ranked, start=1):
            lines.append(f"{i}. {p['name']} — قدرت: {power_score(p)} (💰{p['gold']} 🪖{p['soldiers']})")
        text = "\n".join(lines)

    if update.message:
        await update.message.reply_text(text)
    else:
        await update.callback_query.edit_message_text(text)


# ---------------------------------------------------------------------------
# دکمه‌های شیشه‌ای (callback query)
# ---------------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "profile":
        await profile(update, context)
    elif query.data == "collect":
        await collect(update, context)
    elif query.data == "army_menu":
        await army_menu(update, context)
    elif query.data == "leaderboard":
        await leaderboard(update, context)
    elif query.data == "help":
        await help_cmd(update, context)


# ---------------------------------------------------------------------------
# اجرای ربات
# ---------------------------------------------------------------------------
def main():
    if BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit(
            "توکن ربات تنظیم نشده! مقدار BOT_TOKEN رو به عنوان متغیر محیطی ست کن."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("collect", collect))
    app.add_handler(CommandHandler("army", army))
    app.add_handler(CommandHandler("attack", attack))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("ربات جنگ جهانی در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
