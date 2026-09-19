'use strict';
/*
 * TON Wallet Notifier Bot
 * - Sends Telegram notifications when TON is received or sent from watched wallets.
 * - Zero dependencies (Node 18+).
 * - Storage: Firebase Realtime Database (optional) or local data.json
 *
 * Environment Variables:
 *   BOT_TOKEN          (required) Bot token from @BotFather
 *   FIREBASE_DB_URL    (optional) e.g. https://xxxx-default-rtdb.firebaseio.com
 *   FIREBASE_SECRET    (optional) Database secret for write access
 *   TONCENTER_API_KEY  (optional) API key from @tonapibot for higher rate limits
 *   POLL_MS            (optional) Polling interval in ms, default 12000
 */

const http = require('http');
const fs = require('fs');

const BOT_TOKEN = process.env.BOT_TOKEN || '';
const TONCENTER_API_KEY = process.env.TONCENTER_API_KEY || '';
const FIREBASE_DB_URL = (process.env.FIREBASE_DB_URL || '').replace(/\/+$/, '');
const FIREBASE_SECRET = process.env.FIREBASE_SECRET || '';
const POLL_MS = Number(process.env.POLL_MS || 12000);
const PORT = Number(process.env.PORT || 3000);
const DATA_FILE = process.env.DATA_FILE || './data.json';
const TONCENTER = 'https://toncenter.com/api/v2';

// ---------- helpers ----------
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function esc(s) {
    return String(s === undefined || s === null ? '' : s)
        .replace(/&/g, '&')
        .replace(/</g, '<')
        .replace(/>/g, '>');
}

function shortAddr(a) {
    a = String(a || '');
    return a.length > 12 ? a.slice(0, 6) + '...' + a.slice(-4) : a;
}

function formatTon(nano) {
    const b = BigInt(nano || '0');
    const whole = b / 1000000000n;
    const frac = (b % 1000000000n).toString().padStart(9, '0').replace(/0+$/, '');
    return frac ? whole + '.' + frac : String(whole);
}

// ---------- state ----------
// subs[chatId][key] = display address
// addrs[key] = { raw, lastLt }
let state = { subs: {}, addrs: {} };
let dirty = false;
let saving = Promise.resolve();

function dbUrl() {
    return FIREBASE_DB_URL + '/wallet_notifier.json' + (FIREBASE_SECRET ? '?auth=' + encodeURIComponent(FIREBASE_SECRET) : '');
}

async function loadState() {
    let data = null;
    if (FIREBASE_DB_URL) {
        const r = await fetch(dbUrl());
        if (!r.ok) throw new Error('Firebase load failed: ' + r.status);
        data = await r.json();
    } else if (fs.existsSync(DATA_FILE)) {
        try { data = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8')); } catch (e) { data = null; }
    }
    if (data) state = { subs: data.subs || {}, addrs: data.addrs || {} };
}

function saveState() {
    saving = saving.then(async function () {
        try {
            const body = JSON.stringify(state);
            if (FIREBASE_DB_URL) {
                const r = await fetch(dbUrl(), { method: 'PUT', headers: { 'content-type': 'application/json' }, body: body });
                if (!r.ok) throw new Error('Firebase save failed: ' + r.status);
            } else {
                fs.writeFileSync(DATA_FILE, body);
            }
        } catch (e) {
            console.error('save failed:', e.message);
        }
    });
    return saving;
}

// ---------- Toncenter ----------
async function ton(method, params) {
    const url = new URL(TONCENTER + '/' + method);
    Object.keys(params).forEach(function (k) { url.searchParams.set(k, String(params[k])); });
    const headers = TONCENTER_API_KEY ? { 'X-API-Key': TONCENTER_API_KEY } : {};
    for (let attempt = 0; attempt < 3; attempt++) {
        const r = await fetch(url, { headers: headers });
        if (r.status === 429) { await sleep(1500 * (attempt + 1)); continue; }
        const j = await r.json().catch(function () { return null; });
        if (!j || !j.ok) throw new Error((j && j.error) || ('toncenter ' + r.status));
        return j.result;
    }
    throw new Error('toncenter rate limited');
}

// ---------- Telegram ----------
async function tg(method, params) {
    const r = await fetch('https://api.telegram.org/bot' + BOT_TOKEN + '/' + method, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(params || {})
    });
    const j = await r.json();
    if (!j.ok) {
        const err = new Error(j.description || 'telegram error');
        err.code = j.error_code;
        throw err;
    }
    return j.result;
}

