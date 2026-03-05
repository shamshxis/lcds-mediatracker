import pandas as pd
import feedparser
import requests
import urllib.parse
import time
import logging
import sys
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# --- UPDATED IMPORT ---
try:
    from ddgs import DDGS 
except ImportError:
    # Fallback if user installed the old package by mistake
    from duckduckgo_search import DDGS

# ... rest of the script remains exactly the same ...

# --- CONFIGURATION ---
INPUT_ORCID_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
USER_AGENT = "LCDS_Impact_Tracker/3.0 (mailto:admin@lcds.ox.ac.uk)"

# Keywords for High-Prestige Events
EVENT_KEYWORDS = ["keynote", "plenary", "distinguished lecture", "award", "prize", "medal", "fellowship", "honorary"]

# Affiliations for Verification
AFFILIATIONS = [
    "university of oxford", "oxford university", "leverhulme", "lcds", 
    "nuffield college", "sociology", "demographic", "population"
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- HELPERS ---

def get_date_window():
    """±6 Months Window"""
    today = datetime.now()
    return today - timedelta(days=180), today + timedelta(days=180)

def normalize_date(date_obj):
    if not date_obj: return None
    try:
        return pd.to_datetime(date_obj).strftime('%Y-%m-%d')
    except:
        return None

def clean_html(text):
    if not text: return ""
    try:
        return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()
    except:
        return str(text)

def verify_affiliation(text, name):
    """Checks if text contains Name + (Affiliation OR Event Keyword)."""
    text_lower = text.lower()
    if name.lower() not in text_lower:
        return False
    
    # Check for affiliation OR a specific event type (like 'Keynote')
    has_aff = any(a in text_lower for a in AFFILIATIONS)
    has_event = any(e in text_lower for e in EVENT_KEYWORDS)
    
    # For Melinda Mills, check Groningen specifically
    if "melinda mills" in name.lower() and ("groningen" in text_lower):
        has_aff = True

    return has_aff or has_event

def load_orcid_file(filepath):
    try:
        return pd.read_csv(filepath) # Assuming UTF-8 standard now
    except:
        try:
            return pd.read_csv(filepath, encoding='latin1')
        except Exception as e:
            logging.error(f"Failed to load CSV: {e}")
            sys.exit(1)

# --- DATA FETCHERS ---

def fetch_crossref_titles(orcid):
    """Fetches titles for SEARCH only. Does not return full records."""
    if not orcid or str(orcid) == "nan": return []
    
    s_date, e_date = get_date_window()
    url = f"https://api.crossref.org/works?filter=orcid:{orcid},from-pub-date:{s_date.strftime('%Y-%m-%d')},until-pub-date:{e_date.strftime('%Y-%m-%d')}"
    
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if r.status_code == 200:
            items = r.json().get('message', {}).get('items', [])
            # Return only titles longer than 20 chars
            return [clean_html(i.get('title', [''])[0]) for i in items if len(i.get('title', [''])[0]) > 20]
    except:
        pass
    return []

def search_google_rss(query, mode="Name", academic_name=None):
    """Fast News Search (RSS)"""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    hits = []
    
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = clean_html(entry.title)
            summary = clean_html(getattr(entry, 'summary', ''))
            full_text = f"{title} {summary}"
            
            # Logic: If searching by Name, verify context. If searching by Pub Title, trust the title match.
            valid = False
            if mode == "Name":
                if verify_affiliation(full_text, academic_name): valid = True
            else: # Mode == Pub
                valid = True 

            if valid:
                hits.append({
                    "LCDS Mention": title,
                    "Link": entry.link,
                    "Date Available Online": normalize_date(entry.published),
                    "Type": "Media Mention",
                    "Source": entry.source.get('title', 'Google News'),
                    "Name": academic_name,
                    "Snippet": summary[:300]
                })
    except Exception as e:
        logging.error(f"RSS Error: {e}")
        
    return hits

def search_deep_events(name):
    """
    DuckDuckGo Search for 'Keynotes', 'Awards', and 'Talks'.
    This finds static pages (conference agendas, university news) that RSS misses.
    """
    hits = []
    # Query: "Name" + (Keynote OR Award OR Prize) + Demography
    query = f'"{name}" (keynote OR plenary OR award OR prize) demography'
    
    try:
        # DDGS can be sensitive, so we use it gently
        with DDGS() as ddgs:
            results = list(ddgs.text(query, region='wt-wt', safesearch='off', timelimit='y', max_results=5))
            
            for r in results:
                title = r.get('title', '')
                link = r.get('href', '')
                snippet = r.get('body', '')
                
                # Verify it's actually about them
                if verify_affiliation(title + " " + snippet, name):
                    hits.append({
                        "LCDS Mention": title,
                        "Link": link,
                        "Date Available Online": datetime.now().strftime('%Y-%m-%d'), # DDG doesn't give precise dates, assume current/recent
                        "Type": "Talk/Award",
                        "Source": "Web Search",
                        "Name": name,
                        "Snippet": snippet[:300]
                    })
                time.sleep(1) # Be polite
    except Exception as e:
        logging.warning(f"Deep Search failed for {name}: {e}")
        
    return hits

# --- MAIN ---

def main():
    print("--- Starting LCDS Event & Media Tracker v3.0 ---")
    
    df_orcid = load_orcid_file(INPUT_ORCID_FILE)
    if 'Name' not in df_orcid.columns: return

    # Load existing to avoid dupes
    try:
        df_old = pd.read_csv(OUTPUT_FILE)
        existing_links = set(df_old['Link'].astype(str))
        all_data = df_old.to_dict('records')
    except:
        existing_links = set()
        all_data = []

    for _, row in df_orcid.iterrows():
        name = row['Name']
        orcid_col = next((c for c in df_orcid.columns if c.lower() == 'orcid'), None)
        orcid = row[orcid_col] if orcid_col else None
        
        print(f"Scanning: {name}...")

        # 1. CROSSREF (HIDDEN SEED)
        # We fetch titles, but do NOT add them to 'all_data' directly.
        pub_titles = fetch_crossref_titles(orcid)
        
        # 2. TRACK PUBLICATIONS IN MEDIA
        for title in pub_titles:
            # Search Google News for the paper title
            news_hits = search_google_rss(f'"{title}"', mode="Pub", academic_name=name)
            for h in news_hits:
                if h['Link'] not in existing_links:
                    h['Type'] = 'Research Coverage' # Specific tag for paper mentions
                    all_data.append(h)
                    existing_links.add(h['Link'])
            time.sleep(0.5)

        # 3. TRACK PERSON (MEDIA)
        name_hits = search_google_rss(f'"{name}"', mode="Name", academic_name=name)
        for h in name_hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])

        # 4. TRACK EVENTS (TALKS/AWARDS - DEEP SEARCH)
        event_hits = search_deep_events(name)
        for h in event_hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])
        
        time.sleep(2) # Polite delay between people

    # SAVE
    df_final = pd.DataFrame(all_data)
    if not df_final.empty:
        # Date Filter (Keep ±6 Months)
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        s_date, e_date = get_date_window()
        df_final = df_final[
            (df_final['Date Available Online'] >= s_date) & 
            (df_final['Date Available Online'] <= e_date)
        ]
        
        df_final.sort_values('Date Available Online', ascending=False, inplace=True)
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Saved {len(df_final)} records.")

if __name__ == "__main__":
    main()
