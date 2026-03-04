import pandas as pd
import feedparser
import requests
import urllib.parse
import os
import time
from datetime import datetime, date
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
INPUT_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
START_DATE_FILTER = "2019-09-01"

# Targeted queries for the Centre itself
CENTRE_QUERIES = [
    '"Leverhulme Centre for Demographic Science"',
    '"LCDS Oxford"',
]

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

# --- ENGINE 1: GOOGLE NEWS (Smart Query) ---
def fetch_google_news(query):
    # Smart Query: Forces "Oxford" or "LCDS" context for names
    if "Leverhulme" not in query and "LCDS" not in query: 
        smart_query = f'"{query}" AND ("Oxford" OR "LCDS" OR "Demographic" OR "Sociology")'
    else:
        smart_query = query

    encoded = urllib.parse.quote(smart_query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    
    try:
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (LCDS Bot)'})
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries:
            title = entry.title
            summary = clean_html(getattr(entry, 'summary', ''))
            try:
                dt = pd.to_datetime(entry.published).date()
            except:
                dt = date.today()

            results.append({
                "LCDS Mention": title,
                "Summary": summary[:500],
                "Link": entry.link,
                "Date Available Online": dt,
                "Type": "Media Mention",
                "Source": "Google News",
                "Name": query
            })
        return results
    except:
        return []

# --- ENGINE 2: CROSSREF EVENT DATA (Free Impact) ---
def fetch_crossref_events(doi, name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    
    # We ask for Wikipedia, Newsfeed, Reddit, and Policy (Web)
    url = f"https://api.eventdata.crossref.org/v1/events?obj-id={clean_doi}&rows=5&mailto=admin@lcds.ox.ac.uk"
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200: return []
        
        events = []
        for item in r.json().get('message', {}).get('events', []):
            sid = item.get('source_id')
            if sid in ['newsfeed', 'wikipedia', 'reddit-links', 'web']:
                
                link = item.get('subj', {}).get('pid') or item.get('subj', {}).get('url')
                occurred = item.get('occurred_at', '')[:10]
                
                events.append({
                    "LCDS Mention": f"Mention in {sid.capitalize()}",
                    "Summary": f"Paper ({clean_doi}) discussed on {sid}.",
                    "Link": link,
                    "Date Available Online": occurred,
                    "Type": "Impact / Social",
                    "Source": f"Crossref ({sid})",
                    "Name": name
                })
        return events
    except:
        return []

# --- ENGINE 3: ALTMETRIC FREE (Backup Impact) ---
def fetch_altmetric_free(doi, name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    
    try:
        r = requests.get(f"https://api.altmetric.com/v1/doi/{clean_doi}", timeout=5)
        if r.status_code != 200: return []
        data = r.json()
        events = []
        
        # Check for News
        if 'news' in data.get('posts', {}):
            for post in data['posts']['news'][:3]:
                events.append({
                    "LCDS Mention": post.get('name', 'News Mention'),
                    "Summary": post.get('summary', 'News tracked by Altmetric'),
                    "Link": post.get('url'),
                    "Date Available Online": post.get('posted_on', '')[:10],
                    "Type": "Media (Altmetric)",
                    "Source": "Altmetric",
                    "Name": name
                })
        return events
    except:
        return []

# --- ENGINE 4: OPENALEX (Discovery Only) ---
def fetch_openalex_papers(orcid):
    """
    Fetches papers since Sep 2019 purely for Discovery.
    Does NOT return a record to be saved, only raw data to be searched.
    """
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan':
        return []
    
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=20"
    
    raw_papers = []
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        for item in data.get('results', []):
            raw_papers.append({
                'title': item.get('title'),
                'doi': item.get('doi'),
                'id': item.get('id')
            })
    except:
        pass
    return raw_papers

# --- WORKER ---
def process_person(row):
    name = row['Name']
    orcid = row['ORCID']
    person_results = []
    
    # A. Search Media (Name) - Smart Query
    person_results.extend(fetch_google_news(name))
    
    # B. Fetch Papers (Internal Use Only)
    papers = fetch_openalex_papers(orcid)
    
    for paper in papers:
        doi = paper.get('doi')
        title = paper.get('title')
        
        # We DO NOT save the paper itself anymore. 
        # We only save what we find ABOUT the paper.
        
        # C. Search Impact (Crossref + Altmetric)
        if doi:
            person_results.extend(fetch_crossref_events(doi, name))
            person_results.extend(fetch_altmetric_free(doi, name))
            
        # D. Search Media for Paper Title 
        # (Only for top 3 recent papers to prevent Google blocking)
        if papers.index(paper) < 3 and title and len(title.split()) > 5:
            news_hits = fetch_google_news(f'"{title}"')
            for hit in news_hits:
                hit['Type'] = "Media (via Paper)"
                hit['Name'] = name
                person_results.append(hit)

    return person_results

# --- MAIN ---
def main():
    print("--- LCDS Media-Only Tracker (No Raw Pubs) ---")
    start_time = time.time()
    
    # 1. Load Data
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_people.columns:
            df_people = df_people[df_people['Status'] != 'Ignore']
        print(f"Loaded {len(df_people)} people.")
    except Exception as e:
        print(f"Error loading CSV: {e}"); return

    # 2. Load Existing DB
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            existing_links = set(df_existing['Link'].astype(str))
            print(f"Loaded {len(df_existing)} existing records.")
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []

    # 3. CENTRE NEWS
    print("Scanning Centre News...")
    for query in CENTRE_QUERIES:
        hits = fetch_google_news(query)
        for h in hits:
            h['Name'] = "LCDS General"
            if str(h['Link']) not in existing_links:
                new_records.append(h)
                existing_links.add(str(h['Link']))

    # 4. PEOPLE (PARALLEL)
    print("Scanning People (Parallel)...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_person = {executor.submit(process_person, row): row['Name'] for _, row in df_people.iterrows()}
        
        for i, future in enumerate(as_completed(future_to_person)):
            try:
                results = future.result()
                for res in results:
                    if str(res['Link']) not in existing_links:
                        new_records.append(res)
                        existing_links.add(str(res['Link']))
            except Exception: pass

    # 5. SAVE
    print(f"Scan finished. Found {len(new_records)} new media mentions.")
    if new_records:
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        
        # Standardize Dates
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final['Year'] = df_final['Date Available Online'].dt.year
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print("SUCCESS: Database updated.")
    else:
        print("No new data.")

if __name__ == "__main__":
    main()
