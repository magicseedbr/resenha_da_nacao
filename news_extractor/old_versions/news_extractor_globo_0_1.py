import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

OUTPUT_DIR = "./raw_news"
HISTORY_FILE = "processed_links.json"

# Advanced headers to bypass Globo's strict WAF/CDN 400 Bad Request blocks
GLOBO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
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

def fetch_globo_full_text(url):
    """Scrapes the full article body specifically from ge.globo.com layout."""
    try:
        response = requests.get(url, headers=GLOBO_HEADERS, timeout=10)
        if response.status_code != 200:
            return ""
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Globo's modern article layout uses content-text__paragraph class
        paragraphs = soup.find_all('p', class_='content-text__paragraph')
        
        # Fallback to standard p tags if layout differs
        if not paragraphs:
            paragraphs = soup.find_all('p')
            
        clean_paragraphs = []
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 45: 
                clean_paragraphs.append(text)
                
        return "\n\n".join(clean_paragraphs)
    except Exception as e:
        print(f"      [Scraping Error] Failed to parse Globo article: {e}")
        return ""

def scrape_globo_flamengo():
    history = load_history()
    new_links = []
    
    target_url = "https://ge.globo.com/futebol/times/flamengo/"
    print(f"[{datetime.now()}] Connecting directly to ge.globo Flamengo landing page...")
    
    try:
        response = requests.get(target_url, headers=GLOBO_HEADERS, timeout=10)
        if response.status_code != 200:
            print(f"   [HTTP Error] Failed to access Globo. Status code: {response.status_code}")
            return
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # ge.globo targets titles and links using the 'feed-post-link' class
        articles = soup.find_all('a', class_='feed-post-link')
        
    except Exception as err:
        print(f"   [Connection Error] Failed to reach Globo: {err}")
        return

    if not articles:
        print("   [Warning] Could not find any article elements on the page layout.")
        return

    print(f"   Found {len(articles)} articles on the page. Processing the top 3 latest items...")
    new_articles_count = 0

    for article in articles[:3]:
        link = article.get('href')
        title = article.get_text().strip()
        
        if not link or not title:
            continue
            
        if link in history:
            print(f"   -> Skipping (Duplicate): {title[:50]}...")
            continue
            
        print(f"   -> Processing Globo News: {title[:50]}...")
        
        # Fetch full text with the optimized browser handshake
        full_text = fetch_globo_full_text(link)
        
        if not full_text or len(full_text) < 200:
            print(f"      [Notice] Full text too short, skipping item.")
            continue
            
        print(f"      [Success] Extracted {len(full_text)} characters from ge.globo.")

        news_data = {
            "title": title,
            "source_name": "Globo Esporte",
            "source_url": link,
            "published_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), # Extracted live
            "summary": title,
            "full_text": full_text,
            "extracted_at": datetime.now().isoformat()
        }
        
        filename = f"GloboEsporte_{sanitize_filename(title[:40])}.json"
        file_path = os.path.join(OUTPUT_DIR, filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(news_data, f, ensure_ascii=False, indent=4)
            
        new_links.append(link)
        new_articles_count += 1

    if new_links:
        history.extend(new_links)
        save_history(history)
        
    print(f"Process finished. {new_articles_count} new Globo articles stored successfully.")

if __name__ == "__main__":
    scrape_globo_flamengo()