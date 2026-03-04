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

# --- SIDEBAR: SETTINGS ---
with st.sidebar:
    st.header("⚙️ Tracker Settings")
    
    # 1. Load People Database (Auto-load)
    try:
        # We use 'latin1' to avoid UnicodeDecodeError from Excel-saved CSVs
        df_people = pd.read_csv("lcds_people_orcid_updated.csv", encoding='latin1')
        st.success(f"✅ Loaded {len(df_people)} people.")
    except FileNotFoundError:
        st.error("❌ 'lcds_people_orcid_updated.csv' not found. Please save it in the app folder.")
        df_people = pd.DataFrame()
    except Exception as e:
        st.error(f"❌ Error loading CSV: {e}")
        df_people = pd.DataFrame()

    if not df_people.empty:
        # Filter Logic (Remove 'Ignore' status)
        all_statuses = [s for s in df_people['Status'].unique() if isinstance(s, str)]
        if 'Ignore' in all_statuses: all_statuses.remove('Ignore')
        
        # Default: Select all valid statuses except Alumni (to save time)
        default_selection = [s for s in all_statuses if "Alumni" not in s]
        
        selected_statuses = st.multiselect(
            "Select Status to Track", 
            all_statuses, 
            default=default_selection
        )
        
        # Filter the dataframe
        track_list = df_people[df_people['Status'].isin(selected_statuses)].copy()
        st.info(f"Tracking {len(track_list)} academics.")
    
    st.markdown("---")
    st.header("Search Keywords")
    context_keywords = st.text_area("Context Filter", ", ".join(config.CONTEXT_KEYWORDS)).split(",")
    context_keywords = [k.strip() for k in context_keywords if k.strip()]

# --- MAIN APP ---
st.title("📰 LCDS Media & Impact Tracker")

if st.button("🚀 Run Tracker"):
    if df_people.empty:
        st.error("Please ensure the CSV file is in the folder.")
        st.stop()
        
    all_results = []
    
    # Progress Bar
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_people = len(track_list)
    
    for idx, row in track_list.iterrows():
        name = row['Name']
        orcid = row['ORCID']
        
        # UPDATE PROGRESS
        status_text.text(f"Scanning: {name}...")
        progress_bar.progress((idx + 1) / total_people)
        
        # 1. SEARCH MEDIA FOR NAME (With Strict Context Filter)
        name_news = utils.fetch_google_news(name, context_keywords, strict_filter=True)
        # Ensure we tag the Name correctly
        for item in name_news: item['Name'] = name
        all_results.extend(name_news)
        
        # 2. GET RECENT WORK VIA ORCID
        works, paper_titles = utils.fetch_openalex_works(orcid, name)
        all_results.extend(works)
        
        # 3. SEARCH MEDIA FOR PAPER TITLES (The "Unnamed" Finder)
        if paper_titles:
            for title in paper_titles[:2]: # Limit to top 2 recent papers
                # Search for the paper title in news (no context filter needed)
                title_news = utils.fetch_google_news(f'"{title}"', strict_filter=False) 
                
                for item in title_news:
                    item['Name'] = name # Link back to author
                    item['LCDS Mention'] = f"Media on Paper: {title}" # Custom label
                    item['Type'] = "Media (via Paper)"
                
                all_results.extend(title_news)

    progress_bar.empty()
    status_text.success("Scan Complete!")
    
    # --- DISPLAY RESULTS ---
    if all_results:
        # Create DataFrame
        df_results = pd.DataFrame(all_results)
        
        # --- FORMATTING FOR USER REQUEST ---
        # Requested Columns: LCDS Mention (Name) (Link) (Year) (Date Available Online)
        
        # 1. Date Available Online
        df_results['Date Available Online'] = pd.to_datetime(df_results['Date'], utc=True, errors='coerce').dt.date
        
        # 2. Year
        df_results['Year'] = pd.to_datetime(df_results['Date'], utc=True, errors='coerce').dt.year
        
        # 3. LCDS Mention (Map 'Title' to this column if not already set)
        if 'LCDS Mention' not in df_results.columns:
            df_results['LCDS Mention'] = df_results['Title']
        else:
            df_results['LCDS Mention'] = df_results['LCDS Mention'].fillna(df_results['Title'])

        # 4. Final Selection & Ordering
        final_columns = ['LCDS Mention', 'Name', 'Link', 'Year', 'Date Available Online', 'Type', 'Source']
        # Ensure all columns exist
        for col in final_columns:
            if col not in df_results.columns:
                df_results[col] = ""
                
        df_final = df_results[final_columns]
        
        # TABS
        tab1, tab2 = st.tabs(["📊 Report View", "📥 Export"])
        
        with tab1:
            st.dataframe(
                df_final,
                column_config={
                    "Link": st.column_config.LinkColumn("Link"),
                    "Date Available Online": st.column_config.DateColumn("Date Available Online"),
                    "Year": st.column_config.NumberColumn("Year", format="%d")
                },
                use_container_width=True
            )
            
        with tab2:
            st.write("### Download Data")
            csv = df_final.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name=f"lcds_media_tracker_{datetime.now().strftime('%Y-%m-%d')}.csv",
                mime="text/csv",
            )
        
    else:
        st.warning("No results found. Try adjusting keywords or checking internet connection.")

else:
    st.info("Click 'Run Tracker' to start scanning.")
