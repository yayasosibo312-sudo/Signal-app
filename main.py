from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import pandas as pd

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')

latest_scan = {"status": "LIVE", "last_update": "Starting...", "last_dt": None, "day_best": None, "night_best": None, "current_signal": None, "current_pair": "EURUSD"}
best_day = []
best_night = []

def get_current_pair():
    h = datetime.now(SAST).hour
    # DAY 06:00-17:59 SAST = EURUSD (London+NY), NIGHT 18:00-05:59 SAST = USDJPY (Asian)
    return "EURUSD=X" if 6 <= h < 18 else "USDJPY=X"

def get_signal_for_pair(pair_code):
    try:
        df = yf.download(pair_code, period="2d", interval="5m", progress=False, auto_adjust=True)
        if len(df) < 60:
            return {"signal":"Loading...", "score":0, "level":"watch", "pair": pair_code}
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
        # ATR in pips: EURUSD x10000, USDJPY x100 (diff)
        atr_pips = last_atr * 10000 if "JPY" not in pair_code else last_atr * 100
        ema_gap = abs(last_ema20 - last_ema50) / last_close * (10000 if "JPY" not in pair_code else 100)
        ema_bull = last_ema20 > last_ema50
        signal = "NO TRADE"; score = 0
        if ema_bull and 52 < last_rsi < 75:
            signal = "BUY"; score = ema_gap + (last_rsi-50) + atr_pips*0.5
        elif not ema_bull and 25 < last_rsi < 48:
            signal = "SELL"; score = ema_gap + (50-last_rsi) + atr_pips*0.5
        if score >= 25 and atr_pips >= 6:
            level = "best"; conf = "BEST - Hold to TP"
        elif score >= 15 and atr_pips >= 4:
            level = "ok"; conf = "OK - Good entry"
        else:
            level = "watch"; conf = "WAIT - Low vol / weak"
        if signal == "NO TRADE": level = "watch"; conf = "WAIT"
        return {"signal": signal, "price": round(last_close,5) if "JPY" not in pair_code else round(last_close,3), "rsi": round(last_rsi,1), "atr": round(atr_pips,1), "ema_gap": round(ema_gap,1), "conf": conf, "score": round(score,1), "level": level, "pair": pair_code.replace("=X",""), "time": datetime.now(SAST).strftime("%H:%M:%S"), "dt": datetime.now(SAST)}
    except Exception as e:
        return {"signal": "RETRY", "score":0, "level":"watch", "pair": pair_code, "error": str(e)[:80], "time": datetime.now(SAST).strftime("%H:%M:%S"), "dt": datetime.now(SAST)}

def update_live():
    pair_code = get_current_pair()
    res = get_signal_for_pair(pair_code)
    latest_scan["current_signal"] = res
    latest_scan["current_pair"] = res.get("pair")
    latest_scan["last_update"] = res.get("time","")
    latest_scan["last_dt"] = res.get("dt")
    if res.get("signal") in ["BUY","SELL"]:
        now = datetime.now(SAST)
        # Day best = 06-17:59, Night best = 18-05:59
        if 6 <= now.hour < 18:
            best_day.append(res)
            latest_scan["day_best"] = sorted(best_day, key=lambda x: x['score'], reverse=True)[0]
        else:
            best_night.append(res)
            latest_scan["night_best"] = sorted(best_night, key=lambda x: x['score'], reverse=True)[0]

def reset_day():
    global best_day; best_day=[]
def reset_night():
    global best_night; best_night=[]

@app.route("/")
def home():
    last_dt = latest_scan.get("last_dt")
    if not last_dt or datetime.now(SAST) - last_dt > timedelta(minutes=3):
        update_live()

    def card_html(data, title):
        if not data: return f"<div class='card'><h2>{title}</h2><span class='watch'>Waiting for window</span></div>"
        lvl = data.get('level','watch')
        return f"<div class='card'><h2>{title} ({data.get('pair')})</h2><div class='{lvl}'><b>{data.get('signal')} @ {data.get('price')} | {data.get('conf')}</b><br>Score: {data.get('score')} | RSI: {data.get('rsi')} | ATR: {data.get('atr')} pips | EMA gap: {data.get('ema_gap')}<br><small>{data.get('time')} - {data.get('pair')}</small></div></div>"

    cs = latest_scan["current_signal"] or {}
    lvl = cs.get('level','watch')
    pair_label = cs.get('pair','EURUSD')
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
    <h1>PWP Bot - {pair_label} - 0.4 Lots</h1>
    <div class='card' style='border-left-color:{"#00a651" if lvl=="best" else "#1e90ff" if lvl=="ok" else "#ff3b30"}'>
    Last Update: {latest_scan['last_update']} | Now Trading: {pair_label} (auto day/night)<br>
    <div class='{lvl}'>Current: {cs.get('signal','...')} @ {cs.get('price','')} - {cs.get('conf','')}<br>Score {cs.get('score','')} | RSI {cs.get('rsi','')} | ATR {cs.get('atr','')} | {cs.get('time','')} | {pair_label}</div>
    TP $120 (30pips) SL $70 (17.5pips) - Close before 18:00 day / 03:00 night
    </div>
    {card_html(latest_scan['day_best'], 'DAY Best')}
    {card_html(latest_scan['night_best'], 'NIGHT Best')}
    <p><span class='best'>■ Dark Green = BEST - Hold</span><br><span class='ok'>■ Blue = OK</span><br><span class='watch'>■ Red = WAIT</span></p>
    <p>Day 06:00-17:59 = EURUSD | Night 18:00-05:59 = USDJPY</p>
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
sched.add_job(reset_day, 'cron', hour=6, minute=0)
sched.add_job(reset_night, 'cron', hour=18, minute=0)
sched.start()
update_live()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
