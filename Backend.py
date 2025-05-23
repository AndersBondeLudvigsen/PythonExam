from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_bcrypt import Bcrypt
import sqlite3
import requests
import os
from datetime import datetime, timedelta

# Initialize Flask app, CORS, and Bcrypt
app = Flask(__name__)
CORS(app)
bcrypt = Bcrypt(app)

# Configure Mistral API
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

# Database helper functions
def get_db_connection():
    """Establishes a connection to the SQLite database."""
    conn = sqlite3.connect('finance.db')
    conn.row_factory = sqlite3.Row  # This allows accessing columns by name
    return conn

def init_db():
    """Initializes the database by creating necessary tables if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users table: stores user credentials
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    ''')

    # Transactions table: stores individual financial transactions, linked to a user
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

    # Budgets table: stores monthly budget limits for categories, linked to a user
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            monthly_limit REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (user_id, category) -- Each user can only have one budget per category
        )
    ''')

    # Goals table: stores financial goals, linked to a user
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            target_amount REAL NOT NULL,
            current_amount REAL DEFAULT 0.0,
            due_date TEXT, -- YYYY-MM-DD format
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    conn.commit()
    conn.close()

# Initialize the database when the app starts
init_db()


### **Authentication Endpoints**

@app.route('/api/signup', methods=['POST'])
def signup():
    """Handles user registration."""
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Brugernavn og adgangskode er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Check if username already exists
    cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Brugernavn er allerede taget.'}), 409

    # Hash password using bcrypt with 12 salt rounds
    pw_hash = bcrypt.generate_password_hash(password, rounds=12).decode('utf-8')

    # Insert new user into the database
    cursor.execute(
        'INSERT INTO users (username, password) VALUES (?, ?)',
        (username, pw_hash)
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()

    return jsonify({'id': user_id, 'username': username}), 201

@app.route('/api/login', methods=['POST'])
def login():
    """Handles user login."""
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

    # Verify user exists and password is correct
    if not user or not bcrypt.check_password_hash(user['password'], password):
        return jsonify({'error': 'Ugyldigt brugernavn eller adgangskode.'}), 401

    return jsonify({
        'message': 'Login successful',
        'user': {'id': user['id'], 'username': user['username']}
    }), 200


### **Transaction Endpoints (User-Specific)**

@app.route('/api/transactions/<int:user_id>', methods=['GET'])
def get_transactions(user_id):
    """Retrieves all transactions for a specific user, ordered by date."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM transactions WHERE user_id = ? ORDER BY date DESC', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    # Convert rows to list of dictionaries using a list comprehension
    return jsonify([dict(row) for row in rows])

@app.route('/api/transactions', methods=['POST'])
def add_transaction():
    """Adds a new transaction for a user."""
    data = request.get_json()
    user_id = data.get('user_id')
    category = data.get('category')
    amount = data.get('amount')
    date = data.get('date', datetime.now().strftime('%Y-%m-%d')) # Default to current date

    if not user_id or not category or amount is None:
        return jsonify({'error': 'Brugers-ID, kategori og beløb er påkrævet.'}), 400

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
def update_transaction(txn_id):
    """Updates an existing transaction, ensuring it belongs to the correct user."""
    data = request.get_json()
    user_id = data.get('user_id') 
    category = data.get('category')
    amount = data.get('amount')
    date = data.get('date')

    if not user_id:
        return jsonify({'error': 'Brugers-ID er påkrævet.'}), 400

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
    return jsonify({'id': txn_id, 'category': category, 'amount': amount, 'date': date})

