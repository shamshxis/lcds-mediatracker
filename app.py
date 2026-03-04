# app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import config
import utils

st.set_page_config(page_title="LCDS Impact Tracker", layout="wide")

# --- SIDEBAR: DATA LOADING ---
with st.sidebar:
    st.header("1. People Database")
    
    # Allow user to upload a newer version, otherwise use default
    uploaded_file = st.file_uploader("Upload People CSV", type=["csv"])
    
    if uploaded_file:
        df_people = pd.read_csv(uploaded_file)
    else:
        # Load the local file you uploaded
        try:
            df_people = pd.read_csv("lcds_people_orcid_updated.csv")
        except FileNotFoundError:
            st.error("Default CSV not found. Please upload one.")
            df_people = pd.DataFrame()

    if not df_people.empty:
        # Filter Logic
        all_statuses = df_people['Status'].unique().tolist()
        if 'Ignore' in all_statuses: all_statuses.remove('Ignore')
        
        selected_statuses = st.multiselect(
            "Select Status to Track", 
            all_statuses, 
            default=[s for s in all_statuses if "Alumni" not in s] # Exclude Alumni by default
        )
        
        # Filter the dataframe
        track_list = df_people[df_people['Status'].isin(selected_statuses)].copy()
        
        st.write(f"Tracking **{len(track_list)}** people.")
    
    st.markdown("---")
    st.header("2. Search Settings")
    context_keywords = st.text_area("Context Keywords (Comma Separated)", ", ".join(config.CONTEXT_KEYWORDS)).split(",")
    context_keywords = [k.strip() for k in context_keywords if k.strip()]

# --- MAIN APP ---
st.title("📰 LCDS Media & Impact Tracker")

if st.button("🚀 Run Tracker") and not df_people.empty:
    
    all_results = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_people = len(track_list)
    
    for idx, row in track_list.iterrows():
        name = row['Name']
        orcid = row['ORCID']
        
        # UPDATE PROGRESS
        status_text.text(f"Scanning: {name}...")
        progress_bar.progress((idx + 1) / total_people)
        
        # 1. SEARCH MEDIA FOR NAME (With Context Filter)
        # We enforce strict filtering for names to avoid "John Smith" noise
        name_news = utils.fetch_google_news(name, context_keywords, strict_filter=True)
        all_results.extend(name_news)
        
        # 2. GET RECENT WORK VIA ORCID
        works, paper_titles = utils.fetch_openalex_works(orcid, name)
        all_results.extend(works)
        
        # 3. SEARCH MEDIA FOR PAPER TITLES (The "Unnamed" Finder)
        # If we found recent papers, search news for their titles
        if paper_titles:
            for title in paper_titles[:2]: # Limit to top 2 recent papers to save time
                # We do NOT use strict context filter here because the paper title IS the context
                title_news = utils.fetch_google_news(f'"{title}"', strict_filter=False) 
                
                # Tag these results specifically
                for item in title_news:
                    item['Query'] = f"Paper: {title[:30]}..."
                    item['Entity/Author'] = name # Link back to author
                    item['Type'] = "Media Mention (via Paper Title)"
                
                all_results.extend(title_news)

    progress_bar.empty()
    status_text.success("Scan Complete!")
    
    # --- DISPLAY RESULTS ---
    if all_results:
        df_results = pd.DataFrame(all_results)
        
        # Clean Dates
        df_results['Date'] = pd.to_datetime(df_results['Date'], utc=True, errors='coerce').dt.date
        
        # TABS
        tab1, tab2, tab3 = st.tabs(["All Mentions", "Media Only", "Academic Output"])
        
        with tab1:
            st.dataframe(df_results, use_container_width=True)
            
        with tab2:
            media_df = df_results[df_results['Type'].str.contains("Media")]
            st.dataframe(media_df, use_container_width=True)
            
        with tab3:
            academic_df = df_results[df_results['Type'] == "Academic Output"]
            st.dataframe(academic_df, use_container_width=True)
            
        # Download
        csv = df_results.to_csv(index=False).encode('utf-8')
        st.download_button("Download CSV", csv, "lcds_impact_data.csv", "text/csv")
        
    else:
        st.warning("No results found.")

elif df_people.empty:
    st.info("Please upload a CSV file to begin.")
