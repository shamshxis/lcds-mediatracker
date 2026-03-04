import pandas as pd
import feedparser
import requests
import urllib.parse
import os
import time
from datetime import datetime, date
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
INPUT_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
START_DATE_FILTER = "2023-01-01"  # Adjust start date as needed

# 1. CONTEXT KEYWORDS (Noise Filter)
CONTEXT_KEYWORDS = [
    "Oxford", "Leverhulme", "LCDS", "Demographic", "Population", 
    "Sociology", "Nuffield", "Social Science", "Study", "Research",
    "University", "Professor", "Dr", "Scientist"
]

# 2. OFFICIAL FEEDS
OXFORD_RSS_URLS = [
    "https://www.ox.ac.uk/feeds/rss/news",
    "https://www.oxfordmail.co.uk/news/rss/",
    "https://www.sociology.ox.ac.uk/news/rss",
]

# --- HELPERS ---
def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

def is_relevant(text, keywords):
    if not text: return False
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)

# --- ENGINE 1: NEWS RSS (Google & Bing) ---
def fetch_news_rss(query, engine="google", strict_filter=False):
    encoded = urllib.parse.quote(query)
    
    if engine == "google":
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
        source_label = "Google News"
    else: 
        url = f"https://www.bing.com/news/search?q={encoded}&format=rss"
        source_label = "Bing News"

    try:
        feed = feedparser.parse(url) # Standard user agent usually fine for RSS
        results = []
        for entry in feed.entries:
            title = entry.title
            summary = clean_html(getattr(entry, 'summary', ''))
            link = entry.link
            
            try:
                dt = pd.to_datetime(entry.published).date()
            except:
                dt = date.today()

            full_text = f"{title} {summary}"
            if strict_filter and not is_relevant(full_text, CONTEXT_KEYWORDS):
                continue
            
            results.append({
                "LCDS Mention": title,
                "Summary": summary[:300] + "...",
                "Link": link,
                "Date Available Online": dt,
                "Type": "Media Mention",
                "Source": source_label,
                "Name": query
            })
        return results
    except Exception:
        return []

