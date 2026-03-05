import pandas as pd
import feedparser
import requests
import urllib.parse
import time
import logging
import sys
import re
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# --- IMPORT DDGS SAFELY ---
try:
    from ddgs import DDGS 
except ImportError:
    # Fallback in case of package mismatch
    try:
        from duckduckgo_search import DDGS
    except:
        DDGS = None

# --- CONFIGURATION ---
INPUT_ORCID_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
USER_AGENT = "LCDS_Impact_Tracker/4.0 (mailto:admin@lcds.ox.ac.uk)"

# DOMAIN ALLOWLIST FOR GDELT (To reduce noise)
# We accept these domains OR anything ending in .ac.uk / .edu
TRUSTED_DOMAINS = [
    "bbc.co.uk", "bbc.com", "ft.com", "theguardian.com", "telegraph.co.uk",
    "timeshighereducation.com", "economist.com", "reuters.com", "bloomberg.com",
    "ox.ac.uk", "nuffield.ox.ac.uk", "demography.ox.ac.uk", "science.org", "nature.com"
]

# GDELT QUERY LOGIC
# "Keyword1" near10 "Keyword2"
GDELT_QUERIES = [
    '"Leverhulme Centre" near10 "Demographic"',
    '"LCDS" near10 "Oxford"',
    '"Oxford University" near10 "Demography"',
    '"Melinda Mills" near10 "Oxford"',
    '"Jennifer Dowd" near10 "Oxford"'
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- HELPERS ---

def get_date_window():
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

def verify_affiliation(text, name=""):
    """
    Checks if text contains LCDS context. 
    If 'name' is provided, ensures name is also present.
    """
    text_lower = text.lower()
    
    # Context Keywords
    affiliations = ["oxford", "leverhulme", "lcds", "nuffield", "demographic", "population", "sociology"]
    
    # 1. If name is provided, it MUST be there
    if name and name.lower() not in text_lower:
        return False
        
    # 2. Check for at least one affiliation context
    has_aff = any(a in text_lower for a in affiliations)
    
    # 3. Special case for Melinda Mills (Groningen)
    if name and "melinda mills" in name.lower() and "groningen" in text_lower:
        has_aff = True

    return has_aff

def load_orcid_file(filepath):
    # Try multiple encodings
    for enc in ['utf-8', 'latin1', 'cp1252']:
        try:
            return pd.read_csv(filepath, encoding=enc)
        except: continue
    return pd.DataFrame() # Return empty if fails

# --- DATA FETCHERS ---

def fetch_crossref_titles(orcid):
    """Fetches titles for SEARCH SEED only."""
    if not orcid or str(orcid) == "nan": return []
    s_date, e_date = get_date_window()
    url = f"https://api.crossref.org/works?filter=orcid:{orcid},from-pub-date:{s_date.strftime('%Y-%m-%d')},until-pub-date:{e_date.strftime('%Y-%m-%d')}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if r.status_code == 200:
            items = r.json().get('message', {}).get('items', [])
            return [clean_html(i.get('title', [''])[0]) for i in items if len(i.get('title', [''])[0]) > 20]
    except: pass
    return []

def search_google_rss(query, mode="Name", academic_name=None):
    """Standard RSS Search"""
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    hits = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title = clean_html(entry.title)
            summary = clean_html(getattr(entry, 'summary', ''))
            full_text = f"{title} {summary}"
            
            valid = False
            if mode == "Name":
                if verify_affiliation(full_text, academic_name): valid = True
            else: 
                valid = True # Trust Pub title matches
            
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

def search_deep_events_safe(name):
    """
    DDGS Search with Error Handling (Fixes Wikipedia Crash).
    """
    if DDGS is None: return []
    
    hits = []
    query = f'"{name}" (keynote OR plenary OR award OR prize) demography'
    
    try:
        with DDGS() as ddgs:
            # We catch specific errors inside the generator loop if possible, 
            # but DDGS often throws them at the start.
            results = list(ddgs.text(query, region='wt-wt', safesearch='off', timelimit='y', max_results=5))
            
            for r in results:
                title = r.get('title', '')
                link = r.get('href', '')
                snippet = r.get('body', '')
                
                if verify_affiliation(title + " " + snippet, name):
                    hits.append({
                        "LCDS Mention": title,
                        "Link": link,
                        "Date Available Online": datetime.now().strftime('%Y-%m-%d'),
                        "Type": "Talk/Award",
                        "Source": "Web Search",
                        "Name": name,
                        "Snippet": snippet[:300]
                    })
                time.sleep(1)
    except Exception as e:
        # ⚠️ This is where we catch the "Wikipedia" / DNS error silently
        logging.warning(f"Skipping deep search for {name} due to connection error: {e}")
        
    return hits

def fetch_gdelt_impact():
    """
    GDELT 2.0 Doc API - Captures Global News & Transcripts.
    Filters: near10 context + Trusted Domains.
    """
    print("  Running GDELT Global Scan...")
    hits = []
    base_url = "https://api.gdeltproject.org/api/v2/doc/doc"
    
    for q in GDELT_QUERIES:
        params = {
            "query": q,
            "mode": "artlist",
            "maxrecords": "50",
            "timespan": "6m",
            "format": "json"
        }
        try:
            r = requests.get(base_url, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                for article in data.get('articles', []):
                    title = article.get('title', '')
                    url = article.get('url', '')
                    domain = article.get('domain', '').lower()
                    
                    # DOMAIN FILTER
                    is_trusted = any(d in domain for d in TRUSTED_DOMAINS) or domain.endswith(".edu") or domain.endswith(".ac.uk")
                    
                    if is_trusted:
                        # Parse GDELT Date (YYYYMMDDTHHMMSS)
                        raw_date = article.get('seendate', '')
                        fmt_date = datetime.strptime(raw_date, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d") if raw_date else None
                        
                        hits.append({
                            "LCDS Mention": title,
                            "Link": url,
                            "Date Available Online": fmt_date,
                            "Type": "Global News/Transcript",
                            "Source": article.get('sourcegeography', 'GDELT'),
                            "Name": "LCDS General",
                            "Snippet": f"Sourced via GDELT (Domain: {domain})"
                        })
            time.sleep(1)
        except Exception as e:
            logging.error(f"GDELT Error for {q}: {e}")
            
    return hits

# --- MAIN ---

def main():
    print("--- LCDS Tracker v4.0 (GDELT + SafeMode) ---")
    
    df_orcid = load_orcid_file(INPUT_ORCID_FILE)
    if 'Name' not in df_orcid.columns: 
        print("Error: Name column missing in ORCID file.")
        return

    # Load Existing
    try:
        df_old = pd.read_csv(OUTPUT_FILE)
        existing_links = set(df_old['Link'].astype(str))
        all_data = df_old.to_dict('records')
        print(f"Loaded {len(df_old)} existing records.")
    except:
        existing_links = set()
        all_data = []

    # 1. PROCESS PEOPLE
    for _, row in df_orcid.iterrows():
        name = row['Name']
        orcid_col = next((c for c in df_orcid.columns if c.lower() == 'orcid'), None)
        orcid = row[orcid_col] if orcid_col else None
        
        print(f"Scanning: {name}")

        # A. Crossref Seed -> News
        titles = fetch_crossref_titles(orcid)
        for t in titles:
            hits = search_google_rss(f'"{t}"', mode="Pub", academic_name=name)
            for h in hits:
                if h['Link'] not in existing_links:
                    all_data.append(h)
                    existing_links.add(h['Link'])
            time.sleep(0.5)

        # B. Direct Name Search
        hits = search_google_rss(f'"{name}"', mode="Name", academic_name=name)
        for h in hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])

        # C. Deep Events (Protected)
        hits = search_deep_events_safe(name)
        for h in hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])
        
        time.sleep(1)

    # 2. PROCESS GDELT (GLOBAL LAYER)
    gdelt_hits = fetch_gdelt_impact()
    for h in gdelt_hits:
        if h['Link'] not in existing_links:
            all_data.append(h)
            existing_links.add(h['Link'])

    # 3. SAVE
    df_final = pd.DataFrame(all_data)
    if not df_final.empty:
        # Convert date safely
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        s_date, e_date = get_date_window()
        
        # Filter Window
        df_final = df_final[
            (df_final['Date Available Online'] >= s_date) & 
            (df_final['Date Available Online'] <= e_date)
        ]
        
        df_final.sort_values('Date Available Online', ascending=False, inplace=True)
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Done. Saved {len(df_final)} records.")

if __name__ == "__main__":
    main()
