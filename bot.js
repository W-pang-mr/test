'use strict';
/*
 * TON Wallet Notifier Bot v2.1
 * - Instant alerts for incoming / outgoing TON
 * - Balance check + Live Binance price
 * - Total Withdrawn calculator
 * - Per-address remove buttons
 * - Min amount & direction filters
 * - Daily summary
 * Zero dependencies (Node 18+)
 * Welcome screen removed — /start is minimal.
 */

const http = require('http');
const fs = require('fs');
const crypto = require('crypto');

const BOT_TOKEN = process.env.BOT_TOKEN || '';
const TONCENTER_API_KEY = process.env.TONCENTER_API_KEY || '';
const FIREBASE_DB_URL = (process.env.FIREBASE_DB_URL || 'https://walletapp-2f0be-default-rtdb.firebaseio.com').replace(/\/+$/, '');
const FIREBASE_SECRET = process.env.FIREBASE_SECRET || '';
const POLL_MS = Number(process.env.POLL_MS || 1000);
const PORT = Number(process.env.PORT || 3000);
const DATA_FILE = process.env.DATA_FILE || './data.json';
const TONCENTER = 'https://toncenter.com/api/v2';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
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

function nanoToNumber(nano) {
    return Number(BigInt(nano || '0')) / 1e9;
}

let state = { subs: {}, addrs: {}, settings: {}, users: {} };
let dirty = false;
let saving = Promise.resolve();
let tonPriceUsd = 0;
let lastPriceFetch = 0;
let usdToman = 0;
let lastTomanFetch = 0;

function getSettings(chatId) {
    if (!state.settings[chatId]) {
        state.settings[chatId] = { minTon: 0, onlyIn: false, onlyOut: false, lastDaily: '', dayIn: 0, dayOut: 0 };
    }
    return state.settings[chatId];
}

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
    if (data) state = { subs: data.subs || {}, addrs: data.addrs || {}, settings: data.settings || {}, users: data.users || {} };
}

function saveState() {
    saving = saving.then(async () => {
        try {
            const body = JSON.stringify(state);
            if (FIREBASE_DB_URL) {
                const r = await fetch(dbUrl(), { method: 'PUT', headers: { 'content-type': 'application/json' }, body });
                if (!r.ok) throw new Error('Firebase save failed: ' + r.status);
            } else {
                fs.writeFileSync(DATA_FILE, body);
            }
        } catch (e) { console.error('save failed:', e.message); }
    });
    return saving;
}

async function ton(method, params) {
    const url = new URL(TONCENTER + '/' + method);
    Object.keys(params).forEach(k => url.searchParams.set(k, String(params[k])));
    const headers = TONCENTER_API_KEY ? { 'X-API-Key': TONCENTER_API_KEY } : {};
    for (let attempt = 0; attempt < 3; attempt++) {
        const r = await fetch(url, { headers });
        if (r.status === 429) { await sleep(1500 * (attempt + 1)); continue; }
        const j = await r.json().catch(() => null);
        if (!j || !j.ok) throw new Error((j && j.error) || ('toncenter ' + r.status));
        return j.result;
    }
    throw new Error('toncenter rate limited');
}

async function fetchTonPrice() {
    if (Date.now() - lastPriceFetch < 30 * 1000 && tonPriceUsd > 0) return tonPriceUsd;
    try {
        const r = await fetch('https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT');
        if (r.ok) {
            const j = await r.json();
            const p = parseFloat(j.price);
            if (p > 0) { tonPriceUsd = p; lastPriceFetch = Date.now(); return tonPriceUsd; }
        }
    } catch (e) { console.error('Binance price failed', e.message); }
    try {
        const r = await fetch('https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd');
        if (r.ok) {
            const j = await r.json();
            const p = j['the-open-network']?.usd || 0;
            if (p > 0) { tonPriceUsd = p; lastPriceFetch = Date.now(); }
        }
    } catch (e) { console.error('CoinGecko price failed', e.message); }
    return tonPriceUsd;
}

