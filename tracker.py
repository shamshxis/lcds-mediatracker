import pandas as pd
import feedparser
import requests
import urllib.parse
import urllib.robotparser
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
ARCHIVE_FILE = "lcds_media_archive.csv" 
MEMORY_FILE = "source_memory.json"
USER_AGENT = "LCDS_Impact_Tracker/12.0 (mailto:admin@lcds.ox.ac.uk)"

# 1. BLOCKLIST: ACADEMIC JOURNALS + STATIC PROFILES + LEGACY SOCIALS
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

# 2. ALLOWLIST (Preferred Media)
TRUSTED_MEDIA = [
    "bbc.co.uk", "bbc.com", "ft.com", "theguardian.com", "telegraph.co.uk",
    "timeshighereducation.com", "economist.com", "reuters.com", "bloomberg.com",
    "ox.ac.uk", "nuffield.ox.ac.uk", "demography.ox.ac.uk",
    "washingtonpost.com", "nytimes.com", "forbes.com", "weforum.org", 
    "abc.net.au", "theconversation.com", "medium.com", "substack.com",
    "newscientist.com", "phys.org", "eurekalert.org"
]

# 3. TARGETED RADAR: Global Conferences, Societies & Substacks
TARGETED_FEEDS = [
    # Global Societies
    {"name": "PAA (US)", "url": 'https://news.google.com/rss/search?q="Population+Association+of+America"+OR+"PAA+Annual+Meeting"&hl=en-US&gl=US&ceid=US:en', "type": "Conference"},
    {"name": "IUSSP (Global)", "url": 'https://news.google.com/rss/search?q="International+Union+for+the+Scientific+Study+of+Population"+OR+"IUSSP"&hl=en-GB&gl=GB&ceid=GB:en', "type": "Conference"},
    {"name": "EAPS / EPC (Europe)", "url": 'https://news.google.com/rss/search?q="European+Association+for+Population+Studies"+OR+"European+Population+Conference"&hl=en-GB&gl=GB&ceid=GB:en', "type": "Conference"},
    {"name": "BSPS (UK)", "url": 'https://news.google.com/rss/search?q="British+Society+for+Population+Studies"+OR+"BSPS"&hl=en-GB&gl=GB&ceid=GB:en', "type": "Conference"},
    {"name": "APA (Australia)", "url": 'https://news.google.com/rss/search?q="Australian+Population+Association"&hl=en-AU&gl=AU&ceid=AU:en', "type": "Conference"},
    {"name": "Asian Population Assoc", "url": 'https://news.google.com/rss/search?q="Asian+Population+Association"&hl=en-IN&gl=IN&ceid=IN:en', "type": "Conference"},
    {"name": "IASP (India)", "url": 'https://news.google.com/rss/search?q="Indian+Association+for+the+Study+of+Population"+OR+"IASP+Conference"&hl=en-IN&gl=IN&ceid=IN:en', "type": "Conference"},
    
    # Example: If you know a specific Substack, add it here like this:
    # {"name": "Works in Progress", "url": "https://worksinprogress.substack.com/feed", "type": "Blog/Opinion"},
]

GDELT_QUERIES = [
    '"Leverhulme Centre" near10 "Demographic"',
    '"LCDS" near10 "Oxford"',
    '"Oxford University" near10 "Demography"',
    '"Melinda Mills" near10 "Oxford"',
    '"Jennifer Dowd" near10 "Oxford"'
]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- ETHICAL SCRAPING: ROBOTS.TXT MANAGER ---
class RobotManager:
    def __init__(self, user_agent):
        self.parsers = {}
        self.user_agent = user_agent

    def can_fetch(self, url):
        try:
            parsed_url = urllib.parse.urlparse(url)
            domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            if domain not in self.parsers:
                rp = urllib.robotparser.RobotFileParser()
                rp.set_url(f"{domain}/robots.txt")
                try:
                    rp.read()
                    self.parsers[domain] = rp
                except:
                    self.parsers[domain] = None 
            
            if self.parsers[domain] is None: return True
            return self.parsers[domain].can_fetch(self.user_agent, url)
        except: return True 

robot_checker = RobotManager(USER_AGENT)

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
        return True 
    return False

# --- HELPERS ---
def get_date_window():
    today = datetime.now()
    return today - timedelta(days=180), today + timedelta(days=180)

def normalize_date(date_obj):
    if not date_obj: return None
    try: return pd.to_datetime(date_obj).strftime('%Y-%m-%d')
    except: return None

def clean_html(text):
    if not text: return ""
    try: return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()
    except: return str(text)

