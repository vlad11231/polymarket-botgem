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
# 1. CONFIGURARE "MULTI-AI"
# ==========================================

BOT_TOKEN = "8408560792:AAEEaQNwcMtUM3NhG6muehfax6G-PkE0FL8" 
CHAT_ID = "6854863928"

PORT = int(os.getenv("PORT", 5000))

API_ACTIVITY = "https://data-api.polymarket.com/activity"
API_POSITIONS = "https://data-api.polymarket.com/positions"
API_CLOB = "https://clob.polymarket.com/price" 

POLL = 60 

# Limite
MIN_BUY_ALERT = 800    
MIN_SELL_ALERT = 1000  
WHALE_ALERT = 5000      
MIN_DASHBOARD_LOG = 500 

# Clustere
MINI = 6000      
NORMAL = 10000
BIG = 20000
MAX_DASHBOARD_CLUSTERS = 20 

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
# 2. STATE MANAGEMENT & CACHE
# ==========================================
global_state = {
    "initialized": False,
    "bot_start_time": time.time(),
    "last": {},           
    "positions": {},      
    "my_portfolio": [],   
    "trade_log": [],      
    "scores": {},         
    "market_prices": {},  
    "last_buy_times": {}, 
    "cluster_participants": {}, 
    "cluster_created_at": {}, 
    "clusters_sent": {},
    "last_summary_day": "",
    "nightly_stats": {"new_clusters": 0, "big_exits": 0},
    "last_update": "Never"
}

price_cache = {} 

def sanitize_state():
    defaults = {
        "clusters_sent": {},
        "cluster_created_at": {},
        "cluster_participants": {},
        "scores": {},
        "market_prices": {},
        "positions": {}
    }
    for k, v in defaults.items():
        if k not in global_state:
            global_state[k] = v

