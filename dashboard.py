import streamlit as st
import pandas as pd
import os
import plotly.express as px
from datetime import datetime, timedelta

# --- CONFIGURATION ---
FILE_PATH = "lcds_media_tracker.csv"

st.set_page_config(
    page_title="LCDS Impact Dashboard", 
    page_icon="📰", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CSS: DARK MODE & FOOTER ---
st.markdown("""
<style>
    .footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: var(--background-color);
        color: var(--text-color);
        text-align: center;
        padding: 12px;
        font-size: 13px;
        border-top: 1px solid rgba(150, 150, 150, 0.2);
        z-index: 1000;
    }
    .footer a {
        color: #0072CE;
        text-decoration: none;
        font-weight: bold;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# 2. LOAD DATA
@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame()
    try:
        df = pd.read_csv(FILE_PATH)
        df['Date Available Online'] = pd.to_datetime(df['Date Available Online'])
        df['Year'] = df['Date Available Online'].dt.year
        
        # Safety: Fill missing columns
        if "Snippet" not in df.columns and "Summary" in df.columns:
            df["Snippet"] = df["Summary"]
        if "Type" not in df.columns:
            df["Type"] = "Media Mention"
            
        return df
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        return pd.DataFrame()

df = load_data()

# 3. HEADER
st.title("📰 LCDS Media & Impact Intelligence")

if df.empty:
    st.info("System initializing... Data will appear after the first background run.")
    st.stop()

# 4. SIDEBAR & FILTERS
with st.sidebar:
    st.header("🔍 Intelligence Filters")
    
    # A. Time Filter (Crucial Request)
    time_option = st.radio(
        "Time Range",
        ["Last Week", "Last Month", "Last Year", "All Time (Sep 2019+)"],
        index=3 # Default to All Time
    )
    
    # Calculate Start Date
    today = pd.Timestamp.now()
    if time_option == "Last Week":
        start_date = today - timedelta(days=7)
    elif time_option == "Last Month":
        start_date = today - timedelta(days=30)
    elif time_option == "Last Year":
        start_date = today - timedelta(days=365)
    else:
        start_date = pd.Timestamp("2019-09-01")

    # Apply Time Filter Immediately
    df_filtered = df[df['Date Available Online'] >= start_date].copy()
    
    st.divider()
    
    # B. Category & Name Filters
    selected_types = st.multiselect("Content Type", sorted(df_filtered['Type'].unique()))
    selected_names = st.multiselect("Academic", sorted(df_filtered['Name'].unique()))
    
    st.divider()
    
    # C. Download
    st.download_button(
        label="📥 Download View (CSV)", 
        data=df_filtered.to_csv(index=False).encode('utf-8'),
        file_name=f"lcds_impact_{time_option.replace(' ', '_').lower()}.csv",
        mime="text/csv"
    )

# 5. GLOBAL SEARCH
search_query = st.text_input("🔎 Search (e.g., 'Fertility', 'Keynote', 'BBC')", "")

# Apply Sidebar & Search Filters
if selected_types: df_filtered = df_filtered[df_filtered['Type'].isin(selected_types)]
if selected_names: df_filtered = df_filtered[df_filtered['Name'].isin(selected_names)]

if search_query:
    mask = (
        df_filtered['Name'].str.contains(search_query, case=False, na=False) |
        df_filtered['LCDS Mention'].str.contains(search_query, case=False, na=False) |
        df_filtered['Snippet'].str.contains(search_query, case=False, na=False)
    )
    df_filtered = df_filtered[mask]

# 6. METRICS
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Mentions", len(df_filtered))
c2.metric("Academics", df_filtered['Name'].nunique())
c3.metric("Keynotes", len(df_filtered[df_filtered['Type'].str.contains("Conference|Keynote", na=False)]))
c4.metric("Sources", df_filtered['Source'].nunique())

st.divider()

# 7. MAIN TABLE
st.subheader(f"Mentions ({time_option})")
st.dataframe(
    df_filtered[[
        "Type", "Name", "LCDS Mention", "Snippet", "Link", "Date Available Online"
    ]],
    column_config={
        "Link": st.column_config.LinkColumn("Action", display_text="View Here"),
        "Snippet": st.column_config.TextColumn("Context", width="large"),
        "LCDS Mention": st.column_config.TextColumn("Title", width="medium"),
        "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
        "Type": st.column_config.TextColumn("Category", width="small"),
    },
    use_container_width=True,
    hide_index=True
)

st.divider()

# 8. ANALYTICS
col1, col2 = st.columns(2)
with col1:
    st.markdown("#### Share of Voice")
    if not df_filtered.empty:
        top = df_filtered['Name'].value_counts().reset_index().head(10)
        top.columns = ['Name', 'Count']
        fig = px.pie(top, values='Count', names='Name', hole=0.4)
        st.plotly_chart(fig, use_container_width=True)

with col2:
    st.markdown("#### Media vs. Impact")
    if not df_filtered.empty:
        types = df_filtered['Type'].value_counts().reset_index()
        types.columns = ['Type', 'Count']
        fig2 = px.bar(types, x='Count', y='Type', orientation='h', color='Count')
        st.plotly_chart(fig2, use_container_width=True)

# 9. FOOTER
st.markdown("""
<div class="footer">
    &copy; University of Oxford 2026. All rights reserved. 
    <a href="https://www.demography.ox.ac.uk" target="_blank">demography.ox.ac.uk</a>
</div>
""", unsafe_allow_html=True)
