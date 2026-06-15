"""
Gerador de posts para o X (Twitter) — Resenha da Nação.

Para cada artigo aprovado ainda não postado, gera um tweet com:
  - Texto de até ~240 chars no estilo do jornalista
  - Link direto para o artigo no site
  - Hashtags rubro-negras

Limite do X: 280 chars. URLs são sempre 23 chars (t.co).
Texto + hashtags ficam em até 255 chars; o script completa com a URL.

Saída: x_posts/<slug>/tweet.txt
Histórico: x_posts/published_x.json
"""

import os
import json
import shutil
import subprocess
from datetime import datetime
from google import genai
from groq import Groq

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Constantes ────────────────────────────────────────────────────────────────
OUTPUT_DIR     = os.path.join(SCRIPT_DIR, "x_posts")
PUBLISHED_FILE = os.path.join(OUTPUT_DIR, "published_x.json")
PERSONAS_DIR   = os.path.abspath(os.path.join(SCRIPT_DIR, "../article_writer/personas"))
COVERS_DIR     = os.path.abspath(os.path.join(SCRIPT_DIR, "../site_builder/static/covers"))

GEMINI_MODEL_NAME = "gemini-2.5-flash"
GROQ_MODEL_NAME   = "llama-3.3-70b-versatile"

# No X, URLs são sempre encurtadas para 23 chars pelo t.co
_URL_COST = 23
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


# ── Helpers de ambiente ───────────────────────────────────────────────────────

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
SITE_BASE_URL  = os.environ.get("SITE_BASE_URL", "").rstrip("/")


# ── Git ───────────────────────────────────────────────────────────────────────

