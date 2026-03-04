import streamlit as st
import pandas as pd
import os
from datetime import datetime, timedelta

# CONFIG
FILE_PATH = "lcds_media_tracker.csv"

st.set_page_config(page_title="LCDS Tracker", layout="wide")
st.title("📰 LCDS Media & Impact Dashboard")

# 1. LOAD DATA
@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame()
    try:
        df = pd.read_csv(FILE_PATH)
        df['Date Available Online'] = pd.to_datetime(df['Date Available Online'])
        df['Year'] = df['Date Available Online'].dt.year
        return df
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        return pd.DataFrame()

df = load_data()

if df.empty:
    st.info("Tracker is initializing... please check back after the next scheduled run.")
    st.stop()

# 2. SIDEBAR FILTERS
with st.sidebar:
    st.header("🔍 Filters")
    
    # Time Period
    time_filter = st.radio(
        "Time Period",
        ["All Data", "Last Year", "Last Month", "Last Week"],
        index=0
    )
    
    # Date Logic
    today = pd.Timestamp.now()
    if time_filter == "Last Week":
        start_date = today - timedelta(days=7)
    elif time_filter == "Last Month":
        start_date = today - timedelta(days=30)
    elif time_filter == "Last Year":
        start_date = today - timedelta(days=365)
    else:
        start_date = pd.Timestamp("2019-09-01") 

    df_filtered = df[df['Date Available Online'] >= start_date].copy()
    
    st.markdown("---")
    
    # --- NEW: TYPE FILTER (Find Keynotes) ---
    all_types = sorted(df_filtered['Type'].dropna().unique().tolist())
    selected_types = st.multiselect("Category (e.g. Keynote)", all_types)
    
    # Academic Filter
    available_names = sorted(df_filtered['Name'].dropna().unique().tolist())
    selected_names = st.multiselect("Academic", available_names)
    
    # Source Filter
    available_sources = sorted(df_filtered['Source'].dropna().unique().tolist())
    selected_source = st.multiselect("Source", available_sources)

# 3. APPLY FILTERS
if selected_types:
    df_filtered = df_filtered[df_filtered['Type'].isin(selected_types)]

if selected_names:
    df_filtered = df_filtered[df_filtered['Name'].isin(selected_names)]

if selected_source:
    df_filtered = df_filtered[df_filtered['Source'].isin(selected_source)]

# 4. METRICS
st.markdown(f"### Showing: {time_filter}")
c1, c2, c3 = st.columns(3)
c1.metric("Items Found", len(df_filtered))
c2.metric("Keynotes/Talks", len(df_filtered[df_filtered['Type'].str.contains("Keynote", na=False)]))
c3.metric("Unique People", df_filtered['Name'].nunique())

# 5. DATA TABLE
df_display = df_filtered.copy()
df_display['Date'] = df_display['Date Available Online'].dt.strftime('%Y-%m-%d')

st.dataframe(
    df_display[[
        "Type", "LCDS Mention", "Name", "Source", "Date", "Link"
    ]],
    column_config={
        "Link": st.column_config.LinkColumn("Link"),
        "Type": st.column_config.TextColumn("Category", width="small"),
        "LCDS Mention": st.column_config.TextColumn("Title / Summary", width="large"),
    },
    use_container_width=True,
    hide_index=True
)

# 6. DOWNLOAD
csv = df_filtered.to_csv(index=False).encode('utf-8')
st.download_button(
    label=f"Download CSV ({time_filter})", 
    data=csv, 
    file_name=f"lcds_media_keynotes.csv", 
    mime="text/csv"
)
