from flask import Flask, request, redirect, url_for, flash, Response, render_template_string, session, g
import os, re, hmac, time, secrets, sqlite3, csv, io
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from datetime import date, datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
DB = Path(os.environ.get("DB_PATH", Path(__file__).with_name("chi_tieu.db")))

def load_secret_key():
    """Ưu tiên biến môi trường SECRET_KEY; nếu không có thì tự sinh khóa ngẫu nhiên và lưu cạnh file DB."""
    k = os.environ.get("SECRET_KEY")
    if k:
        return k
    f = DB.with_name(".secret_key")
    try:
        if f.exists():
            return f.read_text().strip()
        k = secrets.token_hex(32)
        f.write_text(k)
        try: os.chmod(f, 0o600)
        except OSError: pass
        return k
    except OSError:
        return secrets.token_hex(32)

app.secret_key = load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=64 * 1024,
    CSRF_ENABLED=True,
)
MAX_AMOUNT = 10**12  # giới hạn số tiền để tránh tràn số trong SQLite


@contextmanager
def db():
    """Mở kết nối, tự commit khi thành công, rollback khi lỗi và luôn đóng kết nối."""
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('income','expense')));
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('income','expense')), amount INTEGER NOT NULL CHECK(amount>0), tx_date TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), note TEXT DEFAULT '', user_id INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS budgets(id INTEGER PRIMARY KEY, month TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), limit_amount INTEGER NOT NULL CHECK(limit_amount>0), user_id INTEGER REFERENCES users(id), UNIQUE(user_id,month,category_id));
        """)
        # Nâng cấp từ DB cũ (chưa có user_id): dữ liệu cũ có user_id = NULL, sẽ gán cho tài khoản đầu tiên đăng ký.
        if "user_id" not in [r["name"] for r in c.execute("PRAGMA table_info(transactions)")]:
            c.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        if "user_id" not in [r["name"] for r in c.execute("PRAGMA table_info(budgets)")]:
            c.executescript("""
            ALTER TABLE budgets RENAME TO budgets_old;
            CREATE TABLE budgets(id INTEGER PRIMARY KEY, month TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), limit_amount INTEGER NOT NULL CHECK(limit_amount>0), user_id INTEGER REFERENCES users(id), UNIQUE(user_id,month,category_id));
            INSERT INTO budgets(id,month,category_id,limit_amount,user_id) SELECT id,month,category_id,limit_amount,NULL FROM budgets_old;
            DROP TABLE budgets_old;
            """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_tx_user_date ON transactions(user_id,tx_date)")
        if c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            c.executemany("INSERT INTO categories(name,kind) VALUES(?,?)", [
                ("Lương","income"),("Thưởng","income"),("Thu nhập khác","income"),
                ("Ăn uống","expense"),("Đi lại","expense"),("Mua sắm","expense"),
                ("Hóa đơn","expense"),("Giải trí","expense"),("Sức khỏe","expense"),
                ("Nhà ở","expense"),("Chi phí khác","expense")])

def parse_amount(v):
    """Nhận '15000000', '15.000.000' hoặc '15,000,000' -> số nguyên; sai định dạng sẽ ném ValueError."""
    return int(str(v or "").replace(".","").replace(",","").replace(" ","").strip())

def money(n): return f"{int(n or 0):,}".replace(",", ".") + " đ"
app.jinja_env.filters["money"] = money

VN_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
def vdate(v):
    m = VN_DATE_RE.match(str(v or ""))
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else v
def vmonth(v):
    v = str(v or "")
    return f"{v[5:7]}/{v[:4]}" if len(v) == 7 else v
CAT_COLORS = ["#0F7B5F", "#2F6FDE", "#E0673A", "#8B5CF6", "#C98200", "#0E9AA7", "#C2417C", "#6B7F3A"]
def cat_color(i):
    try: return CAT_COLORS[int(i) % len(CAT_COLORS)]
    except (TypeError, ValueError): return CAT_COLORS[0]
def shift_month(m, delta):
    y, mo = int(m[:4]), int(m[5:7]) - 1 + delta
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"
app.jinja_env.filters["vdate"] = vdate
app.jinja_env.filters["vmonth"] = vmonth
app.jinja_env.globals["cat_color"] = cat_color

BASE = r"""
<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0B4F3E">
<title>{{ title or 'Chi Tiêu Thông Minh' }}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%230F7B5F'/%3E%3Ctext x='16' y='23' font-size='20' text-anchor='middle' fill='white' font-family='Arial'%3E%E2%82%AB%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&display=swap" rel="stylesheet">
<style>
:root{--paper:#F2F4EF;--panel:#fff;--ink:#12261F;--muted:#5C6D65;--line:#DCE3DB;--soft:#E7EEE8;--jade:#0F7B5F;--jade-d:#0B4F3E;--mint:#8FE3BE;--coral:#FF9A7B;--in:#0F7B5F;--out:#C23B25;--warn:#B87400;--ring:rgba(15,123,95,.28)}
*{box-sizing:border-box}[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;min-height:100vh;display:flex;flex-direction:column}
h1,h2,.num,.brand,.mark{font-family:"Bricolage Grotesque","Be Vietnam Pro",system-ui,sans-serif}
.num,td,.tx-amt{font-variant-numeric:tabular-nums}
a{color:var(--jade)}
:focus-visible{outline:3px solid var(--ring);outline-offset:2px;border-radius:8px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.muted{color:var(--muted)}.small{font-size:13px}
/* thanh trên */
.top{position:sticky;top:0;z-index:20;background:rgba(242,244,239,.92);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding-top:env(safe-area-inset-top,0px)}
.top-in{max-width:1120px;margin:0 auto;padding:11px 20px;display:flex;align-items:center;gap:18px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--ink);text-decoration:none;margin-right:auto;letter-spacing:-.01em;white-space:nowrap}
.mark{width:34px;height:34px;border-radius:10px;background:var(--jade);color:#fff;display:grid;place-items:center;font-weight:700;font-size:18px;flex:none}
.links{display:flex;gap:4px}
.links a{padding:8px 15px;border-radius:999px;color:var(--muted);font-weight:500;text-decoration:none}
.links a:hover{color:var(--ink);background:var(--soft)}
.links a.on{background:var(--ink);color:#fff}
.who{display:flex;align-items:center;gap:10px}.who form{margin:0}.who-email{color:var(--muted);font-size:13px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* nút */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;background:var(--jade);color:#fff;border:1px solid var(--jade);border-radius:10px;padding:10px 16px;font:600 14px/1.2 "Be Vietnam Pro",system-ui,sans-serif;text-decoration:none;cursor:pointer;white-space:nowrap}
a.btn{color:#fff}.btn:hover{background:var(--jade-d);border-color:var(--jade-d)}
.btn.light,a.btn.light{background:#fff;color:var(--ink);border-color:#C9D3CA}.btn.light:hover{background:var(--soft);border-color:#B5C2B7}
.btn.danger,a.btn.danger{background:#fff;color:var(--out);border-color:#EBC3BA}.btn.danger:hover{background:#FCE9E4}
.btn.sm{padding:7px 12px;font-size:13px}.btn.wide{width:100%;padding:13px 16px;font-size:15px}
/* bố cục */
.wrap{max-width:1120px;width:100%;margin:0 auto;padding:28px 20px 44px;flex:1}
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:32px;line-height:1.12;margin:0 0 6px;font-weight:700;letter-spacing:-.02em}
h2{font-size:18px;margin:0;font-weight:700;letter-spacing:-.01em}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;margin-bottom:18px;min-width:0}
.panel-h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}.cols .panel{margin-bottom:0}.cols{margin-bottom:18px}
.link{font-weight:600;font-size:14px;text-decoration:none}.link:hover{text-decoration:underline}
/* chọn tháng */
.month{display:flex;align-items:center;background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px}
.month form{margin:0}.month input{border:0;background:transparent;padding:6px 8px;width:auto;font-weight:600;border-radius:8px}
.month a{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:var(--ink)}.month a:hover{background:var(--soft)}.month svg{width:18px;height:18px}
/* khối số dư (điểm nhấn của trang) */
.hero{background:var(--jade-d);color:#fff;border-radius:22px;padding:28px 30px 24px;margin-bottom:18px}
.hero-top{display:flex;justify-content:space-between;align-items:center;gap:12px;color:#BFE9D6;font-weight:500}
.pill{background:rgba(255,255,255,.12);border-radius:999px;padding:4px 12px;font-size:13px;color:#fff}
.big{font-size:clamp(40px,8vw,68px);line-height:1.05;font-weight:700;letter-spacing:-.03em;margin:10px 0 20px}.big.neg{color:var(--coral)}
.split{display:flex;height:14px;border-radius:999px;overflow:hidden;background:rgba(255,255,255,.1)}
.seg{display:block;flex-basis:0;flex-shrink:1;min-width:0;transform-origin:left;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}
.seg+.seg{margin-left:3px}.seg.in{background:var(--mint)}.seg.out{background:var(--coral)}.seg.none{background:transparent}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.hero-foot{display:grid;grid-template-columns:1fr 1fr 1.2fr;gap:18px;margin-top:18px;align-items:end}
.hero-foot .k{display:flex;align-items:center;gap:8px;color:#BFE9D6;font-size:13px}.hero-foot b{display:block;font-size:22px;font-weight:700;letter-spacing:-.01em}
.sw{width:10px;height:10px;border-radius:3px;display:inline-block}.sw.in{background:var(--mint)}.sw.out{background:var(--coral)}
.hero-foot .note{color:#BFE9D6;font-size:14px;text-align:right}
/* danh mục & ngân sách */
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex:none}
.cats{list-style:none;margin:0;padding:0}.cats li{margin:0 0 16px}.cats li:last-child{margin:0}
.cat-row{display:flex;align-items:center;gap:10px}.cat-name{flex:1;font-weight:500}.cat-share{color:var(--muted);font-size:13px;min-width:38px;text-align:right}.cat-row b{min-width:112px;text-align:right}
.bar{position:relative;height:9px;background:var(--soft);border-radius:999px;margin:8px 0}
.bar .fill{height:100%;border-radius:999px;background:var(--jade);min-width:4px}.bar .fill.warn{background:#E0A100}.bar .fill.over{background:#D9482B}
.bar.mark80::after{content:"";position:absolute;left:80%;top:-4px;bottom:-4px;width:2px;background:var(--ink);opacity:.35;border-radius:2px}
.bud{margin:0 0 20px}.bud:last-child{margin:0}
.bud-top{display:flex;align-items:center;gap:10px}.bud-top b{flex:1}
.bud-pct{font-weight:700;font-size:20px;letter-spacing:-.01em}.bud-pct.ok{color:var(--in)}.bud-pct.warn{color:var(--warn)}.bud-pct.over{color:var(--out)}
.bud-foot{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--muted)}
.msg.warn{color:var(--warn);font-weight:600}.msg.over{color:var(--out);font-weight:600}
.legend{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);margin-top:14px}.legend i{width:2px;height:12px;background:var(--ink);opacity:.35;display:inline-block}
/* danh sách giao dịch */
.txs{list-style:none;margin:0;padding:0}
.tx{display:grid;grid-template-columns:42px 1fr auto auto;gap:6px 14px;align-items:center;padding:13px 0;border-bottom:1px solid var(--line)}.tx:last-child{border-bottom:0;padding-bottom:0}.tx:first-child{padding-top:0}
.ico{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font:700 16px "Bricolage Grotesque",sans-serif;color:var(--c,#0F7B5F);background:color-mix(in srgb,var(--c,#0F7B5F) 14%,#fff)}
.tx-main{min-width:0}.tx-main b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.tx-meta{display:flex;gap:10px;color:var(--muted);font-size:13px}
.tx-amt{font:700 16px "Bricolage Grotesque",sans-serif;text-align:right;white-space:nowrap}.tx-amt.in{color:var(--in)}.tx-amt.out{color:var(--out)}
.actions{display:flex;gap:8px;align-items:center}.actions form{margin:0}
.sum{display:flex;gap:18px;flex-wrap:wrap;align-items:baseline;margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid var(--line)}.sum .in{color:var(--in);font-weight:600}.sum .out{color:var(--out);font-weight:600}
.empty{padding:34px 12px;text-align:center;color:var(--muted)}.empty .btn{margin-top:12px}
/* biểu mẫu */
label{font-size:13px;font-weight:600;display:block;margin-bottom:6px}
input,select,textarea{width:100%;padding:11px 12px;border:1px solid #C9D3CA;border-radius:10px;background:#fff;font:inherit;color:var(--ink)}
input:focus,select:focus,textarea:focus{outline:3px solid var(--ring);border-color:var(--jade)}
.filters{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;align-items:start}.f-actions{grid-column:1/-1;display:flex;gap:10px;flex-wrap:wrap}
.formcard{max-width:660px}.field{margin-bottom:20px}
.seg2{display:grid;grid-template-columns:1fr 1fr;background:var(--soft);border-radius:12px;padding:4px;gap:4px}
.seg2 label,.chips label{position:relative;margin:0;cursor:pointer}.seg2 input,.chips input{position:absolute;opacity:0;pointer-events:none}
.seg2 span{display:block;text-align:center;padding:10px;border-radius:9px;font-weight:600;color:var(--muted)}
.seg2 input[value=expense]:checked+span{background:var(--out);color:#fff}.seg2 input[value=income]:checked+span{background:var(--in);color:#fff}
.seg2 input:focus-visible+span,.chips input:focus-visible+span{outline:3px solid var(--ring);outline-offset:2px}
.amount-box{position:relative}.amount{font:700 34px/1.1 "Bricolage Grotesque",sans-serif;padding:14px 48px 14px 16px;letter-spacing:-.01em}.amount-box .unit{position:absolute;right:16px;top:50%;transform:translateY(-50%);font:600 20px "Bricolage Grotesque",sans-serif;color:var(--muted)}
.amt-fmt{margin-left:auto;color:var(--jade);font-weight:700;align-self:center}
.quick{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;min-height:30px}.quick button{border:1px solid #C9D3CA;background:#fff;border-radius:999px;padding:5px 12px;font:500 13px "Be Vietnam Pro",sans-serif;cursor:pointer;color:var(--ink)}.quick button:hover{background:var(--soft)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips span{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border:1px solid #C9D3CA;border-radius:999px;background:#fff;font-weight:500}
.chips input:checked+span{background:var(--ink);border-color:var(--ink);color:#fff}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:8px}
.budget-layout{display:grid;grid-template-columns:340px 1fr;gap:18px;align-items:start}.budget-layout .panel{margin-bottom:0}
.flash{display:flex;gap:10px;align-items:center;padding:12px 16px;border-radius:12px;background:#E1F3E9;border:1px solid #B9DFC9;margin-bottom:16px;font-weight:500}
.flash.error{background:#FCE9E4;border-color:#F0BFB2;color:#7A2112}
/* đăng nhập */
.auth{display:grid;grid-template-columns:1.05fr 1fr;max-width:920px;margin:24px auto;border-radius:22px;overflow:hidden;border:1px solid var(--line);background:#fff}
.auth-side{background:var(--jade-d);color:#fff;padding:38px 34px}.auth-side h1{font-size:30px;margin:18px 0 22px}.auth-side ul{margin:0;padding:0;list-style:none;color:#CDEFE0}.auth-side li{padding:10px 0;border-top:1px solid rgba(255,255,255,.15)}
.auth-side .mark{background:var(--mint);color:var(--jade-d);width:46px;height:46px;font-size:24px;border-radius:13px}
.auth-form{padding:38px 34px;display:flex;flex-direction:column;justify-content:center}.auth-form h2{font-size:24px;margin-bottom:4px}
footer{text-align:center;color:#8A9A92;font-size:12px;padding:6px 20px 24px}
.tabbar{display:none}
@media(max-width:900px){.cols,.budget-layout{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.hero-foot{grid-template-columns:1fr 1fr}.hero-foot .note{grid-column:1/-1;text-align:left}}
@media(max-width:760px){
 .links,.top .btn:not(.light),.who-email{display:none}
 .top-in{padding:10px 16px}.wrap{padding:20px 14px 100px}h1{font-size:27px}
 .tabbar{display:grid;grid-template-columns:repeat(4,1fr);position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding:6px 6px calc(6px + env(safe-area-inset-bottom,0px));z-index:30}
 .tabbar a{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:11px;font-weight:500;color:var(--muted);text-decoration:none;padding:6px 0}
 .tabbar a.on{color:var(--jade);font-weight:600}.tabbar svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
 .tabbar .add .circle{width:46px;height:46px;border-radius:50%;background:var(--jade);color:#fff;display:grid;place-items:center;margin-top:-20px;box-shadow:0 4px 14px rgba(15,123,95,.4)}
 .hero{padding:22px 20px 20px;border-radius:18px}.panel{padding:18px}
 .auth{grid-template-columns:1fr;margin:8px auto}.auth-side{padding:24px 22px}.auth-side ul{display:none}.auth-side h1{font-size:24px;margin:12px 0 0}.auth-form{padding:26px 22px}
 .tx{grid-template-columns:42px 1fr auto}.tx .actions{grid-column:2/-1}
 .month input{max-width:150px}
}
@media(max-width:440px){.brand{font-size:16px;gap:8px}.filters,.two{grid-template-columns:1fr}.cat-row b{min-width:0}.hero-foot b{font-size:19px}}
@media(prefers-reduced-motion:reduce){.seg{animation:none}}
</style></head><body>
{% set p=request.path %}
<header class="top"><div class="top-in"><a class="brand" href="/"><span class="mark" aria-hidden="true">₫</span>Chi Tiêu Thông Minh</a>
{% if g.user %}<nav class="links" aria-label="Điều hướng chính"><a href="/" class="{{ 'on' if p=='/' }}">Tổng quan</a><a href="/transactions" class="{{ 'on' if p.startswith('/transactions') and p!='/transactions/new' }}">Giao dịch</a><a href="/budgets" class="{{ 'on' if p.startswith('/budgets') }}">Ngân sách</a></nav><a class="btn" href="/transactions/new">Thêm giao dịch</a><div class="who"><span class="who-email">{{ g.user.email }}</span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn light sm">Đăng xuất</button></form></div>
{% else %}<nav class="links" aria-label="Tài khoản"><a href="/login" class="{{ 'on' if p=='/login' or p=='/' }}">Đăng nhập</a><a href="/register" class="{{ 'on' if p=='/register' }}">Đăng ký</a></nav>{% endif %}</div></header>
<main class="wrap">{% with msgs=get_flashed_messages(with_categories=true) %}{% for typ,msg in msgs %}<div class="flash {{ 'error' if typ=='error' else '' }}" role="{{ 'alert' if typ=='error' else 'status' }}">{{ msg }}</div>{% endfor %}{% endwith %}{% block body %}{% endblock %}</main>
{% if g.user %}<nav class="tabbar" aria-label="Điều hướng nhanh">
<a href="/" class="{{ 'on' if p=='/' }}"><svg viewBox="0 0 24 24"><path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z"/></svg>Tổng quan</a>
<a href="/transactions" class="{{ 'on' if p.startswith('/transactions') and p!='/transactions/new' }}"><svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>Giao dịch</a>
<a href="/transactions/new" class="add"><span class="circle"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></span>Thêm</a>
<a href="/budgets" class="{{ 'on' if p.startswith('/budgets') }}"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 4v8h8"/></svg>Ngân sách</a></nav>{% endif %}
<footer>Dữ liệu được lưu trong cơ sở dữ liệu SQLite của máy chủ</footer></body></html>
"""

DASH = r"""{% extends base %}{% block body %}
{% set bal=income-expense %}
<div class="head"><div><h1>Chi tiêu của bạn</h1><p class="muted" style="margin:0">Tiền vào, tiền ra và ngân sách trong tháng.</p></div>
<div class="month"><a href="/?month={{ prev_month }}" aria-label="Tháng trước"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a><form method="get"><input type="month" name="month" value="{{ month }}" onchange="this.form.submit()" aria-label="Chọn tháng"><noscript><button class="btn sm">Xem</button></noscript></form><a href="/?month={{ next_month }}" aria-label="Tháng sau"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></a></div></div>
<section class="hero" aria-labelledby="bal-label">
<div class="hero-top"><span id="bal-label">Số dư ròng</span><span class="pill">Tháng {{ month|vmonth }}</span></div>
<div class="big num{{ ' neg' if bal<0 }}">{{ bal|money }}</div>
<div class="split" role="img" aria-label="Tỷ lệ tổng thu so với tổng chi">{% if income+expense>0 %}{% if income>0 %}<i class="seg in" style="flex-grow:{{ income }}"></i>{% endif %}{% if expense>0 %}<i class="seg out" style="flex-grow:{{ expense }}"></i>{% endif %}{% else %}<i class="seg none" style="flex-grow:1"></i>{% endif %}</div>
<div class="hero-foot"><div><span class="k"><i class="sw in"></i>Tổng thu</span><b class="num">{{ income|money }}</b></div><div><span class="k"><i class="sw out"></i>Tổng chi</span><b class="num">{{ expense|money }}</b></div>
<div class="note">{% if income>0 %}{% if bal>=0 %}Giữ lại {{ ((income-expense)/income*100)|round|int }}% thu nhập trong tháng{% else %}Chi vượt thu {{ (expense-income)|money }}{% endif %}{% else %}Chưa có khoản thu nào trong tháng{% endif %}</div></div></section>
<div class="cols">
<section class="panel"><div class="panel-h"><h2>Chi tiêu theo danh mục</h2></div>
{% if bycat %}<ul class="cats">{% for x in bycat %}<li><div class="cat-row"><span class="dot" style="background:{{ cat_color(x.id) }}"></span><span class="cat-name">{{ x.name }}</span><span class="cat-share">{{ (x.total/expense*100)|round|int }}%</span><b class="num">{{ x.total|money }}</b></div><div class="bar"><div class="fill" style="width:{{ (x.total/maxcat*100)|round }}%;background:{{ cat_color(x.id) }}"></div></div></li>{% endfor %}</ul>
{% else %}<div class="empty">Chưa có khoản chi nào trong tháng này.<br><a class="btn" href="/transactions/new">Thêm giao dịch</a></div>{% endif %}</section>
<section class="panel"><div class="panel-h"><h2>Ngân sách tháng {{ month|vmonth }}</h2><a class="link" href="/budgets?month={{ month }}">Quản lý ngân sách</a></div>
{% if budgets %}{% for b in budgets %}{% set pct=b.spent/b.limit_amount*100 %}{% set st='over' if pct>=100 else ('warn' if pct>=80 else 'ok') %}<div class="bud"><div class="bud-top"><span class="dot" style="background:{{ cat_color(b.category_id) }}"></span><b>{{ b.name }}</b><span class="bud-pct num {{ st }}">{{ pct|round|int }}%</span></div><div class="bar mark80"><div class="fill {{ st }}" style="width:{{ [pct,100]|min }}%"></div></div><div class="bud-foot"><span>{{ b.spent|money }} / {{ b.limit_amount|money }}</span>{% if pct>=80 %}<span class="msg {{ st }}">{{ 'Đã vượt ngân sách' if pct>=100 else 'Đã dùng ít nhất 80% ngân sách' }}</span>{% endif %}</div></div>{% endfor %}<div class="legend"><i></i>Vạch đứng là mốc cảnh báo 80%</div>
{% else %}<div class="empty">Chưa đặt ngân sách cho tháng này.<br><a class="btn light" href="/budgets?month={{ month }}">Đặt ngân sách</a></div>{% endif %}</section></div>
<section class="panel" style="margin-top:18px"><div class="panel-h"><h2>Giao dịch gần đây</h2><a class="link" href="/transactions?month={{ month }}">Xem tất cả</a></div>
{% if recent %}<ul class="txs">{% for t in recent %}<li class="tx"><span class="ico" style="--c:{{ cat_color(t.category_id) }}" aria-hidden="true">{{ t.category|first|upper }}</span><div class="tx-main"><b>{{ t.note or t.category }}</b><div class="tx-meta"><span>{{ t.category }}</span><span>{{ t.tx_date|vdate }}</span></div></div><span class="tx-amt {{ 'in' if t.kind=='income' else 'out' }}">{{ '+' if t.kind=='income' else '−' }}{{ t.amount|money }}</span><div class="actions"><a class="link" href="/transactions/{{ t.id }}/edit">Sửa</a></div></li>{% endfor %}</ul>
{% else %}<div class="empty">Chưa có giao dịch nào.<br><a class="btn" href="/transactions/new">Thêm giao dịch đầu tiên</a></div>{% endif %}</section>{% endblock %}"""

LIST = r"""{% extends base %}{% block body %}
{% set ti=rows|selectattr('kind','equalto','income')|map(attribute='amount')|sum %}{% set to=rows|selectattr('kind','equalto','expense')|map(attribute='amount')|sum %}
<div class="head"><div><h1>Giao dịch</h1><p class="muted" style="margin:0">Thêm, sửa, xóa và lọc các khoản thu chi.</p></div><a class="btn" href="/transactions/new">Thêm giao dịch</a></div>
<form class="panel filters" method="get"><div><label for="f-month">Tháng</label><input id="f-month" type="month" name="month" value="{{ month }}"></div><div><label for="f-kind">Loại</label><select id="f-kind" name="kind"><option value="">Tất cả</option><option value="income" {{ 'selected' if kind=='income' }}>Thu</option><option value="expense" {{ 'selected' if kind=='expense' }}>Chi</option></select></div><div><label for="f-cat">Danh mục</label><select id="f-cat" name="category_id"><option value="">Tất cả</option>{% for c in cats %}<option value="{{ c.id }}" {{ 'selected' if category_id|string==c.id|string }}>{{ c.name }}</option>{% endfor %}</select></div><div><label for="f-q">Tìm ghi chú</label><input id="f-q" name="q" value="{{ q }}" placeholder="Nhập từ khóa"></div><div class="f-actions"><button class="btn">Lọc</button><a class="btn light" href="/transactions">Xóa lọc</a><a class="btn light" href="{{ export_url }}">Xuất CSV</a></div></form>
<section class="panel"><div class="sum"><span class="muted">{{ rows|length }} giao dịch</span><span class="in num">Thu +{{ ti|money }}</span><span class="out num">Chi −{{ to|money }}</span></div>
{% if rows %}<ul class="txs">{% for t in rows %}<li class="tx"><span class="ico" style="--c:{{ cat_color(t.category_id) }}" aria-hidden="true">{{ t.category|first|upper }}</span><div class="tx-main"><b>{{ t.note or t.category }}</b><div class="tx-meta"><span>{{ t.category }}</span><span>{{ t.tx_date|vdate }}</span></div></div><span class="tx-amt {{ 'in' if t.kind=='income' else 'out' }}">{{ '+' if t.kind=='income' else '−' }}{{ t.amount|money }}</span><div class="actions"><a class="btn light sm" href="/transactions/{{ t.id }}/edit">Sửa</a><form method="post" action="/transactions/{{ t.id }}/delete" onsubmit="return confirm('Xóa giao dịch này?')"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger sm">Xóa</button></form></div></li>{% endfor %}</ul>
{% else %}<div class="empty">Không có giao dịch nào khớp bộ lọc.<br><a class="btn light" href="/transactions">Xóa bộ lọc</a></div>{% endif %}</section>{% endblock %}"""

FORM = r"""{% extends base %}{% block body %}
<div class="head"><div><h1>{{ title }}</h1><p class="muted" style="margin:0">{{ 'Cập nhật thông tin khoản này.' if row else 'Ghi lại một khoản thu hoặc chi.' }}</p></div><a class="btn light" href="/transactions">Quay lại</a></div>
<form class="panel formcard" method="post"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
<div class="field"><div class="seg2" role="radiogroup" aria-label="Loại giao dịch"><label><input type="radio" name="kind" value="expense" {{ 'checked' if not row or row.kind=='expense' }}><span>Chi tiêu</span></label><label><input type="radio" name="kind" value="income" {{ 'checked' if row and row.kind=='income' }}><span>Thu nhập</span></label></div></div>
<div class="field"><label for="amount">Số tiền (VND) *</label><div class="amount-box"><input class="amount" id="amount" name="amount" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" required value="{{ row.amount if row else '' }}" placeholder="150000"><span class="unit">đ</span></div><div class="quick" aria-label="Cộng nhanh"><button type="button" data-add="50000">+50k</button><button type="button" data-add="100000">+100k</button><button type="button" data-add="500000">+500k</button><button type="button" data-add="1000000">+1tr</button><span class="amt-fmt" id="amt-fmt" aria-live="polite"></span></div></div>
<div class="field"><span class="sr" id="cat-label">Danh mục</span><label aria-hidden="true">Danh mục *</label><div class="chips" role="radiogroup" aria-labelledby="cat-label">{% for c in cats %}<label><input type="radio" name="category_id" value="{{ c.id }}" data-kind="{{ c.kind }}" required {{ 'checked' if row and row.category_id==c.id }}><span><i class="dot" style="background:{{ cat_color(c.id) }}"></i>{{ c.name }}</span></label>{% endfor %}</div></div>
<div class="two field"><div><label for="tx_date">Ngày *</label><input id="tx_date" name="tx_date" type="date" required value="{{ row.tx_date if row else today }}"></div></div>
<div class="field"><label for="note">Ghi chú</label><textarea id="note" name="note" rows="3" maxlength="300" placeholder="Ví dụ: ăn trưa với đồng nghiệp">{{ row.note if row else '' }}</textarea></div>
<div class="form-actions"><a class="btn light" href="/transactions">Hủy</a><button class="btn">Lưu giao dịch</button></div></form>
<script>(function(){var kinds=document.querySelectorAll('input[name=kind]'),chips=document.querySelectorAll('.chips label');
function sync(){var k=document.querySelector('input[name=kind]:checked').value,has=false;chips.forEach(function(l){var r=l.querySelector('input'),ok=r.dataset.kind===k;l.hidden=!ok;if(!ok)r.checked=false;else if(r.checked)has=true});if(!has){for(var i=0;i<chips.length;i++){if(!chips[i].hidden){chips[i].querySelector('input').checked=true;break}}}}
kinds.forEach(function(r){r.addEventListener('change',sync)});sync();
var a=document.getElementById('amount'),o=document.getElementById('amt-fmt');
function fmt(){var d=a.value.replace(/[^0-9]/g,'');o.textContent=d?Number(d).toLocaleString('vi-VN')+' đ':''}
a.addEventListener('input',fmt);fmt();
document.querySelectorAll('[data-add]').forEach(function(b){b.addEventListener('click',function(){var d=a.value.replace(/[^0-9]/g,'');a.value=String(Number(d||0)+Number(b.dataset.add));fmt();a.focus()})});})();</script>{% endblock %}"""

BUDGETS = r"""{% extends base %}{% block body %}
<div class="head"><div><h1>Ngân sách</h1><p class="muted" style="margin:0">Đặt hạn mức chi theo tháng và danh mục.</p></div>
<div class="month"><a href="/budgets?month={{ prev_month }}" aria-label="Tháng trước"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a><form method="get"><input type="month" name="month" value="{{ month }}" onchange="this.form.submit()" aria-label="Chọn tháng"><noscript><button class="btn sm">Xem</button></noscript></form><a href="/budgets?month={{ next_month }}" aria-label="Tháng sau"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></a></div></div>
<div class="budget-layout">
<section class="panel"><h2 style="margin-bottom:16px">Thiết lập ngân sách</h2><form method="post"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
<div class="field"><label for="b-month">Tháng</label><input id="b-month" type="month" name="month" value="{{ month }}" required></div>
<div class="field"><label for="b-cat">Danh mục chi</label><select id="b-cat" name="category_id" required>{% for c in cats %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></div>
<div class="field"><label for="b-limit">Hạn mức (VND)</label><input id="b-limit" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" name="limit_amount" required placeholder="2000000"></div>
<button class="btn wide">Lưu ngân sách</button><p class="muted small" style="margin:12px 0 0">Đặt lại cho cùng danh mục và tháng sẽ cập nhật hạn mức cũ.</p></form></section>
<section class="panel"><div class="panel-h"><h2>Ngân sách tháng {{ month|vmonth }}</h2></div>
{% if rows %}{% for b in rows %}{% set pct=b.spent/b.limit_amount*100 %}{% set st='over' if pct>=100 else ('warn' if pct>=80 else 'ok') %}<div class="bud"><div class="bud-top"><span class="dot" style="background:{{ cat_color(b.category_id) }}"></span><b>{{ b.name }}</b><span class="bud-pct num {{ st }}">{{ pct|round|int }}%</span><form method="post" action="/budgets/{{ b.id }}/delete" onsubmit="return confirm('Xóa ngân sách?')" style="margin:0"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger sm">Xóa</button></form></div><div class="bar mark80"><div class="fill {{ st }}" style="width:{{ [pct,100]|min }}%"></div></div><div class="bud-foot"><span>{{ b.spent|money }} / {{ b.limit_amount|money }}</span><span class="msg {{ st }}">{{ 'Đã vượt ngân sách' if pct>=100 else ('Đã dùng ít nhất 80% ngân sách' if pct>=80 else 'Còn lại '+((b.limit_amount-b.spent)|money)) }}</span></div></div>{% endfor %}<div class="legend"><i></i>Vạch đứng là mốc cảnh báo 80%</div>
{% else %}<div class="empty">Chưa có ngân sách cho tháng này.<br>Chọn danh mục và hạn mức ở khung bên cạnh.</div>{% endif %}</section></div>{% endblock %}"""

LOGIN = r"""{% extends base %}{% block body %}<div class="auth"><aside class="auth-side"><span class="mark" aria-hidden="true">₫</span><h1>Biết tiền của bạn đi đâu mỗi tháng</h1><ul><li>Ghi khoản thu chi chỉ trong vài chạm</li><li>Đặt ngân sách và nhận cảnh báo từ mức 80%</li><li>Xuất CSV, mở được bằng Excel</li></ul></aside>
<form class="auth-form" method="post" action="{{ url_for('login', next=next_url) }}"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><h2>Đăng nhập</h2><p class="muted" style="margin:0 0 22px">Xem và quản lý chi tiêu của bạn.</p>
<div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required maxlength="254" autocomplete="username" value="{{ email or '' }}"></div>
<div class="field"><label for="password">Mật khẩu</label><input id="password" name="password" type="password" required maxlength="128" autocomplete="current-password"></div>
<button class="btn wide">Đăng nhập</button><p class="muted small" style="text-align:center;margin:16px 0 0">Chưa có tài khoản? <a class="link" href="/register">Đăng ký</a></p></form></div>{% endblock %}"""

REGISTER = r"""{% extends base %}{% block body %}<div class="auth"><aside class="auth-side"><span class="mark" aria-hidden="true">₫</span><h1>Tạo sổ chi tiêu riêng của bạn</h1><ul><li>Mỗi tài khoản có dữ liệu thu chi riêng</li><li>Danh mục có sẵn, dùng được ngay</li><li>Dữ liệu được lưu bền vững trên máy chủ</li></ul></aside>
<form class="auth-form" method="post" action="/register"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><h2>Đăng ký</h2><p class="muted" style="margin:0 0 22px">Mật khẩu tối thiểu 8 ký tự.</p>
<div class="field"><label for="email">Email</label><input id="email" name="email" type="email" required maxlength="254" autocomplete="username" value="{{ email or '' }}"></div>
<div class="field"><label for="password">Mật khẩu</label><input id="password" name="password" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></div>
<div class="field"><label for="confirm">Xác nhận mật khẩu</label><input id="confirm" name="confirm" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></div>
{% if invite_required %}<div class="field"><label for="invite">Mã mời</label><input id="invite" name="invite" type="password" required autocomplete="off"></div>{% endif %}
<button class="btn wide">Tạo tài khoản</button><p class="muted small" style="text-align:center;margin:16px 0 0">Đã có tài khoản? <a class="link" href="/login">Đăng nhập</a></p></form></div>{% endblock %}"""

def page(template, **kwargs):
    return render_template_string(template, base=app.jinja_env.from_string(BASE), **kwargs)

def month_arg(value):
    try: datetime.strptime(value, "%Y-%m"); return value
    except (ValueError, TypeError): return date.today().strftime("%Y-%m")

def categories():
    with db() as c: return c.execute("SELECT * FROM categories ORDER BY kind,id").fetchall()

# ---------------------------------------------------------------- Xác thực & CSRF
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DUMMY_HASH = generate_password_hash("mat-khau-gia-de-chong-do-thoi-gian")
LOGIN_FAILS = {}          # (ip, email) -> [số lần sai, thời điểm sai đầu tiên]
MAX_FAILS, LOCK_SECONDS = 5, 600

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]
app.jinja_env.globals["csrf_token"] = csrf_token

@app.before_request
def load_user_and_check_csrf():
    g.user = None
    user_id = session.get("uid")
    if user_id:
        with db() as c:
            g.user = c.execute("SELECT id,email FROM users WHERE id=?", (user_id,)).fetchone()
        if g.user is None:
            session.pop("uid", None)
    if request.method == "POST" and app.config.get("CSRF_ENABLED", True):
        sent = request.form.get("csrf", "").encode()
        good = session.get("csrf", "").encode()
        if not sent or not good or not hmac.compare_digest(sent, good):
            return "Phiên làm việc không hợp lệ hoặc đã hết hạn. Hãy quay lại, tải lại trang rồi thử lại.", 400

def uid():
    return g.user["id"]

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.user is None:
            nxt = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("login", next=nxt))
        return f(*args, **kwargs)
    return wrapper

def safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else url_for("dashboard")

def start_session(user_id):
    session.clear()
    session["uid"] = user_id
    session["csrf"] = secrets.token_hex(16)
    session.permanent = True

def is_locked(key):
    f = LOGIN_FAILS.get(key)
    if not f: return False
    if time.time() - f[1] > LOCK_SECONDS:
        LOGIN_FAILS.pop(key, None); return False
    return f[0] >= MAX_FAILS

def record_fail(key):
    if len(LOGIN_FAILS) > 5000: LOGIN_FAILS.clear()
    f = LOGIN_FAILS.get(key)
    if not f or time.time() - f[1] > LOCK_SECONDS: LOGIN_FAILS[key] = [1, time.time()]
    else: f[0] += 1

def render_login(email="", next_url=None):
    return page(LOGIN, title="Đăng nhập", email=email, next_url=next_url)

@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user: return redirect(url_for("dashboard"))
    next_url = request.args.get("next")
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        key = (request.remote_addr, email)
        if is_locked(key):
            flash("Đăng nhập sai quá nhiều lần. Hãy thử lại sau vài phút.", "error")
        else:
            with db() as c:
                user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            ok = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password)
            if user and ok:
                LOGIN_FAILS.pop(key, None)
                start_session(user["id"])
                return redirect(safe_next(next_url))
            record_fail(key)
            flash("Email hoặc mật khẩu không đúng.", "error")
    return render_login(email, next_url)

@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user: return redirect(url_for("dashboard"))
    invite = os.environ.get("INVITE_CODE", "")
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        code = request.form.get("invite", "")
        err, new_id = None, None
        if invite and not hmac.compare_digest(code.encode(), invite.encode()): err = "Mã mời không đúng."
        elif not EMAIL_RE.match(email) or len(email) > 254: err = "Email không hợp lệ."
        elif len(password) < 8 or len(password) > 128: err = "Mật khẩu phải từ 8 đến 128 ký tự."
        elif password != confirm: err = "Mật khẩu xác nhận không khớp."
        if not err:
            try:
                with db() as c:
                    new_id = c.execute("INSERT INTO users(email,password_hash) VALUES(?,?)", (email, generate_password_hash(password))).lastrowid
                    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1:
                        # Tài khoản đầu tiên nhận lại dữ liệu cũ (tạo trước khi có đăng nhập)
                        c.execute("UPDATE transactions SET user_id=? WHERE user_id IS NULL", (new_id,))
                        c.execute("UPDATE budgets SET user_id=? WHERE user_id IS NULL", (new_id,))
            except sqlite3.IntegrityError:
                err = "Email này đã được đăng ký."
        if err:
            flash(err, "error")
        else:
            start_session(new_id)
            flash("Đã tạo tài khoản.", "success")
            return redirect(url_for("dashboard"))
    return page(REGISTER, title="Đăng ký", email=email, invite_required=bool(invite))

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Đã đăng xuất.", "success")
    return redirect(url_for("login"))

# ---------------------------------------------------------------- Chức năng chính (mọi truy vấn lọc theo user_id)
@app.route("/")
def dashboard():
    if g.user is None:
        return render_login()          # trả 200 để health check "/" luôn thấy trang hoạt động
    month=month_arg(request.args.get("month",date.today().strftime("%Y-%m"))); u=uid()
    with db() as c:
        totals=c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE 0 END),0) income, COALESCE(SUM(CASE WHEN kind='expense' THEN amount ELSE 0 END),0) expense FROM transactions WHERE user_id=? AND substr(tx_date,1,7)=?",(u,month)).fetchone()
        bycat=c.execute("SELECT ca.id,ca.name,SUM(t.amount) total FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.user_id=? AND t.kind='expense' AND substr(t.tx_date,1,7)=? GROUP BY ca.id ORDER BY total DESC",(u,month)).fetchall()
        recent=c.execute("SELECT t.*,ca.name category FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.user_id=? AND substr(t.tx_date,1,7)=? ORDER BY tx_date DESC,t.id DESC LIMIT 8",(u,month)).fetchall()
        budgets=c.execute("""SELECT b.*,ca.name,COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.user_id=b.user_id AND t.category_id=b.category_id AND t.kind='expense' AND substr(t.tx_date,1,7)=b.month),0) spent FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.user_id=? AND b.month=?""",(u,month)).fetchall()
    return page(DASH, title="Tổng quan",month=month,income=totals["income"],expense=totals["expense"],bycat=bycat,maxcat=max([x["total"] for x in bycat],default=1),recent=recent,budgets=budgets,prev_month=shift_month(month,-1),next_month=shift_month(month,1))

@app.route("/transactions")
@login_required
def tx_list():
    month=request.args.get("month",""); kind=request.args.get("kind",""); cat=request.args.get("category_id",""); q=request.args.get("q","").strip()
    sql="SELECT t.*,ca.name category FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.user_id=?"; params=[uid()]
    if month:
        try: datetime.strptime(month,"%Y-%m"); sql+=" AND substr(t.tx_date,1,7)=?"; params.append(month)
        except ValueError: month=""
    if kind in ("income","expense"): sql+=" AND t.kind=?"; params.append(kind)
    else: kind=""
    if cat.isdigit(): sql+=" AND t.category_id=?"; params.append(int(cat))
    else: cat=""
    if q: sql+=" AND t.note LIKE ?"; params.append("%"+q+"%")
    sql+=" ORDER BY t.tx_date DESC,t.id DESC"
    with db() as c: rows=c.execute(sql,params).fetchall()
    export_url=url_for("export_csv",month=month,kind=kind,category_id=cat,q=q)
    return page(LIST,title="Giao dịch",rows=rows,cats=categories(),month=month,kind=kind,category_id=cat,q=q,export_url=export_url)

def save_tx(txid=None):
    kind=request.form.get("kind",""); amount=request.form.get("amount",""); tx_date=request.form.get("tx_date",""); cat=request.form.get("category_id",""); note=request.form.get("note","").strip()
    try:
        amount=parse_amount(amount); datetime.strptime(tx_date,"%Y-%m-%d")
        if kind not in ("income","expense") or amount<=0 or amount>MAX_AMOUNT: raise ValueError
        with db() as c:
            category=c.execute("SELECT * FROM categories WHERE id=?",(cat,)).fetchone()
            if not category or category["kind"]!=kind: raise ValueError
            if txid: c.execute("UPDATE transactions SET kind=?,amount=?,tx_date=?,category_id=?,note=? WHERE id=? AND user_id=?",(kind,amount,tx_date,int(cat),note,txid,uid()))
            else: c.execute("INSERT INTO transactions(kind,amount,tx_date,category_id,note,user_id) VALUES(?,?,?,?,?,?)",(kind,amount,tx_date,int(cat),note,uid()))
        flash("Đã lưu giao dịch.","success"); return redirect(url_for("tx_list"))
    except (ValueError,TypeError,OverflowError):
        flash("Dữ liệu không hợp lệ. Kiểm tra số tiền, ngày và danh mục.","error"); return None

@app.route("/transactions/new",methods=["GET","POST"])
@login_required
def tx_new():
    if request.method=="POST":
        result=save_tx()
        if result: return result
    return page(FORM,title="Thêm giao dịch",row=None,cats=categories(),today=date.today().isoformat())

@app.route("/transactions/<int:txid>/edit",methods=["GET","POST"])
@login_required
def tx_edit(txid):
    with db() as c: row=c.execute("SELECT * FROM transactions WHERE id=? AND user_id=?",(txid,uid())).fetchone()
    if not row: flash("Không tìm thấy giao dịch.","error"); return redirect(url_for("tx_list"))
    if request.method=="POST":
        result=save_tx(txid)
        if result: return result
        with db() as c: row=c.execute("SELECT * FROM transactions WHERE id=? AND user_id=?",(txid,uid())).fetchone()
    return page(FORM,title="Sửa giao dịch",row=row,cats=categories(),today=date.today().isoformat())

@app.route("/transactions/<int:txid>/delete",methods=["POST"])
@login_required
def tx_delete(txid):
    with db() as c: c.execute("DELETE FROM transactions WHERE id=? AND user_id=?",(txid,uid()))
    flash("Đã xóa giao dịch.","success"); return redirect(url_for("tx_list"))

@app.route("/budgets",methods=["GET","POST"])
@login_required
def budget_page():
    month=month_arg(request.values.get("month",date.today().strftime("%Y-%m")))
    if request.method=="POST":
        cat=request.form.get("category_id",""); amount=request.form.get("limit_amount",""); month=month_arg(request.form.get("month",month))
        try:
            amount=parse_amount(amount)
            with db() as c:
                category=c.execute("SELECT * FROM categories WHERE id=? AND kind='expense'",(cat,)).fetchone()
                if not category or amount<=0 or amount>MAX_AMOUNT: raise ValueError
                c.execute("INSERT INTO budgets(month,category_id,limit_amount,user_id) VALUES(?,?,?,?) ON CONFLICT(user_id,month,category_id) DO UPDATE SET limit_amount=excluded.limit_amount",(month,int(cat),amount,uid()))
            flash("Đã lưu ngân sách.","success"); return redirect(url_for("budget_page",month=month))
        except (ValueError,TypeError,OverflowError): flash("Chọn danh mục và nhập hạn mức lớn hơn 0.","error")
    with db() as c:
        rows=c.execute("""SELECT b.*,ca.name,COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.user_id=b.user_id AND t.category_id=b.category_id AND t.kind='expense' AND substr(t.tx_date,1,7)=b.month),0) spent FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.user_id=? AND b.month=? ORDER BY ca.name""",(uid(),month)).fetchall()
    return page(BUDGETS,title="Ngân sách",month=month,rows=rows,cats=[x for x in categories() if x["kind"]=="expense"],prev_month=shift_month(month,-1),next_month=shift_month(month,1))

@app.route("/budgets/<int:bid>/delete",methods=["POST"])
@login_required
def budget_delete(bid):
    with db() as c: c.execute("DELETE FROM budgets WHERE id=? AND user_id=?",(bid,uid()))
    flash("Đã xóa ngân sách.","success"); return redirect(url_for("budget_page"))

@app.route("/export.csv")
@login_required
def export_csv():
    month=request.args.get("month",""); kind=request.args.get("kind",""); cat=request.args.get("category_id",""); q=request.args.get("q","").strip()
    sql="SELECT t.tx_date,t.kind,t.amount,ca.name category,t.note FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.user_id=?"; params=[uid()]
    if month:
        try: datetime.strptime(month,"%Y-%m"); sql+=" AND substr(t.tx_date,1,7)=?"; params.append(month)
        except ValueError: pass
    if kind in ("income","expense"): sql+=" AND t.kind=?"; params.append(kind)
    if cat.isdigit(): sql+=" AND t.category_id=?"; params.append(int(cat))
    if q: sql+=" AND t.note LIKE ?"; params.append("%"+q+"%")
    sql+=" ORDER BY t.tx_date DESC,t.id DESC"
    with db() as c: rows=c.execute(sql,params).fetchall()
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Ngày","Loại","Số tiền (VND)","Danh mục","Ghi chú"])
    for r in rows: w.writerow([r["tx_date"],"Thu" if r["kind"]=="income" else "Chi",r["amount"],r["category"],r["note"]])
    return Response("\ufeff"+out.getvalue(),content_type="text/csv; charset=utf-8",headers={"Content-Disposition":f"attachment; filename=giao_dich_{month or 'tat_ca'}.csv"})

@app.route("/health")
def health(): return {"status":"ok"}

init_db()
if __name__=="__main__":
    # Có PORT (môi trường container/PaaS) -> nghe 0.0.0.0 để proxy bên ngoài (Traefik...) truy cập được.
    # Chạy local không đặt PORT -> chỉ nghe 127.0.0.1 cho an toàn.
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG")=="1")
