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
    .metric-card {
        background-color: var(--secondary-background-color);
        border: 1px solid var(--primary-color);
        border-radius: 10px;
        padding: 20px;
        text-align: center;
        box-shadow: 2px 2px 10px rgba(0,0,0,0.1);
    }
    .footer {
        position: fixed; left: 0; bottom: 0; width: 100%;
        background-color: #002147; color: white;
        text-align: center; padding: 10px; font-size: 13px;
        border-top: 2px solid #FFD700; z-index: 1000;
    }
    .footer a { color: #FFD700 !important; text-decoration: none; font-weight: bold; }
    .footer a:hover { text-decoration: underline; }
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    .block-container { padding-bottom: 80px; }
</style>
""", unsafe_allow_html=True)

# --- 1. TIMESTAMP CACHE BUSTER ---
def get_file_timestamp():
    if not os.path.exists(FILE_PATH): return 0
    return os.path.getmtime(FILE_PATH)

# --- 2. LOAD DATA (PERMISSIVE) ---
@st.cache_data(ttl=60)
def load_data(timestamp_key):
    if not os.path.exists(FILE_PATH): return None
    try:
        df = pd.read_csv(FILE_PATH)
        
        # 1. Standardize Dates (Force UTC to match filter logic)
        if 'Date Available Online' in df.columns:
            df['Date Available Online'] = pd.to_datetime(df['Date Available Online'], utc=True, errors='coerce')
        
        # 2. Fill Missing Columns
        for col in ['Snippet', 'Type', 'Name', 'Link', 'LCDS Mention']:
            if col not in df.columns:
                df[col] = "Unknown" if col != 'Snippet' else ""
        return df
    except: return pd.DataFrame()

# --- INITIALIZATION ---
current_ts = get_file_timestamp()
df = load_data(current_ts)

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Controls")
    if st.button("🔄 Check for New Data"):
        st.cache_data.clear()
        st.rerun()
    
    if current_ts > 0:
        readable_time = datetime.fromtimestamp(current_ts).strftime('%Y-%m-%d %H:%M:%S')
        st.caption(f"📂 File Last Modified:\n{readable_time}")
    else:
        st.caption("📂 File Status: Missing")
    st.divider()

# --- SAFETY CHECKS ---
if df is None:
    st.title("⏳ LCDS Tracker is Running...")
    st.info("Initial scan in progress. Please refresh in 5 minutes.")
    st.stop()

if df.empty:
    st.title("🔬 LCDS Research & Media Tracker")
    st.warning("Tracker file exists but is empty.")
    st.stop()

# --- SIDEBAR FILTERS ---
with st.sidebar:
    st.header("🔍 Filters")
    time_filter = st.radio("Time Window", ["± 6 Months (Default)", "Last Month", "Last Week", "All Data"], index=0)
    
    if 'Type' in df.columns:
        types = ["All"] + sorted(list(df['Type'].dropna().unique()))
        selected_type = st.selectbox("Type", types)
    else: selected_type = "All"
    st.divider()

# --- FILTERING LOGIC (THE FIX) ---
filtered_df = df.copy()
today = pd.Timestamp.now(tz='UTC') # Use UTC to match dataframe

if time_filter == "All Data":
    # Show everything, even if date is missing
    pass 
else:
    # 1. Determine Date Range
    if time_filter == "Last Week":
        start_date = today - timedelta(days=7)
        end_date = today + timedelta(days=1) # Future buffer
    elif time_filter == "Last Month":
        start_date = today - timedelta(days=30)
        end_date = today + timedelta(days=1)
    else: # ± 6 Months
        start_date = today - timedelta(days=180)
        end_date = today + timedelta(days=180)
    
    # 2. APPLY FILTER (PERMISSIVE)
    # logic: (Date is inside range) OR (Date is Missing/NaT)
    # We include NaT so we don't accidentally hide rows with bad date formats
    mask = (
        (filtered_df['Date Available Online'] >= start_date) & 
        (filtered_df['Date Available Online'] <= end_date)
    ) | (filtered_df['Date Available Online'].isna())
    
    filtered_df = filtered_df[mask]

if selected_type != "All":
    filtered_df = filtered_df[filtered_df['Type'] == selected_type]

# Sort: Newest first, NaT at bottom
filtered_df.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)

# --- DOWNLOAD BUTTON ---
with st.sidebar:
    csv = filtered_df.to_csv(index=False).encode('utf-8')
    st.download_button(label="📥 Download View (CSV)", data=csv, file_name=f"lcds_view_{datetime.now().strftime('%Y-%m-%d')}.csv", mime="text/csv")

# --- MAIN DASHBOARD ---
st.title("🔬 LCDS Research & Media Tracker")

c1, c2, c3 = st.columns(3)
c1.metric("Total Records", len(filtered_df))
c2.metric("Media Mentions", len(filtered_df[filtered_df['Type'] == 'Media Mention']) if 'Type' in filtered_df.columns else 0)
new_count = len(filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))])
c3.metric("New (7 Days)", new_count)
st.markdown("---")

col1, col2 = st.columns([2,1])
with col1:
    if not filtered_df.empty:
        # Only group if valid dates exist
        valid_dates = filtered_df.dropna(subset=['Date Available Online']).copy()
        if not valid_dates.empty:
            valid_dates['Week'] = valid_dates['Date Available Online'].dt.to_period('W').apply(lambda r: r.start_time)
            daily = valid_dates.groupby(['Week', 'Type']).size().reset_index(name='Count')
            st.plotly_chart(px.bar(daily, x='Week', y='Count', color='Type', title="Weekly Volume"), use_container_width=True)
        else:
            st.info("No dated records available for timeline.")
    else:
        st.info("No data in current view.")

with col2:
    if not filtered_df.empty and 'Name' in filtered_df.columns:
        top = filtered_df['Name'].value_counts().head(5).reset_index()
        top.columns = ['Name', 'Count']
        st.plotly_chart(px.pie(top, values='Count', names='Name', hole=0.4, title="Top Academics"), use_container_width=True)

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

# --- DEBUG EXPANDER (If you suspect data is hidden) ---
with st.expander("🛠️ Debug: View Raw Data Header"):
    st.write("First 5 rows of raw file (unfiltered):")
    st.dataframe(df.head())

st.markdown("""<div class="footer" align="center">© 2026 Leverhulme Centre for Demographic Science | University of Oxford <br><a href="https://www.demography.ox.ac.uk/" target="_blank">demography.ox.ac.uk</a></div>""", unsafe_allow_html=True)
