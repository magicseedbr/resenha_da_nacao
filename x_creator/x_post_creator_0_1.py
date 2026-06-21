"""
Gerador de posts para o X (Twitter) — Resenha da Nação.

Estratégia (conta de torcida): o alcance no X morre quando o post manda a
pessoa pra fora da plataforma ou parece automatizado. Por isso cada post agora
é uma THREAD de 2 tweets:
  1. tweet.txt  — hook com tensão/opinião/provocação que termina puxando reply.
                  SEM link e SEM hashtags (engajamento distribui, hashtag não).
  2. reply.txt  — primeiro reply, só com o link do artigo (o tráfego vai por aqui).

Limite do X: 280 chars por tweet. Como o link saiu do tweet principal, o hook
tem os 280 chars inteiros disponíveis.

Saída: x_posts/<slug>/{tweet.txt, reply.txt, image.*, post_data.json}
Histórico: x_posts/published_x_posted.json
"""

import os
import sys
import json
import shutil
import hashlib
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(SCRIPT_DIR, "..")))
import llm

# ── Constantes ────────────────────────────────────────────────────────────────
OUTPUT_DIR      = os.path.join(SCRIPT_DIR, "x_posts")
PUBLISHED_FILE  = os.path.join(OUTPUT_DIR, "published_x_posted.json")  # fonte da verdade: já postado no X
TO_POST_FILE    = os.path.join(OUTPUT_DIR, "to_post.json")
PERSONAS_DIR   = os.path.abspath(os.path.join(SCRIPT_DIR, "../article_writer/personas"))
COVERS_DIR     = os.path.abspath(os.path.join(SCRIPT_DIR, "../site_builder/static/covers"))

_MAX_TWEET = 280
# Margem de segurança para o hook (o modelo às vezes estoura a contagem)
_HOOK_BUDGET = 270

# Estilos de fechamento do hook — escolhidos de forma determinística por slug
# para variar entre posts e não saturar (pergunta repetida vira ruído, igual
# hashtag). Inclui opções SEM pergunta: às vezes a afirmação categórica provoca
# mais reply, porque o leitor sente que precisa te corrigir.
HOOK_CLOSERS = [
    'Feche com uma pergunta curta tipo "concorda?" (NÃO use "quem discorda?", já saturou).',
    'Feche com uma pergunta tipo "tô errado?" ou "tô viajando?".',
    'Feche com uma provocação curta tipo "fala aí" ou "quem tá comigo?".',
    'NÃO faça pergunta nenhuma. Feche com uma AFIRMAÇÃO categórica e polêmica — '
    'daquelas que fazem o leitor querer te corrigir nos comentários.',
    'NÃO faça pergunta. Feche com um desafio curto tipo "me prova o contrário".',
]

# Teasers de reserva (se o modelo não devolver reply_teaser) — também variados.
FALLBACK_TEASERS = [
    "Tem um detalhe nessa história que muda tudo — botei tudo aqui 👇",
    "O que ninguém tá falando sobre isso eu coloquei aqui 👇",
    "A real, sem filtro, tá na resenha completa 👇",
    "Expliquei o que tá por trás disso aqui 👇",
]

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


