import streamlit as st
import pandas as pd
import os
import plotly.express as px
from datetime import datetime, timedelta

FILE_PATH = "lcds_media_tracker.csv"
st.set_page_config(page_title="LCDS Impact Dashboard", page_icon="🧪", layout="wide")

st.markdown("""<style>.metric-card {background-color: var(--secondary-background-color); border: 1px solid var(--primary-color); border-radius: 10px; padding: 20px; text-align: center; box-shadow: 2px 2px 10px rgba(0,0,0,0.1);} .footer {position: fixed; left: 0; bottom: 0; width: 100%; background-color: var(--secondary-background-color); color: var(--text-color); text-align: center; padding: 10px; font-size: 13px; border-top: 1px solid rgba(150, 150, 150, 0.2);} .footer a { color: #0072CE; text-decoration: none; font-weight: bold; } #MainMenu {visibility: hidden;} footer {visibility: hidden;} .block-container { padding-bottom: 80px; }</style>""", unsafe_allow_html=True)

@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(FILE_PATH): return pd.DataFrame()
    try:
        df = pd.read_csv(FILE_PATH)
        df['Date Available Online'] = pd.to_datetime(df['Date Available Online'], errors='coerce')
        for col in ['Snippet', 'Type', 'Name']:
            if col not in df.columns: df[col] = "Unknown" if col != 'Snippet' else ""
        return df
    except: return pd.DataFrame()

df = load_data()

with st.sidebar:
    st.header("🔍 Filters")
    time_filter = st.radio("Time Window", ["± 6 Months", "Last Month", "Last Week"], index=0)
    if not df.empty:
        types = ["All"] + sorted(list(df['Type'].dropna().unique()))
        selected_type = st.selectbox("Type", types)
    else: selected_type = "All"
    st.divider()
    st.caption(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

if not df.empty:
    filtered_df = df.copy()
    today = pd.Timestamp.now().normalize()
    if time_filter == "Last Week": filtered_df = filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))]
    elif time_filter == "Last Month": filtered_df = filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=30))]
    else:
        s, e = today - timedelta(days=180), today + timedelta(days=180)
        filtered_df = filtered_df[(filtered_df['Date Available Online'] >= s) & (filtered_df['Date Available Online'] <= e)]
    if selected_type != "All": filtered_df = filtered_df[filtered_df['Type'] == selected_type]
    filtered_df.sort_values(by='Date Available Online', ascending=False, inplace=True)
else: filtered_df = pd.DataFrame()

st.title("🔬 LCDS Research & Media Tracker")

if filtered_df.empty:
    st.info("No data available yet. Please wait for the tracker to complete its first run.")
else:
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Records", len(filtered_df))
    c2.metric("Media Mentions", len(filtered_df[filtered_df['Type'] == 'Media Mention']))
    c3.metric("New (7 Days)", len(filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))]))
    st.markdown("---")
    col1, col2 = st.columns([2,1])
    with col1:
        filtered_df['Week'] = filtered_df['Date Available Online'].dt.to_period('W').apply(lambda r: r.start_time)
        daily = filtered_df.groupby(['Week', 'Type']).size().reset_index(name='Count')
        if not daily.empty: st.plotly_chart(px.bar(daily, x='Week', y='Count', color='Type', title="Weekly Volume"), use_container_width=True)
    with col2:
        if 'Name' in filtered_df.columns:
            top = filtered_df['Name'].value_counts().head(5).reset_index()
            top.columns = ['Name', 'Count']
            st.plotly_chart(px.pie(top, values='Count', names='Name', hole=0.4, title="Top Academics"), use_container_width=True)
    st.subheader("📄 Latest Updates")
    st.dataframe(filtered_df[["Date Available Online", "Type", "Name", "LCDS Mention", "Source", "Link"]], column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open 🔗"), "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY")}, use_container_width=True, hide_index=True)

st.markdown("""<div class="footer"><p>© 2026 <b>Leverhulme Centre for Demographic Science</b> | University of Oxford</p></div>""", unsafe_allow_html=True)
