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

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function esc(s) {
    return String(s ?? '')
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

function nanoToNumber(nano) {
    return Number(BigInt(nano || '0')) / 1e9;
}

let state = { subs: {}, addrs: {}, settings: {} };
let dirty = false;
let saving = Promise.resolve();
let tonPriceUsd = 0;
let lastPriceFetch = 0;

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
    if (data) state = { subs: data.subs || {}, addrs: data.addrs || {}, settings: data.settings || {} };
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
    if (Date.now() - lastPriceFetch < 60 * 1000 && tonPriceUsd > 0) return tonPriceUsd;
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
            [ { text: '📤 Total Withdrawn', callback_data: 'total_out' }, { text: '⚙️ Settings', callback_data: 'settings' } ],
            [ { text: '🔕 Stop All', callback_data: 'stop_all' } ]
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

async function notify(chatId, text) {
    try { await send(chatId, text); }
    catch (e) {
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
    const txLink = hashHex ? '\n\n<a href="https://tonviewer.com/transaction/' + hashHex + '">Open transaction</a>' : '';

    const result = [];
    const inMsg = tx.in_msg || {};
    const outs = (tx.out_msgs || []).filter(m => m.destination);

    if (inMsg.source && !s.onlyOut) {
        const val = nanoToNumber(inMsg.value);
        if (val > 0 && val >= s.minTon) {
            const c = commentOf(inMsg);
            let msg = '🟢 <b>Received TON</b>\n';
            msg += 'Amount: <b>' + formatTon(inMsg.value) + ' TON</b>';
            if (tonPriceUsd > 0) msg += ' (' + (val * tonPriceUsd).toFixed(2) + ' USD)';
            msg += '\nFrom: <code>' + esc(shortAddr(inMsg.source)) + '</code>';
            if (c) msg += '\nComment: ' + esc(c.slice(0, 120));
            msg += txLink;
            result.push(msg);
            s.dayIn += val;
        }
    } else if (outs.length && !s.onlyIn) {
        let total = 0n;
        outs.forEach(m => { total += BigInt(m.value || '0'); });
        const val = nanoToNumber(total);
        if (val >= s.minTon) {
            const dest = outs[0] ? shortAddr(outs[0].destination) : '—';
            let msg = '🔴 <b>Sent TON</b>\n';
            msg += 'Amount: <b>' + formatTon(total) + ' TON</b>';
            if (tonPriceUsd > 0) msg += ' (' + (val * tonPriceUsd).toFixed(2) + ' USD)';
            msg += '\nTo: <code>' + esc(dest) + '</code>';
            if (outs.length > 1) msg += ' (+' + (outs.length - 1) + ' more)';
            const c = commentOf(outs[0]);
            if (c) msg += '\nComment: ' + esc(c.slice(0, 120));
            msg += txLink;
            result.push(msg);
            s.dayOut += val;
        }
    }
    return result;
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

const WELCOME =
    '👋 <b>TON Wallet Notifier</b>\n📦 Version <b>2.1</b>\n\n' +
    'Get instant alerts when TON is <b>received</b> or <b>sent</b> from your wallet.\n\n' +
    '📌 <b>How to use</b>\nJust send your wallet address (e.g. <code>UQ...</code>)\n\n' +
    '✨ <b>Features</b>\n• 🔔 Real-time transaction alerts\n• 💰 Balance checker\n• 📈 Live TON price (Binance)\n' +
    '• 💵 USD value display\n• 📤 Total withdrawn calculator\n• ⚙️ Min amount & direction filters\n• 📊 Daily summary\n• 🗑 Easy address management';

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
    if (!keys.length) return send(chatId, '📭 You have no active addresses yet.\n\nSend a wallet address to start receiving alerts.', { reply_markup: mainKeyboard() });
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
    if (data === 'total_out') { await answerCallback(cq.id, 'Calculating...'); return showTotalWithdrawn(chatId); }
    if (data === 'settings') { await answerCallback(cq.id); return showSettings(chatId); }
    if (data === 'stop_all') {
        const n = await unsubscribe(chatId, null);
        await answerCallback(cq.id, n ? 'Alerts stopped' : 'No active alerts');
        return send(chatId, n ? '🔕 All alerts have been turned off.' : 'No active alerts found.', { reply_markup: mainKeyboard() });
    }
    if (data === 'back_main') { await answerCallback(cq.id); return send(chatId, WELCOME, { reply_markup: mainKeyboard() }); }
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
    if (cmd === '/start') { if (arg) return doSubscribe(chatId, arg); return send(chatId, WELCOME, { reply_markup: mainKeyboard() }); }
    if (cmd === '/help') return send(chatId, WELCOME, { reply_markup: mainKeyboard() });
    if (cmd === '/list') return showList(chatId);
    if (cmd === '/balance' || cmd === '/balances') return showBalances(chatId);
    if (cmd === '/settings') return showSettings(chatId);
    if (cmd === '/stop') {
        try { const n = await unsubscribe(chatId, null); return send(chatId, n ? '🔕 All alerts have been turned off.' : 'No active alerts found.', { reply_markup: mainKeyboard() }); }
        catch (e) { return send(chatId, '❌ Something went wrong.'); }
    }
    if (/^[A-Za-z0-9_\-+\/=:]{40,70}$/.test(text)) return doSubscribe(chatId, text);
    return send(chatId, 'Please send a TON wallet address or use the buttons below.', { reply_markup: mainKeyboard() });
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

async function pollOnce() {
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
                    for (const m of msgs) await notify(chatId, m);
                }
                a.lastLt = tx.transaction_id.lt; dirty = true;
            }
        } catch (e) { console.error('poll error', key, e.message); }
        await sleep(TONCENTER_API_KEY ? 200 : 1200);
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
        await sleep(Math.max(1000, POLL_MS - (Date.now() - t0)));
    }
}

async function main() {
    if (!BOT_TOKEN) { console.error('BOT_TOKEN is not set'); process.exit(1); }
    await loadState();
    await fetchTonPrice();
    http.createServer((req, res) => { res.writeHead(200); res.end('ok'); }).listen(PORT);
    await tg('deleteWebhook', {}).catch(() => {});
    const me = await tg('getMe');
    console.log('Bot started: @' + me.username + ' | storage: ' + (FIREBASE_DB_URL ? 'firebase' : 'file') + ' | TON price: $' + tonPriceUsd);
    updatesLoop();
    pollLoop();
}

process.on('unhandledRejection', e => console.error('unhandledRejection', e));
if (require.main === module) main().catch(e => { console.error(e); process.exit(1); });
else module.exports = { buildMessages, formatTon };
