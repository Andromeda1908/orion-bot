import asyncio
import aiohttp
import json
from datetime import datetime

# ===== НАЛАШТУВАННЯ =====
BOT_TOKEN = "8970243960:AAG0Xn25cPSZPAyllXsuGnNfnH7GUOEgk0k"
CHAT_ID = "1593443250"
CHECK_INTERVAL = 900  # кожні 15 хвилин

PAIRS = [
    "LINKUSDT", "NEARUSDT", "BTCUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "APTUSDT", "ARBUSDT",
    "MNTUSDT", "TRXUSDT", "SUIUSDT"
]

# ===== BYBIT API =====
async def get_klines(session, symbol, interval="60", limit=60):
    url = f"https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }
    async with session.get(url, params=params) as resp:
        data = await resp.json()
        if data["retCode"] == 0:
            return data["result"]["list"]
        return None

async def get_ticker(session, symbol):
    url = f"https://api.bybit.com/v5/market/tickers"
    params = {"category": "linear", "symbol": symbol}
    async with session.get(url, params=params) as resp:
        data = await resp.json()
        if data["retCode"] == 0 and data["result"]["list"]:
            t = data["result"]["list"][0]
            return {
                "price": float(t["lastPrice"]),
                "change24h": float(t["price24hPcnt"]) * 100,
                "high24h": float(t["highPrice24h"]),
                "low24h": float(t["lowPrice24h"]),
                "volume24h": float(t["turnover24h"])
            }
        return None

# ===== ІНДИКАТОРИ =====
def calc_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_sar(highs, lows, af_start=0.02, af_max=0.2):
    if len(highs) < 5:
        return None, None
    # Спрощений SAR
    trend = 1  # 1=up, -1=down
    ep = lows[0]
    sar = highs[0]
    af = af_start
    
    for i in range(2, len(highs)):
        if trend == 1:
            sar = sar + af * (ep - sar)
            if lows[i] < sar:
                trend = -1
                sar = ep
                ep = lows[i]
                af = af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(af + af_start, af_max)
        else:
            sar = sar + af * (ep - sar)
            if highs[i] > sar:
                trend = 1
                sar = ep
                ep = highs[i]
                af = af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(af + af_start, af_max)
    
    return sar, trend

def calc_bb(prices, period=20, std_mult=2):
    if len(prices) < period:
        return None, None, None
    slice_p = prices[-period:]
    mid = sum(slice_p) / period
    variance = sum((p - mid) ** 2 for p in slice_p) / period
    std = variance ** 0.5
    return mid - std_mult * std, mid, mid + std_mult * std

def calc_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    if ema_fast is None or ema_slow is None:
        return None, None
    macd_line = ema_fast - ema_slow
    return macd_line, macd_line  # спрощено