async function fetchUsdToman() {
    if (Date.now() - lastTomanFetch < 5 * 60 * 1000 && usdToman > 0) return usdToman;
    const endpoints = [
        async () => {
            const r = await fetch('https://api.tetherland.com/currencies');
            if (!r.ok) throw new Error('tetherland ' + r.status);
            const j = await r.json();
            const p = parseFloat(j?.data?.currencies?.USDT?.price || j?.data?.USDT?.price || 0);
            if (p > 1000) return p;
            throw new Error('tetherland bad');
        },
        async () => {
            const r = await fetch('https://api.wallex.ir/v1/currencies/stats');
            if (!r.ok) throw new Error('wallex ' + r.status);
            const j = await r.json();
            const list = j?.result?.symbols || j?.result || [];
            const usdt = (Array.isArray(list) ? list : []).find(x => (x.symbol || x.key || '') === 'USDTTMN' || (x.symbol || '') === 'USDT');
            const p = parseFloat(usdt?.stats?.lastPrice || usdt?.lastPrice || usdt?.price || 0);
            if (p > 1000) return p;
            throw new Error('wallex bad');
        }
    ];
    for (const fn of endpoints) {
        try {
            const p = await fn();
            if (p > 0) { usdToman = p; lastTomanFetch = Date.now(); return usdToman; }
        } catch (e) { console.error('toman rate failed', e.message); }
    }
    return usdToman;
}

function formatToman(n) {
    const v = Math.round(Number(n) || 0);
    return v.toLocaleString('en-US');
}

async function showTonPrice(chatId, amount) {
    await fetchTonPrice();
    await fetchUsdToman();
    if (!tonPriceUsd) {
        return send(chatId, '❌ Could not fetch TON price right now. Try again.', { reply_markup: mainKeyboard() });
    }
    const amt = (amount && amount > 0) ? amount : 1;
    const usd = tonPriceUsd * amt;
    let text = '💎 <b>TON Price</b>\n\n';
    text += '📌 Unit: <b>$' + tonPriceUsd.toFixed(4) + '</b> / TON\n';
    if (usdToman > 0) {
        text += '📎 USDT ≈ <b>' + formatToman(usdToman) + '</b> تومان\n';
    }
    text += '\n──────────────\n';
    text += '🔢 Amount: <b>' + amt + ' TON</b>\n';
    text += '💵 <b>$' + usd.toFixed(4) + '</b> USD\n';
    if (usdToman > 0) {
        text += '🇮🇷 <b>' + formatToman(usd * usdToman) + '</b> تومان\n';
    } else {
        text += '🇮🇷 Toman rate unavailable temporarily\n';
    }
    text += '\n📡 Source: Binance TON/USDT';
    if (amt === 1 && !(amount && amount > 0)) {
        text += '\n\n💡 Tip: send <code>1.5 تون</code> to convert any amount';
    }
    return send(chatId, text, { reply_markup: mainKeyboard() });
}

function usdStr(tonAmount) {
    if (!tonPriceUsd) return '';
    const usd = tonAmount * tonPriceUsd;
    return usd >= 0.01 ? '  ≈ $' + usd.toFixed(2) : '';
}

async function tg(method, params) {
    const r = await fetch('https://api.telegram.org/bot' + BOT_TOKEN + '/' + method, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(params || {})
    });
    const j = await r.json();
    if (!j.ok) { const err = new Error(j.description || 'telegram error'); err.code = j.error_code; throw err; }
    return j.result;
}

function mainKeyboard() {
    return {
        inline_keyboard: [
            [ { text: '📋 My Addresses', callback_data: 'my_addresses' }, { text: '💰 Balances', callback_data: 'balances' } ],
            [ { text: '💎 TON Price', callback_data: 'ton_price' }, { text: '📤 Total Withdrawn', callback_data: 'total_out' } ],
            [ { text: '⚙️ Settings', callback_data: 'settings' }, { text: '🔕 Stop All', callback_data: 'stop_all' } ]
        ]
    };
}

