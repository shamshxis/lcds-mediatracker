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
START_DATE_FILTER = "2023-01-01"

# SEARCH TERMS FOR "GENERAL" CENTRE MENTIONS (Replaces the noisy RSS feeds)
CENTRE_QUERIES = [
    '"Leverhulme Centre for Demographic Science"',
    '"LCDS Oxford"',
]

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

# --- WORKER FUNCTIONS (API CALLS) ---
def fetch_google_news(query):
    """
    Fetches news for a specific query using Google RSS.
    We append 'Oxford' or 'LCDS' to the query URL to force relevance 
    on the server side, ensuring we don't miss key academics.
    """
    # ENHANCEMENT: Construct a smart query to filter noise AT THE SOURCE.
    # If query is a person's name, ensure it looks for Oxford/LCDS context.
    if "Leverhulme" not in query: 
        # e.g., search for: "Melinda Mills" AND ("Oxford" OR "Demographic")
        # This catches "Melinda Mills" in an Oxford context even if the snippet is short.
        smart_query = f'"{query}" AND ("Oxford" OR "LCDS" OR "Demographic" OR "Sociology")'
    else:
        smart_query = query

    encoded = urllib.parse.quote(smart_query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    
    try:
        # Timeout is crucial for threading
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (LCDS Bot)'})
        feed = feedparser.parse(resp.content)
        
        results = []
        for entry in feed.entries:
            title = entry.title
            summary = clean_html(getattr(entry, 'summary', ''))
            
            # Date Parsing
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
                "Name": query # Keeps the original name (e.g., Melinda Mills)
            })
        return results
    except Exception as e:
        # Fail silently in threads to keep moving
        return []

def fetch_openalex_impact(orcid, name):
    """
    Checks recent papers (titles) and citations.
    """
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan':
        return []
        
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    # Fetch recent works
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=5"
    
    results = []
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        
        for item in data.get('results', []):
            title = item.get('title')
            # If we find a paper, check if IT is in the news
            if title and len(title.split()) > 5:
                # Recursive check: Search news for this paper title
                paper_news = fetch_google_news(f'"{title}"')
                for p in paper_news:
                    p['Type'] = "Media (via Paper)"
                    p['Name'] = name
                    results.append(p)
    except:
        pass
    return results

def process_person(row):
    """
    The worker function that runs in parallel for each person.
    """
    name = row['Name']
    orcid = row['ORCID']
    person_results = []
    
    # 1. Search Media for Name (Smart Query)
    person_results.extend(fetch_google_news(name))
    
    # 2. Search Impact (Papers)
    person_results.extend(fetch_openalex_impact(orcid, name))
    
    return person_results

# --- MAIN ---
def main():
    print("--- Starting LCDS Parallel Tracker ---")
    start_time = time.time()
    
    # 1. Load Data
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        # Filter: Ensure we are only tracking verified/active people if needed
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

    # 3. SCAN GENERAL CENTRE NEWS (Targeted)
    print("Scanning Centre News...")
    for query in CENTRE_QUERIES:
        hits = fetch_google_news(query)
        for h in hits:
            h['Name'] = "LCDS General"
            if str(h['Link']) not in existing_links:
                new_records.append(h)
                existing_links.add(str(h['Link']))

    # 4. SCAN PEOPLE (PARALLEL)
    print("Scanning People (Parallel Execution)...")
    
    # We use max_workers=5 to be polite to Google. 
    # Too high (e.g. 20) = instant 429 Block.
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all tasks
        future_to_person = {executor.submit(process_person, row): row['Name'] for _, row in df_people.iterrows()}
        
        # Process as they complete
        for i, future in enumerate(as_completed(future_to_person)):
            person_name = future_to_person[future]
            try:
                results = future.result()
                count = 0
                for res in results:
                    if str(res['Link']) not in existing_links:
                        new_records.append(res)
                        existing_links.add(str(res['Link']))
                        count += 1
                
                # Optional: Print status every 10 people
                if (i+1) % 10 == 0:
                    print(f"  Processed {i+1}/{len(df_people)} profiles...")
                    
            except Exception as exc:
                print(f"  Error processing {person_name}: {exc}")

    # 5. SAVE
    print(f"Scan finished in {round(time.time() - start_time, 2)}s. Found {len(new_records)} new items.")
    
    if new_records:
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        
        # Formatting
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