def load():
    global global_state
    if not DATA_DIR.exists(): DATA_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            current_start = global_state["bot_start_time"]
            global_state.update(saved)
            global_state["bot_start_time"] = current_start
            sanitize_state()
        except: 
            sanitize_state()

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
    <title>PolyBot Multi-Opp</title>
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
        .details { font-size: 0.85em; color: #aaa; }
        
        .rec-box { border-left: 4px solid; padding-left: 10px; margin-bottom: 10px; background: rgba(0,0,0,0.2); padding: 8px; }
        .rec-swap { border-color: #fdcb6e; }
        .rec-sell { border-color: #d63031; }
        .rec-buy  { border-color: #00b894; }
        .rec-ok   { border-color: #00d2ff; }
        .rec-text { font-size: 1.1em; font-weight: 500; }
        .rec-sub  { font-size: 0.9em; color: #aaa; }
    </style>
</head>
<body>
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <h1>🤖 PolyBot Dashboard</h1>
        <div>Update: <b>{{ state.last_update }}</b> | <span style="color:#00b894">● Online</span></div>
    </div>

    <div class="card" style="border: 1px solid #00d2ff;">
        <h3 style="margin-top:0;">🧠 Analiză Strategică (AI)</h3>
        {% if recommendations %}
            {% for rec in recommendations %}
            <div class="rec-box {{ rec.class }}">
                <div class="rec-text">{{ rec.text|safe }}</div>
                <div class="rec-sub">{{ rec.reason }}</div>
            </div>
            {% endfor %}
        {% else %}
            <div class="rec-box rec-ok">
                <div class="rec-text">✅ Portofoliu Stabil</div>
                <div class="rec-sub">Nu sunt necesare vânzări urgente. Aștept oportunități noi...</div>
            </div>
        {% endif %}
    </div>

    <div class="card">
        <h3>💼 Portofoliul Tău ({{ self_name }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Acțiuni</th><th>Valoare Reală ($)</th><th>Preț Curent</th></tr></thead>
            <tbody>
                {% for pos in state.my_portfolio %}
                <tr class="self-row">
                    <td>{{ pos.title }}</td>
                    <td><span class="{{ 'yes' if 'YES' in pos.outcome else 'no' }}">{{ pos.outcome }}</span></td>
                    <td>{{ pos.size }}</td>
                    <td><b>${{ pos.value }}</b></td>
                    <td>{{ pos.display_price }}</td>
                </tr>
                {% else %}
                <tr><td colspan="5" style="text-align:center; color:#666;">Se încarcă datele din piață...</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>🔥 Active New Clusters (Apărute ACUM > ${{ mini }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Traderi & Investiții</th><th>Total ($)</th><th>Preț</th><th>Scor</th></tr></thead>
            <tbody>
                {% for c in smart_clusters %}
                <tr>
                    <td>{{ c.key.split('|')[0] }}</td>
                    <td><span class="{{ 'yes' if 'YES' in c.key else 'no' }}">{{ c.key.split('|')[1] }}</span></td>
                    <td>
                        👥 {{ c.count }}<br>
                        <span class="details">{{ c.breakdown }}</span>
                    </td>
                    <td><b>${{ c.vol_fmt }}</b></td>
                    <td>{{ c.price }}¢</td>
                    <td>{{ c.score_html|safe }}</td>
                </tr>
                {% else %}
                <tr><td colspan="6" style="text-align:center; color:#666;">Niciun cluster <b>NOU</b> apărut de la pornirea botului.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>💰 Top Investiții Comune (All Time - Total Deținut)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Participanți (Min 2)</th><th>Total Deținut ($)</th><th>Preț Curent</th></tr></thead>
            <tbody>
                {% for c in all_shared %}
                <tr>
                    <td>{{ c.key.split('|')[0] }}</td>
                    <td><span class="{{ 'yes' if 'YES' in c.key else 'no' }}">{{ c.key.split('|')[1] }}</span></td>
                    <td>
                        👥 {{ c.count }}<br>
                        <span class="details">{{ c.breakdown }}</span>
                    </td>
                    <td><b>${{ c.vol_fmt }}</b></td>
                    <td>{{ c.price }}¢</td>
                </tr>
                {% else %}
                <tr><td colspan="5" style="text-align:center; color:#666;">Se scanează portofoliile traderilor...</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>📜 Istoric Decizii (Min ${{ min_dash }})</h3>
        <table>
            <thead><tr><th>Ora</th><th>Trader</th><th>Acțiune</th><th>Piață</th><th>Suma</th><th>Preț</th><th>Notă / Scor</th></tr></thead>
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
                    <td>{{ log.price_fmt }}</td> 
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
    def get_cluster_data_raw(key):
        total = 0
        participants = []
        for pos_k, val in global_state["positions"].items():
            if f"{key}" in pos_k and not pos_k.startswith(SELF):
                total += val
                name = pos_k.split("|")[0]
                participants.append((name, val))
        return total, list(set([p[0] for p in participants])), participants

    # 1. AI RECS
    recs = []
    my_weakest_score = 10.0
    my_weakest_market = None
    my_owned_markets = []

    # -- Analiza Portofoliu --
    for pos in global_state["my_portfolio"]:
        m_key = f"{pos['title']}|{pos['outcome']}"
        my_owned_markets.append(m_key)
        try:
            p_val = float(pos['price'].replace('¢', '').split()[0]) / 100.0
        except: p_val = 0.0
        
        current_score = global_state["scores"].get(m_key, 5.0)

        if p_val >= 0.94:
            recs.append({"class": "rec-sell", "text": f"💰 <b>VINDE: {pos['title']}</b>", "reason": f"Profit maxim ({p_val*100:.0f}¢)."})
        elif current_score < 4.0:
            recs.append({"class": "rec-sell", "text": f"⚠️ <b>IEȘI DIN: {pos['title']}</b>", "reason": f"Scor AI slab {current_score:.1f}."})
            if current_score < my_weakest_score:
                my_weakest_score = current_score
                my_weakest_market = pos['title']

    # -- Analiza Oportunitati (Multiple) --
    best_opp_score = 0
    best_opp_key = None
    potential_buys = []

    smart_clusters = []   
    all_shared = []       

    unique_markets = set()
    for pos_k in global_state["positions"]:
        parts = pos_k.split("|")
        if len(parts) == 3 and parts[0] != SELF:
            unique_markets.add(f"{parts[1]}|{parts[2]}")

    for key in unique_markets:
        vol, users, parts = get_cluster_data_raw(key)
        
        p_live = global_state['market_prices'].get(key, 0.5)
        created_at = global_state["cluster_created_at"].get(key, 0)
        score = calc_score(vol, len(users), p_live, False, created_at)
        
        if len(users) >= 2:
            sums = {}
            for name, val in parts: sums[name] = sums.get(name, 0) + val
            sorted_sums = sorted(sums.items(), key=lambda x: x[1], reverse=True)
            breakdown = ", ".join([f"{n}: ${v:,.0f}" for n, v in sorted_sums if v > 10])

            price_txt = f"{p_live * 100:.1f}"
            
            color = "score-low"
            if score >= 8: color = "score-high"
            elif score >= 6: color = "score-med"
            score_html = f'<span class="{color}">{score:.1f}/10</span>'
            
            cluster_obj = {
                "key": key, "vol": vol, "vol_fmt": f"{vol:,.0f}",
                "count": len(users), "breakdown": breakdown,
                "price": price_txt, "score_html": score_html
            }

            all_shared.append(cluster_obj)

            is_new = False
            if created_at > global_state["bot_start_time"]:
                is_new = True

            if vol >= MINI and is_new:
                smart_clusters.append(cluster_obj)
            
            # Colectam potentiale cumparari (Top 3)
            if key not in my_owned_markets and score >= 8.0 and vol >= MINI:
                potential_buys.append((key, score))
                # Pastram si cel mai bun pt swap logic
                if score > best_opp_score:
                    best_opp_score = score
                    best_opp_key = key

    smart_clusters.sort(key=lambda x: x["vol"], reverse=True)
    all_shared.sort(key=lambda x: x["vol"], reverse=True)

    # -- RECOMANDARI MULTIPLE --
    # Sortam descrescator dupa scor si luam top 3
    potential_buys.sort(key=lambda x: x[1], reverse=True)
    
    for opp_key, opp_score in potential_buys[:3]:
        recs.append({
            "class": "rec-buy", 
            "text": f"🚀 <b>OPORTUNITATE: {opp_key.split('|')[0]}</b>", 
            "reason": f"Scor {opp_score:.1f}. Cluster puternic."
        })
    
    # SWAP Logic (doar cu cel mai bun)
    if my_weakest_market and best_opp_key:
        if my_weakest_score < 5.0 and best_opp_score > 7.5:
            recs.append({"class": "rec-swap", "text": f"🔄 <b>SWAP: {my_weakest_market} ➔ {best_opp_key.split('|')[0]}</b>", "reason": "Upgrade Scor (AI Optimization)."})

    return render_template_string(
        HTML_TEMPLATE, 
        state=global_state, 
        self_name=SELF, 
        recommendations=recs, 
        min_dash=MIN_DASHBOARD_LOG,
        mini=MINI,
        smart_clusters=smart_clusters[:MAX_DASHBOARD_CLUSTERS],
        all_shared=all_shared[:20]
    )

# ==========================================
# 4. LOGICA & SYNC
# ==========================================

def tg(msg):
    print(f"📢 MSG: {msg[:20]}...")
    if not BOT_TOKEN or "SECRET" in BOT_TOKEN: return
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                          json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"❌ ERR: {e}")

def safe_float(v):
    try: return float(v)
    except: return 0.0

def get_real_price(token_id):
    if not token_id: return 0
    if token_id in price_cache:
        ts, price = price_cache[token_id]
        if time.time() - ts < 60: return price
            
    try:
        r = requests.get(API_CLOB, params={"token_id": token_id, "side": "buy"}, timeout=2)
        if r.status_code == 200:
            data = r.json()
            price = safe_float(data.get("price"))
            if price > 0:
                price_cache[token_id] = (time.time(), price)
                return price
    except: pass
    return 0

def sync_trader_positions():
    print("♻️ Sincronizare Traderi...")
    for name, addr in TRADERS.items():
        try:
            r = requests.get(API_POSITIONS, params={"user": addr}, timeout=5)
            if r.status_code == 200:
                data = r.json()
                for item in data:
                    size = safe_float(item.get("size"))
                    if size < 5: continue
                    
                    title = item.get("title", "Unknown")
                    outcome = item.get("outcome", "YES").upper()
                    
                    p = safe_float(item.get("price"))
                    if p == 0: p = get_real_price(item.get("asset"))
                    if p == 0: p = safe_float(item.get("avgBuyPrice"))
                    
                    val = size * p
                    
                    pos_key = f"{name}|{title}|{outcome}"
                    global_state["positions"][pos_key] = val
                    
                    if p > 0:
                        global_state["market_prices"][f"{title}|{outcome}"] = p
                        
        except Exception as e:
            print(f"⚠️ Err sync trader {name}: {e}")

def sync_portfolio():
    print("♻️ Sincronizare Portofoliu Tău...")
    try:
        r = requests.get(API_POSITIONS, params={"user": SELF_ADDR}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            real_portfolio = []
            
            for item in data:
                size = safe_float(item.get("size"))
                if size < 1: continue 
                
                title = item.get("title", "Unknown Market")
                outcome = item.get("outcome", "YES").upper()
                
                p = safe_float(item.get("price"))
                if p == 0: p = get_real_price(item.get("asset"))
                if p == 0: p = safe_float(item.get("avgBuyPrice"))
                
                if p == 0: p = global_state["market_prices"].get(f"{title}|{outcome}", 0)

                value = size * p
                
                if p > 0: display_price = f"{p*100:.1f}¢"
                else: display_price = "N/A"

                real_portfolio.append({
                    "title": title, "outcome": outcome,
                    "size": f"{size:.0f}", "value": f"{value:.0f}",
                    "price": f"{p*100:.1f}",
                    "display_price": display_price
                })
            
            global_state["my_portfolio"] = real_portfolio
            print(f"✅ Portofoliu Sync: {len(real_portfolio)} poziții.")
    except Exception as e:
        print(f"⚠️ Eroare Sync: {e}")

def fetch(addr):
    try:
        r = requests.get(API_ACTIVITY, params={"user": addr, "limit": 20}, timeout=10)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_usd(e):
    try: return float(e.get("size", 0)) * float(e.get("price", 0))
    except: return 0

def calc_score(cluster_val, participants_count, price, is_ping_pong, created_at):
    score = 5.0 
    if cluster_val >= BIG: score += 2.5      
    elif cluster_val >= NORMAL: score += 1.5 
    elif cluster_val >= MINI: score += 1.0   
    if participants_count >= 3: score += 1.5
    elif participants_count == 2: score += 0.5
    if price > 0:
        if price < 0.50: score += 1.0     
        elif price < 0.85: score += 0.5   
        elif price > 0.95: score -= 2.0   
    if is_ping_pong: score -= 5.0 
    return max(0.0, min(10.0, score))

def check_nightly_summary():
    now = datetime.now(RO)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == 7 and global_state["last_summary_day"] != today_str:
        total_val = sum(float(p["value"]) for p in global_state["my_portfolio"])
        tg(f"☕ <b>Raport Dimineața</b>\nClustere Noi: {global_state['nightly_stats']['new_clusters']}\nPortofoliu: ${total_val:.0f}")
        global_state["last_summary_day"] = today_str
        global_state["nightly_stats"] = {"new_clusters": 0, "big_exits": 0}
        save()

# ==========================================
# 5. LOOP PRINCIPAL
# ==========================================

def bot_loop():
    load()
    print("Bot loop started.")
    tg("✅ <b>SYSTEM RESTARTED</b>\nFeatures: Multi-Opp AI & Fixed Status") 
    
    sync_trader_positions()
    sync_portfolio()
    
    loop_count = 0

    while True:
        try:
            loop_count += 1
            if loop_count % 5 == 0: sync_portfolio()
            if loop_count % 10 == 0: sync_trader_positions()

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
                    raw_side = e.get("outcome", e.get("side", "YES"))
                    side = raw_side.upper() 
                    
                    action = e.get("type", "").lower() 
                    val = get_usd(e)
                    price = float(e.get("price", 0))
                    
                    pos_key = f"{name}|{title}|{side}"
                    market_key = f"{title}|{side}"
                    
                    if price > 0:
                        global_state["market_prices"][market_key] = price

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
                        if ts - last_buy < 3600 and last_buy != 0: 
                            is_ping_pong = True
                    if action == "buy" or action == "trade": global_state["last_buy_times"][pos_key] = ts

                    current_score = calc_score(cluster_sum, participants_count, price, is_ping_pong, 0)
                    global_state["scores"][market_key] = current_score

                    action_ro = "A CUMPĂRAT"
                    if action == "sell": action_ro = "A VÂNDUT"
                    side_emoji = "🟢" if "YES" in side else "🔴"
                    side_formatted = f"{side_emoji} <b>{side}</b>"
                    
                    if name == SELF:
                        if action in ["buy", "trade"]:
                            tg(f"🔔 <b>TU {action_ro} {side_formatted}</b>\n🏆 {title}\n💲{val:.0f} | Scor: <b>{current_score:.1f}/10</b>")
                        elif action == "sell":
                            tg(f"🔔 <b>TU {action_ro} {side_formatted}</b>\n🏆 {title}\nRecuperat: ${val:.0f}")
                        threading.Thread(target=sync_portfolio).start()

                    else:
                        if action in ["buy", "trade"]:
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            if val >= MIN_BUY_ALERT:
                                whale_tag = " 🐋 <b>WHALE MOVE!</b>" if val >= WHALE_ALERT else ""
                                tg(f"👤 <b>{name} {action_ro} {side_formatted}</b>{whale_tag}\n🏆 {title}\n💲 {val:.0f} @ {price*100:.1f}¢\n🎯 Scor: <b>{current_score:.1f}/10</b>")

                        elif action == "sell":
                            old = global_state["positions"].get(pos_key, 0)
                            global_state["positions"][pos_key] = max(old - val, 0)
                            
                            if val >= MIN_SELL_ALERT:
                                reason = "(Profit)" if price > 0.90 else "(Loss)"
                                if cluster_sum + val >= MINI and participants_count >= 2:
                                    pp_warn = "⚠️ <b>PING-PONG</b>" if is_ping_pong else "📉 <b>CLUSTER SLĂBIT</b>"
                                    tg(f"{pp_warn}\n👤 <b>{name} {action_ro} {side_formatted}</b>\n🏆 {title}\nSuma: ${val:.0f} {reason}")
                                else:
                                    tg(f"📉 <b>{name} {action_ro} {side_formatted}</b>\n🏆 {title}\nSuma: ${val:.0f} {reason}")

                    if val >= MIN_DASHBOARD_LOG:
                        note = f"Scor: {current_score:.1f}"
                        if is_ping_pong: note += " | ⚠️ PingPong"
                        if val >= WHALE_ALERT: note += " | 🐋 Whale"
                        global_state["trade_log"].append({
                            "time": datetime.now(RO).strftime("%H:%M"),
                            "trader": name, "action": action,
                            "market": f"{title} ({side})", "value": int(val), 
                            "price_fmt": f"{price*100:.1f}¢",
                            "note": note
                        })

                global_state["last"][name] = new_max_ts

            processed_clusters = set()
            for k, v in global_state["positions"].items():
                parts = k.split("|")
                if len(parts) == 3 and parts[0] != SELF:
                    c_key = f"{parts[1]}|{parts[2]}" 
                    if c_key in processed_clusters: continue
                    processed_clusters.add(c_key)
                    
                    c_total = 0
                    c_users_set = set()
                    for sub_k, sub_v in global_state["positions"].items():
                        if c_key in sub_k and not sub_k.startswith(SELF):
                            c_total += sub_v
                            c_users_set.add(sub_k.split("|")[0])
                    
                    if len(c_users_set) >= 2 and c_total >= MINI:
                        # KEY FIX: Daca suntem in primul loop, setam 'created_at' la 0 (Istoric)
                        if c_key not in global_state["cluster_created_at"]:
                            if loop_count == 1:
                                global_state["cluster_created_at"][c_key] = 0 # Marcam ca VECHI
                                global_state["clusters_sent"][c_key] = c_total # Marcam ca trimis (Silent)
                            else:
                                global_state["cluster_created_at"][c_key] = time.time() # Marcam ca NOU

                        known_users = set(global_state["cluster_participants"].get(c_key, []))
                        new_users = c_users_set - known_users
                        if new_users and len(known_users) > 0 and loop_count > 1: # Doar dupa start
                            new_guy = list(new_users)[0]
                            c_side = c_key.split("|")[1]
                            c_emoji = "🟢" if "YES" in c_side else "🔴"
                            tg(f"🚨 <b>TRADER NOU ÎN COMUN!</b>\n👤 <b>{new_guy}</b> a intrat pe {c_emoji} {c_side}\n🏆 {c_key.split('|')[0]}\n💰 Total Acum: ${c_total:,.0f}")

                        global_state["cluster_participants"][c_key] = list(c_users_set)

                        last_sent = global_state["clusters_sent"].get(c_key, 0)
                        
                        # ALERTE DE CLUSTER DOAR DUPA LOOP 1 (SILENT START)
                        if loop_count > 1 and c_total > last_sent * 1.2:
                            created_ts = global_state["cluster_created_at"][c_key]
                            sc = calc_score(c_total, len(c_users_set), global_state["market_prices"].get(c_key, 0.5), False, created_ts)
                            c_side = c_key.split("|")[1]
                            c_emoji = "🟢" if "YES" in c_side else "🔴"
                            level = "MINI"
                            if c_total >= BIG: level = "MARE"
                            elif c_total >= NORMAL: level = "MEDIU"
                            
                            tg(f"📊 <b>CLUSTER {level} {c_emoji} {c_side}</b>\n🏆 {c_key.split('|')[0]}\n👥 {len(c_users_set)} Traderi\n💰 Total: ${c_total:,.0f}\n🎯 Scor: {sc:.1f}")
                            
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