function settingsKeyboard(chatId) {
    const s = getSettings(chatId);
    return {
        inline_keyboard: [
            [{ text: 'Min amount: ' + s.minTon + ' TON', callback_data: 'set_min' }],
            [
                { text: s.onlyIn ? '✅ Incoming only' : 'Incoming only', callback_data: 'toggle_in' },
                { text: s.onlyOut ? '✅ Outgoing only' : 'Outgoing only', callback_data: 'toggle_out' }
            ],
            [{ text: '« Back', callback_data: 'back_main' }]
        ]
    };
}

function send(chatId, text, extra) {
    const payload = { chat_id: chatId, text, parse_mode: 'HTML', disable_web_page_preview: true };
    if (extra?.reply_markup) payload.reply_markup = extra.reply_markup;
    return tg('sendMessage', payload);
}

function answerCallback(id, text) {
    return tg('answerCallbackQuery', { callback_query_id: id, text: text || '' }).catch(() => {});
}

async function notify(chatId, text, buttonUrl) {
    try {
        const extra = buttonUrl ? {
            reply_markup: {
                inline_keyboard: [[{ text: 'Open transaction', url: buttonUrl }]]
            }
        } : undefined;
        await send(chatId, text, extra);
    } catch (e) {
        if (e.code === 403) { delete state.subs[chatId]; delete state.settings[chatId]; dirty = true; }
        else console.error('notify failed', chatId, e.message);
    }
}

function commentOf(m) {
    if (!m) return '';
    if (m.message) return String(m.message);
    const d = m.msg_data;
    if (d && d['@type'] === 'msg.dataText' && d.text) {
        try { return Buffer.from(d.text, 'base64').toString('utf8'); } catch (e) {}
    }
    return '';
}

function buildMessages(tx, display, chatId) {
    const s = getSettings(chatId);
    let hashHex = '';
    try { hashHex = Buffer.from(tx.transaction_id.hash, 'base64').toString('hex'); } catch (e) {}
    const txUrl = hashHex ? 'https://tonviewer.com/transaction/' + hashHex : null;

    const result = [];
    const inMsg = tx.in_msg || {};
    const outs = (tx.out_msgs || []).filter(m => m.destination);

    if (inMsg.source && !s.onlyOut) {
        const val = nanoToNumber(inMsg.value);
        if (val > 0 && val >= s.minTon) {
            const c = commentOf(inMsg);
            let msg = '🎉 <b>Received TON</b>\n';
            msg += 'Amount: <b>' + formatTon(inMsg.value) + ' TON</b>';
            if (tonPriceUsd > 0) msg += ' (' + (val * tonPriceUsd).toFixed(2) + ' USD)';
            msg += '\nFrom: <code>' + esc(shortAddr(inMsg.source)) + '</code>';
            if (c) msg += '\nComment: ' + esc(c.slice(0, 120));
            result.push({ text: msg, url: txUrl });
            s.dayIn += val;
        }
    } else if (outs.length && !s.onlyIn) {
        let total = 0n;
        outs.forEach(m => { total += BigInt(m.value || '0'); });
        const val = nanoToNumber(total);
        if (val >= s.minTon) {
            const dest = outs[0] ? shortAddr(outs[0].destination) : '—';
            let msg = '🚀 <b>Sent TON</b>\n';
            msg += 'Amount: <b>' + formatTon(total) + ' TON</b>';
            if (tonPriceUsd > 0) msg += ' (' + (val * tonPriceUsd).toFixed(2) + ' USD)';
            msg += '\nTo: <code>' + esc(dest) + '</code>';
            if (outs.length > 1) msg += ' (+' + (outs.length - 1) + ' more)';
            const c = commentOf(outs[0]);
            if (c) msg += '\nComment: ' + esc(c.slice(0, 120));
            result.push({ text: msg, url: txUrl });
            s.dayOut += val;
        }
    }
    return result;
}


