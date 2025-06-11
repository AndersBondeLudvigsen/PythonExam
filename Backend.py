import os
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import numpy as np
import sqlite3
import requests
import random
import time
import calendar
from datetime import datetime, timedelta
from functools import wraps
import json # <-- NY: Til parsing af JSON fra Mistral

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_bcrypt import Bcrypt

import chromadb # <-- NY: Importer ChromaDB

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=False
)
CORS(app,
     supports_credentials=True,
     resources={r"/api/*": {"origins": "http://localhost:8501"}})
bcrypt = Bcrypt(app)

# Mistral API
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_EMBEDDING_API_URL = "https://api.mistral.ai/v1/embeddings" # <-- NY: URL til Mistral's embedding API

# ----------------------------------------------------------------------------
# ChromaDB Configuration (NY: Genintroduceret for RAG og Embeddings)
# ----------------------------------------------------------------------------
CHROMA_DB_PATH = "./chroma_db"
# Initialiser ChromaDB-klienten, der gemmer data permanent
chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
# Hent eller opret 'collections' (svarende til tabeller) i ChromaDB
# Disse vil gemme embeddings for transaktioner, budgetter og mål
transactions_collection = chroma_client.get_or_create_collection(name="transactions_collection")
budgets_collection = chroma_client.get_or_create_collection(name="budgets_collection")
goals_collection = chroma_client.get_or_create_collection(name="goals_collection")

# ----------------------------------------------------------------------------
# Embedding Helper (NY: Funktion til at generere og gemme embeddings)
# ----------------------------------------------------------------------------
def generate_and_store_embedding(collection, doc_id, text_to_embed, metadata, max_retries=3):
    """
    Genererer en embedding for den givne tekst ved hjælp af Mistral AI's embedding-model
    og gemmer den i den specificerede ChromaDB collection.
    Ved 429-fejl prøves op til `max_retries` gange med stigende ventetid.
    """
    if not MISTRAL_API_KEY:
        print("Advarsel: MISTRAL_API_KEY er ikke sat. Kan ikke generere embeddings.")
        return

    retries = 0
    while True:
        try:
            resp = requests.post(
                MISTRAL_EMBEDDING_API_URL,
                json={
                    "model": "mistral-embed",
                    "input": [text_to_embed]
                },
                headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
            )
            resp.raise_for_status()
            embedding = resp.json()["data"][0]["embedding"]

            # Tilføj/opdater i Chro½qmaDB
            collection.add(
                embeddings=[embedding],
                documents=[text_to_embed],
                metadatas=[metadata],
                ids=[str(doc_id)]
            )
            print(f"Embedding genereret og gemt for ID {doc_id} i {collection.name}.")
            break

        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status == 429 and retries < max_retries:
                wait = 2 ** retries  # 1s, 2s, 4s, …
                print(f"Rate limit for ID {doc_id}, retry {retries+1} efter {wait}s…")
                time.sleep(wait)
                retries += 1
                continue
            print(f"Fejl ved generering/lagring af embedding for ID {doc_id}: {e}")
            break

# ----------------------------------------------------------------------------
# Database Helpers (Uændret, men 'init_db' påkalder nu ChromaDB initialisering indirekte)
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

    # Transactions table (Tilføjet 'description' kolonne)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL,
            description TEXT DEFAULT '', -- <-- NY: Beskrivelse for bedre embeddings
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

# Initialiser databasen (dette opretter SQLite tabeller, hvis de ikke eksisterer)
init_db()

