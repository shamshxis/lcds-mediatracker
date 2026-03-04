# utils.py
import feedparser
import requests
import pandas as pd
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
import re

def clean_html(text):
    """Removes HTML tags from RSS summaries."""
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text()

def is_relevant(text, keywords):
    """
    The Noise Filter: Returns True if ANY keyword is found in the text.
    Case-insensitive.
    """
    if not text: return False
    text_lower = text.lower()
    for k in keywords:
        if k.lower() in text_lower:
            return True
    return False

def fetch_google_news(query, context_keywords):
    """
    Fetches news from Google News RSS for a specific query.
    Applies 'Context Validator' to reduce noise.
    """
    encoded_query = urllib.parse.quote(query)
    # targeting UK news (gl=GB) in English (hl=en-GB)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
    
    feed = feedparser.parse(url)
    results = []
    
    for entry in feed.entries:
        title = entry.title
        link = entry.link
        published = entry.published
        # RSS summary is often HTML
        summary_raw = getattr(entry, 'summary', '')
        summary_clean = clean_html(summary_raw)
        
        # COMBINED TEXT for filtering
        full_text = f"{title} {summary_clean}"
        
        # FILTER: Only keep if it matches context keywords
        if is_relevant(full_text, context_keywords):
            results.append({
                "Date": published,
                "Entity/Author": query,
                "Title": title,
                "Source": entry.source.get('title', 'Unknown'),
                "Link": link,
                "Type": "Media",
                "Snippet": summary_clean[:150] + "..."
            })
            
    return results

def fetch_openalex_talks(author_name):
    """
    Uses OpenAlex to find 'other' works (preprints, paratext) 
    that might indicate talks or keynotes.
    """
    # Search for the author first to get their ID (simplified for this demo)
    # In a production app, you might want to cache Author IDs.
    
    # We search works directly by author name to save an API call, 
    # filtering for 'other' or 'paratext' types.
    url = f"https://api.openalex.org/works?filter=author.search:{author_name},type:other&per-page=5"
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return []
        
        data = r.json()
        talks = []
        
        for work in data.get('results', []):
            # Try to grab a conference name if available
            source = "OpenAlex"
            if work.get('primary_location') and work['primary_location'].get('source'):
                 source = work['primary_location']['source'].get('display_name', 'OpenAlex')

            talks.append({
                "Date": work.get('publication_date', 'Unknown'),
                "Entity/Author": author_name,
                "Title": work.get('title', 'Untitled'),
                "Source": source,
                "Link": work.get('doi', work.get('id')),
                "Type": "Talk/Keynote (Potential)",
                "Snippet": "Sourced via OpenAlex type:other"
            })
        return talks
        
    except Exception as e:
        print(f"OpenAlex Error for {author_name}: {e}")
        return []
