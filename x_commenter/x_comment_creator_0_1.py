"""
Comentador de posts — Resenha da Nação (etapa 1: GERAÇÃO, não posta).

Para cada perfil em profiles.txt:
  1. Abre o perfil no X (sessão logada de x_creator/x_session.json).
  2. Pega o post MAIS RECENTE (ignora fixados e reposts).
  3. Gera um comentário curto que dialoga com o conteúdo e cria CURIOSIDADE
     sobre o @ResenhadaNacao — SEM link, SEM @menção, SEM hashtag. A ideia é
     que a pessoa clique no nosso perfil pra ver quem comentou aquilo.

Saída (pra revisão antes de postar):
  comments/<handle>/{comment.txt, post_data.json}
  pending_comments.json  — lista de handles gerados, fila pro publisher.

Revisão: olhe os comment.txt; depois rode x_comment_publisher_0_1.py pra postar
só o que estiver na fila (você pode editar pending_comments.json à mão).

Uso:
    python x_comment_creator_0_1.py
"""

import os
import json
import time
import base64
import random
import tempfile
from datetime import datetime

from google import genai
from groq import Groq
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PROFILES_FILE  = os.path.join(SCRIPT_DIR, "profiles.txt")
COMMENTS_DIR   = os.path.join(SCRIPT_DIR, "comments")
PENDING_FILE   = os.path.join(SCRIPT_DIR, "pending_comments.json")
PUBLISHED_FILE = os.path.join(SCRIPT_DIR, "published_comments.json")
# Reusa a sessão já logada do processo de posts (uso local)
SESSION_FILE   = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "x_creator", "x_session.json"))

GEMINI_MODEL_NAME = "gemini-2.5-flash"
GROQ_MODEL_NAME   = "llama-3.3-70b-versatile"

_MAX_COMMENT = 270  # margem dentro dos 280 do X
_STEALTH = Stealth(navigator_user_agent=False)

# Rótulos de "social context" que indicam fixado/repost (pt-BR e en)
_SKIP_CONTEXT = ("fixado", "fixou", "pinned", "repost", "você repostou", "reposted")


# ── Ambiente ───────────────────────────────────────────────────────────────────

def load_env_file(env_path):
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")


# ── Perfis ───────────────────────────────────────────────────────────────────

def load_profiles():
    if not os.path.exists(PROFILES_FILE):
        return []
    handles = []
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            handles.append(line.lstrip("@").strip())
    return handles


# ── Browser ──────────────────────────────────────────────────────────────────

def _human_delay(lo=0.4, hi=1.0):
    time.sleep(random.uniform(lo, hi))


def resolve_session():
    """Caminho do storage_state: secret X_SESSION_JSON (CI) ou o arquivo local."""
    raw = os.environ.get("X_SESSION_JSON", "")
    if raw:
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        tmp.write(base64.b64decode(raw).decode("utf-8"))
        tmp.close()
        return tmp.name
    return SESSION_FILE if os.path.exists(SESSION_FILE) else None


def make_browser(pw):
    return pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-blink-features=AutomationControlled"],
    )


def make_context(browser, session_path):
    context = browser.new_context(
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1280, "height": 900},
        storage_state=session_path,
    )
    context.set_default_navigation_timeout(30_000)
    context.set_default_timeout(15_000)
    return context


def new_page(context):
    page = context.new_page()
    _STEALTH.use_sync(page)
    return page


def is_logged_in(page):
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25_000)
        page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=10_000)
        return True
    except PWTimeout:
        return False


# ── Extração do post mais recente ──────────────────────────────────────────────

def get_latest_post(page, handle):
    """Retorna {url, text} do post original mais recente (pula fixado/repost)."""
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=25_000)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
    except PWTimeout:
        return None
    _human_delay(1.0, 2.0)

    articles = page.query_selector_all('article[data-testid="tweet"]')
    for art in articles[:10]:
        ctx = art.query_selector('[data-testid="socialContext"]')
        if ctx:
            label = (ctx.inner_text() or "").lower()
            if any(k in label for k in _SKIP_CONTEXT):
                continue

        link = art.query_selector("a:has(time)")
        href = link.get_attribute("href") if link else None
        if not href or "/status/" not in href:
            continue

        text_el = art.query_selector('[data-testid="tweetText"]')
        text = (text_el.inner_text() if text_el else "").strip()

        return {"url": "https://x.com" + href.split("?")[0], "text": text}
    return None


# ── Geração do comentário ──────────────────────────────────────────────────────

def _build_prompt(handle, post_text):
    has_text = bool(post_text.strip())
    post_block = post_text if has_text else "(o post não tem texto — é imagem/vídeo)"

    return f"""Você administra o @ResenhadaNacao, uma página de TORCIDA do Flamengo no X.
Você vai comentar no post mais recente do perfil @{handle}.

OBJETIVO: escrever um comentário que dialoga com o conteúdo do post e gera
CURIOSIDADE sobre quem comentou, de um jeito que faça a pessoa querer CLICAR no
seu perfil pra ver mais — SEM pedir isso explicitamente.

COMO ESCREVER:
- Reaja ao conteúdo REAL do post (mostre que leu/entendeu), com uma opinião,
  um ângulo rubro-negro, uma provocação leve ou um "tem mais nessa história".
- Deixe um gancho de curiosidade: a sensação de que você sabe/tem algo a mais —
  sem entregar tudo, sem ser vago genérico ("top!", "concordo").
- Natural, como torcedor esperto comentando. 1 a 2 frases. Direto.

PROIBIDO:
- NÃO inclua link.
- NÃO mencione @ nenhum (nem o seu perfil, nem "vai no meu perfil").
- NÃO use hashtag.
- NÃO invente fato sobre o post (não sabe? não afirma). Curiosidade não é mentira.
- Nada de elogio vazio nem comentário que serviria pra qualquer post.
- No máximo 1 emoji (zero é ótimo).

FORMATO:
- LIMITE: {_MAX_COMMENT} caracteres. Português informal, sotaque carioca leve.
- Responda SOMENTE com JSON válido, sem markdown.

--- POST DE @{handle} ---
{post_block}
--- FIM DO POST ---

JSON esperado:
{{
    "comment": "<comentário curto, curioso, sem link/@/hashtag, até {_MAX_COMMENT} chars>"
}}
"""


