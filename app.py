from flask import Flask, render_template_string, request, flash, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
import threading
import time
import urllib.request
import json

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config['SECRET_KEY'] = 'super_secret_key_change_this_in_production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'velox_portal.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    subscription_expires = db.Column(db.DateTime, nullable=True)
    active_plan = db.Column(db.String(20), default="None")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_active_subscription(self):
        if self.is_admin: 
            return True
        if self.subscription_expires is None: 
            return False
        return self.subscription_expires > datetime.utcnow()

class Signal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pair = db.Column(db.String(50), nullable=False)
    badge = db.Column(db.String(30), default="STRONG BUY")
    entry = db.Column(db.String(50), nullable=False)
    target = db.Column(db.String(50), nullable=False)
    stop_loss = db.Column(db.String(50), nullable=False)
    accuracy = db.Column(db.String(20), default="94.2%")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def fetch_live_price(symbol):
    try:
        if "BTC" in symbol:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                return float(data['price'])
        elif "ETH" in symbol:
            url = "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                return float(data['price'])
        elif "GOLD" in symbol or "XAU" in symbol:
            return 2385.50
        elif "EUR" in symbol:
            return 1.0845
    except Exception:
        pass
    return None

def run_market_analyzer():
    with app.app_context():
        if Signal.query.count() == 0:
            db.session.add(Signal(pair="BTC/USD (Crypto)", badge="STRONG BUY", entry="$67,420.00", target="$69,800.00", stop_loss="$65,900.00", accuracy="95.4%"))
            db.session.add(Signal(pair="ETH/USD (Crypto)", badge="BUY SETUP", entry="$2,610.00", target="$2,750.00", stop_loss="$2,540.00", accuracy="92.8%"))
            db.session.add(Signal(pair="EUR/USD (Forex)", badge="BULLISH MOMENTUM", entry="1.0845", target="1.0920", stop_loss="1.0810", accuracy="93.1%"))
            db.session.add(Signal(pair="XAU/USD (Gold)", badge="BREAKOUT BUY", entry="$2,385.50", target="$2,425.00", stop_loss="$2,365.00", accuracy="96.2%"))
            db.session.commit()

    while True:
        try:
            time.sleep(300)
            with app.app_context():
                btc_price = fetch_live_price("BTC")
                if btc_price:
                    Signal.query.filter(Signal.pair.like("%BTC/USD%")).delete()
                    target_price = round(btc_price * 1.035, 2)
                    stop_loss_price = round(btc_price * 0.982, 2)
                    new_sig = Signal(
                        pair="BTC/USD (Crypto)",
                        badge="AI QUANT BUY",
                        entry=f"${btc_price:,.2f}",
                        target=f"${target_price:,.2f}",
                        stop_loss=f"${stop_loss_price:,.2f}",
                        accuracy="95.8%"
                    )
                    db.session.add(new_sig)
                    db.session.commit()
        except Exception as e:
            print(f"Analyzer loop error: {e}")

analyzer_thread = threading.Thread(target=run_market_analyzer, daemon=True)
analyzer_thread.start()

with app.app_context():
    db.create_all()
    admin_email = "admin@velox.com"
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(email=admin_email, is_admin=True, active_plan="Lifetime Admin")
        admin.set_password("adminpassword")
        db.session.add(admin)
        db.session.commit()