def read_from_git(git_path):
    try:
        result = subprocess.run(
            ["git", "show", f"origin/main:{git_path}"],
            cwd=os.path.dirname(SCRIPT_DIR),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise IOError(f"Erro ao ler {git_path} do Git: {e.stderr.strip()}")


def read_json_from_git(git_path):
    return json.loads(read_from_git(git_path))


# ── Capa ─────────────────────────────────────────────────────────────────────

def find_cover(slug):
    """Retorna (caminho_absoluto, extensão) da capa, ou (None, None) se não encontrada."""
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        path = os.path.join(COVERS_DIR, f"{slug}.{ext}")
        if os.path.exists(path):
            return path, ext
    return None, None


# ── Publicados ────────────────────────────────────────────────────────────────

def load_published():
    if not os.path.exists(PUBLISHED_FILE):
        return []
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_published(published):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(published, f, ensure_ascii=False, indent=4)


# ── Personas ──────────────────────────────────────────────────────────────────

def load_persona(author):
    filename = PERSONA_FILENAME_MAP.get(author)
    if not filename:
        return ""
    path = os.path.join(PERSONAS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except IOError:
        return ""


# ── Geração do tweet ──────────────────────────────────────────────────────────

def build_hashtags(editoria):
    extra = HASHTAGS_BY_EDITORIA.get(editoria, "")
    return f"{HASHTAGS_BASE} {extra}".strip() if extra else HASHTAGS_BASE


def article_url(slug):
    base = SITE_BASE_URL or "https://www.resenhadanacao.com.br"
    # Garante www. no domínio para o X exibir o subdomínio
    if "://resenhadanacao.com.br" in base:
        base = base.replace("://resenhadanacao.com.br", "://www.resenhadanacao.com.br")
    return f"{base}/artigo/{slug}.html"


def _build_prompt(article_data, persona_content, editoria, slug):
    title    = article_data.get("title", "")
    subtitle = article_data.get("subtitle", "")
    body     = article_data.get("body", "")
    hashtags = build_hashtags(editoria)

    # O link ocupa 23 chars fixos + "\n" = 24. Texto + hashtags: até 255 chars.
    max_text_chars = _MAX_TWEET - _URL_COST - 1  # 256

    return f"""Você é o(a) jornalista abaixo. Incorpore completamente o estilo descrito.

--- PERFIL DO JORNALISTA ---
{persona_content}
--- FIM DO PERFIL ---

Escreva um POST PARA O X (Twitter) sobre o artigo abaixo, para o perfil @ResenhadaNacao.

REGRAS:
- Tom nativo do X: direto, rápido, pode ser provocador ou empolgado — sem enrolação
- Até 1 emoji no máximo (pode ter zero; sem excesso de emojis)
- Inclua os hashtags abaixo ao final do texto
- NÃO inclua o link (ele será adicionado automaticamente)
- LIMITE TOTAL: {max_text_chars} caracteres (texto + hashtags juntos)
- Conte os caracteres antes de responder e respeite o limite
- Use linguagem brasileira informal com sotaque carioca
- Responda SOMENTE com JSON válido, sem markdown, sem texto fora do JSON

--- ARTIGO ---
Título: {title}
Linha de apoio: {subtitle}

Corpo (trecho):
{body[:1500]}
--- FIM DO ARTIGO ---

Hashtags a incluir ao final:
{hashtags}

JSON esperado:
{{
    "tweet": "<texto completo com hashtags, SEM o link, dentro de {max_text_chars} chars>"
}}
"""


def _generate_via_gemini(prompt):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY não definida")
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(response.text).get("tweet", "")


def _generate_via_groq(prompt):
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY não definida")
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content).get("tweet", "")


def generate_tweet_text(article_data, persona_content, editoria, slug):
    prompt = _build_prompt(article_data, persona_content, editoria, slug)

    try:
        text = _generate_via_gemini(prompt)
        print("  (via Gemini)")
        return text
    except Exception as err:
        err_str = str(err)
        if "429" in err_str or "quota" in err_str.lower():
            print("  [Gemini 429] Quota esgotada — tentando Groq...")
        else:
            print(f"  [Gemini falhou] {err_str[:120]} — tentando Groq...")

    try:
        text = _generate_via_groq(prompt)
        print("  (via Groq fallback)")
        return text
    except Exception as err:
        print(f"  [Falha na API] Groq também falhou: {err}")
        return ""


def assemble_tweet(tweet_text, slug):
    """Monta o tweet final: texto + quebra de linha + URL."""
    url = article_url(slug)
    full = f"{tweet_text}\n{url}"

    # Calcula tamanho real: texto + "\n" + 23 (t.co) em vez do URL literal
    real_len = len(tweet_text) + 1 + _URL_COST
    if real_len > _MAX_TWEET:
        print(f"  [Aviso] Tweet longo demais ({real_len} chars). Truncando texto.")
        # Trunca o texto para caber; mantém hashtags se possível
        budget = _MAX_TWEET - _URL_COST - 1  # 256
        tweet_text = tweet_text[:budget].rstrip()
        full = f"{tweet_text}\n{url}"

    return full, real_len


# ── Processamento por artigo ──────────────────────────────────────────────────

def folder_name(entry):
    """Retorna o nome da pasta: YYYY-MM-DD_<slug>."""
    approved_at = entry.get("approved_at", "")
    date_str = approved_at[:10] if approved_at else datetime.now().strftime("%Y-%m-%d")
    return f"{date_str}_{entry.get('slug', '')}"


def process_article(entry, published):
    slug     = entry.get("slug", "")
    editoria = entry.get("editoria", "geral")
    file_    = entry.get("file", "")

    if slug in published:
        print("  [Ignorado] Já processado.")
        return False

    try:
        article_json = read_json_from_git(f"article_writer/generated_articles/{file_}")
    except IOError as e:
        print(f"  [Erro] {e}")
        return False

    author       = article_json.get("author", "")
    article_data = article_json.get("article", {})

    cover_src, cover_ext = find_cover(slug)
    if not cover_src:
        print("  [Aviso] Capa não encontrada — post criado sem imagem.")

    persona_content = load_persona(author)
    if not persona_content:
        print(f"  [Aviso] Persona de '{author}' não encontrada.")

    print(f"  Jornalista: {author}")
    print("  Gerando tweet...")
    tweet_text = generate_tweet_text(article_data, persona_content, editoria, slug)

    if not tweet_text:
        print("  [Erro] Falha ao gerar tweet.")
        return False

    full_tweet, char_count = assemble_tweet(tweet_text, slug)

    dirname  = folder_name(entry)
    post_dir = os.path.join(OUTPUT_DIR, dirname)
    os.makedirs(post_dir, exist_ok=True)

    with open(os.path.join(post_dir, "tweet.txt"), "w", encoding="utf-8") as f:
        f.write(full_tweet)

    if cover_src:
        shutil.copy2(cover_src, os.path.join(post_dir, f"image.{cover_ext}"))

    post_data = {
        "created_at":    datetime.now().isoformat(),
        "slug":          slug,
        "editoria":      editoria,
        "author":        author,
        "article_title": article_data.get("title", ""),
        "tweet_chars":   char_count,
        "url":           article_url(slug),
        "has_cover":     cover_src is not None,
    }
    with open(os.path.join(post_dir, "post_data.json"), "w", encoding="utf-8") as f:
        json.dump(post_data, f, ensure_ascii=False, indent=4)

    img_info = f"image.{cover_ext}" if cover_src else "sem imagem"
    print(f"  [OK] Salvo em x_posts/{dirname}/  ({char_count}/280 chars, {img_info})")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("--- Criador de Posts para o X ---\n")

    try:
        approved = read_json_from_git("site_builder/approved.json")
    except IOError as e:
        print(f"[Erro] {e}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    published = load_published()
    new_posts = 0

    for entry in approved:
        slug = entry.get("slug", "")
        print(f"-> {slug[:70]}")
        created = process_article(entry, published)
        if created:
            published.append(slug)
            save_published(published)
            new_posts += 1

    print(f"\n--- Concluído: {new_posts} post(s) novo(s) criado(s) ---")


if __name__ == "__main__":
    main()
