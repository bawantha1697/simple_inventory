@app.route("/billing")
def billing():
    with get_db() as db:
        products = db.execute("SELECT * FROM products ORDER BY name").fetchall()
        invoices = db.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 15").fetchall()
    return render_template("billing.html", products=products, invoices=invoices)