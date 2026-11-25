from apscheduler.schedulers.blocking import BlockingScheduler
import yfinance as yf
from datetime import datetime
import pytz
import pandas as pd

SAST = pytz.timezone('Africa/Johannesburg')
PAIR = "EURUSD=X"

best_10am = []
best_230pm = []

def get_signal():
    try:
        df = yf.download(PAIR, period="2d", interval="5m", progress=False, auto_adjust=True)
        if len(df) < 60:
            return None
        
        df['EMA20'] = df['Close'].ewm(span=20).mean()
        df['EMA50'] = df['Close'].ewm(span=50).mean()
        df['ATR'] = (df['High'] - df['Low']).rolling(14).mean()
        
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        price = float(last['Close'])
        rsi = float(last['RSI'])
        atr_pips = float(last['ATR']) * 10000
        ema20 = float(last['EMA20'])
        ema50 = float(last['EMA50'])
        ema_gap = abs(ema20 - ema50) / price * 10000  # in pips

        ema_bull = ema20 > ema50
        momentum_up = price > float(prev['Close'])

        signal = "NO TRADE"
        score = 0

        if ema_bull and rsi > 52 and rsi < 75:
            signal = "BUY"
            score = ema_gap + (rsi - 50) + (atr_pips * 0.5)
        elif not ema_bull and rsi < 48 and rsi > 25:
            signal = "SELL"
            score = ema_gap + (50 - rsi) + (atr_pips * 0.5)

        # Balanced confidence for 0.4 lots
        if atr_pips > 10:
            conf = "STRONG - Good for 0.4 lots $120"
        elif atr_pips > 6:
            conf = "NORMAL - Can hit 30 pips"
        else:
            conf = "LOW VOL - May take longer, still valid"

        return {
            "signal": signal,
            "price": price,
            "rsi": rsi,
            "atr": atr_pips,
            "ema_gap": ema_gap,
            "conf": conf,
            "score": score,
            "time": datetime.now(SAST).strftime("%H:%M:%S")
        }
    except Exception as e:
        print(f"Error in get_signal: {e}", flush=True)
        return None

def scan_10am_window():
    global best_10am
    res = get_signal()
    if not res: return
    print(f"[{res['time']}] 10AM SCAN: {res['signal']} @ {res['price']:.5f} | RSI {res['rsi']:.1f} | ATR {res['atr']:.1f} pips | EMA gap {res['ema_gap']:.1f} pips | {res['conf']} | Score {res['score']:.1f}", flush=True)
    if res['signal'] in ["BUY", "SELL"]:
        best_10am.append(res)
        best = sorted(best_10am, key=lambda x: x['score'], reverse=True)[0]
        print(f"  >> BEST SO FAR 10AM: {best['signal']} @ {best['price']:.5f} | {best['conf']} -> PLACE NOW, you have until 10:00!", flush=True)

def scan_230pm_window():
    global best_230pm
    res = get_signal()
    if not res: return
    print(f"[{res['time']}] 2:30PM SCAN: {res['signal']} @ {res['price']:.5f} | RSI {res['rsi']:.1f} | ATR {res['atr']:.1f} pips | EMA gap {res['ema_gap']:.1f} pips | {res['conf']} | Score {res['score']:.1f}", flush=True)
    if res['signal'] in ["BUY", "SELL"]:
        best_230pm.append(res)
        best = sorted(best_230pm, key=lambda x: x['score'], reverse=True)[0]
        print(f"  >> BEST SO FAR 2:30PM: {best['signal']} @ {best['price']:.5f} | {best['conf']} -> PLACE NOW, you have until 14:30!", flush=True)

def reset_10am():
    global best_10am
    if best_10am:
        best = sorted(best_10am, key=lambda x: x['score'], reverse=True)[0]
        print(f"\n=== FINAL 10AM ENTRY (09:55): {best['signal']} @ {best['price']:.5f} | {best['conf']} | TP 30pips $120 / SL 17.5pips $70 ===\n", flush=True)
    else:
        print("\n=== 10AM: No strong signal found, check last scan ===\n", flush=True)
    best_10am = []

def reset_230pm():
    global best_230pm
    if best_230pm:
        best = sorted(best_230pm, key=lambda x: x['score'], reverse=True)[0]
        print(f"\n=== FINAL 2:30PM ENTRY (14:25): {best['signal']} @ {best['price']:.5f} | {best['conf']} | TP 30pips $120 / SL 17.5pips $70 ===\n", flush=True)
    else:
        print("\n=== 2:30PM: No strong signal found, check last scan ===\n", flush=True)
    best_230pm = []

sched = BlockingScheduler(timezone=SAST)

# 10AM window 09:40 - 09:55 every 2 min
for m in [40, 42, 44, 46, 48, 50, 52, 54]:
    sched.add_job(scan_10am_window, 'cron', hour=9, minute=m)
sched.add_job(reset_10am, 'cron', hour=9, minute=56)

# 2:30PM window 14:10 - 14:25 every 2 min
for m in [10, 12, 14, 16, 18, 20, 22, 24]:
    sched.add_job(scan_230pm_window, 'cron', hour=14, minute=m)
sched.add_job(reset_230pm, 'cron', hour=14, minute=26)

print("PWP Bot LIVE - EURUSD ONLY - Balanced for 0.4 lots", flush=True)
print("Scanning: 09:40-09:55 & 14:10-14:25 SAST - 5-10min leeway to place", flush=True)
print("TP $120 (30 pips) / SL $70 (17.5 pips) - Trades every day", flush=True)
sched.start()
