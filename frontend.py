import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt

API_URL = 'http://localhost:5000/api'

st.set_page_config(layout="wide")
st.title("Dit Finans Dashboard")

# --- Initialize requests session to handle cookies ---
if 'session' not in st.session_state:
    st.session_state.session = requests.Session()

# --- Session state for signed-in user ---
if "user" not in st.session_state:
    st.session_state.user = None

# --- Login / Signup ---
if st.session_state.user is None:
    st.subheader("Log ind / Registrer")
    mode = st.radio("Vælg:", ["Login", "Registrer"], horizontal=True)
    username = st.text_input("Brugernavn", key="auth_username")
    password = st.text_input("Adgangskode", type="password", key="auth_password")

    if st.button(mode, key="auth_button"):
        endpoint = "/login" if mode == "Login" else "/signup"
        res = st.session_state.session.post(API_URL + endpoint, json={
            "username": username,
            "password": password
        })

        if res.ok:
            data = res.json()
            if mode == "Login":
                st.session_state.user = data["user"]
                st.success(f"Logget ind som {st.session_state.user['username']}!")
                st.rerun()
            else:
                st.success("Bruger oprettet – du kan nu logge ind.")
        else:
            st.error(res.json().get("error", "Noget gik galt under login/registrering"))

    st.markdown("---")
    st.info("Kører du dette for første gang? Du kan seed data til en testbruger:")
    if st.button("Seed Test Data (Sletter eksisterende data!)", key="seed_button"):
        seed_res = st.session_state.session.post(f"{API_URL}/seed")
        if seed_res.ok:
            st.success(seed_res.json().get('message', 'Data seedet!'))
            st.rerun()
        else:
            st.error(seed_res.json().get('error', 'Fejl ved seeding af data.'))

