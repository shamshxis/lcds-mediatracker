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

# Time Limit: Look back 1 year
LOOKBACK_DAYS = 365 
START_DATE = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

# STRICT CONTEXT KEYWORDS
VALID_CONTEXTS = [
    "university of oxford", "oxford university", 
    "leverhulme centre", "lcds", 
    "nuffield college", "department of sociology", 
    "demographic science", "demography"
]

# JUNK KEYWORDS
JUNK_TERMS = [
    "obituary", "funeral", "death notice", "in memoriam", 
    "dignity memorial", "passed away", "survived by", 
    "class of", "high school", "football", "basketball"
]

# ACADEMIC DOMAINS TO BLOCK (We only want Media/Blogs, not the papers themselves)
ACADEMIC_DOMAINS = [
    "doi.org", "sciencedirect.com", "wiley.com", "springer.com", 
    "tandfonline.com", "sagepub.com", "oup.com", "cambridge.org", 
    "jstor.org", "ncbi.nlm.nih.gov", "arxiv.org", "researchgate.net", 
    "academia.edu", "mdpi.com", "frontiersin.org", "plos.org"
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

def is_junk(text):
    text = text.lower()
    if any(junk in text for junk in JUNK_TERMS): return True
    return False

def is_academic_source(url):
    """
    Returns True if the URL points to a direct academic paper or repository.
    We want to SKIP these because we have a separate Publications Tracker.
    """
    url = url.lower()
    
    # 1. Block File Types
    if url.endswith(".pdf") or url.endswith(".doc") or url.endswith(".docx"):
        return True
        
    # 2. Block Academic Publishers
    if any(domain in url for domain in ACADEMIC_DOMAINS):
        return True
        
    return False

def validate_hit(entry, name, paper_titles):
    """
    QUALITY CONTROL:
    1. Discard Junk (Obits).
    2. Discard Academic Sources (Papers).
    3. Accept if Paper Title matches.
    4. Accept if Name + Context matches.
    """
    title = entry.title.lower()
    snippet = extract_snippet(entry).lower()
    content = title + " " + snippet
    link = entry.link
    
    # 1. Junk Filter
    if is_junk(content): return False
    
    # 2. Source Filter (No Papers)
    if is_academic_source(link): return False
    
    # 3. Paper Title Match (Highest Priority)
    if paper_titles:
        for p_title in paper_titles:
            if p_title and len(p_title) > 20 and p_title.lower() in content:
                return True

    # 4. Person + Institution Match
    name_lower = name.lower()
    if name_lower in content:
        if any(ctx in content for ctx in VALID_CONTEXTS):
            return True
            
    return False

# --- ENGINE 1: CROSSREF (Primary Source for Paper Titles) ---
def fetch_papers_crossref(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    
    # Fetch recent papers
    url = f"https://api.crossref.org/works?filter=orcid:{orcid_id},from-pub-date:{START_DATE}&rows=20&mailto=admin@lcds.ox.ac.uk"
    
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        papers = []
        for item in data.get('message', {}).get('items', []):
            title = item.get('title', [''])[0] if item.get('title') else ""
            doi = item.get('DOI')
            if title:
                papers.append({'title': title, 'doi': doi})
        return papers
    except: return []

# --- ENGINE 2: OPENALEX (Backup Source) ---
def fetch_papers_openalex(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE}&per-page=20"
    
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        papers = []
        for item in data.get('results', []):
            title = item.get('title')
            doi = item.get('doi')
            if title:
                papers.append({'title': title, 'doi': doi})
        return papers
    except: return []

def get_combined_papers(orcid):
    # Merge Crossref & OpenAlex, deduplicating by normalized title
    cr = fetch_papers_crossref(orcid)
    oa = fetch_papers_openalex(orcid)
    
    seen = set()
    unique = []
    for p in cr + oa:
        norm = "".join(filter(str.isalnum, p['title'].lower()))
        if norm not in seen and len(norm) > 10:
            seen.add(norm)
            unique.append(p)
    return unique

# --- ENGINE 3: GOOGLE NEWS (Smart Search) ---
def fetch_google_news(name, paper_titles):
    results = []
    
    # A. Person Search (Name + Oxford/LCDS)
    context_query = ' OR '.join([f'"{c}"' for c in ["University of Oxford", "LCDS", "Nuffield College", "Leverhulme Centre"]])
    person_query = f'"{name}" AND ({context_query}) after:{START_DATE}'
    
    # B. Paper Search (Titles)
    queries = [person_query]
    if paper_titles:
        for p in paper_titles:
            clean_t = p['title'].replace(":", "").replace("-", " ")
            queries.append(f'"{clean_t}" after:{START_DATE}')
            
    for q in queries:
        encoded = urllib.parse.quote(q)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
        
        try:
            resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (LCDS Bot)'})
            feed = feedparser.parse(resp.content)
            
            for entry in feed.entries:
                snippet = extract_snippet(entry)
                
                # VALIDATION (Rejects Junk + Academic Papers)
                if not validate_hit(entry, name, [p['title'] for p in paper_titles]):
                    continue

                # TAGGING
                low_text = (entry.title + " " + snippet).lower()
                if any(x in low_text for x in ["keynote", "plenary", "panelist", "conference"]):
                    item_type = "Conference / Talk"
                elif any(x in low_text for x in ["study", "research", "paper", "journal"]):
                    item_type = "Media (Research)"
                else:
                    item_type = "Media Mention"

                results.append({
                    "LCDS Mention": entry.title,
                    "Snippet": snippet[:400],
                    "Link": entry.link,
                    "Date Available Online": pd.to_datetime(entry.published).date(),
                    "Type": item_type,
                    "Source": "Google News",
                    "Name": name
                })
        except: pass
        
    return results

# --- MAIN WORKER ---
def process_person(row):
    name = row['Name']
    orcid = row['ORCID']
    person_results = []
    
    # 1. Get Papers
    papers = get_combined_papers(orcid)
    
    # 2. Search Media (News/Blogs only)
    person_results.extend(fetch_google_news(name, papers))
    
    # 3. (Optional) Altmetric would go here if you decide to add it back
            
    return person_results

# --- MAIN ---
def main():
    print(f"--- LCDS Media-Only Tracker (No Raw Papers) ---")
    
    try:
        df_p = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_p.columns: df_p = df_p[df_p['Status'] != 'Ignore']
        print(f"Loaded {len(df_p)} profiles.")
    except Exception as e: print(e); return

    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            # Filter out any old junk
            df_existing = df_existing[~df_existing['Snippet'].str.contains("Dignity Memorial", case=False, na=False)]
            existing_links = set(df_existing['Link'].astype(str))
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []
    print("Scanning (Parallel)...")
    
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
        df_final.drop_duplicates(subset=['Link'], inplace=True)
        
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"Done. Database has {len(df_final)} media records.")

if __name__ == "__main__":
    main()
