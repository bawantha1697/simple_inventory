from flask import Flask, render_template, request, redirect, url_for, flash, abort
import os
import sqlite3

app = Flask(__name__)
app.secret_key = "dev-secret"  # needed for flash()

DB = "inventory.db"

# ---------------------- DB helpers ----------------------
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        # Products
        db.execute("""
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL DEFAULT 0,
            stock REAL NOT NULL DEFAULT 0
        )""")
        # Invoices (header)
        db.execute("""
        CREATE TABLE IF NOT EXISTS invoices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            customer_name TEXT,
            total REAL DEFAULT 0,
            tax REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            grand_total REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )""")
        # Invoice items (lines)
        db.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            qty REAL NOT NULL,
            unit_price REAL NOT NULL,
            line_total REAL NOT NULL,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        )""")

        # Seed one product if empty (so pages aren’t blank)
        c = db.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
        if c == 0:
            db.execute("INSERT INTO products(name, price, stock) VALUES(?,?,?)",
                       ("Sample Product", 100.0, 10))

# ---------------------- tiny helpers ----------------------
def fnum(val, default=0.0):
    """Parse float safely from form input."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def pos_fnum(val, default=0.0):
    v = fnum(val, default)
    return v if v >= 0 else default

# ---------------------- Dashboard ----------------------
@app.route("/")
def index():
    with get_db() as db:
        product_count = db.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        invoice_count = db.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"]
        revenue = db.execute("SELECT COALESCE(SUM(grand_total),0) rev FROM invoices").fetchone()["rev"]
        last7 = db.execute("""
            SELECT COALESCE(SUM(grand_total),0) rev7
            FROM invoices
            WHERE datetime(created_at) >= datetime('now','-7 days')
        """).fetchone()["rev7"]
        low_stock = db.execute("""
            SELECT id, name, stock
            FROM products
            WHERE stock <= 5
            ORDER BY stock ASC, name ASC
            LIMIT 10
        """).fetchall()
        recent_invoices = db.execute("""
            SELECT id, number, customer_name, grand_total, created_at
            FROM invoices
            ORDER BY id DESC
            LIMIT 5
        """).fetchall()
        top_products = db.execute("""
            SELECT p.name, SUM(ii.qty) AS qty_sold
            FROM invoice_items ii
            JOIN products p ON p.id = ii.product_id
            GROUP BY ii.product_id
            ORDER BY qty_sold DESC
            LIMIT 5
        """).fetchall()

    return render_template(
        "index.html",
        product_count=product_count,
        invoice_count=invoice_count,
        revenue=revenue,
        last7=last7,
        low_stock=low_stock,
        recent_invoices=recent_invoices,
        top_products=top_products
    )

# ---------------------- Products ----------------------
@app.route("/products")
def products():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "id_desc")
    valid_sorts = {
        "id_desc": "id DESC",
        "id_asc": "id ASC",
        "name_asc": "LOWER(name) ASC",
        "name_desc": "LOWER(name) DESC",
        "price_asc": "price ASC",
        "price_desc": "price DESC",
        "stock_asc": "stock ASC",
        "stock_desc": "stock DESC",
    }
    order_by = valid_sorts.get(sort, "id DESC")

    with get_db() as db:
        if q:
            items = db.execute(
                f"SELECT * FROM products WHERE name LIKE ? ORDER BY {order_by}",
                (f"%{q}%",),
            ).fetchall()
        else:
            items = db.execute(f"SELECT * FROM products ORDER BY {order_by}").fetchall()
    return render_template("products.html", items=items, q=q, sort=sort)

@app.route("/products/add", methods=["POST"])
def add_product():
    name = (request.form.get("name") or "").strip()
    price = pos_fnum(request.form.get("price"), 0)
    stock = pos_fnum(request.form.get("stock"), 0)
    if not name:
        flash("Product name is required.", "error")
        return redirect(url_for("products"))
    with get_db() as db:
        db.execute("INSERT INTO products(name, price, stock) VALUES(?,?,?)",
                   (name, price, stock))
    flash("Product added.", "success")
    return redirect(url_for("products"))

