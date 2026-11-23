# StockWise Inventory API

A small REST API for product catalog, stock movements, and order placement. SQLite keeps the project easy to run locally; FastAPI exposes an interactive OpenAPI page. Order creation and stock decrement happen in one database transaction.

## Course connection

Database Management System and Lab, Object Oriented Analysis & Design, Software Engineering, Design Patterns, and Human Computer Interaction.

## Run

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/docs`. The database file is created locally at `stockwise.db`.

## API overview

- `GET /products` lists products and current stock.
- `POST /products` creates a product (`sku`, `name`, `price`, `initial_stock`).
- `POST /products/{id}/movements` records a signed stock adjustment.
- `POST /orders` places a multi-line order after checking availability.
- `GET /orders/{id}` retrieves an order and its lines.

The schema uses unique SKUs and foreign keys. Monetary values are stored as integer minor units to avoid floating-point currency errors. This learning project is a local single-user service; authentication, migrations, and production deployment are future extensions.
