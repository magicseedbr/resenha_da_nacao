import os
import re
import json
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration - Define how deep into history you want to go
# 1 page = ~10 to 20 articles. Range(1, 15) will fetch up to 200-300 historical articles instantly.
START_PAGE = 1
END_PAGE = 15  
MAX_WORKERS = 12  # High concurrency for bulk downloading

BASE_FEED_URL = "https://colunadofla.com/feed/?paged="
OUTPUT_DIR = "./raw_news"
HISTORY_FILE = "processed_links.json"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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

def fetch_article_body(url):
    """Worker task to scrape and clean the full text from a specific article link."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=8)
        if response.status_code != 200:
            return ""
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p')
        clean_paragraphs = [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 45]
        return "\n\n".join(clean_paragraphs)
    except Exception:
        return ""

def run_bulk_pipeline():
    history = set(load_history())
    discovered_articles = {}
    
    print(f"[{datetime.now()}] Starting Bulk Discovery Phase across {END_PAGE - START_PAGE} feed pages...")
    
    # Step 1: Deep dive into historical RSS pages
    for page in range(START_PAGE, END_PAGE):
        target_feed = f"{BASE_FEED_URL}{page}"
        print(f" -> Crawling Feed Page {page}: {target_feed}")
        
        try:
            response = requests.get(target_feed, headers=HTTP_HEADERS, timeout=10)
            if response.status_code == 404:
                print(f"    [End of Feed] Hit page 404. Stopping discovery.")
                break
            if response.status_code != 200:
                continue
                
            feed = feedparser.parse(response.content)
            for entry in feed.entries:
                link = entry.link
                title = entry.title
                if link not in history and link not in discovered_articles:
                    discovered_articles[link] = {
                        "title": title,
                        "published": entry.get('published', 'Unknown Date'),
                        "summary": entry.get('summary', '')
                    }
        except Exception as e:
            print(f"    [Error] Failed to read feed page {page}: {e}")
            continue

    total_tasks = len(discovered_articles)
    print(f"\nDiscovery complete. Found {total_tasks} historical un-processed articles.")
    if total_tasks == 0:
        return

    print(f"Launching ThreadPoolExecutor with {MAX_WORKERS} workers for bulk scraping...\n")
    new_successful_links = []

    # Step 2: High-speed parallel scraping using clean dictionary iteration
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Fixed: Directly iterating over items without illegal rebinding syntax
        future_to_url = {
            executor.submit(fetch_article_body, url): (url, info) 
            for url, info in discovered_articles.items()
        }
        
        for future in as_completed(future_to_url):
            url, info = future_to_url[future]
            try:
                full_text = future.result()
                if not full_text or len(full_text) < 250:
                    continue
                    
                news_data = {
                    "title": info["title"],
                    "source_name": "Coluna do Fla Archive",
                    "source_url": url,
                    "published_at": info["published"],
                    "summary": info["summary"],
                    "full_text": full_text,
                    "extracted_at": datetime.now().isoformat()
                }
                
                filename = f"Bulk_Fla_{sanitize_filename(info['title'][:40])}.json"
                file_path = os.path.join(OUTPUT_DIR, filename)
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(news_data, f, ensure_ascii=False, indent=4)
                    
                print(f" [Bulk Saved] -> {info['title'][:50]}... ({len(full_text)} chars)")
                new_successful_links.append(url)
            except Exception as e:
                print(f" [Worker Error] Failed to process article: {e}")

    # Step 3: Append new links to history state sequentially
    if new_successful_links:
        updated_history = list(history) + new_successful_links
        save_history(updated_history)
        
    print(f"\n[{datetime.now()}] Bulk extraction finished. Successfully added {len(new_successful_links)} new JSON files.")

if __name__ == "__main__":
    run_bulk_pipeline()