import pandas as pd
import feedparser
import requests
import urllib.parse
import time
import re
from datetime import datetime, timedelta
import logging

# --- CONFIGURATION ---
INPUT_ORCID_FILE = "lcds_people_orcid_updated.csv"
OUTPUT_FILE = "lcds_media_tracker.csv"
USER_AGENT = "LCDS_Impact_Tracker/2.0 (mailto:admin@lcds.ox.ac.uk)"

# Affiliations to Verify (Lowercase for matching)
AFFILIATIONS = [
    "university of oxford",
    "oxford university",
    "leverhulme centre",
    "leverhulme center",
    "lcds",
    "nuffield college",
    "department of sociology",
    "population studies"
]

# --- SETUP LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_date_window():
    """Returns the start and end date for the ±6 month window."""
    today = datetime.now()
    start_date = today - timedelta(days=180)
    end_date = today + timedelta(days=180)
    return start_date, end_date

def normalize_date(date_str):
    """Attempts to parse various date formats into YYYY-MM-DD."""
    if not date_str:
        return None
    try:
        return pd.to_datetime(date_str, utc=True).strftime('%Y-%m-%d')
    except:
        return None

def verify_affiliation(text, name):
    """
    NLP-lite: Checks if the text contains the academic's name AND a valid affiliation.
    """
    text_lower = text.lower()
    
    # 1. Check strict name match (to avoid partial matches like 'Jen' for 'Jennifer')
    if name.lower() not in text_lower:
        return False
        
    # 2. Define valid affiliations for this person
    valid_affiliations = AFFILIATIONS.copy()
    if "melinda mills" in name.lower():
        valid_affiliations.append("university of groningen")
        valid_affiliations.append("groningen university")

    # 3. Scan text for any valid affiliation
    for aff in valid_affiliations:
        if aff in text_lower:
            return True
            
    return False

def fetch_crossref_pubs(orcid):
    """
    Fetches publications from Crossref for the specified ORCID (±6 months).
    """
    if not orcid or str(orcid) == "nan":
        return []

    start_date, end_date = get_date_window()
    
    # Crossref API (Polite Pool)
    url = f"https://api.crossref.org/works?filter=orcid:{orcid},from-pub-date:{start_date.strftime('%Y-%m-%d')},until-pub-date:{end_date.strftime('%Y-%m-%d')}"
    headers = {"User-Agent": USER_AGENT}
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            items = data.get('message', {}).get('items', [])
            pubs = []
            for item in items:
                title = item.get('title', [''])[0]
                # Clean title (remove newlines, extra spaces)
                title = " ".join(title.split())
                if title:
                    pubs.append({
                        "Title": title,
                        "DOI": item.get('DOI', ''),
                        "Date": "/".join(map(str, item.get('created', {}).get('date-parts', [[0,0,0]])[0])),
                        "Type": "Publication"
                    })
            return pubs
    except Exception as e:
        logging.error(f"Crossref error for {orcid}: {e}")
    
    return []

def search_media(query, mode="Name", academic_name=None):
    """
    Searches Google News RSS for the query.
    mode='Name': Strict affiliation check.
    mode='Pub': Loose check (referencing the paper is enough).
    """
    encoded_query = urllib.parse.quote(query)
    # Search GB (UK) news in English
    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
    
    try:
        feed = feedparser.parse(rss_url)
        hits = []
        
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            pub_date = normalize_date(entry.published)
            
            # Combine title and description for context checking
            # Feedparser puts the description in 'summary' usually
            summary = getattr(entry, 'summary', '')
            full_text = f"{title} {summary}"
            
            # FILTERS
            is_match = False
            
            if mode == "Name":
                # Must have Affiliation in text
                if verify_affiliation(full_text, academic_name):
                    is_match = True
            elif mode == "Pub":
                # If searching by Title, we assume the result is relevant if it matches the title query
                # (Google News does the matching, we just trust it matches the title)
                if len(title) > 10: # Avoid noise from short titles
                    is_match = True

            if is_match:
                hits.append({
                    "LCDS Mention": title,
                    "Summary": summary,
                    "Link": link,
                    "Date Available Online": pub_date,
                    "Type": "Media Mention" if mode == "Name" else "Pub Reference",
                    "Source": entry.source.get('title', 'Google News'),
                    "Name": academic_name,
                    "Snippet": full_text[:500] # Save snippet for context
                })
        
        return hits
    except Exception as e:
        logging.error(f"Media search error for {query}: {e}")
        return []