def is_blocked_content(link, title, name):
    link = link.lower()
    title = title.lower()
    name = name.lower() if name else ""
    
    if any(blocked in link for blocked in URL_BLOCKLIST): return True
    if any(blocked in title for blocked in TITLE_BLOCKLIST): return True
    if name:
        clean_title = re.sub(r'\s*[|\-–].*', '', title).strip()
        if clean_title == name: return True
    return False

def classify_entry(title, snippet, default_type):
    full_text = (str(title) + " " + str(snippet)).lower()
    if "podcast" in full_text or "episode" in full_text: return "Podcast"
    if "radio" in full_text or "bbc radio" in full_text or " fm " in full_text: return "Radio"
    if "blog" in full_text or "substack" in full_text or "opinion" in full_text or "medium.com" in full_text: return "Blog/Opinion"
    if "keynote" in full_text or "plenary" in full_text: return "Keynote"
    if "award" in full_text or "prize" in full_text or "medal" in full_text: return "Award"
    if "conference" in full_text or "annual meeting" in full_text: return "Conference"
    if "forum" in full_text or "discussion" in full_text: return "Forum/Discussion"
    return default_type

def verify_affiliation(text, name=""):
    text_lower = text.lower()
    affiliations = ["oxford", "leverhulme", "lcds", "nuffield", "demographic", "population", "sociology"]
    if name and name.lower() not in text_lower: return False
    has_aff = any(a in text_lower for a in affiliations)
    if name and "melinda mills" in name.lower() and "groningen" in text_lower: has_aff = True
    return has_aff

def load_orcid_file(filepath):
    for enc in ['utf-8', 'latin1', 'cp1252']:
        try: return pd.read_csv(filepath, encoding=enc)
        except: continue
    return pd.DataFrame() 

# --- DATA FETCHERS ---

def fetch_crossref_titles(orcid):
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

def search_multi_engine_rss(query, mode="Name", academic_name=None, default_type="Media Mention", memory=None):
    """Searches Google, Bing, and Yahoo via RSS."""
    encoded = urllib.parse.quote(query)
    hits = []
    engines = {
        "Google News": f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en",
        "Bing News": f"https://www.bing.com/news/search?q={encoded}&format=RSS",
        "Yahoo News": f"https://news.search.yahoo.com/rss?p={encoded}"
    }
    
    for engine_name, url in engines.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                link = entry.link
                title = clean_html(entry.title)
                
                if is_blocked_content(link, title, academic_name): continue
                    
                if not robot_checker.can_fetch(link):
                    logging.info(f"Skipping link (Robots.txt restricted): {link}")
                    continue
                
                summary = clean_html(getattr(entry, 'summary', ''))
                full_text = f"{title} {summary}"
                
                valid = False
                if mode == "Name":
                    if verify_affiliation(full_text, academic_name): valid = True
                else: valid = True 
                
                if valid:
                    domain = urllib.parse.urlparse(link).netloc
                    if memory is not None: update_memory(domain, memory)

                    category = classify_entry(title, summary, default_type)
                    hits.append({
                        "LCDS Mention": title,
                        "Link": link,
                        "Date Available Online": normalize_date(getattr(entry, 'published', None)),
                        "Type": category,
                        "Source": entry.source.get('title', engine_name),
                        "Name": academic_name,
                        "Snippet": summary[:400]
                    })
        except: pass
    return hits

