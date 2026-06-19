"""
Utilidades compartilhadas entre approve.py e build_site.py.

Concentra: resolução de caminhos, geração de slug, derivação de editoria por
palavras-chave e mapeamento autor -> persona (com extração da bio).
"""

import os
import re
import json
import glob
import unicodedata

# --- Caminhos ---------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

GENERATED_ARTICLES_DIR = os.path.join(
    PROJECT_DIR, "article_writer", "generated_articles"
)
PERSONAS_DIR = os.path.join(PROJECT_DIR, "article_writer", "personas")

APPROVED_FILE = os.path.join(SCRIPT_DIR, "approved.json")
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "templates")
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

# --- Editorias --------------------------------------------------------------

# Slug -> rótulo exibido no site. A ordem aqui define a ordem do menu.
EDITORIAS = {
    "mercado": "Mercado da Bola",
    "base": "Crias do Ninho",
    "rivais": "Rivais",
    "selecao": "Seleção",
    "bastidores": "Bastidores",
    "geral": "Geral",
}

# Slug da editoria -> lista de termos (já sem acento, minúsculos). A primeira
# editoria com ao menos um termo presente vence; "geral" é o fallback.
_EDITORIA_KEYWORDS = {
    # Só contexto REAL de base. "ninho"/"gavea"/" base"/"cria" soltos eram
    # gatilhos errados: o Ninho do Urubu é o CT de TODO o elenco, e a Gávea é
    # a sede/diretoria. "Crias do Ninho" = matéria sobre a base, não o CT.
    "base": [
        "sub-20", "sub20", "sub-17", "sub17", "sub-15", "sub15",
        "cria do ninho", "crias do ninho", "moleque do ninho",
        "molecada do ninho", "garoto do ninho", "garotos do ninho",
        "joia da base", "categoria de base", "categorias de base",
        "revelado pel", "formado na base", "formado nas categorias",
        "base rubro-negra",
    ],
    # Matéria centrada em outro clube (rival). Nomes/apelidos distintivos dos
    # rivais — captura notícias sobre o adversário (demissão, reforço, crise).
    "rivais": [
        "vasco", "vascaino", "cruzeiro", "cruzeirense",
        "palmeiras", "palmeirense", "corinthians", "corintiano",
        "botafogo", "fluminense", "tricolor das laranjeiras",
        "sao paulo", "atletico-mg", "atletico mineiro", "galo",
        "gremio", "internacional", "colorado", "athletico",
    ],
    "selecao": ["selecao", "copa do mundo", "ancelotti", "convoca"],
    "mercado": [
        "contrat", "transfer", "reforco", "negocia", "proposta",
        "multa", "alvo", "assina", "mercado",
    ],
    "bastidores": [
        "diretoria", "presidente", "estatuto", "bap", "boto",
        "eleicao", "conselho",
    ],
}

# Ordem de prioridade na hora de classificar (mais específicas primeiro).
_EDITORIA_PRIORITY = ["base", "rivais", "selecao", "mercado", "bastidores"]


def _strip_accents(text):
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def slugify(text):
    """Converte um texto em slug seguro para URL: minúsculo, sem acento, com hífens."""
    text = _strip_accents(text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "artigo"


def derive_editoria(title, body=""):
    """Deriva o slug da editoria a partir de palavras-chave no título e corpo."""
    haystack = _strip_accents(f"{title} {body}".lower())
    for editoria in _EDITORIA_PRIORITY:
        for term in _EDITORIA_KEYWORDS[editoria]:
            if term in haystack:
                return editoria
    return "geral"


def editoria_label(slug):
    """Rótulo exibível da editoria; cai em 'Geral' para slugs desconhecidos."""
    return EDITORIAS.get(slug, EDITORIAS["geral"])


# --- Personas / jornalistas -------------------------------------------------


def author_to_persona_path(author):
    """'Thiago Vasconcelos' -> caminho de thiago_vasconcelos.md (ou None)."""
    filename = _strip_accents(author or "").lower().strip().replace(" ", "_") + ".md"
    path = os.path.join(PERSONAS_DIR, filename)
    return path if os.path.exists(path) else None


def extract_persona_bio(author):
    """Extrai o texto da seção '## Bio' da persona do autor (string, pode ser vazia)."""
    path = author_to_persona_path(author)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except IOError:
        return ""

    # Captura tudo entre '## Bio' e o próximo cabeçalho '##'.
    match = re.search(r"##\s*Bio\s*\n(.*?)(?:\n##\s|\Z)", content, re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def author_initials(author):
    """Iniciais do autor para o avatar (ex.: 'Thiago Vasconcelos' -> 'TV')."""
    parts = [p for p in (author or "").split() if p]
    if not parts:
        return "RN"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# --- approved.json ----------------------------------------------------------


def load_approved():
    """Carrega o registro de artigos aprovados como lista (vazia se ausente)."""
    if not os.path.exists(APPROVED_FILE):
        return []
    try:
        with open(APPROVED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def save_approved(entries):
    """Persiste o registro de aprovados (lista de dicts)."""
    with open(APPROVED_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=4)


def load_article(filename):
    """Carrega um JSON de artigo de generated_articles/ pelo nome do arquivo."""
    path = os.path.join(GENERATED_ARTICLES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_generated_articles():
    """Nomes dos arquivos JSON em generated_articles/, ordenados (mais antigo primeiro)."""
    pattern = os.path.join(GENERATED_ARTICLES_DIR, "*.json")
    return sorted(os.path.basename(p) for p in glob.glob(pattern))
