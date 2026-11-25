from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime
import pytz
import pandas as pd

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')
PAIR = "EURUSD=X"

latest_scan = {"status": "LIVE", "last_update": "Starting...", "10am_best": None, "230pm_best": None, "current_signal": None}
best_10am = []
best_230pm = []

def get_signal():
    try:
        df = yf.download(PAIR, period="2d", interval="5m", progress=False, auto_adjust=True)
        if len(df) < 60:
            return {"signal":"Loading...", "time": datetime.now(SAST).strftime("%H:%M:%S")}
        
        # Fix: flatten if multi-index from yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        
        close = df['Close']
        high = df['High']
        low = df['Low']
        
        df['EMA20'] = close.ewm(span=20).mean()
        df['EMA50'] = close.ewm(span=50).mean()
        df['ATR'] = (high - low).rolling(14).mean()
        
        delta = close.diff()
        gain = delta.where(delta>0, 0).rolling(14).mean()
        loss = -delta.where(delta<0, 0).rolling(14).mean()
        df['RSI'] = 100 - (100 / (1 + gain/loss))
        
        last_close = float(close.iloc[-1])
        last_ema20 = float(df['EMA20'].iloc[-1])
        last_ema50 = float(df['EMA50'].iloc[-1])
        last_atr = float(df['ATR'].iloc[-1])
        last_rsi = float(df['RSI'].iloc[-1])
        
        atr_pips = last_atr * 10000
        ema_gap = abs(last_ema20 - last_ema50) / last_close * 10000
        ema_bull = last_ema20 > last_ema50
        
        signal = "NO TRADE"
        score = 0
        if ema_bull and 52 < last_rsi < 75:
            signal = "BUY"
            score = ema_gap + (last_rsi-50) + atr_pips*0.5
        elif not ema_bull and 25 < last_rsi < 48:
            signal = "SELL"
            score = ema_gap + (50-last_rsi) + atr_pips*0.5
        
        if atr_pips > 10: conf = "STRONG - Good for 0.4 lots $120"
        elif atr_pips > 6: conf = "NORMAL - Can hit 30 pips"
        else: conf = "LOW VOL - Still valid"
        
        return {
            "signal": signal,
            "price": round(last_close,5),
            "rsi": round(last_rsi,1),
            "atr": round(atr_pips,1),
            "ema_gap": round(ema_gap,1),
            "conf": conf,
            "score": round(score,1),
            "time": datetime.now(SAST).strftime("%H:%M:%S")
        }
    except Exception as e:
        return {"signal": "RETRY", "error": str(e)[:100], "time": datetime.now(SAST).strftime("%H:%M:%S")}

def update_live():
    res = get_signal()
    latest_scan["current_signal"] = res
    latest_scan["last_update"] = res.get("time","")
    if res.get("signal") in ["BUY","SELL"]:
        now = datetime.now(SAST)
        if 9 <= now.hour < 10:
            best_10am.append(res)
            latest_scan["10am_best"] = sorted(best_10am, key=lambda x: x['score'], reverse=True)[0]
        if 14 <= now.hour < 15:
            best_230pm.append(res)
            latest_scan["230pm_best"] = sorted(best_230pm, key=lambda x: x['score'], reverse=True)[0]

def reset_10am(): 
    global best_10am; best_10am=[]
def reset_230pm(): 
    global best_230pm; best_230pm=[]

@app.route("/")
def home():
    cs = latest_scan["current_signal"] or {}
    c10 = latest_scan["10am_best"]
    c230 = latest_scan["230pm_best"]
    color = "buy" if str(cs.get('signal'))=="BUY" else "sell" if str(cs.get('signal'))=="SELL" else ""
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>body{{font-family:sans-serif;background:#111;color:#fff;padding:20px}} .buy{{color:#0f0;font-size:26px}} .sell{{color:#f00;font-size:26px}} .card{{background:#222;padding:15px;border-radius:10px;margin:10px 0}}</style></head>
    <body>
    <h1>PWP EURUSD Bot - 0.4 Lots</h1>
    <div class='card'>Last Update: {latest_scan['last_update']}<br>Current: <span class='{color}'><b>{cs}</b></span><br>TP $120 (30pips) SL $70 (17.5pips)</div>
    <div class='card'><h2>10AM Best</h2><b>{c10 if c10 else 'Waiting for 09:40 window'}</b></div>
    <div class='card'><h2>2:30PM Best</h2><b>{c230 if c230 else 'Waiting for 14:10 window'}</b></div>
    <p><a href='/api' style='color:#0ff'>View JSON API</a></p>
    <script>setTimeout(()=>location.reload(),30000)</script>
    </body></html>
    """

@app.route("/api")
def api(): return jsonify(latest_scan)

sched = BackgroundScheduler(timezone=SAST)
sched.add_job(update_live, 'interval', minutes=2)
sched.add_job(reset_10am, 'cron', hour=9, minute=30)
sched.add_job(reset_230pm, 'cron', hour=14, minute=0)
sched.start()
update_live()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
