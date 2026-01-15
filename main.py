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
# 1. CONFIGURARE "ZERO ERRORS"
# ==========================================

BOT_TOKEN = "8261089656:AAF_JM39II4DpfiFzVTd0zsXZKtKcDE5G9A" 
CHAT_ID = "6854863928"

PORT = int(os.getenv("PORT", 5000))

API_ACTIVITY = "https://data-api.polymarket.com/activity"
API_POSITIONS = "https://data-api.polymarket.com/positions"
API_CLOB = "https://clob.polymarket.com/price" 

POLL = 60 

# Limite & Setari
MIN_BUY_ALERT = 1500    
MIN_SELL_ALERT = 1500  
MICRO_SELL_THRESHOLD_PCT = 0.80 
WHALE_ALERT = 5000      
MIN_DASHBOARD_LOG = 500 

# Clustere
MINI = 6000      
NORMAL = 10000
BIG = 20000
MAX_DASHBOARD_CLUSTERS = 20 
MIN_TRADER_DISPLAY = 1000 

# !!! FIX EROARE 1: DEFINIRE VARIABILA !!!
ACCUMULATION_LIMIT_3DAYS = 15000

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
    "processed_ids": [],    
    "cluster_participants": {}, 
    "cluster_created_at": {}, 
    "clusters_sent": {},
    "last_summary_day": "",
    "nightly_sales": [],    
    "session_accumulated": {}, 
    "buy_history": [],         
    "last_accum_alert": {},
    "micro_tracker": {}, # !!! FIX EROARE 2: INITIALIZARE !!!
    "last_update": "Never"
}

price_cache = {} 

def sanitize_state():
    # Asiguram ca toate cheile noi exista, chiar daca incarcam un fisier vechi
    defaults = {
        "clusters_sent": {},
        "cluster_created_at": {},
        "cluster_participants": {},
        "scores": {},
        "market_prices": {},
        "positions": {},
        "trader_entries": {},
        "nightly_sales": [],
        "processed_ids": [],
        "session_accumulated": {},
        "buy_history": [],
        "last_accum_alert": {},
        "micro_tracker": {}, # CRITIC
        "trade_log": []
    }
    for k, v in defaults.items():
        if k not in global_state: global_state[k] = v
    
    if "shadow" in global_state: del global_state["shadow"]

def load():
    global global_state
    if not DATA_DIR.exists(): DATA_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            current_start = global_state["bot_start_time"]
            global_state.update(saved)
            global_state["bot_start_time"] = current_start 
            global_state["session_accumulated"] = {}       
            sanitize_state() # Aici se repara cheile lipsa
        except: sanitize_state()

def save():
    if len(global_state["processed_ids"]) > 5000:
        global_state["processed_ids"] = global_state["processed_ids"][-5000:]
    if len(global_state["trade_log"]) > 200:
        global_state["trade_log"] = global_state["trade_log"][-200:]
    
    now_ts = time.time()
    global_state["buy_history"] = [
        b for b in global_state["buy_history"] 
        if now_ts - b["ts"] < 345600 
    ]
    
    STATE_FILE.write_text(json.dumps(global_state, indent=2))

