import streamlit as st
import pandas as pd
import os
import plotly.express as px
from datetime import datetime, timedelta

# --- CONFIGURATION ---
FILE_PATH = "lcds_media_tracker.csv"

# 1. PAGE SETUP (Layout & Dark Mode Friendly)
st.set_page_config(
    page_title="LCDS Impact Dashboard", 
    page_icon="📰", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CUSTOM CSS FOR FOOTER & STYLING ---
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
        padding: 10px;
        font-size: 12px;
        border-top: 1px solid #ddd;
        z-index: 1000;
    }
    .footer a {
        color: #0072CE;
        text-decoration: none;
    }
    /* Hide the default Streamlit footer */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Better metric styling */
    div[data-testid="stMetricValue"] {
        font-size: 24px;
    }
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
        
        # Safety: Ensure columns exist
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
st.markdown("Monitor the global reach of Leverhulme Centre academics across News, Policy, and Conferences.")

if df.empty:
    st.info("System initializing... Data will appear after the first background run.")
    st.stop()

# 4. SIDEBAR CONTROLS
with st.sidebar:
    st.header("🔍 Intelligence Filters")
    
    # A. Time Filter
    time_option = st.radio(
        "Time Range",
        ["Last Week", "Last Month", "Last Year", "All Time (Sep 2019+)"],
        index=1
    )
    
    # Time Logic
    today = pd.Timestamp.now()
    if time_option == "Last Week":
        start_date = today - timedelta(days=7)
    elif time_option == "Last Month":
        start_date = today - timedelta(days=30)
    elif time_option == "Last Year":
        start_date = today - timedelta(days=365)
    else:
        start_date = pd.Timestamp("2019-09-01")

    # B. Filter Data
    df_filtered = df[df['Date Available Online'] >= start_date].copy()
    
    st.divider()
    
    # C. Category Filters
    selected_types = st.multiselect("Content Type", sorted(df_filtered['Type'].unique()), default=None)
    selected_names = st.multiselect("Academic / Project", sorted(df_filtered['Name'].unique()), default=None)
    
    st.divider()
    
    # D. Download Button (Sidebar)
    csv = df_filtered.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Current View (CSV)",
        data=csv,
        file_name=f"lcds_impact_{time_option.replace(' ', '_').lower()}.csv",
        mime="text/csv"
    )

# 5. GLOBAL SEARCH & APPLY FILTERS
# Search Box (Top of main page)
search_query = st.text_input("🔎 Search Keywords (e.g., 'Pension', 'Fertility', 'Covid')", "")

if selected_types:
    df_filtered = df_filtered[df_filtered['Type'].isin(selected_types)]
if selected_names:
    df_filtered = df_filtered[df_filtered['Name'].isin(selected_names)]
if search_query:
    # Search in Title, Summary, or Snippet
    mask = (
        df_filtered['LCDS Mention'].str.contains(search_query, case=False, na=False) |
        df_filtered['Snippet'].str.contains(search_query, case=False, na=False)
    )
    df_filtered = df_filtered[mask]

# 6. KEY METRICS
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Mentions", len(df_filtered))
c2.metric("Unique Academics", df_filtered['Name'].nunique())
c3.metric("Keynotes / Talks", len(df_filtered[df_filtered['Type'].str.contains("Keynote|Talk", na=False)]))
c4.metric("Sources", df_filtered['Source'].nunique())

st.divider()

# 7. DATA TABLE (The Core View)
st.subheader(f"Latest Mentions ({time_option})")

st.dataframe(
    df_filtered[[
        "Type", "Name", "LCDS Mention", "Snippet", "Link", "Date Available Online", "Source"
    ]],
    column_config={
        "Link": st.column_config.LinkColumn("Action", display_text="View Here"),
        "Snippet": st.column_config.TextColumn("Context", width="large", help="Snippet found by tracker"),
        "LCDS Mention": st.column_config.TextColumn("Title", width="medium"),
        "Date Available Online": st.column_config.DateColumn("Date", format="DD MMM YYYY"),
        "Type": st.column_config.TextColumn("Category", width="small"),
    },
    use_container_width=True,
    hide_index=True
)

st.divider()

# 8. ANALYTICS SECTION (The "Doughnut" Request)
st.subheader("📊 Impact Analytics")

col1, col2 = st.columns(2)

with col1:
    st.markdown("#### Share of Voice (Top Academics)")
    # Prepare Data for Doughnut
    top_voices = df_filtered['Name'].value_counts().reset_index()
    top_voices.columns = ['Academic', 'Mentions']
    
    # If too many, group small ones into "Others" for cleaner chart
    if len(top_voices) > 10:
        others_count = top_voices.iloc[10:]['Mentions'].sum()
        top_voices = top_voices.iloc[:10]
        # Use pandas.concat instead of append
        new_row = pd.DataFrame({'Academic': ['Others'], 'Mentions': [others_count]})
        top_voices = pd.concat([top_voices, new_row], ignore_index=True)

    fig_donut = px.pie(
        top_voices, 
        values='Mentions', 
        names='Academic', 
        hole=0.4, # Makes it a Doughnut
        color_discrete_sequence=px.colors.qualitative.Prism
    )
    fig_donut.update_traces(textposition='inside', textinfo='percent+label')
    fig_donut.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
    st.plotly_chart(fig_donut, use_container_width=True)

with col2:
    st.markdown("#### Mention Sources")
    # Bar Chart for Source/Type
    source_counts = df_filtered['Type'].value_counts().reset_index()
    source_counts.columns = ['Type', 'Count']
    
    fig_bar = px.bar(
        source_counts, 
        x='Count', 
        y='Type', 
        orientation='h',
        color='Count',
        color_continuous_scale='Bluyl'
    )
    fig_bar.update_layout(yaxis={'categoryorder':'total ascending'}, showlegend=False)
    st.plotly_chart(fig_bar, use_container_width=True)

# 9. FOOTER
st.markdown("""
<div class="footer">
    © University of Oxford 2026. All rights reserved. 
    <a href="https://www.demography.ox.ac.uk" target="_blank">demography.ox.ac.uk</a>
</div>
""", unsafe_allow_html=True)