def read_json_source(repo_path):
    """Prefere o arquivo local (fluxo local-first: artigo aprovado nesta máquina,
    ainda não commitado/pushado pro main). Só faz fallback para o origin/main via
    git quando o arquivo não existe no disco — mantendo o comportamento de CI,
    onde o working tree já é o próprio main."""
    local_path = os.path.join(os.path.dirname(SCRIPT_DIR), repo_path)
    if os.path.exists(local_path):
        with open(local_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return read_json_from_git(repo_path)


# ── Capa ─────────────────────────────────────────────────────────────────────

def find_cover(slug):
    """Retorna (caminho_absoluto, extensão) da capa, ou (None, None) se não encontrada."""
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        path = os.path.join(COVERS_DIR, f"{slug}.{ext}")
        if os.path.exists(path):
            return path, ext
    return None, None


# ── Publicados (já postados no X) ────────────────────────────────────────────

def load_published():
    """Retorna dict {dirname: {...}} dos posts já publicados no X."""
    if not os.path.exists(PUBLISHED_FILE):
        return {}
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


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

def article_url(slug):
    base = SITE_BASE_URL or "https://www.resenhadanacao.com.br"
    # Garante www. no domínio para o X exibir o subdomínio
    if "://resenhadanacao.com.br" in base:
        base = base.replace("://resenhadanacao.com.br", "://www.resenhadanacao.com.br")
    return f"{base}/artigo/{slug}.html"


def _slug_index(slug, n):
    """Índice determinístico em [0, n) a partir do slug (estável entre runs)."""
    h = int(hashlib.md5(slug.encode("utf-8")).hexdigest(), 16)
    return h % n


def pick_closer(slug):
    return HOOK_CLOSERS[_slug_index(slug, len(HOOK_CLOSERS))]


def _build_prompt(article_data, persona_content, closer):
    title    = article_data.get("title", "")
    subtitle = article_data.get("subtitle", "")
    body     = article_data.get("body", "")

    return f"""Você é o(a) jornalista abaixo. Incorpore completamente o estilo descrito.

--- PERFIL DO JORNALISTA ---
{persona_content}
--- FIM DO PERFIL ---

Você vai escrever uma THREAD de 2 tweets sobre o artigo abaixo, para o perfil
@ResenhadaNacao — uma página de TORCIDA do Flamengo. O objetivo NÃO é informar:
é fazer o torcedor PARAR o scroll e RESPONDER. No X de 2026, engajamento (reply)
distribui o post; comunicado não alcança ninguém.

TWEET 1 — O HOOK (siga à risca):
- Comece com TENSÃO ou uma OPINIÃO forte e divisível — algo que rubro-negro defende
  e torcedor de rival vem brigar. Nada de "Nossos guerreiros honrando a Nação".
- Munição pra discussão: comparação com rival, alguém ignorado/subestimado,
  ranking polêmico, provocação, indignação. Identidade e briga, não anúncio.
- FECHAMENTO (importante, varia a cada post): {closer}
- Pode usar quebras de linha curtas pra dar ritmo (estilo nativo do X).
- LIMITE: {_HOOK_BUDGET} caracteres. Conte antes de responder e respeite.

TWEET 2 — O REPLY (o link entra aqui automaticamente, você NÃO escreve o link):
- Escreva UMA linha de CURIOSIDADE que AUMENTA a tensão e dá motivo pra clicar,
  SEM entregar a resposta. Pense em "gancho de retenção", não em "aqui está o link".
- Exemplos de tom: "Tem um detalhe nessa convocação que muda tudo 👇" /
  "O que ninguém tá falando sobre essa decisão eu botei aqui:".
- NÃO repita o hook. NÃO escreva "Resenha completa aqui" puro e seco. Máx ~120 chars.

PROIBIDO (nos dois tweets):
- FATOS: baseie-se SOMENTE no que está escrito no artigo abaixo. NÃO invente nem
  acrescente clubes, nomes, números, valores, datas, placares ou declarações que
  não estejam no corpo do artigo. Se não está no artigo, NÃO entra no tweet. A
  tensão tem que vir de um fato real do artigo, nunca de algo inventado.
- NÃO inclua link (ele vai no reply automaticamente).
- NÃO use NENHUMA hashtag.
- NÃO faça frase de aquecimento genérica nem o mesmo padrão de sempre.
- No máximo 1 emoji por tweet (zero é ótimo no hook).

Português brasileiro informal, sotaque carioca, tom da persona acima.
Responda SOMENTE com JSON válido, sem markdown, sem texto fora do JSON.

--- ARTIGO ---
Título: {title}
Linha de apoio: {subtitle}

Corpo (trecho):
{body[:1500]}
--- FIM DO ARTIGO ---

JSON esperado:
{{
    "tweet": "<hook com tensão, fechamento conforme instruído, SEM link, SEM hashtag, até {_HOOK_BUDGET} chars>",
    "reply_teaser": "<linha de curiosidade que puxa o clique, SEM o link, SEM hashtag, até ~120 chars>"
}}
"""


def generate_tweet_text(article_data, persona_content, slug):
    """Retorna (hook, reply_teaser). reply_teaser pode vir vazio (usa fallback)."""
    prompt = _build_prompt(article_data, persona_content, pick_closer(slug))

    try:
        data = json.loads(llm.generate(prompt))
        print("  (via Claude)")
        return data.get("tweet", ""), data.get("reply_teaser", "")
    except Exception as err:
        print(f"  [Claude falhou] {str(err)[:120]} — sem fallback.")
        return "", ""


def clean_hook(tweet_text):
    """Garante que o hook caiba em 280 chars (sem link/hashtag por construção)."""
    text = tweet_text.strip()
    if len(text) > _MAX_TWEET:
        print(f"  [Aviso] Hook longo demais ({len(text)} chars). Truncando.")
        text = text[:_MAX_TWEET].rstrip()
    return text, len(text)


def build_reply(slug, teaser=""):
    """Primeiro reply: teaser de curiosidade + link (o tráfego vai por aqui).

    Usa o teaser gerado pelo modelo; se vier vazio, cai num fallback variado por
    slug. O teaser nunca traz o link — ele é anexado aqui.
    """
    teaser = (teaser or "").strip()
    if not teaser:
        teaser = FALLBACK_TEASERS[_slug_index(slug, len(FALLBACK_TEASERS))]
    # Cap defensivo: deixa folga pro link (t.co = 23) dentro dos 280
    if len(teaser) > 240:
        teaser = teaser[:240].rstrip()
    return f"{teaser}\n{article_url(slug)}"


# ── Processamento por artigo ──────────────────────────────────────────────────

def folder_name(entry):
    """Retorna o nome da pasta: YYYY-MM-DD_<slug>."""
    approved_at = entry.get("approved_at", "")
    date_str = approved_at[:10] if approved_at else datetime.now().strftime("%Y-%m-%d")
    return f"{date_str}_{entry.get('slug', '')}"


def process_article(entry, posted):
    """Gera ou re-enfileira o post. Retorna dirname se deve ser postado, None caso contrário."""
    slug     = entry.get("slug", "")
    editoria = entry.get("editoria", "geral")
    file_    = entry.get("file", "")
    dirname  = folder_name(entry)
    post_dir = os.path.join(OUTPUT_DIR, dirname)

    # Já foi postado no X — pula
    if dirname in posted:
        print("  [Ignorado] Já publicado no X.")
        return None

    tweet_file = os.path.join(post_dir, "tweet.txt")

    # Tweet já existe (gerado antes) mas NÃO está em `posted` (o check acima já
    # filtrou os publicados): re-enfileira para não perder o post no fluxo
    # gerar -> revisar -> publicar. Não regenera o texto — só mantém na fila.
    if os.path.exists(tweet_file):
        print("  [Re-enfileirado] Tweet já gerado, ainda não publicado.")
        return dirname

    # Tweet ainda não existe — gera agora
    try:
        article_json = read_json_source(f"article_writer/generated_articles/{file_}")
    except IOError as e:
        print(f"  [Erro] {e}")
        return None

    author       = article_json.get("author", "")
    article_data = article_json.get("article", {})

    cover_src, cover_ext = find_cover(slug)
    if not cover_src:
        print("  [Aviso] Capa não encontrada — post criado sem imagem.")

    persona_content = load_persona(author)
    if not persona_content:
        print(f"  [Aviso] Persona de '{author}' não encontrada.")

    print(f"  Jornalista: {author}")
    print("  Gerando hook + reply...")
    tweet_text, reply_teaser = generate_tweet_text(article_data, persona_content, slug)

    if not tweet_text:
        print("  [Erro] Falha ao gerar tweet.")
        return None

    hook, char_count = clean_hook(tweet_text)
    reply_text       = build_reply(slug, reply_teaser)

    os.makedirs(post_dir, exist_ok=True)

    with open(tweet_file, "w", encoding="utf-8") as f:
        f.write(hook)

    with open(os.path.join(post_dir, "reply.txt"), "w", encoding="utf-8") as f:
        f.write(reply_text)

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
        "reply":         reply_text,
        "has_cover":     cover_src is not None,
    }
    with open(os.path.join(post_dir, "post_data.json"), "w", encoding="utf-8") as f:
        json.dump(post_data, f, ensure_ascii=False, indent=4)

    img_info = f"image.{cover_ext}" if cover_src else "sem imagem"
    print(f"  [OK] Salvo em x_posts/{dirname}/  (hook {char_count}/280 chars, + reply c/ link, {img_info})")
    return dirname


# ── Main ──────────────────────────────────────────────────────────────────────

def save_to_post_queue(dirnames):
    with open(TO_POST_FILE, "w", encoding="utf-8") as f:
        json.dump(dirnames, f, ensure_ascii=False, indent=4)


def main():
    print("--- Criador de Posts para o X ---\n")

    try:
        approved = read_json_source("site_builder/approved.json")
    except IOError as e:
        print(f"[Erro] {e}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    posted = load_published()   # {dirname: {...}} dos já publicados no X
    queue  = []

    for entry in approved:
        slug = entry.get("slug", "")
        print(f"-> {slug[:70]}")
        dirname = process_article(entry, posted)
        if dirname:
            queue.append(dirname)

    save_to_post_queue(queue)
    print(f"\nFila to_post.json: {queue}")
    print(f"--- Concluído: {len(queue)} post(s) na fila ---")


if __name__ == "__main__":
    main()
