from flask import Flask, render_template_string, request, flash, redirect, url_for, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os, threading, time, urllib.request, urllib.error, json, uuid, hmac, hashlib

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-in-render")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "velox_portal.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

NOWPAYMENTS_API_KEY = os.environ.get("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET")
SITE_URL = os.environ.get("SITE_URL", "https://velox-trading-labs.onrender.com").rstrip("/")
NOWPAYMENTS_API_URL = "https://api.nowpayments.io/v1/payment"

PLANS = {
    "1 Month": {"price": 49, "days": 30},
    "3 Month": {"price": 147, "days": 90},
    "6 Month": {"price": 294, "days": 180},
    "8 Month": {"price": 392, "days": 240},
    "12 Month": {"price": 588, "days": 365},
}

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    subscription_expires = db.Column(db.DateTime, nullable=True)
    active_plan = db.Column(db.String(30), default="None")
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    def has_active_subscription(self):
        return self.is_admin or bool(self.subscription_expires and self.subscription_expires > datetime.utcnow())

class Signal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pair = db.Column(db.String(50), nullable=False)
    badge = db.Column(db.String(30), default="MARKET SETUP")
    entry = db.Column(db.String(50), nullable=False)
    target = db.Column(db.String(50), nullable=False)
    stop_loss = db.Column(db.String(50), nullable=False)
    accuracy = db.Column(db.String(20), default="Setup Score")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(100), unique=True, nullable=False)
    payment_id = db.Column(db.String(100), unique=True, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    plan_name = db.Column(db.String(30), nullable=False)
    amount_usd = db.Column(db.Float, nullable=False)
    pay_currency = db.Column(db.String(40), default="usdttrc20")
    pay_amount = db.Column(db.String(100), nullable=True)
    pay_address = db.Column(db.String(255), nullable=True)
    payment_status = db.Column(db.String(50), default="created")
    actually_paid = db.Column(db.String(100), nullable=True)
    tx_hash = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def fetch_live_price(symbol):
    try:
        pair = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}.get(symbol)
        if not pair:
            return None
        req = urllib.request.Request(
            f"https://api.binance.com/api/v3/ticker/price?symbol={pair}",
            headers={"User-Agent": "Velox-Trading-Labs/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return float(json.loads(response.read().decode())["price"])
    except Exception as e:
        print(f"Market price error {symbol}: {e}")
        return None

def run_market_analyzer():
    with app.app_context():
        if Signal.query.count() == 0:
            for pair in ["BTC/USD (Crypto)", "ETH/USD (Crypto)", "EUR/USD (Forex)"]:
                db.session.add(Signal(pair=pair, badge="MARKET SETUP", entry="LIVE",
                                       target="Calculating", stop_loss="Calculating",
                                       accuracy="Setup Score"))
            db.session.commit()
    while True:
        try:
            time.sleep(300)
            with app.app_context():
                price = fetch_live_price("BTC")
                if price:
                    Signal.query.filter(Signal.pair.like("%BTC/USD%")).delete()
                    db.session.add(Signal(
                        pair="BTC/USD (Crypto)", badge="LIVE MARKET SETUP",
                        entry=f"${price:,.2f}", target=f"${price * 1.025:,.2f}",
                        stop_loss=f"${price * .985:,.2f}", accuracy="Setup Score"
                    ))
                    db.session.commit()
        except Exception as e:
            print(f"Analyzer loop error: {e}")

def migrate_sqlite_schema():
    """Safely migrate the existing SQLite DB without touching users/payments."""
    db.create_all()

    # The signal table is disposable market data. If an old deployment created
    # an incompatible signal schema, rebuild ONLY this table. User/payment data
    # is never deleted here.
    signal_required = {
        "id", "pair", "badge", "entry", "target", "stop_loss", "accuracy", "created_at"
    }

    with db.engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(signal)").fetchall()
        signal_columns = {row[1] for row in rows}

        if rows and not signal_required.issubset(signal_columns):
            conn.exec_driver_sql("DROP TABLE IF EXISTS signal")
            conn.exec_driver_sql("""
                CREATE TABLE signal (
                    id INTEGER PRIMARY KEY,
                    pair VARCHAR(50) NOT NULL,
                    badge VARCHAR(30) DEFAULT 'MARKET SETUP',
                    entry VARCHAR(50) NOT NULL,
                    target VARCHAR(50) NOT NULL,
                    stop_loss VARCHAR(50) NOT NULL,
                    accuracy VARCHAR(20) DEFAULT 'Setup Score',
                    created_at DATETIME
                )
            """)

        expected = {
            "user": {
                "email": "VARCHAR(120)",
                "password_hash": "VARCHAR(255)",
                "is_admin": "BOOLEAN",
                "subscription_expires": "DATETIME",
                "active_plan": "VARCHAR(30)",
            },
            "payment": {
                "order_id": "VARCHAR(100)",
                "payment_id": "VARCHAR(100)",
                "user_id": "INTEGER",
                "plan_name": "VARCHAR(30)",
                "amount_usd": "FLOAT",
                "pay_currency": "VARCHAR(40)",
                "pay_amount": "VARCHAR(100)",
                "pay_address": "VARCHAR(255)",
                "payment_status": "VARCHAR(50)",
                "actually_paid": "VARCHAR(100)",
                "tx_hash": "VARCHAR(255)",
                "created_at": "DATETIME",
                "completed_at": "DATETIME",
            },
        }

        for table, columns in expected.items():
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {row[1] for row in rows}
            for column, sql_type in columns.items():
                if column not in existing:
                    conn.exec_driver_sql(
                        f'ALTER TABLE "{table}" ADD COLUMN "{column}" {sql_type}'
                    )

    db.session.remove()


def safe_signal_query(limit=20):
    """Read signals with a recovery path for an incompatible legacy table."""
    try:
        return Signal.query.order_by(Signal.id.desc()).limit(limit).all()
    except Exception as first_error:
        message = str(first_error).lower()
        print(f"Signal query failed: {first_error}")
        db.session.rollback()
        # Only rebuild for schema errors. Do NOT destroy market data because of
        # transient SQLite locking/connection errors.
        schema_error = (
            "no such column" in message
            or "no such table" in message
            or "malformed" in message
        )
        if not schema_error:
            return []
        try:
            with db.engine.begin() as conn:
                conn.exec_driver_sql("DROP TABLE IF EXISTS signal")
                conn.exec_driver_sql("""
                    CREATE TABLE signal (
                        id INTEGER PRIMARY KEY,
                        pair VARCHAR(50) NOT NULL,
                        badge VARCHAR(30) DEFAULT 'MARKET SETUP',
                        entry VARCHAR(50) NOT NULL,
                        target VARCHAR(50) NOT NULL,
                        stop_loss VARCHAR(50) NOT NULL,
                        accuracy VARCHAR(20) DEFAULT 'Setup Score',
                        created_at DATETIME
                    )
                """)
            db.session.remove()
            return Signal.query.order_by(Signal.id.desc()).limit(limit).all()
        except Exception as second_error:
            print(f"Signal-table repair failed: {second_error}")
            db.session.rollback()
            return []

with app.app_context():
    migrate_sqlite_schema()
    admin_email = "admin@velox.com"
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(email=admin_email, is_admin=True, active_plan="Lifetime Admin")
        admin.set_password(os.environ.get("ADMIN_PASSWORD", "change-this-admin-password"))
        db.session.add(admin)
        db.session.commit()

# Market analysis runs on demand; no background SQLite thread, avoiding Render/SQLite worker conflicts.

BASE_STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg-deep:#000205;--panel-bg:rgba(2,6,12,.88);--border-color:rgba(0,255,102,.35);--neon-green:#00ff66;--neon-green-glow:rgba(0,255,102,.7);--neon-red:#ff3344;--text-main:#fff;--text-muted:#8b949e}
*{box-sizing:border-box;margin:0;padding:0;font-family:Inter,sans-serif;-webkit-tap-highlight-color:transparent}
html,body{background:#000205;color:#fff;width:100%;min-height:100vh;overflow-x:hidden}
#candlestick-canvas{position:fixed;inset:0;width:100vw;height:100vh;z-index:0;pointer-events:none;background:#000205}
.cinematic-overlay{position:fixed;inset:0;background:radial-gradient(circle at center,rgba(0,2,5,.3),rgba(0,2,5,.75));z-index:1;pointer-events:none}
.header{height:56px;background:rgba(2,6,12,.95);backdrop-filter:blur(16px);border-bottom:1px solid var(--border-color);display:flex;align-items:center;justify-content:space-between;padding:0 16px;position:sticky;top:0;z-index:100}
.brand{display:flex;align-items:center;gap:10px}.pro-logo{width:32px;height:32px;background:linear-gradient(135deg,#07101e,#010408);border-radius:8px;display:flex;align-items:center;justify-content:center;border:1.5px solid var(--neon-green);box-shadow:0 0 12px var(--neon-green-glow);font-family:'JetBrains Mono';font-weight:800;color:var(--neon-green)}
.brand-name{font-size:11px;font-weight:800;color:var(--neon-green);text-shadow:0 0 8px var(--neon-green-glow);letter-spacing:.5px}
.menu-container{position:relative}.menu-btn{background:rgba(255,255,255,.05);border:1px solid var(--border-color);color:#fff;width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;cursor:pointer}
.dropdown-menu{display:none;position:absolute;right:0;top:40px;background:#030814;border:1px solid var(--border-color);border-radius:10px;width:170px;box-shadow:0 10px 30px #000;z-index:999;overflow:hidden}.dropdown-menu.active{display:block}.dropdown-menu a{display:block;padding:12px 16px;color:var(--text-muted);text-decoration:none;font-size:12px;font-family:'JetBrains Mono';border-bottom:1px solid rgba(255,255,255,.05)}
.main-container{width:100%;display:flex;flex-direction:column;align-items:center;padding:16px 16px 60px;position:relative;z-index:10;gap:16px}.content-card,.gateway-card{background:var(--panel-bg);border:1px solid var(--border-color);border-radius:14px;padding:16px 14px;width:100%;max-width:420px;box-shadow:0 20px 50px rgba(0,0,0,.9);backdrop-filter:blur(14px)}
h1{font-size:17px;color:#fff;margin-bottom:10px;text-align:center;font-weight:700}input{width:100%;padding:10px;background:#02060c;border:1px solid rgba(0,255,102,.25);border-radius:8px;color:#fff;margin-bottom:10px;font-size:13px;font-family:'JetBrains Mono'}
.btn{width:100%;padding:12px;border:none;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer;text-align:center;display:inline-block;text-decoration:none;font-family:'JetBrains Mono'}.btn-primary{background:var(--neon-green);color:#000205;font-weight:800;box-shadow:0 0 15px var(--neon-green-glow)}
.flash{padding:8px;border-radius:6px;margin-bottom:10px;font-size:11px;text-align:center;background:rgba(0,255,102,.15);border:1px solid var(--neon-green);color:var(--neon-green)}
.market-ticker{height:32px;background:rgba(2,6,12,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border-color);display:flex;align-items:center;padding:0 14px;font-family:'JetBrains Mono';font-size:10px;gap:16px;overflow-x:auto;color:var(--text-muted);white-space:nowrap;position:sticky;top:56px;z-index:99}.ticker-item span{color:var(--neon-green);font-weight:700;margin-left:4px}
.signals-card{background:var(--panel-bg);border:1.5px solid var(--neon-green);border-radius:14px;padding:16px;width:100%;max-width:420px;box-shadow:0 0 35px rgba(0,255,102,.25);backdrop-filter:blur(14px);font-family:'JetBrains Mono'}.signals-header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(0,255,102,.2);padding-bottom:8px;margin-bottom:12px}.signals-title{font-size:13px;font-weight:800;color:var(--neon-green);text-shadow:0 0 8px var(--neon-green-glow)}.signal-item{background:rgba(0,255,102,.04);border:1px solid rgba(0,255,102,.2);border-radius:10px;padding:12px;margin-bottom:10px;display:flex;flex-direction:column;gap:6px}.signal-row{display:flex;justify-content:space-between;align-items:center;font-size:11px}.signal-pair{font-weight:700;color:#fff;font-size:12px}.signal-badge{font-size:9px;padding:2px 6px;border-radius:4px;font-weight:800;background:rgba(0,255,102,.2);color:var(--neon-green);border:1px solid var(--neon-green)}.signal-details{font-size:10px;color:var(--text-muted);display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:4px}
.plan-row{background:rgba(2,6,12,.8);border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between;font-family:'JetBrains Mono'}.plan-info{display:flex;flex-direction:column;gap:3px}.plan-title{font-size:12px;color:#fff;font-weight:700}.plan-price{font-size:14px;color:var(--neon-green);font-weight:800}.plan-badge{font-size:8px;background:var(--neon-green);color:#000;padding:2px 5px;border-radius:4px;font-weight:800;width:max-content}.select-btn{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.15);color:#fff;padding:8px 16px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer;font-family:'JetBrains Mono';text-decoration:none;text-align:center}.select-btn.highlight{background:var(--neon-green);border-color:var(--neon-green);color:#000205}
.payment-box{background:rgba(0,255,102,.04);border:1px solid rgba(0,255,102,.25);border-radius:10px;padding:14px;margin-top:12px}.payment-address{word-break:break-all;font-size:10px;color:var(--neon-green);background:#02060c;border-radius:7px;padding:10px;margin-top:8px;border:1px solid rgba(0,255,102,.2)}.copy-btn{margin-top:8px;background:rgba(255,255,255,.07);color:#fff;border:1px solid rgba(255,255,255,.15);padding:8px;border-radius:7px;width:100%;font-family:'JetBrains Mono'}.status{text-align:center;padding:10px;border-radius:8px;margin-top:10px;background:rgba(255,255,255,.04);color:var(--text-muted);font-family:'JetBrains Mono';font-size:10px}
</style>
"""

BACKGROUND = """
<canvas id="candlestick-canvas"></canvas><div class="cinematic-overlay"></div>
<script>
const canvas=document.getElementById('candlestick-canvas'),ctx=canvas.getContext('2d');function resize(){canvas.width=innerWidth;canvas.height=innerHeight}addEventListener('resize',resize);resize();
const columns=[],columnWidth=45,gap=20;function candle(){return{height:Math.random()*70+30,wickTop:Math.random()*30+10,wickBottom:Math.random()*30+10,isGreen:Math.random()>.48,alpha:Math.random()*.4+.6}}function init(){columns.length=0;const n=Math.ceil(canvas.width/(columnWidth+gap))+2;for(let i=0;i<n;i++)columns.push({x:i*(columnWidth+gap),candles:Array.from({length:14},candle)})}init();let off=0,wave=0;function animate(){ctx.clearRect(0,0,canvas.width,canvas.height);wave+=.03;off+=.8;if(off>110){off=0;columns.forEach(c=>{c.candles.pop();c.candles.unshift(candle())})}columns.forEach((col,i)=>{ctx.save();ctx.translate(0,off+Math.sin(wave+i*.4)*22);col.candles.forEach((c,j)=>{const y=j*110-70;ctx.shadowBlur=18;ctx.shadowColor=c.isGreen?'#00ff66':'#ff3344';const color=c.isGreen?'rgba(0,255,102,':'rgba(255,51,68,';ctx.strokeStyle=color+(c.alpha*.9)+')';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(col.x+columnWidth/2,y-c.wickTop);ctx.lineTo(col.x+columnWidth/2,y+c.height+c.wickBottom);ctx.stroke();ctx.fillStyle=color+(c.alpha*.75)+')';ctx.fillRect(col.x,y,columnWidth,c.height)});ctx.restore()});requestAnimationFrame(animate)}animate();
function toggleMenu(){const m=document.getElementById('dropdownMenu');if(m)m.classList.toggle('active')}addEventListener('click',e=>{if(!e.target.closest('.menu-container')){const m=document.getElementById('dropdownMenu');if(m)m.classList.remove('active')}})
</script>
"""

HEADER = """
<div class="header"><div class="brand"><div class="pro-logo">V</div><div class="brand-name">VELOX LABS AI</div></div><div class="menu-container"><div class="menu-btn" onclick="toggleMenu()">⋮</div><div id="dropdownMenu" class="dropdown-menu"><a href="/">Home</a>{% if user_is_auth %}<a href="/logout">Logout</a>{% else %}<a href="/login">Login</a><a href="/register">Register</a>{% endif %}</div></div></div>
"""

INDEX = BASE_STYLE + BACKGROUND + """
</head><body>{{header_rendered|safe}}
<div class="market-ticker"><div>AI ENGINE: <span style="color:var(--neon-green)">ACTIVE 24/7 SCANNER</span></div><div class="ticker-item">BTC/USD: <span>LIVE</span></div><div class="ticker-item">ETH/USD: <span>LIVE</span></div><div class="ticker-item">EUR/USD: <span>LIVE ANALYSIS</span></div><div class="ticker-item">XAU/USD: <span>MARKET ANALYSIS</span></div></div>
<div class="main-container">
{% with messages=get_flashed_messages(with_categories=true) %}{% for category,message in messages %}<div style="width:100%;max-width:420px" class="flash">{{message}}</div>{% endfor %}{% endwith %}
{% if user_has_sub %}
<div class="signals-card"><div class="signals-header"><span class="signals-title">⚡ 24/7 AUTO-ANALYZED SIGNALS</span><span style="font-size:9px;color:var(--neon-green)">● QUANT LIVE</span></div>
{% for sig in signals %}<div class="signal-item"><div class="signal-row"><span class="signal-pair">{{sig.pair}}</span><span class="signal-badge">{{sig.badge}}</span></div><div class="signal-details"><div>Entry: {{sig.entry}}</div><div>Target: {{sig.target}}</div><div>Stop Loss: {{sig.stop_loss}}</div><div>{{sig.accuracy}}</div></div></div>{% endfor %}
</div>
{% else %}
<div class="signals-card" style="text-align:center;border-color:var(--neon-red);box-shadow:0 0 30px rgba(255,51,68,.2)"><div class="signals-header" style="border-color:rgba(255,51,68,.3)"><span class="signals-title" style="color:var(--neon-red)">🔒 SIGNALS LOCKED</span><span style="font-size:9px;color:var(--neon-red)">SUBSCRIPTION REQUIRED</span></div><p style="font-size:11px;color:var(--text-muted);margin-bottom:12px">Active subscription required to view the automated trading terminal.</p><a href="#gateway-section" class="btn btn-primary" style="background:var(--neon-red);color:#fff">Choose Plan & Unlock</a></div>
{% endif %}
<div id="gateway-section" class="gateway-card"><div style="text-align:center;margin-bottom:12px"><h3 style="color:var(--neon-green);font-family:'JetBrains Mono';font-size:13px">ACCESS GATEWAY</h3><p style="font-size:9px;color:var(--text-muted);font-family:'JetBrains Mono'">Select a plan. Pay securely with USDT TRC-20.</p></div>
{% for name,plan in plans.items() %}<div class="plan-row" {% if name=="3 Month" %}style="border:1.5px solid var(--neon-green)"{% endif %}><div class="plan-info">{% if name=="3 Month" %}<span class="plan-badge">POPULAR</span>{% endif %}<span class="plan-title">{{name}} Access</span><span class="plan-price">${{plan.price}}</span></div><a href="{{url_for('checkout',plan_name=name)}}" class="select-btn {% if name=='3 Month' %}highlight{% endif %}">Select & Pay</a></div>{% endfor %}
</div>
{% if not current_user.is_authenticated %}<div style="width:100%;max-width:420px"><a href="/register" class="btn btn-primary" style="display:block;text-align:center;font-size:15px;padding:14px">Start Here ▼</a><div style="text-align:center;margin-top:8px;font-size:11px"><a href="/login" style="color:var(--text-muted);text-decoration:none">Already registered? <span style="color:var(--neon-green)">Login</span></a></div></div>{% endif %}
</div></body></html>
"""

LOGIN = BASE_STYLE + BACKGROUND + """
</head><body>{{header_rendered|safe}}<div class="main-container" style="min-height:calc(100vh - 56px);justify-content:center"><div class="content-card"><h1>Member Login</h1><form method="post"><input type="email" name="email" placeholder="Your Email" required><input type="password" name="password" placeholder="Password" required><button type="submit" class="btn btn-primary">Authenticate</button></form></div></div></body></html>
"""

REGISTER = BASE_STYLE + BACKGROUND + """
</head><body>{{header_rendered|safe}}<div class="main-container" style="min-height:calc(100vh - 56px);justify-content:center"><div class="content-card"><h1>Create Account</h1><p style="text-align:center;font-size:10px;margin-bottom:10px;color:var(--text-muted)">Register to unlock tier: <strong style="color:var(--neon-green)">{{selected_plan}}</strong></p><form method="post"><input type="email" name="email" placeholder="Your Email" required><input type="password" name="password" placeholder="Create Password" minlength="6" required><button type="submit" class="btn btn-primary">Create Account</button></form></div></div></body></html>
"""

CHECKOUT = BASE_STYLE + BACKGROUND + """
</head><body>{{header_rendered|safe}}<div class="main-container" style="min-height:calc(100vh - 56px);justify-content:center"><div class="content-card" style="border-color:var(--neon-green)"><h1 style="color:var(--neon-green)">SECURE CHECKOUT</h1>
<div style="background:rgba(0,255,102,.05);border:1px solid rgba(0,255,102,.2);border-radius:8px;padding:12px;margin-bottom:14px;font-family:'JetBrains Mono';font-size:11px"><div style="display:flex;justify-content:space-between"><span style="color:var(--text-muted)">Selected Plan:</span><span style="color:#fff;font-weight:700">{{plan_name}}</span></div><div style="display:flex;justify-content:space-between;margin-top:6px"><span style="color:var(--text-muted)">Amount:</span><span style="color:var(--neon-green);font-weight:800">${{plan.price}}</span></div></div>
{% if payment %}
<div class="payment-box"><div style="text-align:center;color:var(--neon-green);font-weight:800;font-size:13px">SEND USDT</div><div style="text-align:center;color:var(--text-muted);font-size:10px;margin-top:5px">Network: <strong style="color:#fff">TRON / TRC-20</strong></div><div style="margin-top:12px;font-size:10px;color:var(--text-muted)">Amount to send:</div><div style="color:var(--neon-green);font-size:18px;font-weight:800;margin-top:3px">{{payment.pay_amount}} USDT</div><div style="margin-top:10px;font-size:10px;color:var(--text-muted)">Payment address:</div><div id="paymentAddress" class="payment-address">{{payment.pay_address}}</div><button class="copy-btn" onclick="copyAddress()">COPY ADDRESS</button><div class="status">Payment status: <strong id="paymentStatus" style="color:var(--neon-green)">{{payment.payment_status}}</strong></div></div>
<script>
function copyAddress(){navigator.clipboard.writeText(document.getElementById("paymentAddress").innerText.trim());alert("Address copied")}
async function checkPayment(){try{const r=await fetch("{{url_for('payment_status',order_id=payment.order_id)}}");const d=await r.json();document.getElementById("paymentStatus").innerText=d.status;if(d.status==="finished")setTimeout(()=>location.href="/",1200)}catch(e){}}
setInterval(checkPayment,10000);
</script>
{% else %}
<form method="post"><button type="submit" class="btn btn-primary" style="font-size:14px;padding:14px">CREATE USDT PAYMENT</button></form>
{% endif %}
</div></div></body></html>
"""

def render_page(template, **kwargs):
    header_rendered = render_template_string(HEADER, user_is_auth=current_user.is_authenticated)
    user_has_sub = current_user.has_active_subscription() if current_user.is_authenticated else False
    # Do not touch the signal SQLite table for public pages. This prevents a legacy
    # signal-table problem from taking down the entire landing page/login flow.
    signals = safe_signal_query(20) if user_has_sub else []
    return render_template_string(template, header_rendered=header_rendered,
        user_is_auth=current_user.is_authenticated, current_user=current_user,
        user_has_sub=user_has_sub, selected_plan=session.get("selected_plan", "1 Month"),
        signals=signals, plans=PLANS, **kwargs)

def create_nowpayments_payment(plan_name, user):
    if not NOWPAYMENTS_API_KEY:
        raise RuntimeError("NOWPAYMENTS_API_KEY is not configured.")
    plan = PLANS[plan_name]
    payload = {
        "price_amount": plan["price"], "price_currency": "usd",
        "pay_currency": "usdttrc20",
        "ipn_callback_url": f"{SITE_URL}/payment/ipn",
        "order_id": f"VELOX-{user.id}-{uuid.uuid4().hex[:16]}",
        "order_description": f"Velox Trading Labs - {plan_name}"
    }
    req = urllib.request.Request(NOWPAYMENTS_API_URL,
        data=json.dumps(payload).encode(),
        headers={"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except Exception: pass
        raise RuntimeError(f"NOWPayments error {e.code}: {body}")

def verify_ipn(data, signature):
    if not signature or not NOWPAYMENTS_IPN_SECRET:
        return False
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    expected = hmac.new(NOWPAYMENTS_IPN_SECRET.encode(), payload.encode(), hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)

def activate_subscription(payment):
    if payment.completed_at is not None:
        return
    user = User.query.get(payment.user_id)
    plan = PLANS.get(payment.plan_name)
    if not user or not plan:
        return
    now = datetime.utcnow()
    start = user.subscription_expires if user.subscription_expires and user.subscription_expires > now else now
    user.subscription_expires = start + timedelta(days=plan["days"])
    user.active_plan = payment.plan_name
    payment.payment_status = "finished"
    payment.completed_at = now
    db.session.commit()

@app.route("/")
def index():
    return render_page(INDEX)

@app.route("/checkout/<plan_name>", methods=["GET"])
def checkout(plan_name):
    if plan_name not in PLANS: abort(404)
    session["selected_plan"] = plan_name
    if not current_user.is_authenticated:
        return redirect(url_for("register"))
    payment = Payment.query.filter_by(user_id=current_user.id, plan_name=plan_name).filter(
        Payment.payment_status.in_(["created","waiting","confirming","confirmed"])
    ).order_by(Payment.id.desc()).first()
    return render_page(CHECKOUT, plan_name=plan_name, plan=PLANS[plan_name], payment=payment)

@app.route("/checkout/<plan_name>", methods=["POST"])
@login_required
def process_checkout(plan_name):
    if plan_name not in PLANS: abort(404)
    try:
        existing = Payment.query.filter_by(user_id=current_user.id, plan_name=plan_name).filter(
            Payment.payment_status.in_(["created","waiting","confirming","confirmed"])
        ).order_by(Payment.id.desc()).first()
        if existing:
            return render_page(CHECKOUT, plan_name=plan_name, plan=PLANS[plan_name], payment=existing)
        result = create_nowpayments_payment(plan_name, current_user)
        payment_id = str(result.get("payment_id", ""))
        if not payment_id: raise RuntimeError("NOWPayments did not return a payment ID.")
        payment = Payment(
            order_id=result.get("order_id") or f"VELOX-{current_user.id}-{uuid.uuid4().hex[:16]}",
            payment_id=payment_id, user_id=current_user.id, plan_name=plan_name,
            amount_usd=float(PLANS[plan_name]["price"]),
            pay_currency=result.get("pay_currency", "usdttrc20"),
            pay_amount=str(result.get("pay_amount", "")),
            pay_address=result.get("pay_address", ""),
            payment_status=result.get("payment_status", "waiting")
        )
        db.session.add(payment)
        db.session.commit()
        return render_page(CHECKOUT, plan_name=plan_name, plan=PLANS[plan_name], payment=payment)
    except Exception as e:
        print(f"Payment creation error: {e}")
        flash("Unable to create payment. Please try again.", "danger")
        return redirect(url_for("checkout", plan_name=plan_name))

@app.route("/payment/status/<order_id>")
@login_required
def payment_status(order_id):
    payment = Payment.query.filter_by(order_id=order_id).first()
    if not payment: return {"status":"not_found"}, 404
    if payment.user_id != current_user.id: return {"status":"forbidden"}, 403
    return {"status": payment.payment_status}

@app.route("/payment/ipn", methods=["POST"])
def payment_ipn():
    data = request.get_json(silent=True)
    if not data: return "Invalid JSON", 400
    if not verify_ipn(data, request.headers.get("x-nowpayments-sig")):
        return "Invalid signature", 401
    payment_id = str(data.get("payment_id", ""))
    order_id = str(data.get("order_id", ""))
    payment = Payment.query.filter_by(payment_id=payment_id).first() if payment_id else None
    if not payment and order_id: payment = Payment.query.filter_by(order_id=order_id).first()
    if not payment: return "Unknown payment", 404
    previous_status = payment.payment_status
    new_status = str(data.get("payment_status", "")).lower()
    payment.payment_status = new_status
    payment.actually_paid = str(data.get("actually_paid", ""))
    payment.tx_hash = data.get("payin_hash") or data.get("tx_hash") or data.get("hash")
    if new_status == "finished" and previous_status != "finished":
        activate_subscription(payment)
    else:
        db.session.commit()
    return "OK", 200

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if not email or not password:
            flash("Email and password are required.","danger"); return redirect(url_for("register"))
        if len(password) < 6:
            flash("Password must be at least 6 characters.","danger"); return redirect(url_for("register"))
        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists. Please login.","danger"); return redirect(url_for("login"))
        plan = session.get("selected_plan","1 Month")
        if plan not in PLANS: plan = "1 Month"
        user = User(email=email, active_plan="None")
        user.set_password(password); db.session.add(user); db.session.commit(); login_user(user)
        return redirect(url_for("checkout", plan_name=plan))
    return render_page(REGISTER)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user); return redirect(url_for("index"))
        flash("Invalid email or password.","danger")
    return render_page(LOGIN)

@app.route("/logout")
@login_required
def logout():
    logout_user(); return redirect(url_for("index"))

@app.route("/health")
def health():
    return {"status":"ok","service":"Velox Trading Labs"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)))
