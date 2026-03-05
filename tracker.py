import pandas as pd
import feedparser
import requests
import urllib.parse
import time
import logging
import re
import os
import json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dateutil import parser as date_parser

# --- CONFIGURATION ---
INPUT_ORCID_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
MEMORY_FILE = "source_memory.json"
USER_AGENT = "LCDS_Impact_Tracker/10.0 (mailto:admin@lcds.ox.ac.uk)"

# 1. BLOCKLIST: ACADEMIC JOURNALS + STATIC PROFILES + SOCIAL MEDIA
# We want news ABOUT papers, not the papers themselves.
URL_BLOCKLIST = [
    "nature.com", "science.org", "sciencedirect.com", "wiley.com", 
    "springer.com", "tandfonline.com", "sagepub.com", "frontiersin.org",
    "plos.org", "mdpi.com", "academic.oup.com", "cambridge.org", 
    "jstor.org", "ncbi.nlm.nih.gov", "researchgate.net", "academia.edu",
    "/people/", "/staff/", "/profile/", "/biography/", "/cv", "/contact",
    "linkedin.com", "facebook.com", "twitter.com", "instagram.com"
]

TITLE_BLOCKLIST = [
    "profile", "biography", "curriculum vitae", "cv", "staff", "people", 
    "contact", "about us", "faculty", "home page", "department of"
]

# 2. ALLOWLIST (Preferred Media for GDELT/Scoring)
TRUSTED_MEDIA = [
    "bbc.co.uk", "bbc.com", "ft.com", "theguardian.com", "telegraph.co.uk",
    "timeshighereducation.com", "economist.com", "reuters.com", "bloomberg.com",
    "ox.ac.uk", "nuffield.ox.ac.uk", "demography.ox.ac.uk",
    "washingtonpost.com", "nytimes.com", "forbes.com", "weforum.org", 
    "abc.net.au", "theconversation.com", "medium.com", "substack.com",
    "newscientist.com", "phys.org", "eurekalert.org"
]

# GDELT QUERIES
GDELT_QUERIES = [
    '"Leverhulme Centre" near10 "Demographic"',
    '"LCDS" near10 "Oxford"',
    '"Oxford University" near10 "Demography"',
    '"Melinda Mills" near10 "Oxford"',
    '"Jennifer Dowd" near10 "Oxford"'
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- MEMORY SYSTEM ---
def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                return json.load(f)
        except: return {"trusted_sources": []}
    return {"trusted_sources": []}

def save_memory(memory):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f)

def update_memory(source_domain, memory):
    if source_domain not in memory["trusted_sources"]:
        memory["trusted_sources"].append(source_domain)
        save_memory(memory)
        return True 
    return False

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

def is_blocked_content(link, title, name):
    link = link.lower()
    title = title.lower()
    name = name.lower() if name else ""
    
    if any(blocked in link for blocked in URL_BLOCKLIST): return True
    if any(blocked in title for blocked in TITLE_BLOCKLIST): return True
    
    # Block if title is JUST the name (e.g. "Melinda Mills")
    if name:
        clean_title = re.sub(r'\s*[|\-–].*', '', title).strip()
        if clean_title == name: return True
        
    return False

def classify_entry(title, snippet, default_type):
    full_text = (str(title) + " " + str(snippet)).lower()
    if "podcast" in full_text or "episode" in full_text: return "Podcast"
    if "radio" in full_text or "bbc radio" in full_text or " fm " in full_text: return "Radio"
    if "blog" in full_text or "substack" in full_text or "opinion" in full_text: return "Blog/Opinion"
    if "keynote" in full_text or "plenary" in full_text: return "Keynote"
    if "award" in full_text or "prize" in full_text or "medal" in full_text: return "Award"
    if "forum" in full_text or "discussion" in full_text: return "Forum/Discussion"
    return default_type

def verify_affiliation(text, name=""):
    text_lower = text.lower()
    affiliations = ["oxford", "leverhulme", "lcds", "nuffield", "demographic", "population", "sociology"]
    
    if name and name.lower() not in text_lower:
        return False
        
    has_aff = any(a in text_lower for a in affiliations)
    if name and "melinda mills" in name.lower() and "groningen" in text_lower:
        has_aff = True

    return has_aff

def load_orcid_file(filepath):
    for enc in ['utf-8', 'latin1', 'cp1252']:
        try:
            return pd.read_csv(filepath, encoding=enc)
        except: continue
    return pd.DataFrame() 

# --- DATA FETCHERS ---

def fetch_crossref_titles(orcid):
    """Fetches titles for SEARCH only."""
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

def search_google_rss(query, mode="Name", academic_name=None, default_type="Media Mention", memory=None):
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    hits = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            link = entry.link
            title = clean_html(entry.title)
            
            if is_blocked_content(link, title, academic_name): 
                continue
            
            summary = clean_html(getattr(entry, 'summary', ''))
            full_text = f"{title} {summary}"
            
            valid = False
            if mode == "Name":
                if verify_affiliation(full_text, academic_name): valid = True
            else: 
                valid = True 
            
            if valid:
                # LEARN SOURCE
                domain = urllib.parse.urlparse(link).netloc
                if memory is not None: update_memory(domain, memory)

                category = classify_entry(title, summary, default_type)
                hits.append({
                    "LCDS Mention": title,
                    "Link": link,
                    "Date Available Online": normalize_date(entry.published),
                    "Type": category,
                    "Source": entry.source.get('title', 'Google News'),
                    "Name": academic_name,
                    "Snippet": summary[:400]
                })
    except: pass
    return hits

