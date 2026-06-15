import os
import re
import json
import glob
from datetime import datetime
from google import genai

# Dynamic Path Resolution based on the script's absolute location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(env_path):
    """Lê um arquivo .env simples (KEY=VALUE) e popula os.environ, sem dependências externas."""
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# Navigation: Go up one level to root, then enter the news_extractor folder structure
RAW_NEWS_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../news_extractor/raw_news"))
RAW_TWEETS_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../news_extractor/raw_tweets"))

# The output manifest will be saved directly inside the news_curator folder
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "selected_story.json")
GEMINI_MODEL_NAME = "gemini-2.5-flash"
USED_STORIES_FILE = os.path.join(SCRIPT_DIR, "used_stories.json")

# API Key carregada do arquivo .env na raiz do projeto (um nível acima deste script)
load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Portuguese stop-words list to filter out noise from titles
PORTUGUESE_STOP_WORDS = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "em", "no", "na",
    "nos", "nas", "com", "para", "por", "que", "se",
    "ao", "aos", "e", "ou", "mas", "com", "mais", "um",
    "contra", "sobre", "pelo", "pela", "pelos", "pelas"
}

def load_json_files(directory_path):
    """
    Scans the specified directory and loads all valid JSON files into memory.
    Yields parsed dictionaries while gracefully handling malformed files.
    """
    if not os.path.exists(directory_path):
        print(f"[Aviso] Diretório '{directory_path}' não existe.")
        return []
        
    json_data_list = []
    file_pattern = os.path.join(directory_path, "*.json")
    
    for file_path in glob.glob(file_pattern):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data_list.append(json.load(f))
        except (json.JSONDecodeError, IOError) as err:
            print(f"[Erro de Leitura] Falha ao ler {file_path}: {err}")
            continue
            
    return json_data_list

def extract_keywords(title):
    """
    Tokenizes the title, removes punctuation, applies lowercasing, 
    and filters out short words and Portuguese stop-words.
    """
    # Clean special characters and tokenize text safely
    words = re.findall(r'\w+', title.lower())
    # Drop short noise and stop words
    keywords = [w for w in words if w not in PORTUGUESE_STOP_WORDS and len(w) > 2]
    return keywords

def calculate_hype_score(news_item, tweets_pool):
    """
    Calculates the statistical relevance score (Hype Score) by checking 
    the presence of title keywords inside the text body of loaded tweets.
    """
    keywords = extract_keywords(news_item.get('title', ''))
    if not keywords:
        return 0
        
    score = 0
    for tweet in tweets_pool:
        tweet_text = tweet.get('text', '').lower()
        # Count how many of the core keywords appear in this tweet
        matches = sum(1 for kw in keywords if kw in tweet_text)
        if matches > 0:
            # Scale score based on match density per tweet item
            score += matches
            
    return score

