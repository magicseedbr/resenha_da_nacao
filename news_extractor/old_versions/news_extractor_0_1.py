import os
import re
import feedparser
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# Configuration - Direct open feed from a major Flamengo portal
FEED_URL = "https://colunadofla.com/feed/"
OUTPUT_DIR = "./raw_news"
HISTORY_FILE = "processed_links.json"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
    """Navigates directly to the source portal and extracts paragraph texts."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=10)
        if response.status_code != 200:
            print(f"   [HTTP Error] Status code: {response.status_code}")
            return ""
            
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        
        clean_paragraphs = []
        for p in paragraphs:
            text = p.get_text().strip()
            # Filters out noise, social media widgets, and short interface text
            if len(text) > 45: 
                clean_paragraphs.append(text)
                
        return "\n\n".join(clean_paragraphs)
    except Exception as e:
        print(f"   [Scraping Error] {e}")
        return ""

def fetch_and_store_news():
    history = load_history()
    new_links = []
    
    print(f"[{datetime.now()}] Fetching direct Flamengo feed entries...")
    feed = feedparser.parse(FEED_URL)
    
    if not feed.entries:
        print("Error: Could not parse entries from the feed source.")
        return

    print(f"Found {len(feed.entries)} entries. Processing the latest 5...")
    new_articles_count = 0

    for entry in feed.entries[:5]: 
        link = entry.link
        title = entry.title
        
        if link in history:
            print(f"-> Skipping (Already Processed): {title[:50]}...")
            continue
            
        print(f"-> Processing: {title[:50]}...")
        summary = entry.get('summary', '')
        published_date = entry.get('published', 'Unknown Date')
        
        # Directly fetches text since the link is already clean
        full_text = fetch_full_text(link)
        
        if not full_text or len(full_text) < 200:
            print(f"   [Notice] Scraped text too short. Using summary fallback.")
            full_text = summary
        else:
            print(f"   [Success] Scraped {len(full_text)} characters of full text.")

        news_data = {
            "title": title,
            "source_url": link,
            "published_at": published_date,
            "summary": summary,
            "full_text": full_text,
            "extracted_at": datetime.now().isoformat()
        }
        
        filename = f"{sanitize_filename(title)}.json"
        file_path = os.path.join(OUTPUT_DIR, filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(news_data, f, ensure_ascii=False, indent=4)
            
        new_links.append(link)
        new_articles_count += 1

    if new_links:
        history.extend(new_links)
        save_history(history)
        
    print(f"Process finished. {new_articles_count} new articles stored.")

if __name__ == "__main__":
    fetch_and_store_news()