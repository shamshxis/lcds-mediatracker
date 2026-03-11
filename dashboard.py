import streamlit as st
import pandas as pd
import os
import json
import plotly.express as px
from datetime import datetime, timedelta

# --- CONFIGURATION ---
FILE_PATH = "lcds_media_tracker.csv"
ARCHIVE_PATH = "lcds_media_archive.csv"
MEMORY_PATH = "source_memory.json"

st.set_page_config(
    page_title="LCDS Impact Dashboard", 
    page_icon="🧪", 
    layout="wide"
)

# --- CSS STYLING ---
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

# --- LOADERS ---
def get_file_timestamp():
    if not os.path.exists(FILE_PATH): return 0
    return os.path.getmtime(FILE_PATH)

def get_archive_timestamp():
    if not os.path.exists(ARCHIVE_PATH): return 0
    return os.path.getmtime(ARCHIVE_PATH)

@st.cache_data(ttl=60)
def load_data(timestamp_key):
    if not os.path.exists(FILE_PATH): return None
    try:
        df = pd.read_csv(FILE_PATH)
        if 'Date Available Online' in df.columns:
            df['Date Available Online'] = pd.to_datetime(df['Date Available Online'], utc=True, errors='coerce')
        for col in ['Snippet', 'Type', 'Name', 'Link', 'LCDS Mention']:
            if col not in df.columns:
                df[col] = "Unknown" if col != 'Snippet' else ""
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=60)
def load_archive_data(timestamp_key):
    if not os.path.exists(ARCHIVE_PATH): return None
    try:
        df = pd.read_csv(ARCHIVE_PATH)
        if 'Date Available Online' in df.columns:
            df['Date Available Online'] = pd.to_datetime(df['Date Available Online'], utc=True, errors='coerce')
        for col in ['Snippet', 'Type', 'Name', 'Link', 'LCDS Mention']:
            if col not in df.columns:
                df[col] = "Unknown" if col != 'Snippet' else ""
        return df
    except: return pd.DataFrame()

def load_memory():
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, 'r') as f:
                return json.load(f)
        except: return None
    return None

# --- INITIALIZATION ---
current_ts = get_file_timestamp()
df = load_data(current_ts)

archive_ts = get_archive_timestamp()
archive_df = load_archive_data(archive_ts)

memory = load_memory()

# --- SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Controls")
    if st.button("🔄 Check for New Data"):
        st.cache_data.clear()
        st.rerun()
    
    if current_ts > 0:
        readable_time = datetime.fromtimestamp(current_ts).strftime('%Y-%m-%d %H:%M:%S')
        st.caption(f"📂 Last Updated:\n{readable_time}")
        
    st.divider()

# --- MAIN LOGIC CHECKS ---
if df is None:
    st.title("⏳ LCDS Tracker is Running...")
    st.info("Initial scan in progress. Refresh shortly.")
    st.stop()

# --- FILTERING (ACTIVE VIEW) ---
with st.sidebar:
    st.header("🔍 Filters (Active View)")
    time_filter = st.radio("Time Window", ["± 6 Months", "Last Month", "Last Week", "All Data"], index=0)
    if 'Type' in df.columns:
        types = ["All"] + sorted(list(df['Type'].dropna().unique()))
        selected_type = st.selectbox("Type", types)
    else: selected_type = "All"
    st.divider()

filtered_df = df.copy()
today = pd.Timestamp.now(tz='UTC')

if time_filter != "All Data":
    if time_filter == "Last Week": start = today - timedelta(days=7)
    elif time_filter == "Last Month": start = today - timedelta(days=30)
    else: start = today - timedelta(days=180)
    
    # Permissive Filter
    mask = (filtered_df['Date Available Online'] >= start) | (filtered_df['Date Available Online'].isna())
    filtered_df = filtered_df[mask]

if selected_type != "All":
    filtered_df = filtered_df[filtered_df['Type'] == selected_type]

filtered_df.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)

# --- DOWNLOAD ACTIVE ---
with st.sidebar:
    csv = filtered_df.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Download Active CSV", csv, "lcds_active_view.csv", "text/csv")


# ==========================================
# UI LAYOUT: TABS
# ==========================================
tab1, tab2 = st.tabs(["📊 Active Dashboard (±6 Months)", "🗄️ Historical Archive"])

