import os, random, sqlite3
from datetime import date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "insurance.db")
LINES = ["Auto", "Health", "Life", "Home", "Travel", "Commercial"]
POLICY_STATUS = ["Active", "Expired", "Cancelled"]
CLAIM_STATUS = ["Submitted", "Under Review", "Approved", "Rejected", "Paid"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT, full_name TEXT);
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, phone TEXT, dob TEXT, address TEXT, city TEXT);
CREATE TABLE IF NOT EXISTS policies(id INTEGER PRIMARY KEY, policy_no TEXT UNIQUE, customer_id INTEGER NOT NULL REFERENCES customers(id),
  line TEXT, coverage REAL, premium REAL, start_date TEXT, end_date TEXT, status TEXT DEFAULT 'Active', notes TEXT);
CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY, claim_no TEXT UNIQUE, policy_id INTEGER NOT NULL REFERENCES policies(id),
  incident_date TEXT, description TEXT, amount REAL, status TEXT DEFAULT 'Submitted', adjuster_notes TEXT, filed_on TEXT);
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, args=(), one=False):
    cur = db().execute(sql, args)
    rows = cur.fetchall()
    db().commit()
    return (rows[0] if rows else None) if one else rows

def init_db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM users").fetchone():
        con.execute("INSERT INTO users(username,password,full_name) VALUES(?,?,?)",
                    ("admin", generate_password_hash("admin123"), "Claims Administrator"))
        con.executemany("INSERT INTO customers(name,email,phone,dob,address,city) VALUES(?,?,?,?,?,?)", [
            ("Asha Raman", "asha@example.com", "9840011223", "1988-04-12", "12 Lake View Rd", "Coimbatore"),
            ("Vikram Nair", "vikram@example.com", "9876543210", "1979-11-03", "48 Gandhi St", "Chennai"),
            ("Meera Joseph", "meera@example.com", "9447012345", "1992-07-25", "7 Beach Rd", "Kochi")])
        con.executemany("INSERT INTO policies(policy_no,customer_id,line,coverage,premium,start_date,end_date,status) VALUES(?,?,?,?,?,?,?,?)", [
            ("POL-AUT-100001", 1, "Auto", 800000, 18500, "2026-01-01", "2026-12-31", "Active"),
            ("POL-HEA-100002", 1, "Health", 500000, 24000, "2026-02-01", "2027-01-31", "Active"),
            ("POL-HOM-100003", 2, "Home", 3500000, 12800, "2026-03-15", "2027-03-14", "Active"),
            ("POL-LIF-100004", 3, "Life", 5000000, 31000, "2025-06-01", "2026-05-31", "Expired")])
        con.executemany("INSERT INTO claims(claim_no,policy_id,incident_date,description,amount,status,filed_on) VALUES(?,?,?,?,?,?,?)", [
            ("CLM-20001", 1, "2026-08-14", "Rear bumper damage in parking lot collision.", 42000, "Under Review", "2026-08-15"),
            ("CLM-20002", 2, "2026-07-02", "Hospitalisation for appendectomy.", 128000, "Approved", "2026-07-05")])
        con.commit()
    con.close()

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w

@app.template_filter("money")
def money(v):
    return "₹{:,.0f}".format(v or 0)

@app.context_processor
def ctx():
    return dict(LINES=LINES, POLICY_STATUS=POLICY_STATUS, CLAIM_STATUS=CLAIM_STATUS)

def num(name):
    try: return float(request.form.get(name, 0))
    except ValueError: return -1

