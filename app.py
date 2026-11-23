"""StockWise: a transaction-safe inventory and order API."""
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
from typing import Annotated

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(__file__).with_name("stockwise.db")

def connect():
    db = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db

def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS products(
          id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL, price_cents INTEGER NOT NULL CHECK(price_cents >= 0),
          stock INTEGER NOT NULL CHECK(stock >= 0));
        CREATE TABLE IF NOT EXISTS movements(
          id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
          delta INTEGER NOT NULL CHECK(delta <> 0), note TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY, total_cents INTEGER NOT NULL CHECK(total_cents >= 0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS order_lines(
          order_id INTEGER NOT NULL REFERENCES orders(id),
          product_id INTEGER NOT NULL REFERENCES products(id),
          quantity INTEGER NOT NULL CHECK(quantity > 0),
          unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
          PRIMARY KEY(order_id, product_id));
        """)

@asynccontextmanager
async def lifespan(_app):
    initialize()
    yield

app = FastAPI(title="StockWise Inventory API", version="1.0.0", lifespan=lifespan)

class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(ge=0)
    initial_stock: int = Field(default=0, ge=0)

class MovementIn(BaseModel):
    delta: int = Field(ne=0)
    note: str = Field(min_length=1, max_length=200)

class OrderLineIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=10000)

class OrderIn(BaseModel):
    lines: list[OrderLineIn] = Field(min_length=1, max_length=100)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/products")
def list_products():
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM products ORDER BY name")]

@app.post("/products", status_code=201)
def create_product(body: ProductIn):
    try:
        with connect() as db:
            cur = db.execute("INSERT INTO products(sku,name,price_cents,stock) VALUES(?,?,?,?)",
                             (body.sku.strip(), body.name.strip(), body.price_cents, body.initial_stock))
            if body.initial_stock:
                db.execute("INSERT INTO movements(product_id,delta,note) VALUES(?,?,?)",
                           (cur.lastrowid, body.initial_stock, "opening stock"))
            return dict(db.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "SKU already exists or product data is invalid") from exc

@app.post("/products/{product_id}/movements", status_code=201)
def add_movement(product_id: int, body: MovementIn):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        product = db.execute("SELECT stock FROM products WHERE id=?", (product_id,)).fetchone()
        if product is None:
            db.rollback()
            raise HTTPException(404, "Product not found")
        new_stock = product["stock"] + body.delta
        if new_stock < 0:
            db.rollback()
            raise HTTPException(409, "Movement would make stock negative")
        db.execute("UPDATE products SET stock=? WHERE id=?", (new_stock, product_id))
        db.execute("INSERT INTO movements(product_id,delta,note) VALUES(?,?,?)",
                   (product_id, body.delta, body.note.strip()))
        db.commit()
        return {"product_id": product_id, "stock": new_stock}

@app.post("/orders", status_code=201)
def place_order(body: OrderIn):
    quantities = {}
    for line in body.lines:
        quantities[line.product_id] = quantities.get(line.product_id, 0) + line.quantity
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        products = {}
        for product_id, quantity in quantities.items():
            row = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if row is None:
                db.rollback()
                raise HTTPException(404, f"Product {product_id} not found")
            if row["stock"] < quantity:
                db.rollback()
                raise HTTPException(409, f"Insufficient stock for {row['sku']}")
            products[product_id] = row
        total = sum(products[pid]["price_cents"] * qty for pid, qty in quantities.items())
        cur = db.execute("INSERT INTO orders(total_cents) VALUES(?)", (total,))
        order_id = cur.lastrowid
        for product_id, quantity in quantities.items():
            product = products[product_id]
            db.execute("UPDATE products SET stock=stock-? WHERE id=?", (quantity, product_id))
            db.execute("INSERT INTO order_lines VALUES(?,?,?,?)",
                       (order_id, product_id, quantity, product["price_cents"]))
            db.execute("INSERT INTO movements(product_id,delta,note) VALUES(?,?,?)",
                       (product_id, -quantity, f"order {order_id}"))
        db.commit()
        return {"id": order_id, "total_cents": total, "items": len(quantities)}

@app.get("/orders/{order_id}")
def get_order(order_id: int):
    with connect() as db:
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if order is None:
            raise HTTPException(404, "Order not found")
        lines = db.execute("""SELECT l.product_id,p.sku,p.name,l.quantity,l.unit_price_cents
                              FROM order_lines l JOIN products p ON p.id=l.product_id
                              WHERE l.order_id=? ORDER BY p.name""", (order_id,)).fetchall()
        return {**dict(order), "lines": [dict(x) for x in lines]}
