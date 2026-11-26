from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import pandas as pd

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')

PAIRS_CONFIG = {
    "USDCHF=X": {"label": "USDCHF", "tp": 70, "sl": 70},
    "EURUSD=X": {"label": "EURUSD", "tp": 70, "sl": 70},
    "USDJPY=X": {"label": "USDJPY", "tp": 120, "sl": 70},
}

latest_scan = {"last_update": "Starting...", "last_dt": None, "pairs": {}}

def get_signal_for_pair(pair_code):
    cfg = PAIRS_CONFIG[pair_code]
    try:
        df = yf.download(pair_code, period="2d", interval="5m", progress=False, auto_adjust=True)
        if len(df) < 60:
            return {"signal":"Loading...", "score":0, "level":"no_trade", "pair": cfg["label"], "tp": cfg["tp"], "sl": cfg["sl"]}
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
        is_jpy = "JPY" in pair_code
        is_chf = "CHF" in pair_code
        atr_pips = last_atr * 100 if is_jpy else last_atr * 10000
        ema_gap = abs(last_ema20 - last_ema50) / last_close * (100 if is_jpy else 10000)
        ema_bull = last_ema20 > last_ema50
        ema_bear = last_ema20 < last_ema50
        signal = "NO TRADE"; base_score = 0
        if is_chf:
            if ema_bull and 40 < last_rsi < 70:
                signal = "BUY"; base_score = 20 + ema_gap*3 + (last_rsi-40)*0.5 + atr_pips*2
            elif ema_bear and 30 < last_rsi < 60:
                signal = "SELL"; base_score = 20 + ema_gap*3 + (60-last_rsi)*0.5 + atr_pips*2
        elif is_jpy:
            if ema_bull and 40 < last_rsi < 75:
                signal = "BUY"; base_score = 15 + ema_gap*2.5 + (last_rsi-40)*0.6 + atr_pips*1.5
            elif ema_bear and 25 < last_rsi < 60:
                signal = "SELL"; base_score = 15 + ema_gap*2.5 + (60-last_rsi)*0.6 + atr_pips*1.5
        else:
            if ema_bull and 42 < last_rsi < 72:
                signal = "BUY"; base_score = 18 + ema_gap*2.8 + (last_rsi-42)*0.5 + atr_pips*1.8
            elif ema_bear and 28 < last_rsi < 58:
                signal = "SELL"; base_score = 18 + ema_gap*2.8 + (58-last_rsi)*0.5 + atr_pips*1.8
        score = round(base_score,1)
        if signal == "NO TRADE":
            level = "no_trade"; conf = "NO TRADE"; score = 0
        elif score >= 60:
            level = "best"; conf = f"{signal} - DEFINITE TRADE"
        elif score >= 35:
            level = "ok"; conf = f"{signal} - NORMAL ENTRY"
        elif score >= 20:
            level = "watch"; conf = f"{signal} - IN PROFIT? HOLD"
        elif score >= 10:
            level = "risky"; conf = f"{signal} - RISKY BUT TRADE"
        else:
            level = "no_trade"; conf = "NO TRADE - Weak"; signal = "NO TRADE"; score = 0
        price_fmt = round(last_close,3) if is_jpy else round(last_close,5)
        return {
            "signal": signal, "price": price_fmt, "rsi": round(last_rsi,1),
            "atr": round(atr_pips,1), "ema_gap": round(ema_gap,1),
            "conf": conf, "score": score, "level": level,
            "pair": cfg["label"], "tp": cfg["tp"], "sl": cfg["sl"],
            "time": datetime.now(SAST).strftime("%H:%M:%S"), "dt": datetime.now(SAST)
        }
    except Exception as e:
        return {"signal": "RETRY", "score":0, "level":"no_trade", "pair": cfg["label"], "tp": cfg["tp"], "sl": cfg["sl"], "error": str(e)[:80], "time": datetime.now(SAST).strftime("%H:%M:%S"), "dt": datetime.now(SAST)}

def update_live():
    for code in PAIRS_CONFIG.keys():
        latest_scan["pairs"][code] = get_signal_for_pair(code)
    latest_scan["last_update"] = datetime.now(SAST).strftime("%H:%M:%S")
    latest_scan["last_dt"] = datetime.now(SAST)

