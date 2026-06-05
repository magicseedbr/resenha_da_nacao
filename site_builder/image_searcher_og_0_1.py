"""
Image Searcher (og:image) — Resenha da Nação.

Para cada artigo aprovado em approved.json, abre a matéria original
(source_ref.source_url no JSON do artigo) e extrai a imagem de capa declarada
pela própria fonte (meta og:image / twitter:image). Baixa a imagem para
static/covers/<slug>.<ext> e grava o caminho no campo "cover" do approved.json,
de modo que build_site.py exiba a capa real (com fallback para capa_padrao.svg).

Diferente do image_searcher antigo (raspagem do Google, hoje bloqueada), esta
abordagem não depende de buscadores: a imagem vem da própria notícia, então é
sempre relacionada e não sofre challenge de JavaScript.

Uso:
    python image_searcher_og_0_1.py            # só os aprovados ainda sem capa
    python image_searcher_og_0_1.py --force    # rebaixa a capa de todos
"""

import os
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import shared

COVERS_DIR = os.path.join(shared.STATIC_DIR, "covers")

# Headers de navegador para receber o HTML padrão da matéria (não a versão bot).
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Content-Type -> extensão de arquivo.
_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def extract_cover_url(html, base_url):
    """Extrai a URL da imagem de capa do HTML (og:image > twitter:image)."""
    soup = BeautifulSoup(html, "html.parser")

    candidates = [
        ("meta", {"property": "og:image"}),
        ("meta", {"property": "og:image:url"}),
        ("meta", {"name": "twitter:image"}),
        ("meta", {"name": "twitter:image:src"}),
    ]
    for tag_name, attrs in candidates:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get("content"):
            return urljoin(base_url, tag["content"].strip())

    return None


def download_cover(img_url, slug):
    """Baixa a imagem para static/covers/<slug>.<ext>. Retorna o caminho relativo."""
    response = requests.get(img_url, headers=HTTP_HEADERS, timeout=12, stream=True)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    ext = _EXT_BY_TYPE.get(content_type, ".jpg")

    os.makedirs(COVERS_DIR, exist_ok=True)
    filename = f"{slug}{ext}"
    file_path = os.path.join(COVERS_DIR, filename)

    with open(file_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    # Caminho relativo no formato que os templates consomem ({{ root }}{{ cover }}).
    return f"static/covers/{filename}"


def _has_valid_cover(entry):
    """True se o entry já aponta para um arquivo de capa existente em disco."""
    cover = entry.get("cover")
    if not cover:
        return False
    return os.path.exists(os.path.join(shared.SCRIPT_DIR, cover))


def fetch_cover_for_entry(entry):
    """Busca e baixa a capa de um artigo aprovado. Retorna o caminho ou None."""
    slug = entry["slug"]

    try:
        data = shared.load_article(entry["file"])
    except Exception as err:
        print(f" [Aviso] Falha ao ler '{entry['file']}': {err}. Pulando.")
        return None

    source_url = data.get("source_ref", {}).get("source_url", "").strip()
    if not source_url:
        print(f" [Aviso] '{slug}' não tem source_url. Ficará com a capa-padrão.")
        return None

    try:
        page = requests.get(source_url, headers=HTTP_HEADERS, timeout=10)
        page.raise_for_status()
    except Exception as err:
        print(f" [Aviso] Não consegui abrir a matéria de '{slug}': {err}.")
        return None

    img_url = extract_cover_url(page.text, source_url)
    if not img_url:
        print(f" [Aviso] '{slug}': a fonte não declara og:image/twitter:image.")
        return None

    print(f" -> {slug}: capa encontrada {img_url[:70]}...")
    try:
        return download_cover(img_url, slug)
    except Exception as err:
        print(f" [Aviso] Falha ao baixar a capa de '{slug}': {err}.")
        return None


def main():
    force = "--force" in sys.argv[1:]

    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Busca de capa (og:image da matéria original)")
    print("=" * 70)

    approved = shared.load_approved()
    if not approved:
        print(" [Aviso] Nenhum artigo aprovado em approved.json.")
        print(" Rode 'python approve.py' antes desta etapa.")
        return

    obtained = 0
    skipped = 0
    failed = 0

    for entry in approved:
        if not force and _has_valid_cover(entry):
            skipped += 1
            continue

        cover = fetch_cover_for_entry(entry)
        if cover:
            entry["cover"] = cover
            shared.save_approved(approved)  # persiste a cada sucesso
            obtained += 1
            print(f" [OK] {entry['slug']} -> {cover}")
        else:
            failed += 1

    print("-" * 70)
    print(f" Aprovados : {len(approved)}")
    print(f" Capas novas: {obtained} | Já tinham capa: {skipped} | Sem capa (fallback): {failed}")
    print(" Rode 'python build_site.py' para (re)gerar o site com as capas reais.")
    print("=" * 70)


if __name__ == "__main__":
    main()
