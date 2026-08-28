"""
ML-021: FastAPI service backed by PostgreSQL (deploy-ready)
-----------------------------------------------------------------
Changes from the local prototype version:
  - DATABASE_URL read from environment (falls back to local default for
    dev), since a deployed host (Render) sets this as an env var pointing
    at Neon/wherever the Postgres instance actually lives.
  - CORS enabled so the dashboard (served from a different origin, e.g.
    Netlify) is allowed to fetch from this API in the browser.
  - New aggregate endpoints added specifically to back the dashboard's
    Executive and Inventory tabs (network-level KPIs, weekly totals,
    below-reorder-point list) — the original endpoints were all per-SKU.

Local run:  uvicorn db.api_postgres:app --reload --port 8000
Deployed:   uvicorn db.api_postgres:app --host 0.0.0.0 --port $PORT
"""

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ml021_app:ml021_pass@localhost:5432/ml021_supplychain",
)
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DB_URL, pool_pre_ping=True)

app = FastAPI(title="ML-021 API (PostgreSQL-backed)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.get("/products")
def list_products():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT sku_id, product_name, category, retail_price FROM products ORDER BY sku_id"
        )).mappings().all()
    return {"products": [dict(r) for r in rows]}


@app.get("/forecast/{sku_id}")
def forecast(sku_id: str):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT week_start, actual_units, predicted_units FROM forecast_results "
                 "WHERE sku_id = :sku ORDER BY week_start"),
            {"sku": sku_id},
        ).mappings().all()
    if not rows:
        raise HTTPException(404, f"No forecast data for {sku_id}")
    return {"sku_id": sku_id, "weeks": [dict(r) for r in rows]}


@app.get("/forecast/network/weekly")
def network_weekly_forecast():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT week_start, SUM(actual_units) AS actual, SUM(predicted_units) AS predicted "
            "FROM forecast_results GROUP BY week_start ORDER BY week_start"
        )).mappings().all()
    return {"weeks": [dict(r) for r in rows]}


@app.get("/inventory/below-reorder")
def below_reorder():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT wi.warehouse_id, wi.sku_id, p.product_name, wi.available_stock, "
            "ir.computed_reorder_point, ir.computed_safety_stock, "
            "(wi.available_stock < ir.computed_safety_stock) AS urgent "
            "FROM warehouse_inventory wi "
            "JOIN products p ON p.sku_id = wi.sku_id "
            "JOIN LATERAL ("
            "  SELECT * FROM inventory_recommendations ir2 "
            "  WHERE ir2.sku_id = wi.sku_id ORDER BY ir2.computed_at DESC LIMIT 1"
            ") ir ON true "
            "WHERE wi.available_stock < ir.computed_reorder_point "
            "ORDER BY urgent DESC, wi.warehouse_id"
        )).mappings().all()
    return {"count": len(rows), "pairs": [dict(r) for r in rows]}


@app.get("/inventory/{sku_id}")
def inventory(sku_id: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM inventory_recommendations WHERE sku_id = :sku "
                 "ORDER BY computed_at DESC LIMIT 1"),
            {"sku": sku_id},
        ).mappings().first()
    if not row:
        raise HTTPException(404, f"No inventory data for {sku_id}")
    return dict(row)


@app.get("/inventory")
def list_inventory():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT ON (ir.sku_id) ir.sku_id, p.product_name, ir.avg_daily_demand, "
            "p.lead_time_days, ir.computed_safety_stock, ir.computed_reorder_point, ir.eoq "
            "FROM inventory_recommendations ir "
            "JOIN products p ON p.sku_id = ir.sku_id "
            "ORDER BY ir.sku_id, ir.computed_at DESC"
        )).mappings().all()
    return {"inventory": [dict(r) for r in rows]}


@app.get("/executive/summary")
def executive_summary():
    with engine.connect() as conn:
        sales = conn.execute(text(
            "SELECT SUM(units_sold) AS units, SUM(total_revenue) AS revenue, "
            "SUM(gross_profit) AS profit, COUNT(*) AS n_transactions FROM sales"
        )).mappings().first()

        inv_value = conn.execute(text(
            "SELECT SUM(wi.stock_on_hand * p.unit_cost) AS value, "
            "SUM(wi.stock_on_hand) AS units_on_hand "
            "FROM warehouse_inventory wi JOIN products p ON p.sku_id = wi.sku_id"
        )).mappings().first()

        supplier_stats = conn.execute(text(
            "SELECT AVG(on_time_rate_pct) AS avg_on_time, "
            "COUNT(*) FILTER (WHERE on_time_rate_pct < 92) AS n_below_threshold, "
            "SUM(total_orders) AS total_orders FROM suppliers"
        )).mappings().first()

        below = below_reorder()

    return {
        "total_revenue": round(float(sales["revenue"] or 0), 2),
        "total_units_sold": int(sales["units"] or 0),
        "gross_profit": round(float(sales["profit"] or 0), 2),
        "n_transactions": sales["n_transactions"],
        "inventory_value": round(float(inv_value["value"] or 0), 2),
        "units_on_hand": int(inv_value["units_on_hand"] or 0),
        "avg_supplier_on_time_pct": round(float(supplier_stats["avg_on_time"] or 0), 1),
        "suppliers_below_threshold": supplier_stats["n_below_threshold"],
        "total_purchase_orders": int(supplier_stats["total_orders"] or 0),
        "pairs_below_reorder_point": below["count"],
        "pairs_urgent": sum(1 for p in below["pairs"] if p["urgent"]),
    }


@app.get("/risk/alerts")
def alerts(severity: str = None):
    query = "SELECT * FROM risk_alerts WHERE resolved = FALSE"
    params = {}
    if severity:
        query += " AND severity = :sev"
        params["sev"] = severity
    query += " ORDER BY created_at DESC"
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()
    return {"alert_count": len(rows), "alerts": [dict(r) for r in rows]}


@app.get("/suppliers/at-risk")
def suppliers_at_risk():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT supplier_id, supplier_name, on_time_rate_pct, sla_compliance_score "
            "FROM suppliers WHERE on_time_rate_pct < 92 ORDER BY on_time_rate_pct"
        )).mappings().all()
    return {"suppliers": [dict(r) for r in rows]}
