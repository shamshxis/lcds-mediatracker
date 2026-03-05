import streamlit as st
import pandas as pd
import os
import plotly.express as px
from datetime import datetime, timedelta, date

# --- CONFIGURATION ---
FILE_PATH = "lcds_media_tracker.csv"

st.set_page_config(
    page_title="LCDS Impact Dashboard", 
    page_icon="🧪", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CSS STYLING ---
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 20px;
        text-align: center;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
    }
    .metric-value {
        font-size: 28px;
        font-weight: bold;
        color: #0072CE;
    }
    .metric-label {
        font-size: 14px;
        color: #555;
    }
</style>
""", unsafe_allow_html=True)

# --- LOAD DATA ---
@st.cache_data(ttl=300) # Cache for 5 minutes
def load_data():
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame()
    try:
        df = pd.read_csv(FILE_PATH)
        # Convert date to datetime objects
        df['Date Available Online'] = pd.to_datetime(df['Date Available Online'], errors='coerce')
        return df
    except Exception as e:
        st.error(f"Error loading data: {e}")
        return pd.DataFrame()

df = load_data()

# --- SIDEBAR FILTERS ---
with st.sidebar:
    st.header("🔍 Filters")
    
    # 1. TIME WINDOW FILTER
    time_filter = st.radio(
        "Time Window",
        ["± 6 Months (Default)", "Last Month", "Last Week", "All Data"],
        index=0
    )
    
    # 2. TYPE FILTER
    if not df.empty:
        all_types = ["All"] + sorted(list(df['Type'].dropna().unique()))
        selected_type = st.selectbox("Filter by Type", all_types)
    else:
        selected_type = "All"
    
    st.divider()
    st.caption(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# --- FILTER LOGIC ---
if not df.empty:
    filtered_df = df.copy()
    
    # Apply Time Filter
    today = pd.Timestamp.now().normalize()
    
    if time_filter == "Last Week":
        start_date = today - timedelta(days=7)
        filtered_df = filtered_df[filtered_df['Date Available Online'] >= start_date]
        
    elif time_filter == "Last Month":
        start_date = today - timedelta(days=30)
        filtered_df = filtered_df[filtered_df['Date Available Online'] >= start_date]
        
    elif time_filter == "± 6 Months (Default)":
        # The tracker script already filters to ±6 months, 
        # but we enforce it here just in case 'All Data' has older rows.
        start_date = today - timedelta(days=180)
        end_date = today + timedelta(days=180)
        filtered_df = filtered_df[
            (filtered_df['Date Available Online'] >= start_date) & 
            (filtered_df['Date Available Online'] <= end_date)
        ]

    # Apply Type Filter
    if selected_type != "All":
        filtered_df = filtered_df[filtered_df['Type'] == selected_type]
        
    # Sort by Date (Newest first)
    filtered_df = filtered_df.sort_values(by='Date Available Online', ascending=False)

else:
    filtered_df = pd.DataFrame()

# --- MAIN DASHBOARD ---
st.title("🔬 LCDS Research & Media Tracker")
st.markdown("Monitoring research output and media mentions for the **Leverhulme Centre for Demographic Science**.")

if filtered_df.empty:
    st.info("No data found for the selected filters. Run `tracker.py` to fetch new data.")
else:
    # 1. METRICS ROW
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{len(filtered_df)}</div>
            <div class="metric-label">Total Records</div>
        </div>
        """, unsafe_allow_html=True)
        
    with col2:
        media_count = len(filtered_df[filtered_df['Type'] == 'Media Mention'])
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{media_count}</div>
            <div class="metric-label">Media Mentions</div>
        </div>
        """, unsafe_allow_html=True)
        
    with col3:
        pub_count = len(filtered_df[filtered_df['Type'] == 'Publication'])
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{pub_count}</div>
            <div class="metric-label">Publications</div>
        </div>
        """, unsafe_allow_html=True)
        
    with col4:
        recent_count = len(filtered_df[filtered_df['Date Available Online'] >= (today - timedelta(days=7))])
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-value" style="color:green;">+{recent_count}</div>
            <div class="metric-label">New (Last 7 Days)</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # 2. CHARTS ROW
    c1, c2 = st.columns([2, 1])
    
    with c1:
        st.subheader("📈 Activity Over Time")
        # Aggregate by Week
        timeline_df = filtered_df.copy()
        timeline_df['Week'] = timeline_df['Date Available Online'].dt.to_period('W').apply(lambda r: r.start_time)
        daily_counts = timeline_df.groupby(['Week', 'Type']).size().reset_index(name='Count')
        
        fig = px.bar(
            daily_counts, 
            x='Week', 
            y='Count', 
            color='Type',
            color_discrete_map={"Media Mention": "#FF4B4B", "Publication": "#0072CE", "Pub Reference": "#FFA500"},
            title="Weekly Volume by Type"
        )
        st.plotly_chart(fig, use_container_width=True)
        
    with c2:
        st.subheader("🏆 Top Academics")
        if 'Name' in filtered_df.columns:
            top_names = filtered_df['Name'].value_counts().head(5).reset_index()
            top_names.columns = ['Name', 'Count']
            fig2 = px.pie(top_names, values='Count', names='Name', hole=0.4)
            st.plotly_chart(fig2, use_container_width=True)

    # 3. DETAILED DATA TABLE
    st.subheader("📄 Recent Updates")
    
    # Configure columns for clean display
    st.dataframe(
        filtered_df[[
            "Date Available Online", "Type", "Name", "LCDS Mention", "Source", "Link"
        ]],
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="Open 🔗"),
            "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
            "LCDS Mention": st.column_config.TextColumn("Title / Headline", width="large"),
        },
        use_container_width=True,
        hide_index=True
    )