/** Validate Telegram Mini App initData (HMAC-SHA256) */
function validateWebAppInitData(initData) {
    if (!initData || !BOT_TOKEN) return null;
    try {
        const params = new URLSearchParams(initData);
        const hash = params.get('hash');
        if (!hash) return null;
        params.delete('hash');
        const entries = [];
        params.forEach((v, k) => entries.push(k + '=' + v));
        entries.sort();
        const dataCheckString = entries.join('\n');
        const secretKey = crypto.createHmac('sha256', 'WebAppData').update(BOT_TOKEN).digest();
        const calculated = crypto.createHmac('sha256', secretKey).update(dataCheckString).digest('hex');
        if (calculated !== hash) return null;
        // auth_date not older than 24h
        const authDate = Number(params.get('auth_date') || 0);
        if (authDate && Date.now() / 1000 - authDate > 86400) return null;
        let user = null;
        try { user = JSON.parse(params.get('user') || 'null'); } catch (e) { user = null; }
        if (!user || !user.id) return null;
        return { userId: String(user.id), user: user };
    } catch (e) {
        console.error('validateWebAppInitData', e.message);
        return null;
    }
}

/**
 * Link telegram user -> wallet address (public only).
 * Same TG user always maps to the wallet they create/import in the mini-app.
 * Private keys never touch the server.
 */
async function linkTelegramWallet(telegramId, addressInput) {
    const det = await ton('detectAddress', { address: String(addressInput).trim() });
    if (det.test_only) throw new Error('TESTNET');
    const raw = det.raw_form;
    const key = raw.replace(':', '_');
    const display = det.non_bounceable.b64url;

    // drop previous address subscription for this user if different
    const prev = state.users[telegramId];
    if (prev && prev.key && prev.key !== key) {
        try { await unsubscribe(telegramId, prev.key); } catch (e) {}
    }

    if (!state.addrs[key]) {
        let lastLt = '0';
        try {
            const t = await ton('getTransactions', { address: raw, limit: 1 });
            if (t.length) lastLt = t[0].transaction_id.lt;
        } catch (e) {}
        state.addrs[key] = { raw, lastLt };
    }

    state.subs[telegramId] = state.subs[telegramId] || {};
    state.subs[telegramId][key] = display;
    getSettings(telegramId);

    state.users[telegramId] = {
        address: display,
        key: key,
        raw: raw,
        linkedAt: Date.now()
    };
    dirty = true;
    await saveState();
    return { address: display, telegramId: telegramId };
}

async function handleApiRequest(req, res) {
    const url = new URL(req.url || '/', 'http://localhost');
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
    }

    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/health')) {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ ok: true, service: 'ton-wallet-notifier', version: '2.1-link' }));
        return;
    }

    if (req.method === 'GET' && url.pathname === '/api/me') {
        // optional: ?initData= for lookup
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ ok: true, hint: 'POST /api/link-wallet with { initData, address }' }));
        return;
    }

    if (req.method === 'POST' && url.pathname === '/api/link-wallet') {
        let body = '';
        req.on('data', chunk => { body += chunk; if (body.length > 200000) req.destroy(); });
        req.on('end', async () => {
            try {
                const data = JSON.parse(body || '{}');
                const auth = validateWebAppInitData(data.initData || '');
                if (!auth) {
                    res.writeHead(401, { 'content-type': 'application/json' });
                    res.end(JSON.stringify({ ok: false, error: 'invalid_init_data' }));
                    return;
                }
                if (!data.address || String(data.address).length < 20) {
                    res.writeHead(400, { 'content-type': 'application/json' });
                    res.end(JSON.stringify({ ok: false, error: 'missing_address' }));
                    return;
                }
                const result = await linkTelegramWallet(auth.userId, data.address);
                res.writeHead(200, { 'content-type': 'application/json' });
                res.end(JSON.stringify({ ok: true, ...result }));
            } catch (e) {
                console.error('link-wallet error', e.message);
                res.writeHead(500, { 'content-type': 'application/json' });
                res.end(JSON.stringify({ ok: false, error: e.message || 'server_error' }));
            }
        });
        return;
    }

    res.writeHead(404, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: 'not_found' }));
}

async function subscribe(chatId, input) {
    const det = await ton('detectAddress', { address: input.trim() });
    if (det.test_only) throw new Error('TESTNET');
    const raw = det.raw_form;
    const key = raw.replace(':', '_');
    const display = det.non_bounceable.b64url;
    if (!state.addrs[key]) {
        let lastLt = '0';
        try { const t = await ton('getTransactions', { address: raw, limit: 1 }); if (t.length) lastLt = t[0].transaction_id.lt; } catch (e) {}
        state.addrs[key] = { raw, lastLt };
    }
    state.subs[chatId] = state.subs[chatId] || {};
    state.subs[chatId][key] = display;
    getSettings(chatId);
    await saveState();
    return display;
}

