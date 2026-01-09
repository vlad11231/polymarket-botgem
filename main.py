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
# 1. CONFIGURARE "GOLD" 
# ==========================================

BOT_TOKEN = "8408560792:AAEEaQNwcMtUM3NhG6muehfax6G-PkE0FL8" 
CHAT_ID = "6854863928"

PORT = int(os.getenv("PORT", 5000))
API = "https://data-api.polymarket.com/activity"
POLL = 60  # Verifică la fiecare 60 secunde

# --- LIMITELE TALE ---
MIN_BUY_ALERT = 500    # Alerta Telegram la BUY > $500
MIN_SELL_ALERT = 1000  # Alerta Telegram la SELL > $1000
MIN_DASHBOARD = 250    # Apare în Dashboard doar dacă e > $250

# --- CLUSTERE ---
MINI, NORMAL, BIG = 2500, 5000, 10000

RO = pytz.timezone("Europe/Bucharest")
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
    "Dropper": "0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292"
}

# ==========================================
# 2. STATE MANAGEMENT (PERSISTENT)
# ==========================================
# Aici salvam totul ca sa nu "uite" la restart
global_state = {
    "initialized": False,
    "last": {},           # Timestamp ultima procesare per trader
    "positions": {},      # "Nume|Market|Side": USD (Suma investita)
    "entry_prices": {},   # "Nume|Market|Side": Pret Mediu (NOU!)
    "trade_log": [],      # Istoric pentru Dashboard
    "clusters_sent": {},  # Ce clustere am anuntat deja
    "scores": {},         # Scoruri calculate live
    "market_prices": {},  # Pretul curent din piata
    "last_buy_times": {}, # Pentru Ping-Pong
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
            # Facem update inteligent (pastram logica noua + date vechi)
            global_state.update(saved)
        except: pass

def save():
    # Salvam ultimele 200 intrari in log
    if len(global_state["trade_log"]) > 200:
        global_state["trade_log"] = global_state["trade_log"][-200:]
    STATE_FILE.write_text(json.dumps(global_state, indent=2))

# ==========================================
# 3. DASHBOARD (FLASK)
# ==========================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>PolyBot Gold</title>
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
        .trade { background: #fdcb6e; color: #000; }
        .score-high { color: #00d2ff; font-weight: bold; }
        .score-med { color: #fdcb6e; font-weight: bold; }
        .score-low { color: #ff7675; }
        .self-row { background: rgba(0, 210, 255, 0.1); }
    </style>
</head>
<body>
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <h1>🤖 PolyBot Dashboard</h1>
        <div>Update: <b>{{ state.last_update }}</b> | <span style="color:#00b894">● Online</span></div>
    </div>

    <div class="card">
        <h3>💼 Portofoliul Tău ({{ self_name }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Investiție</th><th>Preț Intrare</th><th>Preț Curent</th><th>Scor</th></tr></thead>
            <tbody>
                {% for key, val in state.positions.items() %}
                    {% if key.startswith(self_name) and val > 10 %}
                    <tr class="self-row">
                        <td>{{ key.split('|')[1] }}</td>
                        <td><span class="tag {{ 'buy' if 'YES' in key else 'sell' }}">{{ key.split('|')[2] }}</span></td>
                        <td>${{ val|int }}</td>
                        <td>{{ get_entry_price(key) }}¢</td>
                        <td>{{ get_price(key.split('|')[1] + '|' + key.split('|')[2]) }}¢</td>
                        <td>{{ get_score_html(key.split('|')[1] + '|' + key.split('|')[2])|safe }}</td>
                    </tr>
                    {% endif %}
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>🔥 Active Clusters (Smart Money)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Volum Total ($)</th><th>Preț</th><th>Scor Decizie</th></tr></thead>
            <tbody>
                {% for key, score in state.scores.items() %}
                {% if get_cluster_vol_int(key) > 2000 %}
                <tr>
                    <td>{{ key.split('|')[0] }}</td>
                    <td><span class="tag buy">{{ key.split('|')[1] }}</span></td>
                    <td>${{ get_cluster_vol(key) }}</td>
                    <td>{{ get_price(key) }}¢</td>
                    <td>{{ get_score_html(key)|safe }}</td>
                </tr>
                {% endif %}
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>📜 Istoric Decizii (Min ${{ min_dash }})</h3>
        <table>
            <thead><tr><th>Ora</th><th>Trader</th><th>Acțiune</th><th>Piață</th><th>Suma</th><th>Notă / Scor</th></tr></thead>
            <tbody>
                {% for log in state.trade_log[-25:]|reverse %}
                <tr>
                    <td>{{ log.time }}</td>
                    <td>{{ log.trader }}</td>
                    <td><span class="tag {{ 'buy' if log.action == 'buy' else ('sell' if log.action == 'sell' else 'trade') }}">{{ log.action|upper }}</span></td>
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
        total = 0
        for pos_k, val in global_state["positions"].items():
            if f"{key}" in pos_k and not pos_k.startswith(SELF):
                total += val
        return f"{total:,.0f}"

    def get_cluster_vol_int(key):
        total = 0
        for pos_k, val in global_state["positions"].items():
            if f"{key}" in pos_k and not pos_k.startswith(SELF):
                total += val
        return total

    def get_price(key):
        return f"{global_state['market_prices'].get(key, 0) * 100:.1f}"
    
    def get_entry_price(key):
        p = global_state['entry_prices'].get(key, 0)
        return f"{p * 100:.1f}" if p else "-"

    def get_score_html(key):
        s = global_state['scores'].get(key, 0)
        color = "score-low"
        if s >= 8: color = "score-high"
        elif s >= 5: color = "score-med"
        return f'<span class="{color}">{s:.1f}/10</span>'

    return render_template_string(
        HTML_TEMPLATE, 
        state=global_state, 
        self_name=SELF, 
        min_dash=MIN_DASHBOARD,
        get_cluster_vol=get_cluster_vol, 
        get_cluster_vol_int=get_cluster_vol_int,
        get_price=get_price,
        get_entry_price=get_entry_price,
        get_score_html=get_score_html
    )

# ==========================================
# 4. LOGICA DE SCOR & TELEGRAM
# ==========================================

def tg(msg):
    print(f"📢 TENTATIVĂ TRIMITERE: {msg[:15]}...")
    if not BOT_TOKEN or "SECRET" in BOT_TOKEN: return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ REFUZ TELEGRAM ({r.status_code}): {r.text}")
        else:
            print("✅ MESAJ TRIMIS!")
    except Exception as e:
        print(f"❌ EROARE CONEXIUNE: {e}")

def fetch(addr):
    try:
        r = requests.get(API, params={"user": addr, "limit": 20}, timeout=10)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_usd(e):
    try: return float(e.get("size", 0)) * float(e.get("price", 0))
    except: return 0

# 🔥 ALGORITMUL DE SCOR (0-10)
def calc_score(cluster_val, price, is_ping_pong):
    score = 5.0 # Start neutru

    # 1. Volum (Cluster)
    if cluster_val >= BIG: score += 3.0
    elif cluster_val >= NORMAL: score += 2.0
    elif cluster_val >= MINI: score += 1.0
    
    # 2. Pret (Entry Point)
    if price < 0.85: score += 2.0     # Intrare superba
    elif price < 0.92: score += 1.0   # Intrare buna
    elif price > 0.97: score -= 2.0   # Prea scump (Risc)
    
    # 3. Penalizari
    if is_ping_pong: score -= 4.0     # Scalping periculos
    
    return max(0.0, min(10.0, score))

def check_nightly_summary():
    # Raport trimis la 07:00 RO Time
    now = datetime.now(RO)
    today_str = now.strftime("%Y-%m-%d")
    
    if now.hour == 7 and global_state["last_summary_day"] != today_str:
        my_total = sum(v for k, v in global_state["positions"].items() if k.startswith(SELF))
        
        msg = (
            f"☕ <b>Raport Dimineața ({today_str})</b>\n\n"
            f"🌙 <b>Activitate Noapte:</b>\n"
            f"• Clustere Noi: {global_state['nightly_stats']['new_clusters']}\n"
            f"• Ieșiri Majore: {global_state['nightly_stats']['big_exits']}\n\n"
            f"💼 <b>Capital Activ:</b> ${my_total:.0f}\n"
            f"<i>Botul rulează non-stop.</i>"
        )
        tg(msg)
        global_state["last_summary_day"] = today_str
        global_state["nightly_stats"] = {"new_clusters": 0, "big_exits": 0}
        save()

def check_portfolio_health():
    # Monitorizeaza pozitiile TALE pentru exit
    for key, my_amount in global_state["positions"].items():
        if not key.startswith(SELF) or my_amount < 10: continue
        parts = key.split("|")
        market_key = f"{parts[1]}|{parts[2]}"
        
        current_price = global_state["market_prices"].get(market_key, 0.5)
        current_score = global_state["scores"].get(market_key, 5.0)
        
        # Take Profit la 0.98
        if current_price >= 0.98:
            warn_key = f"tp_{market_key}"
            if warn_key not in global_state["last"]:
                tg(f"💰 <b>TAKE PROFIT!</b>\n{parts[1]}\nPreț: {current_price*100:.1f}¢ -> Risc maxim de blocaj.")
                global_state["last"][warn_key] = time.time()
        
        # Stop Loss / Bad Score
        elif current_score < 3.0:
            warn_key = f"low_{market_key}"
            if time.time() - global_state["last"].get(warn_key, 0) > 14400: # O data la 4 ore
                tg(f"⚠️ <b>EXIT SIGNAL (Scor {current_score:.1f})</b>\n{parts[1]}\nBalenele au ieșit, trendul s-a rupt.")
                global_state["last"][warn_key] = time.time()

# ==========================================
# 5. LOOP PRINCIPAL
# ==========================================

def bot_loop():
    load()
    print("Bot loop started.")
    tg("✅ <b>SYSTEM ONLINE</b>\nToate sistemele (Scor, Clustere, Dropper) sunt active.") 
    
    while True:
        try:
            check_nightly_summary()
            
            for name, addr in {**TRADERS, SELF: SELF_ADDR}.items():
                events = fetch(addr)
                if not events: continue

                # Initializare fara spam
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
                    action = e.get("type", "").lower() 
                    val = get_usd(e)
                    price = float(e.get("price", 0.5))
                    
                    pos_key = f"{name}|{title}|{side}"
                    market_key = f"{title}|{side}"
                    global_state["market_prices"][market_key] = price

                    # --- 1. ACTUALIZARE CLUSTER INSTANT ---
                    cluster_sum = 0
                    for k, v in global_state["positions"].items():
                        if market_key in k and not k.startswith(SELF): cluster_sum += v
                    
                    # --- 2. DETECTARE PING-PONG ---
                    is_ping_pong = False
                    if action == "sell":
                        last_buy = global_state["last_buy_times"].get(pos_key, 0)
                        if ts - last_buy < 600: # 10 minute
                            is_ping_pong = True

                    if action == "buy" or action == "trade":
                        global_state["last_buy_times"][pos_key] = ts

                    # --- 3. CALCUL SCOR ---
                    current_score = calc_score(cluster_sum, price, is_ping_pong)
                    global_state["scores"][market_key] = current_score

                    # ================================
                    # A. LOGICA PENTRU TINE (SELF)
                    # ================================
                    if name == SELF:
                        if action == "buy" or action == "trade":
                            # Calcul Pret Mediu Ponderat
                            old_amt = global_state["positions"].get(pos_key, 0)
                            old_price = global_state["entry_prices"].get(pos_key, price)
                            
                            total_amt = old_amt + val
                            if total_amt > 0:
                                avg_price = ((old_amt * old_price) + (val * price)) / total_amt
                            else:
                                avg_price = price
                            
                            global_state["positions"][pos_key] = total_amt
                            global_state["entry_prices"][pos_key] = avg_price

                            tg(f"🔔 <b>AI CUMPĂRAT</b>\n{title} ({side})\n💲{val:.0f} | Scor: <b>{current_score:.1f}/10</b>")
                            
                        elif action == "sell":
                            global_state["positions"][pos_key] = max(global_state["positions"].get(pos_key, 0) - val, 0)
                            if global_state["positions"][pos_key] < 10: 
                                global_state["entry_prices"].pop(pos_key, None) # Reset daca am iesit
                            
                            # Analiza Exit
                            verdict = "Neutral"
                            if price >= 0.95: verdict = "✅ TAKE PROFIT (Ideal)"
                            elif current_score < 4.0: verdict = "✅ SMART EXIT (Scor mic)"
                            elif current_score > 8.0: verdict = "⚠️ PANIC SELL? (Scor mare)"

                            tg(f"🔔 <b>AI VÂNDUT</b>\n{title} ({side})\nRecuperat: ${val:.0f}\nAnaliză: {verdict}")

                    # ================================
                    # B. LOGICA PENTRU TRADERI
                    # ================================
                    else:
                        if action == "buy" or action == "trade":
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            
                            if val >= MIN_BUY_ALERT:
                                label = "BUY" if action == "buy" else "🔄 TRADE"
                                pp_msg = "\n⚠️ <b>PING-PONG (Risc)</b>" if is_ping_pong else ""
                                
                                # Adaugam interpretarea scorului
                                score_msg = f"{current_score:.1f}/10"
                                if current_score >= 8: score_msg += " (STRONG)"
                                elif current_score <= 4: score_msg += " (WEAK)"
                                
                                tg(f"👤 <b>{name}</b> {label} {pp_msg}\n{title} ({side})\n💲 {val:.0f} @ {price*100:.1f}¢\n🎯 Scor Decizie: <b>{score_msg}</b>")
                            else:
                                print(f"❌ IGNORAT {action.upper()}: ${val:.0f} < ${MIN_BUY_ALERT}")

                        elif action == "sell":
                            old = global_state["positions"].get(pos_key, 0)
                            global_state["positions"][pos_key] = max(old - val, 0)
                            
                            if val >= MIN_SELL_ALERT:
                                reason = "(Dump)"
                                if price > 0.90: reason = "(Profit)"
                                elif current_score < 4.0: reason = "(Abandon)"
                                
                                tg(f"📉 <b>{name}</b> SELL {reason}\n{title} | ${val:.0f}")
                            else:
                                print(f"❌ IGNORAT SELL: ${val:.0f} < ${MIN_SELL_ALERT}")

                    # ================================
                    # C. DASHBOARD LOG
                    # ================================
                    if val >= MIN_DASHBOARD:
                        note = f"Scor: {current_score:.1f}"
                        if is_ping_pong: note += " | ⚠️ PingPong"
                        
                        global_state["trade_log"].append({
                            "time": datetime.now(RO).strftime("%H:%M"),
                            "trader": name,
                            "action": action,
                            "market": f"{title} ({side})",
                            "value": int(val),
                            "note": note
                        })

                global_state["last"][name] = new_max_ts

            # Calcul Clustere (Pentru Alerte Noi)
            clusters = {}
            for k, v in global_state["positions"].items():
                parts = k.split("|")
                if len(parts) == 3 and parts[0] != SELF:
                    c_key = f"{parts[1]}|{parts[2]}"
                    clusters[c_key] = clusters.get(c_key, 0) + v

            for c_key, total in clusters.items():
                p = global_state["market_prices"].get(c_key, 0.5)
                # Scor recalculat pe baza clusterului total
                final_score = calc_score(total, p, False)
                global_state["scores"][c_key] = final_score
                
                last_sent = global_state["clusters_sent"].get(c_key, 0)
                # Alerta doar daca creste semnificativ (20%)
                if total >= MINI and total > last_sent * 1.2:
                    tg(f"📊 <b>CLUSTER NOU {final_score:.1f}/10</b>\n{c_key}\nBani Smart: ${total:,.0f}")
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
