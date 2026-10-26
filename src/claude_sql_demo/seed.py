"""Build the demo database: a fictional outdoor-gear shop, Bluebonnet Outfitters.

Everything here is made up. The data is generated from a fixed seed, so every
run produces the same database and the tests can rely on exact figures.
"""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    joined_on   TEXT NOT NULL
);
CREATE TABLE products (
    id          INTEGER PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price_cents INTEGER NOT NULL,
    stock       INTEGER NOT NULL
);
CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    ordered_on  TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'shipped', 'delivered', 'cancelled'))
);
CREATE TABLE order_items (
    order_id         INTEGER NOT NULL REFERENCES orders(id),
    product_id       INTEGER NOT NULL REFERENCES products(id),
    quantity         INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL
);
"""

FIRST = ["Avery", "Blake", "Casey", "Dana", "Elliot", "Frankie", "Harper", "Jordan", "Kendall", "Logan",
         "Morgan", "Parker", "Quinn", "Reese", "Riley", "Rowan", "Sage", "Skyler", "Taylor", "Wren"]
LAST = ["Alvarez", "Brooks", "Castillo", "Dalton", "Ellis", "Flores", "Garza", "Hayes", "Ibarra", "Jensen",
        "Keller", "Lopez", "Mendoza", "Nolan", "Ortiz", "Pruitt", "Ramos", "Salinas", "Tate", "Vega"]
PLACES = [("Austin", "TX"), ("San Antonio", "TX"), ("Houston", "TX"), ("Dallas", "TX"), ("El Paso", "TX"),
          ("Lubbock", "TX"), ("Waco", "TX"), ("Tulsa", "OK"), ("Santa Fe", "NM"), ("Shreveport", "LA")]
PRODUCTS = [
    ("TENT-2P", "Two-person backpacking tent", "Camping", 18900),
    ("TENT-4P", "Four-person family tent", "Camping", 27900),
    ("BAG-20F", "20°F sleeping bag", "Camping", 12900),
    ("PAD-INF", "Inflatable sleeping pad", "Camping", 7900),
    ("STOVE-CN", "Canister camp stove", "Cooking", 4900),
    ("POT-SET", "Nesting cook set", "Cooking", 3900),
    ("COOLER-45", "45-quart cooler", "Cooking", 19900),
    ("PACK-40L", "40L daypack", "Packs", 11900),
    ("PACK-65L", "65L backpacking pack", "Packs", 24900),
    ("BOOT-HK", "Waterproof hiking boots", "Footwear", 15900),
    ("SOCK-WL3", "Wool hiking socks, 3 pairs", "Footwear", 2900),
    ("JKT-RAIN", "Packable rain jacket", "Clothing", 9900),
    ("HAT-SUN", "Wide-brim sun hat", "Clothing", 3400),
    ("LAMP-HD", "Rechargeable headlamp", "Accessories", 4400),
    ("BOTTLE-1L", "Insulated 1L bottle", "Accessories", 3200),
    ("FILTER-SQ", "Squeeze water filter", "Accessories", 3900),
]
STATUSES = ["delivered"] * 7 + ["shipped"] * 2 + ["pending", "cancelled"]


def build(path: Path, seed: int = 2026) -> Path:
    """Create (or replace) the demo database at `path`."""
    rng = random.Random(seed)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)

    customers = []
    for i in range(1, 61):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        city, state = rng.choice(PLACES)
        joined = date(2024, 1, 1) + timedelta(days=rng.randrange(600))
        customers.append((i, f"{first} {last}", f"{first}.{last}.{i}@example.com".lower(), city, state, joined.isoformat()))
    db.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)", customers)

    db.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?)",
        [(i, sku, name, cat, price, rng.randrange(0, 80)) for i, (sku, name, cat, price) in enumerate(PRODUCTS, 1)],
    )

    order_id = 0
    for day in range(365):
        on = date(2025, 10, 1) + timedelta(days=day)
        for _ in range(rng.choice([0, 1, 1, 2, 2, 3])):
            order_id += 1
            status = rng.choice(STATUSES) if day < 350 else rng.choice(["pending", "shipped"])
            db.execute("INSERT INTO orders VALUES (?, ?, ?, ?)", (order_id, rng.randrange(1, 61), on.isoformat(), status))
            for pid in rng.sample(range(1, len(PRODUCTS) + 1), rng.choice([1, 1, 2, 3])):
                db.execute(
                    "INSERT INTO order_items VALUES (?, ?, ?, ?)",
                    (order_id, pid, rng.choice([1, 1, 1, 2]), PRODUCTS[pid - 1][3]),
                )
    db.commit()
    db.close()
    return path


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "bluebonnet.db")
    print(f"Built {build(target)}")