# --- ENGINE 2: CROSSREF EVENT DATA (Free & Open) ---
def fetch_crossref_events(doi, author_name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    
    url = f"https://api.eventdata.crossref.org/v1/events?obj-id={clean_doi}&rows=5&mailto=admin@lcds.ox.ac.uk"
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200: return []
        
        data = r.json()
        events = []
        for item in data.get('message', {}).get('events', []):
            source_id = item.get('source_id')
            if source_id in ['newsfeed', 'wikipedia', 'reddit-links', 'web']:
                subj = item.get('subj', {})
                link = subj.get('pid') or subj.get('url')
                
                events.append({
                    "LCDS Mention": f"Mention in {source_id.capitalize()}",
                    "Summary": f"Paper ({clean_doi}) discussed on {source_id}.",
                    "Link": link,
                    "Date Available Online": item.get('occurred_at', '')[:10],
                    "Type": "Impact / Social",
                    "Source": f"Crossref ({source_id})",
                    "Name": author_name
                })
        return events
    except Exception:
        return []

# --- ENGINE 3: ALTMETRIC FREE API (Backup) ---
def fetch_altmetric_free(doi, author_name):
    """
    Queries the free Altmetric API for basic stats and links.
    Fail-safe: Ignores errors silently.
    """
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    
    # API Endpoint for specific DOI
    url = f"https://api.altmetric.com/v1/doi/{clean_doi}"
    
    try:
        r = requests.get(url, timeout=3) # Short timeout to not slow down script
        if r.status_code != 200: return [] # 404 means no data or blocked
        
        data = r.json()
        events = []
        
        # Check if there are ANY posts
        if data.get('posts'):
            # Extract News if available
            if 'news' in data['posts']:
                for post in data['posts']['news'][:3]: # Limit to top 3
                    events.append({
                        "LCDS Mention": post.get('name', 'News Mention'),
                        "Summary": post.get('summary', 'News mention tracked by Altmetric'),
                        "Link": post.get('url'),
                        "Date Available Online": post.get('posted_on', '')[:10],
                        "Type": "Media (Altmetric)",
                        "Source": "Altmetric",
                        "Name": author_name
                    })
                    
            # Extract Blogs if available
            if 'blogs' in data['posts']:
                for post in data['posts']['blogs'][:2]:
                    events.append({
                        "LCDS Mention": post.get('title', 'Blog Mention'),
                        "Summary": post.get('summary', 'Blog mention tracked by Altmetric'),
                        "Link": post.get('url'),
                        "Date Available Online": post.get('posted_on', '')[:10],
                        "Type": "Blog (Altmetric)",
                        "Source": "Altmetric",
                        "Name": author_name
                    })
        return events
    except Exception:
        return []

# --- ENGINE 4: OPENALEX (Discovery) ---
def get_author_works(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "": return [], []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=15"
    
    try:
        r = requests.get(url, headers={'User-Agent': 'LCDS_Tracker/1.0'})
        if r.status_code != 200: return [], []
        data = r.json()
        dois = []
        titles = []
        for item in data.get('results', []):
            if item.get('doi'): dois.append(item['doi'])
            t = item.get('title', '')
            if t and len(t.split()) > 5: titles.append(t)
        return dois, titles
    except:
        return [], []

# --- MAIN WORKFLOW ---
def main():
    print("--- Starting LCDS Media Tracker ---")
    
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        if 'Status' in df_people.columns:
            df_people = df_people[df_people['Status'] != 'Ignore']
    except Exception as e:
        print(f"CRITICAL: Could not load CSV. {e}")
        return

    # Load Existing Data
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            existing_links = set(df_existing['Link'].astype(str))
        except:
            df_existing = pd.DataFrame()
    else:
        df_existing = pd.DataFrame()

    new_records = []

    # 1. OFFICIAL FEEDS
    print("Checking Official Feeds...")
    for feed in OXFORD_RSS_URLS:
        items = fetch_news_rss(feed, "google", strict_filter=True)
        for i in items:
            i['Name'] = "LCDS General"
            if str(i['Link']) not in existing_links:
                new_records.append(i)
                existing_links.add(str(i['Link']))

    # 2. PEOPLE SCAN
    for idx, row in df_people.iterrows():
        name = row['Name']
        orcid = row['ORCID']
        print(f"Scanning: {name}")

        # A. Name Search (Google & Bing)
        media_hits = fetch_news_rss(name, "google", strict_filter=True)
        media_hits += fetch_news_rss(name, "bing", strict_filter=True)
        
        for hit in media_hits:
            if str(hit['Link']) not in existing_links:
                new_records.append(hit)
                existing_links.add(str(hit['Link']))

        # B. Paper Search (via OpenAlex DOIs)
        if pd.notna(orcid):
            dois, titles = get_author_works(orcid)
            
            for doi in dois:
                # 1. Check Crossref (Primary Free Source)
                events = fetch_crossref_events(doi, name)
                
                # 2. Check Altmetric (Backup Source)
                alt_events = fetch_altmetric_free(doi, name)
                
                # Merge lists
                for event in events + alt_events:
                    if str(event['Link']) not in existing_links:
                        new_records.append(event)
                        existing_links.add(str(event['Link']))
            
            # 3. Check Paper Titles in News
            for title in titles[:2]: # Top 2 recent only
                title_hits = fetch_news_rss(f'"{title}"', "google", strict_filter=False)
                for hit in title_hits:
                    hit['Type'] = "Media (via Paper)"
                    hit['Name'] = name
                    if str(hit['Link']) not in existing_links:
                        new_records.append(hit)
                        existing_links.add(str(hit['Link']))
        
        time.sleep(1) # Respect rate limits

    # 3. SAVE
    if new_records:
        df_new = pd.DataFrame(new_records)
        df_new['Year'] = pd.to_datetime(df_new['Date Available Online'], errors='coerce').dt.year
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"SUCCESS: Added {len(new_records)} new records.")
    else:
        print("No new records found.")

if __name__ == "__main__":
    main()