# ===== АНАЛІЗ =====
def analyze(symbol, klines, ticker):
    if not klines or len(klines) < 30:
        return None
    
    # Bybit повертає [time, open, high, low, close, volume] - від нових до старих
    klines = list(reversed(klines))
    
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    
    price = ticker["price"]
    change = ticker["change24h"]
    
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    rsi   = calc_rsi(closes)
    bb_low, bb_mid, bb_high = calc_bb(closes)
    sar, sar_trend = calc_sar(highs, lows)
    macd, _ = calc_macd(closes)
    
    if None in [ema21, ema50, rsi, bb_low, bb_mid, bb_high, sar, sar_trend, macd]:
        return None
    
    # Підрахунок сигналів
    bull_signals = 0
    bear_signals = 0
    
    if sar_trend == 1:   bull_signals += 2
    else:                bear_signals += 2
    
    if price > ema21:    bull_signals += 1
    else:                bear_signals += 1
    
    if price > ema50:    bull_signals += 1
    else:                bear_signals += 1
    
    if price > bb_mid:   bull_signals += 1
    else:                bear_signals += 1
    
    if rsi < 35:         bull_signals += 2  # перепроданість
    elif rsi > 65:       bear_signals += 2  # перекупленість
    
    if macd > 0:         bull_signals += 1
    else:                bear_signals += 1
    
    total = bull_signals + bear_signals
    bull_pct = (bull_signals / total) * 100

    # Визначення сигналу
    signal = None
    
    # ЛОНГ умови
    if bull_pct >= 70 and rsi < 60 and price > ema21:
        sl = round(price * 0.975, 4)   # SL -2.5%
        tp1 = round(price * 1.03, 4)   # TP1 +3%
        tp2 = round(price * 1.06, 4)   # TP2 +6%
        rr = round((tp1 - price) / (price - sl), 2)
        signal = {
            "type": "ЛОНГ 🟢",
            "entry": price,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rr": rr,
            "reason": f"SAR↑ EMA бичачі RSI={rsi:.0f}"
        }
    
    # ШОРТ умови
    elif bear_pct := (bear_signals / total) * 100 if total > 0 else 0:
        if bear_pct >= 70 and rsi > 40 and price < ema21:
            sl = round(price * 1.025, 4)   # SL +2.5%
            tp1 = round(price * 0.97, 4)   # TP1 -3%
            tp2 = round(price * 0.94, 4)   # TP2 -6%
            rr = round((price - tp1) / (sl - price), 2)
            signal = {
                "type": "ШОРТ 🔴",
                "entry": price,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "rr": rr,
                "reason": f"SAR↓ EMA ведмежі RSI={rsi:.0f}"
            }
    
    if signal is None:
        return None
    
    return {
        "symbol": symbol,
        "price": price,
        "change24h": change,
        "signal": signal,
        "ema21": ema21,
        "ema50": ema50,
        "rsi": rsi,
        "bb": (bb_low, bb_mid, bb_high),
        "sar": sar,
        "sar_trend": sar_trend
    }

# ===== TELEGRAM =====
async def send_message(session, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    async with session.post(url, json=payload) as resp:
        return await resp.json()

def format_signal(data):
    s = data["signal"]
    sym = data["symbol"].replace("USDT", "/USDT")
    chg = f"+{data['change24h']:.2f}%" if data['change24h'] > 0 else f"{data['change24h']:.2f}%"
    
    msg = f"""
🔱 <b>ОРІОН СИГНАЛ</b>
━━━━━━━━━━━━━━
<b>{sym}</b> {s['type']}
💰 Ціна: <b>${data['price']}</b> ({chg})

📍 Вхід:  <b>${s['entry']}</b>
🛑 SL:    <b>${s['sl']}</b>
🎯 TP1:   <b>${s['tp1']}</b>
🎯 TP2:   <b>${s['tp2']}</b>
📊 R:R =  <b>{s['rr']}</b>

📈 RSI: {data['rsi']:.0f} | EMA21: ${data['ema21']:.3f}
💡 {s['reason']}
━━━━━━━━━━━━━━
⏰ {datetime.now().strftime('%H:%M %d.%m.%Y')}
"""
    return msg.strip()

# ===== ГОЛОВНИЙ ЦИКЛ =====
async def main():
    print("🔱 Оріон Бот запущено...")
    
    async with aiohttp.ClientSession() as session:
        # Привітання
        await send_message(session, "🔱 <b>Оріон Бот активний</b>\nМоніторинг: " + ", ".join([p.replace("USDT","") for p in PAIRS]))
        
        while True:
            signals_found = 0
            
            for symbol in PAIRS:
                try:
                    klines = await get_klines(session, symbol)
                    ticker = await get_ticker(session, symbol)
                    
                    if not ticker:
                        continue
                    
                    result = analyze(symbol, klines, ticker)
                    
                    if result:
                        msg = format_signal(result)
                        await send_message(session, msg)
                        signals_found += 1
                        await asyncio.sleep(1)
                
                except Exception as e:
                    print(f"Помилка {symbol}: {e}")
                
                await asyncio.sleep(0.5)
            
            if signals_found == 0:
                now = datetime.now().strftime('%H:%M')
                await send_message(session, f"🔍 {now} — Перевірка виконана. Чітких сигналів немає. Чекаємо.")
            
            print(f"Перевірка завершена. Сигналів: {signals_found}. Наступна через {CHECK_INTERVAL//60} хв.")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