def fetch_targeted_radar(academic_name):
    """Parses defined RSS feeds for societies and conferences."""
    hits = []
    for feed_info in TARGETED_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries:
                title = clean_html(entry.title)
                summary = clean_html(getattr(entry, 'summary', ''))
                full_text = f"{title} {summary}"
                
                if academic_name.lower() in full_text.lower():
                    hits.append({
                        "LCDS Mention": title,
                        "Link": entry.link,
                        "Date Available Online": normalize_date(getattr(entry, 'published', None)),
                        "Type": feed_info.get("type", "Conference/Talk"),
                        "Source": feed_info["name"],
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
                    if not robot_checker.can_fetch(url): continue
                    
                    domain = article.get('domain', '').lower()
                    
                    is_known = any(d in domain for d in TRUSTED_MEDIA) or domain.endswith((".edu", ".ac.uk"))
                    if memory and domain in memory.get("trusted_sources", []): is_known = True

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
    print("--- LCDS Tracker v12.0 (Substack/Modern Newsletters included) ---")
    
    memory = load_memory()
    print(f"Memory: Tracking {len(memory['trusted_sources'])} trusted sources.")

    df_orcid = load_orcid_file(INPUT_ORCID_FILE)
    if 'Name' not in df_orcid.columns: 
        print("Error: Name column missing in ORCID file.")
        return

    # Load Existing Active File
    try:
        df_old = pd.read_csv(OUTPUT_FILE)
        df_old = df_old[df_old['Type'] != 'Publication']
        existing_links = set(df_old['Link'].astype(str))
        all_data = df_old.to_dict('records')
    except:
        existing_links = set()
        all_data = []

    # 1. PROCESS PEOPLE
    for _, row in df_orcid.iterrows():
        name = row['Name']
        orcid_col = next((c for c in df_orcid.columns if c.lower() == 'orcid'), None)
        orcid = row[orcid_col] if orcid_col else None
        
        print(f"Scanning: {name}")

        # A. Targeted Radar (Societies/Conferences/Direct Substacks)
        radar_hits = fetch_targeted_radar(name)
        for h in radar_hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])

        # B. Crossref Seed -> Multi-Engine News
        titles = fetch_crossref_titles(orcid)
        for t in titles:
            hits = search_multi_engine_rss(f'"{t}"', mode="Pub", academic_name=name, default_type="Research Coverage", memory=memory)
            for h in hits:
                if h['Link'] not in existing_links:
                    all_data.append(h)
                    existing_links.add(h['Link'])
            time.sleep(0.5)

        # C. Direct Name Search (Multi-Engine Media)
        hits = search_multi_engine_rss(f'"{name}"', mode="Name", academic_name=name, default_type="Media Mention", memory=memory)
        for h in hits:
            if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])
        
        # D. Event Search (Keynotes & Awards)
        event_query = f'"{name}" AND (keynote OR plenary OR award OR prize)'
        event_hits = search_multi_engine_rss(event_query, mode="Name", academic_name=name, default_type="Talk/Award", memory=memory)
        for h in event_hits:
             if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])

        # E. MODERN NEWSLETTERS & BLOGS (Substack, Medium, Ghost)
        # We enforce "site:" operators to guarantee it only looks in these platforms.
        blog_query = f'"{name}" (site:substack.com OR site:medium.com OR site:ghost.io)'
        blog_hits = search_multi_engine_rss(blog_query, mode="Name", academic_name=name, default_type="Blog/Opinion", memory=memory)
        for h in blog_hits:
             if h['Link'] not in existing_links:
                all_data.append(h)
                existing_links.add(h['Link'])

        time.sleep(1)

    # 2. PROCESS GDELT (GLOBAL MEDIA)
    gdelt_hits = fetch_gdelt_impact(memory=memory)
    for h in gdelt_hits:
        if h['Link'] not in existing_links:
            all_data.append(h)
            existing_links.add(h['Link'])

    # 3. BUILD DATAFRAMES
    df_new_data = pd.DataFrame(all_data)
    
    if not df_new_data.empty:
        df_new_data['Date Available Online'] = pd.to_datetime(df_new_data['Date Available Online'], errors='coerce')
        
        # --- A. MASTER ARCHIVE SAVE (Never Delete Old Data) ---
        try:
            df_archive = pd.read_csv(ARCHIVE_FILE)
            df_archive['Date Available Online'] = pd.to_datetime(df_archive['Date Available Online'], errors='coerce')
        except:
            df_archive = pd.DataFrame()
            
        df_master = pd.concat([df_archive, df_new_data], ignore_index=True)
        if not df_master.empty:
            df_master.drop_duplicates(subset='Link', keep='last', inplace=True)
            df_master.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)
            df_master.to_csv(ARCHIVE_FILE, index=False)
            print(f"Archived {len(df_master)} total historical records to {ARCHIVE_FILE}.")

        # --- B. DASHBOARD VIEW SAVE (Filtered to ±6 Months) ---
        s_date, e_date = get_date_window()
        mask = ((df_new_data['Date Available Online'] >= s_date) & (df_new_data['Date Available Online'] <= e_date)) | (df_new_data['Date Available Online'].isna())
        df_dashboard_view = df_new_data[mask].copy()
        
        df_dashboard_view.sort_values(by='Date Available Online', ascending=False, na_position='last', inplace=True)
        df_dashboard_view.drop_duplicates(subset='Link', keep='first', inplace=True)

        temp_file = f"{OUTPUT_FILE}.tmp"
        df_dashboard_view.to_csv(temp_file, index=False)
        os.replace(temp_file, OUTPUT_FILE)
        print(f"Saved {len(df_dashboard_view)} active records for the Dashboard.")
        
        save_memory(memory)

if __name__ == "__main__":
    main()