@app.route("/products/<int:pid>/update", methods=["POST"])
def update_product(pid):
    name = (request.form.get("name") or "").strip()
    price = pos_fnum(request.form.get("price"), 0)
    stock = pos_fnum(request.form.get("stock"), 0)
    with get_db() as db:
        db.execute("UPDATE products SET name=?, price=?, stock=? WHERE id=?",
                   (name, price, stock, pid))
    flash("Product updated.", "success")
    return redirect(url_for("products"))

@app.route("/products/<int:pid>/delete", methods=["POST"])
def delete_product(pid):
    with get_db() as db:
        db.execute("DELETE FROM products WHERE id=?", (pid,))
    flash("Product deleted.", "success")
    return redirect(url_for("products"))


# ---------------------- Invoice view ----------------------
@app.route("/invoice/<int:invoice_id>")
def view_invoice(invoice_id):
    with get_db() as db:
        inv = db.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not inv:
            abort(404)
        items = db.execute("""
           SELECT ii.*, p.name AS product_name
           FROM invoice_items ii JOIN products p ON p.id=ii.product_id
           WHERE ii.invoice_id=?
        """, (invoice_id,)).fetchall()
    return render_template("invoice.html", inv=inv, items=items)

# ---------------------- Billing ----------------------
@app.route("/billing", methods=["GET", "POST"])
def billing():
    with get_db() as db:
        products = db.execute("SELECT * FROM products ORDER BY name").fetchall()
        invoices = db.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 15").fetchall()
        if request.method == "POST":
            customer = (request.form.get("customer_name") or "").strip()
            product_id_raw = request.form.get("product_id")
            qty = pos_fnum(request.form.get("qty"), 1)
            if not product_id_raw or qty <= 0:
                flash("Please select a product and enter a valid quantity.", "error")
                return redirect(url_for("billing"))
            try:
                product_id = int(product_id_raw)
            except ValueError:
                flash("Invalid product selected.", "error")
                return redirect(url_for("billing"))
            p = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not p:
                flash("Product not found.", "error")
                return redirect(url_for("billing"))
            if float(p["stock"]) < qty:
                flash(f"Not enough stock for '{p['name']}'. In stock: {p['stock']}, requested: {qty}.", "error")
                return redirect(url_for("billing"))
            unit_price = float(p["price"])
            line_total = unit_price * qty
            cur = db.execute("INSERT INTO invoices(number, customer_name) VALUES(?,?)", (None, customer))
            inv_id = cur.lastrowid
            db.execute("INSERT INTO invoice_items(invoice_id, product_id, qty, unit_price, line_total) VALUES(?,?,?,?,?)",
                       (inv_id, product_id, qty, unit_price, line_total))
            subtotal = line_total
            tax = 0
            discount = 0
            grand = subtotal
            db.execute("UPDATE products SET stock = stock - ? WHERE id=?", (qty, product_id))
            number = f"INV-{inv_id:05d}"
            db.execute("UPDATE invoices SET number=?, total=?, tax=?, discount=?, grand_total=? WHERE id=?",
                       (number, subtotal, tax, discount, grand, inv_id))
            flash(f"Invoice {number} created.", "success")
            return redirect(url_for("billing"))
    return render_template("billing.html", products=products, invoices=invoices)

@app.route("/invoice/<int:invoice_id>/delete", methods=["POST"])
def delete_invoice(invoice_id):
    with get_db() as db:
        # Delete invoice items first (due to foreign key constraint)
        db.execute("DELETE FROM invoice_items WHERE invoice_id=?", (invoice_id,))
        db.execute("DELETE FROM invoices WHERE id=?", (invoice_id,))
    flash("Invoice deleted.", "success")
    return redirect(url_for("index"))

# ---------------------- Run ----------------------
if __name__ == "__main__":
    init_db()  # ensure tables/seed exist every run
    app.run(debug=True)