def select_best_story_via_gemini(top_stories):
    """
    Leverages Gemini LLM capabilities to resolve ties or select the best option
    from top candidates based on contextual journalistic impact and social hype.
    """
    # Validation check for the API key loaded from the .env file
    if not GEMINI_API_KEY:
        raise ValueError("[Falha de Autenticação] Defina GEMINI_API_KEY no arquivo .env da raiz do projeto.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Formulate context payload for evaluation loop
    candidates_context = []
    for index, item in enumerate(top_stories):
        candidates_context.append({
            "index": index,
            "title": item["title"],
            "source": item["source_name"],
            "hype_score": item["hype_score"],
            "excerpt": item.get("full_text", "")[:400] # Provide truncated text body to optimize tokens
        })
        
    prompt = f"""
    You are an expert sports editor for the Flamengo news portal 'Resenha da Nação'.
    Analyze these top 3 news candidate objects that are trending on social media:
    
    {json.dumps(candidates_context, ensure_ascii=False, indent=2)}
    
    Your task: Select exactly ONE news index that has the highest potential for an engaging long-form article.
    Evaluate the balance between raw social engagement (hype_score) and high journalistic value/interest to Flamengo fans.
    
    You MUST respond with a strict raw JSON object with this format, without markdown backticks:
    {{
        "selected_index": <int_index_of_chosen_story>,
        "justification": "<detailed_string_explaining_why_this_is_the_best_choice_in_portuguese>"
    }}
    """
    
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        
        decision_data = json.loads(response.text)
        chosen_index = int(decision_data["selected_index"])
        justification = decision_data["justification"]
        
        return top_stories[chosen_index], justification
    except Exception as gemini_err:
        print(f"[Falha na API] Erro crítico durante a execução do Gemini: {gemini_err}")
        # Secure execution state fallback logic: return the absolute top 1 item by default
        return top_stories[0], "Seleção baseada estritamente no ranking matemático do Hype Score (Fallback devido a falha na API)."

def load_used_urls():
    if not os.path.exists(USED_STORIES_FILE):
        return set()
    try:
        with open(USED_STORIES_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except (json.JSONDecodeError, IOError):
        return set()

def save_used_url(url):
    used = load_used_urls()
    used.add(url)
    with open(USED_STORIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(used), f, ensure_ascii=False, indent=4)

def main():
    print("--- Iniciando Leitura das Pastas ---")
    print(f" Target News Path: {RAW_NEWS_DIR}")
    print(f" Target Tweets Path: {RAW_TWEETS_DIR}")

    news_pool = load_json_files(RAW_NEWS_DIR)
    tweets_pool = load_json_files(RAW_TWEETS_DIR)

    print(f"Carregados em memória: {len(news_pool)} notícias e {len(tweets_pool)} tweets.")

    if not news_pool:
        print("[Execução Interrompida] Nenhuma notícia encontrada na pasta de entrada.")
        return

    used_urls = load_used_urls()
    news_pool = [n for n in news_pool if n.get('source_url') not in used_urls]
    print(f" Notícias ainda não usadas: {len(news_pool)} (excluídas {len(used_urls)} já publicadas)")

    if not news_pool:
        print("[Execução Interrompida] Todas as notícias disponíveis já foram usadas. Rode os extratores para obter novas.")
        return

    print("\n--- Calculando Algoritmo de Hype Score ---")
    scored_news = []
    
    for item in news_pool:
        title = item.get('title', 'Título Desconhecido')
        score = calculate_hype_score(item, tweets_pool)
        
        # Enriched model payload inclusion mapping tracking parameters
        item['hype_score'] = score
        scored_news.append(item)
        print(f" Avaliando Notícia: '{title[:50]}...' -> Hype Score: {score}")

    # Sort descending based on numeric statistical scores
    scored_news.sort(key=lambda x: x['hype_score'], reverse=True)
    
    # Isolate up to 3 candidate stories for cognitive processing loop
    top_candidates = scored_news[:3]
    print(f"\nTop {len(top_candidates)} notícias isoladas. Enviando para validação cognitiva do Gemini...")

    # Route logic to generative nodes
    winner_story, justification = select_best_story_via_gemini(top_candidates)
    
    print("\n--- Resultado da Curadoria ---")
    print(f" Vencedora: {winner_story['title']}")
    print(f" Hype Score Final: {winner_story['hype_score']}")
    print(f" Justificativa: {justification}")
    
    # Structural payload template output serialization schema
    final_output = {
        "curated_at": datetime.now().isoformat(),
        "hype_score": winner_story["hype_score"],
        "justification": justification,
        "article_data": {
            "title": winner_story["title"],
            "source_name": winner_story["source_name"],
            "source_url": winner_story["source_url"],
            "full_text": winner_story["full_text"]
        }
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)

    save_used_url(winner_story.get('source_url', ''))
    print(f"\nSucesso! Manifesto salvo com segurança em: '{OUTPUT_FILE}'.")

if __name__ == "__main__":
    main()