BASE_STYLE = '''
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --bg-deep: #000205; --panel-bg: rgba(2, 6, 12, 0.88); --border-color: rgba(0, 255, 102, 0.35); --neon-green: #00ff66; --neon-green-glow: rgba(0, 255, 102, 0.7); --neon-red: #ff3344; --text-main: #ffffff; --text-muted: #8b949e; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; -webkit-tap-highlight-color: transparent; }
        html { background-color: var(--bg-deep); color: var(--text-main); width: 100%; min-height: 100vh; overflow-x: hidden; }
        body { background-color: var(--bg-deep); color: var(--text-main); width: 100%; min-height: 100vh; overflow-y: auto; overflow-x: hidden; position: relative; }
        #candlestick-canvas { position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 0; pointer-events: none; background: #000205; }
        .cinematic-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: radial-gradient(circle at center, rgba(0, 2, 5, 0.3) 0%, rgba(0, 2, 5, 0.75) 100%); z-index: 1; pointer-events: none; }
        .header { height: 56px; background: rgba(2, 6, 12, 0.95); backdrop-filter: blur(16px); border-bottom: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; padding: 0 16px; position: sticky; top: 0; z-index: 100; flex-shrink: 0; }
        .brand { display: flex; align-items: center; gap: 10px; }
        .pro-logo { width: 32px; height: 32px; background: linear-gradient(135deg, #07101e 0%, #010408 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; border: 1.5px solid var(--neon-green); box-shadow: 0 0 12px var(--neon-green-glow); font-family: 'JetBrains Mono', monospace; font-weight: 800; font-size: 14px; color: var(--neon-green); }
        .brand-name { font-size: 11px; font-weight: 800; color: var(--neon-green); text-shadow: 0 0 8px var(--neon-green-glow); letter-spacing: 0.5px; }
        .menu-container { position: relative; }
        .menu-btn { background: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: #fff; width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 16px; cursor: pointer; }
        .dropdown-menu { display: none; position: absolute; right: 0; top: 40px; background: #030814; border: 1px solid var(--border-color); border-radius: 10px; width: 150px; box-shadow: 0 10px 30px rgba(0,0,0,0.9); z-index: 999; overflow: hidden; }
        .dropdown-menu.active { display: block; }
        .dropdown-menu a { display: block; padding: 12px 16px; color: var(--text-muted); text-decoration: none; font-size: 12px; font-family: 'JetBrains Mono', monospace; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .dropdown-menu a:hover { color: var(--neon-green); background: rgba(0,255,102,0.05); }
        .main-container { width: 100%; display: flex; flex-direction: column; align-items: center; justify-content: flex-start; padding: 16px; padding-bottom: 60px; position: relative; z-index: 10; gap: 16px; }
        .content-card { background: var(--panel-bg); border: 1px solid var(--border-color); border-radius: 14px; padding: 16px 14px; width: 100%; max-width: 420px; box-shadow: 0 20px 50px rgba(0,0,0,0.9); backdrop-filter: blur(14px); }
        h1 { font-size: 17px; color: #fff; margin-bottom: 10px; text-align: center; font-weight: 700; }
        input, select { width: 100%; padding: 10px; background: #02060c; border: 1px solid rgba(0, 255, 102, 0.25); border-radius: 8px; color: #fff; margin-bottom: 10px; font-size: 13px; font-family: 'JetBrains Mono', monospace; }
        input:focus, select:focus { outline: none; border-color: var(--neon-green); box-shadow: 0 0 10px rgba(0, 255, 102, 0.3); }
        .btn { width: 100%; padding: 12px; border: none; border-radius: 8px; font-weight: 700; font-size: 13px; cursor: pointer; transition: all 0.2s; text-align: center; display: inline-block; text-decoration: none; font-family: 'JetBrains Mono', monospace; }
        .btn-primary { background: var(--neon-green); color: #000205; font-weight: 800; box-shadow: 0 0 15px var(--neon-green-glow); }
        .flash { padding: 8px; border-radius: 6px; margin-bottom: 10px; font-size: 11px; text-align: center; background: rgba(0, 255, 102, 0.15); border: 1px solid var(--neon-green); color: var(--neon-green); }
    </style>
'''

BACKGROUND_CANVAS_HTML = '''
    <canvas id="candlestick-canvas"></canvas>
    <div class="cinematic-overlay"></div>
    <script>
        const canvas = document.getElementById('candlestick-canvas');
        const ctx = canvas.getContext('2d');
        function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
        window.addEventListener('resize', resize);
        resize();
        const columns = [];
        const columnWidth = 45, gap = 20;
        function initColumns() {
            columns.length = 0;
            const totalCols = Math.ceil(canvas.width / (columnWidth + gap)) + 2;
            for (let i = 0; i < totalCols; i++) {
                columns.push({ x: i * (columnWidth + gap), candles: Array.from({length: 14}, () => createCandle()) });
            }
        }
        function createCandle() {
            return { height: Math.random() * 70 + 30, wickTop: Math.random() * 30 + 10, wickBottom: Math.random() * 30 + 10, isGreen: Math.random() > 0.48, alpha: Math.random() * 0.4 + 0.6 };
        }
        initColumns();
        let baseOffset = 0, waveTimer = 0;
        function animate() {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            waveTimer += 0.03; baseOffset += 0.8; 
            if (baseOffset > 110) {
                baseOffset = 0;
                columns.forEach(col => { col.candles.pop(); col.candles.unshift(createCandle()); });
            }
            columns.forEach((col, colIdx) => {
                ctx.save();
                const waveMod = Math.sin(waveTimer + colIdx * 0.4) * 22;
                ctx.translate(0, baseOffset + waveMod);
                col.candles.forEach((candle, index) => {
                    const y = index * 110 - 70;
                    ctx.shadowBlur = 18;
                    ctx.shadowColor = candle.isGreen ? '#00ff66' : '#ff3344';
                    const colorStr = candle.isGreen ? 'rgba(0, 255, 102, ' : 'rgba(255, 51, 68, ';
                    ctx.strokeStyle = colorStr + (candle.alpha * 0.9) + ')';
                    ctx.lineWidth = 3;
                    ctx.beginPath();
                    ctx.moveTo(col.x + columnWidth / 2, y - candle.wickTop);
                    ctx.lineTo(col.x + columnWidth / 2, y + candle.height + candle.wickBottom);
                    ctx.stroke();
                    ctx.fillStyle = colorStr + (candle.alpha * 0.75) + ')';
                    ctx.fillRect(col.x, y, columnWidth, candle.height);
                });
                ctx.restore();
            });
            requestAnimationFrame(animate);
        }
        animate();
        function toggleMenu() { document.getElementById('dropdownMenu').classList.toggle('active'); }
        window.addEventListener('click', function(e) {
            if (!e.target.closest('.menu-container')) {
                const menu = document.getElementById('dropdownMenu');
                if(menu) menu.classList.remove('active');
            }
        });
    </script>
'''