function mainKeyboard() {
    return {
        inline_keyboard: [
            [{ text: '📋 My Active Addresses', callback_data: 'my_addresses' }],
            [{ text: '🔕 Stop All Alerts', callback_data: 'stop_all' }]
        ]
    };
}

function send(chatId, text, extra) {
    const payload = {
        chat_id: chatId,
        text: text,
        parse_mode: 'HTML',
        disable_web_page_preview: true
    };
    if (extra && extra.reply_markup) payload.reply_markup = extra.reply_markup;
    return tg('sendMessage', payload);
}

function answerCallback(id, text) {
    return tg('answerCallbackQuery', { callback_query_id: id, text: text || '' }).catch(function () { });
}

async function notify(chatId, text) {
    try {
        await send(chatId, text);
    } catch (e) {
        if (e.code === 403) {
            delete state.subs[chatId];
            dirty = true;
        } else {
            console.error('notify failed', chatId, e.message);
        }
    }
}

// ---------- build notification text ----------
function commentOf(m) {
    if (!m) return '';
    if (m.message) return String(m.message);
    const d = m.msg_data;
    if (d && d['@type'] === 'msg.dataText' && d.text) {
        try { return Buffer.from(d.text, 'base64').toString('utf8'); } catch (e) { }
    }
    return '';
}

function buildMessages(tx, display) {
    const when = new Date(tx.utime * 1000).toLocaleString('en-GB', {
        timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
    }) + ' UTC';
    let hashHex = '';
    try { hashHex = Buffer.from(tx.transaction_id.hash, 'base64').toString('hex'); } catch (e) { }

    const footer =
        '\n\n──────────────\n' +
        '👛 <code>' + esc(shortAddr(display)) + '</code>\n' +
        '🕒 ' + esc(when) +
        (hashHex ? '\n🔗 <a href="https://tonviewer.com/transaction/' + hashHex + '">View on Tonviewer</a>' : '');

    const result = [];
    const inMsg = tx.in_msg || {};
    const outs = (tx.out_msgs || []).filter(function (m) { return m.destination; });

    if (inMsg.source) {
        if (BigInt(inMsg.value || '0') > 0n) {
            const c = commentOf(inMsg);
            result.push(
                '🟢 <b>Incoming TON</b>\n\n' +
                '💰 <b>+' + formatTon(inMsg.value) + ' TON</b>\n' +
                '👤 From:\n<code>' + esc(inMsg.source) + '</code>' +
                (c ? '\n💬 ' + esc(c.slice(0, 200)) : '') +
                footer
            );
        }
    } else if (outs.length) {
        let total = 0n;
        outs.forEach(function (m) { total += BigInt(m.value || '0'); });
        const list = outs.slice(0, 4).map(function (m) {
            const c = commentOf(m);
            return '🎯 <code>' + esc(m.destination) + '</code>' +
                (outs.length > 1 ? '  •  ' + formatTon(m.value) + ' TON' : '') +
                (c ? '\n   💬 ' + esc(c.slice(0, 120)) : '');
        }).join('\n\n');
        result.push(
            '🔴 <b>Outgoing TON</b>\n\n' +
            '💸 <b>−' + formatTon(total) + ' TON</b>\n\n' +
            list +
            '\n\n⛽ Fee: ≈' + formatTon(tx.fee) + ' TON' +
            footer
        );
    }
    return result;
}

