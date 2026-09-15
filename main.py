import os
import json
import random
import time
import html
import asyncio
import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# تنظیمات کلی
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
DATA_FILE = "players.json"

COLLECT_COOLDOWN = 60 * 60      # هر ۱ ساعت
ATTACK_COOLDOWN = 60 * 15       # هر ۱۵ دقیقه

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DIVIDER = "▬▬▬▬▬▬▬▬▬▬"

# ---------------------------------------------------------------------------
# ۱۰ کشور قابل انتخاب، هرکدوم با یک امتیاز ویژه
# ---------------------------------------------------------------------------
COUNTRIES = {
    "usa":     {"flag": "🇺🇸", "name": "آمریکا",   "desc": "ارتش مدرن، قدرت حمله بالا",      "power_mult": 1.15},
    "russia":  {"flag": "🇷🇺", "name": "روسیه",    "desc": "ذخایر عظیم نفتی",                "oil_mult": 1.3},
    "china":   {"flag": "🇨🇳", "name": "چین",      "desc": "اقتصاد پرقدرت، طلای بیشتر",       "gold_mult": 1.3},
    "germany": {"flag": "🇩🇪", "name": "آلمان",    "desc": "صنعت آهن و فولاد پیشرفته",        "iron_mult": 1.3},
    "japan":   {"flag": "🇯🇵", "name": "ژاپن",     "desc": "تکنولوژی برتر، تجهیزات ارزان‌تر", "unit_discount": 0.15},
    "uk":      {"flag": "🇬🇧", "name": "بریتانیا", "desc": "ارتشی منظم و کارآمد",             "power_mult": 1.1, "gold_mult": 1.05},
    "france":  {"flag": "🇫🇷", "name": "فرانسه",   "desc": "کشوری متعادل در همه‌چیز",         "gold_mult": 1.1, "oil_mult": 1.1, "iron_mult": 1.1},
    "iran":    {"flag": "🇮🇷", "name": "ایران",    "desc": "نفت فراوان، دفاع قدرتمند",        "oil_mult": 1.25, "power_mult": 1.05},
    "brazil":  {"flag": "🇧🇷", "name": "برزیل",    "desc": "رشد اقتصادی سریع",                "gold_mult": 1.2, "iron_mult": 1.1},
    "india":   {"flag": "🇮🇳", "name": "هند",      "desc": "جمعیت عظیم، تجهیزات ارزان",       "unit_discount": 0.2, "gold_mult": 1.05},
}
COUNTRY_ORDER = list(COUNTRIES.keys())


def country_bonus(p, key, default=1.0):
    code = p.get("country_code")
    if code and code in COUNTRIES:
        return COUNTRIES[code].get(key, default)
    return default


# ---------------------------------------------------------------------------
# واحدهای نظامی
# ---------------------------------------------------------------------------
UNIT_INFO = {
    "soldier": {"label": "🪖 سرباز", "power": 1, "cost": {"gold": 10}},
    "tank": {"label": "🚛 تانک", "power": 5, "cost": {"gold": 50, "iron": 10}},
    "jet": {"label": "🛩 جنگنده", "power": 15, "cost": {"gold": 150, "iron": 30, "oil": 20}},
    "warship": {"label": "🚢 ناو جنگی", "power": 25, "cost": {"gold": 300, "iron": 50, "oil": 50}},
}
UNIT_ORDER = ["soldier", "tank", "jet", "warship"]

# ---------------------------------------------------------------------------
# زیرساخت‌ها
# ---------------------------------------------------------------------------
BUILDING_INFO = {
    "bank": {"label": "🏦 بانک", "resource": "gold", "base_gain": 60, "base_cost": 300},
    "refinery": {"label": "🛢 پالایشگاه", "resource": "oil", "base_gain": 35, "base_cost": 250},
    "mine": {"label": "⛏ معدن آهن", "resource": "iron", "base_gain": 30, "base_cost": 250},
}
BUILDING_ORDER = ["bank", "refinery", "mine"]
RES_NAMES = {"gold": "طلا", "oil": "نفت", "iron": "آهن"}