def main():
    print("--- Starting LCDS Media Tracker v2.0 ---")
    
    # 1. LOAD ACADEMICS
    try:
        df_orcid = pd.read_csv(INPUT_ORCID_FILE)
        # Ensure we have Name and ORCID columns. Adjust if your CSV headers differ.
        if 'Name' not in df_orcid.columns: 
            # Fallback if 'Name' isn't there but maybe 'First' and 'Last' are?
            # For now assume 'Name' exists as per your previous script.
            logging.error("Input CSV must have a 'Name' column.")
            return
    except FileNotFoundError:
        logging.error(f"Could not find {INPUT_ORCID_FILE}. Please ensure it exists.")
        return

    all_results = []
    
    # 2. LOAD EXISTING DATA (To prevent deletion)
    try:
        df_existing = pd.read_csv(OUTPUT_FILE)
        existing_links = set(df_existing['Link'].astype(str))
        print(f"Loaded {len(df_existing)} existing records.")
    except:
        df_existing = pd.DataFrame()
        existing_links = set()
        print("No existing tracker file found. Starting fresh.")

    # 3. PROCESSING LOOP
    total_people = len(df_orcid)
    
    for idx, row in df_orcid.iterrows():
        name = row['Name']
        orcid = row.get('ORCID', row.get('orcid')) # Handle case sensitivity
        
        print(f"[{idx+1}/{total_people}] Processing: {name}...")
        
        # A. FETCH PUBLICATIONS (CROSSREF)
        pubs = fetch_crossref_pubs(orcid)
        for p in pubs:
            # Add Publication itself to the tracker? 
            # The user asked to "fill the table with... data only". 
            # Usually we track *mentions* of pubs, but listing the pub itself is good for the "Publication-First" view.
            # We'll add it as a "Publication" type.
            pub_entry = {
                "LCDS Mention": p['Title'],
                "Summary": f"DOI: {p['DOI']}",
                "Link": f"https://doi.org/{p['DOI']}",
                "Date Available Online": normalize_date(p['Date']),
                "Type": "Publication",
                "Source": "Crossref",
                "Name": name,
                "Snippet": f"New publication detected: {p['Title']}"
            }
            # Check for dupes
            if pub_entry['Link'] not in existing_links:
                all_results.append(pub_entry)
                existing_links.add(pub_entry['Link'])

            # B. SEARCH MEDIA FOR THIS PUBLICATION TITLE
            # Only search if title is long enough to be unique (e.g. > 20 chars)
            if len(p['Title']) > 20:
                media_hits = search_media(f'"{p["Title"]}"', mode="Pub", academic_name=name)
                for m in media_hits:
                    if m['Link'] not in existing_links:
                        all_results.append(m)
                        existing_links.add(m['Link'])
                time.sleep(1) # Polite delay

        # C. SEARCH MEDIA FOR ACADEMIC NAME (STRICT AFFILIATION)
        name_hits = search_media(f'"{name}"', mode="Name", academic_name=name)
        for m in name_hits:
            if m['Link'] not in existing_links:
                all_results.append(m)
                existing_links.add(m['Link'])
        
        time.sleep(1) # Polite delay between people

    # 4. MERGE & SAVE
    if all_results:
        df_new = pd.DataFrame(all_results)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_final = df_existing

    # 5. DATE FILTERING (Last 6 Months & Next 6 Months)
    # Convert date to datetime
    df_final['Date Available Online'] = pd.to_datetime(df_final['Date Available Online'], errors='coerce')
    
    start_date, end_date = get_date_window()
    # Filter
    mask = (df_final['Date Available Online'] >= pd.to_datetime(start_date)) & \
           (df_final['Date Available Online'] <= pd.to_datetime(end_date))
    
    df_final_filtered = df_final.loc[mask].copy()
    
    # Sort
    df_final_filtered.sort_values(by='Date Available Online', ascending=False, inplace=True)
    
    # Save
    df_final_filtered.to_csv(OUTPUT_FILE, index=False)
    print(f"Done! Saved {len(df_final_filtered)} records to {OUTPUT_FILE} (Filtered to ±6 months).")

if __name__ == "__main__":
    main()
