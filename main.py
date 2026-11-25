from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import pandas as pd

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')
PAIR = "EURUSD=X"

latest_scan = {"status": "LIVE", "last_update": "Starting...", "last_dt": None, "10am_best": None, "230pm_best": None, "current_signal": None}
best_10am = []
best_230pm = []

def get_signal():
    try:
        df = yf.download(PAIR, period="2d", interval="5m", progress=False, auto_adjust=True)
        if len(df) < 60:
            return {"signal":"Loading...", "score":0, "level":"watch", "time": datetime.now(SAST).strftime("%H:%M:%S")}
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df['Close']; high = df['High']; low = df['Low']
        df['EMA20'] = close.ewm(span=20).mean()
        df['EMA50'] = close.ewm(span=50).mean()
        df['ATR'] = (high - low).rolling(14).mean()
        delta = close.diff()
        gain = delta.where(delta>0, 0).rolling(14).mean()
        loss = -delta.where(delta<0, 0).rolling(14).mean()
        df['RSI'] = 100 - (100 / (1 + gain/loss))
        last_close = float(close.iloc[-1]); last_ema20 = float(df['EMA20'].iloc[-1]); last_ema50 = float(df['EMA50'].iloc[-1]); last_atr = float(df['ATR'].iloc[-1]); last_rsi = float(df['RSI'].iloc[-1])
        atr_pips = last_atr * 10000
        ema_gap = abs(last_ema20 - last_ema50) / last_close * 10000
        ema_bull = last_ema20 > last_ema50
        signal = "NO TRADE"; score = 0
        if ema_bull and 52 < last_rsi < 75:
            signal = "BUY"; score = ema_gap + (last_rsi-50) + atr_pips*0.5
        elif not ema_bull and 25 < last_rsi < 48:
            signal = "SELL"; score = ema_gap + (50-last_rsi) + atr_pips*0.5
        if score >= 25 and atr_pips >= 6:
            level = "best"; conf = "BEST - Take 0.4 lots"
        elif score >= 15 and atr_pips >= 4:
            level = "ok"; conf = "OK - Good entry"
        else:
            level = "watch"; conf = "OK BUT WATCH - Low vol / weak"
        if signal == "NO TRADE": level = "watch"; conf = "WAIT"
        return {"signal": signal, "price": round(last_close,5), "rsi": round(last_rsi,1), "atr": round(atr_pips,1), "ema_gap": round(ema_gap,1), "conf": conf, "score": round(score,1), "level": level, "time": datetime.now(SAST).strftime("%H:%M:%S"), "dt": datetime.now(SAST)}
    except Exception as e:
        return {"signal": "RETRY", "score":0, "level":"watch", "error": str(e)[:80], "time": datetime.now(SAST).strftime("%H:%M:%S"), "dt": datetime.now(SAST)}

def update_live():
    res = get_signal()
    latest_scan["current_signal"] = res
    latest_scan["last_update"] = res.get("time","")
    latest_scan["last_dt"] = res.get("dt")
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
    # FORCE UPDATE IF OLDER THAN 3 MIN - FIXES RENDER SLEEP
    last_dt = latest_scan.get("last_dt")
    if not last_dt or datetime.now(SAST) - last_dt > timedelta(minutes=3):
        update_live()

    def card_html(data, title):
        if not data: return f"<div class='card'><h2>{title}</h2><span class='watch'>Waiting for window</span></div>"
        lvl = data.get('level','watch')
        return f"<div class='card'><h2>{title}</h2><div class='{lvl}'><b>{data.get('signal')} @ {data.get('price')} | {data.get('conf')}</b><br>Score: {data.get('score')} | RSI: {data.get('rsi')} | ATR: {data.get('atr')} pips | EMA gap: {data.get('ema_gap')}<br><small>{data.get('time')}</small></div></div>"

    cs = latest_scan["current_signal"] or {}
    lvl = cs.get('level','watch')
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
    body{{font-family:sans-serif;background:#111;color:#fff;padding:18px}}
    .card{{background:#222;padding:14px;border-radius:12px;margin:12px 0;border-left:6px solid #444}}
    .best{{color:#00a651; font-weight:800; font-size:20px}}
    .ok{{color:#1e90ff; font-weight:700; font-size:20px}}
    .watch{{color:#ff3b30; font-weight:700; font-size:20px}}
    small{{color:#aaa}}
    </style></head>
    <body>
    <h1>PWP EURUSD Bot - 0.4 Lots</h1>
    <div class='card' style='border-left-color:{"#00a651" if lvl=="best" else "#1e90ff" if lvl=="ok" else "#ff3b30"}'>
    Last Update: {latest_scan['last_update']} (auto-updates on page load)<br>
    <div class='{lvl}'>Current: {cs.get('signal','...')} @ {cs.get('price','')} - {cs.get('conf','')}<br>Score {cs.get('score','')} | RSI {cs.get('rsi','')} | ATR {cs.get('atr','')} | {cs.get('time','')}</div>
    TP $120 (30pips) SL $70 (17.5pips)
    </div>
    {card_html(latest_scan['10am_best'], '10AM Best')}
    {card_html(latest_scan['230pm_best'], '2:30PM Best')}
    <p><span class='best'>■ Dark Green = BEST</span><br><span class='ok'>■ Blue = OK</span><br><span class='watch'>■ Red = OK BUT WATCH</span></p>
    <script>setTimeout(()=>location.reload(),60000)</script>
    </body></html>
    """

@app.route("/api")
def api():
    last_dt = latest_scan.get("last_dt")
    if not last_dt or datetime.now(SAST) - last_dt > timedelta(minutes=3):
        update_live()
    return jsonify(latest_scan)

sched = BackgroundScheduler(timezone=SAST)
sched.add_job(update_live, 'interval', minutes=2)
sched.add_job(reset_10am, 'cron', hour=9, minute=30)
sched.add_job(reset_230pm, 'cron', hour=14, minute=0)
sched.start()
update_live()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