START_RESOURCES = {"gold": 1000, "oil": 400, "iron": 200}
START_UNITS = {"soldier": 30, "tank": 0, "jet": 0, "warship": 0}
START_BUILDINGS = {"bank": 0, "refinery": 0, "mine": 0}


# ---------------------------------------------------------------------------
# ذخیره‌سازی داده
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


def player_exists(data, user_id):
    return str(user_id) in data


def create_player(data, user_id, name, username, country_code):
    uid = str(user_id)
    c = COUNTRIES[country_code]
    data[uid] = {
        "name": name or "بی‌نام",
        "username": username or "",
        "country_code": country_code,
        "country": f"{c['flag']} {c['name']}",
        "resources": dict(START_RESOURCES),
        "units": dict(START_UNITS),
        "buildings": dict(START_BUILDINGS),
        "wins": 0,
        "losses": 0,
        "last_collect": 0,
        "last_attack": 0,
        "created_at": time.time(),
    }
    save_data(data)
    return data[uid]


def get_player(data, user_id, name=None, username=None):
    """فقط برای بازیکنانی که قبلاً ثبت‌نام کردن. اگه ثبت‌نام نکرده None برمی‌گردونه."""
    uid = str(user_id)
    if uid not in data:
        return None
    p = data[uid]
    p.setdefault("resources", dict(START_RESOURCES))
    p.setdefault("units", dict(START_UNITS))
    p.setdefault("buildings", dict(START_BUILDINGS))
    for k in UNIT_ORDER:
        p["units"].setdefault(k, 0)
    for k in BUILDING_ORDER:
        p["buildings"].setdefault(k, 0)
    for k in START_RESOURCES:
        p["resources"].setdefault(k, 0)
    p.setdefault("country_code", None)
    if name:
        p["name"] = name
    if username:
        p["username"] = username
    return p


def esc(text):
    return html.escape(str(text))


def total_power(p):
    base = sum(p["units"][u] * UNIT_INFO[u]["power"] for u in UNIT_ORDER)
    return base * country_bonus(p, "power_mult", 1.0)


def power_score(p):
    res = p["resources"]
    return total_power(p) * 10 + res["gold"] // 10 + res["oil"] // 10 + res["iron"] // 10


def fmt_time(seconds):
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h} ساعت و {m} دقیقه"
    if m:
        return f"{m} دقیقه و {s} ثانیه"
    return f"{s} ثانیه"


def building_upgrade_cost(building, level):
    return BUILDING_INFO[building]["base_cost"] * (level + 1)


def unit_real_cost(p, unit, amount=1):
    discount = 1.0 - country_bonus(p, "unit_discount", 0.0)
    result = {}
    for res, amt in UNIT_INFO[unit]["cost"].items():
        result[res] = max(1, int(round(amt * amount * discount)))
    return result


def cost_text(cost_dict):
    return " + ".join(f"{amt} {RES_NAMES[res]}" for res, amt in cost_dict.items())


# ---------------------------------------------------------------------------
# کیبوردها
# ---------------------------------------------------------------------------
def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👤 پروفایل", callback_data="profile"),
             InlineKeyboardButton("💰 جمع‌آوری", callback_data="collect")],
            [InlineKeyboardButton("🛒 فروشگاه", callback_data="shop"),
             InlineKeyboardButton("🏗 زیرساخت‌ها", callback_data="buildings")],
            [InlineKeyboardButton("⚔️ حمله", callback_data="attack_menu"),
             InlineKeyboardButton("🏆 برترین‌ها", callback_data="leaderboard")],
            [InlineKeyboardButton("❓ راهنما", callback_data="help")],
        ]
    )