HEADER_TEMPLATE = '''
    <div class="header">
        <div class="brand">
            <div class="pro-logo">V</div>
            <div class="brand-name">VELOX LABS AI</div>
        </div>
        <div class="menu-container">
            <div class="menu-btn" onclick="toggleMenu()">⋮</div>
            <div id="dropdownMenu" class="dropdown-menu">
                <a href="/">Home</a>
                {% if user_is_auth %}
                    <a href="/logout">Logout</a>
                {% else %}
                    <a href="/login">Login</a>
                    <a href="/register">Register</a>
                {% endif %}
            </div>
        </div>
    </div>
'''

INDEX_HTML = BASE_STYLE + BACKGROUND_CANVAS_HTML + '''
    <style>
        .market-ticker { height: 32px; background: rgba(2, 6, 12, 0.92); backdrop-filter: blur(12px); border-bottom: 1px solid var(--border-color); display: flex; align-items: center; padding: 0 14px; font-family: 'JetBrains Mono', monospace; font-size: 10px; gap: 16px; overflow-x: auto; color: var(--text-muted); white-space: nowrap; position: sticky; top: 56px; z-index: 99; }
        .ticker-item span { color: var(--neon-green); font-weight: 700; margin-left: 4px; text-shadow: 0 0 6px var(--neon-green-glow); }
        .signals-card { background: var(--panel-bg); border: 1.5px solid var(--neon-green); border-radius: 14px; padding: 16px; width: 100%; max-width: 420px; box-shadow: 0 0 35px rgba(0,255,102,0.25); backdrop-filter: blur(14px); font-family: 'JetBrains Mono', monospace; }
        .signals-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(0,255,102,0.2); padding-bottom: 8px; margin-bottom: 12px; }
        .signals-title { font-size: 13px; font-weight: 800; color: var(--neon-green); text-shadow: 0 0 8px var(--neon-green-glow); }
        .signal-item { background: rgba(0,255,102,0.04); border: 1px solid rgba(0,255,102,0.2); border-radius: 10px; padding: 12px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
        .signal-row { display: flex; justify-content: space-between; align-items: center; font-size: 11px; }
        .signal-pair { font-weight: 700; color: #fff; font-size: 12px; }
        .signal-badge { font-size: 9px; padding: 2px 6px; border-radius: 4px; font-weight: 800; background: rgba(0,255,102,0.2); color: var(--neon-green); border: 1px solid var(--neon-green); }
        .signal-details { font-size: 10px; color: var(--text-muted); display: grid; grid-template-columns: 1fr 1fr; gap: 4px; margin-top: 4px; }
        .gateway-card { background: rgba(3, 8, 16, 0.94); border: 1px solid var(--border-color); border-radius: 14px; padding: 16px 14px; width: 100%; max-width: 420px; box-shadow: 0 0 30px rgba(0,255,102,0.15); backdrop-filter: blur(14px); }
        .plan-row { background: rgba(2, 6, 12, 0.8); border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 12px; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; font-family: 'JetBrains Mono', monospace; }
        .plan-info { display: flex; flex-direction: column; gap: 3px; }
        .plan-title { font-size: 12px; color: #fff; font-weight: 700; }
        .plan-price { font-size: 14px; color: var(--neon-green); font-weight: 800; }
        .plan-badge { font-size: 8px; background: var(--neon-green); color: #000; padding: 2px 5px; border-radius: 4px; font-weight: 800; display: inline-block; width: max-content; }
        .select-btn { background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); color: #fff; padding: 8px 16px; border-radius: 6px; font-size: 12px; font-weight: 700; cursor: pointer; font-family: 'JetBrains Mono', monospace; text-decoration: none; text-align: center; }
        .select-btn.highlight { background: var(--neon-green); border-color: var(--neon-green); color: #000205; }
    </style>
    </head>
    <body>
        {{ header_rendered|safe }}
        <div class="market-ticker">
            <div>AI ENGINE: <span style="color: var(--neon-green);">ACTIVE 24/7 SCANNER</span></div>
            <div class="ticker-item">BTC/USD: <span>$67,420.00 +2.34%</span></div>
            <div class="ticker-item">ETH/USD: <span>$2,610.00 +1.45%</span></div>
            <div class="ticker-item">EUR/USD: <span>1.0845</span></div>
            <div class="ticker-item">XAU/USD (GOLD): <span>$2,385.50 +1.12%</span></div>
        </div>
        <div class="main-container">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}{% for category, message in messages %}
                    <div style="width:100%; max-width:420px;" class="flash">{{ message }}</div>
                {% endfor %}{% endif %}
            {% endwith %}
            {% if user_has_sub %}
            <div class="signals-card">
                <div class="signals-header">
                    <span class="signals-title">⚡ 24/7 AUTO-ANALYZED SIGNALS</span>
                    <span style="font-size: 9px; color: var(--neon-green);">● AI QUANT LIVE</span>
                </div>
                {% for sig in signals %}
                <div class="signal-item">
                    <div class="signal-row">
                        <span class="signal-pair">{{ sig.pair }}</span>
                        <span class="signal-badge">{{ sig.badge }}</span>
                    </div>
                    <div class="signal-details">
                        <div>Entry: {{ sig.entry }}</div>
                        <div>Target (TP): {{ sig.target }}</div>
                        <div>Stop Loss: {{ sig.stop_loss }}</div>
                        <div>Accuracy: {{ sig.accuracy }}</div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="signals-card" style="text-align: center; border-color: var(--neon-red); box-shadow: 0 0 30px rgba(255,51,68,0.2);">
                <div class="signals-header" style="border-color: rgba(255,51,68,0.3);">
                    <span class="signals-title" style="color: var(--neon-red);">🔒 SIGNALS LOCKED</span>
                    <span style="font-size: 9px; color: var(--neon-red);">SUBSCRIPTION REQUIRED</span>
                </div>
                <p style="font-size: 11px; color: var(--text-muted); margin-bottom: 12px;">Active subscription required to view automated crypto & forex signals generated live by the quantitative engine.</p>
                <a href="#gateway-section" class="btn btn-primary" style="background: var(--neon-red); color: #fff; box-shadow: 0 0 15px rgba(255,51,68,0.5);">Choose Plan & Unlock</a>
            </div>
            {% endif %}
            <div id="gateway-section" class="gateway-card">
                <div style="text-align: center; margin-bottom: 12px;">
                    <h3 style="color: var(--neon-green); font-family: 'JetBrains Mono', monospace; font-size: 13px; text-shadow: 0 0 8px var(--neon-green-glow);">ACCESS GATEWAY</h3>
                    <p style="font-size: 9px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">Select a plan to proceed to secure payment.</p>
                </div>
                <div class="plan-row">
                    <div class="plan-info"><span class="plan-title">1 Month Access</span><span class="plan-price">$49</span></div>
                    <a href="/checkout/1 Month" class="select-btn">Select & Pay</a>
                </div>
                <div class="plan-row" style="border: 1.5px solid var(--neon-green); box-shadow: 0 0 15px rgba(0,255,102,0.25); background: rgba(0,255,102,0.03);">
                    <div class="plan-info"><span class="plan-badge">BEST VALUE</span><span class="plan-title">3 Month Access</span><span class="plan-price">$135</span></div>
                    <a href="/checkout/3 Month" class="select-btn highlight">Select & Pay</a>
                </div>
            </div>
            {% if not current_user.is_authenticated %}
            <div style="width: 100%; max-width: 420px;">
                <a href="/register" class="btn btn-primary" style="display: block; text-align: center; font-size: 15px; padding: 14px;">Start Here ▼</a>
                <div style="text-align: center; margin-top: 8px; font-size: 11px;">
                    <a href="/login" style="color: var(--text-muted); text-decoration: none;">Already registered? <span style="color: var(--neon-green);">Login</span></a>
                </div>
            </div>
            {% endif %}
        </div>
    </body>
    </html>
'''