# ----------------------------------------------------------------------------
# Authentication Decorator (Uændret)
# ----------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Uautoriseret. Log venligst ind.'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ----------------------------------------------------------------------------
# Authentication Endpoints (Uændret)
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
# Transaction Endpoints (ÆNDRET: Tilføjet 'description' og embedding-kald)
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
    description = data.get('description', '') # Hent description

    if not category or amount is None:
        return jsonify({'error': 'Kategori og beløb er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO transactions (user_id, category, amount, date, description) VALUES (?, ?, ?, ?, ?)',
        (user_id, category, amount, date, description)
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()

    # NY: Generer og gem embedding for den nye transaktion
    text_to_embed = f"Transaktion: {description} i kategorien {category} på {amount} DKK den {date}."
    metadata = {"user_id": user_id, "type": "transaction", "category": category, "original_id": new_id}
    generate_and_store_embedding(transactions_collection, new_id, text_to_embed, metadata)

    return jsonify({'id': new_id, 'user_id': user_id, 'category': category, 'amount': amount, 'date': date, 'description': description}), 201

@app.route('/api/transactions/<int:txn_id>', methods=['PUT'])
@login_required
def update_transaction(txn_id):
    user_id = session['user_id']
    data = request.get_json()
    category = data.get('category')
    amount = data.get('amount')
    date = data.get('date')
    description = data.get('description') # Hent description

    if not category or amount is None or not date:
        return jsonify({'error': 'Kategori, beløb og dato er påkrævet for opdatering.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE transactions SET category = ?, amount = ?, date = ?, description = ? WHERE id = ? AND user_id = ?',
        (category, amount, date, description, txn_id, user_id)
    )
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Transaktion ikke fundet eller du har ikke adgang.'}), 404
    conn.close()

    # NY: Opdater embedding for transaktionen
    text_to_embed = f"Transaktion: {description} i kategorien {category} på {amount} DKK den {date}."
    metadata = {"user_id": user_id, "type": "transaction", "category": category, "original_id": txn_id}
    generate_and_store_embedding(transactions_collection, txn_id, text_to_embed, metadata) # 'add' vil overskrive hvis ID'et findes

    return jsonify({'id': txn_id, 'category': category, 'amount': amount, 'date': date, 'description': description}), 200

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

    # NY: Slet embedding fra ChromaDB
    try:
        transactions_collection.delete(ids=[str(txn_id)])
        print(f"Embedding slettet for transaktion ID {txn_id} fra ChromaDB.")
    except Exception as e:
        print(f"Advarsel: Kunne ikke slette embedding for transaktion ID {txn_id} fra ChromaDB: {e}")

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
# Budget Endpoints (ÆNDRET: Tilføjet embedding-kald)
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

        # NY: Generer og gem embedding for det nye budget
        text_to_embed = f"Budget for kategorien {category} med en månedlig grænse på {monthly_limit} DKK."
        metadata = {"user_id": user_id, "type": "budget", "category": category, "original_id": new_id}
        generate_and_store_embedding(budgets_collection, new_id, text_to_embed, metadata)

        return jsonify({'id': new_id, 'user_id': user_id, 'category': category, 'monthly_limit': monthly_limit}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Du har allerede et budget for denne kategori.'}), 409

@app.route('/api/budgets/<int:budget_id>', methods=['PUT'])
@login_required
def update_budget(budget_id):
    user_id = session['user_id']
    data = request.get_json()
    category = data.get('category')
    monthly_limit = data.get('monthly_limit')

    if not category or monthly_limit is None:
        return jsonify({'error': 'Kategori og månedlig grænse er påkrævet for opdatering.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE budgets SET category = ?, monthly_limit = ? WHERE id = ? AND user_id = ?',
        (category, monthly_limit, budget_id, user_id)
    )
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Budget ikke fundet eller du har ikke adgang.'}), 404
    conn.close()

    # NY: Opdater embedding for budgettet
    text_to_embed = f"Budget for kategorien {category} med en månedlig grænse på {monthly_limit} DKK."
    metadata = {"user_id": user_id, "type": "budget", "category": category, "original_id": budget_id}
    generate_and_store_embedding(budgets_collection, budget_id, text_to_embed, metadata)

    return jsonify({'id': budget_id, 'category': category, 'monthly_limit': monthly_limit}), 200

@app.route('/api/budgets/<int:budget_id>', methods=['DELETE'])
@login_required
def delete_budget(budget_id):
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM budgets WHERE id = ? AND user_id = ?', (budget_id, user_id))
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Budget ikke fundet eller du har ikke adgang.'}), 404
    conn.close()

    # NY: Slet embedding fra ChromaDB
    try:
        budgets_collection.delete(ids=[str(budget_id)])
        print(f"Embedding slettet for budget ID {budget_id} fra ChromaDB.")
    except Exception as e:
        print(f"Advarsel: Kunne ikke slette embedding for budget ID {budget_id} fra ChromaDB: {e}")

    return '', 204

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
# Goal Endpoints (ÆNDRET: Tilføjet embedding-kald)
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

    # NY: Generer og gem embedding for det nye mål
    text_to_embed = f"Mål: {name} med et målbeløb på {target_amount} DKK og forfaldsdato {due_date if due_date else 'ingen'}."
    metadata = {"user_id": user_id, "type": "goal", "original_id": new_id}
    generate_and_store_embedding(goals_collection, new_id, text_to_embed, metadata)

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
    cursor.execute('SELECT current_amount, target_amount, name, due_date FROM goals WHERE id = ? AND user_id = ?', (goal_id, user_id))
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

    # NY: Opdater embedding for målet
    # Vigtigt: current_amount er en del af teksten, så opdater den også i embeddingen.
    text_to_embed = f"Mål: {goal['name']} med et målbeløb på {goal['target_amount']} DKK, nuværende beløb {new_amount} DKK og forfaldsdato {goal['due_date'] if goal['due_date'] else 'ingen'}."
    metadata = {"user_id": user_id, "type": "goal", "original_id": goal_id}
    generate_and_store_embedding(goals_collection, goal_id, text_to_embed, metadata)

    return jsonify({'id': goal_id, 'current_amount': new_amount, 'message': 'Bidrag tilføjet.'}), 200

@app.route('/api/goals/<int:goal_id>', methods=['DELETE'])
@login_required
def delete_goal(goal_id):
    user_id = session['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM goals WHERE id = ? AND user_id = ?', (goal_id, user_id))
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Mål ikke fundet eller du har ikke adgang.'}), 404
    conn.close()

    # NY: Slet embedding fra ChromaDB
    try:
        goals_collection.delete(ids=[str(goal_id)])
        print(f"Embedding slettet for mål ID {goal_id} fra ChromaDB.")
    except Exception as e:
        print(f"Advarsel: Kunne ikke slette embedding for mål ID {goal_id} fra ChromaDB: {e}")

    return '', 204

# ----------------------------------------------------------------------------
# AI Insight Endpoint (Denne er nu din "Generel indsigt"-funktion/værktøj)
# ----------------------------------------------------------------------------
@app.route('/api/insight', methods=['GET'])
@login_required
def get_insight():
    user_id = session['user_id']
    try:
        if not MISTRAL_API_KEY:
            return jsonify({'error': 'Mistral API-nøgle ikke konfigureret.'}), 500

        # --- Hent ALLE relevante data for brugeren fra SQLite (dette er din "værktøj 1") ---
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT category, amount, date, description FROM transactions WHERE user_id = ? ORDER BY date DESC LIMIT 20', (user_id,))
        transactions = [dict(row) for row in cursor.fetchall()]

        cursor.execute('SELECT category, monthly_limit FROM budgets WHERE user_id = ?', (user_id,))
        budgets = [dict(row) for row in cursor.fetchall()]

        cursor.execute('SELECT name, target_amount, current_amount, due_date FROM goals WHERE user_id = ?', (user_id,))
        goals = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Formatér data til en læsbar streng for LLM'en
        transaction_str = "Ingen transaktioner."
        if transactions:
            transaction_str = "Dine seneste transaktioner:\n" + "\n".join([
                f"- Dato: {t['date']}, Kategori: {t['category']}, Beløb: {t['amount']} DKK, Beskrivelse: {t['description']}"
                for t in transactions
            ])

        budget_str = "Ingen budgetter."
        if budgets:
            budget_str = "Dine opsatte budgetter:\n" + "\n".join([
                f"- Kategori: {b['category']}, Månedlig grænse: {b['monthly_limit']} DKK"
                for b in budgets
            ])

        goal_str = "Ingen mål."
        if goals:
            goal_str = "Dine økonomiske mål:\n" + "\n".join([
                f"- Mål: {g['name']}, Målbeløb: {g['target_amount']} DKK, Nuværende beløb: {g['current_amount']} DKK, Forfaldsdato: {g['due_date'] if g['due_date'] else 'Ingen'}"
                for g in goals
            ])

        user_message_content = f"""
        Jeg har følgende finansielle data:

        {transaction_str}

        {budget_str}

        {goal_str}

        Baseret på disse oplysninger, giv mig venligst en overordnet indsigt i min økonomiske situation, fremhæv vigtige trends, hvordan jeg overholder budgetter, og fremskridt mod mine mål. Vær gerne specifik med kategorier og beløb, hvis relevant.
        """

        system_message = """Du er en venlig, hjælpsom finansiel AI-assistent, der analyserer brugerens økonomiske data.
        Din opgave er at give en overordnet og relevant indsigt i deres finansielle situation, baseret på den givne transaktioner, budgetter og mål.
        Fokuser på trends, overholdelse af budgetter, fremskridt mod mål og generelle observationer.
        Svar på dansk og vær konkret, men også opmuntrende og konstruktiv.
        Inkluder ikke information, der ikke er direkte relateret til den givne data.
        """

        resp = requests.post(
            MISTRAL_API_URL,
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {"role":"system","content":system_message},
                    {"role":"user","content":user_message_content}
                ]
            },
            headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}
        )
        resp.raise_for_status()
        data = resp.json()
        insight = data["choices"][0]["message"]["content"].strip()
        return jsonify({'insight': insight}), 200

    except Exception as e:
        print(f"Fejl i get_insight: {e}")
        return jsonify({'error': str(e)}), 500

# ----------------------------------------------------------------------------
# NY: Endpoint for Simpel RAG (Retrieval-Augmented Generation)
# Denne viser hvordan Embeddings og ChromaDB bruges til specifikke forespørgsler.
# ----------------------------------------------------------------------------
@app.route('/api/semantic_query', methods=['GET'])
@login_required
def semantic_query(query: str = None):
    user_id = session['user_id']
    # Brug query-argumentet fra ask_ai, ellers hent fra URL-param
    user_question = query or request.args.get('query')

    if not user_question:
        return jsonify({'error': 'En specifik forespørgsel (query) er påkrævet.'}), 400
    if not MISTRAL_API_KEY:
        return jsonify({'error': 'Mistral API-nøgle ikke konfigureret.'}), 500

    try:
        # 1) Generer embedding for brugerens specifikke spørgsmål
        resp = requests.post(
            MISTRAL_EMBEDDING_API_URL,
            json={
                "model": "mistral-embed",
                "input": [user_question]
            },
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        resp.raise_for_status()
        embedding_data = resp.json()
        query_embedding = embedding_data["data"][0]["embedding"]

        # 2) Hent relevante dokumenter fra ChromaDB
        search_tx = transactions_collection.query(
            query_embeddings=[query_embedding],
            n_results=50,
            where={"user_id": user_id},
            include=['documents']
        )
        search_bd = budgets_collection.query(
            query_embeddings=[query_embedding],
            n_results=3,
            where={"user_id": user_id},
            include=['documents']
        )
        search_gl = goals_collection.query(
            query_embeddings=[query_embedding],
            n_results=3,
            where={"user_id": user_id},
            include=['documents']
        )

        # Byg kontekst
        context_parts = []
        if search_tx.get('documents'):
            context_parts.append(
                "Relevante transaktioner:\n" +
                "\n".join(search_tx['documents'][0])
            )
        if search_bd.get('documents'):
            context_parts.append(
                "Relevante budgetter:\n" +
                "\n".join(search_bd['documents'][0])
            )
        if search_gl.get('documents'):
            context_parts.append(
                "Relevante mål:\n" +
                "\n".join(search_gl['documents'][0])
            )

        retrieved_context = "\n\n".join(context_parts) or \
            "Ingen specifikke relevante data fundet for denne forespørgsel."

        # 3) Send kontekst + brugerens spørgsmål til LLM
        system_message = """Du er en hjælpsom finansiel AI-assistent.
Du skal besvare brugerens spørgsmål baseret på den GIVNE KONTEKST fra brugerens finansielle data.
Hvis konteksten ikke indeholder svaret, skal du sige, at du ikke har nok information, men give en generel relevant forklaring.
Svar på dansk og vær direkte og præcis.
"""
        user_message = (
            f"Kontekst fra mine finansielle data:\n\n{retrieved_context}\n\n"
            f"Mit spørgsmål: {user_question}"
        )

        resp = requests.post(
            MISTRAL_API_URL,
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {"role": "system", "content": system_message},
                    {"role": "user",   "content": user_message}
                ]
            },
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        resp.raise_for_status()
        data = resp.json()
        insight = data["choices"][0]["message"]["content"].strip()
        return jsonify({'insight': insight}), 200

    except Exception as e:
        print(f"Fejl i semantic_query: {e}")
        return jsonify({'error': str(e)}), 500


# ----------------------------------------------------------------------------
# Endpoint for Agentic RAG (router)
# ----------------------------------------------------------------------------
@app.route('/api/ask_ai', methods=['GET'])
@login_required
def ask_ai():
    user_id = session['user_id']
    user_query = request.args.get('query')

    if not user_query:
        return jsonify({'error': 'En forespørgsel er påkrævet.'}), 400
    if not MISTRAL_API_KEY:
        return jsonify({'error': 'Mistral API-nøgle ikke konfigureret.'}), 500

    try:
        # 1) Bed LLM om at beslutte hvilken handling der skal tages
        router_system = """Du er en router-assistent, der bestemmer:
Svar kun med JSON:
{"action":"GENERAL_INSIGHT"} eller {"action":"SEMANTIC_QUERY","query":"<dit spørgsmål>"}
"""
        resp_dec = requests.post(
            MISTRAL_API_URL,
            json={
                "model": "mistral-small-latest",
                "messages": [
                    {"role": "system", "content": router_system},
                    {"role": "user",   "content": user_query}
                ],
                # Fjern evt. ukendt response_format
            },
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        resp_dec.raise_for_status()
        raw = resp_dec.json()["choices"][0]["message"]["content"].strip()

        # 2) Parse resultat med fallback
        try:
            decision = json.loads(raw)
        except json.JSONDecodeError:
            print(f"Router-parse fejlede på: {raw!r}, fallback til GENERAL_INSIGHT")
            decision = {"action": "GENERAL_INSIGHT"}

        action = decision.get("action")
        if action == "GENERAL_INSIGHT":
            return get_insight()
        elif action == "SEMANTIC_QUERY":
            refined = decision.get("query", user_query)
            return semantic_query(query=refined)
        else:
            return jsonify({'error': 'Ugyldig action fra AI.'}), 500

    except Exception as e:
        print(f"Fejl i ask_ai: {e}")
        return jsonify({'error': str(e)}), 500

# ----------------------------------------------------------------------------
# Spending Forecast Endpoint (Uændret)
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# Weekly Pattern Endpoint (Uændret)
# ----------------------------------------------------------------------------
@app.route('/api/weekly_pattern', methods=['GET'])
@login_required
def weekly_pattern():
    user_id = session['user_id']

    conn = get_db_connection()
    df = pd.read_sql(
        'SELECT date, amount FROM transactions WHERE user_id = ?',
        conn, params=(user_id,), parse_dates=['date']
    )
    conn.close()

    daily_df = (
        df
        .set_index('date')['amount']
        .resample('D').sum()
    
    )

    daily_array = daily_df.values
    n_days = daily_array.shape[0]
    n_weeks = int(np.ceil(n_days / 7))
    padded = np.pad(daily_array, (0, n_weeks * 7 - n_days), constant_values=0)
    weeks_matrix = padded.reshape(n_weeks, 7)

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
# Seed Data Endpoint (ÆNDRET: Rydder nu både SQLite og ChromaDB og gemmer embeddings igen)
# ----------------------------------------------------------------------------
@app.route('/api/seed_data', methods=['POST'])
def seed_data():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Ryd alle eksisterende data fra SQLite
    for tbl in ['users', 'transactions', 'budgets', 'goals']:
        cursor.execute(f"DELETE FROM {tbl}")
    conn.commit()

    # Ryd alle eksisterende data fra ChromaDB
    try:
        transactions_collection.delete()
        budgets_collection.delete()
        goals_collection.delete()
        print("Alle data slettet fra SQLite og ChromaDB.")
    except Exception as e:
        print(f"Advarsel: Kunne ikke rydde ChromaDB collections: {e}")

    # Opret testbruger
    username = "testuser"
    password = "password123"
    pw_hash = bcrypt.generate_password_hash(password, rounds=12).decode('utf-8')
    cursor.execute(
        'INSERT INTO users (username, password) VALUES (?, ?)',
        (username, pw_hash)
    )
    conn.commit()
    user_id = cursor.lastrowid
    print(f"Testbruger '{username}' (ID: {user_id}) oprettet.")

    # Parametre
    num_transactions = 200
    months_back = 6
    categories = ['Mad', 'Transport', 'Underholdning', 'Regninger', 'Shopping', 'Sundhed', 'Uddannelse']
    budgets = {'Mad': 500.0, 'Transport': 300.0, 'Underholdning': 250.0, 'Regninger': 800.0, 'Shopping': 400.0, 'Sundhed': 200.0, 'Uddannelse': 300.0}
    goals_data = [
        {'name': 'Ny Laptop', 'target_amount': 1500.0, 'current_amount': 0.0, 'due_date': '2025-12-31'},
        {'name': 'Ferie', 'target_amount': 5000.0, 'current_amount': 0.0, 'due_date': '2026-06-30'},
        {'name': 'Bil Reparation', 'target_amount': 2000.0, 'current_amount': 0.0, 'due_date': '2025-09-30'}
    ]

    # Generer num_transactions tilfældige transaktioner over months_back måneder
    today = datetime.now()
    start_date = today - timedelta(days=30 * months_back)
    for _ in range(num_transactions):
        delta_days = (today - start_date).days
        rand_days = random.randint(0, delta_days)
        txn_date = (start_date + timedelta(days=rand_days)).strftime('%Y-%m-%d')
        cat = random.choice(categories)
        avg = budgets.get(cat, 300.0) / 4
        amount = round(abs(random.gauss(avg, avg * 0.5)), 2)
        description = f"Autogenerated transaktion i kategorien {cat}."

        cursor.execute(
            'INSERT INTO transactions (user_id, category, amount, date, description) VALUES (?, ?, ?, ?, ?)',
            (user_id, cat, amount, txn_date, description)
        )
        conn.commit()
        txn_id = cursor.lastrowid
        text = f"Transaktion: {description} på {amount} DKK den {txn_date}."
        metadata = {"user_id": user_id, "type": "transaction", "category": cat, "original_id": txn_id}
        generate_and_store_embedding(transactions_collection, txn_id, text, metadata)

    # Seed Budgetter
    for cat, limit in budgets.items():
        cursor.execute(
            'INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?)',
            (user_id, cat, limit)
        )
        conn.commit()
        budget_id = cursor.lastrowid
        text = f"Budget for {cat}: {limit} DKK månedligt."
        metadata = {"user_id": user_id, "type": "budget", "category": cat, "original_id": budget_id}
        generate_and_store_embedding(budgets_collection, budget_id, text, metadata)

    # Seed Mål
    for g in goals_data:
        cursor.execute(
            'INSERT INTO goals (user_id, name, target_amount, current_amount, due_date) VALUES (?, ?, ?, ?, ?)',
            (user_id, g['name'], g['target_amount'], g['current_amount'], g['due_date'])
        )
        conn.commit()
        goal_id = cursor.lastrowid
        text = f"Mål: {g['name']} {g['current_amount']}/{g['target_amount']} DKK, forfald {g['due_date']}."
        metadata = {"user_id": user_id, "type": "goal", "original_id": goal_id}
        generate_and_store_embedding(goals_collection, goal_id, text, metadata)

    conn.close()
    return jsonify({'message': f"Database seeded med {num_transactions} transaktioner, {len(budgets)} budgetter, {len(goals_data)} mål for user: {username}"}), 200



# ----------------------------------------------------------------------------
# Main entry point for running the Flask app
# ----------------------------------------------------------------------------
if __name__ == '__main__':
    # Sørg for at databasen er initialiseret (opretter SQLite tabeller hvis de ikke findes)
    init_db()
    # Kør Flask applikationen i debug-mode
    app.run(debug=True,  use_reloader=False)