async function unsubscribe(chatId, keyOrNull) {
    if (!state.subs[chatId]) return 0;
    let count = 0;
    if (keyOrNull) {
        if (state.subs[chatId][keyOrNull]) { delete state.subs[chatId][keyOrNull]; count = 1; }
    } else {
        count = Object.keys(state.subs[chatId]).length;
        delete state.subs[chatId];
    }
    if (state.subs[chatId] && Object.keys(state.subs[chatId]).length === 0) delete state.subs[chatId];
    await saveState();
    return count;
}

async function doSubscribe(chatId, input) {
    try {
        const display = await subscribe(chatId, input);
        await send(chatId, '✅ <b>Alerts activated!</b>\n\n👛 <code>' + esc(display) + '</code>\n\nYou will now receive notifications for every incoming and outgoing TON transaction.', { reply_markup: mainKeyboard() });
    } catch (e) {
        if (e.message === 'TESTNET') await send(chatId, '❌ Testnet addresses are not supported.\nPlease send a <b>mainnet</b> address.');
        else { console.error('subscribe failed', e.message); await send(chatId, '❌ Invalid address or temporary network issue.\nPlease try again.'); }
    }
}

async function showList(chatId) {
    const mine = state.subs[chatId] || {};
    const keys = Object.keys(mine);
    if (!keys.length) return send(chatId, '📭 No active addresses.\nSend a wallet address to start.', { reply_markup: mainKeyboard() });
    let text = '📋 <b>My Active Addresses</b> (' + keys.length + ')\n\n';
    const rows = [];
    keys.forEach((key, i) => { text += (i + 1) + '. <code>' + esc(mine[key]) + '</code>\n\n'; rows.push([{ text: '🗑 Remove #' + (i + 1), callback_data: 'rm_' + key }]); });
    rows.push([{ text: '« Back', callback_data: 'back_main' }]);
    return send(chatId, text, { reply_markup: { inline_keyboard: rows } });
}

async function showBalances(chatId) {
    const mine = state.subs[chatId] || {};
    const keys = Object.keys(mine);
    if (!keys.length) return send(chatId, '📭 No active addresses.\nSend a wallet address first.', { reply_markup: mainKeyboard() });
    await fetchTonPrice();
    let text = '💰 <b>Balances</b>\n';
    text += tonPriceUsd > 0 ? '📈 TON Price: <b>$' + tonPriceUsd.toFixed(4) + '</b> (Binance)\n\n' : '\n';
    for (const key of keys) {
        try {
            const bal = await ton('getAddressBalance', { address: state.addrs[key].raw });
            const tonAmt = nanoToNumber(bal);
            text += '👛 <code>' + esc(shortAddr(mine[key])) + '</code>\n   <b>' + formatTon(bal) + ' TON</b>' + usdStr(tonAmt) + '\n\n';
        } catch (e) { text += '👛 <code>' + esc(shortAddr(mine[key])) + '</code>\n   ⚠️ Failed to fetch\n\n'; }
        await sleep(300);
    }
    return send(chatId, text, { reply_markup: mainKeyboard() });
}

