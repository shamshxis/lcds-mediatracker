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
    soup = BeautifulSoup(text, "html.parser")
    for a in soup.findAll('a'): del a['href']
    return soup.get_text(separator=" ").strip()

def extract_snippet(entry):
    if hasattr(entry, 'summary'): return clean_html(entry.summary)
    if hasattr(entry, 'description'): return clean_html(entry.description)
    return ""

def validate_hit(entry, name, paper_titles):
    """
    SMART VALIDATION (v4):
    1. Check for Name.
    2. Check for ANY known Paper Title (fuzzy match).
    """
    content = (entry.title + " " + extract_snippet(entry)).lower()
    
    # Check 1: Name Match
    if name.lower() in content: 
        return True
        
    # Check 2: Paper Title Match
    # If the article mentions "The future of fertility..." (your paper), we keep it.
    for title in paper_titles:
        if title and len(title) > 20 and title.lower() in content:
            return True
            
    return False

# --- ENGINE 1: OPENALEX (Get Titles for Validation) ---
def fetch_paper_titles(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=10"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        # Return list of clean titles
        return [i.get('title') for i in data.get('results', []) if i.get('title')]
    except: return []

# --- ENGINE 2: GOOGLE NEWS (Conference + Media) ---
def fetch_google_news(query, paper_titles):
    # EXPANDED QUERY: Captures Media + Major Demography Conferences
    if "Leverhulme" not in query:
        # We add PAA, EPC, BSPS, MPIDR, IUSSP to the dragnet
        smart_query = f'"{query}" AND ("Oxford" OR "LCDS" OR "Demographic" OR "Population" OR "PAA" OR "EPC" OR "BSPS" OR "MPIDR" OR "IUSSP")'
    else:
        smart_query = query

    encoded = urllib.parse.quote(smart_query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    
    try:
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (LCDS Bot)'})
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries:
            snippet = extract_snippet(entry)
            
            # PASS THE PAPER TITLES FOR VALIDATION
            if "Leverhulme" not in query and not validate_hit(entry, query, paper_titles): 
                continue 

            title = entry.title
            link = entry.link
            
            try:
                dt = pd.to_datetime(entry.published).date()
            except:
                dt = date.today()

            # INTELLIGENT TAGGING (v4)
            low_text = (title + " " + snippet).lower()
            
            # A. Conferences
            if any(x in low_text for x in ["paa", "epc", "bsps", "mpidr", "iussp", "conference", "annual meeting", "poster session"]):
                item_type = "Conference / Talk"
            # B. Keynotes
            elif any(x in low_text for x in ["keynote", "plenary", "panelist", "invited speaker"]):
                item_type = "Keynote"
            # C. Research/Study
            elif any(x in low_text for x in ["study", "research", "paper", "journal", "published", "new findings"]):
                item_type = "Media (Research)"
            # D. General
            else:
                item_type = "Media Mention"

            results.append({
                "LCDS Mention": title,
                "Snippet": snippet[:400], 
                "Link": link,
                "Date Available Online": dt,
                "Type": item_type,
                "Source": "Google News",
                "Name": query
            })
        return results
    except:
        return []

# --- WORKER ---
def process_person(row):
    name = row['Name']
    orcid = row['ORCID']
    
    # 1. Get recent paper titles (for validation only)
    titles = fetch_paper_titles(orcid)
    
    # 2. Search Media/Conferences using Name + Titles
    return fetch_google_news(name, titles)

# --- MAIN ---
def main():
    print("--- LCDS Conference & Media Tracker (v4) ---")
    try:
        df_p = pd.read_csv(INPUT_FILE, encoding='latin1')
        df_p = df_p[df_p['Status'] != 'Ignore']
    except Exception as e: print(e); return

    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            # Cleanup
            df_existing = df_existing[df_existing['Name'] != "LCDS General"]
            df_existing.dropna(subset=['Snippet'], inplace=True)
            existing_links = set(df_existing['Link'].astype(str))
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []
    print("Scanning (Includes PAA, EPC, BSPS, MPIDR)...")
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_person, r): r['Name'] for _, r in df_p.iterrows()}
        for f in as_completed(futures):
            try:
                for res in f.result():
                    if str(res['Link']) not in existing_links:
                        new_records.append(res); existing_links.add(str(res['Link']))
            except: pass

    if new_records or not df_existing.empty:
        df_final = pd.concat([df_existing, pd.DataFrame(new_records)], ignore_index=True)
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Done. Database now has {len(df_final)} records.")

if __name__ == "__main__":
    main()
