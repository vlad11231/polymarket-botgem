import time
import json
import requests
import os
import threading
from pathlib import Path
from datetime import datetime, timedelta
import pytz
from flask import Flask, render_template_string

# ==========================================
# 1. CONFIGURARE (HARDCODED - VARIANTA SIGURĂ)
# ==========================================

# 🔴 STERGE CE ERA INAINTE SI PUNE ASTA:
# Pune token-ul tau exact intre ghilimele, fara spatii
BOT_TOKEN = "8408560792:AAEEaQNwcMtUM3NhG6muehfax6G-PkE0FL8" 

# Pune ID-ul tau aici (tot intre ghilimele ca sa fim siguri)
CHAT_ID = "6854863928"

PORT = int(os.getenv("PORT", 5000))

# ... restul codului ramane la fel ...

API = "https://data-api.polymarket.com/activity"
POLL = 60  # Verifică la fiecare 60 secunde

# Calea către volumul persistent Railway
DATA_DIR = Path("/app/data") if os.getenv("RAILWAY_ENVIRONMENT") else Path(".")
STATE_FILE = DATA_DIR / "state.json"

SELF = "Pufu"
SELF_ADDR = "0x872ec2644addbbf526744d8e3cb6b0356c0b73d7"

TRADERS = {
    "Euan": "0xdd225a03cd7ed89e3931906c67c75ab31cf89ef1",
    "Car": "0x7c3db723f1d4d8cb9c550095203b686cb11e5c6b",
    "JJo": "0xc4086b708cd3a50880b7069add1a1a80000f4675",
    "Scottilicious": "0x000d257d2dc7616feaef4ae0f14600fdf50a758e",
    "aenews": "0x44c1dfe43260c94ed4f1d00de2e1f80fb113ebc1",
}

MIN_TRADE = 500
MINI, NORMAL, BIG = 2500, 5000, 10000
RO = pytz.timezone("Europe/Bucharest")

# ==========================================
# 2. STATE MANAGEMENT (MEMORIE)
# ==========================================
global_state = {
    "initialized": False,
    "last": {},           # Ultimul timestamp procesat
    "positions": {},      # Bani activi: "Name|Title|Side": USD
    "trade_log": [],      # ISTORIC COMPLET (Inclusiv ce s-a închis)
    "clusters_sent": {},  # Alerte trimise
    "scores": {},         # Scoruri calculate
    "market_prices": {},  # Prețuri live
    "last_buy_times": {}, # Pentru detectare Ping-Pong
    "last_summary_day": "",
    "nightly_stats": {"new_clusters": 0, "big_exits": 0},
    "last_update": "Never"
}

def load():
    global global_state
    if not DATA_DIR.exists(): DATA_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            # Păstrăm structura curentă și facem update cu datele salvate
            global_state.update(saved)
        except: pass

def save():
    # Salvăm doar ultimele 100 intrări în log ca să nu explodeze fișierul
    if len(global_state["trade_log"]) > 100:
        global_state["trade_log"] = global_state["trade_log"][-100:]
    STATE_FILE.write_text(json.dumps(global_state, indent=2))

