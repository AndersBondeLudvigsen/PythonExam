"""
scripts.py
Standalone seeding-script til `finance.db` med parametriseret data-udfyldning.
Kør: python scripts.py
"""
import os
import sqlite3
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from flask_bcrypt import Bcrypt

load_dotenv()
DB_PATH = os.getenv('DB_PATH', 'finance.db')

# Initialiser Bcrypt til password-hashing
bcrypt = Bcrypt()

def seed_data(
    num_transactions=200,
    months_back=6,
    categories=None,
    budgets=None,
    goals=None
):
    """
    Seed databasen med:
      - én testbruger
      - num_transactions tilfældige transaktioner fordelt over 'months_back' måneder
      - budgets (dict: kategori->månedligt loft)
      - goals (liste af dicts med name, target_amount, due_date)
    """
    if categories is None:
        categories = ['Mad','Transport','Underholdning','Regninger','Shopping']
    if budgets is None:
        budgets = {cat: 300.0 for cat in categories}
    if goals is None:
        goals = []

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Ryd eksisterende data
    for tbl in ['transactions', 'budgets', 'goals', 'users']:
        cur.execute(f"DELETE FROM {tbl}")
    conn.commit()

    # Opret testbruger
    username = 'testuser'
    password = 'password123'
    pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
    cur.execute(
        "INSERT INTO users (username, password) VALUES (?, ?)",
        (username, pw_hash)
    )
    user_id = cur.lastrowid

    # Generer transaktioner
    today = datetime.now()
    start_date = today - timedelta(days=30 * months_back)
    for _ in range(num_transactions):
        # tilfældig dato i interval
        delta = (today - start_date).days
        rand_days = random.randint(0, delta)
        txn_date = (start_date + timedelta(days=rand_days)).strftime('%Y-%m-%d')
        # tilfældig kategori og beløb
        cat = random.choice(categories)
        # beløb: normalt fordelt omkring en kategori-gennemsnit
        avg = budgets.get(cat, 300.0) / 4
        amount = round(abs(random.gauss(avg, avg * 0.5)), 2)
        cur.execute(
            "INSERT INTO transactions (user_id, category, amount, date) VALUES (?, ?, ?, ?)",
            (user_id, cat, amount, txn_date)
        )

    # Seed budgetter
    for cat, limit in budgets.items():
        cur.execute(
            "INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?)",
            (user_id, cat, limit)
        )

    # Seed mål
    for g in goals:
        cur.execute(
            "INSERT INTO goals (user_id, name, target_amount, current_amount, due_date) VALUES (?, ?, ?, ?, ?)",
            (user_id, g['name'], g['target_amount'], g.get('current_amount', 0.0), g['due_date'])
        )

    conn.commit()
    conn.close()
    print(f"Seedet database '{DB_PATH}' med bruger '{username}', {num_transactions} transaktioner, {len(budgets)} budgetter og {len(goals)} mål.")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        monthly_limit REAL NOT NULL,
        UNIQUE(user_id, category),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        target_amount REAL NOT NULL,
        current_amount REAL DEFAULT 0.0,
        due_date TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    # Kør seed med flere datapunkter til test
    seed_data(
        num_transactions=200,
        months_back=6,
        categories=['Mad','Transport','Underholdning','Regninger','Shopping','Sundhed','Uddannelse'],
        budgets={
            'Mad': 500.0,
            'Transport': 300.0,
            'Underholdning': 250.0,
            'Regninger': 800.0,
            'Shopping': 400.0,
            'Sundhed': 200.0,
            'Uddannelse': 300.0
        },
        goals=[
            {'name': 'Ny Laptop', 'target_amount': 1500.0, 'due_date': '2025-12-31'},
            {'name': 'Ferie', 'target_amount': 5000.0, 'due_date': '2026-06-30'},
            {'name': 'Bil Reparation', 'target_amount': 2000.0, 'due_date': '2025-09-30'}
        ]
    )
