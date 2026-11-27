from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
from datetime import datetime, timedelta
import pytz
import pandas as pd
import json, os

app = Flask(__name__)
SAST = pytz.timezone('Africa/Johannesburg')

# === CORE RULES ===
GREEN_THRESHOLD = 75
GREEN_VISIBLE_MINUTES = 7
TRADE_LOG_FILE = "trade_log.json"

PAIRS_CONFIG = {
    "EURUSD=X": {"label": "EURUSD", "tp": 120, "sl": 70},
    "AUDUSD=X": {"label": "AUDUSD", "tp": 120, "sl": 70},
    "USDCAD=X": {"label": "USDCAD", "tp": 120, "sl": 70},
    "USDJPY=X": {"label": "USDJPY", "tp": 120, "sl": 70},
}

latest_scan = {"last_update": "Starting...", "last_dt": None, "pairs": {}}
PAIR_HISTORY = {code: [] for code in PAIRS_CONFIG.keys()}
PAIR_GREEN_START = {}

# === TAILORED HOLD RULES PER PAIR BEHAVIOUR ===
PAIR_HOLD_RULES = {
    "EURUSD": {
        "min_gap_pips": 8, "max_atr_spike": 1.8, "rsi_fade": 1.5,
        "hold_bars": 3, "score_bonus": 0,
        "session_block": [],
        "desc": "Slow, tight - small gap OK but must be stable"
    },
    "AUDUSD": {
        "min_gap_pips": 14, "max_atr_spike": 1.9, "rsi_fade": 1.8,
        "hold_bars": 4, "score_bonus": 6,
        "session_block": [0,1,2,3,4,5,6,7],  # Block Asian 00-07 SAST low volume fakes
        "desc": "Risk+Gold pair - blocks Asian, wick filter, needs 20min hold"
    },
    "USDCAD": {
        "min_gap_pips": 15, "max_atr_spike": 2.2, "rsi_fade": 2.5,
        "hold_bars": 4, "score_bonus": 8,
        "session_block": [],
        "desc": "Oil-driven, ranging - needs big gap + clean EMA50 + 20min hold"
    },
    "USDJPY": {
        "min_gap_pips": 18, "max_atr_spike": 2.8, "rsi_fade": 3.0,
        "hold_bars": 3, "score_bonus": 10,
        "session_block": [],
        "desc": "Wildest - only huge gaps count, allows spikes"
    }
}

def load_log():
    if os.path.exists(TRADE_LOG_FILE):
        try: return json.load(open(TRADE_LOG_FILE))
        except: return []
    return []

def save_trade(pair, result, entry_price, close_price, score, direction):
    log = load_log()
    log.append({
        "time": datetime.now(SAST).isoformat(),
        "pair": pair, "result": result, "dir": direction,
        "entry": entry_price, "close": close_price, "score": score
    })
    log = log[-100:]
    json.dump(log, open(TRADE_LOG_FILE, "w"))
    return log

def get_winrate():
    log = load_log()
    if not log: return "No trades yet"
    last20 = log[-20:]
    tp = len([t for t in last20 if t["result"]=="TP"])
    sl = len([t for t in last20 if t["result"]=="SL"])
    total = tp+sl
    if total==0: return f"{len(log)} logged - waiting TP/SL"
    rate = tp/total*100
    all_tp = len([t for t in log if t['result']=='TP'])
    return f"Last {total}: {tp} TP / {sl} SL = {rate:.0f}% | All {all_tp}/{len(log)} = {all_tp/len(log)*100:.0f}%"

