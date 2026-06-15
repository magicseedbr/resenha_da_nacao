"""
Gate de publicação no Instagram — Resenha da Nação.

Revisa os posts gerados em instagram_posts/ que ainda não foram publicados e
permite publicar um a um via Meta Graph API. O registro dos posts publicados
fica em instagram_posts/published_instagram.json.

Pré-requisitos no .env:
    INSTAGRAM_USER_ID      — ID numérico da conta Instagram Business/Creator
    INSTAGRAM_ACCESS_TOKEN — Token de longa duração (60 dias) do Meta
    SITE_BASE_URL          — URL base do site publicado (ex: https://resenhadanacao.com.br)

Uso:
    python instagram_publish_0_1.py
"""

import os
import sys
import json
import requests
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(env_path):
    """Lê um arquivo .env simples (KEY=VALUE) e popula os.environ, sem dependências externas."""
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


POSTS_DIR = os.path.join(SCRIPT_DIR, "instagram_posts")
PUBLISHED_FILE = os.path.join(POSTS_DIR, "published_instagram.json")
GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
INSTAGRAM_USER_ID = os.environ.get("INSTAGRAM_USER_ID", "")
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").rstrip("/")


def load_published():
    if not os.path.exists(PUBLISHED_FILE):
        return {}
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_published(published):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(published, f, ensure_ascii=False, indent=4)


def get_pending_posts(published):
    """Retorna slugs com caption.txt gerada mas ainda não publicados no Instagram."""
    pending = []
    if not os.path.exists(POSTS_DIR):
        return pending
    for slug in sorted(os.listdir(POSTS_DIR)):
        post_dir = os.path.join(POSTS_DIR, slug)
        if not os.path.isdir(post_dir):
            continue
        if not os.path.exists(os.path.join(post_dir, "caption.txt")):
            continue
        if slug in published:
            continue
        pending.append(slug)
    return pending


def get_image_url(slug):
    """Constrói a URL pública da capa a partir do SITE_BASE_URL."""
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        local = os.path.join(POSTS_DIR, slug, f"image.{ext}")
        if os.path.exists(local):
            if SITE_BASE_URL:
                return f"{SITE_BASE_URL}/static/covers/{slug}.{ext}"
    return None


def publish_to_instagram(image_url, caption):
    """
    Publica no Instagram via Meta Graph API (dois passos):
    1. Cria o container de mídia com a imagem e a legenda
    2. Publica o container
    Retorna (post_id, mensagem_de_erro).
    """
    create_url = f"{GRAPH_API_BASE}/{INSTAGRAM_USER_ID}/media"
    resp = requests.post(
        create_url,
        data={"image_url": image_url, "caption": caption, "access_token": INSTAGRAM_ACCESS_TOKEN},
        timeout=30,
    )
    if not resp.ok:
        return None, f"Erro ao criar container ({resp.status_code}): {resp.text}"

    container_id = resp.json().get("id")
    if not container_id:
        return None, f"ID do container não retornado: {resp.text}"

    publish_url = f"{GRAPH_API_BASE}/{INSTAGRAM_USER_ID}/media_publish"
    resp = requests.post(
        publish_url,
        data={"creation_id": container_id, "access_token": INSTAGRAM_ACCESS_TOKEN},
        timeout=30,
    )
    if not resp.ok:
        return None, f"Erro ao publicar ({resp.status_code}): {resp.text}"

    return resp.json().get("id"), None


def _preview(text, limit=200):
    flat = " ".join(text.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def main():
    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Publicação no Instagram")
    print("=" * 70)

    missing = [v for v in ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN", "SITE_BASE_URL") if not os.environ.get(v)]
    if missing:
        print(f"\n [Erro] Variáveis não definidas no .env: {', '.join(missing)}")
        print(" Adicione-as ao arquivo .env na raiz do projeto e tente novamente.")
        print("\n Exemplo:")
        print("   INSTAGRAM_USER_ID=123456789")
        print("   INSTAGRAM_ACCESS_TOKEN=EAAxxxxxx...")
        print("   SITE_BASE_URL=https://resenhadanacao.com.br")
        sys.exit(1)

    published = load_published()
    pending = get_pending_posts(published)

    print(f"\n Posts prontos: {len(pending)} aguardando publicação.\n")

    if not pending:
        print(" Nenhum post pendente. Tudo já foi publicado.")
        return

    novos = 0
    for i, slug in enumerate(pending, 1):
        post_dir = os.path.join(POSTS_DIR, slug)

        with open(os.path.join(post_dir, "caption.txt"), "r", encoding="utf-8") as f:
            caption = f.read().strip()

        post_data = {}
        post_data_path = os.path.join(post_dir, "post_data.json")
        if os.path.exists(post_data_path):
            with open(post_data_path, "r", encoding="utf-8") as f:
                post_data = json.load(f)

        image_url = get_image_url(slug)

        print("-" * 70)
        print(f" [{i}/{len(pending)}] {post_data.get('article_title', slug)}")
        print(f" Jornalista : {post_data.get('author', '?')}")
        print(f" Editoria   : {post_data.get('editoria', '?')}")
        print(f" Imagem URL : {image_url or '[nao encontrada]'}")
        print(f" Legenda    : {_preview(caption)}")
        print(f" Caracteres : {len(caption)}")
        print("-" * 70)

        if not image_url:
            print(" [Aviso] Imagem nao disponivel publicamente. Verifique se o site esta")
            print(f"         deployado e SITE_BASE_URL={SITE_BASE_URL} esta correto.")

        resp = input(" [p] publicar  /  [n] pular  /  [s] sair  > ").strip().lower()

        if resp in ("s", "sair", "q", "quit"):
            print(" Encerrando. Posts ja publicados foram registrados.")
            break
        if resp not in ("p", "publicar"):
            print(" Pulado.\n")
            continue
        if not image_url:
            print(" [Erro] Nao e possivel publicar sem URL publica da imagem.\n")
            continue

        print(" Publicando...")
        post_id, error = publish_to_instagram(image_url, caption)

        if error:
            print(f" [Erro] {error}\n")
            continue

        published[slug] = {
            "instagram_post_id": post_id,
            "published_at": datetime.now().isoformat(),
        }
        save_published(published)
        novos += 1
        print(f" [OK] Publicado! ID: {post_id}\n")

    print("=" * 70)
    print(f" Concluido. {novos} post(s) publicado(s) nesta sessao.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n Interrompido.")
        sys.exit(0)
