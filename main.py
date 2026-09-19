#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تلگرام: کیف پول دمو + ترید اسپات + فیوچرز
همه‌چیز فرضی است: قیمت‌ها شبیه‌سازی می‌شوند و پول واقعی در کار نیست.

اجرا:
    pip install -r requirements.txt
    export BOT_TOKEN="توکن ربات"
    python bot.py
"""

import asyncio
import json
import logging
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ======================= تنظیمات =======================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DB_FILE = os.environ.get("DB_FILE", "demo_data.json")

START_BALANCE = 10_000.0   # موجودی اولیه دمو (USDT)
SPOT_FEE = 0.001           # کارمزد اسپات: 0.1%
FUT_FEE = 0.0005           # کارمزد فیوچرز: 0.05% از حجم پوزیشن
MAINT_MARGIN = 0.005       # مارجین نگهداری برای لیکوئید: 0.5%
MIN_ORDER = 5.0            # حداقل سفارش (USDT)
MAX_POSITIONS = 10         # حداکثر پوزیشن باز هر کاربر
TICK_SECONDS = 2           # هر چند ثانیه قیمت‌ها تغییر کنند
LEVERAGES = [2, 3, 5, 10, 20, 50, 100]
PERCENTS = [10, 25, 50, 100]

# base = قیمت پایه فرضی ، vol = نوسان در هر تیک
COINS = {
    "BTC": {"base": 95000.0, "vol": 0.0007, "emoji": "🟠"},
    "ETH": {"base": 3200.0, "vol": 0.0009, "emoji": "🔷"},
    "BNB": {"base": 600.0, "vol": 0.0009, "emoji": "🟡"},
    "SOL": {"base": 160.0, "vol": 0.0013, "emoji": "🟣"},
    "TON": {"base": 3.5, "vol": 0.0014, "emoji": "💎"},
    "XRP": {"base": 0.6, "vol": 0.0012, "emoji": "⚪"},
    "DOGE": {"base": 0.15, "vol": 0.0016, "emoji": "🐶"},
}

SIDE_NAME = {"L": "Long 🟢", "S": "Short 🔴"}
TEHRAN = timezone(timedelta(hours=3, minutes=30))

# ======================= قیمت‌های شبیه‌سازی =======================
prices = {s: c["base"] * random.uniform(0.92, 1.08) for s, c in COINS.items()}


def tick():
    """یک قدم حرکت تصادفی برای همه قیمت‌ها (با کشش ملایم به قیمت پایه)."""
    for s, c in COINS.items():
        p = prices[s]
        drift = -0.0002 * math.log(p / c["base"])
        prices[s] = p * math.exp(random.gauss(0, c["vol"]) + drift)


# ======================= دیتابیس (فایل JSON) =======================
db = {"users": {}}


def load_db():
    global db
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
            db.setdefault("users", {})
        except Exception:
            logging.exception("خواندن دیتابیس ناموفق بود؛ با دیتابیس خالی شروع می‌شود")
            db = {"users": {}}


def save_db():
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, DB_FILE)


def new_user():
    return {
        "usdt": START_BALANCE,
        "coins": {},
        "cost": {},
        "positions": [],
        "next_id": 1,
        "realized": 0.0,
        "history": [],
    }


def get_user(uid):
    key = str(uid)
    if key not in db["users"]:
        db["users"][key] = new_user()
        save_db()
    return db["users"][key]


def log(u, text):
    u["history"].append({"t": int(time.time()), "x": text})
    del u["history"][:-50]


# ======================= فرمت‌دهی =======================
def fp(x):
    """قیمت"""
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.3f}"
    return f"{x:,.5f}"


def fm(x):
    """پول"""
    return f"{x:,.2f}"


def sg(x):
    """پول با علامت"""
    return f"{x:+,.2f}"


def fq(x):
    """مقدار ارز"""
    if x >= 100:
        return f"{x:,.2f}"
    return f"{x:,.6f}".rstrip("0").rstrip(".")


def dot(x):
    return "🟢" if x >= 0 else "🔴"


def nt(note):
    return note + "\n\n" if note else ""


# ======================= منطق ترید =======================
def pos_pnl(p, price):
    if p["side"] == "L":
        return (price - p["entry"]) * p["qty"]
    return (p["entry"] - price) * p["qty"]


def calc_liq(price, side, lev):
    if side == "L":
        return price * (1 - 1 / lev + MAINT_MARGIN)
    return price * (1 + 1 / lev - MAINT_MARGIN)


def futures_totals(u):
    margin = sum(p["margin"] for p in u["positions"])
    upnl = sum(pos_pnl(p, prices[p["sym"]]) for p in u["positions"])
    return margin, upnl


def equity(u):
    total = u["usdt"]
    for s, q in u["coins"].items():
        total += q * prices[s]
    for p in u["positions"]:
        total += max(0.0, p["margin"] + pos_pnl(p, prices[p["sym"]]))
    return total


def spot_buy(u, sym, pct):
    price = prices[sym]
    spend = u["usdt"] * pct / 100
    if spend < MIN_ORDER:
        return "❌ موجودی USDT کافی نیست (حداقل سفارش 5 دلار)."
    fee = spend * SPOT_FEE
    qty = (spend - fee) / price
    u["usdt"] -= spend
    u["coins"][sym] = u["coins"].get(sym, 0.0) + qty
    u["cost"][sym] = u["cost"].get(sym, 0.0) + spend
    log(u, f"🟢 خرید {sym}: {fq(qty)} با {fm(spend)}$ (قیمت {fp(price)})")
    return f"✅ خرید انجام شد: {fq(qty)} {sym} با {fm(spend)} USDT (کارمزد {fm(fee)})"


def spot_sell(u, sym, pct):
    hold = u["coins"].get(sym, 0.0)
    if hold <= 0:
        return f"❌ شما {sym} ندارید."
    price = prices[sym]
    qty = hold if pct == 100 else hold * pct / 100
    gross = qty * price
    fee = gross * SPOT_FEE
    net = gross - fee
    cost_total = u["cost"].get(sym, 0.0)
    cost_removed = cost_total if pct == 100 else cost_total * qty / hold
    pnl = net - cost_removed
    u["usdt"] += net
    u["realized"] += pnl
    if pct == 100:
        u["coins"].pop(sym, None)
        u["cost"].pop(sym, None)
    else:
        u["coins"][sym] = hold - qty
        u["cost"][sym] = cost_total - cost_removed
    log(u, f"🔴 فروش {sym}: {fq(qty)} | سود/زیان {sg(pnl)}$")
    return (
        f"✅ فروش انجام شد: {fq(qty)} {sym} به مبلغ {fm(net)} USDT\n"
        f"{dot(pnl)} سود/زیان: {sg(pnl)}"
    )


def open_pos(u, sym, side, lev, pct):
    if len(u["positions"]) >= MAX_POSITIONS:
        return f"❌ حداکثر {MAX_POSITIONS} پوزیشن باز مجاز است."
    budget = u["usdt"] * pct / 100
    if budget < MIN_ORDER:
        return "❌ موجودی USDT کافی نیست (حداقل سفارش 5 دلار)."
    margin = budget / (1 + lev * FUT_FEE)
    notional = margin * lev
    fee = notional * FUT_FEE
    price = prices[sym]
    pos = {
        "id": u["next_id"],
        "sym": sym,
        "side": side,
        "lev": lev,
        "margin": margin,
        "entry": price,
        "qty": notional / price,
        "liq": calc_liq(price, side, lev),
        "t": int(time.time()),
    }
    u["next_id"] += 1
    u["usdt"] -= budget
    u["realized"] -= fee
    u["positions"].append(pos)
    log(u, f"⚡ باز شد #{pos['id']} {sym} {SIDE_NAME[side]} x{lev} | مارجین {fm(margin)}$")
    return (
        f"✅ پوزیشن #{pos['id']} باز شد: {sym} {SIDE_NAME[side]} x{lev}\n"
        f"ورود: {fp(price)} | مارجین: {fm(margin)} | کارمزد: {fm(fee)}\n"
        f"💥 قیمت لیکوئید: {fp(pos['liq'])}"
    )


def close_pos(u, pos):
    price = prices[pos["sym"]]
    pnl = pos_pnl(pos, price)
    fee = pos["qty"] * price * FUT_FEE
    payout = max(0.0, pos["margin"] + pnl - fee)
    u["usdt"] += payout
    u["realized"] += payout - pos["margin"]
    u["positions"].remove(pos)
    net = payout - pos["margin"]
    log(u, f"❌ بسته شد #{pos['id']} {pos['sym']} {SIDE_NAME[pos['side']]} x{pos['lev']} | {sg(net)}$")
    return net


def liquidate(u, pos):
    u["positions"].remove(pos)
    u["realized"] -= pos["margin"]
    log(u, f"💥 لیکوئید #{pos['id']} {pos['sym']} {SIDE_NAME[pos['side']]} x{pos['lev']} | -{fm(pos['margin'])}$")


def close_by_id(u, pid):
    for p in u["positions"]:
        if p["id"] == pid:
            net = close_pos(u, p)
            return f"✅ پوزیشن #{pid} بسته شد.\n{dot(net)} سود/زیان نهایی: {sg(net)}"
    return "❌ این پوزیشن پیدا نشد (شاید قبلاً بسته یا لیکوئید شده)."


def close_all(u):
    if not u["positions"]:
        return "❌ پوزیشن بازی ندارید."
    total = 0.0
    n = len(u["positions"])
    for p in list(u["positions"]):
        total += close_pos(u, p)
    return f"✅ {n} پوزیشن بسته شد.\n{dot(total)} مجموع سود/زیان: {sg(total)}"


# ======================= صفحه‌ها (Views) =======================
def coin_buttons(prefix):
    rows, row = [], []
    for s, c in COINS.items():
        row.append(B(f"{c['emoji']} {s}", callback_data=f"{prefix}:{s}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def prices_text():
    return "\n".join(f"{c['emoji']} {s}: {fp(prices[s])}" for s, c in COINS.items())


def menu_view(u, note=""):
    eq = equity(u)
    pnl = eq - START_BALANCE
    text = (
        nt(note)
        + "🤖 <b>ربات ترید دمو</b>\n\n"
        "⚠️ همه‌چیز اینجا فرضی و آموزشی است؛ قیمت‌ها شبیه‌سازی می‌شوند و پول واقعی در کار نیست.\n\n"
        f"💵 موجودی USDT: <b>{fm(u['usdt'])}</b>\n"
        f"💎 ارزش کل: <b>{fm(eq)}</b> USDT\n"
        f"{dot(pnl)} سود/زیان کل: <b>{sg(pnl)}</b> ({pnl / START_BALANCE * 100:+.2f}%)"
    )
    kb = M(
        [
            [B("💰 کیف پول", callback_data="wallet"), B("📈 اسپات", callback_data="spot")],
            [B("⚡ فیوچرز", callback_data="fut"), B("📊 پوزیشن‌ها", callback_data="pos")],
            [B("🧾 تاریخچه", callback_data="hist"), B("🔄 ریست حساب", callback_data="rst")],
        ]
    )
    return text, kb


def wallet_view(u, note=""):
    eq = equity(u)
    pnl = eq - START_BALANCE
    lines = ["💰 <b>کیف پول دمو</b>\n", f"💵 USDT آزاد: <b>{fm(u['usdt'])}</b>"]
    if u["coins"]:
        lines.append("\n📦 <b>دارایی‌های اسپات</b>")
        for s, q in u["coins"].items():
            val = q * prices[s]
            cp = val - u["cost"].get(s, 0.0)
            lines.append(f"{COINS[s]['emoji']} {s}: {fq(q)} ≈ ${fm(val)}\n   {dot(cp)} {sg(cp)}")
    else:
        lines.append("\n📦 دارایی اسپات ندارید.")
    margin, upnl = futures_totals(u)
    lines.append(
        f"\n⚡ مارجین فیوچرز: {fm(margin)}\n"
        f"{dot(upnl)} سود/زیان باز فیوچرز: {sg(upnl)}"
    )
    lines.append(f"\n🧾 سود/زیان بسته‌شده: {sg(u['realized'])}")
    lines.append("━━━━━━━━━━")
    lines.append(f"💎 ارزش کل: <b>{fm(eq)}</b> USDT")
    lines.append(f"{dot(pnl)} سود/زیان کل: <b>{sg(pnl)}</b> ({pnl / START_BALANCE * 100:+.2f}%)")
    kb = M(
        [
            [B("🔄 بروزرسانی", callback_data="wallet"), B("⬅️ منو", callback_data="menu")],
        ]
    )
    return nt(note) + "\n".join(lines), kb


def spot_list_view(note=""):
    text = nt(note) + "📈 <b>اسپات</b>\nیک ارز انتخاب کنید:\n\n" + prices_text()
    rows = coin_buttons("spc")
    rows.append([B("⬅️ منو", callback_data="menu")])
    return text, M(rows)


def spot_coin_view(u, sym, note=""):
    price = prices[sym]
    hold = u["coins"].get(sym, 0.0)
    val = hold * price
    pnl = val - u["cost"].get(sym, 0.0)
    text = (
        nt(note)
        + f"📈 <b>{sym}/USDT</b> · اسپات\n\n"
        f"💲 قیمت: <b>{fp(price)}</b>\n"
        f"💵 USDT آزاد: {fm(u['usdt'])}\n"
        f"📦 {sym}: {fq(hold)} ≈ ${fm(val)}\n"
    )
    if hold > 0:
        text += f"{dot(pnl)} سود/زیان این دارایی: {sg(pnl)}\n"
    text += "\n🟢 ردیف سبز: خرید با درصدی از USDT\n🔴 ردیف قرمز: فروش درصدی از دارایی"
    kb = M(
        [
            [B(f"🟢 {p}%", callback_data=f"spb:{sym}:{p}") for p in PERCENTS],
            [B(f"🔴 {p}%", callback_data=f"sps:{sym}:{p}") for p in PERCENTS],
            [B("🔄 بروزرسانی", callback_data=f"spc:{sym}"), B("⬅️ اسپات", callback_data="spot")],
        ]
    )
    return text, kb


def fut_list_view(note=""):
    text = nt(note) + "⚡ <b>فیوچرز</b>\nیک ارز انتخاب کنید:\n\n" + prices_text()
    rows = coin_buttons("ftc")
    rows.append([B("📊 پوزیشن‌های من", callback_data="pos"), B("⬅️ منو", callback_data="menu")])
    return text, M(rows)


def fut_side_view(sym):
    text = (
        f"⚡ <b>{sym}/USDT</b> · فیوچرز\n\n"
        f"💲 قیمت: <b>{fp(prices[sym])}</b>\n\n"
        "جهت پوزیشن را انتخاب کنید:"
    )
    kb = M(
        [
            [
                B("🟢 Long (خرید)", callback_data=f"ftd:{sym}:L"),
                B("🔴 Short (فروش)", callback_data=f"ftd:{sym}:S"),
            ],
            [B("🔄 بروزرسانی", callback_data=f"ftc:{sym}"), B("⬅️ فیوچرز", callback_data="fut")],
        ]
    )
    return text, kb


def fut_lev_view(sym, side):
    text = (
        f"⚡ <b>{sym}/USDT</b> · {SIDE_NAME[side]}\n\n"
        f"💲 قیمت: <b>{fp(prices[sym])}</b>\n\n"
        "اهرم (Leverage) را انتخاب کنید:\n"
        "⚠️ اهرم بالاتر = سود و ضرر بزرگ‌تر و لیکوئید نزدیک‌تر"
    )
    btns = [B(f"{lv}x", callback_data=f"ftl:{sym}:{side}:{lv}") for lv in LEVERAGES]
    rows = [btns[i : i + 4] for i in range(0, len(btns), 4)]
    rows.append([B("⬅️ برگشت", callback_data=f"ftc:{sym}")])
    return text, M(rows)


def fut_margin_view(u, sym, side, lev):
    price = prices[sym]
    liq = calc_liq(price, side, lev)
    text = (
        f"⚡ <b>{sym}/USDT</b> · {SIDE_NAME[side]} · {lev}x\n\n"
        f"💲 قیمت: <b>{fp(price)}</b>\n"
        f"💥 قیمت لیکوئید (تقریبی): {fp(liq)}\n"
        f"💵 USDT آزاد: {fm(u['usdt'])}\n\n"
        "چند درصد از موجودی آزاد را وارد پوزیشن کنم؟"
    )
    btns = [
        B(f"{p}% (${fm(u['usdt'] * p / 100)})", callback_data=f"fto:{sym}:{side}:{lev}:{p}")
        for p in PERCENTS
    ]
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    rows.append([B("⬅️ برگشت", callback_data=f"ftd:{sym}:{side}")])
    return text, M(rows)


def positions_view(u, note=""):
    if not u["positions"]:
        text = nt(note) + "📊 <b>پوزیشن‌های باز</b>\n\nپوزیشن بازی ندارید."
        kb = M(
            [
                [B("⚡ باز کردن پوزیشن", callback_data="fut")],
                [B("⬅️ منو", callback_data="menu")],
            ]
        )
        return text, kb
    lines = ["📊 <b>پوزیشن‌های باز</b>\n"]
    total = 0.0
    for p in u["positions"]:
        price = prices[p["sym"]]
        pnl = pos_pnl(p, price)
        total += pnl
        roe = pnl / p["margin"] * 100
        lines.append(
            f"<b>#{p['id']}</b> {COINS[p['sym']]['emoji']} {p['sym']} {SIDE_NAME[p['side']]} x{p['lev']}\n"
            f"   ورود: {fp(p['entry'])} | فعلی: {fp(price)}\n"
            f"   مارجین: {fm(p['margin'])} | 💥 لیکوئید: {fp(p['liq'])}\n"
            f"   {dot(pnl)} {sg(pnl)} ({roe:+.1f}%)\n"
        )
    lines.append(f"{dot(total)} مجموع سود/زیان باز: <b>{sg(total)}</b>")
    btns = [B(f"❌ بستن #{p['id']}", callback_data=f"cls:{p['id']}") for p in u["positions"]]
    rows = [btns[i : i + 2] for i in range(0, len(btns), 2)]
    rows.append([B("❌ بستن همه", callback_data="clsall")])
    rows.append([B("🔄 بروزرسانی", callback_data="pos"), B("⬅️ منو", callback_data="menu")])
    return nt(note) + "\n".join(lines), M(rows)


def history_view(u):
    if not u["history"]:
        text = "🧾 <b>تاریخچه</b>\n\nهنوز معامله‌ای انجام نشده."
    else:
        lines = ["🧾 <b>آخرین معاملات</b>\n"]
        for h in reversed(u["history"][-15:]):
            t = datetime.fromtimestamp(h["t"], TEHRAN).strftime("%m/%d %H:%M")
            lines.append(f"{t}  {h['x']}")
        text = "\n".join(lines)
    return text, M([[B("⬅️ منو", callback_data="menu")]])


def reset_confirm_view():
    text = (
        "🔄 <b>ریست حساب</b>\n\n"
        f"همه دارایی‌ها، پوزیشن‌ها و تاریخچه پاک می‌شود و موجودی دوباره {fm(START_BALANCE)} USDT خواهد شد.\n"
        "مطمئنی؟"
    )
    kb = M(
        [
            [
                B("✅ بله، ریست کن", callback_data="rsty"),
                B("❌ انصراف", callback_data="menu"),
            ]
        ]
    )
    return text, kb


# ======================= هندلرها =======================
def _coin(s):
    if s not in COINS:
        raise ValueError("bad coin")
    return s


def _side(s):
    if s not in ("L", "S"):
        raise ValueError("bad side")
    return s


def _lev(s):
    v = int(s)
    if v not in LEVERAGES:
        raise ValueError("bad lev")
    return v


def _pct(s):
    v = int(s)
    if v not in PERCENTS:
        raise ValueError("bad pct")
    return v


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    text, kb = menu_view(u)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def cmd_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    text, kb = wallet_view(u)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    text, kb = positions_view(u)
    await update.effective_message.reply_text(text, reply_markup=kb, parse_mode="HTML")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    u = get_user(q.from_user.id)
    parts = (q.data or "").split(":")
    cmd = parts[0]
    try:
        if cmd == "menu":
            view = menu_view(u)
        elif cmd == "wallet":
            view = wallet_view(u)
        elif cmd == "spot":
            view = spot_list_view()
        elif cmd == "spc":
            view = spot_coin_view(u, _coin(parts[1]))
        elif cmd == "spb":
            sym = _coin(parts[1])
            note = spot_buy(u, sym, _pct(parts[2]))
            save_db()
            view = spot_coin_view(u, sym, note)
        elif cmd == "sps":
            sym = _coin(parts[1])
            note = spot_sell(u, sym, _pct(parts[2]))
            save_db()
            view = spot_coin_view(u, sym, note)
        elif cmd == "fut":
            view = fut_list_view()
        elif cmd == "ftc":
            view = fut_side_view(_coin(parts[1]))
        elif cmd == "ftd":
            view = fut_lev_view(_coin(parts[1]), _side(parts[2]))
        elif cmd == "ftl":
            view = fut_margin_view(u, _coin(parts[1]), _side(parts[2]), _lev(parts[3]))
        elif cmd == "fto":
            note = open_pos(u, _coin(parts[1]), _side(parts[2]), _lev(parts[3]), _pct(parts[4]))
            save_db()
            view = positions_view(u, note)
        elif cmd == "pos":
            view = positions_view(u)
        elif cmd == "cls":
            note = close_by_id(u, int(parts[1]))
            save_db()
            view = positions_view(u, note)
        elif cmd == "clsall":
            note = close_all(u)
            save_db()
            view = positions_view(u, note)
        elif cmd == "hist":
            view = history_view(u)
        elif cmd == "rst":
            view = reset_confirm_view()
        elif cmd == "rsty":
            db["users"][str(q.from_user.id)] = new_user()
            save_db()
            view = menu_view(db["users"][str(q.from_user.id)], "✅ حساب ریست شد.")
        else:
            view = menu_view(u)
    except (ValueError, IndexError, KeyError):
        view = menu_view(u, "❌ درخواست نامعتبر.")

    text, kb = view
    try:
        await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logging.warning("edit failed: %s", e)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Unhandled error", exc_info=context.error)


# ======================= حلقه بازار (قیمت + لیکوئید) =======================
async def market_loop(app: Application):
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            tick()
            notices = []
            for uid, u in list(db["users"].items()):
                for pos in list(u["positions"]):
                    price = prices[pos["sym"]]
                    hit = price <= pos["liq"] if pos["side"] == "L" else price >= pos["liq"]
                    if hit and pos in u["positions"]:
                        liquidate(u, pos)
                        notices.append(
                            (
                                int(uid),
                                f"💥 <b>لیکوئید شد!</b>\n"
                                f"پوزیشن #{pos['id']} {pos['sym']} {SIDE_NAME[pos['side']]} x{pos['lev']}\n"
                                f"قیمت لیکوئید: {fp(pos['liq'])}\n"
                                f"🔴 مارجین از دست رفت: -{fm(pos['margin'])} USDT",
                            )
                        )
            if notices:
                save_db()
                for chat_id, text in notices:
                    try:
                        await app.bot.send_message(chat_id, text, parse_mode="HTML")
                    except Exception as e:
                        logging.warning("ارسال پیام لیکوئید ناموفق: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("market loop error")


async def post_init(app: Application):
    app.bot_data["market_task"] = asyncio.create_task(market_loop(app))


async def post_shutdown(app: Application):
    task = app.bot_data.get("market_task")
    if task:
        task.cancel()


# ======================= اجرا =======================
def main():
    if not BOT_TOKEN:
        raise SystemExit("متغیر محیطی BOT_TOKEN تنظیم نشده است.")
    load_db()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler(["start", "menu"], cmd_start))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)
    logging.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