def passes_hold_filter(df, signal_type, pair_label):
    rules = PAIR_HOLD_RULES.get(pair_label, PAIR_HOLD_RULES["EURUSD"])
    if len(df) < 30:
        return False, "Need 30 bars"
    close = df['Close']; high = df['High']; low = df['Low']; open_ = df['Open']
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()
    
    # Block session for AUDUSD
    now_hour = datetime.now(SAST).hour
    if now_hour in rules["session_block"]:
        return False, f"{pair_label} blocked {now_hour}h Asian low vol"
    
    # 1. Hold bars check - must stay same side EMA20 for X bars
    bars = rules["hold_bars"]
    last_closes = close.iloc[-bars:]
    last_ema20 = ema20.iloc[-bars:]
    if signal_type == "BUY":
        if not all(last_closes > last_ema20):
            return False, f"Failed {bars}-bar hold (close under EMA20)"
    else:
        if not all(last_closes < last_ema20):
            return False, f"Failed {bars}-bar hold (close over EMA20)"
    
    # 2. Gap size + expanding check
    gap_now = abs(ema20.iloc[-1] - ema50.iloc[-1])
    is_jpy = "JPY" in pair_label
    gap_pips = gap_now * (100 if is_jpy else 10000)
    if gap_pips < rules["min_gap_pips"]:
        return False, f"Gap {gap_pips:.1f} < min {rules['min_gap_pips']} for {pair_label}"
    gap_3ago = abs(ema20.iloc[-4] - ema50.iloc[-4])
    gap_3ago_pips = gap_3ago * (100 if is_jpy else 10000)
    if gap_pips < gap_3ago_pips * 0.92:
        return False, f"Gap shrinking {gap_3ago_pips:.1f}->{gap_pips:.1f}"
    
    # 3. RSI fade check
    delta = close.diff()
    gain = delta.where(delta>0, 0).rolling(14).mean()
    loss = -delta.where(delta<0, 0).rolling(14).mean()
    rsi = 100 - (100 / (1 + gain/loss))
    rsi_now = float(rsi.iloc[-1]); rsi_prev = float(rsi.iloc[-bars])
    if signal_type == "BUY" and rsi_now < rsi_prev - rules["rsi_fade"]:
        return False, f"RSI fade {rsi_prev:.1f}->{rsi_now:.1f}"
    if signal_type == "SELL" and rsi_now > rsi_prev + rules["rsi_fade"]:
        return False, f"RSI fade {rsi_prev:.1f}->{rsi_now:.1f}"
    
    # 4. ATR spike filter
    atr = (high - low).rolling(14).mean().iloc[-1]
    last_range = float(high.iloc[-1] - low.iloc[-1])
    if last_range > atr * rules["max_atr_spike"]:
        return False, f"Spike {last_range/atr:.1f}x > {rules['max_atr_spike']}x"
    
    # 5. Pair specific extra filters
    if pair_label == "AUDUSD":
        # Long wick = indecision = will become B
        body = abs(float(close.iloc[-1] - open_.iloc[-1]))
        wick = last_range - body
        if body > 0 and wick / body > 1.5:
            return False, "AUDUSD long wick indecision"
    if pair_label == "USDCAD":
        # Wicks must not cross EMA50 - clean trend only
        ema50_last = ema50.iloc[-bars:]
        if signal_type == "BUY" and any(low.iloc[-bars:] < ema50_last):
            return False, "USDCAD wick crossed EMA50"
        if signal_type == "SELL" and any(high.iloc[-bars:] > ema50_last):
            return False, "USDCAD wick crossed EMA50"
    
    return True, f"OK {pair_label} gap {gap_pips:.1f} {bars}bars"

