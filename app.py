from flask import Flask, request, redirect, url_for, flash, Response, render_template_string, session, g
import os, re, hmac, time, secrets, sqlite3, csv, io, calendar, math
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

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

def init_db():
    """Tạo hoặc nâng cấp CSDL. Cấu trúc bảng nằm trong schema.sql (nguồn duy nhất); phần dưới chỉ xử lý nâng cấp từ DB cũ."""
    try:
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Không đọc được {SCHEMA_FILE}. Hãy đặt file schema.sql cạnh app.py.") from e
    with db() as c:
        def columns(table): return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]
        tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Nâng cấp từ DB cũ (chưa có user_id): dữ liệu cũ có user_id = NULL, sẽ gán cho tài khoản đầu tiên đăng ký.
        if "transactions" in tables and "user_id" not in columns("transactions"):
            c.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        legacy_budgets = "budgets" in tables and "user_id" not in columns("budgets")
        if legacy_budgets:
            c.execute("ALTER TABLE budgets RENAME TO budgets_old")
        c.executescript(schema)
        if legacy_budgets:
            c.execute("INSERT INTO budgets(id,month,category_id,limit_amount,user_id) SELECT id,month,category_id,limit_amount,NULL FROM budgets_old")
            c.execute("DROP TABLE budgets_old")

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
.top-in{max-width:1120px;margin:0 auto;padding:11px 20px;display:flex;align-items:center;gap:12px}
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:18px;color:var(--ink);text-decoration:none;margin-right:auto;letter-spacing:-.01em;white-space:nowrap}
.mark{width:34px;height:34px;border-radius:10px;background:var(--brand);color:#fff;display:grid;place-items:center;font-weight:700;font-size:18px;flex:none}
.links{display:flex;gap:2px}
.links a{white-space:nowrap;padding:8px 12px;border-radius:999px;color:var(--muted);font-weight:500;text-decoration:none}
.links a:hover{color:var(--ink);background:var(--soft)}
.links a.on{background:var(--ink);color:#fff}
.who{display:flex;align-items:center;gap:10px}.who form{margin:0}.who-email{color:var(--muted);font-size:13px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
@media(max-width:1180px){.who-email{display:none}}
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
/* giao dịch định kỳ: lịch và trạng thái */
.st{font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap;background:var(--soft);color:var(--muted)}
.st.overdue{background:#FDEBDD;color:#7A2112}.st.today,.st.soon{background:#FFF4D6;color:#6B4700}.st.paused,.st.ended{background:#ECE7E9;color:#5A4B52}
.cal{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff}
.cal-head,.cal-row{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
.cal-head div{padding:8px 6px;text-align:center;font-size:12px;font-weight:600;color:var(--muted);background:var(--soft)}
.cal-cell{min-height:96px;padding:6px;border-top:1px solid var(--line);border-left:1px solid var(--line);display:flex;flex-direction:column;gap:3px;min-width:0}
.cal-row .cal-cell:first-child{border-left:0}
.cal-cell.out{background:#FCF8F9}.cal-cell.out .cal-d{color:#B7A5AE}
.cal-d{font-size:12px;font-weight:600;width:22px;height:22px;display:grid;place-items:center;border-radius:50%}
.cal-cell.today .cal-d{background:var(--brand);color:#fff}
.chip-r{display:flex;align-items:center;gap:4px;font-size:11px;font-weight:600;padding:2px 6px;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chip-r.expense{background:#FDEBDD;color:#8A3410}.chip-r.income{background:#E1F3E9;color:#0B5F49}.chip-r.done{opacity:.6}.chip-r.overdue{outline:1.5px solid var(--out)}
.chip-r svg{width:10px;height:10px;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;flex:none}
.cdots{display:none;gap:3px;flex-wrap:wrap}.cdot{width:7px;height:7px;border-radius:50%}.cdot.expense{background:var(--bar-out)}.cdot.income{background:var(--in)}.cdot.done{opacity:.45}
.check{display:flex;gap:10px;align-items:flex-start;font-weight:500;font-size:14px;margin:0}.check input{width:auto;margin-top:4px}
/* mục tiêu tiết kiệm */
.bar .fill.active{background:var(--brand)}.bar .fill.done{background:var(--in)}
.tabs-row{display:flex;margin-bottom:16px;overflow-x:auto}.tabs2 a{padding:7px 16px;border-radius:8px;font:600 13px "Be Vietnam Pro",system-ui,sans-serif;color:var(--muted);text-decoration:none;white-space:nowrap}
.tabs2 a:hover{color:var(--ink)}.tabs2 a[aria-current=page]{background:#fff;color:var(--ink);box-shadow:0 1px 2px rgba(43,22,33,.14)}
.goals-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.gcard{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;min-width:0}
.gico{width:44px;height:44px;border-radius:12px;display:grid;place-items:center;flex:none;color:var(--c,#C98200);background:color-mix(in srgb,var(--c,#C98200) 14%,#fff)}
.gico svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.gcard-top{display:flex;align-items:center;gap:14px}.gcard-main{flex:1;min-width:0}
.gcard-main a{display:block;color:var(--ink);font-weight:600;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gcard-main a:hover{text-decoration:underline}
.gpct{font-size:20px;font-weight:700;letter-spacing:-.01em}.gpct.active{color:var(--brand-d)}.gpct.done{color:var(--in)}.gpct.over{color:var(--out)}
.gcard .bar{margin:14px 0 8px}.gfoot{display:flex;justify-content:space-between;gap:8px;font-size:13px}.gneed{margin:10px 0 0;font-size:13px;color:var(--muted)}
.gcard .actions{margin-top:14px;flex-wrap:wrap}
.gdetail-top{display:flex;align-items:center;gap:16px;flex-wrap:wrap}.gdetail-top .gico{width:56px;height:56px}.gdetail-top .gico svg{width:28px;height:28px}
.gbig{margin-left:auto;text-align:right}.gbig .num{font-size:clamp(34px,6vw,48px);font-weight:700;letter-spacing:-.02em;line-height:1}
.gdetail .bar{height:14px;margin:18px 0 6px}
.gstats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}.gstats div{padding:12px 14px;background:var(--soft);border-radius:12px}
.gstats span{display:block;font-size:12px;color:var(--muted)}.gstats b{display:block;font-size:16px;margin-top:2px}
.gsum{margin:0 0 20px;padding:12px 14px;background:var(--soft);border-radius:12px}.gsum:empty{display:none}
.gmini{display:flex;gap:14px;align-items:flex-start;padding:14px 0;border-bottom:1px solid var(--line);text-decoration:none;color:var(--ink)}.gmini:last-child{border-bottom:0;padding-bottom:0}.gmini:first-of-type{padding-top:0}
.gmini-main{flex:1;min-width:0}.gmini-top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.gmini .bar{margin:8px 0 6px}
.seg2 input[value=add]:checked+span{background:var(--in);color:#fff}.seg2 input[value=withdraw]:checked+span{background:var(--out);color:#fff}
.dep-form{display:grid;gap:14px}
/* danh sách giao dịch */
.txs{list-style:none;margin:0;padding:0}
.tx{display:grid;grid-template-columns:42px 1fr auto auto;gap:6px 14px;align-items:center;padding:13px 0;border-bottom:1px solid var(--line)}.tx:last-child{border-bottom:0;padding-bottom:0}.tx:first-child{padding-top:0}
.ico{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;font:700 16px "Bricolage Grotesque",sans-serif;color:var(--c,#0F7B5F);background:color-mix(in srgb,var(--c,#0F7B5F) 14%,#fff)}
.tx-main{min-width:0}.tx-main b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.tx-meta{display:flex;flex-wrap:wrap;gap:2px 10px;color:var(--muted);font-size:13px}.tx-meta span{white-space:nowrap}
.tx-amt{font:700 16px "Bricolage Grotesque",sans-serif;text-align:right;white-space:nowrap}.tx-amt.in{color:var(--in)}.tx-amt.out{color:var(--out)}
.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.actions form{margin:0}
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
@media(max-width:700px){.cal-cell{min-height:54px;padding:4px}.chip-r{display:none}.cdots{display:flex}}
@media(max-width:760px){.goals-grid{grid-template-columns:1fr}.gstats{grid-template-columns:1fr 1fr}.gbig{margin-left:0;text-align:left;width:100%}}
@media(max-width:700px){.kpis{grid-template-columns:1fr;gap:12px}.kpi{padding:16px 18px}.kpi-v{font-size:26px}}
@media(max-width:760px){
 .links,.top .btn:not(.light),.who-email{display:none}
 .top-in{padding:10px 16px}.wrap{padding:20px 14px 100px}h1{font-size:27px}
 .tabbar{display:grid;grid-template-columns:repeat(5,1fr);position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);padding:6px 6px calc(6px + env(safe-area-inset-bottom,0px));z-index:30}
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
{% if g.user %}<nav class="links" aria-label="Điều hướng chính"><a href="/" class="{{ 'on' if p=='/' }}">Tổng quan</a><a href="/transactions" class="{{ 'on' if p.startswith('/transactions') and p!='/transactions/new' }}">Giao dịch</a><a href="/budgets" class="{{ 'on' if p.startswith('/budgets') }}">Ngân sách</a><a href="/goals" class="{{ 'on' if p.startswith('/goals') }}">Mục tiêu</a><a href="/recurring" class="{{ 'on' if p.startswith('/recurring') }}">Định kỳ</a></nav><a class="btn" href="/transactions/new">Thêm giao dịch</a><div class="who"><span class="who-email">{{ g.user.email }}</span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn light sm">Đăng xuất</button></form></div>
{% else %}<nav class="links" aria-label="Tài khoản"><a href="/login" class="{{ 'on' if p=='/login' or p=='/' }}">Đăng nhập</a><a href="/register" class="{{ 'on' if p=='/register' }}">Đăng ký</a></nav>{% endif %}</div></header>
<main class="wrap">{% with msgs=get_flashed_messages(with_categories=true) %}{% for typ,msg in msgs %}<div class="flash {{ typ if typ in ('error','warn') else '' }}" role="{{ 'alert' if typ in ('error','warn') else 'status' }}">{{ msg }}</div>{% endfor %}{% endwith %}{% block body %}{% endblock %}</main>
{% if g.user %}<nav class="tabbar" aria-label="Điều hướng nhanh">
<a href="/" class="{{ 'on' if p=='/' }}"><svg viewBox="0 0 24 24"><path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z"/></svg>Tổng quan</a>
<a href="/transactions" class="{{ 'on' if p.startswith('/transactions') and p!='/transactions/new' }}"><svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>Giao dịch</a>
<a href="/transactions/new" class="add"><span class="circle"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></span>Thêm</a>
<a href="/budgets" class="{{ 'on' if p.startswith('/budgets') }}"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 4v8h8"/></svg>Ngân sách</a>
<a href="/goals" class="{{ 'on' if p.startswith('/goals') }}"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/></svg>Mục tiêu</a></nav>{% endif %}
<footer>Dữ liệu được lưu trong cơ sở dữ liệu SQLite của máy chủ</footer></body></html>
"""

DASH = r"""{% extends base %}{% block body %}
{% set bal=income-expense %}
<div class="head"><div><h1>Chi tiêu của bạn</h1><p class="muted" style="margin:0">Tiền vào, tiền ra và ngân sách trong tháng.</p></div>
<div class="month"><a href="/?month={{ prev_month }}" aria-label="Tháng trước"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a><form method="get"><input type="month" name="month" value="{{ month }}" onchange="this.form.submit()" aria-label="Chọn tháng"><noscript><button class="btn sm">Xem</button></noscript></form><a href="/?month={{ next_month }}" aria-label="Tháng sau"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></a></div></div>
{% if b_over or b_warn %}<a class="alert-bar {{ 'over' if b_over else 'warn' }}" href="/budgets?month={{ month }}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/></svg><span>{% if b_over %}<b>{{ b_over|length }} danh mục vượt ngân sách:</b> {{ b_over|map(attribute='name')|join(', ') }}.{% endif %}{% if b_warn %} <b>{{ b_warn|length }} danh mục đã dùng từ 80%:</b> {{ b_warn|map(attribute='name')|join(', ') }}.{% endif %}</span><span class="link">Xem chi tiết</span></a>{% endif %}
{% if rec_alert %}<a class="alert-bar {{ 'over' if rec_alert|selectattr('status','equalto','overdue')|list else 'warn' }}" href="/recurring"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg><span><b>{{ rec_alert|length }} khoản định kỳ cần chú ý:</b> {% for v in rec_alert %}{{ v.name }} ({{ v.text|lower }}){{ ', ' if not loop.last }}{% endfor %}.</span><span class="link">Xem lịch</span></a>{% endif %}
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
<section class="panel" style="margin-top:18px"><div class="panel-h"><h2>Giao dịch định kỳ sắp đến hạn</h2><a class="link" href="/recurring">Xem lịch</a></div>
{% if rec_due %}<ul class="txs">{% for v in rec_due %}<li class="tx"><span class="ico" style="--c:{{ '#0F7B5F' if v.kind=='income' else '#C2410C' }}" aria-hidden="true">{{ v.name|first|upper }}</span><div class="tx-main"><b>{{ v.name }}</b><div class="tx-meta"><span>{{ v.category }}</span><span>{{ v.freq_label }}{% if v.auto %}, tự động{% endif %}</span></div></div><span class="tx-amt {{ 'in' if v.kind=='income' else 'out' }}">{{ '+' if v.kind=='income' else '−' }}{{ v.amount|money }}</span><span class="st {{ v.status }}">{{ v.text }}</span></li>{% endfor %}</ul>
{% elif rcount %}<div class="empty">Không có khoản định kỳ nào đến hạn trong 14 ngày tới.</div>
{% else %}<div class="empty">Chưa có khoản định kỳ nào (tiền thuê nhà, hóa đơn, lương...).<br><a class="btn light" href="/recurring/new">Thêm khoản định kỳ</a></div>{% endif %}</section>
<section class="panel" style="margin-top:18px"><div class="panel-h"><h2>Mục tiêu tiết kiệm</h2><a class="link" href="/goals">Xem tất cả</a></div>
{% if gitems %}{% for g in gitems %}<a class="gmini" href="/goals/{{ g.id }}"><span class="gico" style="--c:{{ g.color }}" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="{{ g.icon }}"/></svg></span><div class="gmini-main"><div class="gmini-top"><b>{{ g.name }}</b><span class="num gpct {{ g.state }}">{{ g.pct_i }}%</span></div><div class="bar"><div class="fill {{ g.state }}" style="width:{{ g.width }}%"></div></div><div class="bud-foot"><span class="num">{{ g.saved|money }} / {{ g.target|money }}</span><span>{{ g.status_text }}</span></div></div></a>{% endfor %}
{% elif gcount %}<div class="empty">Tất cả mục tiêu đã hoàn thành.<br><a class="btn light" href="/goals/new">Tạo mục tiêu mới</a></div>
{% else %}<div class="empty">Chưa có mục tiêu tiết kiệm nào.<br><a class="btn light" href="/goals/new">Tạo mục tiêu đầu tiên</a></div>{% endif %}</section>
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

GOALS = r"""{% extends base %}{% block body %}
<div class="head"><div><h1>Mục tiêu tiết kiệm</h1><p class="muted" style="margin:0">Đặt mục tiêu, thời hạn và theo dõi phần trăm hoàn thành.</p></div><a class="btn" href="/goals/new">Tạo mục tiêu</a></div>
{% if counts.all %}
<section class="hero" aria-labelledby="gs-label"><div class="hero-top"><span id="gs-label">Bạn đã tiết kiệm</span><span class="pill">{{ counts.active }} đang thực hiện, {{ counts.done }} đã đạt</span></div>
<div class="big num">{{ tot_saved|money }}</div>
<div class="split" role="img" aria-label="Tiến độ chung của các mục tiêu"><i class="seg in" style="flex-grow:{{ capped }}"></i><i class="seg none" style="flex-grow:{{ rest }}"></i></div>
<div class="hero-foot"><span>Tổng mục tiêu {{ tot_target|money }}</span><span class="note">Hoàn thành chung {{ overall }}%</span></div></section>
<div class="tabs-row"><nav class="tabs2" aria-label="Lọc mục tiêu"><a href="/goals"{% if status=='all' %} aria-current="page"{% endif %}>Tất cả ({{ counts.all }})</a><a href="/goals?status=active"{% if status=='active' %} aria-current="page"{% endif %}>Đang thực hiện ({{ counts.active }})</a><a href="/goals?status=done"{% if status=='done' %} aria-current="page"{% endif %}>Đã đạt ({{ counts.done }})</a></nav></div>
{% if goals %}<div class="goals-grid">{% for g in goals %}<article class="gcard"><div class="gcard-top"><span class="gico" style="--c:{{ g.color }}" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="{{ g.icon }}"/></svg></span><div class="gcard-main"><a href="/goals/{{ g.id }}">{{ g.name }}</a><span class="muted small">{{ g.kind_label }}, {{ g.status_text|lower }}</span></div><span class="gpct num {{ g.state }}">{{ g.pct_i }}%</span></div>
<div class="bar"><div class="fill {{ g.state }}" style="width:{{ g.width }}%"></div></div>
<div class="gfoot"><b class="num">{{ g.saved|money }}</b><span class="num muted">{{ g.target|money }}</span></div>
{% if not g.done %}<p class="gneed">Còn thiếu {{ g.remaining|money }}{% if g.need %}, cần tiết kiệm khoảng {{ g.need|money }} mỗi tháng{% endif %}.</p>{% endif %}
<div class="actions"><a class="btn sm" href="/goals/{{ g.id }}#nap-tien">Nạp thêm</a><a class="btn light sm" href="/goals/{{ g.id }}/edit">Sửa</a><form method="post" action="/goals/{{ g.id }}/delete" onsubmit="return confirm('Xóa mục tiêu này và toàn bộ lịch sử nạp rút?')"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger sm">Xóa</button></form></div></article>{% endfor %}</div>
{% else %}<div class="panel empty">Không có mục tiêu nào trong nhóm này.</div>{% endif %}
{% else %}
<div class="panel empty"><h2 style="color:var(--ink);margin-bottom:6px">Bạn chưa có mục tiêu tiết kiệm nào</h2>Chọn một loại để bắt đầu.<br><div class="chips" style="justify-content:center;margin-top:16px">{% for k,v in GOAL_KINDS.items() %}<a class="preset" href="/goals/new?kind={{ k }}">{{ v.label }}</a>{% endfor %}</div></div>
{% endif %}{% endblock %}"""

GOAL_FORM = r"""{% extends base %}{% block body %}
<nav class="crumbs" aria-label="Đường dẫn"><a href="/goals">Mục tiêu</a><span aria-hidden="true">/</span><span aria-current="page">{{ 'Sửa mục tiêu' if row else 'Tạo mục tiêu mới' }}</span></nav>
<div class="head head-form"><div><h1 class="caps">{{ 'SỬA MỤC TIÊU' if row else 'TẠO MỤC TIÊU TIẾT KIỆM' }}</h1><p class="muted lead">{{ 'Điều chỉnh tên, số tiền mục tiêu hoặc hạn hoàn thành. Số đã tích lũy được thay đổi ở trang chi tiết.' if row else 'Đặt số tiền cần có và hạn hoàn thành, ứng dụng sẽ tính số tiền cần tiết kiệm mỗi tháng.' }}</p></div><a class="btn light" href="{{ '/goals/%d'|format(row.id) if row else '/goals' }}">Quay lại</a></div>
<form class="panel formcard" method="post" id="g-form" data-saved="{{ saved }}"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
<div class="field"><label for="g-name">Tên mục tiêu *</label><input id="g-name" name="name" maxlength="60" required value="{{ vals.name }}" placeholder="Ví dụ: Du lịch Đà Lạt"></div>
<div class="field"><span class="sr" id="k-label">Loại mục tiêu</span><label aria-hidden="true">Loại mục tiêu *</label><div class="chips" role="radiogroup" aria-labelledby="k-label">{% for k,v in GOAL_KINDS.items() %}<label><input type="radio" name="kind" value="{{ k }}" required {{ 'checked' if vals.kind==k }}><span><i class="dot" style="background:{{ v.color }}"></i>{{ v.label }}</span></label>{% endfor %}</div></div>
<div class="field"><label for="g-target">Số tiền mục tiêu (VND) *</label><div class="amount-box"><input class="amount" id="g-target" name="target_amount" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" required value="{{ vals.target_amount }}" placeholder="20000000"><span class="unit">đ</span></div><div class="amt-fmt" id="g-fmt" style="margin-top:6px"></div></div>
<div class="two field"><div><label for="g-deadline">Hạn hoàn thành *</label><input id="g-deadline" name="deadline" type="date" required value="{{ vals.deadline }}" {% if not row %}min="{{ today }}"{% endif %}></div>
{% if not row %}<div><label for="g-saved">Đã tích lũy (VND)</label><input id="g-saved" name="saved_amount" type="text" inputmode="numeric" pattern="[0-9.,\s]*" maxlength="18" autocomplete="off" value="{{ vals.saved_amount }}" placeholder="0"></div>{% endif %}</div>
<div class="gsum" id="gsum" aria-live="polite"></div>
<div class="form-actions"><a class="btn light" href="{{ '/goals/%d'|format(row.id) if row else '/goals' }}">Hủy</a><button class="btn">{{ 'Lưu thay đổi' if row else 'Tạo mục tiêu' }}</button></div></form>
<script>(function(){
var f=document.getElementById('g-form'),t=document.getElementById('g-target'),d=document.getElementById('g-deadline'),s=document.getElementById('g-saved'),o=document.getElementById('gsum'),p=document.getElementById('g-fmt');
function num(e){return e?Number((e.value||'').replace(/[^0-9]/g,''))||0:0}
function vnd(v){return Number(v).toLocaleString('vi-VN')+' đ'}
function upd(){var T=num(t),S=s?num(s):Number(f.dataset.saved||0);p.textContent=T?vnd(T):'';
 if(!T||!d.value){o.textContent='';return}
 var rem=Math.max(0,T-S),days=Math.round((new Date(d.value+'T00:00:00')-new Date(new Date().toDateString()))/864e5),dt=d.value.split('-').reverse().join('/');
 if(rem===0){o.textContent='Số đã tích lũy đã đạt mục tiêu này.';return}
 if(days<=0){o.textContent='Hạn hoàn thành đã qua hoặc là hôm nay, cần tiết kiệm '+vnd(rem)+' ngay.';return}
 var m=Math.max(1,Math.ceil(days/30.44));
 o.innerHTML='Để đạt <b>'+vnd(T)+'</b> vào <b>'+dt+'</b>, bạn cần tiết kiệm khoảng <b>'+vnd(Math.ceil(rem/m/1000)*1000)+'</b> mỗi tháng trong '+m+' tháng.'}
[t,d,s].forEach(function(e){if(e){e.addEventListener('input',upd);e.addEventListener('change',upd)}});upd();})();</script>{% endblock %}"""

GOAL_DETAIL = r"""{% extends base %}{% block body %}
<nav class="crumbs" aria-label="Đường dẫn"><a href="/goals">Mục tiêu</a><span aria-hidden="true">/</span><span aria-current="page">{{ goal.name }}</span></nav>
<div class="head"><div><h1>{{ goal.name }}</h1><p class="muted" style="margin:0">{{ goal.kind_label }}, hạn hoàn thành {{ goal.deadline|vdate }}</p></div><div class="head-actions"><a class="btn light" href="/goals/{{ goal.id }}/edit">Sửa</a><form method="post" action="/goals/{{ goal.id }}/delete" onsubmit="return confirm('Xóa mục tiêu này và toàn bộ lịch sử nạp rút?')" style="margin:0"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger">Xóa</button></form></div></div>
<section class="panel gdetail" aria-label="Tiến độ mục tiêu"><div class="gdetail-top"><span class="gico" style="--c:{{ goal.color }}" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="{{ goal.icon }}"/></svg></span><div><div class="muted small">{{ goal.status_text }}</div><div class="num gpct {{ goal.state }}" style="font-size:18px">{{ goal.pct_i }}% hoàn thành</div></div><div class="gbig"><div class="num">{{ goal.saved|money }}</div><div class="muted small">trên {{ goal.target|money }}</div></div></div>
<div class="bar"><div class="fill {{ goal.state }}" style="width:{{ goal.width }}%"></div></div>
<div class="gstats"><div><span>Còn thiếu</span><b class="num">{{ goal.remaining|money }}</b></div><div><span>Cần tiết kiệm mỗi tháng</span><b class="num">{% if goal.done %}Đã đạt{% else %}{{ goal.need|money }}{% endif %}</b></div><div><span>Thời gian còn lại</span><b>{{ goal.status_text }}</b></div></div></section>
<div class="cols">
<section class="panel" id="nap-tien"><h2 style="margin-bottom:16px">Nạp hoặc rút tiền</h2>
<form method="post" action="/goals/{{ goal.id }}/deposit" class="dep-form"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
<div class="seg2" role="radiogroup" aria-label="Loại giao dịch"><label><input type="radio" name="direction" value="add" checked><span>Nạp thêm</span></label><label><input type="radio" name="direction" value="withdraw"><span>Rút bớt</span></label></div>
<div><label for="d-amount">Số tiền (VND) *</label><input id="d-amount" name="amount" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" required placeholder="500000"></div>
<div class="two"><div><label for="d-date">Ngày</label><input id="d-date" name="dep_date" type="date" value="{{ today }}" max="{{ today }}"></div><div><label for="d-note">Ghi chú</label><input id="d-note" name="note" maxlength="100" placeholder="Không bắt buộc"></div></div>
<button class="btn wide">Ghi nhận</button></form></section>
<section class="panel"><h2 style="margin-bottom:12px">Lịch sử nạp rút</h2>
{% if deps %}<ul class="txs">{% for d in deps %}<li class="tx"><span class="ico" style="--c:{{ '#0F7B5F' if d.amount>0 else '#C2410C' }}" aria-hidden="true">{{ '+' if d.amount>0 else '−' }}</span><div class="tx-main"><b>{{ d.note or ('Nạp thêm' if d.amount>0 else 'Rút bớt') }}</b><div class="tx-meta"><span>{{ d.dep_date|vdate }}</span></div></div><span class="tx-amt {{ 'in' if d.amount>0 else 'out' }}">{{ '+' if d.amount>0 else '−' }}{{ (d.amount if d.amount>0 else 0-d.amount)|money }}</span><div class="actions"><form method="post" action="/goals/{{ goal.id }}/deposits/{{ d.id }}/delete" onsubmit="return confirm('Xóa khoản này?')"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger sm">Xóa</button></form></div></li>{% endfor %}</ul>
{% else %}<div class="empty">Chưa có khoản nạp nào.</div>{% endif %}</section></div>{% endblock %}"""

RECURRING = r'''{% extends base %}{% block body %}
<div class="head"><div><h1>Giao dịch định kỳ</h1><p class="muted" style="margin:0">Tiền thuê nhà, hóa đơn, lương và các khoản lặp lại. Ứng dụng nhắc khi sắp đến hạn.</p></div><a class="btn" href="/recurring/new">Thêm khoản định kỳ</a></div>
{% if items %}
<div class="kpis">
<article class="kpi"><div class="kpi-h"><i class="sw bi"></i>Thu định kỳ mỗi tháng</div><b class="num kpi-v">{{ est_in|money }}</b><div class="delta flat"><span>Ước tính từ các khoản đang chạy</span></div></article>
<article class="kpi"><div class="kpi-h"><i class="sw be"></i>Chi cố định mỗi tháng</div><b class="num kpi-v">{{ est_out|money }}</b><div class="delta flat"><span>Ước tính từ các khoản đang chạy</span></div></article>
<article class="kpi"><div class="kpi-h"><i class="sw" style="background:var(--brand)"></i>Đến hạn trong 7 ngày</div><b class="num kpi-v">{{ due7_n }} khoản</b><div class="delta flat"><span>Tổng chi {{ due7_sum|money }}</span></div></article></div>
{% if attention %}<section class="panel" aria-labelledby="att-h"><div class="panel-h"><h2 id="att-h">Cần chú ý</h2></div><ul class="txs">
{% for v in attention %}<li class="tx"><span class="ico" style="--c:{{ '#0F7B5F' if v.kind=='income' else '#C2410C' }}" aria-hidden="true">{{ v.name|first|upper }}</span><div class="tx-main"><b>{{ v.name }}</b><div class="tx-meta"><span>{{ v.category }}</span><span class="st {{ v.status }}">{{ v.text }}</span></div></div><span class="tx-amt {{ 'in' if v.kind=='income' else 'out' }}">{{ '+' if v.kind=='income' else '−' }}{{ v.amount|money }}</span>
<div class="actions">{% if v.auto %}<span class="muted small">Tự động ghi nhận khi đến hạn</span>{% else %}<form method="post" action="/recurring/{{ v.id }}/record"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn sm">Ghi nhận</button></form><form method="post" action="/recurring/{{ v.id }}/skip" onsubmit="return confirm('Bỏ qua kỳ này mà không ghi giao dịch?')"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn light sm">Bỏ qua</button></form>{% endif %}</div></li>{% endfor %}</ul></section>{% endif %}
<section class="panel" aria-labelledby="cal-h"><div class="panel-h"><h2 id="cal-h">Lịch tháng {{ month|vmonth }}</h2>
<div class="month"><a href="/recurring?month={{ prev_month }}" aria-label="Tháng trước"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg></a><form method="get"><input type="month" name="month" value="{{ month }}" onchange="this.form.submit()" aria-label="Chọn tháng"><noscript><button class="btn sm">Xem</button></noscript></form><a href="/recurring?month={{ next_month }}" aria-label="Tháng sau"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></a></div></div>
<div class="cal" role="grid" aria-label="Lịch các khoản định kỳ"><div class="cal-head" role="row"><div>T2</div><div>T3</div><div>T4</div><div>T5</div><div>T6</div><div>T7</div><div>CN</div></div>
{% for week in weeks %}<div class="cal-row" role="row">{% for day in week %}<div class="cal-cell{{ ' out' if day.out }}{{ ' today' if day.today }}" role="gridcell"><span class="cal-d">{{ day.n }}</span>
{% for e in day.events %}<span class="chip-r {{ e.kind }} {{ e.state }}" title="{{ e.name }}: {{ e.amount|money }}">{% if e.state=='done' %}<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6.5l2.5 2.5L10 3.5"/></svg>{% endif %}{{ e.name }}</span>{% endfor %}
{% if day.events %}<span class="cdots" aria-hidden="true">{% for e in day.events %}<i class="cdot {{ e.kind }} {{ e.state }}"></i>{% endfor %}</span>{% endif %}</div>{% endfor %}</div>{% endfor %}</div>
<h3 style="font-size:15px;margin:20px 0 8px">Trong tháng này</h3>
{% if agenda %}<ul class="txs">{% for e in agenda %}<li class="tx"><span class="ico" style="--c:{{ '#0F7B5F' if e.kind=='income' else '#C2410C' }}" aria-hidden="true">{{ e.name|first|upper }}</span><div class="tx-main"><b>{{ e.name }}</b><div class="tx-meta"><span>{{ e.category }}</span><span>{{ e.date.isoformat()|vdate }}</span></div></div><span class="tx-amt {{ 'in' if e.kind=='income' else 'out' }}">{{ '+' if e.kind=='income' else '−' }}{{ e.amount|money }}</span><span class="st {{ 'ended' if e.state=='done' else ('overdue' if e.state=='overdue' else ('today' if e.state=='today' else 'later')) }}">{{ {'done':'Đã xử lý','overdue':'Quá hạn','today':'Hôm nay','upcoming':'Sắp tới'}[e.state] }}</span></li>{% endfor %}</ul>
{% else %}<div class="empty">Tháng này không có khoản định kỳ nào đến hạn.</div>{% endif %}</section>
<section class="panel"><div class="panel-h"><h2>Tất cả khoản định kỳ</h2></div><ul class="txs">
{% for v in items %}<li class="tx"><span class="ico" style="--c:{{ '#0F7B5F' if v.kind=='income' else '#C2410C' }}" aria-hidden="true">{{ v.name|first|upper }}</span><div class="tx-main"><b>{{ v.name }}</b><div class="tx-meta"><span>{{ v.category }}</span><span>{{ v.freq_label }}</span><span>Kỳ tới {{ v.next_due|vdate }}</span>{% if v.auto %}<span>Tự động</span>{% endif %}</div></div><span class="tx-amt {{ 'in' if v.kind=='income' else 'out' }}">{{ '+' if v.kind=='income' else '−' }}{{ v.amount|money }}</span>
<div class="actions"><span class="st {{ v.status }}">{{ v.text if v.status in ('paused','ended') else ('Đang chạy' if v.status=='later' else v.text) }}</span><a class="btn light sm" href="/recurring/{{ v.id }}/edit">Sửa</a>{% if v.status!='ended' %}<form method="post" action="/recurring/{{ v.id }}/toggle"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn light sm">{{ 'Tiếp tục' if not v.active else 'Tạm dừng' }}</button></form>{% endif %}<form method="post" action="/recurring/{{ v.id }}/delete" onsubmit="return confirm('Xóa khoản định kỳ này? Các giao dịch đã ghi nhận vẫn được giữ lại.')"><input type="hidden" name="csrf" value="{{ csrf_token() }}"><button class="btn danger sm">Xóa</button></form></div></li>{% endfor %}</ul></section>
{% else %}
<div class="panel empty"><h2 style="color:var(--ink);margin-bottom:6px">Chưa có khoản định kỳ nào</h2>Thêm tiền thuê nhà, hóa đơn, lương hoặc các khoản lặp lại để được nhắc đúng hạn.<br><a class="btn" href="/recurring/new">Thêm khoản định kỳ</a></div>
{% endif %}{% endblock %}'''

REC_FORM = r'''{% extends base %}{% block body %}
<nav class="crumbs" aria-label="Đường dẫn"><a href="/recurring">Giao dịch định kỳ</a><span aria-hidden="true">/</span><span aria-current="page">{{ 'Sửa khoản định kỳ' if row else 'Thêm khoản định kỳ' }}</span></nav>
<div class="head head-form"><div><h1 class="caps">{{ 'SỬA KHOẢN ĐỊNH KỲ' if row else 'THÊM KHOẢN ĐỊNH KỲ' }}</h1><p class="muted lead">Khoản lặp lại theo tuần, tháng hoặc năm. Bạn chọn nhắc để tự xác nhận, hoặc để ứng dụng tự ghi nhận khi đến hạn.</p></div><a class="btn light" href="/recurring">Quay lại</a></div>
<form class="panel formcard" method="post"><input type="hidden" name="csrf" value="{{ csrf_token() }}">
<div class="field"><div class="seg2" role="radiogroup" aria-label="Loại khoản"><label><input type="radio" name="kind" value="expense" {{ 'checked' if vals.kind!='income' }}><span>Chi tiêu</span></label><label><input type="radio" name="kind" value="income" {{ 'checked' if vals.kind=='income' }}><span>Thu nhập</span></label></div></div>
<div class="field"><label for="r-note">Tên khoản *</label><input id="r-note" name="note" maxlength="80" required value="{{ vals.note }}" placeholder="Ví dụ: Tiền thuê nhà"></div>
<div class="field"><label for="r-amount">Số tiền mỗi kỳ (VND) *</label><div class="amount-box"><input class="amount" id="r-amount" name="amount" type="text" inputmode="numeric" pattern="[0-9.,\s]+" maxlength="18" autocomplete="off" required value="{{ vals.amount }}" placeholder="5000000"><span class="unit">đ</span></div><div class="amt-fmt" id="r-fmt" style="margin-top:6px"></div></div>
<div class="field"><span class="sr" id="rc-label">Danh mục</span><label aria-hidden="true">Danh mục *</label><div class="chips" role="radiogroup" aria-labelledby="rc-label">{% for c in cats %}<label><input type="radio" name="category_id" value="{{ c.id }}" data-kind="{{ c.kind }}" required {{ 'checked' if vals.category_id==c.id|string }}><span><i class="dot" style="background:{{ cat_color(c.id) }}"></i>{{ c.name }}</span></label>{% endfor %}</div></div>
<div class="two field"><div><label for="r-freq">Lặp lại *</label><select id="r-freq" name="frequency">{% for k,v in {'weekly':'Hàng tuần','monthly':'Hàng tháng','yearly':'Hàng năm'}.items() %}<option value="{{ k }}" {{ 'selected' if vals.frequency==k }}>{{ v }}</option>{% endfor %}</select></div>
<div><label for="r-due">{{ 'Đến hạn kế tiếp *' if row else 'Ngày đến hạn đầu tiên *' }}</label><input id="r-due" name="next_due" type="date" required value="{{ vals.next_due }}" {% if not row %}min="{{ today }}"{% endif %}></div></div>
<div class="two field"><div><label for="r-end">Ngày kết thúc (nếu có)</label><input id="r-end" name="end_date" type="date" value="{{ vals.end_date }}"></div>
<div><label for="r-remind">Nhắc trước</label><select id="r-remind" name="remind_days">{% for n in (0,1,2,3,5,7,14) %}<option value="{{ n }}" {{ 'selected' if vals.remind_days==n|string }}>{{ 'Đúng ngày đến hạn' if n==0 else (n|string ~ ' ngày') }}</option>{% endfor %}</select></div></div>
<div class="field"><label class="check"><input type="checkbox" name="auto" value="1" {{ 'checked' if vals.auto }}><span>Tự động ghi nhận giao dịch khi đến hạn<br><span class="muted small" style="font-weight:400">Nếu không chọn, ứng dụng chỉ nhắc và bạn bấm "Ghi nhận" để tạo giao dịch.</span></span></label></div>
<div class="form-actions"><a class="btn light" href="/recurring">Hủy</a><button class="btn">{{ 'Lưu thay đổi' if row else 'Thêm khoản định kỳ' }}</button></div></form>
<script>(function(){var kinds=document.querySelectorAll('input[name=kind]'),chips=document.querySelectorAll('.chips label');
function sync(){var k=document.querySelector('input[name=kind]:checked').value,has=false;chips.forEach(function(l){var r=l.querySelector('input'),ok=r.dataset.kind===k;l.hidden=!ok;if(!ok)r.checked=false;else if(r.checked)has=true});if(!has){for(var i=0;i<chips.length;i++){if(!chips[i].hidden){chips[i].querySelector('input').checked=true;break}}}}
kinds.forEach(function(r){r.addEventListener('change',sync)});sync();
var a=document.getElementById('r-amount'),o=document.getElementById('r-fmt');
function fmt(){var d=a.value.replace(/[^0-9]/g,'');o.textContent=d?Number(d).toLocaleString('vi-VN')+' đ':''}
a.addEventListener('input',fmt);fmt();})();</script>{% endblock %}'''

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
    auto_record_notice()
    month=month_arg(request.args.get("month",date.today().strftime("%Y-%m"))); u=uid()
    with db() as c:
        totals=c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE 0 END),0) income, COALESCE(SUM(CASE WHEN kind='expense' THEN amount ELSE 0 END),0) expense FROM transactions WHERE user_id=? AND substr(tx_date,1,7)=?",(u,month)).fetchone()
        bycat=c.execute("SELECT ca.id,ca.name,SUM(t.amount) total FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.user_id=? AND t.kind='expense' AND substr(t.tx_date,1,7)=? GROUP BY ca.id ORDER BY total DESC",(u,month)).fetchall()
        recent=c.execute("SELECT t.*,ca.name category FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.user_id=? AND substr(t.tx_date,1,7)=? ORDER BY tx_date DESC,t.id DESC LIMIT 8",(u,month)).fetchall()
        budgets=c.execute("""SELECT b.*,ca.name,COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.user_id=b.user_id AND t.category_id=b.category_id AND t.kind='expense' AND substr(t.tx_date,1,7)=b.month),0) spent FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.user_id=? AND b.month=?""",(u,month)).fetchall()
    with db() as c:
        grows = c.execute(GOAL_SQL + " WHERE g.user_id=?", (u,)).fetchall()
    gviews = sorted((goal_view(r) for r in grows), key=lambda v: (v["done"], v["deadline"], v["id"]))
    with db() as c:
        rrows = c.execute(REC_SQL, (u,)).fetchall()
    rviews = [rec_view(r) for r in rrows]
    bv = budget_view(budgets)
    prev = shift_month(month, -1)
    with db() as c:
        balance = c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE -amount END),0) FROM transactions WHERE user_id=?", (u,)).fetchone()[0]
        p = c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE 0 END),0) i, COALESCE(SUM(CASE WHEN kind='expense' THEN amount ELSE 0 END),0) e FROM transactions WHERE user_id=? AND substr(tx_date,1,7)=?", (u, prev)).fetchone()
        chart = chart_data(c, u, month)
    d_income = delta(totals["income"], p["i"], True)
    d_expense = delta(totals["expense"], p["e"], False)
    d_net = delta(totals["income"] - totals["expense"], p["i"] - p["e"], True)
    return page(DASH, title="Tổng quan", balance=balance, d_income=d_income, d_expense=d_expense, d_net=d_net, chart=chart,month=month,income=totals["income"],expense=totals["expense"],bycat=bycat,maxcat=max([x["total"] for x in bycat],default=1),recent=recent,bitems=bv["items"],b_over=bv["over"],b_warn=bv["warn"],gitems=[v for v in gviews if not v["done"]][:3],gcount=len(gviews),rec_due=sorted((v for v in rviews if v["status"] in ("overdue","today","soon") or (v["status"]=="later" and v["days"]<=14)),key=lambda v:v["days"])[:4],rec_alert=[v for v in rviews if v["status"] in ("overdue","today","soon") and not v["auto"]],rcount=len(rviews),prev_month=shift_month(month,-1),next_month=shift_month(month,1))

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
    auto_record_notice()
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

FREQ_LABEL = {"weekly": "Hàng tuần", "monthly": "Hàng tháng", "yearly": "Hàng năm"}

def add_months(d, n, anchor):
    """Cộng n tháng, giữ ngày gốc (anchor) và co lại nếu tháng đó ngắn hơn (31 -> 28/29/30)."""
    idx = d.year * 12 + (d.month - 1) + n
    y, m = idx // 12, idx % 12 + 1
    return date(y, m, min(anchor, calendar.monthrange(y, m)[1]))

def next_occurrence(d, freq, anchor):
    if freq == "weekly": return d + timedelta(days=7)
    return add_months(d, 1 if freq == "monthly" else 12, anchor)

def occurrences(r, d_from, d_to):
    """Các ngày đến hạn của một khoản định kỳ trong khoảng [d_from, d_to]."""
    start = date.fromisoformat(r["start_date"])
    end = date.fromisoformat(r["end_date"]) if r["end_date"] else None
    last = min(d_to, end) if end else d_to
    out = []
    if r["frequency"] == "weekly":
        d = start
        if d_from > start: d = start + timedelta(days=-(-(d_from - start).days // 7) * 7)
        while d <= last: out.append(d); d += timedelta(days=7)
    else:
        step = 1 if r["frequency"] == "monthly" else 12
        k = max(0, ((d_from.year - start.year) * 12 + d_from.month - start.month) // step - 1)
        while True:
            d = add_months(start, k * step, r["anchor_day"])
            if d > last: break
            if d >= d_from and d >= start: out.append(d)
            k += 1
    return out

def rec_view(r, today=None):
    today = today or date.today()
    nd = date.fromisoformat(r["next_due"])
    end = date.fromisoformat(r["end_date"]) if r["end_date"] else None
    days = (nd - today).days
    if end and nd > end: status, text = "ended", "Đã kết thúc"
    elif not r["active"]: status, text = "paused", "Tạm dừng"
    elif days < 0: status, text = "overdue", f"Quá hạn {-days} ngày"
    elif days == 0: status, text = "today", "Đến hạn hôm nay"
    else:
        status = "soon" if days <= r["remind_days"] else "later"
        text = "Đến hạn ngày mai" if days == 1 else f"Còn {days} ngày"
    per_month = {"weekly": r["amount"] * 52 / 12, "monthly": r["amount"], "yearly": r["amount"] / 12}[r["frequency"]]
    return {"id": r["id"], "name": r["note"], "kind": r["kind"], "amount": r["amount"], "category": r["category"] if "category" in r.keys() else "", "category_id": r["category_id"],
            "frequency": r["frequency"], "freq_label": FREQ_LABEL[r["frequency"]], "next_due": r["next_due"], "end_date": r["end_date"],
            "days": days, "status": status, "text": text, "auto": bool(r["auto"]), "active": bool(r["active"]), "remind_days": r["remind_days"],
            "per_month": per_month, "counts": status not in ("ended", "paused")}

REC_SQL = "SELECT r.*,ca.name category FROM recurring r JOIN categories ca ON ca.id=r.category_id WHERE r.user_id=? ORDER BY r.next_due,r.id"

def run_auto_recurring(user_id):
    """Ghi nhận các khoản định kỳ 'tự động' đã đến hạn (kể cả các kỳ bị lỡ khi chưa mở ứng dụng). Trả về số giao dịch đã tạo."""
    today, made = date.today(), 0
    with db() as c:
        rows = c.execute("SELECT * FROM recurring WHERE user_id=? AND auto=1 AND active=1 AND next_due<=?", (user_id, today.isoformat())).fetchall()
        for r in rows:
            nd = date.fromisoformat(r["next_due"]); end = date.fromisoformat(r["end_date"]) if r["end_date"] else None
            guard = 0
            while nd <= today and (end is None or nd <= end) and guard < 60:
                nxt = next_occurrence(nd, r["frequency"], r["anchor_day"])
                if c.execute("UPDATE recurring SET next_due=? WHERE id=? AND next_due=?", (nxt.isoformat(), r["id"], nd.isoformat())).rowcount != 1:
                    break   # luồng khác vừa xử lý kỳ này
                c.execute("INSERT INTO transactions(kind,amount,tx_date,category_id,note,user_id) VALUES(?,?,?,?,?,?)", (r["kind"], r["amount"], nd.isoformat(), r["category_id"], r["note"], user_id))
                made += 1; nd = nxt; guard += 1
    return made

def auto_record_notice():
    n = run_auto_recurring(uid())
    if n: flash(f"Đã tự động ghi nhận {n} giao dịch định kỳ đến hạn.", "success")

def read_rec_form():
    note = request.form.get("note", "").strip(); kind = request.form.get("kind", ""); freq = request.form.get("frequency", "")
    if not note or len(note) > 80 or kind not in ("income", "expense") or freq not in FREQ_LABEL: raise ValueError
    amount = parse_amount(request.form.get("amount", ""))
    if amount <= 0 or amount > MAX_AMOUNT: raise ValueError
    cat = request.form.get("category_id", "")
    with db() as c: category = c.execute("SELECT * FROM categories WHERE id=?", (cat,)).fetchone()
    if not category or category["kind"] != kind: raise ValueError
    nd = request.form.get("next_due", "")
    if not valid_date(nd): raise ValueError
    nd = datetime.strptime(nd, "%Y-%m-%d").date()
    end_raw = request.form.get("end_date", "").strip(); end = None
    if end_raw:
        if not valid_date(end_raw): raise ValueError
        end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        if end < nd or end.year > 2100: raise ValueError
    remind = int(request.form.get("remind_days", "3"))
    if not 0 <= remind <= 30 or nd.year > 2100: raise ValueError
    return {"note": note, "kind": kind, "frequency": freq, "amount": amount, "category_id": int(cat), "next_due": nd,
            "end_date": end.isoformat() if end else None, "remind_days": remind, "auto": 1 if request.form.get("auto") == "1" else 0}

def rec_form_vals(row=None):
    if request.method == "POST":
        f = request.form
        return {k: f.get(k, "") for k in ("note", "kind", "amount", "category_id", "frequency", "next_due", "end_date", "remind_days")} | {"auto": f.get("auto") == "1"}
    if row:
        return {"note": row["note"], "kind": row["kind"], "amount": row["amount"], "category_id": str(row["category_id"]), "frequency": row["frequency"],
                "next_due": row["next_due"], "end_date": row["end_date"] or "", "remind_days": str(row["remind_days"]), "auto": bool(row["auto"])}
    return {"note": "", "kind": "expense", "amount": "", "category_id": "", "frequency": "monthly", "next_due": date.today().isoformat(), "end_date": "", "remind_days": "3", "auto": False}

@app.route("/recurring")
@login_required
def rec_page():
    auto_record_notice()
    today = date.today()
    month = month_arg(request.args.get("month", today.strftime("%Y-%m")))
    y, mo = int(month[:4]), int(month[5:7])
    first, last = date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
    with db() as c: rows = c.execute(REC_SQL, (uid(),)).fetchall()
    views = [rec_view(r, today) for r in rows]
    by_day, agenda = {}, []
    for r, v in zip(rows, views):
        nd = date.fromisoformat(r["next_due"])
        for occ in occurrences(r, first, last):
            if occ < nd: state = "done"
            elif v["status"] in ("paused", "ended"): continue
            elif occ < today: state = "overdue"
            elif occ == today: state = "today"
            else: state = "upcoming"
            ev = {"id": r["id"], "name": r["note"], "kind": r["kind"], "amount": r["amount"], "state": state, "date": occ, "category": r["category"], "category_id": r["category_id"]}
            by_day.setdefault(occ, []).append(ev); agenda.append(ev)
    agenda.sort(key=lambda e: (e["date"], e["id"]))
    weeks = [[{"n": d.day, "out": d.month != mo, "today": d == today, "events": by_day.get(d, []) if d.month == mo else []} for d in wk]
             for wk in calendar.Calendar(0).monthdatescalendar(y, mo)]
    attention = sorted((v for v in views if v["status"] in ("overdue", "today", "soon")), key=lambda v: v["days"])
    due7 = [v for v in views if v["status"] in ("overdue", "today", "soon", "later") and v["days"] <= 7]
    return page(RECURRING, title="Giao dịch định kỳ", items=views, attention=attention, weeks=weeks, agenda=agenda, month=month,
                prev_month=shift_month(month, -1), next_month=shift_month(month, 1),
                est_in=round(sum(v["per_month"] for v in views if v["counts"] and v["kind"] == "income")),
                est_out=round(sum(v["per_month"] for v in views if v["counts"] and v["kind"] == "expense")),
                due7_n=len(due7), due7_sum=sum(v["amount"] for v in due7 if v["kind"] == "expense"))

@app.route("/recurring/new", methods=["GET", "POST"])
@login_required
def rec_new():
    if request.method == "POST":
        try:
            d = read_rec_form()
            if d["next_due"] < date.today(): raise ValueError
            with db() as c:
                c.execute("INSERT INTO recurring(user_id,kind,amount,category_id,note,frequency,start_date,anchor_day,next_due,end_date,remind_days,auto) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (uid(), d["kind"], d["amount"], d["category_id"], d["note"], d["frequency"], d["next_due"].isoformat(), d["next_due"].day, d["next_due"].isoformat(), d["end_date"], d["remind_days"], d["auto"]))
            flash("Đã thêm khoản định kỳ.", "success"); return redirect(url_for("rec_page"))
        except (ValueError, TypeError, OverflowError):
            flash("Dữ liệu không hợp lệ. Kiểm tra tên, số tiền, danh mục và ngày (ngày đến hạn đầu tiên từ hôm nay trở đi).", "error")
    return page(REC_FORM, title="Thêm khoản định kỳ", row=None, vals=rec_form_vals(), cats=categories(), today=date.today().isoformat())

@app.route("/recurring/<int:rid>/edit", methods=["GET", "POST"])
@login_required
def rec_edit(rid):
    with db() as c: row = c.execute("SELECT * FROM recurring WHERE id=? AND user_id=?", (rid, uid())).fetchone()
    if not row:
        flash("Không tìm thấy khoản định kỳ.", "error"); return redirect(url_for("rec_page"))
    if request.method == "POST":
        try:
            d = read_rec_form()
            start, anchor = (row["start_date"], row["anchor_day"]) if d["next_due"].isoformat() == row["next_due"] else (d["next_due"].isoformat(), d["next_due"].day)
            with db() as c:
                c.execute("UPDATE recurring SET kind=?,amount=?,category_id=?,note=?,frequency=?,start_date=?,anchor_day=?,next_due=?,end_date=?,remind_days=?,auto=? WHERE id=? AND user_id=?",
                          (d["kind"], d["amount"], d["category_id"], d["note"], d["frequency"], start, anchor, d["next_due"].isoformat(), d["end_date"], d["remind_days"], d["auto"], rid, uid()))
            flash("Đã cập nhật khoản định kỳ.", "success"); return redirect(url_for("rec_page"))
        except (ValueError, TypeError, OverflowError):
            flash("Dữ liệu không hợp lệ. Kiểm tra tên, số tiền, danh mục và ngày.", "error")
    return page(REC_FORM, title="Sửa khoản định kỳ", row=row, vals=rec_form_vals(row), cats=categories(), today=date.today().isoformat())

@app.route("/recurring/<int:rid>/delete", methods=["POST"])
@login_required
def rec_delete(rid):
    with db() as c: c.execute("DELETE FROM recurring WHERE id=? AND user_id=?", (rid, uid()))
    flash("Đã xóa khoản định kỳ. Các giao dịch đã ghi nhận vẫn được giữ lại.", "success"); return redirect(url_for("rec_page"))

def rec_step(c, r):
    """Chuyển khoản định kỳ sang kỳ kế tiếp (an toàn khi nhiều luồng cùng xử lý). Trả về (ngày vừa xử lý, ngày kế tiếp) hoặc None."""
    nd = date.fromisoformat(r["next_due"]); end = date.fromisoformat(r["end_date"]) if r["end_date"] else None
    if not r["active"] or (end and nd > end): return None
    nxt = next_occurrence(nd, r["frequency"], r["anchor_day"])
    if c.execute("UPDATE recurring SET next_due=? WHERE id=? AND next_due=?", (nxt.isoformat(), r["id"], nd.isoformat())).rowcount != 1: return None
    return nd, nxt

@app.route("/recurring/<int:rid>/record", methods=["POST"])
@login_required
def rec_record(rid):
    warning = None
    with db() as c:
        r = c.execute("SELECT * FROM recurring WHERE id=? AND user_id=?", (rid, uid())).fetchone()
        step = rec_step(c, r) if r and rec_view(r)["status"] in ("overdue", "today", "soon") else None
        if step:
            tx_date = min(step[0], date.today()).isoformat()   # không ghi giao dịch vào tương lai
            c.execute("INSERT INTO transactions(kind,amount,tx_date,category_id,note,user_id) VALUES(?,?,?,?,?,?)", (r["kind"], r["amount"], tx_date, r["category_id"], r["note"], uid()))
            if r["kind"] == "expense": warning = budget_warning(c, uid(), tx_date, r["category_id"])
    if not step: flash("Không thể ghi nhận khoản này (chưa đến kỳ nhắc, đã xử lý, tạm dừng hoặc đã kết thúc).", "error")
    else:
        flash(f"Đã ghi nhận \"{r['note']}\" ({money(r['amount'])}) vào giao dịch. Kỳ kế tiếp: {vdate(step[1].isoformat())}.", "success")
        if warning: flash(warning, "warn")
    return redirect(url_for("rec_page"))

@app.route("/recurring/<int:rid>/skip", methods=["POST"])
@login_required
def rec_skip(rid):
    with db() as c:
        r = c.execute("SELECT * FROM recurring WHERE id=? AND user_id=?", (rid, uid())).fetchone()
        step = rec_step(c, r) if r and rec_view(r)["status"] in ("overdue", "today", "soon") else None
    if not step: flash("Không thể bỏ qua khoản này (chưa đến kỳ nhắc, đã xử lý, tạm dừng hoặc đã kết thúc).", "error")
    else: flash(f"Đã bỏ qua kỳ {vdate(step[0].isoformat())} của \"{r['note']}\". Kỳ kế tiếp: {vdate(step[1].isoformat())}.", "success")
    return redirect(url_for("rec_page"))

@app.route("/recurring/<int:rid>/toggle", methods=["POST"])
@login_required
def rec_toggle(rid):
    with db() as c:
        r = c.execute("SELECT * FROM recurring WHERE id=? AND user_id=?", (rid, uid())).fetchone()
        if not r: flash("Không tìm thấy khoản định kỳ.", "error"); return redirect(url_for("rec_page"))
        if r["active"]:
            c.execute("UPDATE recurring SET active=0 WHERE id=?", (rid,)); flash(f"Đã tạm dừng \"{r['note']}\".", "success")
        else:
            nd, today, guard = date.fromisoformat(r["next_due"]), date.today(), 0
            while nd < today and guard < 5000:      # bỏ qua các kỳ đã lỡ trong lúc tạm dừng, không ghi bù
                nd = next_occurrence(nd, r["frequency"], r["anchor_day"]); guard += 1
            c.execute("UPDATE recurring SET active=1,next_due=? WHERE id=?", (nd.isoformat(), rid))
            flash(f"Đã tiếp tục \"{r['note']}\". Kỳ kế tiếp: {vdate(nd.isoformat())}.", "success")
    return redirect(url_for("rec_page"))

GOAL_KINDS = {
    "shopping":  {"label": "Mua sắm",       "color": "#6741D9", "icon": "M6 7h12l-1 13H7L6 7zM9 7a3 3 0 0 1 6 0"},
    "travel":    {"label": "Du lịch",       "color": "#2F6FDE", "icon": "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"},
    "emergency": {"label": "Quỹ dự phòng",  "color": "#0F7B5F", "icon": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"},
    "other":     {"label": "Khác",          "color": "#C98200", "icon": "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7"},
}
app.jinja_env.globals["GOAL_KINDS"] = GOAL_KINDS
GOAL_SQL = "SELECT g.*, COALESCE((SELECT SUM(d.amount) FROM goal_deposits d WHERE d.goal_id=g.id),0) saved FROM goals g"

def goal_view(g, today=None):
    """Tính phần trăm hoàn thành, thời gian còn lại và số tiền cần tiết kiệm mỗi tháng cho một mục tiêu."""
    today = today or date.today()
    kind = GOAL_KINDS.get(g["kind"], GOAL_KINDS["other"])
    target, saved = g["target_amount"], g["saved"]
    done = saved >= target
    dl = date.fromisoformat(g["deadline"]); days_left = (dl - today).days
    remaining = max(0, target - saved)
    overdue = (not done) and days_left < 0
    if done: status = "Đã hoàn thành"
    elif overdue: status = f"Đã quá hạn {-days_left} ngày"
    elif days_left == 0: status = "Hạn hoàn thành là hôm nay"
    elif days_left < 31: status = f"Còn {days_left} ngày"
    else: status = f"Còn {round(days_left / 30.44)} tháng"
    months = max(1, math.ceil(days_left / 30.44)) if days_left > 0 else 1
    need = 0 if done else (remaining if days_left <= 0 else -(-remaining // months))
    if 0 < need and days_left > 0: need = -(-need // 1000) * 1000   # làm tròn lên hàng nghìn cho dễ nhìn
    return {"id": g["id"], "name": g["name"], "kind": g["kind"], "kind_label": kind["label"], "color": kind["color"], "icon": kind["icon"],
            "target": target, "saved": saved, "remaining": remaining, "done": done, "overdue": overdue,
            "pct_i": 100 if done else min(99, int(saved / target * 100)), "width": round(min(100, saved / target * 100), 1),
            "deadline": g["deadline"], "days_left": days_left, "status_text": status, "need": need, "months": months,
            "state": "done" if done else ("over" if overdue else "active")}

def goal_row(c, gid):
    return c.execute(GOAL_SQL + " WHERE g.id=? AND g.user_id=?", (gid, uid())).fetchone()

def read_goal_form():
    name = request.form.get("name", "").strip(); kind = request.form.get("kind", "")
    if not name or len(name) > 60 or kind not in GOAL_KINDS: raise ValueError
    target = parse_amount(request.form.get("target_amount", ""))
    if target <= 0 or target > MAX_AMOUNT: raise ValueError
    deadline = request.form.get("deadline", "")
    if not valid_date(deadline): raise ValueError
    return name, kind, target, datetime.strptime(deadline, "%Y-%m-%d").date().isoformat()

@app.route("/goals")
@login_required
def goals_page():
    status = request.args.get("status", "all"); status = status if status in ("all", "active", "done") else "all"
    with db() as c:
        rows = c.execute(GOAL_SQL + " WHERE g.user_id=?", (uid(),)).fetchall()
    views = sorted((goal_view(r) for r in rows), key=lambda v: (v["done"], v["deadline"], v["id"]))
    counts = {"all": len(views), "active": sum(1 for v in views if not v["done"]), "done": sum(1 for v in views if v["done"])}
    tot_target = sum(v["target"] for v in views); tot_saved = sum(v["saved"] for v in views)
    capped = sum(min(v["saved"], v["target"]) for v in views)
    shown = [v for v in views if status == "all" or (status == "done") == v["done"]]
    return page(GOALS, title="Mục tiêu tiết kiệm", goals=shown, counts=counts, status=status, tot_saved=tot_saved, tot_target=tot_target,
                capped=capped, rest=max(0, tot_target - capped), overall=round(capped / tot_target * 100) if tot_target else 0)

def goal_form_vals(row=None):
    if request.method == "POST":
        f = request.form
        return {"name": f.get("name", ""), "kind": f.get("kind", ""), "target_amount": f.get("target_amount", ""), "deadline": f.get("deadline", ""), "saved_amount": f.get("saved_amount", "")}
    if row:
        return {"name": row["name"], "kind": row["kind"], "target_amount": row["target_amount"], "deadline": row["deadline"], "saved_amount": ""}
    k = request.args.get("kind", "")
    return {"name": "", "kind": k if k in GOAL_KINDS else "shopping", "target_amount": "", "deadline": "", "saved_amount": ""}

@app.route("/goals/new", methods=["GET", "POST"])
@login_required
def goal_new():
    if request.method == "POST":
        try:
            name, kind, target, deadline = read_goal_form()
            if deadline < date.today().isoformat(): raise ValueError
            raw = request.form.get("saved_amount", "").strip()
            saved = parse_amount(raw) if raw else 0
            if saved < 0 or saved > MAX_AMOUNT: raise ValueError
            with db() as c:
                gid = c.execute("INSERT INTO goals(user_id,name,kind,target_amount,deadline) VALUES(?,?,?,?,?)", (uid(), name, kind, target, deadline)).lastrowid
                if saved > 0:
                    c.execute("INSERT INTO goal_deposits(goal_id,user_id,amount,dep_date,note) VALUES(?,?,?,?,?)", (gid, uid(), saved, date.today().isoformat(), "Số tiền đã tích lũy ban đầu"))
            flash("Đã tạo mục tiêu tiết kiệm.", "success")
            return redirect(url_for("goal_detail", gid=gid))
        except (ValueError, TypeError, OverflowError):
            flash("Dữ liệu không hợp lệ. Kiểm tra tên, số tiền mục tiêu và hạn hoàn thành (từ hôm nay trở đi).", "error")
    return page(GOAL_FORM, title="Tạo mục tiêu tiết kiệm", row=None, vals=goal_form_vals(), today=date.today().isoformat(), saved=0)

@app.route("/goals/<int:gid>")
@login_required
def goal_detail(gid):
    with db() as c:
        g = goal_row(c, gid)
        deps = c.execute("SELECT * FROM goal_deposits WHERE goal_id=? AND user_id=? ORDER BY dep_date DESC,id DESC", (gid, uid())).fetchall() if g else []
    if not g:
        flash("Không tìm thấy mục tiêu.", "error"); return redirect(url_for("goals_page"))
    return page(GOAL_DETAIL, title=g["name"], goal=goal_view(g), deps=deps, today=date.today().isoformat())

@app.route("/goals/<int:gid>/edit", methods=["GET", "POST"])
@login_required
def goal_edit(gid):
    with db() as c: row = goal_row(c, gid)
    if not row:
        flash("Không tìm thấy mục tiêu.", "error"); return redirect(url_for("goals_page"))
    if request.method == "POST":
        try:
            name, kind, target, deadline = read_goal_form()
            with db() as c:
                c.execute("UPDATE goals SET name=?,kind=?,target_amount=?,deadline=? WHERE id=? AND user_id=?", (name, kind, target, deadline, gid, uid()))
            flash("Đã cập nhật mục tiêu.", "success")
            return redirect(url_for("goal_detail", gid=gid))
        except (ValueError, TypeError, OverflowError):
            flash("Dữ liệu không hợp lệ. Kiểm tra tên, số tiền mục tiêu và hạn hoàn thành.", "error")
    return page(GOAL_FORM, title="Sửa mục tiêu", row=row, vals=goal_form_vals(row), today=date.today().isoformat(), saved=row["saved"])

@app.route("/goals/<int:gid>/delete", methods=["POST"])
@login_required
def goal_delete(gid):
    with db() as c: c.execute("DELETE FROM goals WHERE id=? AND user_id=?", (gid, uid()))
    flash("Đã xóa mục tiêu.", "success"); return redirect(url_for("goals_page"))

@app.route("/goals/<int:gid>/deposit", methods=["POST"])
@login_required
def goal_deposit(gid):
    with db() as c:
        g = goal_row(c, gid)
        if not g:
            flash("Không tìm thấy mục tiêu.", "error"); return redirect(url_for("goals_page"))
        try:
            amount = parse_amount(request.form.get("amount", ""))
            direction = request.form.get("direction", "add")
            raw_date = request.form.get("dep_date", "").strip() or date.today().isoformat()
            if amount <= 0 or amount > MAX_AMOUNT or direction not in ("add", "withdraw") or not valid_date(raw_date): raise ValueError
            d = datetime.strptime(raw_date, "%Y-%m-%d").date().isoformat()
            if d > date.today().isoformat(): raise ValueError
            signed = amount if direction == "add" else -amount
            new_saved = g["saved"] + signed
            if new_saved < 0:
                flash("Số tiền rút vượt quá số đã tích lũy.", "error"); return redirect(url_for("goal_detail", gid=gid))
            if new_saved > MAX_AMOUNT: raise ValueError
            c.execute("INSERT INTO goal_deposits(goal_id,user_id,amount,dep_date,note) VALUES(?,?,?,?,?)", (gid, uid(), signed, d, request.form.get("note", "").strip()[:100]))
        except (ValueError, TypeError, OverflowError):
            flash("Dữ liệu không hợp lệ. Kiểm tra số tiền và ngày (không được ở tương lai).", "error"); return redirect(url_for("goal_detail", gid=gid))
    flash("Đã ghi nhận khoản nạp." if signed > 0 else "Đã ghi nhận khoản rút.", "success")
    if g["saved"] < g["target_amount"] <= new_saved:
        flash(f"Chúc mừng! Bạn đã hoàn thành mục tiêu \"{g['name']}\".", "success")
    return redirect(url_for("goal_detail", gid=gid))

@app.route("/goals/<int:gid>/deposits/<int:did>/delete", methods=["POST"])
@login_required
def goal_deposit_delete(gid, did):
    with db() as c:
        g = goal_row(c, gid)
        d = c.execute("SELECT * FROM goal_deposits WHERE id=? AND goal_id=? AND user_id=?", (did, gid, uid())).fetchone() if g else None
        if not d:
            flash("Không tìm thấy khoản này.", "error")
        elif g["saved"] - d["amount"] < 0:
            flash("Không thể xóa vì số đã tích lũy sẽ bị âm. Hãy xóa các khoản rút liên quan trước.", "error")
        else:
            c.execute("DELETE FROM goal_deposits WHERE id=?", (did,)); flash("Đã xóa khoản này.", "success")
    return redirect(url_for("goal_detail", gid=gid) if g else url_for("goals_page"))

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
