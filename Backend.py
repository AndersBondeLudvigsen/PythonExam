import os
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import numpy as np

import sqlite3
import requests
import calendar
# … rest of your imports …

from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_bcrypt import Bcrypt

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
app = Flask(__name__)
# Load a fixed secret key from environment (generate once):
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]
# Configure session cookies for cross-site usage during development
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=False  # True when using HTTPS in production
)

# Enable CORS for the Streamlit front-end on localhost:8501
CORS(app,
     supports_credentials=True,
     resources={r"/api/*": {"origins": "http://localhost:8501"}})

# Initialize Bcrypt
bcrypt = Bcrypt(app)

# Mistral API
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

# ----------------------------------------------------------------------------
# Database Helpers
# ----------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect('finance.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    ''')

    # Transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # Budgets table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            monthly_limit REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (user_id, category)
        )
    ''')

    # Goals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            target_amount REAL NOT NULL,
            current_amount REAL DEFAULT 0.0,
            due_date TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()

# Initialize the database
init_db()

# ----------------------------------------------------------------------------
# Authentication Decorator
# ----------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Uautoriseret. Log venligst ind.'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ----------------------------------------------------------------------------
# Authentication Endpoints
# ----------------------------------------------------------------------------
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Brugernavn og adgangskode er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Brugernavn er allerede taget.'}), 409

    pw_hash = bcrypt.generate_password_hash(password, rounds=12).decode('utf-8')
    cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, pw_hash))
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()

    return jsonify({'id': user_id, 'username': username}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'error': 'Brugernavn og adgangskode er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()

    if not user or not bcrypt.check_password_hash(user['password'], password):
        return jsonify({'error': 'Ugyldigt brugernavn eller adgangskode.'}), 401

    session['user_id'] = user['id']
    session['username'] = user['username']
    return jsonify({'message': 'Login successful', 'user': {'id': user['id'], 'username': user['username']}}), 200

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return jsonify({'message': 'Du er logget ud.'}), 200

@app.route('/api/status', methods=['GET'])
def get_status():
    if 'user_id' in session:
        return jsonify({'logged_in': True, 'user_id': session['user_id'], 'username': session.get('username')}), 200
    return jsonify({'logged_in': False}), 200

# ----------------------------------------------------------------------------
# Transaction Endpoints
# ----------------------------------------------------------------------------
@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM transactions WHERE user_id = ? ORDER BY date DESC', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/transactions', methods=['POST'])
@login_required
def add_transaction():
    user_id = session['user_id']
    data = request.get_json()
    category = data.get('category')
    amount = data.get('amount')
    date = data.get('date', datetime.now().strftime('%Y-%m-%d'))
    if not category or amount is None:
        return jsonify({'error': 'Kategori og beløb er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO transactions (user_id, category, amount, date) VALUES (?, ?, ?, ?)',
        (user_id, category, amount, date)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': new_id, 'user_id': user_id, 'category': category, 'amount': amount, 'date': date}), 201

@app.route('/api/transactions/<int:txn_id>', methods=['PUT'])
@login_required
def update_transaction(txn_id):
    user_id = session['user_id']
    data = request.get_json()
    category = data.get('category')
    amount = data.get('amount')
    date = data.get('date')
    if not category or amount is None or not date:
        return jsonify({'error': 'Kategori, beløb og dato er påkrævet for opdatering.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE transactions SET category = ?, amount = ?, date = ? WHERE id = ? AND user_id = ?',
        (category, amount, date, txn_id, user_id)
    )
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Transaktion ikke fundet eller du har ikke adgang.'}), 404
    conn.close()
    return jsonify({'id': txn_id, 'category': category, 'amount': amount, 'date': date}), 200

@app.route('/api/transactions/<int:txn_id>', methods=['DELETE'])
@login_required
def delete_transaction(txn_id):
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM transactions WHERE id = ? AND user_id = ?', (txn_id, user_id))
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Transaktion ikke fundet eller du har ikke adgang.'}), 404
    conn.close()
    return '', 204

@app.route('/api/transactions/summary', methods=['GET'])
@login_required
def summarize_transactions():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM transactions WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    transactions = [dict(r) for r in rows]
    summary = {
        cat: sum(t['amount'] for t in transactions if t['category'] == cat)
        for cat in {t['category'] for t in transactions}
    }
    return jsonify(summary)

@app.route('/api/transactions/monthly_summary', methods=['GET'])
@login_required
def get_monthly_spending():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT date, amount FROM transactions WHERE user_id = ? ORDER BY date', (user_id,))
    rows = cursor.fetchall()
    conn.close()

    monthly_data = {}
    for row in rows:
        date_obj = datetime.strptime(row['date'], '%Y-%m-%d')
        month_year = date_obj.strftime('%Y-%m')
        monthly_data[month_year] = monthly_data.get(month_year, 0) + row['amount']

    monthly_summary = [{'month': m, 'total_spending': s} for m, s in monthly_data.items()]
    return jsonify(monthly_summary)

# ----------------------------------------------------------------------------
# Budget Endpoints
# ----------------------------------------------------------------------------
@app.route('/api/budgets', methods=['GET'])
@login_required
def get_budgets():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM budgets WHERE user_id = ?', (user_id,))
    budgets = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(budgets)

@app.route('/api/budgets', methods=['POST'])
@login_required
def add_budget():
    user_id = session['user_id']
    data = request.get_json()
    category = data.get('category')
    monthly_limit = data.get('monthly_limit')
    if not category or monthly_limit is None:
        return jsonify({'error': 'Kategori og månedlig grænse er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?)',
            (user_id, category, monthly_limit)
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return jsonify({'id': new_id, 'user_id': user_id, 'category': category, 'monthly_limit': monthly_limit}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Du har allerede et budget for denne kategori.'}), 409

@app.route('/api/budgets/status', methods=['GET'])
@login_required
def get_budget_status():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    current_month_start = datetime.now().strftime('%Y-%m-01')
    next_month_start = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=1)
    current_month_end = (next_month_start - timedelta(days=1)).strftime('%Y-%m-%d')

    cursor.execute(
        'SELECT category, SUM(amount) as total_spent '
        'FROM transactions '
        'WHERE user_id = ? AND date BETWEEN ? AND ? '
        'GROUP BY category',
        (user_id, current_month_start, current_month_end)
    )
    spent_data = {row['category']: row['total_spent'] for row in cursor.fetchall()}

    cursor.execute('SELECT category, monthly_limit FROM budgets WHERE user_id = ?', (user_id,))
    budgets_data = [dict(row) for row in cursor.fetchall()]
    conn.close()

    budget_status = []
    for b in budgets_data:
        spent = spent_data.get(b['category'], 0.0)
        budget_status.append({
            'category': b['category'],
            'monthly_limit': b['monthly_limit'],
            'spent': spent,
            'remaining': b['monthly_limit'] - spent
        })
    return jsonify(budget_status)

# ----------------------------------------------------------------------------
# Goal Endpoints
# ----------------------------------------------------------------------------
@app.route('/api/goals', methods=['GET'])
@login_required
def get_goals():
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM goals WHERE user_id = ?', (user_id,))
    goals = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(goals)

@app.route('/api/goals', methods=['POST'])
@login_required
def add_goal():
    user_id = session['user_id']
    data = request.get_json()
    name = data.get('name')
    target_amount = data.get('target_amount')
    due_date = data.get('due_date')
    if not name or target_amount is None:
        return jsonify({'error': 'Navn og målbeløb er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO goals (user_id, name, target_amount, due_date) VALUES (?, ?, ?, ?)',
        (user_id, name, target_amount, due_date)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': new_id, 'user_id': user_id, 'name': name, 'target_amount': target_amount, 'current_amount': 0.0, 'due_date': due_date}), 201

@app.route('/api/goals/<int:goal_id>/contribute', methods=['PUT'])
@login_required
def contribute_to_goal(goal_id):
    user_id = session['user_id']
    data = request.get_json()
    amount = data.get('amount')
    if amount is None or amount <= 0:
        return jsonify({'error': 'Et positivt bidragsbeløb er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT current_amount, target_amount FROM goals WHERE id = ? AND user_id = ?', (goal_id, user_id))
    goal = cursor.fetchone()
    if not goal:
        conn.close()
        return jsonify({'error': 'Mål ikke fundet eller du har ikke adgang.'}), 404

    new_amount = min(goal['current_amount'] + amount, goal['target_amount'])
    cursor.execute(
        'UPDATE goals SET current_amount = ? WHERE id = ? AND user_id = ?',
        (new_amount, goal_id, user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'id': goal_id, 'current_amount': new_amount, 'message': 'Bidrag tilføjet.'}), 200

# ----------------------------------------------------------------------------
# AI Insight Endpoint
# ----------------------------------------------------------------------------
@app.route('/api/insight', methods=['GET'])
@login_required
def get_insight():
    user_id = session['user_id']
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT category, amount, date FROM transactions WHERE user_id = ?', (user_id,))
        transactions = [dict(r) for r in cursor.fetchall()]
        cursor.execute('SELECT category, monthly_limit FROM budgets WHERE user_id = ?', (user_id,))
        budgets = [dict(r) for r in cursor.fetchall()]
        cursor.execute('SELECT name, target_amount, current_amount, due_date FROM goals WHERE user_id = ?', (user_id,))
        goals = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not MISTRAL_API_KEY:
            return jsonify({'error': 'Mistral API-nøgle ikke konfigureret.'}), 500

        # Build prompt...
        prompt_additions = []
        # (Analysis logic same as before)
        # ...

        system_message = """Du er en venlig, hjælpsom... (instructions)..."""
        user_message = f"Transaktioner: {transactions}  Budgetter: {budgets}  Mål: {goals}"

        resp = requests.post(
            MISTRAL_API_URL,
            json={"model": "mistral-small-latest", "messages":[{"role":"system","content":system_message},{"role":"user","content":user_message}]},
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
        )
        resp.raise_for_status()
        data = resp.json()
        insight = data["choices"][0]["message"]["content"].strip()
        return jsonify({'insight': insight}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/spending_forecast', methods=['GET'])
@login_required
def spending_forecast():
    user_id = session['user_id']
    conn = get_db_connection()
    df = pd.read_sql(
        'SELECT date, amount FROM transactions WHERE user_id = ?',
        conn,
        params=(user_id,),
        parse_dates=['date']
    )
    conn.close()
    if df.empty:
        return jsonify({"message": "Ingen data"}), 200

    daily = (
        df
        .set_index('date')['amount']
        .resample('D').sum()
        .fillna(0)
        .values
    )

    today = datetime.now()
    # Brug calendar til at finde antal dage i denne måned
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_left = days_in_month - today.day

    sims = 5000
    samples = np.random.choice(daily, size=(sims, days_left), replace=True)
    spent_so_far = daily[:today.day].sum()
    total_sim = samples.sum(axis=1) + spent_so_far

    p5, p50, p95 = np.percentile(total_sim, [5, 50, 95])

    return jsonify({
        "days_left": days_left,
        "simulations": sims,
        "percentiles": {
            "5th": round(float(p5), 2),
            "50th": round(float(p50), 2),
            "95th": round(float(p95), 2)
        }
    })



@app.route('/api/weekly_pattern', methods=['GET'])
@login_required
def weekly_pattern():
    user_id = session['user_id']

    # 1) Hent dagligt forbrug og resample til alle dage
    conn = get_db_connection()
    df = pd.read_sql(
        'SELECT date, amount FROM transactions WHERE user_id = ?',
        conn, params=(user_id,), parse_dates=['date']
    )
    conn.close()

    daily_df = (
        df
        .set_index('date')['amount']
        .resample('D').sum()  # inkl. dage uden forbrug
    )

    # 2) Konverter til NumPy-array og reshape til uger × ugedag
    daily_array = daily_df.values
    n_days = daily_array.shape[0]
    n_weeks = int(np.ceil(n_days / 7))
    padded = np.pad(daily_array, (0, n_weeks * 7 - n_days), constant_values=0)
    weeks_matrix = padded.reshape(n_weeks, 7)

    # 3) Matematiske operationer
    weekly_totals = weeks_matrix.sum(axis=1)
    weekday_means = weeks_matrix.mean(axis=0)
    weekday_stds = weeks_matrix.std(axis=0)
    top_week = int(np.argmax(weekly_totals))

    return jsonify({
        'weekly_totals': weekly_totals.tolist(),
        'weekday_means': weekday_means.tolist(),
        'weekday_stds': weekday_stds.tolist(),
        'top_week_index': top_week
    }), 200
# ----------------------------------------------------------------------------
# Seed Data Endpoint
# ----------------------------------------------------------------------------

# Main entry point for running the Flask app
if __name__ == '__main__':
    # Ensure database is initialized every time in debug mode for fresh start
    # For production, you'd typically handle migrations separately or ensure init_db()
    # is only called if tables don't exist.
    init_db()  
    app.run(debug=True)

