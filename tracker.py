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
# HISTORY SETTING: We fetch academic impact data back to Sep 2019
START_DATE_FILTER = "2019-09-01"

CENTRE_QUERIES = [
    '"Leverhulme Centre for Demographic Science"',
    '"LCDS Oxford"',
]

def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

# --- WORKER FUNCTIONS ---
def fetch_google_news(query):
    """
    Fetches LIVE news (Google RSS only provides recent history).
    """
    if "Leverhulme" not in query: 
        smart_query = f'"{query}" AND ("Oxford" OR "LCDS" OR "Demographic")'
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

def fetch_openalex_impact(orcid, name):
    """
    Fetches papers from SEP 2019 onwards.
    """
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan':
        return []
        
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    # FILTER DATE SET TO 2019-09-01
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=10"
    
    results = []
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        
        for item in data.get('results', []):
            title = item.get('title')
            pub_date = item.get('publication_date')
            
            # If paper is recent/relevant, search news for it
            if title and len(title.split()) > 5:
                # 1. Add the paper itself as an 'Academic Output' record
                results.append({
                    "LCDS Mention": title,
                    "Summary": f"Publication (OpenAlex). Citations: {item.get('cited_by_count', 0)}",
                    "Link": item.get('doi', item.get('id')),
                    "Date Available Online": pub_date,
                    "Type": "Academic Output",
                    "Source": "OpenAlex",
                    "Name": name
                })
                
                # 2. Check if this paper title is in the news
                paper_news = fetch_google_news(f'"{title}"')
                for p in paper_news:
                    p['Type'] = "Media (via Paper)"
                    p['Name'] = name
                    results.append(p)
    except:
        pass
    return results

def process_person(row):
    name = row['Name']
    orcid = row['ORCID']
    person_results = []
    
    # 1. Media (Smart Query)
    person_results.extend(fetch_google_news(name))
    
    # 2. Impact (Since 2019)
    person_results.extend(fetch_openalex_impact(orcid, name))
    
    return person_results

def main():
    print("--- LCDS Historical Tracker (Sep 2019+) ---")
    start_time = time.time()
    
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_people.columns:
            df_people = df_people[df_people['Status'] != 'Ignore']
        print(f"Loaded {len(df_people)} people.")
    except Exception as e:
        print(f"Error loading CSV: {e}"); return

    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            existing_links = set(df_existing['Link'].astype(str))
            print(f"Loaded {len(df_existing)} existing records.")
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []

    # CENTRE NEWS
    print("Scanning Centre News...")
    for query in CENTRE_QUERIES:
        hits = fetch_google_news(query)
        for h in hits:
            h['Name'] = "LCDS General"
            if str(h['Link']) not in existing_links:
                new_records.append(h)
                existing_links.add(str(h['Link']))

    # PEOPLE (PARALLEL)
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

    # SAVE
    print(f"Scan finished. Found {len(new_records)} new items.")
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