def country_select_keyboard():
    rows = []
    codes = COUNTRY_ORDER
    for i in range(0, len(codes), 2):
        row = []
        for code in codes[i:i + 2]:
            c = COUNTRIES[code]
            row.append(InlineKeyboardButton(f"{c['flag']} {c['name']}", callback_data=f"country_{code}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def shop_keyboard():
    rows = []
    for u in UNIT_ORDER:
        rows.append([InlineKeyboardButton(f"── {UNIT_INFO[u]['label']} ──", callback_data="noop")])
        rows.append([
            InlineKeyboardButton("+1", callback_data=f"buy_{u}_1"),
            InlineKeyboardButton("+10", callback_data=f"buy_{u}_10"),
            InlineKeyboardButton("+50", callback_data=f"buy_{u}_50"),
        ])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def buildings_keyboard(p):
    rows = []
    for b in BUILDING_ORDER:
        level = p["buildings"][b]
        cost = building_upgrade_cost(b, level)
        rows.append([InlineKeyboardButton(
            f"⬆️ ارتقای {BUILDING_INFO[b]['label']} (سطح {level} → {level + 1}) — {cost} طلا",
            callback_data=f"build_{b}"
        )])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def attack_menu_keyboard(data, self_uid):
    others = [(uid, pl) for uid, pl in data.items() if uid != self_uid]
    others.sort(key=lambda x: total_power(x[1]), reverse=True)
    others = others[:8]
    rows = []
    for uid, pl in others:
        flag = COUNTRIES.get(pl.get("country_code"), {}).get("flag", "🏳️")
        rows.append([InlineKeyboardButton(
            f"{flag} {pl['name']} — ⚔️ {int(total_power(pl))}",
            callback_data=f"attack_{uid}"
        )])
    if not others:
        rows.append([InlineKeyboardButton("😴 هنوز کسی برای حمله نیست", callback_data="noop")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# نمایش/ادیت پیام کمکی
# ---------------------------------------------------------------------------
async def respond(update: Update, text, keyboard=None):
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


# ---------------------------------------------------------------------------
# ثبت‌نام / شروع
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()

    if not player_exists(data, user.id):
        await update.message.reply_text(
            f"🌍 <b>به بازی «جنگ جهانی» خوش اومدی!</b>\n{DIVIDER}\n"
            "برای شروع، اول باید کشورت رو انتخاب کنی. هر کشور یک امتیاز ویژه داره 👇",
            reply_markup=country_select_keyboard(),
            parse_mode="HTML",
        )
        return

    p = get_player(data, user.id, user.first_name, user.username)
    save_data(data)
    await show_profile(update, context, p, is_welcome=True)


async def choose_country(update: Update, context: ContextTypes.DEFAULT_TYPE, code):
    user = update.effective_user
    data = load_data()

    if player_exists(data, user.id):
        p = get_player(data, user.id, user.first_name, user.username)
        save_data(data)
        await respond(update, "قبلاً کشورت رو انتخاب کردی!", main_menu_keyboard())
        return

    p = create_player(data, user.id, user.first_name, user.username, code)
    c = COUNTRIES[code]

    bonus_lines = []
    if "power_mult" in c:
        bonus_lines.append(f"⚔️ قدرت نظامی +{int((c['power_mult'] - 1) * 100)}٪")
    if "gold_mult" in c:
        bonus_lines.append(f"💰 تولید طلا +{int((c['gold_mult'] - 1) * 100)}٪")
    if "oil_mult" in c:
        bonus_lines.append(f"🛢 تولید نفت +{int((c['oil_mult'] - 1) * 100)}٪")
    if "iron_mult" in c:
        bonus_lines.append(f"⛏ تولید آهن +{int((c['iron_mult'] - 1) * 100)}٪")
    if "unit_discount" in c:
        bonus_lines.append(f"🛒 هزینه خرید نظامی -{int(c['unit_discount'] * 100)}٪")

    text = (
        f"🎉 <b>{c['flag']} {c['name']}</b> انتخاب شد!\n"
        f"<i>{c['desc']}</i>\n\n"
        + "\n".join(bonus_lines) +
        f"\n{DIVIDER}\n"
        f"💰 طلا: {p['resources']['gold']} | 🛢 نفت: {p['resources']['oil']} | ⛏ آهن: {p['resources']['iron']}\n"
        f"🪖 سرباز اولیه: {p['units']['soldier']}\n\n"
        "حالا با دکمه‌های زیر بازی کن 👇"
    )
    await respond(update, text, main_menu_keyboard())


# ---------------------------------------------------------------------------
# پروفایل
# ---------------------------------------------------------------------------
async def show_profile(update, context, p, is_welcome=False):
    units_text = "\n".join(f"  {UNIT_INFO[u]['label']}: <b>{p['units'][u]}</b>" for u in UNIT_ORDER)
    buildings_text = "\n".join(f"  {BUILDING_INFO[b]['label']}: سطح <b>{p['buildings'][b]}</b>" for b in BUILDING_ORDER)
    header = "🌍 <b>خوش برگشتی!</b>\n" if is_welcome else ""

    text = (
        f"{header}👤 فرمانده <b>{esc(p['name'])}</b>\n"
        f"🏳️ کشور: <b>{p['country']}</b>\n"
        f"{DIVIDER}\n"
        f"💰 طلا: <b>{p['resources']['gold']}</b>  |  🛢 نفت: <b>{p['resources']['oil']}</b>  |  ⛏ آهن: <b>{p['resources']['iron']}</b>\n\n"
        f"🪖 <b>نیروهای نظامی</b>\n{units_text}\n\n"
        f"🏗 <b>زیرساخت‌ها</b>\n{buildings_text}\n"
        f"{DIVIDER}\n"
        f"⚔️ قدرت نظامی: <b>{int(total_power(p))}</b>\n"
        f"✅ پیروزی: {p['wins']}   ❌ شکست: {p['losses']}"
    )
    await respond(update, text, main_menu_keyboard())


async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return
    save_data(data)
    await show_profile(update, context, p)


# ---------------------------------------------------------------------------
# راهنما
# ---------------------------------------------------------------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>راهنمای جنگ جهانی</b>\n"
        f"{DIVIDER}\n"
        "همه‌چیز با دکمه‌های زیر پیامای ربات قابل انجامه، لازم نیست چیزی تایپ کنی:\n\n"
        "👤 <b>پروفایل</b> — وضعیت کامل کشورت\n"
        "💰 <b>جمع‌آوری</b> — گرفتن منابع (هر ۱ ساعت)\n"
        "🛒 <b>فروشگاه</b> — خرید سرباز، تانک، جنگنده، ناو\n"
        "🏗 <b>زیرساخت‌ها</b> — ارتقای بانک، پالایشگاه، معدن\n"
        "⚔️ <b>حمله</b> — انتخاب یک کشور و حمله بهش\n"
        "🏆 <b>برترین‌ها</b> — جدول قدرتمندترین فرمانده‌ها\n\n"
        "هر کشور یک امتیاز ویژه داره؛ انتخاب هوشمندانه کشور می‌تونه تو رشدت خیلی کمک کنه!"
    )
    await respond(update, text, main_menu_keyboard())


# ---------------------------------------------------------------------------
# جمع‌آوری منابع
# ---------------------------------------------------------------------------
async def collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return

    now = time.time()
    remaining = COLLECT_COOLDOWN - (now - p["last_collect"])
    if remaining > 0:
        text = f"⏳ هنوز زوده! <b>{fmt_time(remaining)}</b> دیگه صبر کن."
    else:
        gains = {"gold": 0, "oil": 0, "iron": 0}
        for b in BUILDING_ORDER:
            info = BUILDING_INFO[b]
            level = p["buildings"][b]
            base = random.randint(60, 140) if b == "bank" else random.randint(30, 90)
            gain = base + level * info["base_gain"]
            gain = int(gain * country_bonus(p, f"{info['resource']}_mult", 1.0))
            p["resources"][info["resource"]] += gain
            gains[info["resource"]] += gain

        p["last_collect"] = now
        save_data(data)

        text = (
            "💰 <b>منابع جمع‌آوری شد!</b>\n"
            f"+{gains['gold']} طلا | +{gains['oil']} نفت | +{gains['iron']} آهن\n\n"
            f"موجودی: 💰{p['resources']['gold']} | 🛢{p['resources']['oil']} | ⛏{p['resources']['iron']}"
        )

    await respond(update, text, main_menu_keyboard())


# ---------------------------------------------------------------------------
# فروشگاه
# ---------------------------------------------------------------------------
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return

    lines = ["🛒 <b>فروشگاه نظامی</b>", DIVIDER]
    for u in UNIT_ORDER:
        one_cost = unit_real_cost(p, u, 1)
        lines.append(f"{UNIT_INFO[u]['label']} (قدرت {UNIT_INFO[u]['power']}) — {cost_text(one_cost)}  |  داری: {p['units'][u]}")
    lines.append(f"\n💰 {p['resources']['gold']} | 🛢 {p['resources']['oil']} | ⛏ {p['resources']['iron']}")
    lines.append("\nبا دکمه‌های زیر بخر 👇")
    text = "\n".join(lines)
    await respond(update, text, shop_keyboard())


async def buy_unit(update: Update, context: ContextTypes.DEFAULT_TYPE, unit, amount):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return

    cost = unit_real_cost(p, unit, amount)
    for res, amt in cost.items():
        if p["resources"].get(res, 0) < amt:
            await update.callback_query.answer(
                f"منابع کافی نداری! برای {amount} {UNIT_INFO[unit]['label']} به {cost_text(cost)} نیاز داری.",
                show_alert=True,
            )
            return

    for res, amt in cost.items():
        p["resources"][res] -= amt
    p["units"][unit] += amount
    save_data(data)

    await update.callback_query.answer(f"✅ {amount} {UNIT_INFO[unit]['label']} خریداری شد!")
    await shop(update, context)


# ---------------------------------------------------------------------------
# زیرساخت‌ها
# ---------------------------------------------------------------------------
async def buildings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return
    save_data(data)

    lines = ["🏗 <b>زیرساخت‌های کشورت</b>", DIVIDER]
    for b in BUILDING_ORDER:
        info = BUILDING_INFO[b]
        level = p["buildings"][b]
        lines.append(f"{info['label']} — سطح <b>{level}</b> (هر سطح تولید {info['resource']} رو بیشتر می‌کنه)")
    lines.append(f"\n💰 موجودی طلا: {p['resources']['gold']}")
    text = "\n".join(lines)
    await respond(update, text, buildings_keyboard(p))


async def build_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE, building):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return

    level = p["buildings"][building]
    cost = building_upgrade_cost(building, level)

    if p["resources"]["gold"] < cost:
        await update.callback_query.answer(
            f"طلای کافی نداری! برای ارتقا به {cost} طلا نیاز داری.", show_alert=True
        )
        return

    p["resources"]["gold"] -= cost
    p["buildings"][building] += 1
    save_data(data)

    await update.callback_query.answer(f"✅ {BUILDING_INFO[building]['label']} ارتقا یافت به سطح {p['buildings'][building]}!")
    await buildings_cmd(update, context)


# ---------------------------------------------------------------------------
# حمله
# ---------------------------------------------------------------------------
def apply_losses(units, low, high):
    losses = {}
    for u in UNIT_ORDER:
        if units[u] > 0:
            lost = int(units[u] * random.uniform(low, high))
            lost = min(max(lost, 1), units[u])
            units[u] -= lost
            if lost:
                losses[u] = lost
    return losses


def losses_text(losses):
    if not losses:
        return "بدون تلفات"
    return ", ".join(f"{amt} {UNIT_INFO[u]['label']}" for u, amt in losses.items())


async def attack_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = load_data()
    p = get_player(data, user.id, user.first_name, user.username)
    if not p:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return

    now = time.time()
    remaining = ATTACK_COOLDOWN - (now - p["last_attack"])
    if remaining > 0:
        await respond(update, f"⏳ ارتش تو خسته‌ست! <b>{fmt_time(remaining)}</b> دیگه صبر کن.", main_menu_keyboard())
        return

    text = (
        "⚔️ <b>انتخاب هدف حمله</b>\n"
        f"{DIVIDER}\n"
        f"قدرت نظامی تو: <b>{int(total_power(p))}</b>\n\n"
        "یکی از کشورهای زیر رو برای حمله انتخاب کن 👇"
    )
    await respond(update, text, attack_menu_keyboard(data, str(user.id)))


async def do_attack(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid):
    query = update.callback_query
    user = update.effective_user
    data = load_data()
    attacker = get_player(data, user.id, user.first_name, user.username)
    if not attacker:
        await respond(update, "اول باید کشورت رو انتخاب کنی! /start رو بزن.")
        return

    if target_uid == str(user.id):
        await query.answer("نمی‌تونی به خودت حمله کنی!", show_alert=True)
        return
    if target_uid not in data:
        await query.answer("این کشور دیگه وجود نداره.", show_alert=True)
        return

    now = time.time()
    remaining = ATTACK_COOLDOWN - (now - attacker["last_attack"])
    if remaining > 0:
        await query.answer(f"⏳ {fmt_time(remaining)} دیگه صبر کن.", show_alert=True)
        return

    if total_power(attacker) < 5:
        await query.answer("ارتشت خیلی ضعیفه! از فروشگاه نیرو بخر.", show_alert=True)
        return

    defender = data[target_uid]
    defender_name = defender["name"]

    attacker["last_attack"] = now
    save_data(data)
    await query.answer()

    chat_id = query.message.chat_id
    msg = await context.bot.send_message(chat_id, f"⚔️ حمله به <b>{esc(defender_name)}</b> آغاز شد...", parse_mode="HTML")
    await asyncio.sleep(1.1)

    await msg.edit_text(
        f"⚔️ حمله به <b>{esc(defender_name)}</b> آغاز شد...\n🪖 پیاده‌نظام در حال پیشروی به خط مقدم...",
        parse_mode="HTML",
    )
    await asyncio.sleep(1.1)

    jet_line = "🛩 جنگنده‌ها وارد آسمون نبرد شدند..." if attacker["units"]["jet"] > 0 else "🚛 تانک‌ها موضع گرفتند..."
    await msg.edit_text(
        f"⚔️ حمله به <b>{esc(defender_name)}</b> آغاز شد...\n🪖 پیاده‌نظام در حال پیشروی...\n{jet_line}",
        parse_mode="HTML",
    )
    await asyncio.sleep(1.1)

    await msg.edit_text(
        f"⚔️ حمله به <b>{esc(defender_name)}</b> آغاز شد...\n🪖 پیاده‌نظام در حال پیشروی...\n{jet_line}\n💥 درگیری شدید در جریانه...",
        parse_mode="HTML",
    )
    await asyncio.sleep(1.4)

    attack_power = total_power(attacker) * random.uniform(0.8, 1.3)
    defense_power = total_power(defender) * random.uniform(0.8, 1.3)

    if attack_power > defense_power:
        stolen_gold = min(defender["resources"]["gold"], random.randint(80, 300))
        stolen_oil = min(defender["resources"]["oil"], random.randint(20, 100))

        defender["resources"]["gold"] -= stolen_gold
        defender["resources"]["oil"] -= stolen_oil
        attacker["resources"]["gold"] += stolen_gold
        attacker["resources"]["oil"] += stolen_oil

        def_losses = apply_losses(defender["units"], 0.08, 0.18)
        atk_losses = apply_losses(attacker["units"], 0.02, 0.08)

        attacker["wins"] += 1
        defender["losses"] += 1
        save_data(data)

        final_text = (
            f"🏆 <b>پیروزی!</b> ارتش تو {esc(defender_name)} رو شکست داد!\n"
            f"{DIVIDER}\n"
            f"💰 غنیمت: {stolen_gold} طلا | 🛢 {stolen_oil} نفت\n"
            f"🪖 تلفات تو: {losses_text(atk_losses)}\n"
            f"🪖 تلفات حریف: {losses_text(def_losses)}"
        )
    else:
        atk_losses = apply_losses(attacker["units"], 0.1, 0.22)
        attacker["losses"] += 1
        defender["wins"] += 1
        save_data(data)

        final_text = (
            f"💥 <b>شکست!</b> ارتش تو در برابر {esc(defender_name)} شکست خورد.\n"
            f"{DIVIDER}\n"
            f"🪖 تلفات تو: {losses_text(atk_losses)}"
        )

    await msg.edit_text(final_text, parse_mode="HTML")
    await context.bot.send_message(chat_id, "منوی اصلی 👇", reply_markup=main_menu_keyboard())


# ---------------------------------------------------------------------------
# جدول برترین‌ها
# ---------------------------------------------------------------------------
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data:
        text = "هنوز هیچ فرمانده‌ای ثبت نشده!"
    else:
        ranked = sorted(data.values(), key=power_score, reverse=True)[:10]
        lines = ["🏆 <b>برترین فرمانده‌های جهان</b>", DIVIDER]
        medals = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(ranked):
            flag = COUNTRIES.get(p.get("country_code"), {}).get("flag", "🏳️")
            rank_icon = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{rank_icon} {flag} <b>{esc(p['name'])}</b> — قدرت {int(power_score(p))} (⚔️{int(total_power(p))})")
        text = "\n".join(lines)

    await respond(update, text, main_menu_keyboard())


# ---------------------------------------------------------------------------
# دکمه‌های شیشه‌ای — مسیریابی
# ---------------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data_str = query.data

    if data_str == "noop":
        await query.answer()
        return

    if data_str.startswith("country_"):
        await query.answer()
        code = data_str[len("country_"):]
        if code in COUNTRIES:
            await choose_country(update, context, code)
        return

    if data_str.startswith("buy_"):
        _, unit, amount = data_str.split("_")
        await buy_unit(update, context, unit, int(amount))
        return

    if data_str.startswith("build_"):
        building = data_str[len("build_"):]
        await build_upgrade(update, context, building)
        return

    if data_str.startswith("attack_"):
        target_uid = data_str[len("attack_"):]
        await do_attack(update, context, target_uid)
        return

    await query.answer()
    routes = {
        "menu": lambda: respond(update, "🌍 <b>منوی اصلی</b>", main_menu_keyboard()),
        "profile": lambda: profile_cmd(update, context),
        "collect": lambda: collect(update, context),
        "shop": lambda: shop(update, context),
        "buildings": lambda: buildings_cmd(update, context),
        "attack_menu": lambda: attack_menu(update, context),
        "leaderboard": lambda: leaderboard(update, context),
        "help": lambda: help_cmd(update, context),
    }
    action = routes.get(data_str)
    if action:
        await action()


# ---------------------------------------------------------------------------
# اجرای ربات
# ---------------------------------------------------------------------------
def main():
    if BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
        raise SystemExit("توکن ربات تنظیم نشده! BOT_TOKEN رو به عنوان متغیر محیطی ست کن.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("collect", collect))
    app.add_handler(CommandHandler("shop", shop))
    app.add_handler(CommandHandler("buildings", buildings_cmd))
    app.add_handler(CommandHandler("attack", attack_menu))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("ربات جنگ جهانی (نسخه کامل با دکمه و انتخاب کشور) در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
