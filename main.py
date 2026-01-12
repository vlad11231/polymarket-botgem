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
# 1. CONFIGURARE "FINAL FIXED"
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

# !!! FIX: Variabila definita clar aici !!!
MIN_TRADER_DISPLAY = 1000 

# Shadow
SHADOW_START_CAPITAL = 2000
BASE_BET_SIZE = 200 

RO = pytz.timezone("Europe/Bucharest")
DATA_DIR = Path("/app/data") if os.getenv("RAILWAY_ENVIRONMENT") else Path(".")
STATE_FILE = DATA_DIR / "state.json"

SELF = "Pufu"
SELF_ADDR = "0x872ec2644addbbf526744d8e3cb6b0356c0b73d7"

TRADERS = {
    "Euan": {"addr": "0xdd225a03cd7ed89e3931906c67c75ab31cf89ef1", "tier": 1},
    "Car": {"addr": "0x7c3db723f1d4d8cb9c550095203b686cb11e5c6b", "tier": 1},
    "JJo": {"addr": "0xc4086b708cd3a50880b7069add1a1a80000f4675", "tier": 2},
    "Scottilicious": {"addr": "0x000d257d2dc7616feaef4ae0f14600fdf50a758e", "tier": 1},
    "aenews": {"addr": "0x44c1dfe43260c94ed4f1d00de2e1f80fb113ebc1", "tier": 2},
    "Dropper": {"addr": "0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292", "tier": 2}
}

