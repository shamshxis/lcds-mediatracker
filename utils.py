# utils.py
import feedparser
import requests
import pandas as pd
import urllib.parse
from datetime import datetime
from bs4 import BeautifulSoup
import re

# --- TEXT CLEANING ---
def clean_html(text):
    """Removes HTML tags from RSS summaries."""
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text()

def is_relevant(text, keywords):
    """
    The Noise Filter: Returns True if ANY keyword is found in the text.
    """
    if not text: return False
    text_lower = text.lower()
    for k in keywords:
        if k.lower() in text_lower:
            return True
    return False

# --- GOOGLE NEWS SEARCH ---
def fetch_google_news(query, context_keywords=None, strict_filter=False):
    """
    Fetches news from Google News RSS.
    If 'strict_filter' is True, it requires at least one context keyword to be present.
    """
    encoded_query = urllib.parse.quote(query)
    # UK News, English
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
    
    feed = feedparser.parse(url)
    results = []
    
    for entry in feed.entries:
        title = entry.title
        summary_raw = getattr(entry, 'summary', '')
        summary_clean = clean_html(summary_raw)
        link = entry.link
        published = entry.published
        
        # Determine Relevance
        full_text = f"{title} {summary_clean}"
        keep = True
        if strict_filter and context_keywords:
            keep = is_relevant(full_text, context_keywords)
            
        if keep:
            results.append({
                "Date": published,
                "Query": query,
                "Title": title,
                "Source": entry.source.get('title', 'Unknown'),
                "Link": link,
                "Type": "News Mention",
                "Snippet": summary_clean[:200]
            })
            
    return results

# --- OPENALEX (ACADEMIC DATA) ---
def fetch_openalex_works(orcid, name):
    """
    Uses ORCID to fetch recent works from OpenAlex.
    Returns:
    1. A list of recent 'Talks/Preprints' (for the dashboard)
    2. A list of recent 'Article Titles' (to feed into the News Search)
    """
    if not orcid or str(orcid) == "nan":
        return [], []
        
    # OpenAlex API expects full ORCID URL usually, but works with simple ID in filters
    orcid_id = orcid.replace("https://orcid.org/", "")
    url = f"https://api.openalex.org/works?filter=author.orcid:https://orcid.org/{orcid_id}&sort=publication_date:desc&per-page=5"
    
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return [], []
            
        data = r.json()
        works_log = []
        paper_titles = []
        
        for work in data.get('results', []):
            title = work.get('title', 'Untitled')
            pub_date = work.get('publication_date', '')
            
            # Save title for News Search (only if it's a substantial title, > 4 words)
            if title and len(title.split()) > 4:
                paper_titles.append(title)
            
            # Log as a work record
            works_log.append({
                "Date": pub_date,
                "Query": name,
                "Title": title,
                "Source": work.get('primary_location', {}).get('source', {}).get('display_name', 'OpenAlex'),
                "Link": work.get('doi', work.get('id')),
                "Type": "Academic Output",
                "Snippet": f"Type: {work.get('type', 'article')}"
            })
            
        return works_log, paper_titles
        
    except Exception as e:
        print(f"Error fetching OpenAlex for {name}: {e}")
        return [], []