async function showTotalWithdrawn(chatId) {
    const mine = state.subs[chatId] || {};
    const keys = Object.keys(mine);
    if (!keys.length) return send(chatId, '📭 No active addresses.\nSend a wallet address first.', { reply_markup: mainKeyboard() });
    await fetchTonPrice();
    let text = '📤 <b>Total Withdrawn</b>\n';
    text += tonPriceUsd > 0 ? '📈 TON Price: <b>$' + tonPriceUsd.toFixed(4) + '</b>\n\n' : '\n';
    let grandTotal = 0;
    for (const key of keys) {
        try {
            const txs = await ton('getTransactions', { address: state.addrs[key].raw, limit: 50 });
            let outSum = 0;
            for (const tx of txs) {
                const outs = (tx.out_msgs || []).filter(m => m.destination);
                if (outs.length) {
                    let total = 0n;
                    outs.forEach(m => { total += BigInt(m.value || '0'); });
                    outSum += nanoToNumber(total);
                }
            }
            grandTotal += outSum;
            text += '👛 <code>' + esc(shortAddr(mine[key])) + '</code>\n   <b>−' + outSum.toFixed(4) + ' TON</b>' + usdStr(outSum) + '\n   <i>(last 50 txs)</i>\n\n';
        } catch (e) { text += '👛 <code>' + esc(shortAddr(mine[key])) + '</code>\n   ⚠️ Failed to fetch\n\n'; }
        await sleep(400);
    }
    text += '──────────────\n📊 <b>Grand Total: −' + grandTotal.toFixed(4) + ' TON</b>' + usdStr(grandTotal);
    return send(chatId, text, { reply_markup: mainKeyboard() });
}

async function showSettings(chatId) {
    const s = getSettings(chatId);
    const text = '⚙️ <b>Settings</b>\n\n• Minimum amount: <b>' + s.minTon + ' TON</b>\n• Incoming only: <b>' + (s.onlyIn ? 'Yes' : 'No') + '</b>\n• Outgoing only: <b>' + (s.onlyOut ? 'Yes' : 'No') + '</b>\n\nTap the buttons below to change.';
    return send(chatId, text, { reply_markup: settingsKeyboard(chatId) });
}

async function handleCallback(cq) {
    const chatId = cq.message.chat.id;
    const data = cq.data || '';
    if (data === 'my_addresses') { await answerCallback(cq.id); return showList(chatId); }
    if (data === 'balances') { await answerCallback(cq.id, 'Fetching...'); return showBalances(chatId); }
    if (data === 'ton_price') { await answerCallback(cq.id, 'Fetching...'); return showTonPrice(chatId); }
    if (data === 'total_out') { await answerCallback(cq.id, 'Calculating...'); return showTotalWithdrawn(chatId); }
    if (data === 'settings') { await answerCallback(cq.id); return showSettings(chatId); }
    if (data === 'stop_all') {
        const n = await unsubscribe(chatId, null);
        await answerCallback(cq.id, n ? 'Alerts stopped' : 'No active alerts');
        return send(chatId, n ? '🔕 All alerts have been turned off.' : 'No active alerts found.', { reply_markup: mainKeyboard() });
    }
    if (data === 'back_main') {
        await answerCallback(cq.id);
        return send(chatId, 'Choose an option:', { reply_markup: mainKeyboard() });
    }
    if (data.startsWith('rm_')) {
        const key = data.slice(3);
        const n = await unsubscribe(chatId, key);
        await answerCallback(cq.id, n ? 'Removed' : 'Not found');
        return showList(chatId);
    }
    if (data === 'toggle_in') {
        const s = getSettings(chatId); s.onlyIn = !s.onlyIn; if (s.onlyIn) s.onlyOut = false; dirty = true; await saveState();
        await answerCallback(cq.id, s.onlyIn ? 'Incoming only enabled' : 'Incoming only disabled');
        return showSettings(chatId);
    }
    if (data === 'toggle_out') {
        const s = getSettings(chatId); s.onlyOut = !s.onlyOut; if (s.onlyOut) s.onlyIn = false; dirty = true; await saveState();
        await answerCallback(cq.id, s.onlyOut ? 'Outgoing only enabled' : 'Outgoing only disabled');
        return showSettings(chatId);
    }
    if (data === 'set_min') { await answerCallback(cq.id); return send(chatId, 'Send the minimum TON amount (e.g. <code>0.1</code> or <code>0</code> to disable):\n\nReply with a number.'); }
    return answerCallback(cq.id);
}

