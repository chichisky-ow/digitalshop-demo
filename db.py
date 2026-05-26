import aiosqlite
import re

from config import DB_PATH


def split_license_key_value(value: str) -> list[str]:
    parts = re.split(r"[\r\n,;]+", (value or "").strip())
    keys = []
    seen = set()
    for part in parts:
        key = " ".join(part.strip().split())
        if key and key not in seen:
            keys.append(key)
            seen.add(key)
    return keys


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                total_spent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id TEXT NOT NULL,
                license_key TEXT NOT NULL UNIQUE,
                used INTEGER DEFAULT 0,
                used_by INTEGER,
                used_order_id INTEGER,
                used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                product_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                payment_code TEXT NOT NULL UNIQUE,
                status TEXT DEFAULT 'pending',
                key_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP,
                cancelled_at TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                group_title TEXT NOT NULL,
                emoji TEXT DEFAULT '',
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                days INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sepay_transactions (
                sepay_id TEXT PRIMARY KEY,
                payment_code TEXT,
                amount INTEGER DEFAULT 0,
                raw_body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reseller_prices (
                user_id INTEGER NOT NULL,
                product_id TEXT NOT NULL,
                price INTEGER NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                PRIMARY KEY(user_id, product_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Lightweight migrations for older databases shipped with this project.
        await ensure_column(db, "keys", "used_order_id", "INTEGER")
        await ensure_column(db, "keys", "created_at", "TIMESTAMP")
        await ensure_column(db, "orders", "cancelled_at", "TIMESTAMP")
        await ensure_column(db, "products", "group_title", "TEXT NOT NULL DEFAULT ''")
        await ensure_column(db, "products", "days", "INTEGER DEFAULT 0")
        await ensure_column(db, "products", "active", "INTEGER DEFAULT 1")
        await ensure_column(db, "products", "sort_order", "INTEGER DEFAULT 0")
        await ensure_column(db, "products", "updated_at", "TIMESTAMP")
        await ensure_column(db, "reseller_prices", "active", "INTEGER DEFAULT 1")
        await ensure_column(db, "reseller_prices", "created_at", "TIMESTAMP")
        await ensure_column(db, "reseller_prices", "updated_at", "TIMESTAMP")
        await seed_products(db)
        await db.commit()


async def ensure_column(db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    existing = {row[1] for row in rows}
    if column not in existing:
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def seed_products(db: aiosqlite.Connection) -> None:
    from products import GROUPS, PRODUCTS

    cursor = await db.execute("SELECT COUNT(*) FROM products")
    row = await cursor.fetchone()
    if row and int(row[0]) > 0:
        return

    sort_order = 0
    for product_id, product in PRODUCTS.items():
        sort_order += 10
        group_id = product["group"]
        group = GROUPS.get(group_id, {"title": group_id})
        await db.execute(
            """
            INSERT OR IGNORE INTO products(
                product_id, group_id, group_title, emoji, name, price, days, active, sort_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                product_id,
                group_id,
                group["title"],
                product.get("emoji", ""),
                product["name"],
                int(product["price"]),
                extract_days_from_product_id(product_id),
                sort_order,
            ),
        )


def extract_days_from_product_id(product_id: str) -> int:
    tail = product_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


async def upsert_user(user) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users(user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user.id, user.username or "", user.first_name or ""),
        )
        await db.commit()


async def create_order(user, product_id: str, amount: int, payment_code: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO orders(user_id, username, product_id, amount, payment_code)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user.id, user.username or "", product_id, amount, payment_code),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return str(row[0]) if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        await db.commit()


async def delete_setting(key: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        await db.commit()


async def get_order(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        return await cursor.fetchone()


async def get_order_by_code(payment_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders WHERE payment_code = ?",
            (payment_code,),
        )
        return await cursor.fetchone()


async def get_order_with_key(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                o.*,
                k.license_key
            FROM orders o
            LEFT JOIN keys k ON k.id = o.key_id
            WHERE o.id = ?
            """,
            (order_id,),
        )
        return await cursor.fetchone()


async def find_order(query: str):
    value = (query or "").strip()
    if not value:
        return None

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if value.isdigit():
            cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (int(value),))
            order = await cursor.fetchone()
            if order:
                return order

        cursor = await db.execute(
            """
            SELECT * FROM orders
            WHERE UPPER(payment_code) = UPPER(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (value,),
        )
        order = await cursor.fetchone()
        if order:
            return order

        cursor = await db.execute(
            """
            SELECT * FROM orders
            WHERE UPPER(payment_code) LIKE UPPER(?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (f"%{value}%",),
        )
        return await cursor.fetchone()


async def get_pending_order_by_code(payment_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM orders
            WHERE payment_code = ? AND status = 'pending'
            """,
            (payment_code,),
        )
        return await cursor.fetchone()


async def get_pending_order_by_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM orders
            WHERE user_id = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return await cursor.fetchone()


async def get_latest_order_by_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM orders
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        return await cursor.fetchone()


async def cancel_all_old_pending_orders(user_id: int, keep_order_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE orders
            SET status = 'cancelled',
                cancelled_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND status = 'pending' AND id != ?
            """,
            (user_id, keep_order_id),
        )
        await db.commit()
        return cursor.rowcount


async def find_pending_order_by_content(content: str):
    normalized = (content or "").upper().strip()

    match = re.search(r"\bMUA\s+([A-Z0-9]{6,20})\b", normalized)
    if match:
        payment_code = "MUA " + match.group(1)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM orders WHERE UPPER(payment_code) = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (payment_code,),
            )
            row = await cursor.fetchone()
            if row:
                return row

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders WHERE status = 'pending' ORDER BY id DESC LIMIT 50"
        )
        rows = await cursor.fetchall()

    for row in rows:
        if row["payment_code"].upper() in normalized:
            return row
    return None


async def record_sepay_transaction(sepay_id: str, payment_code: str, amount: int, raw_body: str) -> bool:
    if not sepay_id:
        return True

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO sepay_transactions(sepay_id, payment_code, amount, raw_body)
            VALUES (?, ?, ?, ?)
            """,
            (str(sepay_id), payment_code, int(amount), raw_body),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_order_status(order_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        if status == "cancelled":
            await db.execute(
                """
                UPDATE orders
                SET status = 'cancelled', cancelled_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (order_id,),
            )
        else:
            await db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        await db.commit()


async def cancel_pending_order(order_id: int, user_id: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = await cursor.fetchone()

        if not order:
            return {"ok": False, "msg": "order_not_found"}

        if user_id is not None and int(order["user_id"]) != int(user_id):
            return {"ok": False, "msg": "not_owner", "order": order}

        if order["status"] != "pending":
            return {"ok": False, "msg": f"order_{order['status']}", "order": order}

        await db.execute(
            """
            UPDATE orders
            SET status = 'cancelled',
                cancelled_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (order_id,),
        )
        await db.commit()
        return {"ok": True, "msg": "cancelled", "order": order}


async def add_key(product_id: str, license_key: str) -> bool:
    key_parts = split_license_key_value(license_key)
    if len(key_parts) != 1:
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO keys(product_id, license_key, used)
            VALUES (?, ?, 0)
            """,
            (product_id, key_parts[0]),
        )
        await db.commit()
        return cursor.rowcount > 0


async def fix_composite_unused_keys() -> dict:
    fixed_rows = 0
    inserted_keys = 0
    skipped_used = 0

    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                SELECT *
                FROM keys
                WHERE license_key LIKE '%' || char(10) || '%'
                   OR license_key LIKE '%' || char(13) || '%'
                   OR license_key LIKE '%,%'
                   OR license_key LIKE '%;%'
                ORDER BY id ASC
                """
            )
            rows = await cursor.fetchall()

            for row in rows:
                key_parts = split_license_key_value(row["license_key"])
                if len(key_parts) <= 1:
                    continue

                if int(row["used"] or 0) != 0:
                    skipped_used += 1
                    continue

                await db.execute(
                    "UPDATE keys SET license_key = ? WHERE id = ? AND used = 0",
                    (key_parts[0], row["id"]),
                )
                fixed_rows += 1

                for key_part in key_parts[1:]:
                    cursor = await db.execute(
                        """
                        INSERT OR IGNORE INTO keys(product_id, license_key, used)
                        VALUES (?, ?, 0)
                        """,
                        (row["product_id"], key_part),
                    )
                    inserted_keys += max(cursor.rowcount, 0)

            await db.commit()
            return {
                "fixed_rows": fixed_rows,
                "inserted_keys": inserted_keys,
                "skipped_used": skipped_used,
            }
        except Exception:
            await db.rollback()
            raise


async def fix_paid_order_composite_key(order_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                SELECT o.*, k.license_key
                FROM orders o
                JOIN keys k ON k.id = o.key_id
                WHERE o.id = ? AND o.status = 'paid'
                """,
                (order_id,),
            )
            order = await cursor.fetchone()
            if not order:
                await db.rollback()
                return {"ok": False, "msg": "paid_order_not_found"}

            key_parts = split_license_key_value(order["license_key"])
            if len(key_parts) <= 1:
                await db.rollback()
                return {"ok": True, "msg": "already_clean", "kept_key": order["license_key"], "blocked_keys": 0}

            kept_key = key_parts[0]
            await db.execute(
                "UPDATE keys SET license_key = ? WHERE id = ?",
                (kept_key, order["key_id"]),
            )

            blocked_keys = 0
            for key_part in key_parts[1:]:
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO keys(product_id, license_key, used, used_by, used_order_id, used_at)
                    VALUES (?, ?, 1, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                    """,
                    (order["product_id"], key_part, order["user_id"], order["id"], order["paid_at"]),
                )
                if cursor.rowcount > 0:
                    blocked_keys += 1
                    continue

                cursor = await db.execute(
                    """
                    UPDATE keys
                    SET used = 1,
                        used_by = ?,
                        used_order_id = ?,
                        used_at = COALESCE(used_at, CURRENT_TIMESTAMP)
                    WHERE license_key = ? AND used = 0
                    """,
                    (order["user_id"], order["id"], key_part),
                )
                blocked_keys += max(cursor.rowcount, 0)

            await db.commit()
            return {"ok": True, "msg": "fixed", "kept_key": kept_key, "blocked_keys": blocked_keys}
        except Exception:
            await db.rollback()
            raise


async def delete_unused_key(license_key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM keys WHERE license_key = ?", (license_key.strip(),))
        key = await cursor.fetchone()
        if not key:
            return {"ok": False, "msg": "key_not_found"}
        if int(key["used"] or 0) != 0:
            return {"ok": False, "msg": "key_already_used", "key": key}

        await db.execute("DELETE FROM keys WHERE id = ? AND used = 0", (key["id"],))
        await db.commit()
        return {"ok": True, "msg": "deleted", "key": key}


async def clear_all_keys() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*), COALESCE(SUM(used), 0) FROM keys")
        total, used = await cursor.fetchone()
        await db.execute("DELETE FROM keys")
        await db.commit()
        return {"ok": True, "deleted": int(total or 0), "used_deleted": int(used or 0)}


async def clear_keys_for_product(product_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM products WHERE product_id = ?", (product_id,))
        if not await cursor.fetchone():
            return {"ok": False, "msg": "product_not_found", "deleted": 0}

        cursor = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(used), 0) FROM keys WHERE product_id = ?",
            (product_id,),
        )
        total, used = await cursor.fetchone()
        await db.execute("DELETE FROM keys WHERE product_id = ?", (product_id,))
        await db.commit()
        return {"ok": True, "deleted": int(total or 0), "used_deleted": int(used or 0)}


async def clear_keys_for_group(group_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM products WHERE group_id = ?", (group_id,))
        product_count = int((await cursor.fetchone())[0] or 0)
        if product_count <= 0:
            return {"ok": False, "msg": "group_not_found", "deleted": 0, "product_count": 0}

        cursor = await db.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(used), 0)
            FROM keys
            WHERE product_id IN (SELECT product_id FROM products WHERE group_id = ?)
            """,
            (group_id,),
        )
        total, used = await cursor.fetchone()
        await db.execute(
            """
            DELETE FROM keys
            WHERE product_id IN (SELECT product_id FROM products WHERE group_id = ?)
            """,
            (group_id,),
        )
        await db.commit()
        return {
            "ok": True,
            "deleted": int(total or 0),
            "used_deleted": int(used or 0),
            "product_count": product_count,
        }


async def get_products(active_only: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = "WHERE active = 1" if active_only else ""
        cursor = await db.execute(
            f"""
            SELECT * FROM products
            {where}
            ORDER BY group_id,
                     CASE WHEN days > 0 THEN days ELSE sort_order END,
                     price,
                     product_id
            """
        )
        return await cursor.fetchall()


async def get_product(product_id: str, active_only: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if active_only:
            cursor = await db.execute(
                "SELECT * FROM products WHERE product_id = ? AND active = 1",
                (product_id,),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM products WHERE product_id = ?",
                (product_id,),
            )
        return await cursor.fetchone()


async def set_reseller_price(user_id: int, product_id: str, price: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM products WHERE product_id = ?", (product_id,))
        if not await cursor.fetchone():
            return {"ok": False, "msg": "product_not_found"}

        await db.execute(
            """
            INSERT INTO reseller_prices(user_id, product_id, price, active, updated_at)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, product_id) DO UPDATE SET
                price = excluded.price,
                active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (int(user_id), product_id, int(price)),
        )
        await db.commit()
        return {"ok": True, "msg": "updated"}


async def remove_reseller_price(user_id: int, product_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE reseller_prices
            SET active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND product_id = ? AND active = 1
            """,
            (int(user_id), product_id),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return {"ok": False, "msg": "reseller_price_not_found"}
        return {"ok": True, "msg": "removed"}


async def get_reseller_price(user_id: int, product_id: str) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT price
            FROM reseller_prices
            WHERE user_id = ? AND product_id = ? AND active = 1
            """,
            (int(user_id), product_id),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else None


async def get_effective_price(user_id: int, product_id: str, default_price: int) -> int:
    reseller_price = await get_reseller_price(user_id, product_id)
    return int(reseller_price) if reseller_price is not None else int(default_price)


async def get_reseller_prices(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT rp.user_id, rp.product_id, rp.price, rp.active, rp.updated_at,
                   p.name AS product_name, p.group_title,
                   u.username, u.first_name
            FROM reseller_prices rp
            LEFT JOIN products p ON p.product_id = rp.product_id
            LEFT JOIN users u ON u.user_id = rp.user_id
            WHERE rp.active = 1
            ORDER BY rp.updated_at DESC, rp.user_id, rp.product_id
            LIMIT ?
            """,
            (int(limit),),
        )
        return await cursor.fetchall()


async def upsert_product(
    product_id: str,
    group_id: str,
    group_title: str,
    emoji: str,
    name: str,
    price: int,
    days: int,
    active: int = 1,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO products(
                product_id, group_id, group_title, emoji, name, price, days, active, sort_order, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(product_id) DO UPDATE SET
                group_id = excluded.group_id,
                group_title = excluded.group_title,
                emoji = excluded.emoji,
                name = excluded.name,
                price = excluded.price,
                days = excluded.days,
                active = excluded.active,
                sort_order = excluded.sort_order,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                product_id,
                group_id,
                group_title,
                emoji,
                name,
                int(price),
                int(days),
                int(active),
                int(days) if int(days) > 0 else 9999,
            ),
        )
        await db.commit()


async def update_product_fields(product_id: str, *, price: int | None = None, days: int | None = None, name: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM products WHERE product_id = ?", (product_id,))
        product = await cursor.fetchone()
        if not product:
            return {"ok": False, "msg": "product_not_found"}

        new_days = int(days) if days is not None else int(product["days"] or 0)
        new_name = name if name is not None else product["name"]
        new_price = int(price) if price is not None else int(product["price"])
        sort_order = new_days if new_days > 0 else int(product["sort_order"] or 9999)

        await db.execute(
            """
            UPDATE products
            SET name = ?,
                price = ?,
                days = ?,
                sort_order = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE product_id = ?
            """,
            (new_name, new_price, new_days, sort_order, product_id),
        )
        await db.commit()
        return {"ok": True, "msg": "updated"}


async def set_product_active(product_id: str, active: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE products
            SET active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE product_id = ?
            """,
            (int(active), product_id),
        )
        await db.commit()
        if cursor.rowcount <= 0:
            return {"ok": False, "msg": "product_not_found"}
        return {"ok": True, "msg": "updated"}


async def get_stock_count(product_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM keys WHERE product_id = ? AND used = 0",
            (product_id,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def get_stock():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT product_id, COUNT(*) FROM keys WHERE used = 0 GROUP BY product_id"
        )
        return await cursor.fetchall()


async def get_stock_detail(product_id: str, limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN used = 0 THEN 1 ELSE 0 END) AS unused,
                SUM(CASE WHEN used = 1 THEN 1 ELSE 0 END) AS used
            FROM keys
            WHERE product_id = ?
            """,
            (product_id,),
        )
        summary = await cursor.fetchone()

        cursor = await db.execute(
            """
            SELECT id, license_key, created_at
            FROM keys
            WHERE product_id = ? AND used = 0
            ORDER BY id ASC
            LIMIT ?
            """,
            (product_id, limit),
        )
        unused_keys = await cursor.fetchall()
        return {"summary": summary, "unused_keys": unused_keys}


async def get_low_stock(threshold: int = 3):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                p.product_id,
                p.name,
                COALESCE(SUM(CASE WHEN k.used = 0 THEN 1 ELSE 0 END), 0) AS stock
            FROM products p
            LEFT JOIN keys k ON k.product_id = p.product_id
            WHERE p.active = 1
            GROUP BY p.product_id, p.name
            HAVING stock <= ?
            ORDER BY stock ASC, p.product_id
            """,
            (threshold,),
        )
        return await cursor.fetchall()


async def get_recent_orders(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return await cursor.fetchall()


async def get_pending_orders(limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM orders
            WHERE status = 'pending'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone()


async def get_all_users(limit: int = 10000):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT * FROM users
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()


async def get_user_keys(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                o.id,
                o.product_id,
                o.amount,
                o.paid_at,
                k.license_key
            FROM orders o
            JOIN keys k ON k.id = o.key_id
            WHERE o.user_id = ? AND o.status = 'paid'
            ORDER BY o.id DESC
            LIMIT 30
            """,
            (user_id,),
        )
        return await cursor.fetchall()


async def get_user_order_history(user_id: int, limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                o.id,
                o.product_id,
                o.amount,
                o.payment_code,
                o.status,
                o.created_at,
                o.paid_at,
                o.cancelled_at,
                k.license_key
            FROM orders o
            LEFT JOIN keys k ON k.id = o.key_id
            WHERE o.user_id = ?
            ORDER BY o.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return await cursor.fetchall()


async def get_revenue_stats(period: str = "today"):
    filters = {
        "today": "date(paid_at) = date('now', 'localtime')",
        "7d": "paid_at >= datetime('now', '-7 days', 'localtime')",
        "month": "strftime('%Y-%m', paid_at) = strftime('%Y-%m', 'now', 'localtime')",
        "all": "1 = 1",
    }
    where = filters.get(period, filters["today"])

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            f"""
            SELECT
                COUNT(*) AS orders,
                COALESCE(SUM(amount), 0) AS revenue
            FROM orders
            WHERE status = 'paid' AND {where}
            """
        )
        summary = await cursor.fetchone()

        cursor = await db.execute(
            f"""
            SELECT product_id, COUNT(*) AS orders, COALESCE(SUM(amount), 0) AS revenue
            FROM orders
            WHERE status = 'paid' AND {where}
            GROUP BY product_id
            ORDER BY revenue DESC
            """
        )
        by_product = await cursor.fetchall()
        return {"summary": summary, "by_product": by_product}


async def get_admin_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_orders,
                SUM(CASE WHEN status = 'paid' THEN 1 ELSE 0 END) AS paid_orders,
                SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_orders,
                COALESCE(SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END), 0) AS total_revenue,
                COALESCE(SUM(CASE WHEN status = 'paid' AND date(paid_at) = date('now', 'localtime') THEN amount ELSE 0 END), 0) AS today_revenue
            FROM orders
            """
        )
        orders = await cursor.fetchone()

        cursor = await db.execute("SELECT COUNT(*) AS users FROM users")
        users = await cursor.fetchone()

        cursor = await db.execute("SELECT COUNT(*) AS unused_keys FROM keys WHERE used = 0")
        keys = await cursor.fetchone()

        return {"orders": orders, "users": users, "keys": keys}


async def get_sepay_logs(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT sepay_id, payment_code, amount, raw_body, created_at
            FROM sepay_transactions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()


async def fulfill_order(order_id: int):
    async with aiosqlite.connect(DB_PATH, isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
            order = await cursor.fetchone()
            if not order:
                await db.rollback()
                return {"ok": False, "msg": "order_not_found"}

            if order["status"] == "paid":
                await db.rollback()
                return {"ok": True, "msg": "already_paid", "already": True, "order": order}

            if order["status"] != "pending":
                await db.rollback()
                return {"ok": False, "msg": f"order_{order['status']}", "order": order}

            cursor = await db.execute(
                """
                SELECT * FROM keys
                WHERE product_id = ? AND used = 0
                ORDER BY id ASC
                LIMIT 1
                """,
                (order["product_id"],),
            )
            key = await cursor.fetchone()
            if not key:
                await db.rollback()
                return {"ok": False, "msg": "out_of_stock", "order": order}

            key_parts = split_license_key_value(key["license_key"])
            if len(key_parts) > 1:
                cursor = await db.execute(
                    """
                    UPDATE keys
                    SET license_key = ?
                    WHERE id = ? AND used = 0
                    """,
                    (key_parts[0], key["id"]),
                )
                if cursor.rowcount == 0:
                    await db.rollback()
                    return {"ok": False, "msg": "key_race_condition_retry", "order": order}

                for key_part in key_parts[1:]:
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO keys(product_id, license_key, used)
                        VALUES (?, ?, 0)
                        """,
                        (order["product_id"], key_part),
                    )
                key = dict(key)
                key["license_key"] = key_parts[0]

            cursor = await db.execute(
                """
                UPDATE keys
                SET used = 1,
                    used_by = ?,
                    used_order_id = ?,
                    used_at = CURRENT_TIMESTAMP
                WHERE id = ? AND used = 0
                """,
                (order["user_id"], order["id"], key["id"]),
            )
            if cursor.rowcount == 0:
                await db.rollback()
                return {"ok": False, "msg": "key_race_condition_retry", "order": order}

            cursor = await db.execute(
                """
                UPDATE orders
                SET status = 'paid',
                    key_id = ?,
                    paid_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (key["id"], order["id"]),
            )
            if cursor.rowcount == 0:
                await db.rollback()
                return {"ok": True, "msg": "already_paid", "already": True, "order": order}

            await db.execute(
                """
                UPDATE users
                SET total_spent = total_spent + ?
                WHERE user_id = ?
                """,
                (order["amount"], order["user_id"]),
            )
            await db.commit()

        except Exception:
            await db.rollback()
            raise

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        paid_order = await cursor.fetchone()
        return {"ok": True, "msg": "paid", "order": paid_order, "key": key, "already": False}