# ==========================================
# 2. STATE MANAGEMENT
# ==========================================
global_state = {
    "initialized": False,
    "bot_start_time": time.time(),
    "last": {},           
    "positions": {},      
    "trader_entries": {}, 
    "my_portfolio": [],   
    "trade_log": [],      
    "scores": {},         
    "market_prices": {},  
    "last_buy_times": {}, 
    "cluster_participants": {}, 
    "cluster_created_at": {}, 
    "clusters_sent": {},
    "last_summary_day": "",
    "nightly_data": {}, 
    "shadow": {
        "cash": SHADOW_START_CAPITAL,
        "positions": [],
        "history": []
    },
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
        "positions": {},
        "trader_entries": {},
        "nightly_data": {},
        "shadow": { "cash": SHADOW_START_CAPITAL, "positions": [], "history": [] }
    }
    for k, v in defaults.items():
        if k not in global_state: global_state[k] = v
    if "shadow" not in global_state: global_state["shadow"] = defaults["shadow"]

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
        except: sanitize_state()

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
    <title>PolyBot Fixed</title>
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
        .rec-add  { border-color: #6c5ce7; }
        .rec-ok   { border-color: #00d2ff; }
        .rec-text { font-size: 1.1em; font-weight: 500; }
        .rec-sub  { font-size: 0.9em; color: #aaa; }
        
        .shadow-stats { display: flex; gap: 20px; margin-bottom: 15px; }
        .stat-box { background: rgba(0,0,0,0.3); padding: 10px 20px; border-radius: 5px; }
        .profit-plus { color: #00b894; }
        .profit-minus { color: #d63031; }
    </style>
</head>
<body>
    <div style="display:flex; justify-content:space-between; align-items:center;">
        <h1>🤖 PolyBot Dashboard</h1>
        <div>Update: <b>{{ state.last_update }}</b> | <span style="color:#00b894">● Online</span></div>
    </div>

    <div class="card" style="border: 1px solid #00d2ff;">
        <h3 style="margin-top:0;">🧠 Analiză Strategică (AI Coach)</h3>
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
                <div class="rec-sub">Nu sunt necesare acțiuni urgente. Scanez pentru intrări...</div>
            </div>
        {% endif %}
    </div>

    <div class="card">
        <h3>💼 Portofoliul Tău REAL ({{ self_name }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Acțiuni</th><th>Valoare ($)</th><th>Preț Curent</th></tr></thead>
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
                <tr><td colspan="5" style="text-align:center; color:#666;">Se încarcă datele...</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card" style="border: 1px solid #6c5ce7;">
        <h3 style="margin-top:0; color:#a29bfe;">🧪 Shadow Trading (Smart Money Management)</h3>
        <div class="shadow-stats">
            <div class="stat-box">Cash: <b>${{ shadow_cash }}</b></div>
            <div class="stat-box">Valoare Poziții: <b>${{ shadow_holdings }}</b></div>
            <div class="stat-box">Total Equity: <b>${{ shadow_total }}</b></div>
            <div class="stat-box">Profit: <b class="{{ 'profit-plus' if shadow_pl >= 0 else 'profit-minus' }}">{{ shadow_pl_fmt }}</b></div>
        </div>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Bet Size</th><th>Intrare</th><th>Valoare Acum</th><th>ROI</th></tr></thead>
            <tbody>
                {% for s in shadow_display %}
                <tr>
                    <td>{{ s.title }}</td>
                    <td><span class="{{ 'yes' if 'YES' in s.side else 'no' }}">{{ s.side }}</span></td>
                    <td>${{ s.entry_val }}</td> 
                    <td>{{ s.entry_price }}¢</td>
                    <td><b>${{ s.curr_val }}</b></td>
                    <td class="{{ 'profit-plus' if s.roi >= 0 else 'profit-minus' }}">{{ s.roi }}%</td>
                </tr>
                {% else %}
                <tr><td colspan="6" style="text-align:center; color:#666;">Aștept semnal "High Conviction" (Scor > 8.0)...</td></tr>
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
                <tr><td colspan="6" style="text-align:center; color:#666;">Niciun cluster <b>NOU</b>.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>💰 Top Investiții Comune (All Time)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Participanți (Min 2)</th><th>Total ($)</th><th>Preț</th></tr></thead>
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
                <tr><td colspan="5" style="text-align:center; color:#666;">Scanning...</td></tr>
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
                if val >= 10: 
                    name = pos_k.split("|")[0]
                    participants.append((name, val))
        return total, list(set([p[0] for p in participants])), participants

    recs = []
    my_weakest_score = 10.0
    my_weakest_market = None
    my_owned_markets = []

    for pos in global_state["my_portfolio"]:
        m_key = f"{pos['title']}|{pos['outcome']}"
        my_owned_markets.append(m_key)
        try: p_val = float(pos['price'].replace('¢', '').split()[0]) / 100.0
        except: p_val = 0.0
        
        current_score = global_state["scores"].get(m_key, 5.0)

        if p_val >= 0.98:
            recs.append({"class": "rec-sell", "text": f"💰 <b>TAKE ALL PROFIT: {pos['title']}</b>", "reason": f"Preț {p_val*100:.0f}¢. E aproape de 100%."})
        elif p_val >= 0.94:
            recs.append({"class": "rec-sell", "text": f"📉 <b>REDU POZIȚIA: {pos['title']}</b>", "reason": f"Profit bun ({p_val*100:.0f}¢), securizează câștigul."})
        elif current_score >= 8.5 and p_val < 0.85:
            recs.append({"class": "rec-add", "text": f"➕ <b>ADAUGĂ: {pos['title']}</b>", "reason": f"Scor Excelent {current_score:.1f} și preț bun."})
        elif current_score < 4.0:
            recs.append({"class": "rec-sell", "text": f"⚠️ <b>IEȘI DIN: {pos['title']}</b>", "reason": f"Scor AI slab {current_score:.1f}. Trend rupt."})
            if current_score < my_weakest_score:
                my_weakest_score = current_score
                my_weakest_market = pos['title']

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
        
        score = calc_smart_score(vol, users, p_live, False)
        global_state["scores"][key] = score
        
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
            if created_at > global_state["bot_start_time"]: is_new = True
            if vol >= MINI and is_new: smart_clusters.append(cluster_obj)
            
            if key not in my_owned_markets and score >= 8.0 and vol >= MINI:
                potential_buys.append((key, score))
                if score > best_opp_score:
                    best_opp_score = score
                    best_opp_key = key

    smart_clusters.sort(key=lambda x: x["vol"], reverse=True)
    all_shared.sort(key=lambda x: x["vol"], reverse=True)

    potential_buys.sort(key=lambda x: x[1], reverse=True)
    for opp_key, opp_score in potential_buys[:3]:
        recs.append({"class": "rec-buy", "text": f"🚀 <b>OPORTUNITATE: {opp_key.split('|')[0]}</b>", "reason": f"Scor {opp_score:.1f}. Cluster puternic."})
    
    if my_weakest_market and best_opp_key:
        if my_weakest_score < 5.0 and best_opp_score > 7.5:
            recs.append({"class": "rec-swap", "text": f"🔄 <b>SWAP: {my_weakest_market} ➔ {best_opp_key.split('|')[0]}</b>", "reason": "Upgrade Scor."})

    shadow_display = []
    shadow_holdings_val = 0
    for p in global_state["shadow"]["positions"]:
        curr_price = global_state["market_prices"].get(p["key"], p["entry_price"])
        curr_val = p["shares"] * curr_price
        shadow_holdings_val += curr_val
        roi = ((curr_val - p["cost"]) / p["cost"]) * 100
        title = p["key"].split("|")[0]
        side = p["key"].split("|")[1]
        shadow_display.append({
            "title": title, "side": side,
            "entry_val": f"{p['cost']:.0f}", "entry_price": f"{p['entry_price']*100:.1f}",
            "curr_val": f"{curr_val:.0f}", "curr_price": f"{curr_price*100:.1f}",
            "roi": round(roi, 1)
        })
    shadow_cash = global_state["shadow"]["cash"]
    shadow_total = shadow_cash + shadow_holdings_val
    shadow_pl = shadow_total - SHADOW_START_CAPITAL
    shadow_pl_fmt = f"${shadow_pl:.0f}" if shadow_pl >= 0 else f"-${abs(shadow_pl):.0f}"

    return render_template_string(
        HTML_TEMPLATE, 
        state=global_state, self_name=SELF, recommendations=recs, 
        min_dash=MIN_DASHBOARD_LOG, mini=MINI, 
        smart_clusters=smart_clusters[:MAX_DASHBOARD_CLUSTERS],
        all_shared=all_shared[:20],
        shadow_display=shadow_display, shadow_cash=f"{shadow_cash:.0f}",
        shadow_holdings=f"{shadow_holdings_val:.0f}", shadow_total=f"{shadow_total:.0f}",
        shadow_pl=shadow_pl, shadow_pl_fmt=shadow_pl_fmt
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
    except: pass

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

def calc_smart_score(cluster_val, participants_list, price, is_ping_pong):
    score = 5.0 
    has_tier1 = False
    for user in participants_list:
        tier = TRADERS.get(user, {}).get("tier", 3)
        if tier == 1: 
            score += 1.5 
            has_tier1 = True
        elif tier == 2:
            score += 0.5 
    
    if cluster_val >= BIG: score += 2.0
    elif cluster_val >= NORMAL: score += 1.0
    
    if len(participants_list) == 1 and not has_tier1:
        score -= 2.0 
    
    if price > 0:
        if price < 0.50: score += 1.0     
        elif price < 0.80: score += 0.5
        elif price > 0.92: score -= 2.5 
        
    if is_ping_pong: score -= 5.0
    
    return max(0.0, min(10.0, score))

def calculate_bet_size(score, price):
    bet = BASE_BET_SIZE
    if score >= 9.0: bet *= 2.0
    elif score >= 8.0: bet *= 1.5
    elif score < 6.0: bet *= 0.5
    if price > 0.90: bet *= 0.5
    return int(bet)

def update_shadow_portfolio():
    shadow = global_state["shadow"]
    active_keys = []
    
    for p in shadow["positions"]:
        market_key = p["key"]
        curr_price = global_state["market_prices"].get(market_key, p["entry_price"])
        score = global_state["scores"].get(market_key, 5.0)
        
        if curr_price >= 0.95 or score < 4.0:
            sale_val = p["shares"] * curr_price
            profit = sale_val - p["cost"]
            shadow["cash"] += sale_val
            print(f"🤖 SHADOW SOLD: {market_key} (+${profit:.0f})")
        else:
            active_keys.append(p)
    shadow["positions"] = active_keys 

    for key, score in global_state["scores"].items():
        owned = any(p["key"] == key for p in shadow["positions"])
        if not owned and score >= 8.0:
            price = global_state["market_prices"].get(key, 0)
            if 0.05 < price < 0.88:
                bet_size = calculate_bet_size(score, price)
                if shadow["cash"] >= bet_size:
                    shares = bet_size / price
                    shadow["positions"].append({
                        "key": key, "shares": shares, "cost": bet_size, "entry_price": price
                    })
                    shadow["cash"] -= bet_size
                    print(f"🤖 SHADOW BOUGHT: {key} (${bet_size})")

def sync_trader_positions():
    print("♻️ Sincronizare Traderi...")
    for name, data in TRADERS.items():
        addr = data["addr"]
        try:
            r = requests.get(API_POSITIONS, params={"user": addr}, timeout=5)
            if r.status_code == 200:
                resp = r.json()
                for item in resp:
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
                    if p > 0: global_state["market_prices"][f"{title}|{outcome}"] = p
                    
                    # Store ENTRY PRICE
                    entry = safe_float(item.get("avgBuyPrice"))
                    if entry > 0: global_state["trader_entries"][pos_key] = entry
        except: pass

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
                d_price = f"{p*100:.1f}¢" if p > 0 else "N/A"
                real_portfolio.append({
                    "title": title, "outcome": outcome,
                    "size": f"{size:.0f}", "value": f"{value:.0f}",
                    "price": f"{p*100:.1f}", "display_price": d_price
                })
            global_state["my_portfolio"] = real_portfolio
    except: pass

def fetch(addr):
    try:
        r = requests.get(API_ACTIVITY, params={"user": addr, "limit": 20}, timeout=10)
        return r.json() if r.status_code == 200 else []
    except: return []

def get_usd(e):
    try: return float(e.get("size", 0)) * float(e.get("price", 0))
    except: return 0

# --- NIGHTLY REPORT ---
def check_nightly_summary():
    now = datetime.now(RO)
    today_str = now.strftime("%Y-%m-%d")
    
    if now.hour == 7 and global_state["last_summary_day"] != today_str:
        # A. ANALIZA PORTOFOLIU
        portfolio_msg = "💼 <b>ANALIZĂ PORTOFOLIU:</b>\n"
        total_val = 0
        actions_needed = False
        sync_portfolio() 
        for pos in global_state["my_portfolio"]:
            title = pos['title']
            side = pos['outcome']
            val = float(pos['value'])
            total_val += val
            try: price = float(pos['price'].replace('¢', '').split()[0]) / 100.0
            except: price = 0.0
            m_key = f"{title}|{side}"
            score = global_state["scores"].get(m_key, 5.0)
            
            advice = "✅ HOLD"
            if price >= 0.95: 
                advice = "💰 <b>TAKE PROFIT</b> (Preț mare)"
                actions_needed = True
            elif score < 4.0: 
                advice = "⚠️ <b>CUT LOSS</b> (Scor mic)"
                actions_needed = True
            
            portfolio_msg += f"• {title} ({side})\n   👉 {advice} | Scor: {score:.1f} | ${val:.0f}\n"

        if not actions_needed: portfolio_msg += "<i>(Nicio urgență. Portofoliu stabil.)</i>\n"
        portfolio_msg += f"\n💰 <b>Total Equity: ${total_val:.0f}</b>\n\n"

        # B. RAPORT CLUSTERE NOAPTE (22:00 - 07:00)
        clusters_msg = "🏆 <b>RAPORT NOAPTE (VOLUME):</b>\n"
        nightly_list = []
        for key, vol in global_state["nightly_data"].items():
            if vol > MINI:
                nightly_list.append((key, vol))
        nightly_list.sort(key=lambda x: x[1], reverse=True)
        
        if not nightly_list:
            clusters_msg += "<i>(Liniște totală azi-noapte...)</i>"
        else:
            for key, vol in nightly_list[:5]:
                title = key.split('|')[0]
                side = key.split('|')[1]
                clusters_msg += f"🌙 <b>{title}</b> ({side})\n   💰 Volum Noapte: ${vol:.0f}\n\n"

        tg(f"☕ <b>BUNĂ DIMINEAȚA!</b> ☀️\n\n{portfolio_msg}{clusters_msg}")
        
        global_state["nightly_data"] = {}
        global_state["last_summary_day"] = today_str
        save()

# ==========================================
# 5. LOOP PRINCIPAL
# ==========================================

def bot_loop():
    load()
    print("Bot loop started.")
    tg("✅ <b>SYSTEM RESTARTED</b>\nFix: Variable Crash Fixed\nFull Mode Active") 
    
    sync_trader_positions()
    sync_portfolio()
    
    loop_count = 0

    while True:
        try:
            loop_count += 1
            if loop_count % 5 == 0: 
                sync_portfolio()
                update_shadow_portfolio()
            if loop_count % 10 == 0: sync_trader_positions()

            check_nightly_summary()
            
            for name, data in {**TRADERS, SELF: {"addr": SELF_ADDR}}.items():
                addr = data["addr"]
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

                    title = e.get("title", "")
                    if not title or title.strip() == "": continue 

                    outcome = e.get("outcome", "YES").upper()
                    
                    event_side = e.get("side", "BUY").upper()
                    action = "sell" if event_side == "SELL" else "buy"
                    
                    val = get_usd(e)
                    price = float(e.get("price", 0))
                    
                    pos_key = f"{name}|{title}|{outcome}"
                    market_key = f"{title}|{outcome}"
                    
                    if price > 0: global_state["market_prices"][market_key] = price

                    now_h = datetime.now(RO).hour
                    if action == "buy" and (now_h >= 22 or now_h < 7):
                        global_state["nightly_data"][market_key] = global_state["nightly_data"].get(market_key, 0) + val

                    cluster_participants = set()
                    cluster_sum = 0
                    c_breakdown = []

                    for k, v in global_state["positions"].items():
                        if market_key in k and not k.startswith(SELF): 
                            cluster_sum += v
                            cluster_participants.add(k.split("|")[0])
                            # FIX VARIABLE NAME
                            if v >= MIN_TRADER_DISPLAY:
                                c_breakdown.append(f"• {k.split('|')[0]}: ${v:,.0f}")
                    
                    participants_count = len(cluster_participants)
                    
                    is_ping_pong = False
                    if action == "sell":
                        last_buy = global_state["last_buy_times"].get(pos_key, 0)
                        if ts - last_buy < 3600 and last_buy != 0: 
                            is_ping_pong = True
                    if action == "buy": global_state["last_buy_times"][pos_key] = ts

                    c_list_users = list(cluster_participants)
                    current_score = calc_smart_score(cluster_sum, c_list_users, price, is_ping_pong)
                    global_state["scores"][market_key] = current_score

                    action_ro = "A CUMPĂRAT"
                    if action == "sell": action_ro = "A VÂNDUT"
                    side_emoji = "🟢" if "YES" in outcome else "🔴"
                    side_formatted = f"{side_emoji} <b>{outcome}</b>"
                    
                    if name == SELF:
                        if action == "buy":
                            feedback = "✅ <b>Decizie Bună!</b>"
                            if current_score < 4.5: feedback = "⚠️ <b>Riscant (Scor Mic)</b>"
                            if current_score > 7.5: feedback = "🏆 <b>Intrare Excelentă!</b>"
                            tg(f"🔔 <b>AI CUMPĂRAT {side_formatted}</b>\n🏆 {title}\n💲{val:.0f} | Scor: <b>{current_score:.1f}</b>\n{feedback}")
                        elif action == "sell":
                            tg(f"🔔 <b>AI VÂNDUT {side_formatted}</b>\n🏆 {title}\nRecuperat: ${val:.0f}")
                        threading.Thread(target=sync_portfolio).start()

                    else:
                        if action == "buy":
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            if val >= MIN_BUY_ALERT:
                                whale_tag = " 🐋 <b>WHALE BUY!</b>" if val >= WHALE_ALERT else ""
                                tg(f"👤 <b>{name} {action_ro} {side_formatted}</b>{whale_tag}\n🏆 {title}\n💲 {val:.0f} @ {price*100:.1f}¢\n🎯 Scor: <b>{current_score:.1f}/10</b>")

                        elif action == "sell":
                            held_val = global_state["positions"].get(pos_key, 0)
                            entry_price = global_state["trader_entries"].get(pos_key, 0)
                            global_state["positions"][pos_key] = max(held_val - val, 0)
                            
                            if val >= MIN_SELL_ALERT:
                                total_before = held_val if held_val > val else val 
                                pct_sold = (val / total_before) * 100 if total_before > 0 else 100
                                if pct_sold > 100: pct_sold = 100
                                reason = "(Profit)" if price > entry_price else "(Loss)"
                                pp_warn = ""
                                if is_ping_pong: pp_warn = "⚠️ <b>PING-PONG</b>"
                                elif val >= WHALE_ALERT: pp_warn = "🐋 <b>WHALE DUMP!</b>"
                                
                                exit_str = f"📉 Vândut: <b>{pct_sold:.0f}%</b>"
                                if entry_price > 0:
                                    exit_str += f"\n🚪 Intrare: {entry_price*100:.1f}¢ ➔ Ieșire: {price*100:.1f}¢"
                                
                                tg(f"{pp_warn}\n📉 <b>{name} {action_ro} {side_formatted}</b>\n🏆 {title}\nSuma: ${val:.0f} {reason}\n{exit_str}")

                    if val >= MIN_DASHBOARD_LOG:
                        note = f"Scor: {current_score:.1f}"
                        if is_ping_pong: note += " | ⚠️ PingPong"
                        if val >= WHALE_ALERT: note += " | 🐋 Whale"
                        global_state["trade_log"].append({
                            "time": datetime.now(RO).strftime("%H:%M"),
                            "trader": name, "action": action,
                            "market": f"{title} ({outcome})", "value": int(val), 
                            "price_fmt": f"{price*100:.1f}¢", "note": note
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
                    c_breakdown = []
                    
                    for sub_k, sub_v in global_state["positions"].items():
                        if c_key in sub_k and not sub_k.startswith(SELF):
                            c_total += sub_v
                            # FIX VARIABLE NAME
                            if sub_v >= MIN_TRADER_DISPLAY:
                                c_breakdown.append(f"• {sub_k.split('|')[0]}: ${sub_v:,.0f}")
                    
                    if len(c_breakdown) >= 2 and c_total >= MINI:
                        if c_key not in global_state["cluster_created_at"]:
                            if loop_count == 1:
                                global_state["cluster_created_at"][c_key] = 0 
                                global_state["clusters_sent"][c_key] = c_total
                            else:
                                global_state["cluster_created_at"][c_key] = time.time()

                        last_sent = global_state["clusters_sent"].get(c_key, 0)
                        if loop_count > 1 and c_total > last_sent * 1.2:
                            c_list_users = list(c_breakdown)
                            sc = calc_smart_score(c_total, c_list_users, global_state["market_prices"].get(c_key, 0.5), False)
                            
                            c_side = c_key.split("|")[1]
                            c_emoji = "🟢" if "YES" in c_side else "🔴"
                            level = "MINI"
                            if c_total >= BIG: level = "MARE"
                            elif c_total >= NORMAL: level = "MEDIU"
                            
                            breakdown_str = "\n".join(c_breakdown)
                            tg(f"📊 <b>CLUSTER {level} {c_emoji} {c_side}</b>\n🏆 {c_key.split('|')[0]}\n💰 Total: ${c_total:,.0f}\n🎯 Scor: {sc:.1f}\n\n👥 <b>Participanți (>1k):</b>\n{breakdown_str}")
                            
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