else:
    # --- Sidebar logout ---
    st.sidebar.markdown(f"**Velkommen, {st.session_state.user['username']}!**")
    if st.sidebar.button("Log ud"):
        logout_res = st.session_state.session.post(f"{API_URL}/logout")
        if logout_res.ok:
            st.session_state.user = None
            st.success("Du er logget ud.")
            st.rerun()
        else:
            st.error(logout_res.json().get('error', "Fejl ved logud."))

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        # Add Transaction
        st.header("Tilføj Transaktion")
        with st.form("txn_form"):
            category = st.text_input("Kategori", key="txn_category")
            amount = st.number_input("Beløb", format="%.2f", key="txn_amount")
            date = st.date_input("Dato", datetime.now(), key="txn_date")
            if st.form_submit_button("Gem Transaktion"):
                payload = {
                    "category": category,
                    "amount": amount,
                    "date": date.strftime('%Y-%m-%d')
                }
                res = st.session_state.session.post(f"{API_URL}/transactions", json=payload)
                if res.status_code == 201:
                    st.success("Transaktion tilføjet!")
                    st.rerun()
                else:
                    st.error(res.json().get('error', "Kunne ikke tilføje transaktion"))
        st.markdown("---")

        # Add Budget
        st.header("Opsæt Budget")
        with st.form("budget_form"):
            budget_category = st.text_input("Kategori for budget", key="budget_category")
            monthly_limit = st.number_input(
                "Månedlig grænse", min_value=0.0, format="%.2f", key="budget_limit"
            )
            if st.form_submit_button("Gem Budget"):
                payload = {"category": budget_category, "monthly_limit": monthly_limit}
                res = st.session_state.session.post(f"{API_URL}/budgets", json=payload)
                if res.status_code == 201:
                    st.success("Budget oprettet!")
                    st.rerun()
                else:
                    st.error(res.json().get('error', "Kunne ikke oprette budget. Husk unik kategori."))
        st.markdown("---")

        # Add Goal
        st.header("Opsæt Mål")
        with st.form("goal_form"):
            goal_name = st.text_input("Mål navn", key="goal_name")
            target_amount = st.number_input("Målbeløb", min_value=0.0, format="%.2f", key="goal_target")
            due_date = st.date_input("Deadline (valgfri)", None, key="goal_due_date")
            if st.form_submit_button("Gem Mål"):
                payload = {
                    "name": goal_name,
                    "target_amount": target_amount,
                    "due_date": due_date.strftime('%Y-%m-%d') if due_date else None
                }
                res = st.session_state.session.post(f"{API_URL}/goals", json=payload)
                if res.status_code == 201:
                    st.success("Mål oprettet!")
                    st.rerun()
                else:
                    st.error(res.json().get('error', "Kunne ikke oprette mål."))
        st.markdown("---")

        # CSV Importer
        st.header("Importer Transaktioner fra CSV")
        uploaded = st.file_uploader(
            "Vælg en CSV med kolonnerne: category,amount,date (YYYY-MM-DD)",
            type=["csv"], key="csv_uploader"
        )
        if uploaded is not None:
            try:
                df_csv = pd.read_csv(uploaded)
                missing = {"category", "amount", "date"} - set(df_csv.columns)
                if missing:
                    st.error(f"CSV mangler kolonner: {', '.join(missing)}")
                else:
                    if st.button("Importer transaktioner fra CSV"):
                        errors = []
                        for idx, row in df_csv.iterrows():
                            payload = {
                                "category": str(row["category"]),
                                "amount": float(row["amount"]),
                                "date": str(row["date"])
                            }
                            r = st.session_state.session.post(f"{API_URL}/transactions", json=payload)
                            if not r.ok:
                                errors.append(f"Række {idx+1}: {r.json().get('error')}")
                        if not errors:
                            st.success(f"Importeret {len(df_csv)} transaktioner!")
                        else:
                            st.error("Nogle transaktioner mislykkedes:")
                            for err in errors:
                                st.write(f"- {err}")
                        st.rerun()
            except Exception as e:
                st.error(f"Kunne ikke læse CSV: {e}")

    with col2:
        # My Transactions
        st.header("Mine Transaktioner")
        res_txns = st.session_state.session.get(f"{API_URL}/transactions")
        if res_txns.ok:
            txns = res_txns.json()
            df_txns = pd.DataFrame(txns)
            if not df_txns.empty:
                df_txns['date'] = pd.to_datetime(df_txns['date'])
                st.dataframe(df_txns)

                # Spending by Category
                st.subheader("Forbrug fordelt på kategori")
                cat_spend = df_txns.groupby('category')['amount'].sum().reset_index()
                fig_pie, ax_pie = plt.subplots(figsize=(8, 8))
                ax_pie.pie(
                    cat_spend['amount'],
                    labels=cat_spend['category'],
                    autopct='%1.1f%%',
                    startangle=90,
                    pctdistance=0.85
                )
                ax_pie.axis('equal')
                ax_pie.set_title("Forbrug pr. kategori")
                st.pyplot(fig_pie)

                # Monthly Trends
                st.subheader("Månedlige forbrugstendenser")
                res_mon = st.session_state.session.get(f"{API_URL}/transactions/monthly_summary")
                if res_mon.ok and res_mon.json():
                    mon = res_mon.json()
                    df_mon = pd.DataFrame(mon)
                    df_mon['month'] = pd.to_datetime(df_mon['month'])
                    df_mon = df_mon.sort_values('month')

                    fig_line, ax_line = plt.subplots(figsize=(10, 6))
                    ax_line.plot(df_mon['month'], df_mon['total_spending'], marker='o')
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
                st.info("Ingen transaktioner fundet endnu.")
        else:
            st.error("Kunne ikke hente transaktioner.")

        st.markdown("---")

        # Budget Status
        st.header("Mine Budgetter")
        res_budgets = st.session_state.session.get(f"{API_URL}/budgets/status")
        if res_budgets.ok:
            bstat = res_budgets.json()
            if bstat:
                for b in bstat:
                    st.write(f"**{b['category']}**:")
                    st.write(
                        f"Budget: {b['monthly_limit']:.2f} DKK | "
                        f"Brukt: {b['spent']:.2f} DKK | "
                        f"Resterende: {b['remaining']:.2f} DKK"
                    )
                    p = min(1.0, b['spent'] / b['monthly_limit']) if b['monthly_limit'] > 0 else 0
                    st.progress(p)
                    if b['remaining'] < 0:
                        st.warning(f"Overskredet budget for {b['category']} med {-b['remaining']:.2f} DKK!")
                    elif b['remaining'] == 0:
                        st.info(f"Hele budget for {b['category']} brugt.")
                st.markdown("---")
            else:
                st.info("Ingen budgetter opsat endnu.")
        else:
            st.error("Kunne ikke hente budgetstatus.")

        # Goals Tracking
        st.header("Mine Mål")
        res_goals = st.session_state.session.get(f"{API_URL}/goals")
        if res_goals.ok:
            goals = res_goals.json()
            if goals:
                for g in goals:
                    st.write(f"**Mål: {g['name']}**")
                    st.write(
                        f"Målbeløb: {g['target_amount']:.2f} DKK | "
                        f"Indsamlet: {g['current_amount']:.2f} DKK"
                    )
                    if g['due_date']:
                        st.write(f"Deadline: {g['due_date']}")
                    prog = min(1.0, g['current_amount'] / g['target_amount']) if g['target_amount'] > 0 else 0
                    st.progress(prog, text=f"{prog*100:.1f}%")
                    if g['current_amount'] < g['target_amount']:
                        contrib = st.number_input(
                            f"Bidrag til {g['name']}",
                            min_value=0.01, format="%.2f", key=f"contrib_{g['id']}"
                        )
                        if st.button(f"Tilføj bidrag til {g['name']}", key=f"btn_contrib_{g['id']}"):
                            payload = {"amount": contrib}
                            rc = st.session_state.session.put(f"{API_URL}/goals/{g['id']}/contribute", json=payload)
                            if rc.ok:
                                st.success("Bidrag tilføjet!")
                                st.rerun()
                            else:
                                st.error(rc.json().get('error'))
                st.markdown("---")
            else:
                st.info("Ingen mål opsat endnu.")
        else:
            st.error("Kunne ikke hente mål.")

        # Financial Insight (AI)
        st.header("Finansiel Indsigt (AI)")
        if st.button("Få Personlig Indsigt"):
            with st.spinner("Henter indsigt fra AI..."):
                insight_res = st.session_state.session.get(f"{API_URL}/insight")
                if insight_res.ok:
                    st.write(insight_res.json().get("insight"))
                else:
                    st.error(insight_res.json().get('error', "Fejl ved hentning af indsigt"))

        st.markdown("---")
# Histogram over dagligt forbrug

        # Monte Carlo Spending Forecast
        st.header("Forecast: Forventet Månedligt Forbrug")
        if st.button("Kør Forecast", key="forecast_btn"):
            with st.spinner("Simulerer fremtidigt forbrug..."):
                res = st.session_state.session.get(f"{API_URL}/spending_forecast")
                if res.ok:
                    data = res.json()
                    if data.get("message"):
                        st.info(data["message"])
                    else:
                        pct = data["percentiles"]
                        st.write(f"Dage tilbage i måneden: **{data['days_left']}**")
                        st.write(f"Simuleringer: **{data['simulations']}**")
                        st.markdown(
                            f"- 5th percentile: **{pct['5th']:.2f} DKK**\n"
                            f"- 50th percentile (median): **{pct['50th']:.2f} DKK**\n"
                            f"- 95th percentile: **{pct['95th']:.2f} DKK**"
                        )
                else:
                    st.error("Fejl ved hentning af forecast.")
