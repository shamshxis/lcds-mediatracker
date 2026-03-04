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

# Time Limit: Look back 1 year
START_DATE = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")

# 1. STRICT CONTEXT (Must appear in the article text for Name matches)
CONTEXT_KEYWORDS = [
    r"university of oxford", r"oxford university", 
    r"leverhulme centre", r"lcds", 
    r"nuffield college", r"department of sociology", 
    r"demographic science", r"demography", r"population studies"
]

# 2. JUNK PATTERNS (Instant Reject)
# Blocks shopping items, obituaries, and generic "Oxford" noise
JUNK_REGEX = re.compile(r"\b(obituary|funeral|death notice|dignity memorial|passed away|survived by|class of \d{4}|high school|football|basketball|microfibre|waterproof|drawstring|bag case|glass organiser|hammock|pub|coin|royal mint)\b", re.IGNORECASE)

# 3. ACADEMIC & PUBLISHER BLOCKLIST
# We want media coverage OF the paper, not the paper itself.
BLOCK_DOMAINS = [
    "doi.org", "sciencedirect.com", "wiley.com", "springer.com", 
    "tandfonline.com", "sagepub.com", "oup.com", "cambridge.org", 
    "jstor.org", "ncbi.nlm.nih.gov", "arxiv.org", "researchgate.net", 
    "academia.edu", "mdpi.com", "frontiersin.org", "plos.org",
    "nature.com", "science.org", "amazon.com", "ebay.com", "etsy.com"
]

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()

def extract_snippet(entry):
    if hasattr(entry, 'summary'): return clean_html(entry.summary)
    if hasattr(entry, 'description'): return clean_html(entry.description)
    return ""

def is_blocked_domain(url):
    url = url.lower()
    if url.endswith(".pdf") or url.endswith(".doc"): return True
    if any(d in url for d in BLOCK_DOMAINS): return True
    return False

# --- NLP LAYER: CONTENT VERIFICATION ---
def verify_content_relevance(url, name):
    """
    NLP LAYER: Visits the link to verify the person or topic is actually there.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) LCDS-Bot/1.0'}
        r = requests.get(url, timeout=5, headers=headers)
        if r.status_code != 200: return False 
        
        soup = BeautifulSoup(r.content, 'html.parser')
        for s in soup(["script", "style", "nav", "footer"]): s.decompose()
        text = soup.get_text().lower()
        
        # Check if Name OR Context is present
        if name.lower() in text:
            # If name is found, ensure it's not a false positive by checking context
            for pattern in CONTEXT_KEYWORDS:
                if re.search(pattern, text): return True
        return False
    except: return False # Fail safe

# --- VALIDATION ENGINE ---
def validate_hit(entry, name, paper_titles):
    title = entry.title
    snippet = extract_snippet(entry)
    content_blob = (title + " " + snippet).lower()
    link = entry.link
    
    # 1. Regex Junk Filter (Fast)
    if JUNK_REGEX.search(content_blob): return False
    
    # 2. Domain Block (Fast)
    if is_blocked_domain(link): return False
    
    # 3. Paper Title Match (High Priority - Captures "Nameless" Study Mentions)
    if paper_titles:
        for p_title in paper_titles:
            # Only match specific titles > 20 chars to avoid generic noise
            if p_title and len(p_title) > 20 and p_title.lower() in content_blob:
                return True

    # 4. Name + Context Match
    has_name = name.lower() in content_blob
    has_context = any(re.search(pat, content_blob) for pat in CONTEXT_KEYWORDS)
    
    if has_name and has_context: return True
        
    # 5. NLP Deep Scan (If unsure)
    if has_name: return verify_content_relevance(link, name)

    return False

# --- ENGINES ---
def fetch_papers_crossref(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    oid = str(orcid).replace("https://orcid.org/", "").strip()
    # Fetch papers from last 1 year
    url = f"https://api.crossref.org/works?filter=orcid:{oid},from-pub-date:{START_DATE}&rows=10"
    try:
        r = requests.get(url, timeout=10)
        items = r.json().get('message', {}).get('items', [])
        return [{'title': i.get('title', [''])[0], 'doi': i.get('DOI')} for i in items if i.get('title')]
    except: return []

def fetch_google_news(name, paper_titles):
    # Strategy A: Person + Context
    q_context = f'"{name}" AND ("Oxford" OR "LCDS" OR "Nuffield" OR "Leverhulme") after:{START_DATE}'
    queries = [q_context]
    
    # Strategy B: Paper Titles (Captures "Study published in..." mentions)
    if paper_titles:
        for p in paper_titles[:3]: # Check top 3 recent papers
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
                
                if validate_hit(entry, name, [p['title'] for p in paper_titles]):
                    # Auto-Tagging
                    full_text = (entry.title + " " + extract_snippet(entry)).lower()
                    if any(x in full_text for x in ["keynote", "plenary", "conference", "panel", "talk"]): 
                        tag = "Conference / Talk"
                    elif any(x in full_text for x in ["study", "research", "paper", "journal", "published"]): 
                        tag = "Media (Research)"
                    else: 
                        tag = "Media Mention"

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
    print("--- LCDS Smart Tracker (Regex + NLP + Article Search) ---")
    
    try:
        df_p = pd.read_csv(INPUT_FILE, encoding='latin1')
        df_p = df_p[df_p['Status'] != 'Ignore']
        print(f"Loaded {len(df_p)} profiles.")
    except Exception as e: print(e); return

    # LOAD & CLEAN EXISTING DB
    if os.path.exists(OUTPUT_FILE):
        try:
            df_ex = pd.read_csv(OUTPUT_FILE)
            # Run the Junk Regex on existing data to purge old bad hits
            df_ex = df_ex[~df_ex['LCDS Mention'].astype(str).str.contains(JUNK_REGEX)]
            # Run the Domain Blocklist on existing data
            df_ex = df_ex[~df_ex['Link'].astype(str).str.contains('|'.join(BLOCK_DOMAINS), case=False)]
            
            existing_links = set(df_ex['Link'].astype(str))
            print(f"Cleaned DB. Kept {len(df_ex)} valid records.")
        except: 
            df_ex = pd.DataFrame()
            existing_links = set()
    else: 
        df_ex = pd.DataFrame()
        existing_links = set()

    new_records = []
    print("Scanning (Parallel)...")
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(process_person, r): r['Name'] for _, r in df_p.iterrows()}
        for f in as_completed(futures):
            try:
                hits = f.result()
                for h in hits:
                    if str(h['Link']) not in existing_links:
                        new_records.append(h)
                        existing_links.add(str(h['Link']))
            except: pass

    if new_records:
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_ex, df_new], ignore_index=True)
        df_final.drop_duplicates(subset=['Link'], inplace=True)
        
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Success. Added {len(new_records)} verified items.")
    else:
        if not df_ex.empty:
            df_ex.to_csv(OUTPUT_FILE, index=False)
            print("No new items, but cleaned existing file.")
        else:
            print("No data.")

if __name__ == "__main__":
    main()
