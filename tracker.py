import pandas as pd
import feedparser
import requests
import urllib.parse
import os
import time
import re
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
INPUT_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"

# DATE LOGIC: Restrict to last 6 months only
LOOKBACK_DAYS = 180 # 6 months
START_DATE = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
CUTOFF_DATE = pd.to_datetime(START_DATE).date()

# 1. STRICT CONTEXT
CONTEXT_KEYWORDS = [
    r"university of oxford", r"oxford university", 
    r"leverhulme centre", r"lcds", 
    r"nuffield college", r"department of sociology", 
    r"demographic science", r"demography", r"population studies"
]

# 2. JUNK PATTERNS (Instant Reject)
# Added "Nature", "Frontiers", "BioTechniques" to catch those academic titles
JUNK_REGEX = re.compile(r"\b(obituary|funeral|death notice|dignity memorial|passed away|survived by|class of \d{4}|high school|football|basketball|microfibre|waterproof|drawstring|bag case|glass organiser|hammock|pub|coin|royal mint)\b", re.IGNORECASE)

# 3. PUBLISHER TITLES TO BLOCK (Because Google Links are hidden)
# If the title ends with " - Nature", " - Frontiers", we block it.
BAD_PUBLISHERS = [" - Nature", " - Frontiers", " - BioTechniques", " - MDPI", " - PLOS", " - Science", " - Cell"]

# 4. BLOCKED DOMAINS (For direct links)
BLOCK_DOMAINS = [
    "doi.org", "sciencedirect.com", "wiley.com", "springer.com", 
    "tandfonline.com", "sagepub.com", "oup.com", "cambridge.org", 
    "nature.com", "science.org", "amazon.com", "ebay.com"
]

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()

def extract_snippet(entry):
    if hasattr(entry, 'summary'): return clean_html(entry.summary)
    if hasattr(entry, 'description'): return clean_html(entry.description)
    return ""

def is_junk_hit(title, snippet, link):
    """
    Centralized Junk Checker.
    """
    text = (title + " " + snippet).lower()
    
    # 1. Check Date (Handled in main loop, but good to be safe)
    # 2. Check Regex (Obits, Shopping)
    if JUNK_REGEX.search(text): return True
    
    # 3. Check Bad Publishers in Title (e.g. "Study... - Nature")
    if any(pub.lower() in title.lower() for pub in BAD_PUBLISHERS): return True
    
    # 4. Check Blocked Domains
    if any(d in link.lower() for d in BLOCK_DOMAINS): return True
    
    return False

# --- NLP LAYER ---
def verify_content_relevance(url, name):
    try:
        time.sleep(1) # Be polite
        headers = {'User-Agent': 'Mozilla/5.0 (LCDS-Bot)'}
        r = requests.get(url, timeout=5, headers=headers)
        if r.status_code != 200: return False
        
        soup = BeautifulSoup(r.content, 'html.parser')
        for s in soup(["script", "style", "nav", "footer"]): s.decompose()
        text = soup.get_text().lower()
        
        if name.lower() not in text: return False
        
        # Must match at least one context keyword
        if any(re.search(pat, text) for pat in CONTEXT_KEYWORDS): return True
            
        return False
    except: return False

# --- VALIDATION ENGINE ---
def validate_hit(entry, name, paper_titles):
    title = entry.title
    snippet = extract_snippet(entry)
    link = entry.link
    content_blob = (title + " " + snippet).lower()
    
    # 1. Junk Check
    if is_junk_hit(title, snippet, link): return False
    
    # 2. Paper Title Match
    if paper_titles:
        for p_title in paper_titles:
            if p_title and len(p_title) > 20 and p_title.lower() in content_blob:
                return True

    # 3. Name + Context
    has_name = name.lower() in content_blob
    has_context = any(re.search(pat, content_blob) for pat in CONTEXT_KEYWORDS)
    
    if has_name and has_context: return True
    
    # 4. NLP Deep Scan
    if has_name: return verify_content_relevance(link, name)

    return False

