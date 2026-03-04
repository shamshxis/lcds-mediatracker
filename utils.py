import feedparser
import requests
import urllib.parse
from bs4 import BeautifulSoup

def clean_html(text):
    """Removes HTML tags from RSS summaries."""
    if not text: return ""
    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text()

def is_relevant(text, keywords):
    """Returns True if ANY keyword is found in the text."""
    if not text: return False
    text_lower = text.lower()
    for k in keywords:
        if k.lower() in text_lower:
            return True
    return False

def fetch_google_news(query, context_keywords=None, strict_filter=False):
    """
    Fetches news from Google News RSS.
    Returns list of dicts with standardized keys.
    """
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-GB&gl=GB&ceid=GB:en"
    
    feed = feedparser.parse(url)
    results = []
    
    for entry in feed.entries:
        title = entry.title
        summary_raw = getattr(entry, 'summary', '')
        summary_clean = clean_html(summary_raw)
        link = entry.link
        published = entry.published
        
        # Context Filter
        full_text = f"{title} {summary_clean}"
        keep = True
        if strict_filter and context_keywords:
            keep = is_relevant(full_text, context_keywords)
            
        if keep:
            results.append({
                "Date": published,
                "Title": title,      # Maps to 'LCDS Mention'
                "Source": entry.source.get('title', 'Unknown'),
                "Link": link,
                "Type": "Media Mention",
                "Name": query        # Will be overwritten if needed
            })
            
    return results

def fetch_openalex_works(orcid, name):
    """
    Uses ORCID to fetch recent works.
    """
    if not orcid or str(orcid) == "nan":
        return [], []
        
    orcid_id = str(orcid).replace("https://orcid.org/", "").strip()
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
            
            # Save for title search
            if title and len(title.split()) > 4:
                paper_titles.append(title)
            
            works_log.append({
                "Date": pub_date,
                "Title": title,     # Maps to 'LCDS Mention'
                "Source": work.get('primary_location', {}).get('source', {}).get('display_name', 'OpenAlex'),
                "Link": work.get('doi', work.get('id')),
                "Type": "Academic Output",
                "Name": name
            })
            
        return works_log, paper_titles
        
    except Exception:
        return [], []
