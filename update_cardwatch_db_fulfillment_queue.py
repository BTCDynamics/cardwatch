import os
import shutil
import sqlite3
from datetime import datetime


DB_PATH = os.path.join("instance", "cardwatch.db")


NEW_COLUMNS = [
    ("deal_id", "VARCHAR(100)"),
    ("customer_name", "VARCHAR(150)"),
    ("payment_type", "VARCHAR(50)"),
    ("deal_discount_percent", "FLOAT"),
    ("trade_credit", "FLOAT"),
    ("cash_received", "FLOAT"),
    ("deal_notes", "TEXT"),
    ("fulfillment_status", "VARCHAR(50) DEFAULT 'In Storage'"),
]


def backup_database(db_path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def get_existing_columns(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def add_missing_columns(db_path):
    if not os.path.exists(db_path):
        print(f"Database not found at: {db_path}")
        print("Make sure you run this script from the same folder as app.py.")
        return

    backup_path = backup_database(db_path)
    print(f"Backup created: {backup_path}")

    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()

    existing_columns = get_existing_columns(cursor, "card")

    for column_name, column_type in NEW_COLUMNS:
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE card ADD COLUMN {column_name} {column_type}")
            print(f"Added column: {column_name}")
        else:
            print(f"Column already exists: {column_name}")

    cursor.execute(
        """
        UPDATE card
        SET fulfillment_status = 'Needs Pulling'
        WHERE status = 'Sold'
          AND storage_location IS NOT NULL
          AND storage_location != ''
          AND (fulfillment_status IS NULL OR fulfillment_status = '' OR fulfillment_status = 'In Storage')
        """
    )

    connection.commit()
    connection.close()

    print("")
    print("Database update complete.")
    print("Existing sold cards with storage locations were backfilled as Needs Pulling when appropriate.")


if __name__ == "__main__":
    add_missing_columns(DB_PATH)