# --- ENGINES ---
def fetch_papers_crossref(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    oid = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.crossref.org/works?filter=orcid:{oid},from-pub-date:{START_DATE}&rows=10"
    try:
        r = requests.get(url, timeout=10)
        items = r.json().get('message', {}).get('items', [])
        return [{'title': i.get('title', [''])[0], 'doi': i.get('DOI')} for i in items if i.get('title')]
    except: return []

def fetch_google_news(name, paper_titles):
    # Search for last 6 months only
    q_context = f'"{name}" AND ("Oxford" OR "LCDS" OR "Nuffield" OR "Leverhulme") after:{START_DATE}'
    queries = [q_context]
    
    if paper_titles:
        for p in paper_titles[:3]:
            safe_title = p['title'].replace(":", "").replace("-", " ")
            queries.append(f'"{safe_title}" after:{START_DATE}')
            
    results = []
    seen_links = set()
    
    for q in queries:
        encoded = urllib.parse.quote(q)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
        try:
            feed = feedparser.parse(requests.get(url, timeout=10).content)
            for entry in feed.entries:
                if entry.link in seen_links: continue
                
                # Tagging
                full_text = (entry.title + " " + extract_snippet(entry)).lower()
                if validate_hit(entry, name, [p['title'] for p in paper_titles]):
                    if any(x in full_text for x in ["keynote", "plenary", "conference"]): tag = "Conference / Talk"
                    elif any(x in full_text for x in ["study", "research", "paper"]): tag = "Media (Research)"
                    else: tag = "Media Mention"

                    results.append({
                        "LCDS Mention": entry.title,
                        "Snippet": extract_snippet(entry)[:400],
                        "Link": entry.link,
                        "Date Available Online": pd.to_datetime(entry.published).date(),
                        "Type": tag,
                        "Source": "Google News",
                        "Name": name
                    })
                    seen_links.add(entry.link)
        except: pass
    return results

# --- MAIN ---
def process_person(row):
    papers = fetch_papers_crossref(row['ORCID'])
    return fetch_google_news(row['Name'], papers)

def main():
    print(f"--- LCDS Strict Tracker (Last 6 Months: {START_DATE}+) ---")
    
    # 1. LOAD & PURGE OLD DATA
    if os.path.exists(OUTPUT_FILE):
        try:
            df_ex = pd.read_csv(OUTPUT_FILE)
            initial_count = len(df_ex)
            
            # A. Date Filter (Keep only >= CUTOFF_DATE)
            df_ex['Date Available Online'] = pd.to_datetime(df_ex['Date Available Online'], errors='coerce')
            df_ex = df_ex[df_ex['Date Available Online'].dt.date >= CUTOFF_DATE]
            
            # B. Junk Filter (Retroactive)
            clean_mask = []
            for _, row in df_ex.iterrows():
                is_bad = is_junk_hit(str(row.get('LCDS Mention','')), str(row.get('Snippet','')), str(row.get('Link','')))
                clean_mask.append(not is_bad)
            df_ex = df_ex[clean_mask]
            
            # Save the clean slate immediately
            df_ex.to_csv(OUTPUT_FILE, index=False)
            existing_links = set(df_ex['Link'].astype(str))
            
            print(f"PURGE COMPLETE: Reduced DB from {initial_count} to {len(df_ex)} records (Removed old/junk).")
            
        except Exception as e:
            print(f"Error cleaning DB: {e}")
            df_ex = pd.DataFrame()
            existing_links = set()
    else: 
        df_ex = pd.DataFrame()
        existing_links = set()

    # 2. LOAD PEOPLE
    try:
        df_p = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_p.columns: df_p = df_p[df_p['Status'] != 'Ignore']
        print(f"Loaded {len(df_p)} profiles to scan.")
    except Exception as e: print(e); return

    # 3. RUN SLOW SCAN
    new_records = []
    print("Scanning (5 Workers - Slow Mode)...")
    
    # Reduced workers to ensure thoroughness and avoid blocking
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_person, r): r['Name'] for _, r in df_p.iterrows()}
        for i, f in enumerate(as_completed(futures)):
            try:
                hits = f.result()
                for h in hits:
                    if str(h['Link']) not in existing_links:
                        new_records.append(h)
                        existing_links.add(str(h['Link']))
                
                # Optional progress ticker
                if (i+1) % 5 == 0: print(f"  Processed {i+1}/{len(df_p)}...")
            except: pass

    # 4. SAVE FINAL
    if new_records:
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_ex, df_new], ignore_index=True)
        df_final.drop_duplicates(subset=['Link'], inplace=True)
        
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Success. Added {len(new_records)} new items.")
    else:
        print("No new items found.")

if __name__ == "__main__":
    main()
