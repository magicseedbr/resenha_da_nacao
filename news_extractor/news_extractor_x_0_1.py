import os
import re
import json
import requests
import urllib.parse
from bs4 import BeautifulSoup
from datetime import datetime

# Configuration - Filtering specifically for public X posts from the past 24 hours
QUERY = "site:x.com flamengo"
SEARCH_URL = "https://html.duckduckgo.com/html/"
OUTPUT_DIR = "./raw_tweets"
HISTORY_FILE = "processed_tweets.json"

# Ingestion Network Headers
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9"
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

def run_public_x_extractor():
    history = set(load_history())
    
    print(f"[{datetime.now()}] Harvesting live X posts (Past 24 hours) via HTML Index...")
    
    # Payload parameters injecting 'df': 'd' to force maximum temporal recency
    # Options: 'd' = Past Day, 'w' = Past Week
    payload = {
        'q': QUERY,
        'df': 'd'
    }
    
    try:
        response = requests.get(SEARCH_URL, headers=HTTP_HEADERS, params=payload, timeout=10)
        if response.status_code != 200:
            print(f" [HTTP Error] Search node rejected query: {response.status_code}")
            return
            
        soup = BeautifulSoup(response.text, 'html.parser')
        search_blocks = soup.find_all('div', class_='result')
        
    except Exception as e:
        print(f" [Network Error] Request execution failed: {e}")
        return

    print(f" Discovered {len(search_blocks)} recent index blocks in search payload.")
    new_items_count = 0
    new_links = []

    for block in search_blocks:
        url_tag = block.find('a', class_='result__url')
        snippet_tag = block.find('a', class_='result__snippet')
        
        if not url_tag or not snippet_tag:
            continue
            
        raw_url = url_tag.get('href', '')
        text_body = snippet_tag.get_text().strip()
        
        # Unroll internal DuckDuckGo tracker redirects
        if "/l/?" in raw_url:
            match = re.search(r'uddg=([^&]+)', raw_url)
            if match:
                url = urllib.parse.unquote(match.group(1))
            else:
                url = raw_url
        else:
            url = raw_url

        # Structural validation to ensure target points to a tweet status update
        if "x.com" in url and "/status/" in url:
            clean_url = url.split("?")[0]
            
            if clean_url in history:
                continue
                
            tweet_id = clean_url.split("/status/")[-1]
            
            # Structural social payload template matching previous schemas
            tweet_payload = {
                "tweet_id": tweet_id,
                "source_url": clean_url,
                "text": text_body,
                "time_frame": "Past 24 Hours",
                "source_platform": "X/Twitter Realtime Index",
                "extracted_at": datetime.now().isoformat()
            }
            
            filename = f"LiveTweet_{tweet_id}.json"
            file_path = os.path.join(OUTPUT_DIR, filename)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(tweet_payload, f, ensure_ascii=False, indent=4)
                
            print(f" [Saved Recent] -> Tweet ID: {tweet_id} | Text: {text_body[:55]}...")
            new_links.append(clean_url)
            new_items_count += 1

    if new_links:
        updated_history = list(history) + new_links
        save_history(updated_history)
        
    print(f"\n[{datetime.now()}] Real-time cycle completed. Stored {new_items_count} recent public posts.")

if __name__ == "__main__":
    run_public_x_extractor()