def get_signal_for_pair(pair_code):
    cfg = PAIRS_CONFIG[pair_code]
    now_sast = datetime.now(SAST)
    is_session = 10 <= now_sast.hour < 16  # Your 10-4 rule
    
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
        
        # === SIGNAL LOGIC DOUBLE-CHECKED PER PAIR ===
        signal = "NO TRADE"; base_score = 0
        label = cfg["label"]
        # EURUSD: slow, needs tight RSI 42-72 / 28-58
        if label == "EURUSD":
            if ema_bull and 42 < last_rsi < 72:
                signal = "BUY"; base_score = 18 + ema_gap*2.8 + (last_rsi-42)*0.5 + atr_pips*1.8
            elif ema_bear and 28 < last_rsi < 58:
                signal = "SELL"; base_score = 18 + ema_gap*2.8 + (58-last_rsi)*0.5 + atr_pips*1.8
        # AUDUSD: choppy, needs wider RSI 40-70 / 30-60 + bigger gap weight
        elif label == "AUDUSD":
            if ema_bull and 40 < last_rsi < 70:
                signal = "BUY"; base_score = 20 + ema_gap*3.0 + (last_rsi-40)*0.5 + atr_pips*2.0
            elif ema_bear and 30 < last_rsi < 60:
                signal = "SELL"; base_score = 20 + ema_gap*3.0 + (60-last_rsi)*0.5 + atr_pips*2.0
        # USDCAD: your main, needs strongest trend - higher base + gap*3.2
        elif label == "USDCAD":
            if ema_bull and 40 < last_rsi < 70:
                signal = "BUY"; base_score = 22 + ema_gap*3.2 + (last_rsi-40)*0.6 + atr_pips*2.2
            elif ema_bear and 30 < last_rsi < 60:
                signal = "SELL"; base_score = 22 + ema_gap*3.2 + (60-last_rsi)*0.6 + atr_pips*2.2
        # USDJPY: wild, allows wider RSI 40-75 / 25-60
        else:
            if ema_bull and 40 < last_rsi < 75:
                signal = "BUY"; base_score = 15 + ema_gap*2.5 + (last_rsi-40)*0.6 + atr_pips*1.5
            elif ema_bear and 25 < last_rsi < 60:
                signal = "SELL"; base_score = 15 + ema_gap*2.5 + (60-last_rsi)*0.6 + atr_pips*1.5
        
        score = round(base_score,1)
        hold_reason = ""

        if not is_session:
            PAIR_GREEN_START.pop(pair_code, None)
            return {"signal":"NO TRADE", "price": round(last_close,3 if is_jpy else 5), "score":0, "level":"no_trade", "pair": cfg["label"], "tp": cfg["tp"], "sl": cfg["sl"], "conf": f"Outside 10-16h ({now_sast.strftime('%H:%M')})", "rsi": round(last_rsi,1), "atr": round(atr_pips,1), "ema_gap": round(ema_gap,1), "time": now_sast.strftime("%H:%M:%S"), "dt": now_sast}

        if signal != "NO TRADE":
            ok, reason = passes_hold_filter(df, signal, label)
            hold_reason = reason
            if not ok:
                score = score * 0.35
        else:
            score = 0

        hist = PAIR_HISTORY.get(pair_code, [])
        hist.append(signal)
        if len(hist) > 4: hist.pop(0)
        PAIR_HISTORY[pair_code] = hist
        persisted = len(hist) >= 2 and hist[-1] == hist[-2] and hist[-1] != "NO TRADE"
        if signal != "NO TRADE" and not persisted:
            score = score * 0.55
            hold_reason = (hold_reason + " | ") if hold_reason else "" + "First seen - need 10min confirm"

        # GREEN ONLY with pair bonus
        rules = PAIR_HOLD_RULES[label]
        needed = GREEN_THRESHOLD + rules["score_bonus"]
        if score < needed:
            PAIR_GREEN_START.pop(pair_code, None)
            level = "no_trade"; conf = f"Filtered {score:.0f} < {needed} - {hold_reason}"; signal = "NO TRADE"; score = 0
        else:
            level = "best"; conf = f"{signal} - QUALITY HOLD OK - {hold_reason}"
            if pair_code not in PAIR_GREEN_START:
                PAIR_GREEN_START[pair_code] = now_sast

        price_fmt = round(last_close,3) if is_jpy else round(last_close,5)
        return {
            "signal": signal, "price": price_fmt, "rsi": round(last_rsi,1),
            "atr": round(atr_pips,1), "ema_gap": round(ema_gap,1),
            "conf": conf, "score": score, "level": level,
            "pair": cfg["label"], "tp": cfg["tp"], "sl": cfg["sl"],
            "time": now_sast.strftime("%H:%M:%S"), "dt": now_sast
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
        if lvl != "best":
            return f"""
            <div class='block no_trade' style='border-color:#222;background:#111;opacity:0.35'>
                <div class='header'><h2>{data['pair']}</h2><span class='badge' style='background:#222;color:#666'>🚫 NO TRADE</span></div>
                <div class='sig' style='color:#555;font-size:10px'>{data.get('conf','Filtered')}</div>
                <div class='score' style='color:#333'><b>0</b><br><small>FILTERED</small></div>
                <div class='grid' style='background:#0a0a0a'><div><small>Time</small><br>{data.get('time','')}</div><div><small>RSI</small><br>{data.get('rsi','')}</div></div>
            </div>
            """
        pair_code = None
        for k,v in PAIRS_CONFIG.items():
            if v["label"]==data['pair']:
                pair_code=k; break
        start = PAIR_GREEN_START.get(pair_code)
        remaining_sec = GREEN_VISIBLE_MINUTES*60
        expired=False
        if start:
            elapsed=(datetime.now(SAST)-start).total_seconds()
            remaining_sec=GREEN_VISIBLE_MINUTES*60-elapsed
            if remaining_sec<=0: expired=True
        if expired:
            return f"<div class='block no_trade' style='border-color:#442200;background:#1a1000;opacity:0.7'><div class='header'><h2>{data['pair']}</h2><span class='badge' style='background:#ff6a00;color:#000'>⏰ EXPIRED</span></div><div class='sig'>Was {data['signal']} - Don't chase</div></div>"
        mins=int(remaining_sec//60); secs=int(remaining_sec%60)
        rules=PAIR_HOLD_RULES[data['pair']]
        return f"""
        <div class='block best' style='border-color:#00E676;background:#00E67622;box-shadow:0 0 25px #00E67677'>
            <div class='header'><h2>{data['pair']}</h2><span class='badge' style='background:#00E676;color:#000'>✅ {mins}:{secs:02d} LEFT</span></div>
            <div class='sig' style='font-weight:900;font-size:18px'>{data['signal']} @ {data.get('price','')}</div>
            <div class='score' style='color:#00E676'><b>{data.get('score',0)}</b><br><small>{rules['desc']}</small></div>
            <div class='grid'><div><small>RSI / ATR</small><br>{data.get('rsi','')} / {data.get('atr','')}</div><div><small>EMA gap</small><br>{data.get('ema_gap','')}</div></div>
            <div class='risk' style='font-size:10px'>{data.get('conf','')}</div>
            <a href='/enter/{data["pair"]}/{data["signal"]}/{data.get("price",0)}/{data.get("score",0)}' style='display:block;background:#00E676;color:#000;padding:14px;text-align:center;border-radius:12px;font-weight:900;text-decoration:none;margin-top:10px'>I ENTERED {data["signal"]} - TAP</a>
            <div style='display:flex;gap:8px;margin-top:8px'>
                <a href='/close/{data["pair"]}/TP' style='flex:1;background:#0e121b;border:2px solid #00E676;color:#00E676;padding:12px;text-align:center;border-radius:10px;text-decoration:none;font-weight:800'>✅ TP</a>
                <a href='/close/{data["pair"]}/SL' style='flex:1;background:#0e121b;border:2px solid #FF3D57;color:#FF3D57;padding:12px;text-align:center;border-radius:10px;text-decoration:none;font-weight:800'>❌ SL</a>
            </div>
        </div>
        """
    pairs = latest_scan.get("pairs", {})
    return f"""
    <html><head><meta name='viewport' content='width=device-width, initial-scale=1'>
    <style>
    body{{font-family:sans-serif;background:#080a0f;color:#fff;padding:10px;margin:0}}
    h1{{text-align:center;margin:8px 0;font-size:16px}} .sub{{text-align:center;color:#aaa;font-size:11px;margin-bottom:12px}}
    .container{{display:flex;gap:14px;flex-wrap:wrap;justify-content:center}}
    .block{{border-radius:20px;padding:16px;width:320px;border:3px solid #333;box-shadow:0 4px 20px #000}}
    .header{{display:flex;justify-content:space-between;align-items:center}} .header h2{{margin:0;font-size:22px}}
    .badge{{padding:8px 14px;border-radius:10px;font-weight:900;font-size:13px}}
    .sig{{text-align:center;margin:8px 0;font-size:14px;color:#ccc}}
    .score{{text-align:center;margin:8px 0}} .score b{{font-size:58px;line-height:1}} .score small{{font-size:10px;color:#aaa}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;background:#0e121b;padding:10px;border-radius:12px;font-size:14px;text-align:center;margin-top:8px}} .grid small{{color:#777;font-size:10px}}
    .risk{{background:#0e121b;margin-top:10px;padding:8px;border-radius:12px;text-align:center;font-size:11px}} .legend{{text-align:center;margin-top:14px;font-size:10px;color:#999;line-height:1.6}}
    </style></head>
    <body>
    <h1>PWP - GREEN ONLY - Tailored Hold</h1>
    <div class='sub'>Live {latest_scan['last_update']} SAST | {get_winrate()} | EUR 3bars | AUD 4bars Asian-block | CAD 4bars | JPY 3bars</div>
    <div class='container'>
        {block_html(pairs.get("EURUSD=X"))}
        {block_html(pairs.get("AUDUSD=X"))}
        {block_html(pairs.get("USDCAD=X"))}
        {block_html(pairs.get("USDJPY=X"))}
    </div>
    <div class='legend'>
        Quality Hold = if it says good, it stays good.<br>
        EURUSD min 8 pips | AUDUSD 14 pips + no Asian + wick filter | USDCAD 15 pips + EMA50 clean | USDJPY 18 pips<br>
        Score need: EUR {GREEN_THRESHOLD} | AUD {GREEN_THRESHOLD+6} | CAD {GREEN_THRESHOLD+8} | JPY {GREEN_THRESHOLD+10}<br>
        Timer {GREEN_VISIBLE_MINUTES} min - if expired, leave it. <a href='/winrate' style='color:#00E676'>View log</a>
    </div>
    <script>setTimeout(()=>location.reload(),30000)</script>
    </body></html>
    """

@app.route("/api")
def api():
    return jsonify(latest_scan)

@app.route("/enter/<pair>/<direction>/<price>/<score>")
def log_enter(pair, direction, price, score):
    try:
        json.dump({"pair": pair, "dir": direction, "entry_price": price, "entry_time": datetime.now(SAST).isoformat(), "score": score}, open(f"active_{pair}.json","w"))
    except: pass
    return f"<html><body style='background:#080a0f;color:#fff;font-family:sans-serif;text-align:center;padding:40px'><h1 style='color:#00E676'>✅ {pair} {direction} @ {price} logged</h1><p>Place in cTrader now SL $70 TP $120</p><a href='/' style='display:inline-block;margin-top:20px;background:#00E676;color:#000;padding:14px 24px;border-radius:10px;text-decoration:none;font-weight:900'>Back</a><script>setTimeout(()=>window.location='/',3000)</script></body></html>"

@app.route("/close/<pair>/<result>")
def log_close(pair, result):
    try:
        ap=f"active_{pair}.json"
        if os.path.exists(ap):
            a=json.load(open(ap)); save_trade(pair, result, a.get("entry_price",0), 0, a.get("score",0), a.get("dir","")); os.remove(ap)
        else: save_trade(pair, result, 0, 0, 0, "")
    except: save_trade(pair, result, 0, 0, 0, "")
    return f"<html><body style='background:#080a0f;color:#fff;text-align:center;padding:40px'><h1 style='color:{"#00E676" if result=="TP" else "#FF3D57"}'>{result} logged {pair}</h1><p>{get_winrate()}</p><a href='/' style='display:inline-block;margin-top:20px;background:#00E676;color:#000;padding:14px 24px;border-radius:10px;text-decoration:none;font-weight:900'>Back</a><script>setTimeout(()=>window.location='/',3000)</script></body></html>"

@app.route("/winrate")
def winrate_page():
    log=load_log(); rows=""
    for t in reversed(log[-50:]):
        col="#00E676" if t["result"]=="TP" else "#FF3D57"
        rows+=f"<tr><td>{t['time'][5:16]}</td><td>{t['pair']}</td><td>{t['dir']}</td><td style='color:{col};font-weight:800'>{t['result']}</td><td>{t['score']}</td></tr>"
    return f"<html><head><meta name='viewport' content='width=device-width, initial-scale=1'></head><body style='background:#080a0f;color:#fff;font-family:sans-serif;padding:12px'><h2>{get_winrate()}</h2><table style='width:100%;border-collapse:collapse;font-size:13px'><tr style='color:#777'><th>Time</th><th>Pair</th><th>Dir</th><th>Res</th><th>Score</th></tr>{rows}</table><br><a href='/' style='color:#00E676'>Back</a> | <a href='/clearlog' style='color:#FF3D57'>Clear</a></body></html>"

@app.route("/clearlog")
def clearlog():
    if os.path.exists(TRADE_LOG_FILE): os.remove(TRADE_LOG_FILE)
    return "<script>window.location='/'</script>"

sched = BackgroundScheduler(timezone=SAST)
sched.add_job(update_live, 'interval', minutes=1)
sched.start()
update_live()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
