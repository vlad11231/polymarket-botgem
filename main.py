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
# 1. CONFIGURARE FINALĂ
# ==========================================

BOT_TOKEN = "8408560792:AAEEaQNwcMtUM3NhG6muehfax6G-PkE0FL8" 
CHAT_ID = "6854863928"

PORT = int(os.getenv("PORT", 5000))

# Doua surse de date: una pentru Alerte (Activity), una pentru Portofoliu (Positions)
API_ACTIVITY = "https://data-api.polymarket.com/activity"
API_POSITIONS = "https://data-api.polymarket.com/positions"

POLL = 60  # Verifică la fiecare 60 secunde

# Limite Alerte
MIN_BUY_ALERT = 500    
MIN_SELL_ALERT = 1000  
MIN_DASHBOARD_LOG = 250    

# Clustere
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
# 2. STATE MANAGEMENT
# ==========================================
global_state = {
    "initialized": False,
    "last": {},           
    "positions": {},      # Pozitii CALCULATE (pentru traderi)
    "my_portfolio": [],   # Pozitii REALE (sincronizate din API) - NOU!
    "trade_log": [],      
    "clusters_sent": {},  
    "scores": {},         
    "market_prices": {},  
    "last_buy_times": {}, 
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
            global_state.update(saved)
        except: pass

def save():
    if len(global_state["trade_log"]) > 200:
        global_state["trade_log"] = global_state["trade_log"][-200:]
    STATE_FILE.write_text(json.dumps(global_state, indent=2))

# ==========================================
# 3. DASHBOARD WEB
# ==========================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>PolyBot Platinum</title>
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
        .yes { color: #00b894; font-weight: bold; }
        .no { color: #d63031; font-weight: bold; }
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
        <h3>💼 Portofoliul Tău REAL ({{ self_name }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Acțiuni (Shares)</th><th>Valoare ($)</th><th>Preț Curent</th></tr></thead>
            <tbody>
                {% for pos in state.my_portfolio %}
                <tr class="self-row">
                    <td>{{ pos.title }}</td>
                    <td><span class="{{ 'yes' if 'YES' in pos.outcome else 'no' }}">{{ pos.outcome }}</span></td>
                    <td>{{ pos.size }}</td>
                    <td><b>${{ pos.value }}</b></td>
                    <td>{{ pos.price }}¢</td>
                </tr>
                {% else %}
                <tr><td colspan="5" style="text-align:center; color:#666;">Se încarcă datele sau portofoliu gol...</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>🔥 Active Clusters (Min 2 Traderi)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Nr. Traderi</th><th>Volum Total ($)</th><th>Preț</th><th>Scor</th></tr></thead>
            <tbody>
                {% for key, score in state.scores.items() %}
                {% if get_cluster_count(key) >= 2 and get_cluster_vol_int(key) >= 2500 %}
                <tr>
                    <td>{{ key.split('|')[0] }}</td>
                    <td><span class="{{ 'yes' if 'YES' in key else 'no' }}">{{ key.split('|')[1] }}</span></td>
                    <td>👥 {{ get_cluster_count(key) }}</td>
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
                    <td>
                        <span class="tag {{ 'buy' if log.action == 'buy' else ('sell' if log.action == 'sell' else 'trade') }}">{{ log.action|upper }}</span>
                        <span class="{{ 'yes' if 'YES' in log.market else 'no' }}">{{ log.market.split('(')[1].replace(')', '') }}</span>
                    </td>
                    <td>{{ log.market.split('(')[0] }}</td>
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
    def get_cluster_data(key):
        total = 0
        participants = set()
        for pos_k, val in global_state["positions"].items():
            if f"{key}" in pos_k and not pos_k.startswith(SELF):
                total += val
                participants.add(pos_k.split("|")[0])
        return total, len(participants)

    def get_cluster_vol(key):
        v, _ = get_cluster_data(key)
        return f"{v:,.0f}"

    def get_cluster_vol_int(key):
        v, _ = get_cluster_data(key)
        return v
    
    def get_cluster_count(key):
        _, c = get_cluster_data(key)
        return c

    def get_price(key):
        return f"{global_state['market_prices'].get(key, 0) * 100:.1f}"
    
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
        min_dash=MIN_DASHBOARD_LOG,
        get_cluster_vol=get_cluster_vol, 
        get_cluster_vol_int=get_cluster_vol_int,
        get_cluster_count=get_cluster_count,
        get_price=get_price,
        get_score_html=get_score_html
    )

# ==========================================
# 4. LOGICA & SINCRONIZARE PORTOFOLIU (NOU)
# ==========================================

def tg(msg):
    print(f"📢 TENTATIVĂ TRIMITERE: {msg[:15]}...")
    if not BOT_TOKEN or "SECRET" in BOT_TOKEN: return

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ REFUZ TELEGRAM ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"❌ EROARE CONEXIUNE: {e}")

# --- FUNCTIA MAGICA: ADUCE PORTOFOLIUL REAL DIN BLOCKCHAIN ---
def sync_portfolio():
    print("♻️ Sincronizare Portofoliu Real...")
    try:
        r = requests.get(API_POSITIONS, params={"user": SELF_ADDR}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            real_portfolio = []
            
            # Filtram si formatam datele primite de la Polymarket
            for item in data:
                size = float(item.get("size", 0))
                if size < 1: continue # Ignoram "praf"
                
                title = item.get("title", "Unknown Market")
                outcome = item.get("outcome", "YES")
                price = float(item.get("price", 0))
                value = size * price
                
                real_portfolio.append({
                    "title": title,
                    "outcome": outcome,
                    "size": f"{size:.0f}",
                    "value": f"{value:.0f}",
                    "price": f"{price*100:.1f}"
                })
            
            # Actualizam in memorie
            global_state["my_portfolio"] = real_portfolio
            print(f"✅ Portofoliu Sincronizat: {len(real_portfolio)} poziții active.")
    except Exception as e:
        print(f"⚠️ Eroare Sync Portfolio: {e}")

def fetch(addr):
    try:
        r = requests.get(API_ACTIVITY, params={"user": addr, "limit": 20}, timeout=10)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_usd(e):
    try: return float(e.get("size", 0)) * float(e.get("price", 0))
    except: return 0

def calc_score(cluster_val, participants_count, price, is_ping_pong):
    score = 5.0 
    if participants_count >= 2:
        if cluster_val >= BIG: score += 4.0
        elif cluster_val >= NORMAL: score += 3.0
        elif cluster_val >= MINI: score += 1.5
    else:
        score -= 1.0
    if price < 0.85: score += 1.5
    elif price < 0.92: score += 0.5
    elif price > 0.97: score -= 2.0 
    if is_ping_pong: score -= 4.0
    return max(0.0, min(10.0, score))

def check_nightly_summary():
    now = datetime.now(RO)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == 7 and global_state["last_summary_day"] != today_str:
        # Calculam valoarea reala din portofoliul sincronizat
        total_val = sum(float(p["value"]) for p in global_state["my_portfolio"])
        
        tg(f"☕ <b>Raport Dimineața ({today_str})</b>\n\n"
           f"🌙 <b>Noaptea trecută:</b>\n"
           f"• Clustere Noi: {global_state['nightly_stats']['new_clusters']}\n"
           f"• Ieșiri Majore: {global_state['nightly_stats']['big_exits']}\n\n"
           f"💼 <b>Portofoliu Activ:</b> ${total_val:.0f}\n")
        global_state["last_summary_day"] = today_str
        global_state["nightly_stats"] = {"new_clusters": 0, "big_exits": 0}
        save()

# ==========================================
# 5. LOOP PRINCIPAL
# ==========================================

def bot_loop():
    load()
    print("Bot loop started.")
    tg("✅ <b>SYSTEM RESTARTED</b>\nModul: SYNC PORTFOLIO (Văd și pozițiile vechi!)") 
    
    # Facem o sincronizare la start
    sync_portfolio()
    
    loop_count = 0

    while True:
        try:
            # Sincronizam portofoliul o data la 5 minute (nu la fiecare secunda)
            loop_count += 1
            if loop_count % 5 == 0: # La fiecare 5 cicluri (aprox 5 min)
                sync_portfolio()

            check_nightly_summary()
            
            for name, addr in {**TRADERS, SELF: SELF_ADDR}.items():
                events = fetch(addr)
                if not events: continue

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

                    # --- CLUSTERE & SCORING ---
                    cluster_participants = set()
                    cluster_sum = 0
                    for k, v in global_state["positions"].items():
                        if market_key in k and not k.startswith(SELF): 
                            cluster_sum += v
                            cluster_participants.add(k.split("|")[0])
                    
                    participants_count = len(cluster_participants)
                    is_ping_pong = False
                    if action == "sell":
                        last_buy = global_state["last_buy_times"].get(pos_key, 0)
                        if ts - last_buy < 600: is_ping_pong = True
                    if action == "buy" or action == "trade": global_state["last_buy_times"][pos_key] = ts

                    current_score = calc_score(cluster_sum, participants_count, price, is_ping_pong)
                    global_state["scores"][market_key] = current_score

                    # ================================
                    # A. ALERTE PENTRU TINE (Actiune Imediata)
                    # ================================
                    if name == SELF:
                        # Nota: Nu mai actualizam portofoliul aici manual, se face prin sync
                        if action == "buy" or action == "trade":
                            tg(f"🔔 <b>AI CUMPĂRAT {side}</b>\n{title}\n💲{val:.0f} | Scor: <b>{current_score:.1f}/10</b>")
                        elif action == "sell":
                            tg(f"🔔 <b>AI VÂNDUT {side}</b>\n{title}\nRecuperat: ${val:.0f}")
                        
                        # Fortam un sync imediat dupa ce faci tu o miscare
                        threading.Thread(target=sync_portfolio).start()

                    # ================================
                    # B. ALERTE TRADERI
                    # ================================
                    else:
                        if action == "buy" or action == "trade":
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            if val >= MIN_BUY_ALERT:
                                label = "BUY" if action == "buy" else "🔄 TRADE"
                                pp_msg = "⚠️ <b>PING-PONG</b>" if is_ping_pong else ""
                                tg(f"👤 <b>{name} {label} {side}</b>\n{title}\n💲 {val:.0f} @ {price*100:.1f}¢\n{pp_msg}\n🎯 Scor Decizie: <b>{current_score:.1f}/10</b>")

                        elif action == "sell":
                            old = global_state["positions"].get(pos_key, 0)
                            global_state["positions"][pos_key] = max(old - val, 0)
                            if val >= MIN_SELL_ALERT:
                                reason = "(Profit)" if price > 0.90 else "(Dump/Loss)"
                                tg(f"📉 <b>{name} SELL {side}</b> {reason}\n{title}\nSuma: ${val:.0f}")

                    # ================================
                    # C. DASHBOARD LOG
                    # ================================
                    if val >= MIN_DASHBOARD_LOG:
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

            # --- LOGICA CLUSTER ---
            processed_clusters = set()
            for k, v in global_state["positions"].items():
                parts = k.split("|")
                if len(parts) == 3 and parts[0] != SELF:
                    c_key = f"{parts[1]}|{parts[2]}" 
                    if c_key in processed_clusters: continue
                    processed_clusters.add(c_key)
                    c_total = 0
                    c_users = set()
                    for sub_k, sub_v in global_state["positions"].items():
                        if c_key in sub_k and not sub_k.startswith(SELF):
                            c_total += sub_v
                            c_users.add(sub_k.split("|")[0])
                    
                    if len(c_users) >= 2:
                        last_sent = global_state["clusters_sent"].get(c_key, 0)
                        if c_total >= MINI and c_total > last_sent * 1.2:
                            p = global_state["market_prices"].get(c_key, 0.5)
                            sc = global_state["scores"].get(c_key, 5.0)
                            tg(f"📊 <b>CLUSTER NOU {sc:.1f}/10</b>\n{c_key.replace('|', ' ')}\n👥 Participanți: {len(c_users)}\n💰 Total Smart Money: ${c_total:,.0f}")
                            global_state["clusters_sent"][c_key] = c_total
                            if last_sent == 0: global_state["nightly_stats"]["new_clusters"] += 1

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