LOGIN_HTML = BASE_STYLE + BACKGROUND_CANVAS_HTML + '''
    </head>
    <body>
        {{ header_rendered|safe }}
        <div class="main-container" style="min-height: calc(100vh - 56px); justify-content: center;">
            <div class="content-card">
                <h1>Member Login</h1>
                <form method="post">
                    <input type="email" name="email" placeholder="Your Email" required>
                    <input type="password" name="password" placeholder="Password" required>
                    <button type="submit" class="btn btn-primary">Authenticate</button>
                </form>
                <div style="text-align: center; margin-top: 8px; font-size: 11px;">
                    <a href="/" style="color: var(--text-muted); text-decoration: none;">Back to <span style="color: var(--neon-green);">Home</span></a>
                </div>
            </div>
        </div>
    </body>
    </html>
'''

REGISTER_HTML = BASE_STYLE + BACKGROUND_CANVAS_HTML + '''
    </head>
    <body>
        {{ header_rendered|safe }}
        <div class="main-container" style="min-height: calc(100vh - 56px); justify-content: center;">
            <div class="content-card">
                <h1>Create Account</h1>
                <p style="text-align: center; font-size: 10px; margin-bottom: 10px; color: var(--text-muted);">Register to unlock tier: <strong style="color: var(--neon-green);">{{ selected_plan }}</strong></p>
                <form method="post">
                    <input type="email" name="email" placeholder="Your Email" required>
                    <input type="password" name="password" placeholder="Create Password" required>
                    <button type="submit" class="btn btn-primary">Proceed to Payment</button>
                </form>
            </div>
        </div>
    </body>
    </html>
'''

