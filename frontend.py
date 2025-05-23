import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt # Import Matplotlib

API_URL = 'http://localhost:5000/api'

st.set_page_config(layout="wide") # Use wide layout for better dashboard view
st.title("Familie Finans Dashboard")

# --- Session state for signed-in user ---
if "user" not in st.session_state:
    st.session_state.user = None

# --- Utility for auth headers (if you later add JWTs) ---
def auth_headers():
    # Placeholder for JWT if you implement it. For now, we pass user_id in body/query.
    return {} 

# --- Login / Signup ---
if st.session_state.user is None:
    st.subheader("Log ind / Registrer")
    mode = st.radio("Vælg:", ["Login", "Registrer"], horizontal=True)
    username = st.text_input("Brugernavn", key="auth_username")
    password = st.text_input("Adgangskode", type="password", key="auth_password")

    if st.button(mode, key="auth_button"):
        endpoint = "/login" if mode == "Login" else "/signup"
        res = requests.post(API_URL + endpoint, json={
            "username": username,
            "password": password
        })

        if res.ok:
            data = res.json()
            if mode == "Login":
                st.session_state.user = data["user"]
                st.success(f"Logget ind som {data['user']['username']}!")
                st.rerun() # CHANGED FROM st.experimental_rerun() TO st.rerun()
            else:
                st.success("Bruger oprettet – du kan nu logge ind.")
        else:
            st.error(res.json().get("error", "Noget gik galt under login/registrering"))
    
    st.markdown("---")
    st.info("Kører du dette for første gang? Du kan seed data til en testbruger:")
    if st.button("Seed Test Data (Sletter eksisterende data!)", key="seed_button"):
        seed_res = requests.post(f"{API_URL}/seed")
        if seed_res.ok:
            st.success(seed_res.json().get('message', 'Data seedet!'))
            # Optional: automatically log in the seeded user
            st.session_state.user = {'id': seed_res.json().get('user_id'), 'username': 'testuser'}
            st.rerun() # CHANGED FROM st.experimental_rerun() TO st.rerun()
        else:
            st.error(seed_res.json().get('error', 'Fejl ved seeding af data.'))


