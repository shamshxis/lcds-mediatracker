import streamlit as st
import pandas as pd
import os

# CONFIG
FILE_PATH = "lcds_media_tracker.csv"

st.set_page_config(page_title="LCDS Tracker", layout="wide")
st.title("📰 LCDS Media & Impact Dashboard")

# 1. LOAD DATA (Fast & Cached)
# We use @st.cache_data so it doesn't reload the CSV on every click
@st.cache_data(ttl=300) # Clears cache every 5 mins to pick up new bot data
def load_data():
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame()
    
    try:
        df = pd.read_csv(FILE_PATH)
        # Ensure dates are datetime objects
        df['Date Available Online'] = pd.to_datetime(df['Date Available Online'])
        df['Year'] = df['Date Available Online'].dt.year
        return df
    except Exception as e:
        st.error(f"Error reading CSV: {e}")
        return pd.DataFrame()

df = load_data()

if df.empty:
    st.info("Waiting for the Tracker Bot to populate data... (Check back in 10 mins)")
    st.stop()

# 2. FILTERS (Sidebar)
with st.sidebar:
    st.header("Filters")
    
    # Year Filter
    all_years = sorted(df['Year'].dropna().unique().astype(int), reverse=True)
    selected_year = st.selectbox("Year", ["All"] + list(all_years))
    
    # Name Filter
    all_names = sorted(df['Name'].dropna().unique().tolist())
    selected_name = st.multiselect("Academic / Project", all_names)
    
    # Source Filter
    all_sources = sorted(df['Source'].dropna().unique().tolist())
    selected_source = st.multiselect("Source", all_sources)

# 3. APPLY FILTERS
df_view = df.copy()

if selected_year != "All":
    df_view = df_view[df_view['Year'] == selected_year]

if selected_name:
    df_view = df_view[df_view['Name'].isin(selected_name)]
    
if selected_source:
    df_view = df_view[df_view['Source'].isin(selected_source)]

# 4. METRICS
c1, c2, c3 = st.columns(3)
c1.metric("Total Mentions", len(df_view))
c2.metric("Academics Mentioned", df_view['Name'].nunique())
c3.metric("Latest Update", str(df_view['Date Available Online'].max().date()))

# 5. DATA TABLE
st.dataframe(
    df_view[[
        "LCDS Mention", "Name", "Source", "Date Available Online", "Link", "Summary"
    ]],
    column_config={
        "Link": st.column_config.LinkColumn("Read Article"),
        "Date Available Online": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
    },
    use_container_width=True,
    hide_index=True
)

# 6. DOWNLOAD
csv = df_view.to_csv(index=False).encode('utf-8')
st.download_button("Download Filtered Data", csv, "lcds_impact_report.csv", "text/csv")
