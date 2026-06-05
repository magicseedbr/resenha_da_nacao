import os
import re
import feedparser
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# Configuration - Blending a direct feed with a high-availability keyword-filtered feed
FEED_SOURCES = {
    "Coluna do Fla": {"url": "https://colunadofla.com/feed/", "filter_keyword": False},
    "UOL Esporte": {"url": "https://rss.uol.com.br/feed/esporte.xml", "filter_keyword": True}
}

OUTPUT_DIR = "./raw_news"
HISTORY_FILE = "processed_links.json"
KEYWORD = "flamengo"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def sanitize_filename(title):
    clean_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    return clean_title.replace(" ", "_")

def fetch_full_text(url):
    """Navigates to the target portal and extracts core text paragraphs."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if response.status_code != 200:
            return ""
            
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        
        clean_paragraphs = []
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 45: 
                clean_paragraphs.append(text)
                
        return "\n\n".join(clean_paragraphs)
    except Exception as e:
        print(f"   [Scraping Error] Failed to parse content: {e}")
        return ""

def fetch_and_store_news():
    history = load_history()
    new_links = []
    
    print(f"[{datetime.now()}] Starting multi-source ingestion pipeline...")
    
    for source_name, source_info in FEED_SOURCES.items():
        feed_url = source_info["url"]
        requires_filter = source_info["filter_keyword"]
        
        print(f"\nScanning source: [{source_name}]")
        
        try:
            response = requests.get(feed_url, headers=HTTP_HEADERS, timeout=10)
            if response.status_code != 200:
                print(f"   [HTTP Error] Could not fetch feed. Status code: {response.status_code}")
                continue
                
            feed = feedparser.parse(response.content)
            
        except Exception as feed_err:
            print(f"   [Network Error] Failed to connect to feed: {feed_err}")
            continue
        
        if not feed.entries:
            print(f"   [Warning] Feed parsing returned 0 entries. Skipping.")
            continue

        valid_entries = []
        
        # Apply local keyword processing layer if the source requires filtering
        for entry in feed.entries:
            title_lower = entry.title.lower()
            summary_lower = entry.get('summary', '').lower()
            
            if requires_filter:
                if KEYWORD in title_lower or KEYWORD in summary_lower:
                    valid_entries.append(entry)
            else:
                valid_entries.append(entry)

        print(f"   Found {len(valid_entries)} relevant articles. Processing top 3 latest items...")
        
        for entry in valid_entries[:3]: 
            link = entry.link
            title = entry.title
            
            if link in history:
                print(f"   -> Skipping (Duplicate): {title[:50]}...")
                continue
                
            print(f"   -> Processing: {title[:50]}...")
            summary = entry.get('summary', '')
            published_date = entry.get('published', 'Unknown Date')
            
            full_text = fetch_full_text(link)
            
            if not full_text or len(full_text) < 200:
                print(f"      [Notice] Full text extraction failed. Using summary fallback.")
                full_text = summary
            else:
                print(f"      [Success] Extracted {len(full_text)} characters.")

            news_data = {
                "title": title,
                "source_name": source_name,
                "source_url": link,
                "published_at": published_date,
                "summary": summary,
                "full_text": full_text,
                "extracted_at": datetime.now().isoformat()
            }
            
            filename = f"{sanitize_filename(source_name)}_\
{sanitize_filename(title[:40])}.json"
            file_path = os.path.join(OUTPUT_DIR, filename)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(news_data, f, ensure_ascii=False, indent=4)
                
            new_links.append(link)

    if new_links:
        history.extend(new_links)
        save_history(history)
        
    print(f"\n[{datetime.now()}] Ingestion loop finished. {len(new_links)} new files stored.")

if __name__ == "__main__":
    fetch_and_store_news()