def fetch_gdelt_impact(memory=None):
    print("  Running GDELT Global Scan...")
    hits = []
    base_url = "https://api.gdeltproject.org/api/v2/doc/doc"
    for q in GDELT_QUERIES:
        params = {"query": q, "mode": "artlist", "maxrecords": "30", "timespan": "6m", "format": "json"}
        try:
            r = requests.get(base_url, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                for article in data.get('articles', []):
                    url = article.get('url', '')
                    title = article.get('title', '')
                    
                    if is_blocked_content(url, title, "LCDS"): continue
                    
                    domain = article.get('domain', '').lower()
                    
                    # Check Trust (Allowlist OR Memory)
                    is_known = any(d in domain for d in TRUSTED_MEDIA) or domain.endswith((".edu", ".ac.uk"))
                    if memory and domain in memory.get("trusted_sources", []):
                        is_known = True

                    if is_known:
                        if memory is not None: update_memory(domain, memory)

                        raw_date = article.get('seendate', '')
                        fmt_date = datetime.strptime(raw_date, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d") if raw_date else None
                        category = classify_entry(title, "", "Global News")
                        hits.append({
                            "LCDS Mention": title,
                            "Link": url,
                            "Date Available Online": fmt_date,
                            "Type": category,
                            "Source": article.get('sourcegeography', 'GDELT'),
                            "Name": "LCDS General",
                            "Snippet": f"Sourced via GDELT (Domain: {domain})"
                        })
            time.sleep(1)
        except: pass
    return hits

# --- MAIN ---

def main():
    print("--- LCDS Tracker v10.0 (Clean & Smart) ---")
    
    # 1. LOAD MEMORY
    memory = load_memory()
    print(f"Memory: Tracking {len(memory['trusted_sources'])} trusted sources.")

    df_orcid = load_orcid_file(INPUT_ORCID_FILE)
    if 'Name' not in df_orcid.columns: 
        print("Error: Name column missing in ORCID file.")
        return

    # 2. LOAD EXISTING DATA (For De-duplication)
    # We assume you have DELETED the old file for a fresh start, 
    # but this handles subsequent runs.
    try:
        df_old = pd.read_csv(OUTPUT_FILE)
        # Ensure we filter out old 'Publication' types just in case
        df_old = df_old[df_old['Type'] != 'Publication']
        existing_links = set(df_old['Link'].astype(str))
        all_data = df_old.to_dict('records')
        print(f"Loaded {len(df_old)} existing records.")
    except:
        existing_links = set()
        all_data = []

    # 3. PROCESS PEOPLE
    for _, row in df_orcid.iterrows():
        name = row['Name']
        orcid_col = next((c for c in df_orcid.columns if c.lower() == 'orcid'), None)
        orcid = row[orcid_col] if orcid_col else None
        
        print(f"Scanning: {name}")

        # A. Crossref Seed -> News
        titles = fetch_crossref_titles(orcid)
        for t in titles:
            hits = search_google_rss(f'"{t}"', mode="Pub", academic_name=name, default_type="Research Coverage", memory=memory)
            for h in hits:
                if h['Link'] not in existing_links:
                    all_data.append(h)
                    existing_links.add(h['Link'])
            time.sleep(0.5)

        # B. Direct Name Search (Media)
        hits = search_google_rss(f'"{name}"', mode="Name", academic_name=name, default_type="Media Mention", memory=memory)
        for h in hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])
        
        # C. Event Search (Keynote/Award) - via Google News, NO DDG
        event_query = f'"{name}" AND (keynote OR plenary OR award OR prize)'
        event_hits = search_google_rss(event_query, mode="Name", academic_name=name, default_type="Talk/Award", memory=memory)
        for h in event_hits:
             if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])

        time.sleep(1)

    # 4. PROCESS GDELT (GLOBAL)
    gdelt_hits = fetch_gdelt_impact(memory=memory)
    for h in gdelt_hits:
        if h['Link'] not in existing_links:
            all_data.append(h)
            existing_links.add(h['Link'])

    # 5. SAVE
    df_final = pd.DataFrame(all_data)
    if not df_final.empty:
        # Date Cleaning
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        s_date, e_date = get_date_window()
        
        # Filter: Date in range OR Date is Missing (Keep NaT)
        mask = ((df_final['Date Available Online'] >= s_date) & (df_final['Date Available Online'] <= e_date)) | (df_final['Date Available Online'].isna())
        df_final = df_final[mask]
        
        # Sort & Dedup
        df_final.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)
        df_final.drop_duplicates(subset='Link', keep='first', inplace=True)

        # Atomic Write
        temp_file = f"{OUTPUT_FILE}.tmp"
        df_final.to_csv(temp_file, index=False)
        os.replace(temp_file, OUTPUT_FILE)
        print(f"Done. Saved {len(df_final)} records safely.")
        
        # Save Memory
        save_memory(memory)

if __name__ == "__main__":
    main()