# ==========================================
# 3. DASHBOARD WEB (FLASK)
# ==========================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>PolyBot Ultimate</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #0f111a; color: #e0e0e0; padding: 20px; }
        h1, h3 { color: #00d2ff; }
        .card { background: #1e222d; padding: 20px; margin-bottom: 20px; border-radius: 10px; border: 1px solid #333; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
        th { text-align: left; color: #888; border-bottom: 1px solid #555; padding: 8px; }
        td { padding: 8px; border-bottom: 1px solid #333; }
        .tag { padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 0.8em; }
        .buy { background: #00b894; color: #fff; }
        .sell { background: #d63031; color: #fff; }
        .score-high { color: #00d2ff; font-weight: bold; }
        .score-low { color: #ff7675; }
        .self-row { background: rgba(0, 210, 255, 0.1); }
    </style>
</head>
<body>
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <h1>🤖 PolyBot Dashboard</h1>
        <div>Last Update: <b>{{ state.last_update }}</b> | Status: <span style="color:#00b894">● Online</span></div>
    </div>

    <div class="card">
        <h3>💼 Portofoliul Tău ({{ self_name }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Investiție Activă</th><th>Preț Curent</th><th>Scor Piață</th></tr></thead>
            <tbody>
                {% for key, val in state.positions.items() %}
                    {% if key.startswith(self_name) and val > 10 %}
                    <tr class="self-row">
                        <td>{{ key.split('|')[1] }}</td>
                        <td><span class="tag {{ 'buy' if 'YES' in key else 'sell' }}">{{ key.split('|')[2] }}</span></td>
                        <td>${{ val|int }}</td>
                        <td>{{ get_price(key.split('|')[1] + '|' + key.split('|')[2]) }}¢</td>
                        <td>{{ get_score(key.split('|')[1] + '|' + key.split('|')[2]) }}/100</td>
                    </tr>
                    {% endif %}
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>🔥 Active Clusters (Whales)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Volum ($)</th><th>Preț</th><th>Scor</th></tr></thead>
            <tbody>
                {% for key, score in state.scores.items() %}
                <tr>
                    <td>{{ key.split('|')[0] }}</td>
                    <td><span class="tag buy">{{ key.split('|')[1] }}</span></td>
                    <td>${{ get_cluster_vol(key) }}</td>
                    <td>{{ get_price(key) }}¢</td>
                    <td class="{{ 'score-high' if score >= 70 else 'score-low' }}">{{ score }}/100</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>📜 Istoric Decizii & Tranzacții (Ultimele 20)</h3>
        <table>
            <thead><tr><th>Ora</th><th>Trader</th><th>Acțiune</th><th>Piață</th><th>Suma</th><th>Notă</th></tr></thead>
            <tbody>
                {% for log in state.trade_log[-20:]|reverse %}
                <tr>
                    <td>{{ log.time }}</td>
                    <td>{{ log.trader }}</td>
                    <td><span class="tag {{ 'buy' if log.action == 'buy' else 'sell' }}">{{ log.action|upper }}</span></td>
                    <td>{{ log.market }}</td>
                    <td>${{ log.value }}</td>
                    <td>{{ log.note }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    def get_cluster_vol(key):
        m, s = key.split("|")
        total = 0
        for pos_k, val in global_state["positions"].items():
            if f"{m}|{s}" in pos_k and not pos_k.startswith(SELF):
                total += val
        return f"{total:,.0f}"

    def get_price(key):
        return f"{global_state['market_prices'].get(key, 0) * 100:.1f}"

    def get_score(key):
        return global_state['scores'].get(key, 0)

    return render_template_string(
        HTML_TEMPLATE, 
        state=global_state, 
        self_name=SELF, 
        get_cluster_vol=get_cluster_vol, 
        get_price=get_price,
        get_score=get_score
    )

# ==========================================
# 4. LOGICA & ALGORITM
# ==========================================

def tg(msg):
    # HARDCODED - Verifică de 3 ori că sunt corecte!
    MY_TOKEN = "8408560792:AAEEaQNwcMtUM3NhG6muehfax6G-PkE0FL8" 
    MY_ID = "6854863928"
    
    url = f"https://api.telegram.org/bot{MY_TOKEN}/sendMessage"
    
    try:
        print(f"⏳ Încerc să trimit mesaj la: {MY_ID}...")
        r = requests.post(url, json={"chat_id": MY_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        
        # AICI E CHEIA: Printăm răspunsul serverului Telegram
        print(f"👉 STATUS COD: {r.status_code}") 
        print(f"👉 RĂSPUNS TELEGRAM: {r.text}")
        
    except Exception as e:
        print(f"❌ EROARE CRITICĂ DE CONEXIUNE: {e}")

def fetch(addr):
    try:
        r = requests.get(API, params={"user": addr, "limit": 20}, timeout=10)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_usd(e):
    try: return float(e.get("size", 0)) * float(e.get("price", 0))
    except: return 0

def calc_score(cluster_val, price):
    # 3. SISTEM DE SCOR
    score = 0
    # Volum
    if cluster_val >= BIG: score += 40
    elif cluster_val >= NORMAL: score += 25
    elif cluster_val >= MINI: score += 10
    
    # Pret
    if price < 0.10: score -= 20      # Junk
    elif price < 0.90: score += 20    # Good entry
    elif price < 0.95: score += 10    # Safe
    else: score -= 10                 # Riscant (Expensive)

    return max(0, min(100, score))

def check_nightly_summary():
    # 6. SUMMARY NOCTURN
    now = datetime.now(RO)
    today_str = now.strftime("%Y-%m-%d")
    
    if now.hour == 7 and global_state["last_summary_day"] != today_str:
        my_total = sum(v for k, v in global_state["positions"].items() if k.startswith(SELF))
        
        msg = (
            f"☕ <b>Raport Dimineața {today_str}</b>\n\n"
            f"🌙 <b>Activitate Nocturnă:</b>\n"
            f"• Clustere Noi: {global_state['nightly_stats']['new_clusters']}\n"
            f"• Ieșiri Majore: {global_state['nightly_stats']['big_exits']}\n\n"
            f"💼 <b>Capitalul Tău Activ:</b> ${my_total:.0f}\n"
        )
        tg(msg)
        global_state["last_summary_day"] = today_str
        global_state["nightly_stats"] = {"new_clusters": 0, "big_exits": 0}
        save()

def check_portfolio_health():
    # Monitorizare Exit-uri si Profit
    for key, my_amount in global_state["positions"].items():
        if not key.startswith(SELF) or my_amount < 10: continue
        
        parts = key.split("|")
        market_key = f"{parts[1]}|{parts[2]}"
        
        current_price = global_state["market_prices"].get(market_key, 0.5)
        current_score = global_state["scores"].get(market_key, 50)
        
        # Take Profit Warning
        if current_price >= 0.96:
            warn_key = f"tp_{market_key}"
            if warn_key not in global_state["last"]:
                tg(f"💰 <b>TAKE PROFIT</b>\n{parts[1]}\nPreț: {current_price*100:.1f}¢ -> Recomandare: SELL")
                global_state["last"][warn_key] = time.time()
                
        # Low Score Warning
        elif current_score < 30:
            warn_key = f"low_{market_key}"
            if time.time() - global_state["last"].get(warn_key, 0) > 14400:
                tg(f"⚠️ <b>EXIT SIGNAL (Scor Mic)</b>\n{parts[1]}\nScor: {current_score} -> Trendul s-a rupt.")
                global_state["last"][warn_key] = time.time()

def bot_loop():
    load()
    print("Bot loop started.")
    
    while True:
        try:
            check_nightly_summary()
            
            for name, addr in {**TRADERS, SELF: SELF_ADDR}.items():
                events = fetch(addr)
                if not events: continue

                # Initializare fara alerte
                if not global_state["initialized"]:
                    if events: global_state["last"][name] = max(e.get("timestamp", 0) for e in events)
                    continue

                last_ts = global_state["last"].get(name, 0)
                new_max_ts = last_ts
                events.sort(key=lambda x: x.get("timestamp", 0))

                for e in events:
                    ts = e.get("timestamp", 0)
                    if ts <= last_ts: continue
                    if ts > new_max_ts: new_max_ts = ts

                    title = e.get("title", "Unknown")
                    side = e.get("side", "YES")
                    action = e.get("type", "").lower() # buy / sell
                    val = get_usd(e)
                    price = float(e.get("price", 0.5))
                    
                    pos_key = f"{name}|{title}|{side}"
                    market_key = f"{title}|{side}"
                    global_state["market_prices"][market_key] = price

                    # LOGGING PENTRU DASHBOARD
                    log_entry = {
                        "time": datetime.now(RO).strftime("%H:%M"),
                        "trader": name,
                        "action": action,
                        "market": f"{title} ({side})",
                        "value": int(val),
                        "note": ""
                    }

                    # --- DETECTARE PING-PONG ---
                    # Daca vinde la mai putin de 10 min dupa ce a cumparat
                    is_ping_pong = False
                    if action == "sell":
                        last_buy = global_state["last_buy_times"].get(pos_key, 0)
                        if ts - last_buy < 600: # 600 secunde = 10 min
                            is_ping_pong = True
                            log_entry["note"] = "⚠️ PING-PONG"
                    
                    if action == "buy":
                        global_state["last_buy_times"][pos_key] = ts

                    # --- ACTUALIZARE POZITII & ALERTE ---
                    
                    # 1. LOGICA PENTRU TINE (SELF)
                    if name == SELF:
                        if action == "buy":
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            
                            # Calcul Scor la intrare
                            cluster_sum = 0
                            for k, v in global_state["positions"].items():
                                if market_key in k and not k.startswith(SELF): cluster_sum += v
                            score = calc_score(cluster_sum, price)
                            
                            tg(f"🔔 <b>AI CUMPĂRAT</b>\n{title} ({side})\n💲{val:.0f} | Scor: {score}/100")
                            
                        elif action == "sell":
                            global_state["positions"][pos_key] = max(global_state["positions"].get(pos_key, 0) - val, 0)
                            tg(f"🔔 <b>AI VÂNDUT</b>\n{title} ({side})\nRecuperat: ${val:.0f}")

                    # 2. LOGICA PENTRU TRADERI (ALERTE INTELIGENTE)
                    else:
                        if action == "buy":
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            
                            if val >= MIN_TRADE:
                                pp_msg = "\n⚠️ <b>PING-PONG DETECTED!</b>" if is_ping_pong else ""
                                tg(f"👤 <b>{name}</b> BUY {pp_msg}\n{title} ({side})\n💲 {val:.0f} @ {price*100:.1f}¢")

                        elif action == "sell":
                            old_val = global_state["positions"].get(pos_key, 0)
                            global_state["positions"][pos_key] = max(old_val - val, 0)
                            
                            # Alerta "Whale Dump" daca TU esti in aceeasi pozitie
                            my_hold = global_state["positions"].get(f"{SELF}|{title}|{side}", 0)
                            if my_hold > 10 and val >= 1000:
                                tg(f"🚨 <b>WHALE DUMP WARNING</b>\n{name} a vândut ${val:.0f} din {title}!\n👉 Tu încă ai: ${my_hold:.0f}")
                            elif val >= 2000:
                                global_state["nightly_stats"]["big_exits"] += 1
                                tg(f"📉 <b>{name}</b> SELL\n{title} | ${val:.0f}")

                    # Adaugam in istoric dashboard
                    global_state["trade_log"].append(log_entry)

                global_state["last"][name] = new_max_ts

            # --- CALCUL CLUSTERE ---
            clusters = {}
            for k, v in global_state["positions"].items():
                parts = k.split("|")
                if len(parts) == 3 and parts[0] != SELF:
                    c_key = f"{parts[1]}|{parts[2]}"
                    clusters[c_key] = clusters.get(c_key, 0) + v

            for c_key, total in clusters.items():
                p = global_state["market_prices"].get(c_key, 0.5)
                score = calc_score(total, p)
                global_state["scores"][c_key] = score
                
                last_sent = global_state["clusters_sent"].get(c_key, 0)
                if total >= MINI and total > last_sent * 1.2:
                    tg(f"📊 <b>CLUSTER {score}/100</b>\n{c_key}\nTotal Smart Money: ${total:,.0f}")
                    global_state["clusters_sent"][c_key] = total
                    if last_sent == 0: global_state["nightly_stats"]["new_clusters"] += 1

            check_portfolio_health()
            
            global_state["initialized"] = True
            global_state["last_update"] = datetime.now(RO).strftime("%H:%M:%S")
            save()

        except Exception as e:
            print(f"Error loop: {e}")
            time.sleep(5)
            
        time.sleep(POLL)

if __name__ == "__main__":
    t = threading.Thread(target=bot_loop)
    t.daemon = True
    t.start()
    app.run(host="0.0.0.0", port=PORT)
