import os
import re
import feedparser
import json
from datetime import datetime

# Configuration
# Using Google News RSS tracking 'Flamengo' - highly stable and aggregates multiple sources
FEED_URL = "https://news.google.com/rss/search?q=Flamengo&hl=pt-BR&gl=BR&ceid=BR:pt-419"

OUTPUT_DIR = "./raw_news"
HISTORY_FILE = "processed_links.json"

# Ensure output directory exists
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def load_history():
    """Loads the list of already processed links to ensure idempotency."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_history(history):
    """Updates the history file with newly processed links."""
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

def sanitize_filename(title):
    """Removes special characters to ensure a safe and clean filename."""
    clean_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
    return clean_title.replace(" ", "_")

def fetch_and_store_news():
    history = load_history()
    new_links = []
    
    print(f"[{datetime.now()}] Fetching Flamengo aggregated feed from Google News...")
    feed = feedparser.parse(FEED_URL)
    
    if not feed.entries:
        print("Error: Could not retrieve any articles from the feed source.")
        return

    new_articles_count = 0

    for entry in feed.entries:
        link = entry.link
        
        # Skip if the article has already been downloaded
        if link in history:
            continue
            
        title = entry.title
        summary = entry.get('summary', '')
        published_date = entry.get('published', 'Unknown Date')
        
        # Structured news schema
        news_data = {
            "title": title,
            "source_url": link,
            "published_at": published_date,
            "summary": summary,
            "extracted_at": datetime.now().isoformat()
        }
        
        # Generate a unique file name per news item
        filename = f"{sanitize_filename(title)}.json"
        file_path = os.path.join(OUTPUT_DIR, filename)
        
        # Save the individual news item
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(news_data, f, ensure_ascii=False, indent=4)
            
        print(f"-> Extracted: {title[:60]}...")
        
        new_links.append(link)
        new_articles_count += 1

    # Persist history state if changes occurred
    if new_links:
        history.extend(new_links)
        save_history(history)
        
    print(f"Process finished. {new_articles_count} new articles saved to '{OUTPUT_DIR}'.")

if __name__ == "__main__":
    fetch_and_store_news()