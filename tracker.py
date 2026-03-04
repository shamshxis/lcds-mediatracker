import pandas as pd
import feedparser
import requests
import urllib.parse
import os
import time
from datetime import datetime, date
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
INPUT_FILE = "lcds_people_orcid_updated.csv"  # Your uploaded file
OUTPUT_FILE = "lcds_media_tracker.csv"        # The file getting generated
START_DATE_FILTER = "2019-09-01"              # Get papers from Sep 2019

# Context keywords to filter noise from Google News
CONTEXT_KEYWORDS = [
    "Oxford", "Leverhulme", "LCDS", "Demographic", "Population", 
    "Sociology", "Nuffield", "Social Science", "Study", "Research",
    "University", "Professor", "Dr"
]

def clean_html(text):
    """Removes HTML tags from RSS summaries."""
    if not text: return ""
    return BeautifulSoup(text, "html.parser").get_text()

def is_relevant(text, keywords):
    """Checks if text contains at least one keyword."""
    if not text: return False
    text_lower = text.lower()
    return any(k.lower() in text_lower for k in keywords)

def fetch_google_news(query, strict_filter=False):
    """Fetches Google News RSS for a query (Name or Title)."""
    encoded = urllib.parse.quote(query)
    # UK News, English
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    
    try:
        feed = feedparser.parse(url)
        results = []
        
        for entry in feed.entries:
            title = entry.title
            summary = clean_html(getattr(entry, 'summary', ''))
            pub_date_str = entry.published
            
            # Parse Date
            try:
                # Common RSS date format
                dt = pd.to_datetime(pub_date_str).date()
            except:
                dt = date.today()

            # Filter Logic (Strict for Names, Loose for Paper Titles)
            full_text = f"{title} {summary}"
            if strict_filter and not is_relevant(full_text, CONTEXT_KEYWORDS):
                continue
                
            results.append({
                "LCDS Mention": title,
                "Summary": summary,
                "Link": entry.link,
                "Date Available Online": dt,
                "Type": "Media Mention",
                "Source": entry.source.get('title', 'Google News')
            })
        return results
    except Exception as e:
        print(f"Error fetching news for {query}: {e}")
        return []

def fetch_openalex(orcid, name):
    """Fetches papers since Sep 2019 via OpenAlex."""
    if pd.isna(orcid) or str(orcid).strip() == "" or str(orcid) == "nan":
        return [], []
    
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id},from_publication_date:{START_DATE_FILTER}&per-page=50"
    
    try:
        r = requests.get(url, headers={'User-Agent': 'LCDS_Tracker/1.0 (mailto:admin@lcds.ox.ac.uk)'})
        if r.status_code != 200:
            return [], []
            
        data = r.json()
        works = []
        titles = []
        
        for item in data.get('results', []):
            title = item.get('title', 'Untitled')
            pub_date = item.get('publication_date') # YYYY-MM-DD
            doi = item.get('doi', item.get('id'))
            
            # Store substantial titles for the Media Search
            if title and len(title.split()) > 4:
                titles.append(title)
                
            works.append({
                "LCDS Mention": title,
                "Summary": f"Type: {item.get('type')}",
                "Link": doi,
                "Date Available Online": pub_date,
                "Type": "Academic Output",
                "Source": "OpenAlex"
            })
        return works, titles
    except Exception as e:
        print(f"Error OpenAlex {name}: {e}")
        return [], []

def main():
    print("--- Starting LCDS Background Tracker ---")
    
    # 1. Load People (Using latin1 to fix UnicodeError)
    try:
        df_people = pd.read_csv(INPUT_FILE, encoding='latin1')
        # Filter out 'Ignore'
        if 'Status' in df_people.columns:
            df_people = df_people[df_people['Status'] != 'Ignore']
        print(f"Loaded {len(df_people)} people to track.")
    except Exception as e:
        print(f"CRITICAL: Could not load {INPUT_FILE}. Error: {e}")
        return

    # 2. Load Existing Database (to avoid duplicates)
    if os.path.exists(OUTPUT_FILE):
        try:
            df_existing = pd.read_csv(OUTPUT_FILE)
            existing_links = set(df_existing['Link'].astype(str))
            print(f"Loaded {len(df_existing)} existing records.")
        except:
            df_existing = pd.DataFrame()
            existing_links = set()
    else:
        df_existing = pd.DataFrame()
        existing_links = set()

    new_records = []

    # 3. Iterate & Fetch
    for idx, row in df_people.iterrows():
        name = row['Name']
        orcid = row['ORCID']
        print(f"Scanning: {name}")

        # A. Fetch Media (Name Search - Strict Filter)
        news_items = fetch_google_news(name, strict_filter=True)
        for item in news_items:
            item['Name'] = name
            if str(item['Link']) not in existing_links:
                new_records.append(item)
                existing_links.add(str(item['Link']))

        # B. Fetch Academic (OpenAlex)
        works, titles = fetch_openalex(orcid, name)
        for work in works:
            work['Name'] = name
            if str(work['Link']) not in existing_links:
                new_records.append(work)
                existing_links.add(str(work['Link']))
        
        # C. Fetch Media via Paper Title (Loose Filter)
        # Limit to top 2 recent titles to save execution time
        for title in titles[:2]:
            paper_news = fetch_google_news(f'"{title}"', strict_filter=False)
            for item in paper_news:
                item['Name'] = name
                item['Type'] = "Media (via Paper)"
                item['Summary'] = f"Found via paper: {title}. {item['Summary']}"
                
                if str(item['Link']) not in existing_links:
                    new_records.append(item)
                    existing_links.add(str(item['Link']))

        # Rate limiting
        time.sleep(0.5)

    # 4. Save Updates
    if new_records:
        df_new = pd.DataFrame(new_records)
        
        # Add 'Year' Column
        df_new['Year'] = pd.to_datetime(df_new['Date Available Online'], errors='coerce').dt.year
        
        # Combine
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        
        # Sort by Date Descending
        df_final.sort_values(by='Date Available Online', ascending=False, inplace=True)
        
        # Save
        df_final.to_csv(OUTPUT_FILE, index=False)
        print(f"SUCCESS: Added {len(new_records)} new records. Saved to {OUTPUT_FILE}.")
    else:
        print("No new records found.")

if __name__ == "__main__":
    main()
