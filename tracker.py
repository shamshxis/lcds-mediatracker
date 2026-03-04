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

CONTEXT_KEYWORDS = [
    "Oxford", "Leverhulme", "LCDS", "Demographic", "Population", 
    "Sociology", "Nuffield", "Social Science", "Study", "Research"
]

OXFORD_RSS_URLS = [
    "https://www.ox.ac.uk/feeds/rss/news",
    "https://www.oxfordmail.co.uk/news/rss/",
]

def clean_html(text):
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

def is_relevant(text, keywords):
    if not text: return False
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)

# --- SEARCH ENGINES ---
def fetch_news_rss(query, engine="google", strict_filter=False):
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en" if engine == "google" else f"https://www.bing.com/news/search?q={encoded}&format=rss"
    source_label = "Google News" if engine == "google" else "Bing News"
    try:
        feed = feedparser.parse(url)
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
                "LCDS Mention": title, "Summary": summary[:300] + "...",
                "Link": entry.link, "Date Available Online": dt,
                "Type": "Media Mention", "Source": source_label, "Name": query
            })
        return results
    except: return []

def fetch_crossref_events(doi, author_name):
    if not doi or "doi.org" not in str(doi): return []
    clean_doi = str(doi).split("doi.org/")[-1].strip()
    url = f"https://api.eventdata.crossref.org/v1/events?obj-id={clean_doi}&rows=5&mailto=admin@lcds.ox.ac.uk"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200: return []
        events = []
        for item in r.json().get('message', {}).get('events', []):
            if item.get('source_id') in ['newsfeed', 'wikipedia', 'reddit-links', 'web']:
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
        r = requests.get(f"https://api.altmetric.com/v1/doi/{clean_doi}", timeout=3)
        if r.status_code != 200: return []
        data = r.json()
        events = []
        if 'news' in data.get('posts', {}):
            for post in data['posts']['news'][:3]:
                events.append({
                    "LCDS Mention": post.get('name', 'News Mention'),
                    "Summary": post.get('summary', 'News tracked by Altmetric'),
                    "Link": post.get('url'), "Date Available Online": post.get('posted_on', '')[:10],
                    "Type": "Media (Altmetric)", "Source": "Altmetric", "Name": author_name
                })
        return events
    except: return []

def get_author_works(orcid):
    if pd.isna(orcid) or str(orcid).strip() == "": return [], []
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    try:
        r = requests.get(f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=15", timeout=10)
        data = r.json()
        dois, titles = [], []
        for item in data.get('results', []):
            if item.get('doi'): dois.append(item['doi'])
            if item.get('title') and len(item['title'].split()) > 5: titles.append(item['title'])
        return dois, titles
    except: return [], []

# --- MAIN WORKFLOW ---
def main():
    print("--- Starting LCDS Media Tracker ---")
    try:
        # Load academic list with latin1 to handle Excel CSV characters
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        df_people = df_people[df_people['Status'] != 'Ignore']
    except Exception as e:
        print(f"Error loading CSV: {e}"); return

    # Load existing tracking database if it exists
    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            existing_links = set(df_existing['Link'].astype(str))
        except: df_existing = pd.DataFrame()
    else: df_existing = pd.DataFrame()

    new_records = []

    # 1. SCAN GLOBAL RSS FEEDS
    for feed in OXFORD_RSS_URLS:
        for i in fetch_news_rss(feed, "google", strict_filter=True):
            i['Name'] = "LCDS General"
            if str(i['Link']) not in existing_links:
                new_records.append(i); existing_links.add(str(i['Link']))

    # 2. SCAN PEOPLE AND PUBLICATIONS
    for _, row in df_people.iterrows():
        name, orcid = row['Name'], row['ORCID']
        print(f"Scanning: {name}")
        
        # Name Search in Google and Bing
        for hit in fetch_news_rss(name, "google", strict_filter=True) + fetch_news_rss(name, "bing", strict_filter=True):
            if str(hit['Link']) not in existing_links:
                new_records.append(hit); existing_links.add(str(hit['Link']))

        # Impact and Paper title mentions
        if pd.notna(orcid):
            dois, titles = get_author_works(orcid)
            for doi in dois:
                for event in fetch_crossref_events(doi, name) + fetch_altmetric_free(doi, name):
                    if str(event['Link']) not in existing_links:
                        new_records.append(event); existing_links.add(str(event['Link']))
            for title in titles[:2]:
                for hit in fetch_news_rss(f'"{title}"', "google", strict_filter=False):
                    hit.update({"Type": "Media (via Paper)", "Name": name})
                    if str(hit['Link']) not in existing_links:
                        new_records.append(hit); existing_links.add(str(hit['Link']))
        time.sleep(0.5)

    # 3. CONSOLIDATE, UNIFY DATE TYPES, AND SAVE
    if new_records:
        df_new = pd.DataFrame(new_records)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)

        # UNIFY DATE TYPES: Convert column to actual datetimes so sorting works
        df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
        
        # Re-derive Year from valid dates
        df_final['Year'] = df_final['Date Available Online'].dt.year
        
        # Sort by Date Available Online (Descending)
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        
        # Format back to simple date string (YYYY-MM-DD) for clean CSV display
        df_final['Date Available Online'] = df_final['Date Available Online'].dt.date
        
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"SUCCESS: Added {len(new_records)} records.")
    else:
        print("No new records.")

if __name__ == "__main__":
    main()
