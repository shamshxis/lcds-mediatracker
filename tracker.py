import pandas as pd
import feedparser
import requests
import urllib.parse
import os
import time
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
INPUT_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
START_DATE_FILTER = "2019-09-01"

# Oxford Feeds (For Internal News)
OXFORD_RSS_URLS = [
    "https://www.ox.ac.uk/feeds/rss/news",
    "https://www.oxfordmail.co.uk/news/rss/", # Removed per request, keeping only if you change mind
    "https://www.sociology.ox.ac.uk/news/rss", 
]

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    for a in soup.findAll('a'): del a['href']
    return soup.get_text(separator=" ").strip()

def extract_snippet(entry):
    if hasattr(entry, 'summary'): return clean_html(entry.summary)
    if hasattr(entry, 'description'): return clean_html(entry.description)
    return ""

def validate_hit(entry, name, paper_titles):
    """
    STRICT VALIDATION:
    1. Name + (Oxford/LCDS/Nuffield) must appear.
    2. OR A known Paper Title must appear.
    """
    content = (entry.title + " " + extract_snippet(entry)).lower()
    name_lower = name.lower()
    
    # 1. Check Name + Context
    # We require the name AND one of the context keywords
    if name_lower in content:
        if any(x in content for x in ["oxford", "lcds", "nuffield", "leverhulme", "demographic"]):
            return True
            
    # 2. Check Paper Title (regardless of name)
    # Useful if the headline is "New study on fertility..." without naming the author
    if paper_titles:
        for title in paper_titles:
            # Only match long unique titles to avoid false positives
            if title and len(title) > 25 and title.lower() in content:
                return True
            
    return False

# --- ENGINE 1: OXFORD RSS (Internal Comms) ---
def fetch_oxford_rss(people_names):
    results = []
    for url in OXFORD_RSS_URLS:
        # Strict check: Only trust .ox.ac.uk domains for "University News"
        if ".ox.ac.uk" not in url: continue
        
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                content = (entry.title + " " + extract_snippet(entry)).lower()
                
                # Check if ANY of our people are mentioned in this Oxford News item
                for name in people_names:
                    if name.lower() in content:
                        results.append({
                            "LCDS Mention": entry.title,
                            "Snippet": extract_snippet(entry)[:400],
                            "Link": entry.link,
                            "Date Available Online": pd.to_datetime(entry.published).date(),
                            "Type": "University News",
                            "Source": "Oxford University",
                            "Name": name
                        })
        except: continue
    return results