def _via_gemini(prompt):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY não definida")
    client = genai.Client(api_key=GEMINI_API_KEY)
    resp = client.models.generate_content(
        model=GEMINI_MODEL_NAME, contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(resp.text).get("comment", "")


def _via_groq(prompt):
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY não definida")
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content).get("comment", "")


def generate_comment(handle, post_text):
    prompt = _build_prompt(handle, post_text)
    try:
        text = _via_gemini(prompt)
        print("    (via Gemini)")
    except Exception as err:
        es = str(err)
        tag = "[Gemini 429]" if ("429" in es or "quota" in es.lower()) else "[Gemini falhou]"
        print(f"    {tag} {es[:90]} — tentando Groq...")
        try:
            text = _via_groq(prompt)
            print("    (via Groq fallback)")
        except Exception as err2:
            print(f"    [Falha] Groq também falhou: {err2}")
            return ""
    text = " ".join(text.split()).strip()
    return text[:_MAX_COMMENT].rstrip()


# ── Persistência ───────────────────────────────────────────────────────────────

def save_comment(handle, post, comment):
    handle_dir = os.path.join(COMMENTS_DIR, handle)
    os.makedirs(handle_dir, exist_ok=True)
    with open(os.path.join(handle_dir, "comment.txt"), "w", encoding="utf-8") as f:
        f.write(comment)
    data = {
        "handle": handle,
        "post_url": post["url"],
        "post_text": post["text"],
        "comment": comment,
        "comment_chars": len(comment),
        "generated_at": datetime.now().isoformat(),
    }
    with open(os.path.join(handle_dir, "post_data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def save_pending(handles):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(handles, f, ensure_ascii=False, indent=4)


def commented_post_urls():
    """URLs de posts já comentados (de published_comments.json). Dedup é por POST,
    não por perfil — assim comentamos de novo quando o perfil tiver post novo."""
    if not os.path.exists(PUBLISHED_FILE):
        return set()
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            published = json.load(f)
    except (json.JSONDecodeError, IOError):
        return set()
    # Tolera formato legado (lista) ou arquivo vazio inicializado como []: sem
    # registros de URL, então nada a deduplicar.
    if not isinstance(published, dict):
        return set()
    urls = set()
    for key, val in published.items():
        if "/status/" in key:
            urls.add(key)
        if isinstance(val, dict) and val.get("post_url"):
            urls.add(val["post_url"])
    return urls


# ── Main ────────────────────────────────────────────────────────────────────────

def _short(text, n=90):
    flat = " ".join(text.split())
    return flat[:n] + ("..." if len(flat) > n else "")


def main():
    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Comentador (geração p/ revisão)")
    print("=" * 70)

    profiles = load_profiles()
    if not profiles:
        print(f" [Aviso] Nenhum perfil em {PROFILES_FILE}. Adicione um @ por linha.")
        return
    session_path = resolve_session()
    if not session_path:
        print(f" [Erro] Sessão não encontrada (nem X_SESSION_JSON nem {SESSION_FILE}).")
        print("        Rode x_creator/x_publish_0_1.py uma vez pra logar e salvar a sessão.")
        return

    print(f" {len(profiles)} perfil(is): {', '.join('@'+p for p in profiles)}\n")
    os.makedirs(COMMENTS_DIR, exist_ok=True)
    already = commented_post_urls()
    pending = []

    with sync_playwright() as pw:
        browser = make_browser(pw)
        context = make_context(browser, session_path)
        page = new_page(context)

        if not is_logged_in(page):
            print(" [Erro] Sessão inválida/expirada. Renove com x_creator/x_publish_0_1.py.")
            context.close(); browser.close()
            return

        for handle in profiles:
            print(f"-> @{handle}")
            try:
                post = get_latest_post(page, handle)
            except Exception as err:
                print(f"    [Erro] Falha ao ler o perfil: {err}")
                continue
            if not post:
                print("    [Aviso] Não achei um post recente (só fixado/repost?). Pulando.")
                continue
            if post["url"] in already:
                print("    [Ignorado] Post mais recente já foi comentado antes.")
                continue

            print(f"    Post: {_short(post['text']) or '(sem texto)'}")
            comment = generate_comment(handle, post["text"])
            if not comment:
                print("    [Erro] Não gerei comentário. Pulando.")
                continue

            save_comment(handle, post, comment)
            pending.append(handle)
            print(f"    Comentário: {comment}")
            print(f"    [OK] salvo em comments/{handle}/  ({len(comment)}/280)\n")
            _human_delay(2.0, 4.0)

        context.close(); browser.close()

    save_pending(pending)
    print("-" * 70)
    print(f" Gerados: {len(pending)} | fila pending_comments.json: {pending}")
    print(" Revise os comments/<handle>/comment.txt e rode x_comment_publisher_0_1.py.")
    print("=" * 70)


if __name__ == "__main__":
    main()
