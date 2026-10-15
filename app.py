from flask import Flask, request, redirect, url_for, flash, Response, render_template_string, session, g
import os, re, hmac, time, secrets, sqlite3, csv, io, calendar
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
    c.create_function("pylower", 1, lambda v: (v or "").lower(), deterministic=True)
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
CAT_COLORS = ["#0F7B5F", "#2F6FDE", "#E0673A", "#8B5CF6", "#C98200", "#0E9AA7", "#6741D9", "#6B7F3A"]
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
<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#A61E4D">
<title>{{ title or 'Chi Tiêu Thông Minh' }}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23D6336C'/%3E%3Ctext x='16' y='23' font-size='20' text-anchor='middle' fill='white' font-family='Arial'%3E%E2%82%AB%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&display=swap" rel="stylesheet">
<style>
:root{--paper:#FBF4F6;--panel:#fff;--ink:#2B1621;--muted:#765F6B;--line:#EBDDE2;--soft:#F7E8EE;--brand:#D6336C;--brand-d:#A61E4D;--seg-in:#7EE2B8;--seg-out:#FFB07A;--in:#0F7B5F;--out:#C2410C;--bar-out:#E8743B;--warn:#946000;--ring:rgba(214,51,108,.28)}
*{box-sizing:border-box}[hidden]{display:none!important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;min-height:100vh;display:flex;flex-direction:column}
h1,h2,.num,.brand,.mark{font-family:"Bricolage Grotesque","Be Vietnam Pro",system-ui,sans-serif}
.num,td,.tx-amt{font-variant-numeric:tabular-nums}
a{color:var(--brand-d)}
:focus-visible{outline:3px solid var(--ring);outline-offset:2px;border-radius:8px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.muted{color:var(--muted)}.small{font-size:13px}
/* thanh trên */
.top{position:sticky;top:0;z-index:20;background:rgba(251,244,246,.92);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding-top:env(safe-area-inset-top,0px)}
.top-in{max-width:1120px;margin:0 auto;padding:11px 20px;display:flex;align-items:center;gap:18px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--ink);text-decoration:none;margin-right:auto;letter-spacing:-.01em;white-space:nowrap}
.mark{width:34px;height:34px;border-radius:10px;background:var(--brand);color:#fff;display:grid;place-items:center;font-weight:700;font-size:18px;flex:none}
.links{display:flex;gap:4px}
.links a{padding:8px 15px;border-radius:999px;color:var(--muted);font-weight:500;text-decoration:none}
.links a:hover{color:var(--ink);background:var(--soft)}
.links a.on{background:var(--ink);color:#fff}
.who{display:flex;align-items:center;gap:10px}.who form{margin:0}.who-email{color:var(--muted);font-size:13px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* nút */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;background:var(--brand);color:#fff;border:1px solid var(--brand);border-radius:10px;padding:10px 16px;font:600 14px/1.2 "Be Vietnam Pro",system-ui,sans-serif;text-decoration:none;cursor:pointer;white-space:nowrap}
a.btn{color:#fff}.btn:hover{background:var(--brand-d);border-color:var(--brand-d)}
.btn.light,a.btn.light{background:#fff;color:var(--ink);border-color:#DCC9D2}.btn.light:hover{background:var(--soft);border-color:#C9B1BD}
.btn.danger,a.btn.danger{background:#fff;color:var(--out);border-color:#F0C9B6}.btn.danger:hover{background:#FDEBDD}
.btn.sm{padding:7px 12px;font-size:13px}.btn.wide{width:100%;padding:13px 16px;font-size:15px}
/* bố cục */
.wrap{max-width:1120px;width:100%;margin:0 auto;padding:28px 20px 44px;flex:1}
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:22px}
h1{font-size:32px;line-height:1.12;margin:0 0 6px;font-weight:700;letter-spacing:-.02em}
.crumbs{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);margin-bottom:12px}.crumbs a{color:var(--muted);text-decoration:none}.crumbs a:hover{color:var(--brand);text-decoration:underline}.crumbs [aria-current]{color:var(--ink);font-weight:600}
.head-form{margin-bottom:20px}.caps{font-size:26px;letter-spacing:.02em}.lead{margin:0;max-width:640px}
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
.hero{background:var(--brand-d);color:#fff;border-radius:22px;padding:28px 30px 24px;margin-bottom:18px}
.hero-top{display:flex;justify-content:space-between;align-items:center;gap:12px;color:#F9D3E2;font-weight:500}
.pill{background:rgba(255,255,255,.12);border-radius:999px;padding:4px 12px;font-size:13px;color:#fff}
.big{font-size:clamp(40px,8vw,68px);line-height:1.05;font-weight:700;letter-spacing:-.03em;margin:10px 0 20px}.big.neg{color:var(--seg-out)}
.split{display:flex;height:14px;border-radius:999px;overflow:hidden;background:rgba(255,255,255,.1)}
.seg{display:block;flex-basis:0;flex-shrink:1;min-width:0;transform-origin:left;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}
.seg+.seg{margin-left:3px}.seg.in{background:var(--seg-in)}.seg.out{background:var(--seg-out)}.seg.none{background:transparent}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.hero-foot{display:flex;justify-content:space-between;align-items:center;gap:8px 18px;flex-wrap:wrap;margin-top:14px;color:#F9D3E2;font-size:13px}.hero-foot .lg2{display:flex;gap:16px;flex-wrap:wrap}.hero-foot .lg2 span{display:inline-flex;align-items:center;gap:8px}
.sw{width:10px;height:10px;border-radius:3px;display:inline-block;flex:none}.sw.in{background:var(--seg-in)}.sw.out{background:var(--seg-out)}.sw.bi{background:var(--in)}.sw.be{background:var(--bar-out)}
.hero-foot .note{font-size:14px}
/* thẻ số liệu và biểu đồ */
.kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-bottom:18px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;min-width:0}
.kpi-h{display:flex;align-items:center;gap:8px;color:var(--muted);font-weight:500;font-size:14px}
.kpi-v{display:block;font-size:30px;line-height:1.15;font-weight:700;letter-spacing:-.02em;margin:8px 0 6px;overflow-wrap:anywhere}
.delta{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600}.delta svg{width:11px;height:11px;fill:currentColor;flex:none}.delta svg.down{transform:rotate(180deg)}
.delta.good{color:var(--in)}.delta.bad{color:var(--out)}.delta.flat{color:var(--muted);font-weight:500}
.tabs2{display:inline-flex;background:var(--soft);border-radius:10px;padding:3px;gap:2px}
.tabs2 button{border:0;background:transparent;padding:6px 16px;border-radius:8px;font:600 13px "Be Vietnam Pro",system-ui,sans-serif;color:var(--muted);cursor:pointer}
.tabs2 button[aria-pressed=true]{background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(43,22,33,.14)}
.chart-top{display:flex;justify-content:space-between;align-items:center;gap:8px 18px;flex-wrap:wrap;margin-bottom:8px;font-size:13px;color:var(--muted)}
.lg{display:flex;gap:16px}.lg span{display:inline-flex;align-items:center;gap:8px}.chart-sum{display:flex;gap:16px;flex-wrap:wrap}.chart-sum b{font-weight:700}.chart-sum .in{color:var(--in)}.chart-sum .out{color:var(--out)}
.chart-wrap{position:relative}.chart{min-height:240px}.chart svg{display:block;max-width:100%;touch-action:pan-y}
.chart .gl{stroke:var(--line);stroke-width:1}.chart .ax{fill:var(--muted);font-size:11px;font-family:"Be Vietnam Pro",system-ui,sans-serif}
.chart .bi{fill:var(--in)}.chart .be{fill:var(--bar-out)}.chart .hit{fill:transparent;cursor:crosshair}.chart .hit:hover,.chart .hit.on{fill:rgba(214,51,108,.07)}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:#fff;border-radius:10px;padding:8px 11px;font-size:12px;line-height:1.55;white-space:nowrap;transform:translate(-50%,-100%);z-index:5}
.tip b{display:block;margin-bottom:2px}.tip i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px}
/* danh mục & ngân sách */
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;flex:none}
.cats{list-style:none;margin:0;padding:0}.cats li{margin:0 0 16px}.cats li:last-child{margin:0}
.cat-row{display:flex;align-items:center;gap:10px}.cat-name{flex:1;font-weight:500}.cat-share{color:var(--muted);font-size:13px;min-width:38px;text-align:right}.cat-row b{min-width:112px;text-align:right}
.bar{position:relative;height:9px;background:var(--soft);border-radius:999px;margin:8px 0}
.bar .fill{height:100%;border-radius:999px;background:var(--in);min-width:4px}.bar .fill.warn{background:#E0A100}.bar .fill.over{background:#D9482B}
.bar.mark80::after{content:"";position:absolute;left:80%;top:-4px;bottom:-4px;width:2px;background:var(--ink);opacity:.35;border-radius:2px}
.bud{margin:0 0 20px}.bud:last-child{margin:0}
.bud-top{display:flex;align-items:center;gap:10px}.bud-top b{flex:1}
.bud-pct{font-weight:700;font-size:20px;letter-spacing:-.01em}.bud-pct.ok{color:var(--in)}.bud-pct.warn{color:var(--warn)}.bud-pct.over{color:var(--out)}
.bud-foot{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--muted)}
.msg.warn{color:var(--warn);font-weight:600}.msg.over{color:var(--out);font-weight:600}
.legend{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);margin-top:14px}.legend i{width:2px;height:12px;background:var(--ink);opacity:.35;display:inline-block}
/* ngân sách: tổng quan, cảnh báo, thẻ danh mục */
.bar .fill.ok{background:var(--in)}
.bsum{padding:22px 24px}.bsum-top{display:flex;justify-content:space-between;align-items:flex-end;gap:12px}
.bsum-v{font-size:clamp(30px,5vw,42px);font-weight:700;letter-spacing:-.02em;line-height:1.1;margin-top:4px}
.bsum-pct{font-size:30px;font-weight:700;letter-spacing:-.01em}.bsum-pct.ok{color:var(--in)}.bsum-pct.warn{color:var(--warn)}.bsum-pct.over{color:var(--out)}
.bar.thick{height:14px;margin:16px 0 12px}.bsum-foot{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:14px}.bsum-foot b{color:var(--ink)}
.alerts h2{margin-bottom:12px}.alerts ul{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.al{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 14px;border-radius:10px;font-size:14px}.al b{font-weight:600}.al.over{background:#FDEBDD;color:#7A2112}.al.warn{background:#FFF4D6;color:#6B4700}
.bcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:14px}
.bcard-top{display:flex;align-items:center;gap:14px}.bcard-main{flex:1;min-width:0}.bcard-main b{display:block}.bcard-main span{display:block}
.bcard .bud-foot{align-items:center}.bcard .bud-foot .actions{margin-left:auto}
.alert-bar{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:12px;margin-bottom:16px;text-decoration:none;font-size:14px}
.alert-bar svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}.alert-bar span:nth-of-type(1){flex:1}
.alert-bar.over{background:#FDEBDD;border:1px solid #F0BFB2;color:#7A2112}.alert-bar.warn{background:#FFF4D6;border:1px solid #EBD08A;color:#6B4700}
.alert-bar .link{color:inherit;white-space:nowrap}
.flash.warn{background:#FFF4D6;border-color:#EBD08A;color:#6B4700}
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
/* bộ lọc, chip, phân trang */
.head-actions{display:flex;gap:10px;flex-wrap:wrap}.fpanel{padding:18px 20px}
.presets{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.preset{padding:6px 14px;border-radius:999px;border:1px solid #DCC9D2;background:#fff;color:var(--ink);font-size:13px;font-weight:500;text-decoration:none}
.preset:hover{background:var(--soft)}.preset.on{background:var(--ink);border-color:var(--ink);color:#fff}
.chips-active{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:-4px 0 16px}
.chip-x{display:inline-flex;align-items:center;gap:6px;padding:5px 9px 5px 12px;border-radius:999px;background:var(--soft);border:1px solid var(--line);color:var(--ink);font-size:13px;font-weight:500;text-decoration:none;max-width:100%}
.chip-x:hover{background:#F3DCE5}.chip-x svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2.4;stroke-linecap:round;flex:none}
.pager{display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}
.pg{min-width:38px;height:38px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;border-radius:10px;border:1px solid #DCC9D2;background:#fff;color:var(--ink);text-decoration:none;font-weight:600;font-size:14px}
a.pg:hover{background:var(--soft)}.pg.on{background:var(--brand);border-color:var(--brand);color:#fff}.pg.gap{border:0;background:transparent}
/* biểu mẫu */
label{font-size:13px;font-weight:600;display:block;margin-bottom:6px}
input,select,textarea{width:100%;padding:11px 12px;border:1px solid #DCC9D2;border-radius:10px;background:#fff;font:inherit;color:var(--ink)}
input:focus,select:focus,textarea:focus{outline:3px solid var(--ring);border-color:var(--brand)}
.filters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;align-items:start}.f-actions{grid-column:1/-1;display:flex;gap:10px;flex-wrap:wrap}
.formcard{max-width:660px}.field{margin-bottom:20px}
.seg2{display:grid;grid-template-columns:1fr 1fr;background:var(--soft);border-radius:12px;padding:4px;gap:4px}
.seg2 label,.chips label{position:relative;margin:0;cursor:pointer}.seg2 input,.chips input{position:absolute;opacity:0;pointer-events:none}
.seg2 span{display:block;text-align:center;padding:10px;border-radius:9px;font-weight:600;color:var(--muted)}
.seg2 input[value=expense]:checked+span{background:var(--out);color:#fff}.seg2 input[value=income]:checked+span{background:var(--in);color:#fff}
.seg2 input:focus-visible+span,.chips input:focus-visible+span{outline:3px solid var(--ring);outline-offset:2px}
.amount-box{position:relative}.amount{font:700 34px/1.1 "Bricolage Grotesque",sans-serif;padding:14px 48px 14px 16px;letter-spacing:-.01em}.amount-box .unit{position:absolute;right:16px;top:50%;transform:translateY(-50%);font:600 20px "Bricolage Grotesque",sans-serif;color:var(--muted)}
.amt-fmt{margin-left:auto;color:var(--brand);font-weight:700;align-self:center}
.quick{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;min-height:30px}.quick button{border:1px solid #DCC9D2;background:#fff;border-radius:999px;padding:5px 12px;font:500 13px "Be Vietnam Pro",sans-serif;cursor:pointer;color:var(--ink)}.quick button:hover{background:var(--soft)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips span{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border:1px solid #DCC9D2;border-radius:999px;background:#fff;font-weight:500}
.chips input:checked+span{background:var(--ink);border-color:var(--ink);color:#fff}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.form-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:8px}
.budget-layout{display:grid;grid-template-columns:340px 1fr;gap:18px;align-items:start}.budget-layout .panel{margin-bottom:0}
.flash{display:flex;gap:10px;align-items:center;padding:12px 16px;border-radius:12px;background:#E1F3E9;border:1px solid #B9DFC9;margin-bottom:16px;font-weight:500}
.flash.error{background:#FDEBDD;border-color:#F0BFB2;color:#7A2112}
/* đăng nhập */
.auth{display:grid;grid-template-columns:1.05fr 1fr;max-width:920px;margin:24px auto;border-radius:22px;overflow:hidden;border:1px solid var(--line);background:#fff}
.auth-side{background:var(--brand-d);color:#fff;padding:38px 34px}.auth-side h1{font-size:30px;margin:18px 0 22px}.auth-side ul{margin:0;padding:0;list-style:none;color:#FADCE8}.auth-side li{padding:10px 0;border-top:1px solid rgba(255,255,255,.15)}
.auth-side .mark{background:#FFD6E5;color:var(--brand-d);width:46px;height:46px;font-size:24px;border-radius:13px}
.auth-form{padding:38px 34px;display:flex;flex-direction:column;justify-content:center}.auth-form h2{font-size:24px;margin-bottom:4px}
footer{text-align:center;color:#8E7A84;font-size:12px;padding:6px 20px 24px}
.tabbar{display:none}
@media(max-width:900px){.cols,.budget-layout{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}}
@media(max-width:700px){.kpis{grid-template-columns:1fr;gap:12px}.kpi{padding:16px 18px}.kpi-v{font-size:26px}}
@media(max-width:760px){
 .links,.top .btn:not(.light),.who-email{display:none}
 .top-in{padding:10px 16px}.wrap{padding:20px 14px 100px}h1{font-size:27px}
 .tabbar{display:grid;grid-template-columns:repeat(4,1fr);position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding:6px 6px calc(6px + env(safe-area-inset-bottom,0px));z-index:30}
 .tabbar a{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:11px;font-weight:500;color:var(--muted);text-decoration:none;padding:6px 0}
 .tabbar a.on{color:var(--brand);font-weight:600}.tabbar svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
 .tabbar .add .circle{width:46px;height:46px;border-radius:50%;background:var(--brand);color:#fff;display:grid;place-items:center;margin-top:-20px;box-shadow:0 4px 14px rgba(214,51,108,.4)}
 .hero{padding:22px 20px 20px;border-radius:18px}.panel{padding:18px}
 .auth{grid-template-columns:1fr;margin:8px auto}.auth-side{padding:24px 22px}.auth-side ul{display:none}.auth-side h1{font-size:24px;margin:12px 0 0}.auth-form{padding:26px 22px}
 .tx{grid-template-columns:42px 1fr auto}.tx .actions{grid-column:2/-1}
 .month input{max-width:150px}
}
@media(max-width:440px){.brand{font-size:16px;gap:8px}.filters,.two{grid-template-columns:1fr}.cat-row b{min-width:0}}
@media(prefers-reduced-motion:reduce){.seg{animation:none}}
</style></head><body>
{% set p=request.path %}
<header class="top"><div class="top-in"><a class="brand" href="/"><span class="mark" aria-hidden="true">₫</span>Chi Tiêu Thông Minh</a>
{% if g.user %}<nav class="links" aria-label="Điều hướng chính"><a href="/" class="{{ 'on' if p=='/' }}">Tổng quan</a><a href="/transactions" class="{{ 'on' if p.startswith('/transactions') and p!='/transactions/new' }}">Giao dịch</a><a href="/budgets" class="{{ 'on' if p.startswith('/budgets') }}">Ngân sách</a></nav><a class="btn" href="/transactions/new">Thêm giao dịch</a><div class="who"><span class="who-email">{{ g.user.email }}</span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn light sm">Đăng xuất</button></form></div>
{% else %}<nav class="links" aria-label="Tài khoản"><a href="/login" class="{{ 'on' if p=='/login' or p=='/' }}">Đăng nhập</a><a href="/register" class="{{ 'on' if p=='/register' }}">Đăng ký</a></nav>{% endif %}</div></header>
<main class="wrap">{% with msgs=get_flashed_messages(with_categories=true) %}{% for typ,msg in msgs %}<div class="flash {{ typ if typ in ('error','warn') else '' }}" role="{{ 'alert' if typ in ('error','warn') else 'status' }}">{{ msg }}</div>{% endfor %}{% endwith %}{% block body %}{% endblock %}</main>
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
{% if b_over or b_warn %}<a class="alert-bar {{ 'over' if b_over else 'warn' }}" href="/budgets?month={{ month }}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/></svg><span>{% if b_over %}<b>{{ b_over|length }} danh mục vượt ngân sách:</b> {{ b_over|map(attribute='name')|join(', ') }}.{% endif %}{% if b_warn %} <b>{{ b_warn|length }} danh mục đã dùng từ 80%:</b> {{ b_warn|map(attribute='name')|join(', ') }}.{% endif %}</span><span class="link">Xem chi tiết</span></a>{% endif %}
<section class="hero" aria-labelledby="bal-label">
<div class="hero-top"><span id="bal-label">Số dư hiện tại</span><span class="pill">Tất cả giao dịch</span></div>
<div class="big num{{ ' neg' if balance<0 }}">{{ balance|money }}</div>
<div class="split" role="img" aria-label="Tỷ lệ thu so với chi trong tháng">{% if income+expense>0 %}{% if income>0 %}<i class="seg in" style="flex-grow:{{ income }}"></i>{% endif %}{% if expense>0 %}<i class="seg out" style="flex-grow:{{ expense }}"></i>{% endif %}{% else %}<i class="seg none" style="flex-grow:1"></i>{% endif %}</div>
<div class="hero-foot"><div class="lg2"><span><i class="sw in"></i>Thu tháng {{ month|vmonth }}</span><span><i class="sw out"></i>Chi</span></div>
<div class="note">{% if income>0 %}{% if bal>=0 %}Giữ lại {{ ((income-expense)/income*100)|round|int }}% thu nhập trong tháng{% else %}Chi vượt thu {{ (expense-income)|money }}{% endif %}{% else %}Chưa có khoản thu nào trong tháng{% endif %}</div></div></section>
{% macro dl(d) %}<div class="delta {{ d.tone }}">{% if d.dir in ('up','down') %}<svg class="{{ d.dir }}" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 2l4.5 7h-9z"/></svg><span class="sr">{{ 'Tăng' if d.dir=='up' else 'Giảm' }}</span>{% endif %}<span>{{ d.text }}</span></div>{% endmacro %}
<div class="kpis">
<article class="kpi"><div class="kpi-h"><i class="sw bi"></i>Tổng thu trong tháng</div><b class="num kpi-v">{{ income|money }}</b>{{ dl(d_income) }}</article>
<article class="kpi"><div class="kpi-h"><i class="sw be"></i>Tổng chi trong tháng</div><b class="num kpi-v">{{ expense|money }}</b>{{ dl(d_expense) }}</article>
<article class="kpi"><div class="kpi-h"><i class="sw" style="background:var(--brand)"></i>Còn lại trong tháng</div><b class="num kpi-v">{{ bal|money }}</b>{{ dl(d_net) }}</article>
</div>
<section class="panel" aria-labelledby="ch-title">
<div class="panel-h"><div><h2 id="ch-title">Thu chi theo thời gian</h2><div class="muted small" id="chart-period"></div></div>
<div class="tabs2" role="group" aria-label="Chọn khoảng thời gian"><button type="button" data-mode="day" aria-pressed="true">Ngày</button><button type="button" data-mode="month" aria-pressed="false">Tháng</button><button type="button" data-mode="year" aria-pressed="false">Năm</button></div></div>
<div class="chart-top"><div class="lg"><span><i class="sw bi"></i>Thu</span><span><i class="sw be"></i>Chi</span></div><div class="chart-sum"><span>Thu <b class="num in" id="cs-in"></b></span><span>Chi <b class="num out" id="cs-out"></b></span></div></div>
<div class="chart-wrap"><div class="chart" id="chart"></div><div class="tip" id="chart-tip" hidden></div></div>
<script type="application/json" id="chart-data">{{ chart|tojson }}</script>
</section>
<div class="cols">
<section class="panel"><div class="panel-h"><h2>Chi tiêu theo danh mục</h2></div>
{% if bycat %}<ul class="cats">{% for x in bycat %}<li><div class="cat-row"><span class="dot" style="background:{{ cat_color(x.id) }}"></span><span class="cat-name">{{ x.name }}</span><span class="cat-share">{{ (x.total/expense*100)|round|int }}%</span><b class="num">{{ x.total|money }}</b></div><div class="bar"><div class="fill" style="width:{{ (x.total/maxcat*100)|round }}%;background:{{ cat_color(x.id) }}"></div></div></li>{% endfor %}</ul>
{% else %}<div class="empty">Chưa có khoản chi nào trong tháng này.<br><a class="btn" href="/transactions/new">Thêm giao dịch</a></div>{% endif %}</section>
<section class="panel"><div class="panel-h"><h2>Ngân sách tháng {{ month|vmonth }}</h2><a class="link" href="/budgets?month={{ month }}">Quản lý ngân sách</a></div>
{% if bitems %}{% for b in bitems %}<div class="bud"><div class="bud-top"><span class="dot" style="background:{{ cat_color(b.category_id) }}"></span><b>{{ b.name }}</b><span class="bud-pct num {{ b.state }}">{{ b.pct_i }}%</span></div><div class="bar mark80"><div class="fill {{ b.state }}" style="width:{{ b.width }}%"></div></div><div class="bud-foot"><span>{{ b.spent|money }} / {{ b.limit|money }}</span>{% if b.msg %}<span class="msg {{ b.state }}">{{ b.msg }}</span>{% else %}<span>Còn lại {{ b.remaining|money }}</span>{% endif %}</div></div>{% endfor %}<div class="legend"><i></i>Vạch đứng là mốc cảnh báo 80%</div>
{% else %}<div class="empty">Chưa đặt ngân sách cho tháng này.<br><a class="btn light" href="/budgets?month={{ month }}">Đặt ngân sách</a></div>{% endif %}</section></div>
<section class="panel" style="margin-top:18px"><div class="panel-h"><h2>Giao dịch gần đây</h2><a class="link" href="/transactions?month={{ month }}">Xem tất cả</a></div>
{% if recent %}<ul class="txs">{% for t in recent %}<li class="tx"><span class="ico" style="--c:{{ cat_color(t.category_id) }}" aria-hidden="true">{{ t.category|first|upper }}</span><div class="tx-main"><b>{{ t.note or t.category }}</b><div class="tx-meta"><span>{{ t.category }}</span><span>{{ t.tx_date|vdate }}</span></div></div><span class="tx-amt {{ 'in' if t.kind=='income' else 'out' }}">{{ '+' if t.kind=='income' else '−' }}{{ t.amount|money }}</span><div class="actions"><a class="link" href="/transactions/{{ t.id }}/edit">Sửa</a></div></li>{% endfor %}</ul>
{% else %}<div class="empty">Chưa có giao dịch nào.<br><a class="btn" href="/transactions/new">Thêm giao dịch đầu tiên</a></div>{% endif %}</section><script>(function(){
var data=JSON.parse(document.getElementById('chart-data').textContent),box=document.getElementById('chart'),tip=document.getElementById('chart-tip'),mode='day',layout=null,lh=280;
function vnd(v){return Number(v).toLocaleString('vi-VN')+' đ'}
function short(v){if(v>=1e9)return String(+(v/1e9).toFixed(1)).replace('.',',')+' tỷ';if(v>=1e6)return String(+(v/1e6).toFixed(1)).replace('.',',')+' tr';if(v>=1e3)return Math.round(v/1e3)+'k';return String(Math.round(v))}
function nice(m){var raw=m/4,p=Math.pow(10,Math.floor(Math.log(raw)/Math.LN10)),n=raw/p,c=[1,1.5,2,2.5,3,4,5,6,8,10];for(var i=0;i<c.length;i++){if(n<=c[i]+1e-9)return c[i]*p*4}return 40*p}
function sum(a){return a.reduce(function(x,y){return x+y},0)}
function draw(){
 var d=data[mode],n=d.labels.length,w=box.clientWidth||320,h=w<520?220:280;lh=h;var pl=46,pr=8,pt=10,pb=26,pw=w-pl-pr,ph=h-pt-pb;
 document.getElementById('chart-period').textContent=d.period;
 document.getElementById('cs-in').textContent=vnd(sum(d.income));document.getElementById('cs-out').textContent=vnd(sum(d.expense));
 tip.hidden=true;
 var mx=Math.max.apply(null,d.income.concat(d.expense));
 if(!(mx>0)){box.innerHTML='<div class="empty">Chưa có dữ liệu cho khoảng thời gian này.</div>';layout=null;return}
 var top=nice(mx),g=pw/n,bw=Math.max(3,Math.min(20,g*0.36)),s='<svg width="'+w+'" height="'+h+'" role="img" aria-label="Biểu đồ cột so sánh thu và chi">';
 layout=[];
 for(var i=0;i<=4;i++){var y=pt+ph-ph*i/4;s+='<line class="gl" x1="'+pl+'" x2="'+(w-pr)+'" y1="'+y+'" y2="'+y+'"/><text class="ax" x="'+(pl-8)+'" y="'+(y+4)+'" text-anchor="end">'+short(top*i/4)+'</text>'}
 for(var k=0;k<n;k++){
  var cx=pl+g*k+g/2,hi=d.income[k]>0?Math.max(2,ph*d.income[k]/top):0,he=d.expense[k]>0?Math.max(2,ph*d.expense[k]/top):0;
  layout.push({cx:cx,y:pt+ph-Math.max(hi,he)});
  s+='<rect class="hit" data-i="'+k+'" x="'+(pl+g*k)+'" y="'+pt+'" width="'+g+'" height="'+ph+'"/>';
  if(hi)s+='<rect class="bi" x="'+(cx-bw-1)+'" y="'+(pt+ph-hi)+'" width="'+bw+'" height="'+hi+'" rx="2.5" pointer-events="none"/>';
  if(he)s+='<rect class="be" x="'+(cx+1)+'" y="'+(pt+ph-he)+'" width="'+bw+'" height="'+he+'" rx="2.5" pointer-events="none"/>';
  if(n<=16||k===0||(k+1)%5===0)s+='<text class="ax" x="'+cx+'" y="'+(h-8)+'" text-anchor="middle">'+d.labels[k]+'</text>';
 }
 box.innerHTML=s+'</svg>';
}
function show(e){
 var t=e.target;if(!t.classList||!t.classList.contains('hit')||!layout)return;
 var i=+t.getAttribute('data-i'),d=data[mode],p=layout[i],w=box.clientWidth;
 [].forEach.call(box.querySelectorAll('.hit.on'),function(x){x.classList.remove('on')});t.classList.add('on');
 tip.innerHTML='<b>'+d.tips[i]+'</b><i style="background:#7EE2B8"></i>Thu '+vnd(d.income[i])+'<br><i style="background:#FFB07A"></i>Chi '+vnd(d.expense[i]);
 tip.hidden=false;tip.style.left=Math.min(Math.max(p.cx,80),w-80)+'px';tip.style.top=Math.max(Math.min(p.y-8,lh*0.62),60)+'px';
}
function hide(e){tip.hidden=true;[].forEach.call(box.querySelectorAll('.hit.on'),function(x){x.classList.remove('on')})}
box.addEventListener('pointermove',show);box.addEventListener('pointerdown',show);
box.addEventListener('pointerleave',function(e){if(e.pointerType==='mouse')hide()});
document.addEventListener('pointerdown',function(e){if(!box.contains(e.target))hide()});
[].forEach.call(document.querySelectorAll('.tabs2 button'),function(b){b.addEventListener('click',function(){mode=b.getAttribute('data-mode');[].forEach.call(document.querySelectorAll('.tabs2 button'),function(x){x.setAttribute('aria-pressed',x===b?'true':'false')});draw()})});
if(window.ResizeObserver){new ResizeObserver(draw).observe(box)}else{window.addEventListener('resize',draw)}
draw();
})();</script>{% endblock %}"""

LIST = r"""{% extends base %}{% block body %}
<div class="head"><div><h1>Giao dịch</h1><p class="muted" style="margin:0">Tìm kiếm, lọc theo thời gian, danh mục, loại thu chi và quản lý từng khoản.</p></div><div class="head-actions"><a class="btn light" href="{{ export_url }}">Xuất CSV</a><a class="btn" href="/transactions/new?next={{ back|urlencode }}">Thêm giao dịch</a></div></div>
<form class="panel fpanel" method="get" action="/transactions">
<div class="presets" role="group" aria-label="Khoảng thời gian nhanh">{% for p in presets %}<a class="preset{{ ' on' if p.active }}" href="{{ p.url }}"{% if p.active %} aria-current="true"{% endif %}>{{ p.label }}</a>{% endfor %}</div>
<div class="filters">
<div><label for="f-from">Từ ngày</label><input id="f-from" type="date" name="date_from" value="{{ f.date_from }}" onchange="this.form.submit()"></div>
<div><label for="f-to">Đến ngày</label><input id="f-to" type="date" name="date_to" value="{{ f.date_to }}" onchange="this.form.submit()"></div>
<div><label for="f-kind">Loại</label><select id="f-kind" name="kind" onchange="this.form.submit()"><option value="">Tất cả</option><option value="income" {{ 'selected' if f.kind=='income' }}>Thu</option><option value="expense" {{ 'selected' if f.kind=='expense' }}>Chi</option></select></div>
<div><label for="f-cat">Danh mục</label><select id="f-cat" name="category_id" onchange="this.form.submit()"><option value="">Tất cả</option>{% for c in cats %}<option value="{{ c.id }}" {{ 'selected' if f.category_id==c.id|string }}>{{ c.name }}</option>{% endfor %}</select></div>
<div><label for="f-sort">Sắp xếp</label><select id="f-sort" name="sort" onchange="this.form.submit()"><option value="date_desc" {{ 'selected' if f.sort=='date_desc' }}>Mới nhất trước</option><option value="date_asc" {{ 'selected' if f.sort=='date_asc' }}>Cũ nhất trước</option><option value="amount_desc" {{ 'selected' if f.sort=='amount_desc' }}>Số tiền cao đến thấp</option><option value="amount_asc" {{ 'selected' if f.sort=='amount_asc' }}>Số tiền thấp đến cao</option></select></div>
<div><label for="f-q">Tìm kiếm</label><input id="f-q" name="q" value="{{ f.q }}" maxlength="100" placeholder="Ghi chú hoặc tên danh mục"></div>
<div class="f-actions"><button class="btn">Lọc</button><a class="btn light" href="{{ clear_url }}">Xóa lọc</a></div></div></form>
{% if chips %}<div class="chips-active"><span class="muted small">Đang lọc:</span>{% for c in chips %}<a class="chip-x" href="{{ c.url }}" aria-label="Bỏ lọc {{ c.label }}">{{ c.label }}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></a>{% endfor %}<a class="link small" href="{{ clear_url }}">Xóa tất cả</a></div>{% endif %}
<section class="panel"><div class="sum"><span class="muted">{{ total }} giao dịch{% if total %}, đang xem {{ start }} đến {{ end }}{% endif %}</span><span class="in num">Thu +{{ tot_in|money }}</span><span class="out num">Chi −{{ tot_out|money }}</span></div>
{% if rows %}<ul class="txs">{% for t in rows %}<li class="tx"><span class="ico" style="--c:{{ cat_color(t.category_id) }}" aria-hidden="true">{{ t.category|first|upper }}</span><div class="tx-main"><b>{{ t.note or t.category }}</b><div class="tx-meta"><span>{{ t.category }}</span><span>{{ t.tx_date|vdate }}</span></div></div><span class="tx-amt {{ 'in' if t.kind=='income' else 'out' }}">{{ '+' if t.kind=='income' else '−' }}{{ t.amount|money }}</span><div class="actions"><a class="btn light sm" href="/transactions/{{ t.id }}/edit?next={{ back|urlencode }}">Sửa</a><form method="post" action="/transactions/{{ t.id }}/delete" onsubmit="return confirm('Xóa giao dịch này?')"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><input type="hidden" name="next" value="{{ back }}"><button class="btn danger sm">Xóa</button></form></div></li>{% endfor %}</ul>
{% if pages>1 %}<nav class="pager" aria-label="Phân trang">{% if page_no>1 %}<a class="pg" href="{{ prev_url }}">Trước</a>{% endif %}{% for it in page_items %}{% if it.gap %}<span class="pg gap">…</span>{% else %}<a class="pg{{ ' on' if it.current }}" href="{{ it.url }}"{% if it.current %} aria-current="page"{% endif %}>{{ it.n }}</a>{% endif %}{% endfor %}{% if page_no<pages %}<a class="pg" href="{{ next_url }}">Sau</a>{% endif %}</nav>{% endif %}
{% elif chips %}<div class="empty">Không có giao dịch nào khớp bộ lọc.<br><a class="btn light" href="{{ clear_url }}">Xóa bộ lọc</a></div>
{% else %}<div class="empty">Chưa có giao dịch nào.<br><a class="btn" href="/transactions/new">Thêm giao dịch đầu tiên</a></div>{% endif %}</section>{% endblock %}"""

FORM = r"""{% extends base %}{% block body %}
<nav class="crumbs" aria-label="Đường dẫn"><a href="{{ back_url }}">Giao dịch</a><span aria-hidden="true">/</span><span aria-current="page">{{ 'Sửa giao dịch' if row else 'Thêm giao dịch mới' }}</span></nav>
<div class="head head-form"><div><h1 class="caps">{{ 'SỬA GIAO DỊCH' if row else 'THÊM GIAO DỊCH MỚI' }}</h1><p class="muted lead">{{ 'Cập nhật thông tin khoản thu hoặc chi này.' if row else 'Ghi nhận khoản thu và chi để theo dõi dòng tiền, kiểm soát ngân sách và quản lý tài chính hiệu quả hơn.' }}</p></div><a class="btn light" href="{{ back_url }}">Quay lại</a></div>
<form class="panel formcard" method="post"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
<div class="field"><div class="seg2" role="radiogroup" aria-label="Loại giao dịch"><label><input type="radio" name="kind" value="expense" {{ 'checked' if not row or row.kind=='expense' }}><span>Chi tiêu</span></label><label><input type="radio" name="kind" value="income" {{ 'checked' if row and row.kind=='income' }}><span>Thu nhập</span></label></div></div>
<div class="field"><label for="amount">Số tiền (VND) *</label><div class="amount-box"><input class="amount" id="amount" name="amount" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" required value="{{ row.amount if row else '' }}" placeholder="150000"><span class="unit">đ</span></div><div class="quick" aria-label="Cộng nhanh"><button type="button" data-add="50000">+50k</button><button type="button" data-add="100000">+100k</button><button type="button" data-add="500000">+500k</button><button type="button" data-add="1000000">+1tr</button><span class="amt-fmt" id="amt-fmt" aria-live="polite"></span></div></div>
<div class="field"><span class="sr" id="cat-label">Danh mục</span><label aria-hidden="true">Danh mục *</label><div class="chips" role="radiogroup" aria-labelledby="cat-label">{% for c in cats %}<label><input type="radio" name="category_id" value="{{ c.id }}" data-kind="{{ c.kind }}" required {{ 'checked' if row and row.category_id==c.id }}><span><i class="dot" style="background:{{ cat_color(c.id) }}"></i>{{ c.name }}</span></label>{% endfor %}</div></div>
<div class="two field"><div><label for="tx_date">Ngày *</label><input id="tx_date" name="tx_date" type="date" required value="{{ row.tx_date if row else today }}"></div></div>
<div class="field"><label for="note">Ghi chú</label><textarea id="note" name="note" rows="3" maxlength="300" placeholder="Ví dụ: ăn trưa với đồng nghiệp">{{ row.note if row else '' }}</textarea></div>
<div class="form-actions"><a class="btn light" href="{{ back_url }}">Hủy</a><button class="btn">Lưu giao dịch</button></div></form>
<script>(function(){var kinds=document.querySelectorAll('input[name=kind]'),chips=document.querySelectorAll('.chips label');
function sync(){var k=document.querySelector('input[name=kind]:checked').value,has=false;chips.forEach(function(l){var r=l.querySelector('input'),ok=r.dataset.kind===k;l.hidden=!ok;if(!ok)r.checked=false;else if(r.checked)has=true});if(!has){for(var i=0;i<chips.length;i++){if(!chips[i].hidden){chips[i].querySelector('input').checked=true;break}}}}
kinds.forEach(function(r){r.addEventListener('change',sync)});sync();
var a=document.getElementById('amount'),o=document.getElementById('amt-fmt');
function fmt(){var d=a.value.replace(/[^0-9]/g,'');o.textContent=d?Number(d).toLocaleString('vi-VN')+' đ':''}
a.addEventListener('input',fmt);fmt();
document.querySelectorAll('[data-add]').forEach(function(b){b.addEventListener('click',function(){var d=a.value.replace(/[^0-9]/g,'');a.value=String(Number(d||0)+Number(b.dataset.add));fmt();a.focus()})});})();</script>{% endblock %}"""

BUDGETS = r"""{% extends base %}{% block body %}
<div class="head"><div><h1>Ngân sách</h1><p class="muted" style="margin:0">Đặt hạn mức chi theo tháng và danh mục, theo dõi mức đã dùng.</p></div>
<div class="month"><a href="/budgets?month={{ prev_month }}" aria-label="Tháng trước"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a><form method="get"><input type="month" name="month" value="{{ month }}" onchange="this.form.submit()" aria-label="Chọn tháng"><noscript><button class="btn sm">Xem</button></noscript></form><a href="/budgets?month={{ next_month }}" aria-label="Tháng sau"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></a></div></div>
{% if items %}
<section class="panel bsum" aria-label="Tổng quan ngân sách tháng {{ month|vmonth }}"><div class="bsum-top"><div><div class="muted">Tổng ngân sách tháng {{ month|vmonth }}</div><div class="num bsum-v">{{ total.limit|money }}</div></div><div class="bsum-pct num {{ total.state }}">{{ total.pct_i }}%</div></div>
<div class="bar thick mark80"><div class="fill {{ total.state }}" style="width:{{ total.width }}%"></div></div>
<div class="bsum-foot"><span>Đã chi <b class="num">{{ total.spent|money }}</b></span><span>{% if total.remaining>=0 %}Còn lại <b class="num">{{ total.remaining|money }}</b>{% else %}Chi vượt <b class="num">{{ (0-total.remaining)|money }}</b>{% endif %}</span></div></section>
{% if over or warn %}<section class="panel alerts" aria-label="Cảnh báo ngân sách"><h2>Cần chú ý</h2><ul>
{% for b in over %}<li class="al over"><i class="dot" style="background:{{ cat_color(b.category_id) }}"></i><b>{{ b.name }}</b><span>{% if b.over>0 %}vượt kế hoạch {{ b.over|money }} ({{ b.pct_i }}%){% else %}đã dùng hết ngân sách (100%){% endif %}</span></li>{% endfor %}
{% for b in warn %}<li class="al warn"><i class="dot" style="background:{{ cat_color(b.category_id) }}"></i><b>{{ b.name }}</b><span>đã dùng {{ b.pct_i }}%, còn {{ b.remaining|money }}</span></li>{% endfor %}</ul></section>{% endif %}
{% endif %}
<div class="budget-layout">
<section class="panel"><h2 style="margin-bottom:16px">{{ 'Sửa ngân sách' if edit else 'Thiết lập ngân sách' }}</h2><form method="post"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
{% if edit %}<div class="field"><label for="b-month-ro">Tháng</label><input id="b-month-ro" value="{{ month|vmonth }}" disabled><input type="hidden" name="month" value="{{ month }}"></div>
<div class="field"><label for="b-cat-ro">Danh mục chi</label><input id="b-cat-ro" value="{{ edit.name }}" disabled><input type="hidden" name="category_id" value="{{ edit.category_id }}"></div>
{% else %}<div class="field"><label for="b-month">Tháng</label><input id="b-month" type="month" name="month" value="{{ month }}" required></div>
<div class="field"><label for="b-cat">Danh mục chi</label><select id="b-cat" name="category_id" required>{% for c in cats %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></div>{% endif %}
<div class="field"><label for="b-limit">Hạn mức (VND)</label><input id="b-limit" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" name="limit_amount" required placeholder="2000000" value="{{ edit.limit_amount if edit else '' }}"></div>
<button class="btn wide">{{ 'Cập nhật ngân sách' if edit else 'Lưu ngân sách' }}</button>{% if edit %}<a class="btn light wide" style="margin-top:10px" href="/budgets?month={{ month }}">Hủy</a>{% endif %}
<p class="muted small" style="margin:12px 0 0">Đặt lại cho cùng danh mục và tháng sẽ cập nhật hạn mức cũ. Cảnh báo xuất hiện khi dùng từ 80% và khi đạt hoặc vượt 100%.</p></form>
{% if not edit %}<form method="post" action="/budgets/copy" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--line)"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><input type="hidden" name="month" value="{{ month }}"><button class="btn light wide">Sao chép từ tháng {{ prev_month|vmonth }}</button></form>{% endif %}</section>
<div>{% if items %}{% for b in items %}<article class="bcard"><div class="bcard-top"><span class="ico" style="--c:{{ cat_color(b.category_id) }}" aria-hidden="true">{{ b.name|first|upper }}</span><div class="bcard-main"><b>{{ b.name }}</b><span class="muted small num">{{ b.spent|money }} / {{ b.limit|money }}</span></div><span class="bud-pct num {{ b.state }}">{{ b.pct_i }}%</span></div>
<div class="bar mark80"><div class="fill {{ b.state }}" style="width:{{ b.width }}%"></div></div>
<div class="bud-foot"><span>{% if b.remaining>=0 %}Còn lại {{ b.remaining|money }}{% else %}Vượt {{ b.over|money }}{% endif %}{% if b.msg %} <span class="msg {{ b.state }}">{{ b.msg }}</span>{% endif %}</span>
<div class="actions"><a class="btn light sm" href="/budgets?month={{ month }}&edit={{ b.id }}">Sửa</a><form method="post" action="/budgets/{{ b.id }}/delete" onsubmit="return confirm('Xóa ngân sách?')" style="margin:0"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger sm">Xóa</button></form></div></div></article>{% endfor %}<div class="legend"><i></i>Vạch đứng là mốc cảnh báo 80%</div>
{% else %}<div class="panel empty">Chưa có ngân sách cho tháng này.<br>Chọn danh mục và hạn mức ở khung bên cạnh, hoặc sao chép từ tháng trước.</div>{% endif %}</div></div>{% endblock %}"""

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

def delta(cur, prev, up_is_good):
    """So sánh với kỳ trước: trả về hướng mũi tên, sắc thái (tốt/xấu/trung tính) và câu mô tả."""
    if cur == 0 and prev == 0:
        return {"dir": "none", "tone": "flat", "text": "Không có số liệu"}
    if prev == 0:
        return {"dir": "none", "tone": "flat", "text": "Chưa có số liệu tháng trước"}
    pct = round((cur - prev) / abs(prev) * 100)
    if pct == 0:
        return {"dir": "none", "tone": "flat", "text": "Không đổi so với tháng trước"}
    up = pct > 0
    return {"dir": "up" if up else "down", "tone": "good" if up == up_is_good else "bad",
            "text": f"{abs(pct)}% so với tháng trước"}

def chart_data(c, u, month):
    """Số liệu thu/chi theo ngày (trong tháng), theo tháng (trong năm) và theo năm cho biểu đồ."""
    year, mon = int(month[:4]), int(month[5:7])
    ndays = calendar.monthrange(year, mon)[1]
    def put(rows, key, inc, exp, offset):
        for r in rows:
            i = r[key] - offset
            if 0 <= i < len(inc):
                (inc if r["kind"] == "income" else exp)[i] = r["s"]
    d_inc, d_exp = [0]*ndays, [0]*ndays
    put(c.execute("SELECT CAST(substr(tx_date,9,2) AS INTEGER) k,kind,SUM(amount) s FROM transactions WHERE user_id=? AND substr(tx_date,1,7)=? GROUP BY k,kind", (u, month)).fetchall(), "k", d_inc, d_exp, 1)
    m_inc, m_exp = [0]*12, [0]*12
    put(c.execute("SELECT CAST(substr(tx_date,6,2) AS INTEGER) k,kind,SUM(amount) s FROM transactions WHERE user_id=? AND substr(tx_date,1,4)=? GROUP BY k,kind", (u, str(year))).fetchall(), "k", m_inc, m_exp, 1)
    yrows = c.execute("SELECT CAST(substr(tx_date,1,4) AS INTEGER) k,kind,SUM(amount) s FROM transactions WHERE user_id=? GROUP BY k,kind", (u,)).fetchall()
    years = sorted({r["k"] for r in yrows} | {year})[-8:]
    y_inc, y_exp = [0]*len(years), [0]*len(years)
    for r in yrows:
        if r["k"] in years:
            (y_inc if r["kind"] == "income" else y_exp)[years.index(r["k"])] = r["s"]
    return {
        "day":   {"period": f"Tháng {mon:02d}/{year}", "labels": [str(i) for i in range(1, ndays+1)], "tips": [f"Ngày {i:02d}/{mon:02d}/{year}" for i in range(1, ndays+1)], "income": d_inc, "expense": d_exp},
        "month": {"period": f"Năm {year}", "labels": [f"T{i}" for i in range(1, 13)], "tips": [f"Tháng {i:02d}/{year}" for i in range(1, 13)], "income": m_inc, "expense": m_exp},
        "year":  {"period": "Các năm có dữ liệu", "labels": [str(y) for y in years], "tips": [f"Năm {y}" for y in years], "income": y_inc, "expense": y_exp},
    }

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

def safe_next(n, default=None):
    return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else (default or url_for("dashboard"))

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

def budget_view(rows):
    """Chuẩn hóa dữ liệu ngân sách: phần trăm, trạng thái, còn lại/vượt, tổng hợp và danh sách cần cảnh báo."""
    items = []
    for r in rows:
        limit, spent = r["limit_amount"], r["spent"]
        pct = spent / limit * 100
        if spent > limit: msg = "Đã vượt ngân sách"
        elif spent == limit: msg = "Đã dùng hết ngân sách"
        elif pct >= 80: msg = "Đã dùng ít nhất 80% ngân sách"
        else: msg = ""
        items.append({"id": r["id"], "name": r["name"], "category_id": r["category_id"], "spent": spent, "limit": limit,
                      "pct": pct, "pct_i": round(pct), "state": "over" if pct >= 100 else ("warn" if pct >= 80 else "ok"),
                      "remaining": limit - spent, "over": max(0, spent - limit), "msg": msg, "width": round(min(pct, 100), 1)})
    items.sort(key=lambda i: -i["pct"])
    tl, ts = sum(i["limit"] for i in items), sum(i["spent"] for i in items)
    tp = ts / tl * 100 if tl else 0
    total = {"limit": tl, "spent": ts, "remaining": tl - ts, "pct_i": round(tp), "width": round(min(tp, 100), 1),
             "state": "over" if tp >= 100 else ("warn" if tp >= 80 else "ok")}
    return {"items": items, "total": total,
            "over": [i for i in items if i["state"] == "over"], "warn": [i for i in items if i["state"] == "warn"]}

def budget_warning(c, user_id, tx_date, category_id):
    """Sau khi lưu một khoản chi: trả về câu cảnh báo nếu danh mục đó đã dùng từ 80% ngân sách tháng."""
    month = tx_date[:7]
    r = c.execute("""SELECT b.limit_amount,ca.name,COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.user_id=b.user_id AND t.category_id=b.category_id AND t.kind='expense' AND substr(t.tx_date,1,7)=b.month),0) spent FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.user_id=? AND b.month=? AND b.category_id=?""", (user_id, month, category_id)).fetchone()
    if not r: return None
    limit, spent, vm = r["limit_amount"], r["spent"], vmonth(month)
    if spent > limit: return f"Cảnh báo: {r['name']} đã vượt ngân sách tháng {vm} là {money(spent - limit)}."
    if spent == limit: return f"Cảnh báo: {r['name']} đã dùng hết ngân sách tháng {vm}."
    if spent / limit * 100 >= 80: return f"Cảnh báo: {r['name']} đã dùng {round(spent / limit * 100)}% ngân sách tháng {vm}."
    return None

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
    bv = budget_view(budgets)
    prev = shift_month(month, -1)
    with db() as c:
        balance = c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE -amount END),0) FROM transactions WHERE user_id=?", (u,)).fetchone()[0]
        p = c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE 0 END),0) i, COALESCE(SUM(CASE WHEN kind='expense' THEN amount ELSE 0 END),0) e FROM transactions WHERE user_id=? AND substr(tx_date,1,7)=?", (u, prev)).fetchone()
        chart = chart_data(c, u, month)
    d_income = delta(totals["income"], p["i"], True)
    d_expense = delta(totals["expense"], p["e"], False)
    d_net = delta(totals["income"] - totals["expense"], p["i"] - p["e"], True)
    return page(DASH, title="Tổng quan", balance=balance, d_income=d_income, d_expense=d_expense, d_net=d_net, chart=chart,month=month,income=totals["income"],expense=totals["expense"],bycat=bycat,maxcat=max([x["total"] for x in bycat],default=1),recent=recent,bitems=bv["items"],b_over=bv["over"],b_warn=bv["warn"],prev_month=shift_month(month,-1),next_month=shift_month(month,1))

PER_PAGE = 20
SORTS = {"date_desc": "t.tx_date DESC,t.id DESC", "date_asc": "t.tx_date ASC,t.id ASC",
         "amount_desc": "t.amount DESC,t.id DESC", "amount_asc": "t.amount ASC,t.id ASC"}
TX_FROM = "FROM transactions t JOIN categories ca ON ca.id=t.category_id"

def valid_date(v):
    try: datetime.strptime(v, "%Y-%m-%d"); return True
    except (ValueError, TypeError): return False

def parse_filters(args):
    """Đọc và làm sạch bộ lọc từ URL. Tham số cũ ?month=YYYY-MM được đổi thành khoảng ngày."""
    kind = args.get("kind", ""); kind = kind if kind in ("income", "expense") else ""
    cat = args.get("category_id", ""); cat = cat if cat.isdigit() and len(cat) <= 9 else ""
    q = args.get("q", "").strip()[:100]
    d_from, d_to = args.get("date_from", ""), args.get("date_to", "")
    d_from = d_from if valid_date(d_from) else ""; d_to = d_to if valid_date(d_to) else ""
    if not d_from and not d_to:
        month = args.get("month", "")
        try:
            datetime.strptime(month, "%Y-%m")
            d_from, d_to = f"{month}-01", f"{month}-{calendar.monthrange(int(month[:4]), int(month[5:7]))[1]:02d}"
        except (ValueError, TypeError):
            pass
    if d_from and d_to and d_from > d_to: d_from, d_to = d_to, d_from
    sort = args.get("sort", ""); sort = sort if sort in SORTS else "date_desc"
    return {"kind": kind, "category_id": cat, "q": q, "date_from": d_from, "date_to": d_to, "sort": sort}

def filter_sql(f, user_id):
    where, params = "t.user_id=?", [user_id]
    if f["date_from"]: where += " AND t.tx_date>=?"; params.append(f["date_from"])
    if f["date_to"]: where += " AND t.tx_date<=?"; params.append(f["date_to"])
    if f["kind"]: where += " AND t.kind=?"; params.append(f["kind"])
    if f["category_id"]: where += " AND t.category_id=?"; params.append(int(f["category_id"]))
    if f["q"]:
        like = "%" + f["q"].lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (pylower(t.note) LIKE ? ESCAPE '\\' OR pylower(ca.name) LIKE ? ESCAPE '\\')"
        params += [like, like]
    return where, params

@app.route("/transactions")
@login_required
def tx_list():
    f = parse_filters(request.args)
    where, params = filter_sql(f, uid())
    try: page_no = max(1, int(request.args.get("page", "1")))
    except ValueError: page_no = 1
    with db() as c:
        agg = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN t.kind='income' THEN t.amount END),0) i, COALESCE(SUM(CASE WHEN t.kind='expense' THEN t.amount END),0) e {TX_FROM} WHERE {where}", params).fetchone()
        pages = max(1, -(-agg["n"] // PER_PAGE)); page_no = min(page_no, pages)
        rows = c.execute(f"SELECT t.*,ca.name category {TX_FROM} WHERE {where} ORDER BY {SORTS[f['sort']]} LIMIT ? OFFSET ?", params + [PER_PAGE, (page_no - 1) * PER_PAGE]).fetchall()
    cats = categories()

    def link(**over):
        p = {k: v for k, v in f.items() if v and not (k == "sort" and v == "date_desc")}
        p.update(over)
        return url_for("tx_list", **{k: v for k, v in p.items() if v not in ("", None)})

    cat_name = next((c["name"] for c in cats if str(c["id"]) == f["category_id"]), None)
    chips = []
    if f["date_from"]: chips.append({"label": "Từ " + vdate(f["date_from"]), "url": link(date_from="")})
    if f["date_to"]: chips.append({"label": "Đến " + vdate(f["date_to"]), "url": link(date_to="")})
    if f["kind"]: chips.append({"label": "Loại: " + ("Thu" if f["kind"] == "income" else "Chi"), "url": link(kind="")})
    if cat_name: chips.append({"label": "Danh mục: " + cat_name, "url": link(category_id="")})
    if f["q"]: chips.append({"label": "Tìm: " + f["q"], "url": link(q="")})

    today = date.today(); first = today.replace(day=1); prev_last = first - timedelta(days=1)
    presets = [("Tháng này", first.isoformat(), today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()),
               ("Tháng trước", prev_last.replace(day=1).isoformat(), prev_last.isoformat()),
               ("30 ngày qua", (today - timedelta(days=29)).isoformat(), today.isoformat()),
               ("Năm nay", f"{today.year}-01-01", f"{today.year}-12-31"),
               ("Tất cả", "", "")]
    presets = [{"label": l, "url": link(date_from=a, date_to=b), "active": f["date_from"] == a and f["date_to"] == b} for l, a, b in presets]

    page_items, last = [], 0
    for n in sorted({1, pages, page_no - 1, page_no, page_no + 1} & set(range(1, pages + 1))):
        if n - last > 1: page_items.append({"gap": True})
        page_items.append({"n": n, "url": link(page=n) if n > 1 else link(), "current": n == page_no}); last = n
    start = (page_no - 1) * PER_PAGE + 1 if agg["n"] else 0
    export_url = url_for("export_csv", **{k: v for k, v in f.items() if v and k != "sort"})
    return page(LIST, title="Giao dịch", rows=rows, cats=cats, f=f, total=agg["n"], tot_in=agg["i"], tot_out=agg["e"],
                page_no=page_no, pages=pages, page_items=page_items,
                prev_url=link(page=page_no - 1) if page_no > 2 else link(), next_url=link(page=page_no + 1),
                chips=chips, presets=presets, export_url=export_url, clear_url=url_for("tx_list"),
                start=start, end=start + len(rows) - 1 if rows else 0, back=request.full_path.rstrip("?"))

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
            warning=budget_warning(c,uid(),tx_date,int(cat)) if kind=="expense" else None
        flash("Đã lưu giao dịch.","success")
        if warning: flash(warning,"warn")
        return redirect(safe_next(request.args.get("next"), url_for("tx_list")))
    except (ValueError,TypeError,OverflowError):
        flash("Dữ liệu không hợp lệ. Kiểm tra số tiền, ngày và danh mục.","error"); return None

@app.route("/transactions/new",methods=["GET","POST"])
@login_required
def tx_new():
    if request.method=="POST":
        result=save_tx()
        if result: return result
    return page(FORM,title="Thêm giao dịch",row=None,cats=categories(),today=date.today().isoformat(),back_url=safe_next(request.args.get("next"), url_for("tx_list")))

@app.route("/transactions/<int:txid>/edit",methods=["GET","POST"])
@login_required
def tx_edit(txid):
    with db() as c: row=c.execute("SELECT * FROM transactions WHERE id=? AND user_id=?",(txid,uid())).fetchone()
    if not row: flash("Không tìm thấy giao dịch.","error"); return redirect(url_for("tx_list"))
    if request.method=="POST":
        result=save_tx(txid)
        if result: return result
        with db() as c: row=c.execute("SELECT * FROM transactions WHERE id=? AND user_id=?",(txid,uid())).fetchone()
    return page(FORM,title="Sửa giao dịch",row=row,cats=categories(),today=date.today().isoformat(),back_url=safe_next(request.args.get("next"), url_for("tx_list")))

@app.route("/transactions/<int:txid>/delete",methods=["POST"])
@login_required
def tx_delete(txid):
    with db() as c: c.execute("DELETE FROM transactions WHERE id=? AND user_id=?",(txid,uid()))
    flash("Đã xóa giao dịch.","success"); return redirect(safe_next(request.form.get("next"), url_for("tx_list")))

@app.route("/budgets",methods=["GET","POST"])
@login_required
def budget_page():
    month=month_arg(request.values.get("month",date.today().strftime("%Y-%m")))
    edit=None
    edit_id=request.args.get("edit","")
    if edit_id.isdigit() and len(edit_id)<=9:
        with db() as c:
            edit=c.execute("SELECT b.*,ca.name FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.id=? AND b.user_id=?",(int(edit_id),uid())).fetchone()
        if edit: month=edit["month"]
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
    bv=budget_view(rows)
    return page(BUDGETS,title="Ngân sách",month=month,items=bv["items"],total=bv["total"],over=bv["over"],warn=bv["warn"],edit=edit,
                cats=[x for x in categories() if x["kind"]=="expense"],prev_month=shift_month(month,-1),next_month=shift_month(month,1))

@app.route("/budgets/copy",methods=["POST"])
@login_required
def budget_copy():
    month=month_arg(request.form.get("month",""))
    prev=shift_month(month,-1)
    with db() as c:
        had=c.execute("SELECT COUNT(*) FROM budgets WHERE user_id=? AND month=?",(uid(),prev)).fetchone()[0]
        n=c.execute("INSERT INTO budgets(month,category_id,limit_amount,user_id) SELECT ?,category_id,limit_amount,user_id FROM budgets WHERE user_id=? AND month=? ON CONFLICT(user_id,month,category_id) DO NOTHING",(month,uid(),prev)).rowcount
    if not had: flash(f"Tháng {vmonth(prev)} chưa có ngân sách nào để sao chép.","error")
    elif n<=0: flash(f"Các ngân sách của tháng {vmonth(prev)} đã có đủ ở tháng này.","success")
    else: flash(f"Đã sao chép {n} ngân sách từ tháng {vmonth(prev)}.","success")
    return redirect(url_for("budget_page",month=month))

@app.route("/budgets/<int:bid>/delete",methods=["POST"])
@login_required
def budget_delete(bid):
    with db() as c: c.execute("DELETE FROM budgets WHERE id=? AND user_id=?",(bid,uid()))
    flash("Đã xóa ngân sách.","success"); return redirect(url_for("budget_page"))

def csv_safe(v):
    """Ô bắt đầu bằng = + - @ có thể bị Excel hiểu là công thức; thêm dấu nháy để luôn là văn bản."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

@app.route("/export.csv")
@login_required
def export_csv():
    f = parse_filters(request.args); where, params = filter_sql(f, uid())
    with db() as c:
        rows = c.execute(f"SELECT t.tx_date,t.kind,t.amount,ca.name category,t.note {TX_FROM} WHERE {where} ORDER BY {SORTS[f['sort']]}", params).fetchall()
    out=io.StringIO(); w=csv.writer(out); w.writerow(["Ngày","Loại","Số tiền (VND)","Danh mục","Ghi chú"])
    for r in rows: w.writerow([r["tx_date"],"Thu" if r["kind"]=="income" else "Chi",r["amount"],csv_safe(r["category"]),csv_safe(r["note"])])
    name = f"{f['date_from'] or 'dau'}_{f['date_to'] or 'nay'}" if (f["date_from"] or f["date_to"]) else "tat_ca"
    return Response("\ufeff"+out.getvalue(),content_type="text/csv; charset=utf-8",headers={"Content-Disposition":f"attachment; filename=giao_dich_{name}.csv"})

@app.route("/health")
def health(): return {"status":"ok"}

init_db()
if __name__=="__main__":
    # Có PORT (môi trường container/PaaS) -> nghe 0.0.0.0 để proxy bên ngoài (Traefik...) truy cập được.
    # Chạy local không đặt PORT -> chỉ nghe 127.0.0.1 cho an toàn.
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG")=="1")
