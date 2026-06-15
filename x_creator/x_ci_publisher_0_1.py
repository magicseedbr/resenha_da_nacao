"""
Publicador automático para CI (GitHub Actions) — Resenha da Nação.

Detecta artigos novos comparando o approved.json do commit atual com o anterior
(HEAD~1), gera o tweet com Gemini/Groq e posta no X via Playwright headless.

Variáveis de ambiente necessárias (GitHub Secrets):
    GEMINI_API_KEY    — chave Gemini (já usada no pipeline)
    GROQ_API_KEY      — fallback LLM (opcional)
    X_SESSION_JSON    — conteúdo de x_session.json em base64
                        Gere com: base64 -i x_session.json | tr -d '\\n'

Uso:
    python x_ci_publisher_0_1.py
"""

import os
import sys
import json
import base64
import tempfile
import subprocess
import time
import random
from datetime import datetime
from PIL import Image
from google import genai
from groq import Groq
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.dirname(SCRIPT_DIR)
APPROVED_JSON = os.path.join(REPO_ROOT, "site_builder", "approved.json")
ARTICLES_DIR  = os.path.join(REPO_ROOT, "article_writer", "generated_articles")
PERSONAS_DIR  = os.path.join(REPO_ROOT, "article_writer", "personas")
COVERS_DIR    = os.path.join(REPO_ROOT, "site_builder", "static", "covers")

GEMINI_MODEL  = "gemini-2.5-flash"
GROQ_MODEL    = "llama-3.3-70b-versatile"
_STEALTH      = Stealth(navigator_user_agent=False)

_URL_COST  = 23   # t.co encurta qualquer URL para 23 chars
_MAX_TWEET = 280

HASHTAGS_BASE = "#Flamengo #Mengao #VaiFlamengo"
HASHTAGS_BY_EDITORIA = {
    "mercado":    "#MercadoDaBola #Transferencias",
    "base":       "#CriasDoNinho #GeracaoFlamengo",
    "selecao":    "#Selecao #CopaDoMundo",
    "bastidores": "#Bastidores",
    "geral":      "",
}
PERSONA_FILENAME_MAP = {
    "Rodrigo Marques":    "rodrigo_marques.md",
    "Thiago Vasconcelos": "thiago_vasconcelos.md",
    "Fernanda Aguiar":    "fernanda_aguiar.md",
}


# ── Env ───────────────────────────────────────────────────────────────────────

def load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file(os.path.join(REPO_ROOT, ".env"))
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
X_SESSION_JSON   = os.environ.get("X_SESSION_JSON", "")
SITE_BASE_URL    = os.environ.get("SITE_BASE_URL", "https://www.resenhadanacao.com.br").rstrip("/")

if "://resenhadanacao.com.br" in SITE_BASE_URL:
    SITE_BASE_URL = SITE_BASE_URL.replace("://resenhadanacao.com.br", "://www.resenhadanacao.com.br")


# ── Detecta artigos novos via git diff ───────────────────────────────────────