# ==========================================
# 3. DASHBOARD WEB
# ==========================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>PolyBot Zero Errors</title>
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
                <div class="rec-sub">Nu sunt necesare acțiuni urgente.</div>
            </div>
        {% endif %}
    </div>

    <div class="card">
        <h3>💼 Portofoliul Tău REAL ({{ self_name }})</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Acțiuni</th><th>Valoare ($)</th><th>Preț Intrare</th><th>Preț Curent</th></tr></thead>
            <tbody>
                {% for pos in state.my_portfolio %}
                <tr class="self-row">
                    <td>{{ pos.title }}</td>
                    <td><span class="{{ 'yes' if 'YES' in pos.outcome else 'no' }}">{{ pos.outcome }}</span></td>
                    <td>{{ pos.size }}</td>
                    <td><b>${{ pos.value }}</b></td>
                    <td>{{ pos.entry_fmt }}</td>
                    <td>{{ pos.display_price }}</td>
                </tr>
                {% else %}
                <tr><td colspan="6" style="text-align:center; color:#666;">Se încarcă datele...</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card" style="border: 1px solid #fdcb6e;">
        <h3>⚡ Active Session Clusters (De la ultimul Restart)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Traderi (Sesiune)</th><th>Total Sesiune ($)</th><th>Preț</th></tr></thead>
            <tbody>
                {% for c in session_clusters %}
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
                <tr><td colspan="5" style="text-align:center; color:#666;">Niciun cluster format în această sesiune.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h3>💰 All-Time Clusters (Total Dețineri)</h3>
        <table>
            <thead><tr><th>Piață</th><th>Side</th><th>Participanți (Min 2)</th><th>Total All-Time ($)</th><th>Preț</th></tr></thead>
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
    def get_cluster_data_all_time(key):
        total = 0
        user_totals = {}
        for pos_k, val in global_state["positions"].items():
            if f"{key}" in pos_k and not pos_k.startswith(SELF):
                total += val
                name = pos_k.split("|")[0]
                user_totals[name] = user_totals.get(name, 0) + val
        valid = []
        for n, v in user_totals.items():
            if v >= MIN_TRADER_DISPLAY: valid.append((n, v))
        return total, valid

    def get_cluster_data_session(key):
        total = 0
        user_totals = {}
        for pos_k, val in global_state["session_accumulated"].items():
            if f"{key}" in pos_k and not pos_k.startswith(SELF):
                total += val
                name = pos_k.split("|")[0]
                user_totals[name] = user_totals.get(name, 0) + val
        valid = []
        for n, v in user_totals.items():
            if v >= MIN_TRADER_DISPLAY: valid.append((n, v))
        return total, valid

    recs = []
    for pos in global_state["my_portfolio"]:
        try: p_val = float(pos['price'].replace('¢', '').split()[0]) / 100.0
        except: p_val = 0.0
        try: entry_val = float(pos['entry_fmt'].replace('¢', '').split()[0]) / 100.0
        except: entry_val = 0.0
        
        if p_val >= 0.98:
            recs.append({"class": "rec-sell", "text": f"💰 <b>TAKE ALL PROFIT: {pos['title']}</b>", "reason": f"Preț {p_val*100:.0f}¢."})
        elif p_val >= 0.94:
            if entry_val > 0:
                if p_val > entry_val:
                    recs.append({"class": "rec-sell", "text": f"📉 <b>REDU POZIȚIA: {pos['title']}</b>", "reason": f"Profit marcat."})
                else:
                    recs.append({"class": "rec-ok", "text": f"✊ <b>HOLD: {pos['title']}</b>", "reason": f"Intrare {entry_val*100:.0f}¢."})
            else:
                recs.append({"class": "rec-sell", "text": f"📉 <b>REDU POZIȚIA: {pos['title']}</b>", "reason": "Preț mare 94¢."})

    session_clusters = []
    all_shared = []

    unique_session = set()
    for pos_k in global_state["session_accumulated"]:
        parts = pos_k.split("|")
        if len(parts) == 3: unique_session.add(f"{parts[1]}|{parts[2]}")
    
    for key in unique_session:
        vol, parts = get_cluster_data_session(key)
        if len(parts) >= 2: 
            p_live = global_state['market_prices'].get(key, 0.5)
            sorted_sums = sorted(parts, key=lambda x: x[1], reverse=True)
            breakdown = ", ".join([f"{n}: ${v:,.0f}" for n, v in sorted_sums])
            session_clusters.append({
                "key": key, "vol": vol, "vol_fmt": f"{vol:,.0f}",
                "count": len(parts), "breakdown": breakdown, "price": f"{p_live*100:.1f}"
            })

    unique_all = set()
    for pos_k in global_state["positions"]:
        parts = pos_k.split("|")
        if len(parts) == 3: unique_all.add(f"{parts[1]}|{parts[2]}")
        
    for key in unique_all:
        vol, parts = get_cluster_data_all_time(key)
        if len(parts) >= 2:
            p_live = global_state['market_prices'].get(key, 0.5)
            sorted_sums = sorted(parts, key=lambda x: x[1], reverse=True)
            breakdown = ", ".join([f"{n}: ${v:,.0f}" for n, v in sorted_sums])
            all_shared.append({
                "key": key, "vol": vol, "vol_fmt": f"{vol:,.0f}",
                "count": len(parts), "breakdown": breakdown, "price": f"{p_live*100:.1f}"
            })

    session_clusters.sort(key=lambda x: x["vol"], reverse=True)
    all_shared.sort(key=lambda x: x["vol"], reverse=True)

    return render_template_string(
        HTML_TEMPLATE, 
        state=global_state, self_name=SELF, recommendations=recs, 
        min_dash=MIN_DASHBOARD_LOG, mini=MINI, 
        session_clusters=session_clusters,
        all_shared=all_shared
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

def calc_smart_score(participants_list, price, is_ping_pong, cluster_active):
    score = 5.0 
    score += len(participants_list) * 1.0 
    if cluster_active: score += 2.0
    if price > 0:
        if price < 0.50: score += 1.0     
        elif price < 0.80: score += 0.5
        elif price > 0.92: score -= 2.5 
    if is_ping_pong: score -= 5.0
    return max(0.0, min(10.0, score))

def sync_trader_positions():
    print("♻️ Sincronizare Traderi...")
    full_list = {**TRADERS, SELF: {"addr": SELF_ADDR}}
    for name, data in full_list.items():
        addr = data["addr"] if isinstance(data, dict) else data
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
                    
                    # FORCE MEMORARE INTRARE
                    entry = safe_float(item.get("avgBuyPrice"))
                    if entry > 0: 
                        global_state["trader_entries"][pos_key] = entry
        except: pass
        time.sleep(1)

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
                
                pos_key = f"{SELF}|{title}|{outcome}"
                entry = global_state["trader_entries"].get(pos_key, 0)
                
                value = size * p
                d_price = f"{p*100:.1f}¢" if p > 0 else "N/A"
                e_price = f"{entry*100:.1f}¢" if entry > 0 else "N/A"
                
                real_portfolio.append({
                    "title": title, "outcome": outcome,
                    "size": f"{size:.0f}", "value": f"{value:.0f}",
                    "price": f"{p*100:.1f}", "display_price": d_price,
                    "entry_fmt": e_price
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

def get_ai_reinvestment_strategy(cash_in_hand, sold_key):
    candidates = []
    for key, score in global_state["scores"].items():
        if key == sold_key: continue
        price = global_state["market_prices"].get(key, 0.5)
        if price > 0.90 or price < 0.05: continue 
        alpha = score
        if score > 8.5: alpha += 1.5 
        upside = (1.00 - price) / price 
        if upside > 1.5: alpha += 1.0
        elif upside < 0.2: alpha -= 2.0
        owned = any(key in p['title'] for p in global_state["my_portfolio"])
        if owned: alpha -= 3.0 
        candidates.append({"key": key, "alpha": alpha, "price": price})
    
    if not candidates: return "\n💡 <b>SFAT:</b> 🏖️ <b>KEEP CASH</b>."
    best = max(candidates, key=lambda x: x['alpha'])
    if best['alpha'] < 7.0: return "\n💡 <b>SFAT:</b> 🏖️ <b>KEEP CASH</b>."
    
    allocation_pct = 0.30 
    strategy_name = "STANDARD"
    if best['alpha'] >= 9.5 and best['price'] < 0.60:
        allocation_pct = 0.75; strategy_name = "🚀 <b>ALL-IN AGGRESSIVE</b>"
    elif best['alpha'] >= 8.0:
        allocation_pct = 0.40; strategy_name = "⚔️ <b>TACTICAL ENTRY</b>"
    elif best['alpha'] >= 6.0 and best['price'] < 0.30:
        allocation_pct = 0.15; strategy_name = "🌙 <b>MOONSHOT BET</b>"
        
    invest_amt = cash_in_hand * allocation_pct
    if invest_amt < 10: invest_amt = 10
    title = best['key'].split('|')[0]
    side = best['key'].split('|')[1]
    return (f"\n💡 <b>SFAT REINVESTIRE:</b>\nStrategie: {strategy_name}\n👉 Bagă <b>${invest_amt:.0f}</b> în: {title} ({side})\n📊 Alpha Score: <b>{best['alpha']:.1f}</b>")

# --- NIGHTLY REPORT ---
def check_nightly_summary():
    now = datetime.now(RO)
    today_str = now.strftime("%Y-%m-%d")
    if now.hour == 7 and global_state["last_summary_day"] != today_str:
        portfolio_msg = "💼 <b>ANALIZĂ PORTOFOLIU (07:00):</b>\n"
        sync_portfolio() 
        for pos in global_state["my_portfolio"]:
            try: p_val = float(pos['price'].replace('¢', '').split()[0]) / 100.0
            except: p_val = 0.0
            try: entry_val = float(pos['entry_fmt'].replace('¢', '').split()[0]) / 100.0
            except: entry_val = 0.0
            sfat = "HOLD"
            if p_val > 0.95: sfat = "TAKE PROFIT (High)"
            elif entry_val > 0 and p_val < entry_val * 0.8: sfat = "CUT LOSS (-20%)"
            elif entry_val > 0 and p_val > entry_val * 1.1: sfat = "PROFIT (Secure?)"
            portfolio_msg += f"• {pos['title']}\n   👉 {sfat} ({pos['price']})\n"

        clusters_msg = "\n🔥 <b>CLUSTERE SUPRAVIEȚUITOARE (Active la 7AM):</b>\n"
        active_clusters = []
        unique_keys = set()
        for pos_k in global_state["positions"]:
            parts = pos_k.split("|")
            if len(parts) == 3 and parts[0] != SELF:
                unique_keys.add(f"{parts[1]}|{parts[2]}")
        for key in unique_keys:
            c_sum = 0
            for pos_k, val in global_state["positions"].items():
                if key in pos_k and not pos_k.startswith(SELF): c_sum += val
            if c_sum >= MINI:
                active_clusters.append((key, c_sum))
        if not active_clusters: clusters_msg += "<i>(Toate clusterele s-au dizolvat.)</i>\n"
        else:
            for k, v in active_clusters:
                clusters_msg += f"• {k.split('|')[0]} ({k.split('|')[1]}): ${v:,.0f}\n"

        sales_msg = "\n🐋 <b>VÂNZĂRI MASIVE NOAPTEA (> $5k):</b>\n"
        if not global_state["nightly_sales"]:
            sales_msg += "<i>(Nimic notabil.)</i>"
        else:
            for item in global_state["nightly_sales"]:
                sales_msg += f"• {item['trader']} a vândut {item['market']} (${item['amount']:,.0f})\n"

        tg(f"☕ <b>RAPORT DIMINEAȚA</b> ☀️\n\n{portfolio_msg}{clusters_msg}{sales_msg}")
        global_state["nightly_sales"] = []
        global_state["last_summary_day"] = today_str
        save()

# ==========================================
# 5. LOOP PRINCIPAL
# ==========================================

def bot_loop():
    load()
    print("Bot loop started.")
    tg("✅ <b>SYSTEM RESTARTED</b>\nFix: No Errors\nLimits: 1500 Alert | 500 Log") 
    
    sync_trader_positions()
    sync_portfolio()
    
    loop_count = 0

    while True:
        try:
            loop_count += 1
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

                    # ANTI-SPAM
                    unique_id = e.get("id") or f"{e.get('transactionHash')}_{e.get('logIndex')}"
                    if unique_id in global_state["processed_ids"]: continue
                    global_state["processed_ids"].append(unique_id)

                    title = e.get("title", "")
                    if not title or title.strip() == "": continue 
                    if e.get("type") == "MERGE": continue 

                    outcome = e.get("outcome", "YES").upper()
                    event_side = e.get("side", "BUY").upper()
                    action = "sell" if event_side == "SELL" else "buy"
                    
                    val = get_usd(e)
                    price = float(e.get("price", 0))
                    
                    pos_key = f"{name}|{title}|{outcome}"
                    market_key = f"{title}|{outcome}"
                    
                    if price > 0: global_state["market_prices"][market_key] = price

                    now_h = datetime.now(RO).hour
                    is_night = (now_h >= 22 or now_h < 7)

                    if action == "buy":
                        global_state["session_accumulated"][pos_key] = global_state["session_accumulated"].get(pos_key, 0) + val
                    
                    if action == "buy":
                        global_state["buy_history"].append({
                            "user": name, "market": market_key, "amount": val, "ts": time.time()
                        })
                        cutoff = time.time() - (3 * 24 * 3600)
                        recent_buys = [
                            b["amount"] for b in global_state["buy_history"]
                            if b["user"] == name and b["market"] == market_key and b["ts"] > cutoff
                        ]
                        total_3d = sum(recent_buys)
                        alert_key = f"{name}|{market_key}|3d"
                        last_alert_time = global_state["last_accum_alert"].get(alert_key, 0)
                        
                        if total_3d > ACCUMULATION_LIMIT_3DAYS and (time.time() - last_alert_time > 3600):
                            tg(f"🐳 <b>MASSIVE ACCUMULATION (3 Days)</b>\n👤 {name}\n🏆 {title}\n💰 A cumpărat: <b>${total_3d:,.0f}</b> în ultimele 72h!")
                            global_state["last_accum_alert"][alert_key] = time.time()

                    cluster_users_sum = {}
                    cluster_users_entry = {}
                    cluster_sum = 0
                    for k, v in global_state["positions"].items():
                        if market_key in k and not k.startswith(SELF):
                            cluster_sum += v
                            u_name = k.split("|")[0]
                            cluster_users_sum[u_name] = cluster_users_sum.get(u_name, 0) + v
                            entry = global_state["trader_entries"].get(k, 0)
                            if entry > 0: cluster_users_entry[u_name] = entry
                    
                    c_breakdown_list = []
                    c_valid_count = 0
                    for u, v in cluster_users_sum.items():
                        if v >= MIN_TRADER_DISPLAY:
                            entry_p = cluster_users_entry.get(u, 0)
                            entry_str = f" (@ {entry_p*100:.1f}¢)" if entry_p > 0 else ""
                            c_breakdown_list.append(f"• {u}: ${v:,.0f}{entry_str}")
                            c_valid_count += 1
                    
                    is_ping_pong = False
                    if action == "sell":
                        last_buy = global_state["last_buy_times"].get(pos_key, 0)
                        if ts - last_buy < 3600 and last_buy != 0: is_ping_pong = True
                    if action == "buy": global_state["last_buy_times"][pos_key] = ts

                    c_list_users = list(cluster_users_sum.keys())
                    current_score = calc_smart_score(c_list_users, price, is_ping_pong, c_valid_count >= 2)
                    global_state["scores"][market_key] = current_score

                    action_ro = "A CUMPĂRAT"
                    if action == "sell": action_ro = "A VÂNDUT"
                    side_emoji = "🟢" if "YES" in outcome else "🔴"
                    side_formatted = f"{side_emoji} <b>{outcome}</b>"
                    
                    # HOLDING WARNING DEFINED GLOBALLY
                    holding_warning = ""
                    is_holding = False
                    for my_p in global_state["my_portfolio"]:
                        if my_p['title'] == title:
                            holding_warning = "\n⚠️ <b>ATENȚIE: DEȚII ȘI TU ASTA!</b>"
                            is_holding = True

                    if name == SELF:
                        if action == "buy":
                            tg(f"🔔 <b>AI CUMPĂRAT {side_formatted}</b>\n🏆 {title}\n💲{val:.0f} | Scor: <b>{current_score:.1f}</b>")
                        elif action == "sell":
                            entry_price = global_state["trader_entries"].get(pos_key, 0)
                            reinvest_msg = get_ai_reinvestment_strategy(val, market_key)
                            msg = f"🔔 <b>AI VÂNDUT {side_formatted}</b>\n🏆 {title}\n💵 Recuperat: ${val:.0f} @ {price*100:.1f}¢\n"
                            if entry_price > 0: msg += f"🚪 Intrare: {entry_price*100:.1f}¢ ➔ Ieșire: {price*100:.1f}¢\n"
                            msg += f"{reinvest_msg}"
                            tg(msg)
                        threading.Thread(target=sync_portfolio).start()

                    else:
                        if action == "buy":
                            global_state["positions"][pos_key] = global_state["positions"].get(pos_key, 0) + val
                            global_state["trader_entries"][pos_key] = price 

                            current_holding = global_state["positions"][pos_key]

                            if val >= MIN_BUY_ALERT:
                                whale_tag = " 🐋 <b>WHALE BUY!</b>" if val >= WHALE_ALERT else ""
                                tg(f"👤 <b>{name} {action_ro} {side_formatted}</b>{whale_tag}\n🏆 {title}\n💲 +${val:.0f} @ {price*100:.1f}¢\n💼 Total Acum: <b>${current_holding:,.0f}</b>\n🎯 Scor: <b>{current_score:.1f}/10</b>{holding_warning}")

                        elif action == "sell":
                            held_val = global_state["positions"].get(pos_key, 0)
                            entry_price = global_state["trader_entries"].get(pos_key, 0)
                            
                            total_stack = held_val + val 
                            if total_stack == 0: total_stack = val 
                            pct_sold = (val / total_stack) * 100
                            if pct_sold > 100: pct_sold = 100
                            
                            global_state["positions"][pos_key] = max(held_val - val, 0)
                            
                            track = global_state["micro_tracker"].get(pos_key)
                            if track:
                                track['sold'] += val
                                if track['initial'] > 0 and (track['sold'] / track['initial']) >= MICRO_SELL_THRESHOLD_PCT:
                                    tg(f"⚠️ <b>SNEAKY EXIT</b>: {name} a vândut >80% din {title} prin tranzacții mici.")
                                    del global_state["micro_tracker"][pos_key]

                            if is_night and val >= 5000:
                                global_state["nightly_sales"].append({
                                    "trader": name, "market": f"{title} ({outcome})", "amount": val
                                })

                            if val >= MIN_SELL_ALERT:
                                pp_warn = "⚡ <b>PING-PONG</b>" if is_ping_pong else ""
                                exit_str = f"📉 Vândut: <b>{pct_sold:.0f}%</b>"
                                if entry_price > 0: 
                                    exit_str += f"\n🚪 Intrare: {entry_price*100:.1f}¢ ➔ Ieșire: {price*100:.1f}¢"
                                
                                tg(f"{pp_warn}\n📉 <b>{name} {action_ro} {side_formatted}</b>\n🏆 {title}\nSuma: ${val:.0f}\n{exit_str}{holding_warning}")

                    if c_valid_count >= 2 and c_total >= MINI:
                        if c_key not in global_state["cluster_created_at"]:
                            if loop_count == 1:
                                global_state["cluster_created_at"][c_key] = 0 
                                global_state["clusters_sent"][c_key] = c_total
                            else:
                                global_state["cluster_created_at"][c_key] = time.time()

                        last_sent = global_state["clusters_sent"].get(c_key, 0)
                        if loop_count > 1 and c_total > last_sent * 1.2:
                            breakdown_str = "\n".join(c_breakdown_list)
                            tg(f"📊 <b>CLUSTER INCREASE</b>\n🏆 {c_key.split('|')[0]}\n💰 Total: ${c_total:,.0f}\n👥 <b>Participanți:</b>\n{breakdown_str}")
                            global_state["clusters_sent"][c_key] = c_total
                        
                        elif last_sent > 0 and c_total < last_sent * 0.6:
                             tg(f"📉 <b>CLUSTER BREAKING APART</b>\n🏆 {c_key.split('|')[0]}\n💰 Au rămas doar: ${c_total:,.0f}")
                             global_state["clusters_sent"][c_key] = c_total

                    if val >= MIN_DASHBOARD_LOG:
                        note = f"Scor: {current_score:.1f}"
                        if is_ping_pong: note += " | ⚠️ PingPong"
                        if val >= WHALE_ALERT: note += " | 🐋 Whale"
                        if is_holding: note += " | ⚠️ YOUR HOLDING" # FIXED: Add to dashboard
                        
                        global_state["trade_log"].append({
                            "time": datetime.now(RO).strftime("%H:%M"),
                            "trader": name, "action": action,
                            "market": f"{title} ({outcome})", "value": int(val), 
                            "price_fmt": f"{price*100:.1f}¢", "note": note
                        })

                global_state["last"][name] = new_max_ts

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