# --- TAB 1: EXISTING DASHBOARD ---
with tab1:
    st.title("🔬 LCDS Research & Media Tracker")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Records", len(filtered_df))
    c2.metric("Media Mentions", len(filtered_df[filtered_df['Type'] == 'Media Mention']) if 'Type' in filtered_df.columns else 0)
    new_count = len(filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))])
    c3.metric("New (7 Days)", new_count)
    st.markdown("---")

    col1, col2 = st.columns([2,1])
    with col1:
        if not filtered_df.empty and not filtered_df['Date Available Online'].isna().all():
            valid = filtered_df.dropna(subset=['Date Available Online']).copy()
            valid['Week'] = valid['Date Available Online'].dt.to_period('W').apply(lambda r: r.start_time)
            daily = valid.groupby(['Week', 'Type']).size().reset_index(name='Count')
            st.plotly_chart(px.bar(daily, x='Week', y='Count', color='Type', title="Weekly Volume"), use_container_width=True)
        else: st.info("No dated records for chart.")

    with col2:
        if not filtered_df.empty and 'Name' in filtered_df.columns:
            top = filtered_df['Name'].value_counts().head(5).reset_index()
            top.columns = ['Name', 'Count']
            st.plotly_chart(px.pie(top, values='Count', names='Name', hole=0.4, title="Top Academics"), use_container_width=True)

    st.subheader("📄 Latest Updates")
    st.dataframe(
        filtered_df[["Date Available Online", "Type", "Name", "LCDS Mention", "Source", "Link"]],
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open 🔗"), "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY")},
        use_container_width=True, hide_index=True
    )

    st.markdown("---")

    # 🧠 BRAIN & RAW DUMP SECTION
    c_brain, c_raw = st.columns(2)
    with c_brain:
        with st.expander("🧠 Tracker Memory (Trusted Sources)"):
            if memory and "trusted_sources" in memory:
                st.success(f"The tracker has learned {len(memory['trusted_sources'])} trusted domains.")
                st.write(memory["trusted_sources"])
            else:
                st.warning("No memory file found yet. Wait for the next tracker run.")

    with c_raw:
        with st.expander("🛠️ Debug: Raw Data Dump"):
            st.write("First 10 rows of active file:")
            st.dataframe(df.head(10))

# --- TAB 2: HISTORICAL ARCHIVE ---
with tab2:
    st.title("🗄️ Master Historical Archive")
    st.markdown("This vault stores the permanent history of all hits, bypassing the 6-month rule. Use the built-in column headers to sort, or the search bar below to find specific names or events.")
    
    if archive_df is not None and not archive_df.empty:
        
        # 1. Calculate Date Range
        valid_archive_dates = archive_df.dropna(subset=['Date Available Online'])
        if not valid_archive_dates.empty:
            oldest_date = valid_archive_dates['Date Available Online'].min().strftime('%d %B %Y')
            newest_date = valid_archive_dates['Date Available Online'].max().strftime('%d %B %Y')
            st.info(f"**Archive Status:** Currently holding **{len(archive_df)} records**. Data ranges from **{oldest_date}** to **{newest_date}**.")
        else:
            st.info(f"**Archive Status:** Currently holding **{len(archive_df)} records** (Exact dates unavailable).")

        # 2. Local Search Bar
        archive_search = st.text_input("🔍 Search Archive by Academic Name, Title, or Keyword")
        display_archive = archive_df.copy()
        
        if archive_search:
            display_archive = display_archive[
                display_archive.apply(lambda row: archive_search.lower() in str(row.values).lower(), axis=1)
            ]
            st.caption(f"Found {len(display_archive)} results for '{archive_search}'")

        display_archive.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)

        # 3. Data Table
        st.dataframe(
            display_archive[["Date Available Online", "Type", "Name", "LCDS Mention", "Source", "Link"]],
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open 🔗"), "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY")},
            use_container_width=True, hide_index=True, height=600
        )
        
        # 4. Dedicated Download
        csv_archive = archive_df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Full Archive (CSV)", csv_archive, "lcds_full_archive.csv", "text/csv")
        
    else:
        st.warning("The historical archive file (`lcds_media_archive.csv`) has not been generated or is empty. It will populate over time.")


st.markdown("""<div class="footer" align="center">© 2026 Leverhulme Centre for Demographic Science | University of Oxford <br><a href="https://www.demography.ox.ac.uk/" target="_blank">demography.ox.ac.uk</a></div>""", unsafe_allow_html=True)