# --- ENGINE 2: OPENALEX (Get Papers) ---
def fetch_papers(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    
    # Fetch all papers since 2019 to ensure we have the history
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=30"
    
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        # Return list of dicts: {'title': '...', 'doi': '...', 'date': '...'}
        return [{'title': i.get('title'), 'doi': i.get('doi'), 'date': i.get('publication_date')} for i in data.get('results', [])]
    except: return []

# --- ENGINE 3: GOOGLE NEWS (Person & Papers) ---
def fetch_google_news(name, paper_titles):
    # QUERY LOGIC: Name + (Oxford OR LCDS OR Nuffield)
    # This captures EXTERNAL media mentions about our specific people.
    smart_query = f'"{name}" AND ("Oxford" OR "LCDS" OR "Nuffield" OR "Leverhulme")'

    encoded = urllib.parse.quote(smart_query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    
    try:
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (LCDS Bot)'})
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries:
            snippet = extract_snippet(entry)
            
            # VALIDATION: Must match Name+Context OR a Paper Title
            if not validate_hit(entry, name, paper_titles): 
                continue 

            title = entry.title
            link = entry.link
            try:
                dt = pd.to_datetime(entry.published).date()
            except:
                dt = date.today()

            # TAGGING
            low_text = (title + " " + snippet).lower()
            if any(x in low_text for x in ["keynote", "plenary", "panelist", "conference"]):
                item_type = "Conference / Talk"
            elif any(x in low_text for x in ["study", "research", "paper", "journal"]):
                item_type = "Media (Research)"
            else:
                item_type = "Media Mention"

            results.append({
                "LCDS Mention": title,
                "Snippet": snippet[:400],
                "Link": link,
                "Date Available Online": dt,
                "Type": item_type,
                "Source": "Google News",
                "Name": name
            })
        return results
    except: return []

# --- ENGINE 4: CROSSREF (Impact) ---
def fetch_crossref(doi, name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    url = f"https://api.eventdata.crossref.org/v1/events?obj-id={clean_doi}&rows=5&mailto=admin@lcds.ox.ac.uk"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200: return []
        events = []
        for item in r.json().get('message', {}).get('events', []):
            sid = item.get('source_id')
            if sid in ['newsfeed', 'wikipedia', 'reddit-links', 'web']: # We focus on these sources
                link = item.get('subj', {}).get('pid') or item.get('subj', {}).get('url')
                occurred = item.get('occurred_at', '')[:10]
                events.append({
                    "LCDS Mention": f"Mention in {sid.capitalize()}",
                    "Snippet": f"Article ({clean_doi}) mentioned on {sid}.",
                    "Link": link,
                    "Date Available Online": occurred,
                    "Type": "Impact / Web",
                    "Source": f"Crossref ({sid})",
                    "Name": name
                })
        return events
    except: return []

# --- WORKER ---
def process_person(row):
    name = row['Name']
    orcid = row['ORCID']
    person_results = []
    
    # 1. Get Papers (Sep 2019 - Present)
    papers = fetch_papers(orcid)
    paper_titles = [p['title'] for p in papers if p['title']]
    
    # 2. Search Media for Person + Context (using Paper titles for extra validation)
    person_results.extend(fetch_google_news(name, paper_titles))
    
    # 3. Check Papers for Impact (Crossref)
    # We prioritize papers from the last 6 months for efficiency, 
    # but check all if the list is short (<10).
    recent_cutoff = pd.Timestamp.now() - pd.Timedelta(days=180)
    
    for paper in papers:
        # Check if paper is recent OR if we have few papers total
        is_recent = pd.to_datetime(paper['date']) > recent_cutoff if paper['date'] else False
        
        if is_recent or len(papers) < 10:
            if paper['doi']:
                person_results.extend(fetch_crossref(paper['doi'], name))
                
    return person_results

# --- MAIN ---
def main():
    print("--- LCDS Smart Tracker (Sep 2019+) ---")
    
    # 1. Load People
    try:
        df_p = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_p.columns: df_p = df_p[df_p['Status'] != 'Ignore']
        people_list = df_p['Name'].tolist()
        print(f"Loaded {len(df_p)} people.")
    except Exception as e: print(e); return

    # 2. Load Existing DB
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            # Cleanup
            df_existing.dropna(subset=['Link'], inplace=True)
            existing_links = set(df_existing['Link'].astype(str))
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []

    # 3. SCAN 1: OXFORD FEEDS (Direct)
    print("Scanning Oxford/University Feeds...")
    oxford_hits = fetch_oxford_rss(people_list)
    for h in oxford_hits:
        if str(h['Link']) not in existing_links:
            new_records.append(h)
            existing_links.add(str(h['Link']))

    # 4. SCAN 2: PEOPLE & PAPERS (Parallel)
    print("Scanning People & Papers (20 Workers)...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_person, r): r['Name'] for _, r in df_p.iterrows()}
        for f in as_completed(futures):
            try:
                for res in f.result():
                    if str(res['Link']) not in existing_links:
                        new_records.append(res); existing_links.add(str(res['Link']))
            except: pass

    # 5. SAVE
    if new_records or not df_existing.empty:
        df_final = pd.concat([df_existing, pd.DataFrame(new_records)], ignore_index=True)
        # Ensure dates are standardized
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Done. Database has {len(df_final)} records. Added {len(new_records)} new.")

if __name__ == "__main__":
    main()
