import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration - Drastically expanded the ingestion net to find 10x more Flamengo articles
TARGET_URLS = [
    "https://ge.globo.com/futebol/times/flamengo/",
    "https://ge.globo.com/futebol/brasileirao-serie-a/",
    "https://ge.globo.com/futebol/copa-do-brasil/",
    "https://ge.globo.com/futebol/copa-libertadores/",
    "https://ge.globo.com/futebol/mercado-da-bola/",
    "https://ge.globo.com/futebol/",
    "https://ge.globo.com/futebol/selecao-brasileira/"
]

OUTPUT_DIR = "./raw_news"
HISTORY_FILE = "processed_links.json"
KEYWORD_FILTER = "flamengo"
MAX_WORKERS = 15  # Optimized thread count for wider coverage

GLOBO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Upgrade-Insecure-Requests": "1"
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
    """Worker function tasked with downloading and parsing a single article body."""
    try:
        response = requests.get(url, headers=GLOBO_HEADERS, timeout=8)
        if response.status_code != 200:
            return ""
            
        soup = BeautifulSoup(response.text, 'html.parser')
        paragraphs = soup.find_all('p', class_='content-text__paragraph')
        
        if not paragraphs:
            paragraphs = soup.find_all('p')
            
        clean_paragraphs = [p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 45]
        return "\n\n".join(clean_paragraphs)
    except Exception:
        return ""

def extract_links_from_page(url):
    """Harvests target links from a given index landing page."""
    try:
        response = requests.get(url, headers=GLOBO_HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.find_all('a', class_='feed-post-link')
    except Exception as e:
        print(f"[Link Extraction Error] Failed page {url}: {e}")
        return []

def scale_ingestion_pipeline():
    history = set(load_history()) # Using set for O(1) lookups during high-volume scaling
    discovered_items = {}
    
    print(f"[{datetime.now()}] Initializing high-throughput discovery phase...")
    
    # Discover links across all target entry points
    for target_url in TARGET_URLS:
        print(f" -> Scanning entry point: {target_url}")
        elements = extract_links_from_page(target_url)
        
        for elem in elements:
            link = elem.get('href')
            title = elem.get_text().strip()
            
            if not link or not title:
                continue
            
            # Apply contextual filtering if scanning general tournament or category pages
            if "times/flamengo" not in target_url:
                if KEYWORD_FILTER not in title.lower() and KEYWORD_FILTER not in link.lower():
                    continue # Skip unrelated tournament news
                    
            if link not in history:
                discovered_items[link] = title

    total_tasks = len(discovered_items)
    print(f"\nDiscovery complete. Found {total_tasks} new un-processed articles.")
    if total_tasks == 0:
        return

    print(f"Spinning up ThreadPoolExecutor with {MAX_WORKERS} parallel workers...\n")
    new_successful_links = []

    # Execute concurrent HTTP requests using a thread pool
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Map URLs to their respective processing futures
        future_to_url = {executor.submit(fetch_article_body, url): (url, title) for url, title in discovered_items.items()}
        
        for future in as_completed(future_to_url):
            url, title = future_to_url[future]
            try:
                full_text = future.result()
                
                if not full_text or len(full_text) < 200:
                    continue # Drop empty payloads or blocks
                    
                # Schema serialization
                news_data = {
                    "title": title,
                    "source_name": "Globo Esporte Cluster",
                    "source_url": url,
                    "published_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "full_text": full_text,
                    "extracted_at": datetime.now().isoformat()
                }
                
                filename = f"GloboCluster_{sanitize_filename(title[:40])}.json"
                file_path = os.path.join(OUTPUT_DIR, filename)
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(news_data, f, ensure_ascii=False, indent=4)
                    
                print(f" [Saved] -> {title[:55]}... ({len(full_text)} chars)")
                new_successful_links.append(url)
                
            except Exception as exc:
                print(f" [Thread Failure] Worker crashed processing item: {exc}")

    # Main thread updates state history sequentially to maintain transactional integrity
    if new_successful_links:
        updated_history = list(history) + new_successful_links
        save_history(updated_history)
        
    print(f"\n[{datetime.now()}] Scaled pipeline cycle finished. Total stored this run: {len(new_successful_links)} files.")

if __name__ == "__main__":
    scale_ingestion_pipeline()