@app.route("/")
def home():
    last_dt = latest_scan.get("last_dt")
    if not last_dt or datetime.now(SAST) - last_dt > timedelta(minutes=3):
        update_live()
    def block_html(data):
        if not data:
            return "<div class='block no_trade'>Loading...</div>"
        lvl = data.get('level','no_trade')
        colors = {"best":"#00e676","ok":"#1e90ff","watch":"#ffca28","risky":"#ff5252","no_trade":"#555"}
        bcol = colors.get(lvl,"#555")
        return f"""
        <div class='block {lvl}' style='border-color:{bcol}'>
            <div class='header'><h2>{data['pair']}</h2><span class='badge {lvl}' style='background:{bcol}'>{data['signal']}</span></div>
            <div class='score'>Score<br><b style='color:{bcol}'>{data.get('score',0)}</b></div>
            <div class='grid'>
                <div><small>Last Update</small><br>{data.get('time','')}</div>
                <div><small>Price</small><br>{data.get('price','')}</div>
                <div><small>RSI</small><br>{data.get('rsi','')}</div>
                <div><small>ATR</small><br>{data.get('atr','')}</div>
            </div>
            <div class='risk'>
                <small>{data.get('conf','')}</small><br>
                <span class='sl'>SL ${data['sl']}</span> <span class='tp' style='border-color:{bcol};color:{bcol}'>TP ${data['tp']}</span>
                <br><small>EMA gap {data.get('ema_gap','')}</small>
            </div>
            <div class='dots'>
                <span class='dot {"on" if lvl=="best" else ""}' style='background:#00e676'></span>
                <span class='dot {"on" if lvl=="ok" else ""}' style='background:#1e90ff'></span>
                <span class='dot {"on" if lvl=="watch" else ""}' style='background:#ffca28'></span>
                <span class='dot {"on" if lvl=="risky" else ""}' style='background:#ff5252'></span>
            </div>
        </div>
        """
    pairs = latest_scan.get("pairs", {})
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
    body{{font-family:sans-serif;background:#0e1116;color:#fff;padding:10px;margin:0}}
    h1{{text-align:center;margin:6px 0}} .sub{{text-align:center;color:#aaa;font-size:12px;margin-bottom:10px}}
    .container{{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}}
    .block{{background:#1a1f2b;border-radius:16px;padding:14px;width:300px;border:2px solid #333;box-shadow:0 0 8px #000}}
    .header{{display:flex;justify-content:space-between;align-items:center}} .header h2{{margin:0;font-size:20px}}
    .badge{{padding:5px 10px;border-radius:7px;font-weight:800;font-size:11px;color:#000}} .badge.risky,.badge.no_trade{{color:#fff}}
    .score{{text-align:center;margin:12px 0}} .score b{{font-size:50px}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;background:#11151e;padding:8px;border-radius:10px;font-size:13px;text-align:center}} .grid small{{color:#888;font-size:10px}}
    .risk{{background:#11151e;margin-top:8px;padding:8px;border-radius:10px;text-align:center;font-size:13px}} .sl{{color:#ff5252;border:1px solid #ff5252;padding:3px 8px;border-radius:7px;margin:3px}} .tp{{padding:3px 8px;border-radius:7px;margin:3px;border:1px solid}}
    .dots{{display:flex;gap:6px;justify-content:center;margin-top:10px}} .dot{{width:14px;height:14px;border-radius:50%;background:#333;opacity:0.25}} .dot.on{{opacity:1;box-shadow:0 0 6px currentColor}}
    .legend{{text-align:center;margin-top:12px;font-size:12px;color:#ccc}}
    </style></head>
    <body>
    <h1>PWP Bot Scanner - 3 Markets</h1>
    <div class='sub'>Live • {latest_scan['last_update']} SAST • 3/3 pairs • 0.4 Lots</div>
    <div class='container'>
        {block_html(pairs.get("USDCHF=X"))}
        {block_html(pairs.get("EURUSD=X"))}
        {block_html(pairs.get("USDJPY=X"))}
    </div>
    <div class='legend'>
        🟢 Green (60+) = DEFINITE TRADE | 🔵 Blue (35+) = NORMAL ENTRY | 🟡 Yellow (20+) = IN PROFIT? HOLD<br>
        🔴 Red (10+) = RISKY BUT TRADE | ⚫ Grey (<10) = NO TRADE<br>
        5016: CHF+EUR $70 TP | 5100: JPY $120 TP
    </div>
    <script>setTimeout(()=>location.reload(),60000)</script>
    </body></html>
    """

@app.route("/api")
def api():
    return jsonify(latest_scan)

sched = BackgroundScheduler(timezone=SAST)
sched.add_job(update_live, 'interval', minutes=1)
sched.start()
update_live()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