CHECKOUT_HTML = BASE_STYLE + BACKGROUND_CANVAS_HTML + '''
    </head>
    <body>
        {{ header_rendered|safe }}
        <div class="main-container" style="min-height: calc(100vh - 56px); justify-content: center;">
            <div class="content-card" style="border-color: var(--neon-green);">
                <h1 style="color: var(--neon-green);">SECURE CHECKOUT</h1>
                <div style="background: rgba(0,255,102,0.05); border: 1px solid rgba(0,255,102,0.2); border-radius: 8px; padding: 12px; margin-bottom: 14px; font-family: 'JetBrains Mono', monospace; font-size: 11px;">
                    <div style="display:flex; justify-content:space-between; margin-bottom:6px;"><span style="color: var(--text-muted);">Selected Tier:</span><span style="color: #fff; font-weight:700;">{{ plan_name }}</span></div>
                    <div style="display:flex; justify-content:space-between;"><span style="color: var(--text-muted);">Amount Due:</span><span style="color: var(--neon-green); font-weight:800;">{% if plan_name == '3 Month' %}$135{% else %}$49{% endif %}</span></div>
                </div>
                <form method="post">
                    <button type="submit" class="btn btn-primary" style="font-size: 14px; padding: 14px;">Simulate Payment & Unlock</button>
                </form>
            </div>
        </div>
    </body>
    </html>
'''

def render_page(template_content, **kwargs):
    header_rendered = render_template_string(HEADER_TEMPLATE, user_is_auth=current_user.is_authenticated)
    signals = Signal.query.order_by(Signal.id.desc()).all()
    return render_template_string(
        template_content, 
        header_rendered=header_rendered,
        user_is_auth=current_user.is_authenticated,
        current_user=current_user,
        user_has_sub=current_user.has_active_subscription() if current_user.is_authenticated else False,
        selected_plan=session.get('selected_plan', '1 Month'),
        signals=signals,
        **kwargs
    )

@app.route('/')
def index():
    return render_page(INDEX_HTML)

@app.route('/checkout/<plan_name>')
def checkout(plan_name):
    session['selected_plan'] = plan_name
    if not current_user.is_authenticated:
        return redirect(url_for('register'))
    return render_page(CHECKOUT_HTML, plan_name=plan_name)

@app.route('/checkout/<plan_name>', methods=['POST'])
def process_checkout(plan_name):
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    days = 90 if plan_name == '3 Month' else 30
    current_user.subscription_expires = datetime.utcnow() + timedelta(days=days)
    current_user.active_plan = plan_name
    db.session.commit()
    flash('Payment successful! AI signals unlocked.', 'success')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        existing = User.query.filter_by(email=email).first()
        if existing:
            login_user(existing)
            return redirect(url_for('checkout', plan_name=session.get('selected_plan', '1 Month')))
        chosen_plan = session.get('selected_plan', '1 Month')
        user = User(email=email, active_plan=chosen_plan)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('checkout', plan_name=chosen_plan))
    return render_page(REGISTER_HTML)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index'))
        flash('Invalid email or password.', 'danger')
    return render_page(LOGIN_HTML)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
