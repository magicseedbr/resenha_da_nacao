"""
Publicador headless para CI (GitHub Actions) — Resenha da Nação.

Lê os posts já gerados em x_posts/ (tweet.txt + image.*) e posta no X
tudo que ainda não consta em published_x_posted.json.

Variáveis de ambiente necessárias (GitHub Secrets):
    X_SESSION_JSON  — conteúdo de x_session.json em base64
                      Gere com: base64 -i x_creator/x_session.json | tr -d '\\n'

Uso:
    python x_creator/x_ci_publisher_0_1.py
"""

import os
import sys
import json
import base64
import tempfile
import time
import random
from datetime import datetime
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
REPO_DIR       = os.path.dirname(SCRIPT_DIR)
POSTS_DIR      = os.path.join(SCRIPT_DIR, "x_posts")
COVERS_DIR     = os.path.join(REPO_DIR, "site_builder", "static", "covers")
TO_POST_FILE   = os.path.join(POSTS_DIR, "to_post.json")
PUBLISHED_FILE = os.path.join(POSTS_DIR, "published_x_posted.json")

X_SESSION_JSON = os.environ.get("X_SESSION_JSON", "")
_STEALTH       = Stealth(navigator_user_agent=False)


# ── Fila explícita ────────────────────────────────────────────────────────────

def load_queue():
    """Lê to_post.json — lista de dirnames gerados nesta execução do pipeline."""
    if not os.path.exists(TO_POST_FILE):
        return []
    try:
        with open(TO_POST_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def clear_queue():
    """Remove to_post.json após publicação bem-sucedida."""
    if os.path.exists(TO_POST_FILE):
        os.remove(TO_POST_FILE)


# ── Log de publicados ─────────────────────────────────────────────────────────

def load_published():
    if not os.path.exists(PUBLISHED_FILE):
        return {}
    try:
        with open(PUBLISHED_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_published(published):
    with open(PUBLISHED_FILE, "w") as f:
        json.dump(published, f, ensure_ascii=False, indent=4)


# ── Posts a publicar ──────────────────────────────────────────────────────────

def get_pending():
    """Retorna só o que está na fila explícita desta execução."""
    queue = load_queue()
    pending = []
    for dirname in queue:
        post_dir = os.path.join(POSTS_DIR, dirname)
        if not os.path.isdir(post_dir):
            print(f"  [Aviso] Pasta não encontrada: {dirname}")
            continue
        if not os.path.exists(os.path.join(post_dir, "tweet.txt")):
            print(f"  [Aviso] tweet.txt ausente: {dirname}")
            continue
        pending.append(dirname)
    return pending


def find_image(post_dir):
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        p = os.path.join(post_dir, f"image.{ext}")
        if os.path.exists(p):
            return p
    # Fallback: busca a capa diretamente em site_builder/static/covers/ pelo slug
    pd_path = os.path.join(post_dir, "post_data.json")
    if os.path.exists(pd_path):
        try:
            with open(pd_path) as f:
                post_data = json.load(f)
            slug = post_data.get("slug", "")
            if slug:
                for ext in ("webp", "jpg", "jpeg", "png", "gif"):
                    p = os.path.join(COVERS_DIR, f"{slug}.{ext}")
                    if os.path.exists(p):
                        return p
        except (json.JSONDecodeError, IOError):
            pass
    return None


# ── Sessão ────────────────────────────────────────────────────────────────────

def write_session_file():
    if not X_SESSION_JSON:
        return None
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    tmp.write(base64.b64decode(X_SESSION_JSON).decode("utf-8"))
    tmp.close()
    return tmp.name


# ── Imagem ────────────────────────────────────────────────────────────────────

def to_jpeg(image_path):
    if image_path.lower().endswith((".jpg", ".jpeg")):
        return image_path, False
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    with Image.open(image_path) as img:
        img.convert("RGB").save(tmp.name, "JPEG", quality=92)
    return tmp.name, True


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
    context = browser.new_context(**kwargs)
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
        sys.exit(1)

    pending = get_pending()

    if not pending:
        print("Fila to_post.json vazia ou ausente. Nada a publicar.")
        return

    print(f"\n{len(pending)} post(s) na fila:")
    for d in pending:
        print(f"  - {d}")

    session_path = write_session_file()
    published    = load_published()
    posted       = 0

    try:
        with sync_playwright() as pw:
            browser = make_browser(pw)
            context = make_context(browser, session_path)
            page    = new_page(context)

            if not is_logged_in(page):
                print("[Erro] Sessão inválida ou expirada.")
                print("       Rode x_publish_0_1.py localmente, atualize X_SESSION_JSON e repita.")
                context.close()
                browser.close()
                sys.exit(1)

            print("\nSessão válida. Postando...\n")

            for dirname in pending:
                post_dir   = os.path.join(POSTS_DIR, dirname)
                tweet_file = os.path.join(post_dir, "tweet.txt")
                image_path = find_image(post_dir)

                with open(tweet_file) as f:
                    tweet_text = f.read().strip()

                post_data = {}
                pd_path = os.path.join(post_dir, "post_data.json")
                if os.path.exists(pd_path):
                    with open(pd_path) as f:
                        post_data = json.load(f)

                title = post_data.get("article_title", dirname)[:60]
                print(f"-> {title}")
                print(f"   Imagem: {os.path.basename(image_path) if image_path else '[nenhuma]'}")

                try:
                    ok = post_tweet(page, tweet_text, image_path)
                except Exception as err:
                    print(f"   [Erro] {err}\n")
                    continue

                if ok:
                    published[dirname] = {"published_at": datetime.now().isoformat()}
                    save_published(published)
                    posted += 1
                    print(f"   [OK] Publicado!\n")
                    _human_delay(3.0, 5.0)
                else:
                    print("   [Erro] Pode não ter sido enviado. Verifique o X.\n")

            context.close()
            browser.close()
    finally:
        if session_path:
            os.unlink(session_path)

    # Remove a fila — independente de sucesso parcial, não reposta na próxima execução
    clear_queue()

    print("=" * 60)
    print(f" Concluído. {posted}/{len(pending)} post(s) publicado(s).")
    print("=" * 60)

    if posted < len(pending):
        sys.exit(1)


if __name__ == "__main__":
    main()
