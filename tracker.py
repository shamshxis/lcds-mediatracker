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
START_DATE_FILTER = "2023-01-01"

CONTEXT_KEYWORDS = ["Oxford", "Leverhulme", "LCDS", "Demographic", "Population", "Sociology", "Nuffield", "Social Science", "Study", "Research"]
OXFORD_RSS_URLS = ["https://www.ox.ac.uk/feeds/rss/news", "https://www.oxfordmail.co.uk/news/rss/"]

def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

def is_relevant(text, keywords):
    if not text: return False
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)

# --- ROBUST ENGINES ---
def fetch_news_rss(query, engine="google", strict_filter=False):
    try:
        # Safe encoding for names with accents/special characters
        encoded = urllib.parse.quote(str(query))
        url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en" if engine == "google" else f"https://www.bing.com/news/search?q={encoded}&format=rss"
        
        # Explicit timeout and User-Agent
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        feed = feedparser.parse(resp.content)
        
        results = []
        for entry in feed.entries:
            title = entry.title
            summary = clean_html(getattr(entry, 'summary', ''))
            try:
                dt = pd.to_datetime(entry.published).date()
            except:
                dt = date.today()
            if strict_filter and not is_relevant(f"{title} {summary}", CONTEXT_KEYWORDS):
                continue
            results.append({
                "LCDS Mention": title, "Summary": summary[:300],
                "Link": entry.link, "Date Available Online": dt,
                "Type": "Media Mention", "Source": f"{engine.capitalize()} News", "Name": query
            })
        return results
    except Exception as e:
        print(f"  [!] {engine.capitalize()} Error for {query}: {e}")
        return []

def fetch_crossref_events(doi, author_name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    url = f"https://api.eventdata.crossref.org/v1/events?obj-id={clean_doi}&rows=5&mailto=admin@lcds.ox.ac.uk"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code != 200: return []
        events = []
        for item in r.json().get('message', {}).get('events', []):
            if item.get('source_id') in ['newsfeed', 'wikipedia', 'reddit-links']:
                events.append({
                    "LCDS Mention": f"Mention in {item['source_id'].capitalize()}",
                    "Summary": f"Paper discussed on {item['source_id']}.",
                    "Link": item.get('subj', {}).get('pid') or item.get('subj', {}).get('url'),
                    "Date Available Online": item.get('occurred_at', '')[:10],
                    "Type": "Impact / Social", "Source": f"Crossref ({item['source_id']})", "Name": author_name
                })
        return events
    except: return []

def fetch_altmetric_free(doi, author_name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    try:
        r = requests.get(f"https://api.altmetric.com/v1/doi/{clean_doi}", timeout=5)
        if r.status_code != 200: return []
        events = []
        posts = r.json().get('posts', {})
        if 'news' in posts:
            for post in posts['news'][:2]:
                events.append({
                    "LCDS Mention": post.get('name', 'News Mention'),
                    "Summary": post.get('summary', 'News via Altmetric'),
                    "Link": post.get('url'), "Date Available Online": post.get('posted_on', '')[:10],
                    "Type": "Media (Altmetric)", "Source": "Altmetric", "Name": author_name
                })
        return events
    except: return []

def get_author_works(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid).lower() == 'nan': return [], []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    try:
        r = requests.get(f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=10", timeout=12)
        data = r.json()
        dois, titles = [], []
        for item in data.get('results', []):
            if item.get('doi'): dois.append(item['doi'])
            if item.get('title') and len(item['title'].split()) > 5: titles.append(item['title'])
        return dois, titles
    except Exception as e:
        print(f"  [!] OpenAlex Error: {e}")
        return [], []

# --- MAIN ---
def main():
    start_time = time.time()
    print("--- LCDS Media Tracker Bot: Execution Started ---")
    
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        df_people = df_people[df_people['Status'] != 'Ignore']
        print(f"Loaded {len(df_people)} people.")
    except Exception as e:
        print(f"FATAL: Load CSV Error: {e}"); return

    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            existing_links = set(df_existing['Link'].astype(str))
            print(f"Loaded {len(df_existing)} existing records.")
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []

    # 1. RSS FEEDS
    print("Checking Oxford RSS Feeds...")
    for feed in OXFORD_RSS_URLS:
        for i in fetch_news_rss(feed, "google", strict_filter=True):
            if str(i['Link']) not in existing_links:
                i['Name'] = "LCDS General"
                new_records.append(i); existing_links.add(str(i['Link']))

    # 2. PEOPLE LOOP
    for idx, row in df_people.iterrows():
        name, orcid = str(row['Name']), row['ORCID']
        
        # Check for global script timeout (15 mins)
        if time.time() - start_time > 900:
            print("!!! REACHED 15 MINUTE LIMIT: Saving partial results and exiting...")
            break

        print(f"[{idx+1}/{len(df_people)}] Processing: {name}")
        
        # Name News
        new_records.extend([h for h in fetch_news_rss(name, "google", True) if str(h['Link']) not in existing_links])
        new_records.extend([h for h in fetch_news_rss(name, "bing", True) if str(h['Link']) not in existing_links])

        # Impact mentions
        if pd.notna(orcid) and str(orcid).lower() != 'nan':
            dois, titles = get_author_works(orcid)
            for doi in dois:
                for ev in fetch_crossref_events(doi, name) + fetch_altmetric_free(doi, name):
                    if str(ev['Link']) not in existing_links:
                        new_records.append(ev); existing_links.add(str(ev['Link']))
            
            for title in titles[:1]: # Limit to 1 paper title to save time
                for h in fetch_news_rss(f'"{title}"', "google", False):
                    if str(h['Link']) not in existing_links:
                        h.update({"Type": "Media (via Paper)", "Name": name})
                        new_records.append(h); existing_links.add(str(h['Link']))
        
        time.sleep(0.5)

    print(f"\nScan Complete. New entries found: {len(new_records)}")
    if new_records:
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        df_final['Year'] = df_final['Date Available Online'].dt.year
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"SUCCESS: {OUTPUT_FILE} has been updated.")
    else:
        print("No new data to record.")

if __name__ == "__main__":
    main()