async function handleMessage(msg) {
    if (!msg.chat || msg.chat.type !== 'private') return;
    const chatId = msg.chat.id;
    const text = (msg.text || '').trim();
    if (!text) return;
    const low = text.toLowerCase().trim();

    let amtParsed = null;
    let m1 = text.trim().match(/^(\d+(?:\.\d+)?)\s*(?:تون|ton)$/i);
    if (m1) amtParsed = parseFloat(m1[1]);
    else {
        let m2 = text.trim().match(/^(?:تون|ton)\s*(\d+(?:\.\d+)?)$/i);
        if (m2) amtParsed = parseFloat(m2[1]);
    }
    if (amtParsed !== null && amtParsed > 0 && amtParsed < 1e12) {
        return showTonPrice(chatId, amtParsed);
    }
    if (low === 'تون' || low === 'ton' || low === 'قیمت تون' || low === 'قیمت' || low === '/ton' || low === '/price') {
        return showTonPrice(chatId);
    }
    if (/^\d+(\.\d+)?$/.test(text)) {
        const val = parseFloat(text);
        if (val >= 0 && val < 1000000) {
            const s = getSettings(chatId); s.minTon = val; dirty = true; await saveState();
            return send(chatId, '✅ Minimum amount set to <b>' + val + ' TON</b>', { reply_markup: mainKeyboard() });
        }
    }
    const parts = text.split(/\s+/);
    const cmd = parts[0].toLowerCase().split('@')[0];
    const arg = parts.slice(1).join(' ');

    if (cmd === '/start') {
        if (arg) return doSubscribe(chatId, arg);
        const linked = state.users[String(chatId)];
        if (linked && linked.address) {
            return send(chatId,
                '✅ Linked wallet\n👛 <code>' + esc(linked.address) + '</code>\n\nAlerts are active. Use the buttons below.',
                { reply_markup: mainKeyboard() });
        }
        return send(chatId, 'Open the wallet mini-app once to link automatically, or send a UQ…/EQ… address.', { reply_markup: mainKeyboard() });
    }
    if (cmd === '/help') {
        return send(chatId, 'Send address to subscribe.\nButtons: balances, price, settings, stop.', { reply_markup: mainKeyboard() });
    }
    if (cmd === '/list') return showList(chatId);
    if (cmd === '/balance' || cmd === '/balances') return showBalances(chatId);
    if (cmd === '/settings') return showSettings(chatId);
    if (cmd === '/stop') {
        try {
            const n = await unsubscribe(chatId, null);
            return send(chatId, n ? '🔕 All alerts have been turned off.' : 'No active alerts found.', { reply_markup: mainKeyboard() });
        } catch (e) {
            return send(chatId, '❌ Something went wrong.');
        }
    }
    if (/^[A-Za-z0-9_\-+\/=:]{40,70}$/.test(text)) return doSubscribe(chatId, text);
    return send(chatId, 'Send a TON address or use the buttons.', { reply_markup: mainKeyboard() });
}

async function checkDailySummaries() {
    const today = new Date().toISOString().slice(0, 10);
    for (const chatId of Object.keys(state.subs)) {
        const s = getSettings(chatId);
        if (s.lastDaily === today) continue;
        if (s.dayIn === 0 && s.dayOut === 0) { s.lastDaily = today; continue; }
        await fetchTonPrice();
        const text = '📊 <b>Daily Summary</b> (' + today + ')\n\n🟢 Received: <b>+' + s.dayIn.toFixed(4) + ' TON</b>' + usdStr(s.dayIn) +
            '\n🔴 Sent: <b>−' + s.dayOut.toFixed(4) + ' TON</b>' + usdStr(s.dayOut) +
            '\n📈 Net: <b>' + (s.dayIn - s.dayOut >= 0 ? '+' : '') + (s.dayIn - s.dayOut).toFixed(4) + ' TON</b>';
        await notify(chatId, text);
        s.dayIn = 0; s.dayOut = 0; s.lastDaily = today; dirty = true;
    }
    if (dirty) await saveState();
}

async function updatesLoop() {
    let offset = 0;
    for (;;) {
        try {
            const updates = await tg('getUpdates', { offset, timeout: 50, allowed_updates: ['message', 'callback_query'] });
            for (const u of updates) {
                offset = u.update_id + 1;
                if (u.message) handleMessage(u.message).catch(e => console.error('handler error', e.message));
                else if (u.callback_query) handleCallback(u.callback_query).catch(e => console.error('callback error', e.message));
            }
        } catch (e) { console.error('getUpdates error', e.message); await sleep(3000); }
    }
}