# ---------- public / auth ----------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),), one=True)
        if u and check_password_hash(u["password"], request.form["password"]):
            session["user"] = u["full_name"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ---------- dashboard ----------
@app.route("/dashboard")
@login_required
def dashboard():
    s = dict(
        customers=q("SELECT COUNT(*) c FROM customers", one=True)["c"],
        active=q("SELECT COUNT(*) c FROM policies WHERE status='Active'", one=True)["c"],
        premium=q("SELECT SUM(premium) s FROM policies WHERE status='Active'", one=True)["s"],
        open=q("SELECT COUNT(*) c FROM claims WHERE status IN ('Submitted','Under Review')", one=True)["c"],
        paid=q("SELECT SUM(amount) s FROM claims WHERE status IN ('Approved','Paid')", one=True)["s"])
    by_line = q("SELECT line, COUNT(*) n, SUM(premium) prem FROM policies WHERE status='Active' GROUP BY line ORDER BY prem DESC")
    by_status = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM claims GROUP BY status")}
    recent = q("""SELECT c.*, p.policy_no, p.line, cu.name FROM claims c JOIN policies p ON p.id=c.policy_id
                  JOIN customers cu ON cu.id=p.customer_id ORDER BY c.id DESC LIMIT 5""")
    expiring = q("""SELECT p.*, cu.name FROM policies p JOIN customers cu ON cu.id=p.customer_id
                    WHERE p.status='Active' AND p.end_date <= date('now','+60 day') ORDER BY p.end_date LIMIT 5""")
    return render_template("dashboard.html", s=s, by_line=by_line, by_status=by_status, recent=recent, expiring=expiring)

# ---------- customers ----------
def customer_data():
    f = request.form
    return [f["name"].strip(), f.get("email", "").strip(), f.get("phone", "").strip(),
            f.get("dob", ""), f.get("address", "").strip(), f.get("city", "").strip()]

@app.route("/customers")
@login_required
def customers():
    term = request.args.get("q", "").strip()
    rows = q("""SELECT cu.*, (SELECT COUNT(*) FROM policies WHERE customer_id=cu.id) pols FROM customers cu
                WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? ORDER BY name""", ((f"%{term}%",) * 3))
    return render_template("customers.html", rows=rows, term=term)

@app.route("/customers/add", methods=["GET", "POST"])
@login_required
def customer_add():
    if request.method == "POST":
        if not request.form.get("name", "").strip():
            flash("Customer name is required.", "error")
        else:
            q("INSERT INTO customers(name,email,phone,dob,address,city) VALUES(?,?,?,?,?,?)", customer_data())
            flash("Customer added.", "ok")
            return redirect(url_for("customers"))
    return render_template("customer_form.html", c=request.form or None)

@app.route("/customers/<int:cid>")
@login_required
def customer_view(cid):
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True) or ("", 404)
    if c == ("", 404): return c
    pols = q("SELECT * FROM policies WHERE customer_id=? ORDER BY id DESC", (cid,))
    claims = q("""SELECT c.*, p.policy_no, p.line FROM claims c JOIN policies p ON p.id=c.policy_id
                  WHERE p.customer_id=? ORDER BY c.id DESC""", (cid,))
    return render_template("customer_view.html", c=c, pols=pols, claims=claims)

@app.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def customer_edit(cid):
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
    if not c: return "", 404
    if request.method == "POST" and request.form.get("name", "").strip():
        q("UPDATE customers SET name=?,email=?,phone=?,dob=?,address=?,city=? WHERE id=?", customer_data() + [cid])
        flash("Customer updated.", "ok")
        return redirect(url_for("customer_view", cid=cid))
    return render_template("customer_form.html", c=c)

# ---------- policies ----------
@app.route("/policies")
@login_required
def policies():
    line, status = request.args.get("line", ""), request.args.get("status", "")
    sql = "SELECT p.*, cu.name FROM policies p JOIN customers cu ON cu.id=p.customer_id WHERE 1=1"
    args = []
    if line: sql += " AND p.line=?"; args.append(line)
    if status: sql += " AND p.status=?"; args.append(status)
    return render_template("policies.html", rows=q(sql + " ORDER BY p.id DESC", args), line=line, status=status)

