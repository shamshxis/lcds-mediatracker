import streamlit as st
import pandas as pd
import os
import plotly.express as px
from datetime import datetime, timedelta

# --- CONFIGURATION ---
FILE_PATH = "lcds_media_tracker.csv"

st.set_page_config(
    page_title="LCDS Impact Dashboard", 
    page_icon="🧪", 
    layout="wide"
)

# --- CSS STYLING (OXFORD BLUE FOOTER) ---
st.markdown("""
<style>
    /* Metric Cards */
    .metric-card {
        background-color: var(--secondary-background-color);
        border: 1px solid var(--primary-color);
        border-radius: 10px;
        padding: 20px;
        text-align: center;
        box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
    }
    
    /* Oxford Blue Footer */
    .footer {
        position: fixed; 
        left: 0; 
        bottom: 0; 
        width: 100%;
        background-color: #002147; /* Oxford Blue */
        color: white; /* White text for contrast */
        text-align: center; 
        padding: 10px; 
        font-size: 13px;
        border-top: 2px solid #FFD700; /* Yellow border top */
        z-index: 1000;
    }
    
    /* Yellow Links in Footer */
    .footer a { 
        color: #FFD700 !important; 
        text-decoration: none; 
        font-weight: bold; 
    }
    .footer a:hover {
        text-decoration: underline;
    }
    
    /* Hide Default Streamlit Elements */
    #MainMenu {visibility: hidden;} 
    footer {visibility: hidden;}
    .block-container { padding-bottom: 80px; }
</style>
""", unsafe_allow_html=True)

# --- LOAD DATA (FAIL-SAFE) ---
@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(FILE_PATH):
        return None
    try:
        df = pd.read_csv(FILE_PATH)
        if 'Date Available Online' in df.columns:
            df['Date Available Online'] = pd.to_datetime(df['Date Available Online'], errors='coerce')
        
        # Ensure essential columns exist
        for col in ['Snippet', 'Type', 'Name', 'Link', 'LCDS Mention']:
            if col not in df.columns:
                df[col] = "Unknown" if col != 'Snippet' else ""
        return df
    except Exception:
        return pd.DataFrame()

df = load_data()

# --- CASE 1: FILE IS MISSING (First Run) ---
if df is None:
    st.title("⏳ LCDS Tracker is Running...")
    st.info("The tracker is currently performing its initial scan. This can take 5-10 minutes.")
    st.markdown("Please refresh this page in a few minutes.")
    st.stop()

# --- CASE 2: FILE IS EMPTY ---
if df.empty:
    st.title("🔬 LCDS Research & Media Tracker")
    st.warning("The tracker file exists but contains no data yet. Please check the GitHub Actions logs.")
    st.stop()

# --- CASE 3: DASHBOARD LOADED ---
with st.sidebar:
    st.header("🔍 Filters")
    # Default is ± 6 Months
    time_filter = st.radio("Time Window", ["± 6 Months (Default)", "Last Month", "Last Week"], index=0)
    
    if 'Type' in df.columns:
        types = ["All"] + sorted(list(df['Type'].dropna().unique()))
        selected_type = st.selectbox("Type", types)
    else:
        selected_type = "All"
        
    st.divider()
    
    # DOWNLOAD BUTTON LOGIC
    # We define it here but populate data after filtering below
    download_placeholder = st.empty()
    
    st.caption(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# --- FILTERING ---
filtered_df = df.copy()
today = pd.Timestamp.now().normalize()

if time_filter == "Last Week":
    filtered_df = filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))]
elif time_filter == "Last Month":
    filtered_df = filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=30))]
else:
    # ± 6 Months Window
    s = today - timedelta(days=180)
    e = today + timedelta(days=180)
    filtered_df = filtered_df[(filtered_df['Date Available Online'] >= s) & (filtered_df['Date Available Online'] <= e)]

if selected_type != "All":
    filtered_df = filtered_df[filtered_df['Type'] == selected_type]

# Sort: Newest first, putting 'Unknown Dates' (NaT) at the bottom
filtered_df.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)

# --- POPULATE DOWNLOAD BUTTON ---
with st.sidebar:
    csv = filtered_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Current View (CSV)",
        data=csv,
        file_name=f"lcds_tracker_view_{datetime.now().strftime('%Y-%m-%d')}.csv",
        mime="text/csv",
    )

# --- MAIN UI ---
st.title("🔬 LCDS Research & Media Tracker")

# METRICS
c1, c2, c3 = st.columns(3)
c1.metric("Total Records", len(filtered_df))
c2.metric("Media Mentions", len(filtered_df[filtered_df['Type'] == 'Media Mention']) if 'Type' in filtered_df.columns else 0)
new_count = len(filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))])
c3.metric("New (7 Days)", new_count)

st.markdown("---")

# CHARTS
col1, col2 = st.columns([2,1])
with col1:
    if 'Week' not in filtered_df.columns and not filtered_df.empty:
        filtered_df['Week'] = filtered_df['Date Available Online'].dt.to_period('W').apply(lambda r: r.start_time)
        
    daily = filtered_df.groupby(['Week', 'Type']).size().reset_index(name='Count')
    if not daily.empty:
        fig = px.bar(daily, x='Week', y='Count', color='Type', title="Weekly Volume")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough data for timeline chart.")
    
with col2:
    if 'Name' in filtered_df.columns:
        top = filtered_df['Name'].value_counts().head(5).reset_index()
        top.columns = ['Name', 'Count']
        fig2 = px.pie(top, values='Count', names='Name', hole=0.4, title="Top Academics")
        st.plotly_chart(fig2, use_container_width=True)

# TABLE
st.subheader("📄 Latest Updates")
st.dataframe(
    filtered_df[["Date Available Online", "Type", "Name", "LCDS Mention", "Source", "Link"]],
    column_config={
        "Link": st.column_config.LinkColumn("Link", display_text="Open 🔗"),
        "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
    },
    use_container_width=True,
    hide_index=True
)

# --- FOOTER INJECTION ---
st.markdown("""
<div class="footer">
    <p>
        © 2026 <b>Leverhulme Centre for Demographic Science | University of Oxford </b> <br>
        <a href="https://www.demography.ox.ac.uk/" target="_blank">demography.ox.ac.uk</a> | 
    </p>
</div>
""", unsafe_allow_html=True)