def get_new_slugs():
    """Retorna slugs presentes no approved.json atual mas ausentes no commit anterior."""
    try:
        result = subprocess.run(
            ["git", "show", "HEAD~1:site_builder/approved.json"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        prev = {e["slug"] for e in json.loads(result.stdout)}
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        prev = set()   # primeiro commit ou arquivo inexistente

    with open(APPROVED_JSON) as f:
        current = json.load(f)

    new_entries = [e for e in current if e["slug"] not in prev]
    return new_entries


# ── Geração do tweet ──────────────────────────────────────────────────────────

def article_url(slug):
    return f"{SITE_BASE_URL}/artigo/{slug}.html"


def build_hashtags(editoria):
    extra = HASHTAGS_BY_EDITORIA.get(editoria, "")
    return f"{HASHTAGS_BASE} {extra}".strip() if extra else HASHTAGS_BASE


def load_persona(author):
    filename = PERSONA_FILENAME_MAP.get(author)
    if not filename:
        return ""
    path = os.path.join(PERSONAS_DIR, filename)
    try:
        with open(path) as f:
            return f.read()
    except IOError:
        return ""


def build_prompt(article_data, persona_content, editoria, slug):
    title    = article_data.get("title", "")
    subtitle = article_data.get("subtitle", "")
    body     = article_data.get("body", "")
    hashtags = build_hashtags(editoria)
    max_chars = _MAX_TWEET - _URL_COST - 1  # 256

    return f"""Você é o(a) jornalista abaixo. Incorpore completamente o estilo descrito.

--- PERFIL DO JORNALISTA ---
{persona_content}
--- FIM DO PERFIL ---

Escreva um POST PARA O X (Twitter) sobre o artigo abaixo, para o perfil @ResenhadaNacao.

REGRAS:
- Tom nativo do X: direto, rápido, pode ser provocador ou empolgado
- Até 1 emoji no máximo (pode ter zero)
- Inclua os hashtags abaixo ao final do texto
- NÃO inclua o link (adicionado automaticamente)
- LIMITE TOTAL: {max_chars} caracteres (texto + hashtags)
- Use linguagem brasileira informal com sotaque carioca
- Responda SOMENTE com JSON válido, sem markdown

--- ARTIGO ---
Título: {title}
Linha de apoio: {subtitle}
Corpo: {body[:1500]}
--- FIM DO ARTIGO ---

Hashtags: {hashtags}

JSON: {{"tweet": "<texto com hashtags, sem link, dentro de {max_chars} chars>"}}
"""


def generate_tweet(article_data, persona_content, editoria, slug):
    prompt = build_prompt(article_data, persona_content, editoria, slug)

    if GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            text = json.loads(resp.text).get("tweet", "")
            if text:
                print("  (via Gemini)")
                return text
        except Exception as e:
            print(f"  [Gemini falhou] {str(e)[:80]}")

    if GROQ_API_KEY:
        try:
            client = Groq(api_key=GROQ_API_KEY)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            text = json.loads(resp.choices[0].message.content).get("tweet", "")
            if text:
                print("  (via Groq fallback)")
                return text
        except Exception as e:
            print(f"  [Groq falhou] {str(e)[:80]}")

    return ""


def assemble_tweet(tweet_text, slug):
    url  = article_url(slug)
    budget = _MAX_TWEET - _URL_COST - 1
    if len(tweet_text) > budget:
        tweet_text = tweet_text[:budget].rstrip()
    return f"{tweet_text}\n{url}"


# ── Imagem ────────────────────────────────────────────────────────────────────

def find_cover(slug):
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        p = os.path.join(COVERS_DIR, f"{slug}.{ext}")
        if os.path.exists(p):
            return p
    return None


def to_jpeg(image_path):
    if image_path.lower().endswith((".jpg", ".jpeg")):
        return image_path, False
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    with Image.open(image_path) as img:
        img.convert("RGB").save(tmp.name, "JPEG", quality=92)
    return tmp.name, True


# ── Sessão ────────────────────────────────────────────────────────────────────

def write_session_file():
    """Decodifica X_SESSION_JSON (base64) e escreve em arquivo temporário."""
    if not X_SESSION_JSON:
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    decoded = base64.b64decode(X_SESSION_JSON).decode("utf-8")
    tmp.write(decoded)
    tmp.close()
    return tmp.name


# ── Playwright ────────────────────────────────────────────────────────────────

def _human_delay(lo=0.4, hi=1.0):
    time.sleep(random.uniform(lo, hi))


def make_browser(pw):
    return pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def make_context(browser, session_path=None):
    kwargs = dict(
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1280, "height": 800},
    )
    if session_path:
        kwargs["storage_state"] = session_path
    return browser.new_context(**kwargs)


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


def post_tweet(page, tweet_text, image_path=None):
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20_000)
    _human_delay(2.0, 3.0)

    page.click('[data-testid="SideNav_NewTweet_Button"]')
    _human_delay(1.0, 2.0)

    composer = page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=10_000)
    composer.click()
    _human_delay(0.5, 1.0)

    page.keyboard.type(tweet_text, delay=random.randint(25, 55))
    _human_delay(0.8, 1.5)

    if image_path:
        jpeg_path, tmp_created = to_jpeg(image_path)
        try:
            file_input = page.locator('input[data-testid="fileInput"]').first
            file_input.set_input_files(jpeg_path)
            try:
                page.wait_for_selector('[data-testid="attachments"]', timeout=25_000)
                _human_delay(1.5, 2.5)
                print("  Imagem anexada.")
            except PWTimeout:
                print("  [Aviso] Thumbnail não apareceu — postando sem imagem.")
        finally:
            if tmp_created:
                os.unlink(jpeg_path)

    _human_delay(0.5, 1.0)

    clicked = False
    for sel in ('[data-testid="tweetButton"]', '[data-testid="tweetButtonInline"]'):
        try:
            btn = page.wait_for_selector(f"{sel}:not([disabled])", timeout=5_000)
            _human_delay(0.3, 0.7)
            btn.click()
            clicked = True
            break
        except PWTimeout:
            continue

    if not clicked:
        page.screenshot(path=os.path.join(SCRIPT_DIR, "ci_debug_no_button.png"))
        return False

    _human_delay(3.5, 4.5)
    el = page.query_selector('[data-testid="tweetTextarea_0"]')
    if el is None or not el.is_visible():
        return True
    if not (el.text_content() or "").strip():
        return True

    page.screenshot(path=os.path.join(SCRIPT_DIR, "ci_debug_post_failed.png"))
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(" X CI Publisher — Resenha da Nação")
    print("=" * 60)

    if not X_SESSION_JSON:
        print("[Erro] X_SESSION_JSON não definido.")
        print("       Gere localmente: base64 -i x_session.json | tr -d '\\n'")
        print("       Adicione como GitHub Secret X_SESSION_JSON.")
        sys.exit(1)

    new_entries = get_new_slugs()
    if not new_entries:
        print("Nenhum artigo novo detectado. Encerrando.")
        return

    print(f"\n{len(new_entries)} artigo(s) novo(s) detectado(s):")
    for e in new_entries:
        print(f"  - {e['slug'][:70]}")

    session_path = write_session_file()

    posted = 0
    with sync_playwright() as pw:
        browser = make_browser(pw)
        context = make_context(browser, session_path)
        page    = new_page(context)

        if not is_logged_in(page):
            print("[Erro] Sessão inválida ou expirada.")
            print("       Rode x_publish_0_1.py localmente para renovar a sessão,")
            print("       atualize o Secret X_SESSION_JSON e rode o pipeline de novo.")
            context.close()
            browser.close()
            sys.exit(1)

        print("\nSessão válida. Iniciando postagens...\n")

        for entry in new_entries:
            slug     = entry["slug"]
            editoria = entry.get("editoria", "geral")
            file_    = entry.get("file", "")

            print(f"-> {slug[:70]}")

            # Carrega artigo
            article_path = os.path.join(ARTICLES_DIR, file_)
            if not os.path.exists(article_path):
                print("  [Erro] Arquivo do artigo não encontrado. Pulando.")
                continue

            with open(article_path) as f:
                article_json = json.load(f)

            author       = article_json.get("author", "")
            article_data = article_json.get("article", {})
            persona      = load_persona(author)

            print(f"  Jornalista: {author}")
            print("  Gerando tweet...")
            tweet_text = generate_tweet(article_data, persona, editoria, slug)
            if not tweet_text:
                print("  [Erro] Falha ao gerar tweet. Pulando.")
                continue

            full_tweet = assemble_tweet(tweet_text, slug)
            print(f"  Tweet ({len(tweet_text)} + link):\n  {tweet_text[:100]}...")

            cover_path = find_cover(slug)

            print("  Postando...")
            try:
                ok = post_tweet(page, full_tweet, cover_path)
            except Exception as err:
                print(f"  [Erro] {err}")
                continue

            if ok:
                posted += 1
                print(f"  [OK] Publicado!\n")
                _human_delay(3.0, 5.0)
            else:
                print("  [Erro] Tweet pode não ter sido enviado. Verifique o X.\n")

        context.close()
        browser.close()

    if session_path:
        os.unlink(session_path)

    print("=" * 60)
    print(f" Concluído. {posted}/{len(new_entries)} post(s) publicado(s).")
    print("=" * 60)

    if posted < len(new_entries):
        sys.exit(1)   # falha parcial sinaliza erro no CI


if __name__ == "__main__":
    main()