def policy_form(p=None):
    customers = q("SELECT id,name FROM customers ORDER BY name")
    if request.method == "POST":
        f = request.form
        cov, prem = num("coverage"), num("premium")
        if cov <= 0 or prem <= 0 or not f.get("start_date") or not f.get("end_date"):
            flash("Coverage, premium and both dates are required.", "error")
        elif f["end_date"] <= f["start_date"]:
            flash("End date must be after the start date.", "error")
        else:
            if p:
                q("UPDATE policies SET line=?,coverage=?,premium=?,start_date=?,end_date=?,status=?,notes=? WHERE id=?",
                  (f["line"], cov, prem, f["start_date"], f["end_date"], f["status"], f.get("notes", ""), p["id"]))
                flash("Policy updated.", "ok")
            else:
                no = f"POL-{f['line'][:3].upper()}-{random.randint(100000, 999999)}"
                q("INSERT INTO policies(policy_no,customer_id,line,coverage,premium,start_date,end_date,status,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                  (no, f["customer_id"], f["line"], cov, prem, f["start_date"], f["end_date"], "Active", f.get("notes", "")))
                flash(f"Policy {no} issued.", "ok")
            return redirect(url_for("policies"))
    return render_template("policy_form.html", p=p or request.form or {"customer_id": request.args.get("customer_id", type=int)},
                           customers=customers, editing=bool(p))

@app.route("/policies/add", methods=["GET", "POST"])
@login_required
def policy_add():
    return policy_form()

@app.route("/policies/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def policy_edit(pid):
    p = q("SELECT p.*, cu.name FROM policies p JOIN customers cu ON cu.id=p.customer_id WHERE p.id=?", (pid,), one=True)
    return policy_form(p) if p else ("", 404)

@app.route("/policies/<int:pid>/delete", methods=["POST"])
@login_required
def policy_delete(pid):
    if q("SELECT 1 FROM claims WHERE policy_id=?", (pid,), one=True):
        flash("This policy has claims, so it can't be deleted. Set its status to Cancelled instead.", "error")
    else:
        q("DELETE FROM policies WHERE id=?", (pid,))
        flash("Policy deleted.", "ok")
    return redirect(url_for("policies"))

# ---------- claims ----------
@app.route("/claims")
@login_required
def claims():
    status = request.args.get("status", "")
    sql = """SELECT c.*, p.policy_no, p.line, cu.name FROM claims c JOIN policies p ON p.id=c.policy_id
             JOIN customers cu ON cu.id=p.customer_id"""
    rows = q(sql + (" WHERE c.status=?" if status else "") + " ORDER BY c.id DESC", (status,) if status else ())
    return render_template("claims.html", rows=rows, status=status)

def claim_form(c=None):
    pols = q("""SELECT p.id, p.policy_no, p.line, p.coverage, cu.name FROM policies p JOIN customers cu ON cu.id=p.customer_id
                WHERE p.status='Active' ORDER BY cu.name""")
    if request.method == "POST":
        f = request.form
        amt = num("amount")
        pol = q("SELECT * FROM policies WHERE id=?", (c["policy_id"] if c else f.get("policy_id"),), one=True)
        if not pol or amt <= 0 or not f.get("incident_date") or not f.get("description", "").strip():
            flash("Policy, incident date, description and a positive amount are required.", "error")
        elif amt > pol["coverage"]:
            flash(f"Claim amount exceeds the policy coverage of {money(pol['coverage'])}.", "error")
        elif not c and not (pol["start_date"] <= f["incident_date"] <= pol["end_date"]):
            flash(f"Incident date is outside the policy period ({pol['start_date']} to {pol['end_date']}).", "error")
        else:
            if c:
                q("UPDATE claims SET incident_date=?,description=?,amount=?,status=?,adjuster_notes=? WHERE id=?",
                  (f["incident_date"], f["description"].strip(), amt, f["status"], f.get("adjuster_notes", ""), c["id"]))
                flash("Claim updated.", "ok")
            else:
                no = f"CLM-{random.randint(10000, 99999)}"
                q("INSERT INTO claims(claim_no,policy_id,incident_date,description,amount,filed_on) VALUES(?,?,?,?,?,?)",
                  (no, pol["id"], f["incident_date"], f["description"].strip(), amt, date.today().isoformat()))
                flash(f"Claim {no} filed.", "ok")
            return redirect(url_for("claims"))
    return render_template("claim_form.html", c=c or request.form or {"policy_id": request.args.get("policy_id", type=int)},
                           pols=pols, editing=bool(c))

@app.route("/claims/add", methods=["GET", "POST"])
@login_required
def claim_add():
    return claim_form()

@app.route("/claims/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def claim_edit(cid):
    c = q("""SELECT c.*, p.policy_no, p.line, cu.name FROM claims c JOIN policies p ON p.id=c.policy_id
             JOIN customers cu ON cu.id=p.customer_id WHERE c.id=?""", (cid,), one=True)
    return claim_form(c) if c else ("", 404)

init_db()
if __name__ == "__main__":
    app.run(debug=True)