/** Pull pending links written by the mini-app directly to Firebase */
async function processPendingLinks() {
    if (!FIREBASE_DB_URL) return;
    try {
        const url = FIREBASE_DB_URL + '/wallet_notifier/pending_links.json' +
            (FIREBASE_SECRET ? '?auth=' + encodeURIComponent(FIREBASE_SECRET) : '');
        const r = await fetch(url);
        if (!r.ok) return;
        const pending = await r.json();
        if (!pending || typeof pending !== 'object') return;
        for (const telegramId of Object.keys(pending)) {
            const item = pending[telegramId];
            const address = item && (item.address || item);
            if (!address || typeof address !== 'string') continue;
            try {
                await linkTelegramWallet(String(telegramId), address);
                console.log('pending link applied', telegramId, address.slice(0, 8) + '...');
            } catch (e) {
                console.error('pending link failed', telegramId, e.message);
                continue;
            }
            // remove this pending entry
            try {
                const delUrl = FIREBASE_DB_URL + '/wallet_notifier/pending_links/' + encodeURIComponent(telegramId) + '.json' +
                    (FIREBASE_SECRET ? '?auth=' + encodeURIComponent(FIREBASE_SECRET) : '');
                await fetch(delUrl, { method: 'DELETE' });
            } catch (e) {}
        }
    } catch (e) {
        console.error('processPendingLinks', e.message);
    }
}

async function pollOnce() {
    await processPendingLinks();
    const keys = Object.keys(state.addrs);
    for (const key of keys) {
        const a = state.addrs[key];
        if (!a) continue;
        const chats = Object.keys(state.subs).filter(c => state.subs[c] && state.subs[c][key]);
        if (!chats.length) { delete state.addrs[key]; dirty = true; continue; }
        try {
            const txs = await ton('getTransactions', { address: a.raw, limit: 15 });
            const last = BigInt(a.lastLt || '0');
            const fresh = txs.filter(t => BigInt(t.transaction_id.lt) > last).sort((x, y) => BigInt(x.transaction_id.lt) < BigInt(y.transaction_id.lt) ? -1 : 1);
            for (const tx of fresh) {
                for (const chatId of chats) {
                    if (!state.subs[chatId]) continue;
                    const msgs = buildMessages(tx, state.subs[chatId][key], chatId);
                    for (const m of msgs) await notify(chatId, m.text, m.url);
                }
                a.lastLt = tx.transaction_id.lt; dirty = true;
            }
        } catch (e) { console.error('poll error', key, e.message); }
        await sleep(TONCENTER_API_KEY ? 150 : 1000);
    }
    if (dirty) { dirty = false; await saveState(); }
}

async function pollLoop() {
    let lastDailyCheck = 0;
    for (;;) {
        const t0 = Date.now();
        try {
            await pollOnce();
            if (Date.now() - lastDailyCheck > 30 * 60 * 1000) { await checkDailySummaries(); lastDailyCheck = Date.now(); }
        } catch (e) { console.error('pollOnce error', e.message); }
        await sleep(Math.max(500, POLL_MS - (Date.now() - t0)));
    }
}

async function main() {
    if (!BOT_TOKEN) { console.error('BOT_TOKEN is not set'); process.exit(1); }
    await loadState();
    await fetchTonPrice();
    http.createServer((req, res) => { handleApiRequest(req, res); }).listen(PORT);
    await tg('deleteWebhook', {}).catch(() => {});
    const me = await tg('getMe');
    console.log('Bot started: @' + me.username + ' | storage: ' + (FIREBASE_DB_URL ? 'firebase' : 'file') + ' | TON price: $' + tonPriceUsd);
    updatesLoop();
    pollLoop();
}

process.on('unhandledRejection', e => console.error('unhandledRejection', e));
if (require.main === module) main().catch(e => { console.error(e); process.exit(1); });
else module.exports = { buildMessages, formatTon };
