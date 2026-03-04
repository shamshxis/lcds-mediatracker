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

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

# --- ENGINE 1: GOOGLE NEWS (Smart Query + Keynotes) ---
def fetch_google_news(query):
    # Smart Query matches Name + (Media Context OR Talk Context)
    if "Leverhulme" not in query: 
        smart_query = f'"{query}" AND ("Oxford" OR "LCDS" OR "Demographic" OR "Keynote" OR "Plenary" OR "Conference" OR "Panelist")'
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

            # INTELLIGENT TAGGING
            text_blob = (title + " " + summary).lower()
            if any(x in text_blob for x in ["keynote", "plenary", "panelist", "conference presentation", "invited speaker"]):
                item_type = "Keynote / Talk"
            elif any(x in text_blob for x in ["study", "research", "paper", "journal", "published"]):
                item_type = "Media (Research)"
            else:
                item_type = "Media Mention"

            results.append({
                "LCDS Mention": title,
                "Summary": summary[:500],
                "Link": entry.link,
                "Date Available Online": dt,
                "Type": item_type,
                "Source": "Google News",
                "Name": query
            })
        return results
    except:
        return []

# --- ENGINE 2: CROSSREF EVENT DATA ---
def fetch_crossref_events(doi, name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
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
    except: return []

# --- ENGINE 3: ALTMETRIC FREE ---
def fetch_altmetric_free(doi, name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    try:
        r = requests.get(f"https://api.altmetric.com/v1/doi/{clean_doi}", timeout=5)
        if r.status_code != 200: return []
        data = r.json()
        events = []
        if 'news' in data.get('posts', {}):
            for post in data['posts']['news'][:2]:
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
    except: return []

# --- ENGINE 4: OPENALEX (Discovery Only) ---
def fetch_openalex_papers(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=15"
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        return [{'title': i.get('title'), 'doi': i.get('doi')} for i in data.get('results', [])]
    except: return []

# --- WORKER ---
def process_person(row):
    name = row['Name']
    orcid = row['ORCID']
    person_results = []
    
    # A. Search Media & Keynotes
    person_results.extend(fetch_google_news(name))
    
    # B. Fetch Papers (Internal Use)
    papers = fetch_openalex_papers(orcid)
    
    for paper in papers:
        doi = paper.get('doi')
        title = paper.get('title')
        if doi:
            person_results.extend(fetch_crossref_events(doi, name))
            person_results.extend(fetch_altmetric_free(doi, name))
        if papers.index(paper) < 2 and title and len(title.split()) > 5:
            news_hits = fetch_google_news(f'"{title}"')
            for hit in news_hits:
                hit['Type'] = "Media (via Paper)"
                hit['Name'] = name
                person_results.append(hit)
    return person_results

# --- MAIN ---
def main():
    print("--- LCDS Media & Keynote Tracker ---")
    
    # 1. Load People
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_people.columns: df_people = df_people[df_people['Status'] != 'Ignore']
        print(f"Loaded {len(df_people)} people.")
    except Exception as e: print(f"Error loading CSV: {e}"); return

    # 2. Load Existing DB & CLEAN OLD GARBAGE
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            
            # --- CLEANUP STEP ---
            # Remove any rows where Name is "LCDS General" or Type is "Unknown"
            initial_len = len(df_existing)
            df_existing = df_existing[df_existing['Name'] != "LCDS General"]
            print(f"Loaded {initial_len} records. Removed {initial_len - len(df_existing)} 'LCDS General' junk records.")
            # --------------------

            existing_links = set(df_existing['Link'].astype(str))
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []

    # 3. PEOPLE SCAN (Parallel)
    print("Scanning People & Keynotes (Parallel)...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_person = {executor.submit(process_person, row): row['Name'] for _, row in df_people.iterrows()}
        for future in as_completed(future_to_person):
            try:
                for res in future.result():
                    if str(res['Link']) not in existing_links:
                        new_records.append(res)
                        existing_links.add(str(res['Link']))
            except Exception: pass

    # 4. SAVE
    print(f"Scan finished. Found {len(new_records)} new items.")
    if new_records or len(df_existing) > 0: # Save if we have new records OR we cleaned old ones
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final['Year'] = df_final['Date Available Online'].dt.year
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print("SUCCESS: Database updated and cleaned.")
    else:
        print("No new data.")

if __name__ == "__main__":
    main()
