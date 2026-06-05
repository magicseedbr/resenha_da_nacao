import os
import re
import json
import requests
from datetime import datetime

# Dynamic Path Resolution based on the script's absolute location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.abspath(os.path.join(SCRIPT_DIR, "../news_curator/selected_story.json"))
DOWNLOAD_DIR = os.path.join(SCRIPT_DIR, "downloaded_images")

# Clean standard desktop headers to receive the standard organic search frame
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9"
}

# Local stop-words list to clear the text strings without LLM latency
PORTUGUESE_STOP_WORDS = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "em", "no", "na",
    "nos", "nas", "com", "para", "por", "que", "se",
    "ao", "aos", "e", "ou", "mas", "com", "mais", "um",
    "contra", "sobre", "pelo", "pela", "pelos", "pelas",
    "diz", "que", "pode", "poderia", "ter", "ido", "à"
}

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def generate_search_query_native(title):
    """Tokenizes headlines programmatically into precise search terms without LLMs."""
    clean_title = re.sub(r'[^\w\s]', '', title.lower())
    words = clean_title.split()
    
    important_words = [w for w in words if w not in PORTUGUESE_STOP_WORDS and len(w) > 2]
    
    # Force context matching to trigger Google's Knowledge Graph layout cards
    if "lino" in important_words:
        important_words.append("jogador")
        
    final_query = " ".join(important_words[:3])
    return final_query if final_query else "Samuel Lino"

def fetch_image_from_google_organic(query):
    """
    Queries Google's main search index instead of the locked image tab.
    Extracts structural image assets embedded inside knowledge graphs and news carousels.
    """
    encoded_query = requests.utils.quote(query)
    # Target standard search route which doesn't enforce the JS-execution wall
    search_url = f"https://www.google.com/search?q={encoded_query}&hl=pt-BR&gl=BR"
    
    try:
        response = requests.get(search_url, headers=HTTP_HEADERS, timeout=10)
        if response.status_code != 200:
            print(f" [Erro de Rede] Google recusou resposta: {response.status_code}")
            return None
            
        html_content = response.text
        
        # Resilient Regex: Mine Google's secure image CDN links loaded inside the organic template
        # Targets links matching the structural pattern: https://encrypted-tbn0.gstatic.com/images?q=tbn:...
        gstatic_images = re.findall(r'(https://encrypted-tbn\d+\.gstatic\.com/images\?q=tbn:[^"\s&<>]+)', html_content)
        
        if gstatic_images:
            # Pick the top match which usually represents the main profile image or top trending asset
            return gstatic_images[0]
            
        print(" [Aviso] Nenhuma imagem indexada foi localizada no código da busca geral.")
        return None
    except Exception as e:
        print(f"[Scraper Error] Failed to scan Google organic nodes: {e}")
        return None

def download_image(url, filename_prefix):
    """Streams and stores raw binary chunks securely onto local sectors."""
    try:
        response = requests.get(url, headers=HTTP_HEADERS, timeout=12, stream=True)
        if response.status_code != 200:
            return None
            
        content_type = response.headers.get('Content-Type', '')
        ext = ".png" if "png" in content_type else ".jpg"
        
        clean_filename = f"{filename_prefix}_{int(datetime.now().timestamp())}{ext}"
        file_path = os.path.join(DOWNLOAD_DIR, clean_filename)
        
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        return file_path
    except Exception as e:
        print(f"[Download Error] Failed streaming asset data stream: {e}")
        return None

def main():
    print("--- Deploying Organic Google Image Searcher Layer (No-API) ---")
    
    if not os.path.exists(INPUT_FILE):
        print(f"[Execution Interrupted] Target artifact manifest not found at: '{INPUT_FILE}'")
        return
        
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            story_data = json.load(f)
    except Exception as err:
        print(f"[JSON Error] Failed reading story index file: {err}")
        return

    article_title = story_data.get("article_data", {}).get("title", "")
    if not article_title:
        print("[Execution Interrupted] Selected story artifact contains empty title fields.")
        return
        
    print(f" -> Current Headline: '{article_title[:60]}...'")
    optimized_query = generate_search_query_native(article_title)
    print(f" -> Organic Google Query: '{optimized_query}'")
    
    # Fire the organic web search scraper
    target_img_url = fetch_image_from_google_organic(optimized_query)
    
    if not target_img_url:
        print(" [Failed] Google search template extraction yielded 0 active photo nodes.")
        return
        
    print(f" -> Found matching asset target URL: {target_img_url[:75]}...")
    print(" -> Downloading binary asset stream to disk...")
    
    local_image_path = download_image(target_img_url, "Resenha_Google_Asset")
    
    if local_image_path:
        print(f" [Success] Image securely stored at: {local_image_path}")
        
        story_data["media_data"] = {
            "image_search_query": optimized_query,
            "original_source_url": target_img_url,
            "local_asset_path": os.path.abspath(local_image_path),
            "updated_at": datetime.now().isoformat(),
            "provider": "Google Organic Graph Mining Layer"
        }
        
        with open(INPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(story_data, f, ensure_ascii=False, indent=4)
            
        print(f" [Manifest Updated] Reference mapping injected back into '{INPUT_FILE}'.")
    else:
        print(" [Failed] Execution pipeline dropped during binary download segment.")

if __name__ == "__main__":
    main()