# --- Main dashboard once logged in ---
else:
    st.sidebar.markdown(f"**Velkommen, {st.session_state.user['username']}!**")
    st.sidebar.button("Log ud", on_click=lambda: st.session_state.update(user=None))
    
    user_id = st.session_state.user['id']

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        # --- Add transaction ---
        st.header("Tilføj Transaktion")
        with st.form("txn_form"):
            category = st.text_input("Kategori", key="txn_category")
            amount = st.number_input("Beløb", format="%.2f", key="txn_amount")
            date = st.date_input("Dato", datetime.now(), key="txn_date")
            if st.form_submit_button("Gem Transaktion"):
                payload = {
                    "user_id": user_id,
                    "category": category,
                    "amount": amount,
                    "date": date.strftime('%Y-%m-%d')
                }
                res = requests.post(f"{API_URL}/transactions", json=payload)
                if res.status_code == 201:
                    st.success("Transaktion tilføjet!")
                    st.rerun() # CHANGED FROM st.experimental_rerun() TO st.rerun()
                else:
                    st.error(res.json().get('error', "Kunne ikke tilføje transaktion"))
        
        st.markdown("---")

        # --- Add Budget ---
        st.header("Opsæt Budget")
        with st.form("budget_form"):
            budget_category = st.text_input("Kategori for budget", key="budget_category")
            monthly_limit = st.number_input("Månedlig grænse", min_value=0.0, format="%.2f", key="budget_limit")
            if st.form_submit_button("Gem Budget"):
                payload = {
                    "user_id": user_id,
                    "category": budget_category,
                    "monthly_limit": monthly_limit
                }
                res = requests.post(f"{API_URL}/budgets", json=payload)
                if res.status_code == 201:
                    st.success("Budget oprettet!")
                    st.rerun() # CHANGED FROM st.experimental_rerun() TO st.rerun()
                else:
                    st.error(res.json().get('error', "Kunne ikke oprette budget. Husk unik kategori."))
        
        st.markdown("---")

        # --- Add Goal ---
        st.header("Opsæt Mål")
        with st.form("goal_form"):
            goal_name = st.text_input("Mål navn", key="goal_name")
            target_amount = st.number_input("Målbeløb", min_value=0.0, format="%.2f", key="goal_target")
            due_date = st.date_input("Deadline (valgfri)", None, key="goal_due_date")
            if st.form_submit_button("Gem Mål"):
                payload = {
                    "user_id": user_id,
                    "name": goal_name,
                    "target_amount": target_amount,
                    "due_date": due_date.strftime('%Y-%m-%d') if due_date else None
                }
                res = requests.post(f"{API_URL}/goals", json=payload)
                if res.status_code == 201:
                    st.success("Mål oprettet!")
                    st.rerun() # CHANGED FROM st.experimental_rerun() TO st.rerun()
                else:
                    st.error(res.json().get('error', "Kunne ikke oprette mål."))

    with col2:
        # --- My transactions ---
        st.header("Mine Transaktioner")
        # Fetch user-specific transactions
        res_txns = requests.get(f"{API_URL}/transactions/{user_id}")
        if res_txns.ok:
            txns = res_txns.json()
            df_txns = pd.DataFrame(txns)
            if not df_txns.empty:
                df_txns['date'] = pd.to_datetime(df_txns['date'])
                st.dataframe(df_txns)

                # --- Matplotlib: Spending by Category Pie Chart ---
                st.subheader("Forbrug fordelt på kategori")
                category_spending = df_txns.groupby('category')['amount'].sum().reset_index()
                
                fig_pie, ax_pie = plt.subplots(figsize=(8, 8))
                ax_pie.pie(category_spending['amount'], labels=category_spending['category'], 
                           autopct='%1.1f%%', startangle=90, pctdistance=0.85)
                ax_pie.axis('equal') # Equal aspect ratio ensures that pie is drawn as a circle.
                ax_pie.set_title("Forbrug fordelt på kategori")
                st.pyplot(fig_pie)

                # --- Matplotlib: Monthly Spending Trends Line Chart ---
                st.subheader("Månedlige forbrugstendenser")
                res_monthly = requests.get(f"{API_URL}/transactions/monthly_summary/{user_id}")
                if res_monthly.ok and res_monthly.json():
                    monthly_data = res_monthly.json()
                    df_monthly = pd.DataFrame(monthly_data)
                    df_monthly['month'] = pd.to_datetime(df_monthly['month'])
                    df_monthly = df_monthly.sort_values('month')

                    fig_line, ax_line = plt.subplots(figsize=(10, 6))
                    ax_line.plot(df_monthly['month'], df_monthly['total_spending'], marker='o')
                    ax_line.set_xlabel("Måned")
                    ax_line.set_ylabel("Total Forbrug")
                    ax_line.set_title("Total Forbrug over Tid")
                    ax_line.grid(True)
                    plt.xticks(rotation=45)
                    plt.tight_layout()
                    st.pyplot(fig_line)
                else:
                    st.info("Ikke nok data til at vise månedlige tendenser.")
                    
            else:
                st.info("Ingen transaktioner fundet endnu. Tilføj en i venstre side!")
        else:
            st.error("Kunne ikke hente transaktioner.")
        
        st.markdown("---")

        # --- Budget Status ---
        st.header("Mine Budgetter")
        res_budgets = requests.get(f"{API_URL}/budgets/status/{user_id}")
        if res_budgets.ok:
            budget_status = res_budgets.json()
            if budget_status:
                for b in budget_status:
                    st.write(f"**{b['category']}**:")
                    st.write(f"Budget: {b['monthly_limit']:.2f} DKK | Brugt: {b['spent']:.2f} DKK | Resterende: {b['remaining']:.2f} DKK")
                    progress = min(1.0, b['spent'] / b['monthly_limit']) if b['monthly_limit'] > 0 else 0
                    st.progress(progress)
                    if b['remaining'] < 0:
                        st.warning(f"Du har overskredet dit budget for {b['category']} med {-b['remaining']:.2f} DKK!")
                    elif b['remaining'] == 0:
                        st.info(f"Du har brugt hele dit budget for {b['category']}.")
                st.markdown("---")
            else:
                st.info("Ingen budgetter opsat endnu. Opsæt et i venstre side!")
        else:
            st.error("Kunne ikke hente budgetstatus.")

        # --- Goals Tracking ---
        st.header("Mine Mål")
        res_goals = requests.get(f"{API_URL}/goals/{user_id}")
        if res_goals.ok:
            goals = res_goals.json()
            if goals:
                for g in goals:
                    st.write(f"**Mål: {g['name']}**")
                    st.write(f"Målbeløb: {g['target_amount']:.2f} DKK | Indsamlet: {g['current_amount']:.2f} DKK")
                    if g['due_date']:
                        st.write(f"Deadline: {g['due_date']}")
                    
                    goal_progress = min(1.0, g['current_amount'] / g['target_amount']) if g['target_amount'] > 0 else 0
                    st.progress(goal_progress, text=f"{goal_progress*100:.1f}%")

                    if g['current_amount'] >= g['target_amount']:
                        st.success(f"Tillykke! Du har nået dit mål for {g['name']}!")
                    else:
                        contribute_amount = st.number_input(f"Bidrag til {g['name']}", min_value=0.01, format="%.2f", key=f"contribute_{g['id']}")
                        if st.button(f"Tilføj bidrag til {g['name']}", key=f"btn_contribute_{g['id']}"):
                            payload = {
                                "user_id": user_id,
                                "amount": contribute_amount
                            }
                            res_contribute = requests.put(f"{API_URL}/goals/{g['id']}/contribute", json=payload)
                            if res_contribute.ok:
                                st.success("Bidrag tilføjet!")
                                st.rerun() # CHANGED FROM st.experimental_rerun() TO st.rerun()
                            else:
                                st.error(res_contribute.json().get('error', "Kunne ikke tilføje bidrag."))
                st.markdown("---")
            else:
                st.info("Ingen mål opsat endnu. Opsæt et i venstre side!")
        else:
            st.error("Kunne ikke hente mål.")

        # --- Financial Insight (Mistral) ---
        st.header("Finansiel Indsigt (AI)")
        if st.button("Få Personlig Indsigt"):
            with st.spinner("Henter indsigt fra AI..."):
                # No longer sending transactions in body, backend fetches them
                insight_res = requests.get(f"{API_URL}/insight/{user_id}") 
                if insight_res.ok:
                    st.write(insight_res.json().get("insight"))
                else:
                    st.error(insight_res.json().get("error", "Fejl ved hentning af indsigt"))