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

# 1. STRICT CONTEXT (Must appear in the article text)
# We use regex word boundaries (\b) to avoid partial matches
CONTEXT_KEYWORDS = [
    r"university of oxford", r"oxford university", 
    r"leverhulme centre", r"lcds", 
    r"nuffield college", r"department of sociology", 
    r"demographic science", r"demography", r"population studies"
]

# 2. JUNK PATTERNS (Instant Reject)
# Blocks shopping items, obituaries, sports, and generic lists
JUNK_REGEX = re.compile(r"\b(obituary|funeral|death notice|dignity memorial|passed away|survived by|class of \d{4}|high school|football|basketball|microfibre|waterproof|drawstring|bag case|glass organiser|hammock)\b", re.IGNORECASE)

# 3. ACADEMIC & JUNK DOMAINS (Blocklist)
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
    NLP LAYER: Fetches the article and checks if the academic is actually mentioned 
    in a relevant context. This filters out "Oxford Cloth" products and random name matches.
    """
    try:
        # Fast timeout (5s) to avoid hanging on slow sites
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) LCDS-Bot/1.0'}
        r = requests.get(url, timeout=5, headers=headers)
        if r.status_code != 200: 
            return False # If we can't read it, we assume it's risky/low-quality
        
        # Parse Text
        soup = BeautifulSoup(r.content, 'html.parser')
        # Kill scripts and styles
        for script in soup(["script", "style", "nav", "footer"]):
            script.decompose()
        
        text = soup.get_text().lower()
        
        # CHECK 1: Is the name there?
        if name.lower() not in text:
            return False
            
        # CHECK 2: Is there a Context Keyword? (Oxford, LCDS, Demography)
        # This kills "David Kirk (Basketball)" because his page won't say "Demography"
        has_context = False
        for pattern in CONTEXT_KEYWORDS:
            if re.search(pattern, text):
                has_context = True
                break
        
        return has_context

    except:
        # If fetch fails (paywall/timeout), we fall back to trusting the snippet 
        # ONLY if the snippet was very strong. For now, strict fail.
        return False

# --- VALIDATION ENGINE ---
def validate_hit(entry, name, paper_titles):
    title = entry.title
    snippet = extract_snippet(entry)
    content_blob = (title + " " + snippet).lower()
    link = entry.link
    
    # 1. Regex Junk Filter (Fast)
    if JUNK_REGEX.search(content_blob): 
        return False
    
    # 2. Domain Block (Fast)
    if is_blocked_domain(link): 
        return False
    
    # 3. Paper Title Match (High Trust)
    # If the snippet mentions a known paper, we trust it without fetching
    if paper_titles:
        for p_title in paper_titles:
            if p_title and len(p_title) > 20 and p_title.lower() in content_blob:
                return True

    # 4. Strict Context on Snippet (Medium Trust)
    # If snippet already has "Name" AND "Oxford", we trust it.
    has_name = name.lower() in content_blob
    has_context = any(re.search(pat, content_blob) for pat in CONTEXT_KEYWORDS)
    
    if has_name and has_context:
        return True
        
    # 5. NLP Deep Scan (Slow / Deep Verification)
    # If snippet is vague, we go to the page and read it.
    if has_name: # Only check links where name at least appears
        return verify_content_relevance(link, name)

    return False

# --- ENGINES ---
def fetch_papers_crossref(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    oid = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.crossref.org/works?filter=orcid:{oid},from-pub-date:{START_DATE}&rows=15"
    try:
        r = requests.get(url, timeout=10)
        items = r.json().get('message', {}).get('items', [])
        return [{'title': i.get('title', [''])[0], 'doi': i.get('DOI')} for i in items if i.get('title')]
    except: return []

def fetch_google_news(name, paper_titles):
    # Search Query: Name + Context OR Paper Title
    # We use a broad search first, then filter strictly with Python
    q_context = f'"{name}" AND ("Oxford" OR "LCDS" OR "Nuffield" OR "Leverhulme") after:{START_DATE}'
    
    queries = [q_context]
    if paper_titles:
        # Check top 3 recent papers
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
                
                # --- RUN VALIDATION ---
                if validate_hit(entry, name, [p['title'] for p in paper_titles]):
                    
                    # Tagging
                    full_text = (entry.title + " " + extract_snippet(entry)).lower()
                    if "keynote" in full_text or "conference" in full_text: tag = "Conference / Talk"
                    elif "study" in full_text or "research" in full_text: tag = "Media (Research)"
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
    # 1. Get recent papers (Crossref)
    papers = fetch_papers_crossref(row['ORCID'])
    # 2. Search Media (Google News with NLP filter)
    return fetch_google_news(row['Name'], papers)

def main():
    print("--- LCDS Smart NLP Tracker (Deep Scan) ---")
    
    try:
        df_p = pd.read_csv(INPUT_FILE, encoding='latin1')
        df_p = df_p[df_p['Status'] != 'Ignore']
        print(f"Loaded {len(df_p)} profiles.")
    except Exception as e: print(e); return

    # Load & CLEAN existing DB
    if os.path.exists(OUTPUT_FILE):
        try:
            df_ex = pd.read_csv(OUTPUT_FILE)
            # NUKE THE JUNK: Remove LCDS General and Shopping items
            clean_mask = (
                (df_ex['Name'] != "LCDS General") & 
                (~df_ex['LCDS Mention'].str.contains("Microfibre|Hammock|Bag|Glass", case=False, na=False)) &
                (~df_ex['Link'].str.contains("nature.com|amazon|ebay", case=False, na=False))
            )
            df_ex = df_ex[clean_mask]
            existing_links = set(df_ex['Link'].astype(str))
            print(f"Cleaned database. Kept {len(df_ex)} valid records.")
        except: 
            df_ex = pd.DataFrame()
            existing_links = set()
    else: 
        df_ex = pd.DataFrame()
        existing_links = set()

    new_records = []
    print("Scanning (This may take longer due to content verification)...")
    
    # 20 Workers to handle the extra network load of NLP verification
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
        
        # Date sorting
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Success. Added {len(new_records)} verified items.")
    else:
        # Save the cleaned version even if no new records found
        if not df_ex.empty:
            df_ex.to_csv(OUTPUT_FILE, index=False)
            print("No new items, but junk was cleaned from existing file.")
        else:
            print("No data.")

if __name__ == "__main__":
    main()
