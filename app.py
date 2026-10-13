from flask import Flask, request, redirect, url_for, flash, Response, render_template_string
import sqlite3, csv, io
from pathlib import Path
from datetime import date, datetime

app = Flask(__name__)
app.secret_key = "local-demo-change-before-deployment"
DB = Path(__file__).with_name("chi_tieu.db")

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('income','expense')));
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('income','expense')), amount INTEGER NOT NULL CHECK(amount>0), tx_date TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS budgets(id INTEGER PRIMARY KEY, month TEXT NOT NULL, category_id INTEGER NOT NULL REFERENCES categories(id), limit_amount INTEGER NOT NULL CHECK(limit_amount>0), UNIQUE(month,category_id));
        """)
        if c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            c.executemany("INSERT INTO categories(name,kind) VALUES(?,?)", [
                ("Lương","income"),("Thưởng","income"),("Thu nhập khác","income"),
                ("Ăn uống","expense"),("Đi lại","expense"),("Mua sắm","expense"),
                ("Hóa đơn","expense"),("Giải trí","expense"),("Sức khỏe","expense"),
                ("Nhà ở","expense"),("Chi phí khác","expense")])

def money(n): return f"{int(n or 0):,}".replace(",", ".") + " đ"
app.jinja_env.filters["money"] = money

BASE = r"""
<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title or 'Chi Tiêu Thông Minh' }}</title><style>
:root{--bg:#f4f7fb;--ink:#182230;--muted:#687386;--line:#e3e9f1;--blue:#3459d6;--green:#16845b;--red:#c43d4b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Arial,sans-serif}.nav{background:white;border-bottom:1px solid var(--line);padding:16px max(18px,calc((100% - 1100px)/2));display:flex;align-items:center;gap:24px;flex-wrap:wrap}.brand{font-weight:bold;font-size:18px;margin-right:auto}.nav a,.link{color:var(--blue);text-decoration:none;font-weight:bold}.wrap{max-width:1100px;margin:28px auto;padding:0 18px}h1{margin:0 0 8px;font-size:30px}h2{font-size:18px;margin:0 0 14px}.muted{color:var(--muted)}.row{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:22px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}.card,.panel{background:white;border:1px solid var(--line);border-radius:14px;padding:20px;margin-bottom:18px;min-width:0}.stat{font-size:25px;font-weight:bold;margin:8px 0}.green{color:var(--green)}.red{color:var(--red)}.btn{display:inline-block;background:var(--blue);color:white;border:0;border-radius:8px;padding:10px 14px;text-decoration:none;font-weight:bold;cursor:pointer}.btn.light{background:#eef2f8;color:#344054}.btn.danger{background:#fff0f1;color:var(--red)}input,select,textarea{width:100%;padding:10px;border:1px solid #d6deea;border-radius:8px;background:white;font:inherit}label{font-size:13px;font-weight:bold;display:block;margin-bottom:6px}.filters,.formgrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;align-items:end}.formgrid{max-width:720px;grid-template-columns:1fr 1fr}.full{grid-column:1/-1}.tablewrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:650px}th,td{text-align:left;padding:12px 9px;border-bottom:1px solid #edf0f5;font-size:13px}th{color:var(--muted);background:#fafbfd}.right{text-align:right}.pill{padding:4px 8px;border-radius:6px;background:#eef2f8;font-size:12px}.bar{height:8px;background:#edf0f6;border-radius:9px;overflow:hidden;margin:7px 0}.fill{height:100%;background:#4f6fe8}.warn{background:#e7a23d}.over{background:#d84c5b}.flash{padding:10px 14px;border-radius:8px;background:#e8f7ef;margin-bottom:12px}.error{background:#fff0f1}.budget{margin:18px 0}.actions{display:flex;gap:10px;align-items:center}.actions form{margin:0}.small{font-size:12px}.empty{padding:22px;text-align:center;color:var(--muted)}footer{text-align:center;color:#9aa3b2;font-size:12px;padding:20px}
@media(max-width:700px){.cards{grid-template-columns:1fr}.filters,.formgrid{grid-template-columns:1fr 1fr}.wrap{padding:0 12px}.nav{gap:13px}.brand{width:100%}.stat{font-size:23px}}@media(max-width:440px){.filters,.formgrid{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head><body><div class="nav"><a class="brand" href="/">₫ Chi Tiêu Thông Minh</a><a href="/">Tổng quan</a><a href="/transactions">Giao dịch</a><a href="/budgets">Ngân sách</a><a class="btn" href="/transactions/new">+ Thêm giao dịch</a></div>
<main class="wrap">{% with msgs=get_flashed_messages(with_categories=true) %}{% for typ,msg in msgs %}<div class="flash {{ 'error' if typ=='error' else '' }}">{{ msg }}</div>{% endfor %}{% endwith %}{% block body %}{% endblock %}</main><footer>Dữ liệu lưu trong SQLite trên máy tính này</footer></body></html>
"""
DASH = r"""{% extends base %}{% block body %}
<div class="row"><div><p class="muted small">TỔNG QUAN TÀI CHÍNH</p><h1>Chi tiêu của bạn</h1><span class="muted">Theo dõi dòng tiền theo tháng.</span></div><form method="get" style="display:flex;gap:8px;align-items:end"><div><label>Tháng</label><input type="month" name="month" value="{{ month }}"></div><button class="btn">Xem</button></form></div>
<div class="cards"><div class="card"><span class="muted">Tổng thu</span><div class="stat green">{{ income|money }}</div></div><div class="card"><span class="muted">Tổng chi</span><div class="stat red">{{ expense|money }}</div></div><div class="card"><span class="muted">Số dư ròng</span><div class="stat">{{ (income-expense)|money }}</div></div></div>
<div class="panel"><h2>Chi tiêu theo danh mục</h2>{% if bycat %}{% for x in bycat %}<div style="margin:14px 0"><div class="row" style="margin:0"><span>{{ x.name }}</span><b>{{ x.total|money }}</b></div><div class="bar"><div class="fill" style="width:{{ (x.total/maxcat*100)|round }}%"></div></div></div>{% endfor %}{% else %}<div class="empty">Chưa có dữ liệu chi tiêu. <a class="link" href="/transactions/new">Thêm giao dịch</a></div>{% endif %}</div>
<div class="panel"><div class="row"><h2>Ngân sách tháng {{ month }}</h2><a class="link" href="/budgets?month={{ month }}">Quản lý ngân sách →</a></div>{% if budgets %}{% for b in budgets %}{% set pct=b.spent/b.limit_amount*100 %}<div class="budget"><div class="row" style="margin:0"><b>{{ b.name }}</b><span>{{ b.spent|money }} / {{ b.limit_amount|money }} · {{ pct|round|int }}%</span></div><div class="bar"><div class="fill {{ 'over' if pct>=100 else ('warn' if pct>=80 else '') }}" style="width:{{ [pct,100]|min }}%"></div></div>{% if pct>=80 %}<span class="red small">{{ 'Đã vượt ngân sách' if pct>=100 else 'Sắp chạm hạn mức' }}</span>{% endif %}</div>{% endfor %}{% else %}<p class="muted">Chưa đặt ngân sách cho tháng này.</p>{% endif %}</div>
<div class="panel"><div class="row"><h2>Giao dịch gần đây</h2><a class="link" href="/transactions?month={{ month }}">Xem tất cả →</a></div>{% if recent %}<div class="tablewrap"><table><thead><tr><th>Ngày</th><th>Danh mục</th><th>Ghi chú</th><th>Loại</th><th class="right">Số tiền</th><th></th></tr></thead><tbody>{% for t in recent %}<tr><td>{{ t.tx_date }}</td><td>{{ t.category }}</td><td>{{ t.note or '—' }}</td><td>{{ 'Thu' if t.kind=='income' else 'Chi' }}</td><td class="right {{ 'green' if t.kind=='income' else 'red' }}">{{ t.amount|money }}</td><td><a class="link" href="/transactions/{{ t.id }}/edit">Sửa</a></td></tr>{% endfor %}</tbody></table></div>{% else %}<p class="muted">Chưa có giao dịch.</p>{% endif %}</div>{% endblock %}"""
LIST = r"""{% extends base %}{% block body %}
<div class="row"><div><h1>Giao dịch</h1><p class="muted">Thêm, sửa, xóa và lọc các khoản thu chi.</p></div><a class="btn" href="/transactions/new">+ Thêm giao dịch</a></div>
<form class="panel filters" method="get"><div><label>Tháng</label><input type="month" name="month" value="{{ month }}"></div><div><label>Loại</label><select name="kind"><option value="">Tất cả</option><option value="income" {{ 'selected' if kind=='income' }}>Thu</option><option value="expense" {{ 'selected' if kind=='expense' }}>Chi</option></select></div><div><label>Danh mục</label><select name="category_id"><option value="">Tất cả</option>{% for c in cats %}<option value="{{ c.id }}" {{ 'selected' if category_id|string==c.id|string }}>{{ c.name }}</option>{% endfor %}</select></div><div><label>Tìm ghi chú</label><input name="q" value="{{ q }}" placeholder="Tìm kiếm..."></div><button class="btn">Lọc</button><a class="btn light" href="/transactions">Xóa lọc</a><a class="btn light" href="{{ export_url }}">Xuất CSV</a></form>
<div class="panel"><p class="muted">{{ rows|length }} giao dịch</p>{% if rows %}<div class="tablewrap"><table><thead><tr><th>Ngày</th><th>Danh mục</th><th>Ghi chú</th><th>Loại</th><th class="right">Số tiền</th><th>Thao tác</th></tr></thead><tbody>{% for t in rows %}<tr><td>{{ t.tx_date }}</td><td>{{ t.category }}</td><td>{{ t.note or '—' }}</td><td>{{ 'Thu' if t.kind=='income' else 'Chi' }}</td><td class="right {{ 'green' if t.kind=='income' else 'red' }}">{{ t.amount|money }}</td><td><div class="actions"><a class="link" href="/transactions/{{ t.id }}/edit">Sửa</a><form method="post" action="/transactions/{{ t.id }}/delete" onsubmit="return confirm('Xóa giao dịch này?')"><button class="btn danger">Xóa</button></form></div></td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="empty">Không có giao dịch phù hợp.</div>{% endif %}</div>{% endblock %}"""
FORM = r"""{% extends base %}{% block body %}<div class="row"><div><h1>{{ title }}</h1><p class="muted">Số tiền phải lớn hơn 0.</p></div><a class="btn light" href="/transactions">Quay lại</a></div>
<form class="panel formgrid" method="post"><div><label>Loại giao dịch *</label><select id="kind" name="kind"><option value="expense" {{ 'selected' if not row or row.kind=='expense' }}>Chi</option><option value="income" {{ 'selected' if row and row.kind=='income' }}>Thu</option></select></div><div><label>Số tiền (VND) *</label><input name="amount" type="number" min="1" step="1" required value="{{ row.amount if row else '' }}" placeholder="150000"></div><div><label>Ngày *</label><input name="tx_date" type="date" required value="{{ row.tx_date if row else today }}"></div><div><label>Danh mục *</label><select id="category" name="category_id" required>{% for c in cats %}<option value="{{ c.id }}" data-kind="{{ c.kind }}" {{ 'selected' if row and row.category_id==c.id }}>{{ c.name }} ({{ 'Thu' if c.kind=='income' else 'Chi' }})</option>{% endfor %}</select></div><div class="full"><label>Ghi chú</label><textarea name="note" rows="3" maxlength="300">{{ row.note if row else '' }}</textarea></div><div class="full" style="display:flex;justify-content:flex-end;gap:8px"><a class="btn light" href="/transactions">Hủy</a><button class="btn">Lưu giao dịch</button></div></form>
<script>const k=document.getElementById('kind'),c=document.getElementById('category');function filter(){let valid=false;for(const o of c.options){o.disabled=o.dataset.kind!==k.value;o.hidden=o.disabled;if(o.selected&&!o.disabled)valid=true}if(!valid){const o=[...c.options].find(x=>!x.disabled);if(o)c.value=o.value}}k.addEventListener('change',filter);filter();</script>{% endblock %}"""
BUDGETS = r"""{% extends base %}{% block body %}<div class="row"><div><h1>Ngân sách</h1><p class="muted">Thiết lập hạn mức chi theo tháng và danh mục.</p></div></div>
<div class="panel"><h2>Thiết lập ngân sách</h2><form method="post" class="filters"><div><label>Tháng</label><input type="month" name="month" value="{{ month }}" required></div><div><label>Danh mục chi</label><select name="category_id" required>{% for c in cats %}<option value="{{ c.id }}">{{ c.name }}</option>{% endfor %}</select></div><div><label>Hạn mức (VND)</label><input type="number" min="1" name="limit_amount" required placeholder="2000000"></div><button class="btn">Lưu ngân sách</button></form></div>
<div class="panel"><div class="row"><h2>Ngân sách tháng {{ month }}</h2><form method="get" style="display:flex;gap:8px"><input type="month" name="month" value="{{ month }}"><button class="btn light">Xem</button></form></div>{% if rows %}{% for b in rows %}{% set pct=b.spent/b.limit_amount*100 %}<div class="budget"><div class="row" style="margin:0"><div><b>{{ b.name }}</b><p class="muted small">{{ b.spent|money }} / {{ b.limit_amount|money }}</p></div><div class="actions"><b class="{{ 'red' if pct>=100 else ('green' if pct<80 else '') }}">{{ pct|round|int }}%</b><form method="post" action="/budgets/{{ b.id }}/delete" onsubmit="return confirm('Xóa ngân sách?')"><button class="btn danger">Xóa</button></form></div></div><div class="bar"><div class="fill {{ 'over' if pct>=100 else ('warn' if pct>=80 else '') }}" style="width:{{ [pct,100]|min }}%"></div></div><p class="small {{ 'red' if pct>=80 else 'muted' }}">{{ 'Đã vượt ngân sách' if pct>=100 else ('Đã dùng ít nhất 80% ngân sách' if pct>=80 else 'Còn lại '+((b.limit_amount-b.spent)|money)) }}</p></div>{% endfor %}{% else %}<p class="muted">Chưa có ngân sách cho tháng này.</p>{% endif %}</div>{% endblock %}"""

def page(template, **kwargs):
    return render_template_string(template, base=app.jinja_env.from_string(BASE), **kwargs)

def month_arg(value):
    try: datetime.strptime(value, "%Y-%m"); return value
    except (ValueError, TypeError): return date.today().strftime("%Y-%m")

def categories():
    with db() as c: return c.execute("SELECT * FROM categories ORDER BY kind,name").fetchall()

@app.route("/")
def dashboard():
    month=month_arg(request.args.get("month",date.today().strftime("%Y-%m")))
    with db() as c:
        totals=c.execute("SELECT COALESCE(SUM(CASE WHEN kind='income' THEN amount ELSE 0 END),0) income, COALESCE(SUM(CASE WHEN kind='expense' THEN amount ELSE 0 END),0) expense FROM transactions WHERE substr(tx_date,1,7)=?",(month,)).fetchone()
        bycat=c.execute("SELECT ca.name,SUM(t.amount) total FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE t.kind='expense' AND substr(t.tx_date,1,7)=? GROUP BY ca.id ORDER BY total DESC",(month,)).fetchall()
        recent=c.execute("SELECT t.*,ca.name category FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE substr(t.tx_date,1,7)=? ORDER BY tx_date DESC,t.id DESC LIMIT 8",(month,)).fetchall()
        budgets=c.execute("""SELECT b.*,ca.name,COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.category_id=b.category_id AND t.kind='expense' AND substr(t.tx_date,1,7)=b.month),0) spent FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.month=?""",(month,)).fetchall()
    return page(DASH, title="Tổng quan",month=month,income=totals["income"],expense=totals["expense"],bycat=bycat,maxcat=max([x["total"] for x in bycat],default=1),recent=recent,budgets=budgets)

@app.route("/transactions")
def tx_list():
    month=request.args.get("month",""); kind=request.args.get("kind",""); cat=request.args.get("category_id",""); q=request.args.get("q","").strip()
    sql="SELECT t.*,ca.name category FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE 1=1"; params=[]
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
        amount=int(amount); datetime.strptime(tx_date,"%Y-%m-%d")
        if kind not in ("income","expense") or amount<=0: raise ValueError
        with db() as c:
            category=c.execute("SELECT * FROM categories WHERE id=?",(cat,)).fetchone()
            if not category or category["kind"]!=kind: raise ValueError
            if txid: c.execute("UPDATE transactions SET kind=?,amount=?,tx_date=?,category_id=?,note=? WHERE id=?",(kind,amount,tx_date,int(cat),note,txid))
            else: c.execute("INSERT INTO transactions(kind,amount,tx_date,category_id,note) VALUES(?,?,?,?,?)",(kind,amount,tx_date,int(cat),note))
        flash("Đã lưu giao dịch.","success"); return redirect(url_for("tx_list"))
    except (ValueError,TypeError):
        flash("Dữ liệu không hợp lệ. Kiểm tra số tiền, ngày và danh mục.","error"); return None

@app.route("/transactions/new",methods=["GET","POST"])
def tx_new():
    if request.method=="POST":
        result=save_tx()
        if result: return result
    return page(FORM,title="Thêm giao dịch",row=None,cats=categories(),today=date.today().isoformat())

@app.route("/transactions/<int:txid>/edit",methods=["GET","POST"])
def tx_edit(txid):
    with db() as c: row=c.execute("SELECT * FROM transactions WHERE id=?",(txid,)).fetchone()
    if not row: flash("Không tìm thấy giao dịch.","error"); return redirect(url_for("tx_list"))
    if request.method=="POST":
        result=save_tx(txid)
        if result: return result
        with db() as c: row=c.execute("SELECT * FROM transactions WHERE id=?",(txid,)).fetchone()
    return page(FORM,title="Sửa giao dịch",row=row,cats=categories(),today=date.today().isoformat())

@app.route("/transactions/<int:txid>/delete",methods=["POST"])
def tx_delete(txid):
    with db() as c: c.execute("DELETE FROM transactions WHERE id=?",(txid,))
    flash("Đã xóa giao dịch.","success"); return redirect(url_for("tx_list"))

@app.route("/budgets",methods=["GET","POST"])
def budget_page():
    month=month_arg(request.values.get("month",date.today().strftime("%Y-%m")))
    if request.method=="POST":
        cat=request.form.get("category_id",""); amount=request.form.get("limit_amount",""); month=month_arg(request.form.get("month",month))
        try:
            amount=int(amount)
            with db() as c:
                category=c.execute("SELECT * FROM categories WHERE id=? AND kind='expense'",(cat,)).fetchone()
                if not category or amount<=0: raise ValueError
                c.execute("INSERT INTO budgets(month,category_id,limit_amount) VALUES(?,?,?) ON CONFLICT(month,category_id) DO UPDATE SET limit_amount=excluded.limit_amount",(month,int(cat),amount))
            flash("Đã lưu ngân sách.","success"); return redirect(url_for("budget_page",month=month))
        except (ValueError,TypeError): flash("Chọn danh mục và nhập hạn mức lớn hơn 0.","error")
    with db() as c:
        rows=c.execute("""SELECT b.*,ca.name,COALESCE((SELECT SUM(t.amount) FROM transactions t WHERE t.category_id=b.category_id AND t.kind='expense' AND substr(t.tx_date,1,7)=b.month),0) spent FROM budgets b JOIN categories ca ON ca.id=b.category_id WHERE b.month=? ORDER BY ca.name""",(month,)).fetchall()
    return page(BUDGETS,title="Ngân sách",month=month,rows=rows,cats=[x for x in categories() if x["kind"]=="expense"])

@app.route("/budgets/<int:bid>/delete",methods=["POST"])
def budget_delete(bid):
    with db() as c: c.execute("DELETE FROM budgets WHERE id=?",(bid,))
    flash("Đã xóa ngân sách.","success"); return redirect(url_for("budget_page"))

@app.route("/export.csv")
def export_csv():
    month=request.args.get("month",""); kind=request.args.get("kind",""); cat=request.args.get("category_id",""); q=request.args.get("q","").strip()
    sql="SELECT t.tx_date,t.kind,t.amount,ca.name category,t.note FROM transactions t JOIN categories ca ON ca.id=t.category_id WHERE 1=1"; params=[]
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
def health(): return {"status":"ok","database":DB.name}

init_db()
if __name__=="__main__":
    import os
    app.run(host=os.environ.get("HOST","127.0.0.1"),port=int(os.environ.get("PORT",5000)),debug=os.environ.get("FLASK_DEBUG")=="1")