// ---------- subscribe / unsubscribe ----------
async function subscribe(chatId, input) {
    const det = await ton('detectAddress', { address: input.trim() });
    if (det.test_only) throw new Error('TESTNET');
    const raw = det.raw_form;
    const key = raw.replace(':', '_');
    const display = det.non_bounceable.b64url;

    if (!state.addrs[key]) {
        let lastLt = '0';
        try {
            const t = await ton('getTransactions', { address: raw, limit: 1 });
            if (t.length) lastLt = t[0].transaction_id.lt;
        } catch (e) { }
        state.addrs[key] = { raw: raw, lastLt: lastLt };
    }
    state.subs[chatId] = state.subs[chatId] || {};
    state.subs[chatId][key] = display;
    await saveState();
    return display;
}

async function unsubscribe(chatId, input) {
    if (!state.subs[chatId]) return 0;
    let count = 0;
    if (input) {
        const det = await ton('detectAddress', { address: input.trim() });
        const key = det.raw_form.replace(':', '_');
        if (state.subs[chatId][key]) { delete state.subs[chatId][key]; count = 1; }
    } else {
        count = Object.keys(state.subs[chatId]).length;
        delete state.subs[chatId];
    }
    if (state.subs[chatId] && Object.keys(state.subs[chatId]).length === 0) delete state.subs[chatId];
    await saveState();
    return count;
}

// ---------- commands & callbacks ----------
const WELCOME =
    '👋 <b>Welcome to TON Wallet Notifier</b>\n\n' +
    'Get instant alerts when TON is <b>received</b> or <b>sent</b> from your wallet.\n\n' +
    '📌 <b>How to use</b>\n' +
    'Just send your wallet address (e.g. <code>UQ...</code>)\n\n' +
    '📋 <b>Commands</b>\n' +
    '/list — Show active addresses\n' +
    '/stop — Turn off all alerts\n' +
    '/help — Show this message';

async function doSubscribe(chatId, input) {
    try {
        const display = await subscribe(chatId, input);
        await send(chatId,
            '✅ <b>Alerts activated!</b>\n\n' +
            '👛 <code>' + esc(display) + '</code>\n\n' +
            'You will now receive notifications for every incoming and outgoing TON transaction.',
            { reply_markup: mainKeyboard() }
        );
    } catch (e) {
        if (e.message === 'TESTNET') {
            await send(chatId, '❌ Testnet addresses are not supported.\nPlease send a <b>mainnet</b> address.');
        } else {
            console.error('subscribe failed', e.message);
            await send(chatId, '❌ Invalid address or temporary network issue.\nPlease try again.');
        }
    }
}

async function showList(chatId) {
    const mine = state.subs[chatId] ? Object.keys(state.subs[chatId]).map(function (k) { return state.subs[chatId][k]; }) : [];
    if (!mine.length) {
        return send(chatId, '📭 You have no active addresses yet.\n\nSend a wallet address to start receiving alerts.', { reply_markup: mainKeyboard() });
    }
    return send(chatId,
        '📋 <b>My Active Addresses</b> (' + mine.length + ')\n\n' +
        mine.map(function (a, i) { return (i + 1) + '. <code>' + esc(a) + '</code>'; }).join('\n\n'),
        { reply_markup: mainKeyboard() }
    );
}

async function handleCallback(cq) {
    const chatId = cq.message.chat.id;
    const data = cq.data || '';

    if (data === 'my_addresses') {
        await answerCallback(cq.id);
        return showList(chatId);
    }
    if (data === 'stop_all') {
        const n = await unsubscribe(chatId, null);
        await answerCallback(cq.id, n ? 'Alerts stopped' : 'No active alerts');
        return send(chatId, n ? '🔕 All alerts have been turned off.' : 'No active alerts found.', { reply_markup: mainKeyboard() });
    }
    return answerCallback(cq.id);
}

