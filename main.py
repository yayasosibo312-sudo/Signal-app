from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime
import pytz

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')
PAIR = "EURUSD=X"

latest_scan = {
    "status": "Bot starting...",
    "last_update": "",
    "10am_best": None,
    "230pm_best": None,
    "current_signal": None
}
best_10am = []
best_230pm = []

def get_signal():
    try:
        df = yf.download(PAIR, period="2d", interval="5m", progress=False, auto_adjust=True)
        if len(df) < 60: return None
        df['EMA20'] = df['Close'].ewm(span=20).mean()
        df['EMA50'] = df['Close'].ewm(span=50).mean()
        df['ATR'] = (df['High']-df['Low']).rolling(14).mean()
        delta = df['Close'].diff()
        gain = delta.where(delta>0,0).rolling(14).mean()
        loss = -delta.where(delta<0,0).rolling(14).mean()
        df['RSI'] = 100 - (100/(1+gain/loss))
        last = df.iloc[-1]
        price = float(last['Close'])
        rsi = float(last['RSI'])
        atr_pips = float(last['ATR'])*10000
        ema_gap = abs(float(last['EMA20'])-float(last['EMA50']))/price*10000
        ema_bull = float(last['EMA20']) > float(last['EMA50'])
        
        signal = "NO TRADE"
        score = 0
        if ema_bull and 52 < rsi < 75:
            signal = "BUY"; score = ema_gap + (rsi-50) + atr_pips*0.5
        elif not ema_bull and 25 < rsi < 48:
            signal = "SELL"; score = ema_gap + (50-rsi) + atr_pips*0.5
        
        if atr_pips > 10: conf = "STRONG - Good for 0.4 lots $120"
        elif atr_pips > 6: conf = "NORMAL - Can hit 30 pips"
        else: conf = "LOW VOL - Still valid"
        
        return {"signal": signal, "price": price, "rsi": round(rsi,1), "atr": round(atr_pips,1), "ema_gap": round(ema_gap,1), "conf": conf, "score": round(score,1), "time": datetime.now(SAST).strftime("%H:%M:%S")}
    except Exception as e:
        return {"signal": f"Error {e}", "time": datetime.now(SAST).strftime("%H:%M:%S")}

def scan_10am():
    global best_10am
    res = get_signal()
    if not res: return
    latest_scan["current_signal"] = res
    latest_scan["last_update"] = res["time"]
    if res["signal"] in ["BUY","SELL"]:
        best_10am.append(res)
        best = sorted(best_10am, key=lambda x: x['score'], reverse=True)[0]
        latest_scan["10am_best"] = best

def scan_230pm():
    global best_230pm
    res = get_signal()
    if not res: return
    latest_scan["current_signal"] = res
    latest_scan["last_update"] = res["time"]
    if res["signal"] in ["BUY","SELL"]:
        best_230pm.append(res)
        best = sorted(best_230pm, key=lambda x: x['score'], reverse=True)[0]
        latest_scan["230pm_best"] = best

def reset_10am(): 
    global best_10am; best_10am=[]
def reset_230pm(): 
    global best_230pm; best_230pm=[]

# Web routes
@app.route("/")
def home():
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>body{{font-family:sans-serif;background:#111;color:#fff;padding:20px}} .buy{{color:#0f0;font-size:32px}} .sell{{color:#f00;font-size:32px}} .card{{background:#222;padding:15px;border-radius:10px;margin:10px 0}}</style></head>
    <body>
    <h1>PWP EURUSD Bot - 0.4 Lots</h1>
    <div class='card'>Last Update: {latest_scan['last_update']}<br>Current: {latest_scan['current_signal']}</div>
    <div class='card'><h2>10AM Best</h2><h3 class='{str(latest_scan['10am_best']['signal']).lower() if latest_scan['10am_best'] else ''}'>{latest_scan['10am_best']}</h3></div>
    <div class='card'><h2>2:30PM Best</h2><h3 class='{str(latest_scan['230pm_best']['signal']).lower() if latest_scan['230pm_best'] else ''}'>{latest_scan['230pm_best']}</h3></div>
    <p><a href='/api' style='color:#0ff'>View JSON API</a> - auto refresh every 30s</p>
    <script>setTimeout(()=>location.reload(),30000)</script>
    </body></html>
    """

@app.route("/api")
def api():
    return jsonify(latest_scan)

# Scheduler
sched = BackgroundScheduler(timezone=SAST)
for m in [40,42,44,46,48,50,52,54]:
    sched.add_job(scan_10am, 'cron', hour=9, minute=m)
sched.add_job(reset_10am, 'cron', hour=9, minute=56)
for m in [10,12,14,16,18,20,22,24]:
    sched.add_job(scan_230pm, 'cron', hour=14, minute=m)
sched.add_job(reset_230pm, 'cron', hour=14, minute=26)
sched.add_job(get_signal, 'cron', minute='*/2')  # live update every 2 min
sched.start()

if __name__ == "__main__":
    scan_10am()
    app.run(host="0.0.0.0", port=10000)