@app.route('/api/transactions/<int:txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    """Deletes a transaction, ensuring it belongs to the correct user."""
    user_id = request.args.get('user_id') # Expect user_id as query param for DELETE

    if not user_id:
        return jsonify({'error': 'Brugers-ID er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM transactions WHERE id = ? AND user_id = ?', (txn_id, user_id))
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Transaktion ikke fundet eller du har ikke adgang.'}), 404
    conn.close()
    return '', 204

@app.route('/api/transactions/summary/<int:user_id>', methods=['GET'])
def summarize_transactions(user_id):
    """Provides a summary of spending per category for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM transactions WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    transactions = [dict(r) for r in rows]
    
    # Use a dictionary comprehension for efficient category summary
    summary = {
        cat: sum(t['amount'] for t in transactions if t['category'] == cat)
        for cat in {t['category'] for t in transactions}
    }
    return jsonify(summary)

@app.route('/api/transactions/monthly_summary/<int:user_id>', methods=['GET'])
def get_monthly_spending(user_id):
    """Aggregates and returns total spending per month for a user."""
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
    
    # Transform dict to list of dicts for frontend using a list comprehension
    monthly_summary = [{'month': m, 'total_spending': s} for m, s in monthly_data.items()]
    
    return jsonify(monthly_summary)


### **Budget Endpoints**

@app.route('/api/budgets/<int:user_id>', methods=['GET'])
def get_budgets(user_id):
    """Retrieves all budget limits set by a user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM budgets WHERE user_id = ?', (user_id,))
    budgets = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(budgets)

@app.route('/api/budgets', methods=['POST'])
def add_budget():
    """Adds a new budget category and monthly limit for a user."""
    data = request.get_json()
    user_id = data.get('user_id')
    category = data.get('category')
    monthly_limit = data.get('monthly_limit')

    if not user_id or not category or monthly_limit is None:
        return jsonify({'error': 'Brugers-ID, kategori og månedlig grænse er påkrævet.'}), 400
    
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
    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500

@app.route('/api/budgets/status/<int:user_id>', methods=['GET'])
def get_budget_status(user_id):
    """Calculates and returns the current spending status against budgets for the current month for a user."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get current month's start and end dates
    current_month_start = datetime.now().strftime('%Y-%m-01')
    next_month_start = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=1)
    current_month_end = (next_month_start - timedelta(days=1)).strftime('%Y-%m-%d')

    # Get total amount spent per category in the current month
    cursor.execute(
        'SELECT category, SUM(amount) as total_spent '
        'FROM transactions '
        'WHERE user_id = ? AND date BETWEEN ? AND ? '
        'GROUP BY category',
        (user_id, current_month_start, current_month_end)
    )
    spent_data = {row['category']: row['total_spent'] for row in cursor.fetchall()}

    # Get all budget limits for the user
    cursor.execute('SELECT category, monthly_limit FROM budgets WHERE user_id = ?', (user_id,))
    budgets_data = [dict(row) for row in cursor.fetchall()]

    conn.close()

    # Combine budget limits with actual spending using a list comprehension
    budget_status = [
        {
            'category': b['category'],
            'monthly_limit': b['monthly_limit'],
            'spent': spent_data.get(b['category'], 0.0), # Use .get to handle categories with no spending yet
            'remaining': b['monthly_limit'] - spent_data.get(b['category'], 0.0)
        }
        for b in budgets_data
    ]
    
    return jsonify(budget_status)


### **Goal Endpoints**

@app.route('/api/goals/<int:user_id>', methods=['GET'])
def get_goals(user_id):
    """Retrieves all financial goals for a specific user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM goals WHERE user_id = ?', (user_id,))
    goals = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(goals)

@app.route('/api/goals', methods=['POST'])
def add_goal():
    """Adds a new financial goal for a user."""
    data = request.get_json()
    user_id = data.get('user_id')
    name = data.get('name')
    target_amount = data.get('target_amount')
    due_date = data.get('due_date') # Optional
    
    if not user_id or not name or target_amount is None:
        return jsonify({'error': 'Brugers-ID, navn og målbeløb er påkrævet.'}), 400
    
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
def contribute_to_goal(goal_id):
    """Updates the current amount saved towards a specific goal."""
    data = request.get_json()
    user_id = data.get('user_id')
    amount = data.get('amount')

    if not user_id or amount is None or amount <= 0:
        return jsonify({'error': 'Brugers-ID og et positivt bidragsbeløb er påkrævet.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT current_amount, target_amount FROM goals WHERE id = ? AND user_id = ?', (goal_id, user_id))
    goal = cursor.fetchone()

    if not goal:
        conn.close()
        return jsonify({'error': 'Mål ikke fundet eller du har ikke adgang.'}), 404

    # Ensure current_amount does not exceed target_amount
    new_amount = min(goal['current_amount'] + amount, goal['target_amount']) 
    
    cursor.execute(
        'UPDATE goals SET current_amount = ? WHERE id = ? AND user_id = ?',
        (new_amount, goal_id, user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'id': goal_id, 'current_amount': new_amount, 'message': 'Bidrag tilføjet.'})


### **AI Insight Endpoint (Mistral LLM Integration)**

@app.route('/api/insight/<int:user_id>', methods=['GET'])
def get_insight(user_id):
    """
    Provides personalized financial insight using the Mistral LLM,
    tailoring advice based on transactions, budget adherence, and goal progress.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Fetch user's raw financial data
        cursor.execute('SELECT category, amount, date FROM transactions WHERE user_id = ?', (user_id,))
        transactions = [dict(r) for r in cursor.fetchall()]

        cursor.execute('SELECT category, monthly_limit FROM budgets WHERE user_id = ?', (user_id,))
        budgets = [dict(r) for r in cursor.fetchall()]

        cursor.execute('SELECT name, target_amount, current_amount, due_date FROM goals WHERE user_id = ?', (user_id,))
        goals = [dict(r) for r in cursor.fetchall()]
        
        conn.close()

        if not MISTRAL_API_KEY:
            return jsonify({'error': 'Mistral API-nøgle ikke konfigureret. Indstil MISTRAL_API_KEY miljøvariablen.'}), 500

        # --- Dynamic Prompt Generation based on data analysis ---
        prompt_additions = []

        # 1. Analyze Budget Performance for the current month
        current_month_start = datetime.now().strftime('%Y-%m-01')
        next_month_start_dt = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=1)
        current_month_end = (next_month_start_dt - timedelta(days=1)).strftime('%Y-%m-%d')

        current_month_spending = {}
        for txn in transactions:
            txn_date = datetime.strptime(txn['date'], '%Y-%m-%d')
            # Check if transaction falls within the current month
            if current_month_start <= txn['date'] <= current_month_end:
                current_month_spending[txn['category']] = current_month_spending.get(txn['category'], 0.0) + txn['amount']

        for b in budgets:
            category = b['category']
            monthly_limit = b['monthly_limit']
            spent = current_month_spending.get(category, 0.0)
            remaining = monthly_limit - spent

            if monthly_limit > 0: # Only analyze if a limit is set
                if remaining < 0:
                    prompt_additions.append(f"Bemærk: I {category}-kategorien har brugeren **overskredet** budgettet med {-remaining:.2f} DKK denne måned.")
                elif remaining < monthly_limit * 0.1: # If less than 10% of budget left
                    prompt_additions.append(f"Bemærk: Brugeren er **tæt på at opbruge** budgettet for {category} denne måned (kun {remaining:.2f} DKK tilbage).")
                elif spent > 0:
                    prompt_additions.append(f"Bemærk: Brugeren holder sig **godt inden for** budgettet for {category} med {remaining:.2f} DKK tilbage denne måned.")
                else: # budget > 0 but spent == 0
                    prompt_additions.append(f"Bemærk: Brugeren har endnu ikke brugt noget af sit budget for {category} denne måned.")
            else: # monthly_limit is 0 or less
                prompt_additions.append(f"Bemærk: Budget for {category} er nul eller negativt. Overvej at sætte et realistisk budget her.")

        # 2. Analyze Goal Progress
        for g in goals:
            progress_percent = (g['current_amount'] / g['target_amount']) * 100 if g['target_amount'] > 0 else 0
            
            if g['current_amount'] >= g['target_amount']:
                prompt_additions.append(f"Bemærk: Brugeren har **succesfuldt nået** målet '{g['name']}'.")
            elif g['current_amount'] > 0: # Made some progress
                prompt_additions.append(f"Bemærk: Brugeren har gjort **fremskridt** med målet '{g['name']}' ({progress_percent:.1f}% opnået).")
            else: # No progress yet
                prompt_additions.append(f"Bemærk: Brugeren har endnu ikke startede med målet '{g['name']}'.")
            
            if g['due_date'] and g['current_amount'] < g['target_amount']:
                due_date_obj = datetime.strptime(g['due_date'], '%Y-%m-%d')
                days_left = (due_date_obj - datetime.now()).days
                if days_left > 0 and days_left < 60: # If less than 2 months left
                    prompt_additions.append(f"Bemærk: Målet '{g['name']}' har en **deadline om {days_left} dage**, og det er endnu ikke nået.")
                elif days_left <= 0:
                    prompt_additions.append(f"Bemærk: Målet '{g['name']}' havde en **deadline, der er overskredet**, og det er endnu ikke nået.")


        # Define the system message for the LLM, setting its persona and instructions
        system_message_content = """
        Du er en venlig, hjælpsom, motiverende og forstående personlig økonomiassistent.
        Din opgave er at give indsigt og handlingsorienterede tips baseret på en brugers transaktioner, budgetter og mål.
        Svar udelukkende på dansk og hold dig til de fakta, du får præsenteret.
        Tonen skal være opmuntrende og ikke-dømmende.
        
        **Specifikke instruktioner for feedback:**
        1.  **Start med en generel, venlig hilsen** og en kort opsummering af brugerens økonomiske sundhed.
        2.  **Hvis brugeren har nået et mål:** Giv et meget oprigtigt og positivt tillykke! Fejr deres succes.
        3.  **Hvis brugeren har overskredet et budget:** Advar forsigtigt uden at lyde bebrejdende. Foreslå konkrete løsninger eller overvejelser for at undgå det i fremtiden. Brug formuleringer som "Det ser ud til, at...", "En idé kunne være...", "Overvej måske...".
        4.  **Hvis brugeren er tæt på at overskride et budget (få penge tilbage):** Giv et forsigtigt tip om at være opmærksom på forbrug i den resterende del af måneden for den specifikke kategori.
        5.  **Hvis brugeren ligger godt på budgettet:** Opmuntr dem og bekræft deres gode vaner.
        6.  **Hvis et mål nærmer sig deadline og ikke er nået:** Foreslå måder at øge bidraget på eller at revurdere målet.
        7.  **Afslut med tre handlingsorienterede tips** til at forbedre økonomien. Disse tips skal være specifikke, praktiske og baseret på den analyseret data. Prioritér tips, der adresserer identificerede problemer (f.eks. overforbrug) eller styrker gode vaner (f.eks. målsparing).
        8.  **Hold dig til dansk i hele svaret.**
        """

        # Combine all data and analysis into the user prompt
        user_prompt_content = (
            f"Her er en liste over mine transaktioner: {transactions}.\n"
            f"Her er mine nuværende budgetter: {budgets}.\n"
            f"Her er mine økonomiske mål: {goals}.\n\n"
            "**Yderligere observationspunkter baseret på detaljeret analyse af mine data:**\n" + 
            ("\n".join(prompt_additions) if prompt_additions else "Ingen specifikke observationspunkter udover rå data.") +
            "\n\nAnalyser alle disse data grundigt. Giv en samtale-præget feedback baseret på din systeminstruktion. Svar i en venlig og motiverende tone."
        )

        messages = [
            {"role": "system", "content": system_message_content},
            {"role": "user", "content": user_prompt_content}
        ]
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {"model": "mistral-small-latest", "messages": messages, "max_tokens": 400} # Increased max_tokens for more detailed response
        
        resp = requests.post(MISTRAL_API_URL, json=payload, headers=headers)
        
        # --- DEBUGGING LINE: Print the full response from Mistral ---
        print(f"Mistral API Response Status: {resp.status_code}, Text: {resp.text}") 
        # --- END DEBUGGING LINE ---

        resp.raise_for_status() # This will raise an HTTPError for 4xx/5xx responses
        result = resp.json()

        if 'choices' not in result or not result['choices']:
            # Log the full result for debugging if 'choices' is missing
            print(f"Mistral API response missing 'choices': {result}")
            return jsonify({'error': 'Ugyldig svar fra Mistral API eller ingen "choices" fundet.'}), 502

        insight = result['choices'][0]['message']['content'].strip()
        return jsonify({'insight': insight}), 200

    except requests.exceptions.HTTPError as http_err:
        # Include the response text from the HTTP error for better debugging
        print(f"HTTPError: {http_err.response.text}") # Print full text of error response
        return jsonify({'error': f'Mistral HTTP-fejl ({http_err.response.status_code}): {http_err.response.text}'}), http_err.response.status_code
    except requests.exceptions.ConnectionError as conn_err:
        return jsonify({'error': f'Forbindelsesfejl til Mistral API. Tjek din internetforbindelse eller Mistral API-status: {conn_err}'}), 503
    except Exception as e:
        # Catch any other unexpected errors
        return jsonify({'error': f'En uventet fejl opstod: {str(e)}'}), 500


### **Seed Data Endpoint (for easy testing)**

@app.route('/api/seed', methods=['POST'])
def seed_data():
    """
    Seeds the database with a test user and sample transactions, budgets, and goals.
    WARNING: This deletes existing data in transactions, budgets, and goals tables.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Clear existing data for demonstration
    cursor.execute('DELETE FROM transactions')
    cursor.execute('DELETE FROM budgets')
    cursor.execute('DELETE FROM goals')
    cursor.execute('DELETE FROM users') # Clear users as well to ensure clean state
    conn.commit()

    # Create a test user
    test_username = 'testuser'
    test_password = 'password123'
    pw_hash = bcrypt.generate_password_hash(test_password, rounds=12).decode('utf-8')
    cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (test_username, pw_hash))
    conn.commit()
    test_user_id = cursor.lastrowid

    # Sample Transactions for the test user
    sample_transactions = [
        {'user_id': test_user_id, 'category': 'Mad', 'amount': 150.0, 'date': '2025-04-01'},
        {'user_id': test_user_id, 'category': 'Transport', 'amount': 75.5, 'date': '2025-04-03'},
        {'user_id': test_user_id, 'category': 'Underholdning', 'amount': 200.0, 'date': '2025-04-05'},
        {'user_id': test_user_id, 'category': 'Regninger', 'amount': 450.0, 'date': '2025-04-07'},
        {'user_id': test_user_id, 'category': 'Mad', 'amount': 80.0, 'date': '2025-04-10'},
        {'user_id': test_user_id, 'category': 'Shopping', 'amount': 300.0, 'date': '2025-04-12'},
        {'user_id': test_user_id, 'category': 'Mad', 'amount': 120.0, 'date': '2025-05-01'},
        {'user_id': test_user_id, 'category': 'Transport', 'amount': 60.0, 'date': '2025-05-05'},
        {'user_id': test_user_id, 'category': 'Underholdning', 'amount': 100.0, 'date': '2025-05-10'},
        {'user_id': test_user_id, 'category': 'Mad', 'amount': 210.0, 'date': '2025-05-13'} # Over budget example for May Food
    ]
    for txn in sample_transactions:
        cursor.execute(
            'INSERT INTO transactions (user_id, category, amount, date) VALUES (?, ?, ?, ?)',
            (txn['user_id'], txn['category'], txn['amount'], txn['date'])
        )

    # Sample Budgets for the test user
    sample_budgets = [
        {'user_id': test_user_id, 'category': 'Mad', 'monthly_limit': 300.0}, # This user will likely overspend here in May example
        {'user_id': test_user_id, 'category': 'Transport', 'monthly_limit': 200.0},
        {'user_id': test_user_id, 'category': 'Underholdning', 'monthly_limit': 150.0}
    ]
    for budget in sample_budgets:
        cursor.execute(
            'INSERT INTO budgets (user_id, category, monthly_limit) VALUES (?, ?, ?)',
            (budget['user_id'], budget['category'], budget['monthly_limit'])
        )

    # Sample Goals for the test user
    sample_goals = [
        {'user_id': test_user_id, 'name': 'Ny Laptop', 'target_amount': 1500.0, 'current_amount': 250.0, 'due_date': '2025-08-31'},
        {'user_id': test_user_id, 'name': 'Ferie', 'target_amount': 5000.0, 'current_amount': 1000.0, 'due_date': '2026-06-30'}
    ]
    for goal in sample_goals:
        cursor.execute(
            'INSERT INTO goals (user_id, name, target_amount, current_amount, due_date) VALUES (?, ?, ?, ?, ?)',
            (goal['user_id'], goal['name'], goal['target_amount'], goal['current_amount'], goal['due_date'])
        )

    conn.commit()
    conn.close()
    return jsonify({'status': 'seeded', 'user_id': test_user_id, 'message': f'Database seedet med testbruger ({test_username}) og data.'})

# Main entry point for running the Flask app
if __name__ == '__main__':
    # Ensure database is initialized every time in debug mode for fresh start
    # For production, you'd typically handle migrations separately or ensure init_db()
    # is only called if tables don't exist.
    init_db() 
    app.run(debug=True)