async function handleMessage(msg) {
    if (!msg.chat || msg.chat.type !== 'private') return;
    const chatId = msg.chat.id;
    const text = (msg.text || '').trim();
    if (!text) return;

    const parts = text.split(/\s+/);
    const cmd = parts[0].toLowerCase().split('@')[0];
    const arg = parts.slice(1).join(' ');

    if (cmd === '/start') {
        if (arg) return doSubscribe(chatId, arg);
        return send(chatId, WELCOME, { reply_markup: mainKeyboard() });
    }
    if (cmd === '/help') return send(chatId, WELCOME, { reply_markup: mainKeyboard() });
    if (cmd === '/list') return showList(chatId);
    if (cmd === '/stop') {
        try {
            const n = await unsubscribe(chatId, arg);
            return send(chatId, n ? '🔕 All alerts have been turned off.' : 'No active alerts found.', { reply_markup: mainKeyboard() });
        } catch (e) {
            return send(chatId, '❌ Invalid address.');
        }
    }
    if (/^[A-Za-z0-9_\-+\/=:]{40,70}$/.test(text)) return doSubscribe(chatId, text);
    return send(chatId, 'Please send a TON wallet address or use /help.', { reply_markup: mainKeyboard() });
}

// ---------- loops ----------
async function updatesLoop() {
    let offset = 0;
    for (;;) {
        try {
            const updates = await tg('getUpdates', {
                offset: offset,
                timeout: 50,
                allowed_updates: ['message', 'callback_query']
            });
            for (const u of updates) {
                offset = u.update_id + 1;
                if (u.message) {
                    handleMessage(u.message).catch(function (e) { console.error('handler error', e.message); });
                } else if (u.callback_query) {
                    handleCallback(u.callback_query).catch(function (e) { console.error('callback error', e.message); });
                }
            }
        } catch (e) {
            console.error('getUpdates error', e.message);
            await sleep(3000);
        }
    }
}

async function pollOnce() {
    const keys = Object.keys(state.addrs);
    for (const key of keys) {
        const a = state.addrs[key];
        if (!a) continue;
        const chats = Object.keys(state.subs).filter(function (c) { return state.subs[c] && state.subs[c][key]; });
        if (!chats.length) { delete state.addrs[key]; dirty = true; continue; }

        try {
            const txs = await ton('getTransactions', { address: a.raw, limit: 15 });
            const last = BigInt(a.lastLt || '0');
            const fresh = txs
                .filter(function (t) { return BigInt(t.transaction_id.lt) > last; })
                .sort(function (x, y) { return BigInt(x.transaction_id.lt) < BigInt(y.transaction_id.lt) ? -1 : 1; });

            for (const tx of fresh) {
                for (const chatId of chats) {
                    if (!state.subs[chatId]) continue;
                    const msgs = buildMessages(tx, state.subs[chatId][key]);
                    for (const m of msgs) await notify(chatId, m);
                }
                a.lastLt = tx.transaction_id.lt;
                dirty = true;
            }
        } catch (e) {
            console.error('poll error', key, e.message);
        }
        await sleep(TONCENTER_API_KEY ? 200 : 1200);
    }
    if (dirty) { dirty = false; await saveState(); }
}

async function pollLoop() {
    for (;;) {
        const t0 = Date.now();
        try { await pollOnce(); } catch (e) { console.error('pollOnce error', e.message); }
        await sleep(Math.max(1000, POLL_MS - (Date.now() - t0)));
    }
}

async function main() {
    if (!BOT_TOKEN) { console.error('BOT_TOKEN is not set'); process.exit(1); }
    await loadState();

    http.createServer(function (req, res) { res.writeHead(200); res.end('ok'); }).listen(PORT);

    await tg('deleteWebhook', {}).catch(function () { });
    const me = await tg('getMe');
    console.log('Bot started: @' + me.username + ' | storage: ' + (FIREBASE_DB_URL ? 'firebase' : 'file'));

    updatesLoop();
    pollLoop();
}

process.on('unhandledRejection', function (e) { console.error('unhandledRejection', e); });

if (require.main === module) {
    main().catch(function (e) { console.error(e); process.exit(1); });
} else {
    module.exports = { buildMessages: buildMessages, formatTon: formatTon };
}
