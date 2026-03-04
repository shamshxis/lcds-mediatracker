# app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import config
import utils

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="LCDS Impact Tracker",
    page_icon="📰",
    layout="wide"
)

# --- HEADER ---
st.title("📰 LCDS Media & Impact Tracker")
st.markdown(f"**Tracking media mentions and talks for {config.CENTER_NAME}**")

# --- SIDEBAR: CONFIGURATION ---
with st.sidebar:
    st.header("⚙️ Tracker Settings")
    
    # 1. Names Input
    st.subheader("1. Academics to Track")
    default_names_str = "\n".join(config.DEFAULT_NAMES)
    names_input = st.text_area("One name per line", default_names_str, height=150)
    names_list = [n.strip() for n in names_input.split('\n') if n.strip()]
    
    # 2. Project Input
    st.subheader("2. Projects/Center Terms")
    st.info("Tracks mentions where no specific author is named.")
    default_projects_str = "\n".join(config.PROJECT_KEYWORDS)
    projects_input = st.text_area("One term per line", default_projects_str, height=100)
    projects_list = [p.strip() for p in projects_input.split('\n') if p.strip()]

    # 3. Context Keywords (The Noise Filter)
    st.subheader("3. Noise Filter Keywords")
    st.caption("Articles must contain at least one of these to be included.")
    context_filter = st.multiselect(
        "Keywords", 
        config.CONTEXT_KEYWORDS, 
        default=config.CONTEXT_KEYWORDS
    )

    run_btn = st.button("🚀 Run Tracker", type="primary")

# --- MAIN APP LOGIC ---

# We use session state to hold data so it doesn't vanish when we switch tabs
if "data" not in st.session_state:
    st.session_state.data = pd.DataFrame()

if run_btn:
    all_records = []
    
    # PROGRESS BAR
    total_steps = len(names_list) + len(projects_list)
    progress_bar = st.progress(0)
    step_count = 0
    status_text = st.empty()

    # 1. TRACK ACADEMICS
    for name in names_list:
        status_text.text(f"Scanning media for: {name}...")
        
        # A. Media Mentions
        news_items = utils.fetch_google_news(name, context_filter)
        all_records.extend(news_items)
        
        # B. Talks (OpenAlex)
        talks_items = utils.fetch_openalex_talks(name)
        all_records.extend(talks_items)
        
        step_count += 1
        progress_bar.progress(step_count / total_steps)

    # 2. TRACK PROJECTS (UNNAMED MENTIONS)
    for project in projects_list:
        status_text.text(f"Scanning media for topic: {project}...")
        
        # Only check news for projects (OpenAlex isn't relevant here)
        project_news = utils.fetch_google_news(project, context_filter)
        all_records.extend(project_news)
        
        step_count += 1
        progress_bar.progress(step_count / total_steps)
    
    progress_bar.empty()
    status_text.success("Scan Complete!")
    
    if all_records:
        st.session_state.data = pd.DataFrame(all_records)
        # Normalize dates
        st.session_state.data['Date'] = pd.to_datetime(st.session_state.data['Date'], utc=True, errors='coerce').dt.date
    else:
        st.warning("No records found matching your criteria.")

# --- DISPLAY RESULTS ---
if not st.session_state.data.empty:
    df = st.session_state.data
    
    # TABS
    tab1, tab2, tab3 = st.tabs(["📊 All Data", "🎤 Add Keynote (Manual)", "📥 Export"])
    
    with tab1:
        st.dataframe(
            df, 
            column_config={
                "Link": st.column_config.LinkColumn("Link"),
                "Date": st.column_config.DateColumn("Date"),
            },
            use_container_width=True
        )
        
        # Quick Stats
        col1, col2 = st.columns(2)
        col1.metric("Total Mentions", len(df))
        col2.metric("Unique Sources", df['Source'].nunique())

    with tab2:
        st.write("### Manually Add a Keynote / Talk")
        st.caption("Since keynotes often lack DOIs/Online links, add them here to include in the CSV export.")
        
        with st.form("manual_entry"):
            c1, c2 = st.columns(2)
            m_author = c1.selectbox("Academic", names_list)
            m_date = c2.date_input("Date")
            m_title = st.text_input("Title of Keynote/Talk")
            m_source = st.text_input("Venue / Conference Name")
            m_link = st.text_input("Link (optional)")
            
            submitted = st.form_submit_button("Add to Dataset")
            
            if submitted:
                new_row = {
                    "Date": m_date,
                    "Entity/Author": m_author,
                    "Title": m_title,
                    "Source": m_source,
                    "Link": m_link,
                    "Type": "Keynote (Manual)",
                    "Snippet": "Manually entered"
                }
                st.session_state.data = pd.concat([pd.DataFrame([new_row]), st.session_state.data], ignore_index=True)
                st.rerun()

    with tab3:
        st.write("### Download Data")
        csv = st.session_state.data.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download as CSV",
            data=csv,
            file_name=f"lcds_impact_report_{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
        )
