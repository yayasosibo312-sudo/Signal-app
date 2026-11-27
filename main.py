from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import pandas as pd

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')

PAIRS_CONFIG = {
    "EURUSD=X": {"label": "EURUSD", "tp": 70, "sl": 60},
    "AUDUSD=X": {"label": "AUDUSD", "tp": 70, "sl": 60},
    "USDCAD=X": {"label": "USDCAD", "tp": 70, "sl": 65},
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
        atr_pips = last_atr * 100 if is_jpy else last_atr * 10000
        ema_gap = abs(last_ema20 - last_ema50) / last_close * (100 if is_jpy else 10000)
        ema_bull = last_ema20 > last_ema50
        ema_bear = last_ema20 < last_ema50
        signal = "NO TRADE"; base_score = 0
        label = cfg["label"]
        if label == "EURUSD":
            if ema_bull and 42 < last_rsi < 72:
                signal = "BUY"; base_score = 18 + ema_gap*2.8 + (last_rsi-42)*0.5 + atr_pips*1.8
            elif ema_bear and 28 < last_rsi < 58:
                signal = "SELL"; base_score = 18 + ema_gap*2.8 + (58-last_rsi)*0.5 + atr_pips*1.8
        elif label == "AUDUSD":
            if ema_bull and 40 < last_rsi < 70:
                signal = "BUY"; base_score = 20 + ema_gap*3.0 + (last_rsi-40)*0.5 + atr_pips*2.0
            elif ema_bear and 30 < last_rsi < 60:
                signal = "SELL"; base_score = 20 + ema_gap*3.0 + (60-last_rsi)*0.5 + atr_pips*2.0
        elif label == "USDCAD":
            if ema_bull and 40 < last_rsi < 70:
                signal = "BUY"; base_score = 22 + ema_gap*3.2 + (last_rsi-40)*0.6 + atr_pips*2.2
            elif ema_bear and 30 < last_rsi < 60:
                signal = "SELL"; base_score = 22 + ema_gap*3.2 + (60-last_rsi)*0.6 + atr_pips*2.2
        else:
            if ema_bull and 40 < last_rsi < 75:
                signal = "BUY"; base_score = 15 + ema_gap*2.5 + (last_rsi-40)*0.6 + atr_pips*1.5
            elif ema_bear and 25 < last_rsi < 60:
                signal = "SELL"; base_score = 15 + ema_gap*2.5 + (60-last_rsi)*0.6 + atr_pips*1.5
        score = round(base_score,1)
        if signal == "NO TRADE":
            level = "no_trade"; conf = "NO TRADE"; score = 0
        elif score >= 60:
            level = "best"; conf = f"{signal} - DEFINITE TRADE"
        elif score >= 38:
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
        cfg_map = {
            "best": {"col":"#00E676", "bg":"#00E67622", "icon":"✅", "text":"DEFINITE"},
            "ok": {"col":"#2979FF", "bg":"#2979FF22", "icon":"▶️", "text":"NORMAL"},
            "watch": {"col":"#FFAB00", "bg":"#FFAB0022", "icon":"⏸️", "text":"HOLD PROFIT"},
            "risky": {"col":"#FF3D57", "bg":"#FF3D5722", "icon":"⚠️", "text":"RISKY"},
            "no_trade": {"col":"#616161", "bg":"#212121", "icon":"🚫", "text":"NO TRADE"}
        }
        c = cfg_map.get(lvl, cfg_map["no_trade"])
        return f"""
        <div class='block {lvl}' style='border-color:{c['col']};background:{c['bg']}'>
            <div class='header'><h2>{data['pair']}</h2><span class='badge' style='background:{c['col']};color:{"#000" if lvl in ["best","watch"] else "#fff"}'>{c['icon']} {c['text']}</span></div>
            <div class='sig'>{data['signal']} @ {data.get('price','')}</div>
            <div class='score' style='color:{c['col']}'><b>{data.get('score',0)}</b><br><small>SCORE</small></div>
            <div class='grid'>
                <div><small>Time</small><br>{data.get('time','')}</div>
                <div><small>RSI / ATR</small><br>{data.get('rsi','')} / {data.get('atr','')}</div>
            </div>
            <div class='risk'>
                <small>{data.get('conf','')}</small><br>
                <span class='sl'>SL ${data['sl']}</span> <span class='tp'>TP ${data['tp']}</span>
                <br><small>EMA gap {data.get('ema_gap','')}</small>
            </div>
            <div class='big-dots'>
                <div class='big-dot {"active" if lvl=="best" else ""}' style='border-color:#00E676'><span style='background:#00E676'></span><label>DEFINITE</label></div>
                <div class='big-dot {"active" if lvl=="ok" else ""}' style='border-color:#2979FF'><span style='background:#2979FF'></span><label>NORMAL</label></div>
                <div class='big-dot {"active" if lvl=="watch" else ""}' style='border-color:#FFAB00'><span style='background:#FFAB00'></span><label>HOLD</label></div>
                <div class='big-dot {"active" if lvl=="risky" else ""}' style='border-color:#FF3D57'><span style='background:#FF3D57'></span><label>RISKY</label></div>
            </div>
        </div>
        """
    pairs = latest_scan.get("pairs", {})
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
    body{{font-family:sans-serif;background:#080a0f;color:#fff;padding:10px;margin:0}}
    h1{{text-align:center;margin:8px 0}} .sub{{text-align:center;color:#aaa;font-size:12px;margin-bottom:12px}}
    .container{{display:flex;gap:14px;flex-wrap:wrap;justify-content:center}}
    .block{{border-radius:20px;padding:16px;width:320px;border:3px solid #333;box-shadow:0 4px 20px #000}}
    .header{{display:flex;justify-content:space-between;align-items:center}} .header h2{{margin:0;font-size:22px}}
    .badge{{padding:8px 14px;border-radius:10px;font-weight:900;font-size:13px;letter-spacing:0.5px}}
    .sig{{text-align:center;margin:8px 0;font-size:14px;color:#ccc}}
    .score{{text-align:center;margin:8px 0}} .score b{{font-size:58px;line-height:1}} .score small{{font-size:11px;color:#aaa}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;background:#0e121b;padding:10px;border-radius:12px;font-size:14px;text-align:center;margin-top:8px}} .grid small{{color:#777;font-size:10px}}
    .risk{{background:#0e121b;margin-top:10px;padding:10px;border-radius:12px;text-align:center;font-size:13px}} .sl{{color:#FF3D57;border:1px solid #FF3D57;padding:4px 10px;border-radius:8px;margin:4px;display:inline-block}} .tp{{color:#00E676;border:1px solid #00E676;padding:4px 10px;border-radius:8px;margin:4px;display:inline-block}}
    .big-dots{{display:flex;gap:6px;justify-content:space-between;margin-top:14px}}
    .big-dot{{flex:1;text-align:center;border:2px solid #333;border-radius:12px;padding:8px 2px;background:#111;opacity:0.3;transition:0.2s}} .big-dot.active{{opacity:1;transform:scale(1.08);box-shadow:0 0 14px currentColor;background:#1e1e1e;border-width:3px}}
    .big-dot span{{display:block;width:26px;height:26px;border-radius:50%;margin:0 auto 5px auto}} .big-dot label{{font-size:8.5px;font-weight:900;display:block;letter-spacing:0.5px}}
    .legend{{text-align:center;margin-top:14px;font-size:11px;color:#999;line-height:1.6}}
    </style></head>
    <body>
    <h1>PWP Bot Scanner - 4 Markets Smooth</h1>
    <div class='sub'>Live • {latest_scan['last_update']} SAST • 0.01 Lots • Big Dots + Icons • All trades shown - you skip night</div>
    <div class='container'>
        {block_html(pairs.get("EURUSD=X"))}
        {block_html(pairs.get("AUDUSD=X"))}
        {block_html(pairs.get("USDCAD=X"))}
        {block_html(pairs.get("USDJPY=X"))}
    </div>
    <div class='legend'>
        ✅ GREEN #00E676 = DEFINITE | ▶️ BLUE #2979FF = NORMAL | ⏸️ AMBER #FFAB00 = HOLD IF IN PROFIT | ⚠️ RED #FF3D57 = RISKY<br>
        Same colours as before + icons + 26px big dots with labels<br>
        YOUR RULE: 10:00-16:00 SAST only - you skip night manually, bot shows all
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
