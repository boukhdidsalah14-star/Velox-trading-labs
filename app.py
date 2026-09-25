from flask import Flask, render_template_string, request, flash, redirect, url_for, session, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from urllib.parse import urlencode
import os, json, uuid, hmac, hashlib, urllib.request, urllib.error, threading, time, math

# ============================================================
# VELOX TRADING LABS - ALL-IN-ONE APP
# ============================================================
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret-in-render')

# Prefer Render Postgres when DATABASE_URL is configured. Fall back to SQLite.
database_url = os.environ.get('DATABASE_URL', '').strip()
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or ('sqlite:///' + os.path.join(BASE_DIR, 'velox_portal.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'pool_pre_ping': True} if database_url else {}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

NOWPAYMENTS_API_KEY = os.environ.get('NOWPAYMENTS_API_KEY', '').strip()
NOWPAYMENTS_IPN_SECRET = os.environ.get('NOWPAYMENTS_IPN_SECRET', '').strip()
SITE_URL = os.environ.get('SITE_URL', 'https://velox-trading-labs.onrender.com').rstrip('/')
TWELVE_DATA_API_KEY = os.environ.get('TWELVE_DATA_API_KEY', '').strip()
NOWPAYMENTS_API_URL = 'https://api.nowpayments.io/v1/payment'

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@velox.com').strip().lower()
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'change-this-admin-password')

PLANS = {
    '1 Month': {'price': 49, 'days': 30},
    '3 Month': {'price': 147, 'days': 90},
    '6 Month': {'price': 294, 'days': 180},
    '8 Month': {'price': 392, 'days': 240},
    '12 Month': {'price': 588, 'days': 365},
}

CRYPTO_SYMBOLS = {
    'BTC/USD': 'BTCUSDT',
    'ETH/USD': 'ETHUSDT',
    'SOL/USD': 'SOLUSDT',
    'BNB/USD': 'BNBUSDT',
    'XRP/USD': 'XRPUSDT',
    'DOGE/USD': 'DOGEUSDT',
}
FOREX_SYMBOLS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD']
MARKET_SYMBOLS = list(CRYPTO_SYMBOLS.keys()) + FOREX_SYMBOLS + ['XAU/USD']

# ------------------------- Models -------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    subscription_expires = db.Column(db.DateTime, nullable=True)
    active_plan = db.Column(db.String(30), default='None')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_active_subscription(self):
        return bool(self.is_admin or (self.subscription_expires and self.subscription_expires > datetime.utcnow()))

class Signal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pair = db.Column(db.String(50), nullable=False)
    badge = db.Column(db.String(40), default='MARKET SETUP')
    direction = db.Column(db.String(10), default='WATCH')
    entry = db.Column(db.String(50), nullable=False)
    target = db.Column(db.String(50), nullable=False)
    stop_loss = db.Column(db.String(50), nullable=False)
    score = db.Column(db.String(20), default='Setup Score')
    timeframe = db.Column(db.String(20), default='15m')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    payment_id = db.Column(db.String(100), unique=True, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_name = db.Column(db.String(30), nullable=False)
    amount_usd = db.Column(db.Float, nullable=False)
    pay_currency = db.Column(db.String(40), default='usdttrc20')
    pay_amount = db.Column(db.String(100), nullable=True)
    pay_address = db.Column(db.String(255), nullable=True)
    payment_status = db.Column(db.String(50), default='created')
    actually_paid = db.Column(db.String(100), nullable=True)
    tx_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None

# ---------------------- DB migration ----------------------
def ensure_column(conn, table, column, sql_type):
    rows = conn.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
    existing = {r[1] for r in rows}
    if rows and column not in existing:
        conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}')

def migrate_database():
    # create current tables first
    db.create_all()
    # SQLite legacy compatibility. Postgres tables are handled by create_all.
    if not str(app.config['SQLALCHEMY_DATABASE_URI']).startswith(('sqlite:', 'sqlite')):
        return
    with db.engine.begin() as conn:
        # Upgrade User and Payment without deleting them.
        for col, typ in {
            'password_hash':'VARCHAR(255)', 'is_admin':'BOOLEAN', 'subscription_expires':'DATETIME', 'active_plan':'VARCHAR(30)'
        }.items():
            ensure_column(conn, 'user', col, typ)
        for col, typ in {
            'payment_id':'VARCHAR(100)', 'pay_currency':'VARCHAR(40)', 'pay_amount':'VARCHAR(100)', 'pay_address':'VARCHAR(255)',
            'payment_status':'VARCHAR(50)', 'actually_paid':'VARCHAR(100)', 'tx_hash':'VARCHAR(255)', 'created_at':'DATETIME', 'completed_at':'DATETIME'
        }.items():
            ensure_column(conn, 'payment', col, typ)
        # Signal is disposable market data. If an old schema exists, rebuild only signal.
        rows = conn.exec_driver_sql('PRAGMA table_info("signal")').fetchall()
        cols = {r[1] for r in rows}
        needed = {'id','pair','badge','direction','entry','target','stop_loss','score','timeframe','created_at'}
        if rows and not needed.issubset(cols):
            conn.exec_driver_sql('DROP TABLE IF EXISTS signal')
            conn.exec_driver_sql('''CREATE TABLE signal (
                id INTEGER PRIMARY KEY,
                pair VARCHAR(50) NOT NULL,
                badge VARCHAR(40), direction VARCHAR(10), entry VARCHAR(50) NOT NULL,
                target VARCHAR(50) NOT NULL, stop_loss VARCHAR(50) NOT NULL,
                score VARCHAR(20), timeframe VARCHAR(20), created_at DATETIME
            )''')

with app.app_context():
    migrate_database()
    admin = User.query.filter_by(email=ADMIN_EMAIL).first()
    if not admin:
        admin = User(email=ADMIN_EMAIL, is_admin=True, active_plan='Lifetime Admin')
        admin.set_password(ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()
    else:
        # Always make the configured admin an admin; don't overwrite an existing password.
        if not admin.is_admin:
            admin.is_admin = True
            admin.active_plan = 'Lifetime Admin'
            db.session.commit()

# --------------------- Market utilities --------------------
def http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={'User-Agent': 'Velox-Trading-Labs/2.0'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))

def fetch_binance_klines(symbol, limit=120, interval='15m'):
    url = 'https://api.binance.com/api/v3/klines?' + urlencode({'symbol': symbol, 'interval': interval, 'limit': limit})
    data = http_json(url)
    return [{'open':float(x[1]), 'high':float(x[2]), 'low':float(x[3]), 'close':float(x[4]), 'volume':float(x[5])} for x in data]

def fetch_twelve_batch(symbols, interval='15min', outputsize=120):
    if not TWELVE_DATA_API_KEY:
        return {}
    url = 'https://api.twelvedata.com/time_series?' + urlencode({
        'symbol': ','.join(symbols), 'interval':interval, 'outputsize':outputsize,
        'order':'asc', 'apikey':TWELVE_DATA_API_KEY
    })
    data = http_json(url, timeout=20)
    out = {}
    if isinstance(data, dict):
        # Batch responses are keyed by symbol; a single-symbol response may use values directly.
        if isinstance(data.get('values'), list):
            sym = data.get('meta', {}).get('symbol')
            if sym: out[sym] = data['values']
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(value.get('values'), list):
                out[key] = value['values']
    for sym, values in list(out.items()):
        parsed=[]
        for v in values:
            try:
                parsed.append({'open':float(v['open']), 'high':float(v['high']), 'low':float(v['low']), 'close':float(v['close'])})
            except Exception:
                pass
        out[sym]=parsed
    return out

def ema(values, period):
    if len(values) < period: return None
    k = 2/(period+1)
    e = sum(values[:period])/period
    for v in values[period:]: e = v*k + e*(1-k)
    return e

def rsi(values, period=14):
    if len(values) <= period: return 50.0
    gains=[]; losses=[]
    for i in range(1, len(values)):
        d=values[i]-values[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    avg_gain=sum(gains[:period])/period; avg_loss=sum(losses[:period])/period
    for i in range(period, len(gains)):
        avg_gain=(avg_gain*(period-1)+gains[i])/period
        avg_loss=(avg_loss*(period-1)+losses[i])/period
    if avg_loss == 0: return 100.0
    return 100-(100/(1+avg_gain/avg_loss))

def atr(bars, period=14):
    if len(bars) <= period: return None
    trs=[]
    for i in range(1,len(bars)):
        b=bars[i]; prev=bars[i-1]['close']
        trs.append(max(b['high']-b['low'], abs(b['high']-prev), abs(b['low']-prev)))
    return sum(trs[-period:])/period

def build_signal(pair, bars, timeframe='15m'):
    if len(bars) < 50: return None
    closes=[b['close'] for b in bars]
    fast=ema(closes, 9); slow=ema(closes, 21); trend=ema(closes, 50); r=rsi(closes,14); a=atr(bars,14)
    price=closes[-1]
    if not all(x is not None for x in [fast,slow,trend,a]): return None
    score=50
    direction='WATCH'
    reasons=[]
    if fast > slow: score += 15; reasons.append('EMA+'); direction='BUY'
    else: score -= 15; reasons.append('EMA-'); direction='SELL'
    if price > trend: score += 10; reasons.append('trend+')
    else: score -= 10; reasons.append('trend-')
    if 52 <= r <= 68 and direction=='BUY': score += 10; reasons.append('RSI+')
    elif 32 <= r <= 48 and direction=='SELL': score += 10; reasons.append('RSI-')
    elif r > 72 and direction=='BUY': score -= 8
    elif r < 28 and direction=='SELL': score -= 8
    if len(closes)>=5:
        momentum=(price/closes[-5]-1)*100
        if direction=='BUY' and momentum>0: score += 8
        if direction=='SELL' and momentum<0: score += 8
    score=max(0,min(99,int(score)))
    # Only publish actionable setups at 70+, otherwise keep a watch card.
    if score >= 70:
        badge='AI QUANT BUY' if direction=='BUY' else 'AI QUANT SELL'
    else:
        badge='MARKET WATCH'
        direction='WATCH'
    risk=a*1.2
    reward=a*1.8
    if direction=='BUY':
        sl=price-risk; tp=price+reward
    elif direction=='SELL':
        sl=price+risk; tp=price-reward
    else:
        sl=price-risk; tp=price+reward
    def fmt(x):
        if x >= 1000: return f'${x:,.2f}'
        if x >= 10: return f'{x:.3f}'
        return f'{x:.5f}'
    return {'pair':pair,'badge':badge,'direction':direction,'entry':fmt(price),'target':fmt(tp),'stop_loss':fmt(sl),
            'score':f'{score}/100','timeframe':timeframe,'created_at':datetime.utcnow(), 'rsi':round(r,1)}

def collect_market_signals():
    results=[]
    # Crypto from Binance: no Twelve Data credits required.
    for pair, symbol in CRYPTO_SYMBOLS.items():
        try:
            sig=build_signal(pair, fetch_binance_klines(symbol), '15m')
            if sig: results.append(sig)
        except Exception as e: print('Crypto feed error',pair,e)
    # Forex + gold from Twelve Data in one batch request. One symbol = one credit.
    if TWELVE_DATA_API_KEY:
        symbols=FOREX_SYMBOLS + ['XAU/USD']
        try:
            batch=fetch_twelve_batch(symbols, '15min', 120)
            for pair in symbols:
                bars=batch.get(pair) or batch.get(pair.replace('/',''))
                if bars:
                    sig=build_signal(pair,bars,'15m')
                    if sig: results.append(sig)
        except Exception as e: print('Twelve Data error',e)
    # Put actionable setups first, then watches. Never invent a signal if data failed.
    results.sort(key=lambda x:(x['direction']=='WATCH', -int(x['score'].split('/')[0])))
    return results

def refresh_signal_database(force=False):
    now=datetime.utcnow()
    last=Signal.query.order_by(Signal.created_at.desc()).first()
    if not force and last and (now-last.created_at).total_seconds() < 300:
        return
    signals=collect_market_signals()
    if not signals: return
    Signal.query.delete()
    for s in signals:
        db.session.add(Signal(pair=s['pair'],badge=s['badge'],direction=s['direction'],entry=s['entry'],target=s['target'],stop_loss=s['stop_loss'],score=s['score'],timeframe=s['timeframe'],created_at=s['created_at']))
    db.session.commit()

def background_signal_loop():
    # Best-effort on Render; page requests also refresh stale data, so the app still works after sleep/wake.
    while True:
        try:
            with app.app_context():
                refresh_signal_database(False)
        except Exception as e: print('Signal loop:',e)
        time.sleep(300)

threading.Thread(target=background_signal_loop, daemon=True).start()

# ------------------------- UI -------------------------
BASE_STYLE = '''
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#000205;--panel:rgba(2,6,12,.9);--green:#00ff66;--green2:rgba(0,255,102,.35);--red:#ff3344;--muted:#8b949e;--white:#fff}
*{box-sizing:border-box;margin:0;padding:0;font-family:Inter,sans-serif}html,body{background:#000205;color:#fff;min-height:100vh;overflow-x:hidden}body{position:relative}
.bg{position:fixed;inset:0;z-index:0;background:radial-gradient(circle at 20% 10%,rgba(0,255,102,.07),transparent 35%),radial-gradient(circle at 85% 70%,rgba(0,180,255,.05),transparent 30%),#000205}.grid{position:fixed;inset:0;background-image:linear-gradient(rgba(0,255,102,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(0,255,102,.035) 1px,transparent 1px);background-size:45px 45px;z-index:0;mask-image:linear-gradient(to bottom,#000,transparent 90%)}
.header{height:58px;background:rgba(2,6,12,.94);border-bottom:1px solid var(--green2);display:flex;align-items:center;justify-content:space-between;padding:0 16px;position:sticky;top:0;z-index:100;backdrop-filter:blur(18px)}.brand{display:flex;align-items:center;gap:9px}.logo{width:33px;height:33px;border:1px solid var(--green);border-radius:8px;display:grid;place-items:center;color:var(--green);font-family:'JetBrains Mono';font-weight:900;box-shadow:0 0 15px rgba(0,255,102,.35)}.brand b{font-size:11px;color:var(--green);letter-spacing:.8px}.menu{position:relative}.menu button{width:34px;height:34px;border:1px solid var(--green2);background:#050b12;color:#fff;border-radius:8px}.drop{display:none;position:absolute;right:0;top:42px;width:170px;background:#030814;border:1px solid var(--green2);border-radius:10px;overflow:hidden}.drop.show{display:block}.drop a{display:block;padding:12px;color:#bbb;text-decoration:none;font-size:12px;border-bottom:1px solid rgba(255,255,255,.05)}
.wrap{position:relative;z-index:5;max-width:1050px;margin:auto;padding:0 14px 70px}.ticker{height:34px;display:flex;gap:20px;align-items:center;overflow:auto;white-space:nowrap;color:#8b949e;font:10px 'JetBrains Mono';border-bottom:1px solid rgba(0,255,102,.12)}.ticker span{color:var(--green)}
.hero{min-height:520px;display:grid;align-items:center;padding:58px 0 45px;grid-template-columns:1.1fr .9fr;gap:28px}.eyebrow{color:var(--green);font:11px 'JetBrains Mono';letter-spacing:2px;margin-bottom:12px}.hero h1{font-size:clamp(38px,7vw,72px);line-height:.95;letter-spacing:-3px;margin-bottom:18px}.hero h1 span{color:var(--green);text-shadow:0 0 25px rgba(0,255,102,.35)}.hero p{color:#9aa4ad;max-width:610px;line-height:1.7;font-size:14px}.actions{display:flex;gap:10px;margin-top:24px;flex-wrap:wrap}.btn{display:inline-block;text-decoration:none;border:0;border-radius:8px;padding:13px 18px;font:700 12px 'JetBrains Mono';cursor:pointer}.primary{background:var(--green);color:#000;box-shadow:0 0 20px rgba(0,255,102,.28)}.ghost{border:1px solid var(--green2);color:#fff;background:rgba(255,255,255,.03)}
.hero-card{height:370px;border:1px solid var(--green2);border-radius:18px;overflow:hidden;background:linear-gradient(145deg,rgba(0,255,102,.08),rgba(0,0,0,.2)),url('https://images.unsplash.com/photo-1642790106117-e829e14a795f?auto=format&fit=crop&w=1000&q=85') center/cover;box-shadow:0 25px 70px rgba(0,0,0,.75)}.hero-card:after{content:'LIVE MARKET INTELLIGENCE';display:block;padding:16px;height:100%;background:linear-gradient(to top,rgba(0,0,0,.9),transparent 55%);color:var(--green);font:11px 'JetBrains Mono';letter-spacing:1px}
.section{padding:55px 0}.section-head{display:flex;justify-content:space-between;gap:15px;align-items:end;margin-bottom:22px}.section h2{font-size:27px;letter-spacing:-1px}.section-head p{color:var(--muted);font-size:12px;max-width:520px;line-height:1.6}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.image-card{min-height:360px;border:1px solid var(--green2);border-radius:15px;overflow:hidden;position:relative;background:#02060c center/cover}.image-card .shade{position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.95),rgba(0,0,0,.05) 70%)}.image-card .txt{position:absolute;left:18px;right:18px;bottom:18px}.image-card h3{font-size:20px;margin-bottom:6px}.image-card p{color:#a7afb6;font-size:11px;line-height:1.6}.gold{background-image:url('https://images.unsplash.com/photo-1610375461246-83df859d849d?auto=format&fit=crop&w=900&q=85')}.car{background-image:url('https://images.unsplash.com/photo-1511919884226-fd3cad34687c?auto=format&fit=crop&w=900&q=85')}.green-car{background-image:url('https://images.unsplash.com/photo-1542282088-72c9c27ed0cd?auto=format&fit=crop&w=900&q=85')}
.feature{display:grid;grid-template-columns:1fr 1fr;gap:15px}.feature-box{border:1px solid var(--green2);border-radius:15px;background:var(--panel);padding:24px;min-height:220px}.feature-box h3{color:var(--green);font:700 12px 'JetBrains Mono';letter-spacing:1px;margin-bottom:12px}.feature-box h4{font-size:24px;margin-bottom:9px}.feature-box p,.steps p{color:#9aa4ad;font-size:12px;line-height:1.7}.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.step{padding:22px;border:1px solid rgba(255,255,255,.09);background:rgba(255,255,255,.025);border-radius:14px}.num{font:800 12px 'JetBrains Mono';color:var(--green);margin-bottom:15px}
.pricing{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.plan{border:1px solid rgba(255,255,255,.1);background:var(--panel);border-radius:12px;padding:15px}.plan.featured{border-color:var(--green);box-shadow:0 0 25px rgba(0,255,102,.14)}.plan h3{font-size:12px}.plan .price{font:800 23px 'JetBrains Mono';color:var(--green);margin:10px 0 3px}.plan small{color:var(--muted);font-size:9px}.plan a{margin-top:13px;width:100%;text-align:center}
.terminal{background:var(--panel);border:1px solid var(--green);border-radius:15px;padding:15px;box-shadow:0 0 35px rgba(0,255,102,.12)}.terminal-head{display:flex;justify-content:space-between;gap:10px;align-items:center;border-bottom:1px solid rgba(0,255,102,.15);padding-bottom:12px;margin-bottom:12px}.terminal-title{color:var(--green);font:800 13px 'JetBrains Mono'}.live{font:9px 'JetBrains Mono';color:var(--green)}.signal-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.signal{border:1px solid rgba(0,255,102,.18);border-radius:10px;padding:12px;background:rgba(0,255,102,.025)}.signal-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.pair{font:700 12px 'JetBrains Mono'}.badge{font:800 8px 'JetBrains Mono';padding:4px 6px;border-radius:4px;color:#000;background:var(--green)}.badge.watch{background:#444;color:#fff}.buy{color:var(--green)}.sell{color:var(--red)}.details{display:grid;grid-template-columns:1fr 1fr;gap:5px;color:#9aa4ad;font:10px 'JetBrains Mono'}.details b{color:#fff}.lock{padding:30px;text-align:center;color:var(--muted)}
.form-card{max-width:440px;margin:60px auto;background:var(--panel);border:1px solid var(--green2);border-radius:15px;padding:22px}.form-card h1{font-size:20px;margin-bottom:15px}.form-card input{width:100%;padding:12px;background:#02060c;border:1px solid var(--green2);color:#fff;border-radius:8px;margin:6px 0;font:12px 'JetBrains Mono'}.flash{padding:9px;border:1px solid var(--green2);color:var(--green);border-radius:7px;font-size:11px;margin:8px 0}.checkout{max-width:520px;margin:45px auto}.paybox{border:1px solid var(--green);border-radius:14px;background:rgba(0,255,102,.04);padding:17px;margin-top:15px}.address{word-break:break-all;color:var(--green);background:#02060c;padding:10px;border-radius:8px;margin-top:7px;font:10px 'JetBrains Mono'}.status{margin-top:12px;text-align:center;color:#aaa;font:10px 'JetBrains Mono'}
.footer{border-top:1px solid rgba(0,255,102,.16);padding:35px 0;color:#727b83;font-size:10px;line-height:1.9}.footer strong{color:var(--green)}
@media(max-width:760px){.hero{grid-template-columns:1fr;padding-top:40px}.hero-card{height:260px}.cards,.steps,.feature{grid-template-columns:1fr}.pricing{grid-template-columns:1fr 1fr}.signal-grid{grid-template-columns:1fr}.section{padding:38px 0}.hero h1{font-size:43px}.image-card{min-height:300px}}
</style>'''

BACKGROUND = '''<div class="bg"></div><div class="grid"></div><script>function toggleMenu(){document.getElementById('drop')?.classList.toggle('show')}document.addEventListener('click',e=>{if(!e.target.closest('.menu'))document.getElementById('drop')?.classList.remove('show')})</script>'''
HEADER = '''<header class="header"><div class="brand"><div class="logo">V</div><b>VELOX TRADING LABS</b></div><div class="menu"><button onclick="toggleMenu()">⋮</button><div id="drop" class="drop"><a href="/">Home</a>{% if user_is_auth %}<a href="/terminal">Signals Terminal</a><a href="/logout">Logout</a>{% else %}<a href="/login">Login</a><a href="/register">Create Account</a>{% endif %}</div></div></header>'''

LANDING = BASE_STYLE + BACKGROUND + '''</head><body>{{header|safe}}<div class="wrap">
<div class="ticker"><span>VELOX ENGINE: ONLINE</span><span>FOREX: LIVE ANALYSIS</span><span>CRYPTO: LIVE ANALYSIS</span><span>XAU/USD: LIVE ANALYSIS</span><span>15M ENGINE</span></div>
<section class="hero"><div><div class="eyebrow">AUTOMATED MARKET INTELLIGENCE</div><h1>MOVE WITH THE<br><span>MARKET.</span></h1><p>Velox Trading Labs analyzes live market data across forex, crypto and gold, turning price action and technical conditions into structured BUY, SELL and WATCH setups.</p><div class="actions"><a class="btn primary" href="/register">START HERE</a><a class="btn ghost" href="/login">LOGIN</a></div></div><div class="hero-card"></div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">THE PLATFORM</div><h2>Multi-Market Trading Intelligence</h2></div><p>One terminal for live market monitoring, structured entries, targets and stop-loss levels. No guaranteed returns—just data-driven setups.</p></div><div class="feature"><div class="feature-box"><h3>LIVE MARKET ENGINE</h3><h4>Automatic analysis.</h4><p>Velox periodically refreshes market candles and calculates trend, EMA, RSI and volatility conditions. Signals are generated from current market data rather than hard-coded prices.</p></div><div class="feature-box"><h3>PRECISION TERMINAL</h3><h4>Entry • TP • SL</h4><p>Every actionable setup displays direction, entry, target, stop loss, timeframe and a setup score so the user can see exactly what the engine detected.</p></div></div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">GLOBAL MARKETS</div><h2>Trade the world's movement.</h2></div></div><div class="cards"><div class="image-card gold"><div class="shade"></div><div class="txt"><h3>GOLD / XAU</h3><p>Monitor precious-metal momentum and volatility alongside the currency market.</p></div></div><div class="image-card car"><div class="shade"></div><div class="txt"><h3>PRECISION. SPEED. DISCIPLINE.</h3><p>A premium interface built for traders who want a clean terminal instead of noise.</p></div></div><div class="image-card green-car"><div class="shade"></div><div class="txt"><h3>MOVE WITH THE MARKET.</h3><p>Track multiple markets from one streamlined dashboard.</p></div></div></div></section>
<section class="section"><div class="section-head"><div><div class="eyebrow">HOW IT WORKS</div><h2>Three steps to the terminal.</h2></div></div><div class="steps"><div class="step"><div class="num">01</div><h3>Create account</h3><p>Register with an email and password. No payment is required to create the account.</p></div><div class="step"><div class="num">02</div><h3>Choose your plan</h3><p>Select 1, 3, 6, 8 or 12 months and pay securely with USDT on TRON through NOWPayments.</p></div><div class="step"><div class="num">03</div><h3>Enter the terminal</h3><p>Once payment is confirmed, your subscription activates automatically and the terminal unlocks.</p></div></div></section>
<section class="section" id="pricing"><div class="section-head"><div><div class="eyebrow">ACCESS</div><h2>Choose your duration.</h2></div></div><div class="pricing">{% for name,p in plans.items() %}<div class="plan {% if name=='1 Month' %}featured{% endif %}"><h3>{{name}}</h3><div class="price">${{p.price}}</div><small>{{p.days}} days</small><a class="btn {% if name=='1 Month' %}primary{% else %}ghost{% endif %}" href="/checkout/{{name}}">SELECT</a></div>{% endfor %}</div></section>
<section class="section"><div class="feature-box" style="text-align:center"><div class="eyebrow">VELOX TRADING LABS</div><h2>Ready when the market moves.</h2><p style="margin:12px auto;max-width:600px">Create your account and explore the terminal. The system only activates paid access after a verified payment, while the admin account has full testing access.</p><a class="btn primary" href="/register">START HERE</a></div></section>
<footer class="footer"><strong>VELOX TRADING LABS</strong><br>Markets • Forex • Crypto • Gold • Signals Terminal • Pricing • Login • Create Account<br><span>Data-driven analysis. No guaranteed profits. Trading involves risk.</span></footer>
</div></body></html>'''

TERMINAL = BASE_STYLE + BACKGROUND + '''</head><body>{{header|safe}}<div class="wrap"><div class="ticker"><span>ENGINE ONLINE</span><span>LAST UPDATE: {{last_update}}</span><span>15M ANALYSIS</span></div><section class="section"><div class="terminal"><div class="terminal-head"><div><div class="terminal-title">⚡ VELOX SIGNALS TERMINAL</div><div style="color:#777;font-size:9px;margin-top:4px">Automatic market analysis • live data</div></div><div class="live">● LIVE</div></div>{% if signals %}<div class="signal-grid">{% for s in signals %}<div class="signal"><div class="signal-top"><span class="pair">{{s.pair}}</span><span class="badge {% if s.direction=='WATCH' %}watch{% endif %}">{{s.badge}}</span></div><div style="font:800 12px 'JetBrains Mono';margin-bottom:8px" class="{% if s.direction=='BUY' %}buy{% elif s.direction=='SELL' %}sell{% endif %}">{{s.direction}}</div><div class="details"><span>Entry <b>{{s.entry}}</b></span><span>Target <b>{{s.target}}</b></span><span>Stop <b>{{s.stop_loss}}</b></span><span>Score <b>{{s.score}}</b></span><span>TF <b>{{s.timeframe}}</b></span><span>Updated <b>{{s.created_at.strftime('%H:%M UTC')}}</b></span></div></div>{% endfor %}</div>{% else %}<div class="lock">Waiting for live market data. Refresh in a moment.</div>{% endif %}</div></section></div></body></html>'''

FORM = BASE_STYLE + BACKGROUND + '''</head><body>{{header|safe}}<div class="wrap"><div class="form-card"><div class="eyebrow">VELOX ACCOUNT</div><h1>{{title}}</h1>{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}<form method="post"><input name="email" type="email" placeholder="Email" required><input name="password" type="password" placeholder="Password" required>{% if register %}<input name="confirm" type="password" placeholder="Confirm password" required>{% endif %}<button class="btn primary" style="width:100%;margin-top:8px" type="submit">{{button}}</button></form><p style="color:#777;font-size:10px;text-align:center;margin-top:14px">{% if register %}Already have an account? <a style="color:#00ff66" href="/login">Login</a>{% else %}New here? <a style="color:#00ff66" href="/register">Create account</a>{% endif %}</p></div></div></body></html>'''

CHECKOUT = BASE_STYLE + BACKGROUND + '''</head><body>{{header|safe}}<div class="wrap"><div class="checkout"><div class="eyebrow">SECURE CRYPTO CHECKOUT</div><h1>{{plan_name}} — ${{plan.price}}</h1><p style="color:#8b949e;font-size:11px;line-height:1.6;margin-top:8px">USDT on TRON / TRC-20. Your subscription activates automatically after NOWPayments confirms the transaction.</p>{% with messages=get_flashed_messages() %}{% for m in messages %}<div class="flash">{{m}}</div>{% endfor %}{% endwith %}{% if current_user.is_admin %}<div class="paybox"><h3 style="color:#00ff66">ADMIN ACCESS</h3><p style="color:#aaa;font-size:11px;margin:8px 0 14px">Your admin account has lifetime testing access. No payment is required.</p><a class="btn primary" href="/terminal">ENTER SIGNALS TERMINAL</a></div>{% elif payment %}<div class="paybox"><div style="text-align:center;color:#00ff66;font-weight:800">SEND USDT</div><div style="text-align:center;color:#aaa;font-size:10px;margin-top:5px">Network: TRON / TRC-20</div><div style="margin-top:13px;color:#aaa;font-size:10px">Amount</div><div style="font:800 22px 'JetBrains Mono';color:#00ff66;margin-top:3px">{{payment.pay_amount}} USDT</div><div style="margin-top:12px;color:#aaa;font-size:10px">Payment address</div><div id="addr" class="address">{{payment.pay_address}}</div><button class="btn ghost" style="width:100%;margin-top:8px" onclick="navigator.clipboard.writeText(document.getElementById('addr').innerText);return false">COPY ADDRESS</button><div class="status">Status: <b id="status">{{payment.payment_status}}</b></div></div><script>async function poll(){try{let r=await fetch('/payment/status/{{payment.order_id}}');let d=await r.json();document.getElementById('status').textContent=d.status;if(d.status==='finished'){location.href='/terminal'}}catch(e){}}setInterval(poll,8000)</script>{% else %}<form method="post" style="margin-top:18px"><button class="btn primary" style="width:100%">CREATE USDT PAYMENT</button></form>{% endif %}</div></div></body></html>'''

# --------------------- rendering/routes ---------------------
def render_page(template, **kwargs):
    header=render_template_string(HEADER, user_is_auth=current_user.is_authenticated)
    return render_template_string(template, header=header, current_user=current_user, user_is_auth=current_user.is_authenticated, plans=PLANS, **kwargs)

@app.route('/')
def index():
    return render_page(LANDING)

@app.route('/terminal')
@login_required
def terminal():
    if not current_user.has_active_subscription():
        flash('Active subscription required to enter the Signals Terminal.')
        return redirect(url_for('index'))
    try:
        refresh_signal_database(False)
    except Exception as e:
        print('Terminal refresh:',e)
    signals=Signal.query.order_by(Signal.created_at.desc()).limit(20).all()
    last_update=signals[0].created_at.strftime('%Y-%m-%d %H:%M UTC') if signals else 'waiting'
    return render_page(TERMINAL, signals=signals, last_update=last_update)

@app.route('/register', methods=['GET','POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('terminal' if current_user.has_active_subscription() else 'index'))
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); password=request.form.get('password',''); confirm=request.form.get('confirm','')
        if not email or not password: flash('Email and password are required.'); return redirect(url_for('register'))
        if len(password)<6: flash('Password must be at least 6 characters.'); return redirect(url_for('register'))
        if password!=confirm: flash('Passwords do not match.'); return redirect(url_for('register'))
        if User.query.filter_by(email=email).first(): flash('That account already exists. Please login.'); return redirect(url_for('login'))
        user=User(email=email,active_plan='None'); user.set_password(password); db.session.add(user); db.session.commit(); login_user(user)
        return redirect(url_for('index'))
    return render_page(FORM,title='Create your account',button='CREATE ACCOUNT',register=True)

@app.route('/login', methods=['GET','POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('terminal' if current_user.has_active_subscription() else 'index'))
    if request.method=='POST':
        email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
        user=User.query.filter_by(email=email).first()
        if user and user.check_password(password): login_user(user); return redirect(url_for('terminal' if user.has_active_subscription() else 'index'))
        flash('Invalid email or password.')
    return render_page(FORM,title='Login to Velox',button='LOGIN',register=False)

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('index'))

@app.route('/checkout/<path:plan_name>', methods=['GET','POST'])
def checkout(plan_name):
    if plan_name not in PLANS: abort(404)
    if not current_user.is_authenticated: session['selected_plan']=plan_name; return redirect(url_for('register'))
    if current_user.is_admin: return render_page(CHECKOUT,plan_name=plan_name,plan=PLANS[plan_name],payment=None)
    if request.method=='POST':
        try:
            existing=Payment.query.filter_by(user_id=current_user.id,plan_name=plan_name).filter(Payment.payment_status.in_(['created','waiting','confirming','confirmed'])).order_by(Payment.id.desc()).first()
            if existing: return render_page(CHECKOUT,plan_name=plan_name,plan=PLANS[plan_name],payment=existing)
            if not NOWPAYMENTS_API_KEY: raise RuntimeError('NOWPayments API key missing on server.')
            order_id=f'VELOX-{current_user.id}-{uuid.uuid4().hex[:16]}'
            payload={'price_amount':PLANS[plan_name]['price'],'price_currency':'usd','pay_currency':'usdttrc20','ipn_callback_url':f'{SITE_URL}/payment/ipn','order_id':order_id,'order_description':f'Velox Trading Labs - {plan_name}'}
            req=urllib.request.Request(NOWPAYMENTS_API_URL,data=json.dumps(payload).encode(),headers={'x-api-key':NOWPAYMENTS_API_KEY,'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(req,timeout=25) as r: result=json.loads(r.read().decode())
            payment=Payment(order_id=order_id,payment_id=str(result.get('payment_id','')),user_id=current_user.id,plan_name=plan_name,amount_usd=PLANS[plan_name]['price'],pay_currency=result.get('pay_currency','usdttrc20'),pay_amount=str(result.get('pay_amount','')),pay_address=result.get('pay_address',''),payment_status=str(result.get('payment_status','waiting')))
            db.session.add(payment); db.session.commit(); return render_page(CHECKOUT,plan_name=plan_name,plan=PLANS[plan_name],payment=payment)
        except Exception as e:
            print('Payment error:',e); flash('Unable to create payment right now. Please try again.'); return redirect(url_for('checkout',plan_name=plan_name))
    existing=Payment.query.filter_by(user_id=current_user.id,plan_name=plan_name).filter(Payment.payment_status.in_(['created','waiting','confirming','confirmed'])).order_by(Payment.id.desc()).first()
    return render_page(CHECKOUT,plan_name=plan_name,plan=PLANS[plan_name],payment=existing)

@app.route('/payment/status/<order_id>')
@login_required
def payment_status(order_id):
    p=Payment.query.filter_by(order_id=order_id,user_id=current_user.id).first()
    if not p: return jsonify(status='not_found'),404
    # Also poll NOWPayments so a browser does not have to wait for IPN alone.
    if p.payment_id and NOWPAYMENTS_API_KEY and p.payment_status not in ('finished','failed','expired','refunded'):
        try:
            req=urllib.request.Request(f'https://api.nowpayments.io/v1/payment/{p.payment_id}',headers={'x-api-key':NOWPAYMENTS_API_KEY,'User-Agent':'Velox-Trading-Labs/2.0'})
            with urllib.request.urlopen(req,timeout=12) as r: data=json.loads(r.read().decode())
            status=str(data.get('payment_status',p.payment_status)).lower(); p.payment_status=status
            p.actually_paid=str(data.get('actually_paid',p.actually_paid or '')); p.tx_hash=data.get('payin_hash') or p.tx_hash
            if status=='finished': activate_subscription(p)
            else: db.session.commit()
        except Exception as e: print('Payment status poll:',e)
    return jsonify(status=p.payment_status)

def verify_ipn(data, signature):
    if not NOWPAYMENTS_IPN_SECRET or not signature: return False
    raw=json.dumps(data,sort_keys=True,separators=(',',':'))
    expected=hmac.new(NOWPAYMENTS_IPN_SECRET.encode(),raw.encode(),hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected,signature)

def activate_subscription(payment):
    if payment.completed_at: return
    plan=PLANS.get(payment.plan_name); user=db.session.get(User,payment.user_id)
    if not plan or not user: return
    now=datetime.utcnow(); start=user.subscription_expires if user.subscription_expires and user.subscription_expires>now else now
    user.subscription_expires=start+timedelta(days=plan['days']); user.active_plan=payment.plan_name; payment.payment_status='finished'; payment.completed_at=now; db.session.commit()

@app.route('/payment/ipn',methods=['POST'])
def payment_ipn():
    data=request.get_json(silent=True) or {}
    if not verify_ipn(data,request.headers.get('x-nowpayments-sig')): return 'Invalid signature',401
    pid=str(data.get('payment_id','')); oid=str(data.get('order_id',''))
    p=Payment.query.filter_by(payment_id=pid).first() if pid else None
    if not p and oid: p=Payment.query.filter_by(order_id=oid).first()
    if not p: return 'Unknown payment',404
    status=str(data.get('payment_status',p.payment_status)).lower(); p.payment_status=status; p.actually_paid=str(data.get('actually_paid',p.actually_paid or '')); p.tx_hash=data.get('payin_hash') or data.get('tx_hash') or p.tx_hash
    if status=='finished': activate_subscription(p)
    else: db.session.commit()
    return 'OK',200

@app.route('/health')
def health(): return jsonify(status='ok',service='Velox Trading Labs',time=datetime.utcnow().isoformat()+'Z')

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))
