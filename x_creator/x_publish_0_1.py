"""
Publicador automático de posts no X (Twitter) — Resenha da Nação.

Fluxo:
  1. Na primeira vez (ou se a sessão expirou), abre o Chrome VISÍVEL para
     você fazer login manualmente (suporta 2FA). Salva a sessão em
     x_session.json para os próximos usos.
  2. Nas execuções seguintes, carrega a sessão salva — sem abrir janela.
  3. Para cada pasta em x_posts/ com tweet.txt ainda não publicado, posta o
     texto e, se existir image.*, anexa a imagem.
  4. Registra os publicados em x_posts/published_x_posted.json.

Credenciais necessárias no .env:
    X_USERNAME   — @ do perfil (sem o @)
    X_PASSWORD   — senha da conta

Uso:
    python x_publish_0_1.py
"""

import os
import json
import time
import random
import tempfile
from datetime import datetime
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
POSTS_DIR      = os.path.join(SCRIPT_DIR, "x_posts")
SESSION_FILE   = os.path.join(SCRIPT_DIR, "x_session.json")
PUBLISHED_FILE = os.path.join(POSTS_DIR, "published_x_posted.json")

_STEALTH = Stealth(navigator_user_agent=False)  # user-agent gerenciado pelo channel=chrome


# ── Env ───────────────────────────────────────────────────────────────────────

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
X_USERNAME = os.environ.get("X_USERNAME", "")
X_PASSWORD = os.environ.get("X_PASSWORD", "")


# ── Publicados ────────────────────────────────────────────────────────────────

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


# ── Posts pendentes ───────────────────────────────────────────────────────────

def get_pending(published):
    pending = []
    for dirname in sorted(os.listdir(POSTS_DIR)):
        post_dir = os.path.join(POSTS_DIR, dirname)
        if not os.path.isdir(post_dir):
            continue
        if not os.path.exists(os.path.join(post_dir, "tweet.txt")):
            continue
        if dirname in published:
            continue
        pending.append(dirname)
    return pending


def find_image(post_dir):
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        p = os.path.join(post_dir, f"image.{ext}")
        if os.path.exists(p):
            return p
    return None


def to_jpeg(image_path):
    """Converte qualquer imagem para JPEG em arquivo temporário. Retorna o path JPEG."""
    if image_path.lower().endswith((".jpg", ".jpeg")):
        return image_path, False   # (path, precisa_deletar)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    with Image.open(image_path) as img:
        img.convert("RGB").save(tmp.name, "JPEG", quality=92)
    return tmp.name, True


# ── Browser ───────────────────────────────────────────────────────────────────

def make_browser(pw, headless):
    """Lança o Chrome real (channel='chrome') com stealth ativo."""
    return pw.chromium.launch(
        channel="chrome",
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )


def make_context(browser, storage_state=None):
    kwargs = dict(
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        viewport={"width": 1280, "height": 800},
    )
    if storage_state:
        kwargs["storage_state"] = storage_state
    return browser.new_context(**kwargs)


def new_page(context):
    page = context.new_page()
    _STEALTH.use_sync(page)
    return page


def _human_delay(lo=0.4, hi=1.0):
    time.sleep(random.uniform(lo, hi))


# ── Login ─────────────────────────────────────────────────────────────────────

def is_logged_in(page):
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=25_000)
        page.wait_for_selector('[data-testid="SideNav_NewTweet_Button"]', timeout=10_000)
        return True
    except PWTimeout:
        return False


def _fill_login_form(page):
    """Tenta preencher user + password automaticamente. Não lança exceção em falha."""
    if not X_USERNAME or not X_PASSWORD:
        return

    try:
        # Campo de usuário/email
        page.wait_for_selector('input[autocomplete="username"]', timeout=8_000)
        _human_delay()
        page.fill('input[autocomplete="username"]', X_USERNAME)
        _human_delay(0.3, 0.7)
        page.keyboard.press("Enter")
        _human_delay(1.0, 2.0)

        # Tela extra de verificação de identidade (pede username de novo)
        try:
            extra = page.wait_for_selector(
                'input[data-testid="ocfEnterTextTextInput"]', timeout=4_000
            )
            extra.fill(X_USERNAME)
            _human_delay(0.3, 0.7)
            page.keyboard.press("Enter")
            _human_delay(1.0, 2.0)
        except PWTimeout:
            pass

        # Senha
        page.wait_for_selector('input[name="password"]', timeout=8_000)
        _human_delay()
        page.fill('input[name="password"]', X_PASSWORD)
        _human_delay(0.3, 0.7)
        page.keyboard.press("Enter")
        print("   Credenciais preenchidas. Aguarde 2FA ou confirmação manual...")
    except PWTimeout:
        print("   Não consegui preencher automaticamente — faça login à mão na janela.")


def manual_login(page):
    print("\n [Login] Abrindo o X para login...")
    # Entra em x.com e clica em Sign In (mais natural que ir direto ao /flow/login)
    page.goto("https://x.com", wait_until="domcontentloaded", timeout=20_000)
    _human_delay(1.5, 2.5)

    try:
        signin = page.wait_for_selector('a[href="/login"]', timeout=6_000)
        signin.click()
    except PWTimeout:
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")

    _human_delay(1.5, 2.5)
    _fill_login_form(page)

    input("\n   >>> Quando estiver na home do X, pressione ENTER: ")


# ── Publicação ────────────────────────────────────────────────────────────────

def _add_reply_to_thread(page, reply_text):
    """Adiciona um segundo tweet (o reply com o link) à mesma thread no compositor."""
    try:
        add_btn = page.wait_for_selector('[data-testid="addButton"]', timeout=8_000)
    except PWTimeout:
        print("  [Aviso] Botão de thread não encontrado — postando só o hook, sem reply.")
        return False
    add_btn.click()
    _human_delay(0.6, 1.2)
    try:
        second = page.wait_for_selector('[data-testid="tweetTextarea_1"]', timeout=8_000)
    except PWTimeout:
        print("  [Aviso] Segundo campo da thread não apareceu — postando só o hook.")
        return False
    second.click()
    _human_delay(0.4, 0.8)
    page.keyboard.type(reply_text, delay=random.randint(25, 55))
    _human_delay(0.6, 1.2)
    return True


def post_tweet(page, tweet_text, image_path=None, reply_text=None):
    """Posta o tweet (+ reply com link, se houver). Retorna True em sucesso."""
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
            # Usa o locator do Playwright (mais robusto que query_selector para inputs escondidos)
            file_input = page.locator('input[data-testid="fileInput"]').first
            file_input.set_input_files(jpeg_path)
            try:
                page.wait_for_selector('[data-testid="attachments"]', timeout=25_000)
                _human_delay(1.5, 2.5)
                print(f"  Imagem anexada: {os.path.basename(image_path)}")
            except PWTimeout:
                print("  [Aviso] Thumbnail não apareceu — postando sem imagem.")
        finally:
            if tmp_created:
                os.unlink(jpeg_path)

    if reply_text:
        if _add_reply_to_thread(page, reply_text):
            print("  Reply com link adicionado à thread.")

    _human_delay(0.5, 1.0)

    # Clica no botão Postar (tenta os dois seletores conhecidos)
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
        page.screenshot(path=os.path.join(SCRIPT_DIR, "debug_no_button.png"))
        print("  [Debug] Screenshot salvo: debug_no_button.png")
        return False

    # Aguarda 4s e verifica se a textarea ficou vazia/sumiu (sinal de envio)
    _human_delay(3.5, 4.5)
    el = page.query_selector('[data-testid="tweetTextarea_0"]')
    if el is None or not el.is_visible():
        return True
    if not (el.text_content() or "").strip():
        return True  # textarea esvaziou = tweet enviado

    page.screenshot(path=os.path.join(SCRIPT_DIR, "debug_post_failed.png"))
    print("  [Debug] Screenshot salvo: debug_post_failed.png")
    return False


# ── Main ──────────────────────────────────────────────────────────────────────

def _preview(text, limit=120):
    flat = " ".join(text.split())
    return flat[:limit] + ("..." if len(flat) > limit else "")


def main():
    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Publicação no X")
    print("=" * 70)

    published = load_published()
    pending   = get_pending(published)

    print(f"\n Posts prontos: {len(pending)} aguardando publicação.\n")
    if not pending:
        print(" Nenhum post pendente.")
        return

    with sync_playwright() as pw:
        session_exists = os.path.exists(SESSION_FILE)

        # Primeira tentativa: headless com sessão salva
        headless = session_exists
        browser  = make_browser(pw, headless=headless)
        context  = make_context(browser, storage_state=SESSION_FILE if session_exists else None)
        page     = new_page(context)

        if not is_logged_in(page):
            # Sessão expirou ou não existe — abre janela para login manual
            context.close()
            browser.close()
            browser = make_browser(pw, headless=False)
            context = make_context(browser)
            page    = new_page(context)
            manual_login(page)
            context.storage_state(path=SESSION_FILE)
            print(f" [Sessão salva] → {SESSION_FILE}\n")

        novos = 0
        for i, dirname in enumerate(pending, 1):
            post_dir   = os.path.join(POSTS_DIR, dirname)
            tweet_file = os.path.join(post_dir, "tweet.txt")
            image_path = find_image(post_dir)

            with open(tweet_file, "r", encoding="utf-8") as f:
                tweet_text = f.read().strip()

            reply_text = None
            reply_file = os.path.join(post_dir, "reply.txt")
            if os.path.exists(reply_file):
                with open(reply_file, "r", encoding="utf-8") as f:
                    reply_text = f.read().strip() or None

            post_data = {}
            pd_path = os.path.join(post_dir, "post_data.json")
            if os.path.exists(pd_path):
                with open(pd_path, "r", encoding="utf-8") as f:
                    post_data = json.load(f)

            print("-" * 70)
            print(f" [{i}/{len(pending)}] {post_data.get('article_title', dirname)[:60]}")
            print(f" Pasta     : {dirname}")
            print(f" Imagem    : {os.path.basename(image_path) if image_path else '[nenhuma]'}")
            print(f" Hook      : {_preview(tweet_text)}")
            print(f" Chars     : {len(tweet_text)} (hook, sem link)")
            print(f" Reply     : {_preview(reply_text) if reply_text else '[nenhum]'}")
            print("-" * 70)

            resp = input(" [p] publicar  /  [n] pular  /  [s] sair  > ").strip().lower()

            if resp in ("s", "sair", "q"):
                print(" Encerrando.")
                break
            if resp not in ("p", "publicar"):
                print(" Pulado.\n")
                continue

            print(" Publicando...")
            try:
                ok = post_tweet(page, tweet_text, image_path, reply_text)
            except Exception as err:
                print(f" [Erro] {err}\n")
                continue

            if ok:
                published[dirname] = {"published_at": datetime.now().isoformat()}
                save_published(published)
                context.storage_state(path=SESSION_FILE)
                novos += 1
                print(f" [OK] Publicado!\n")
                _human_delay(2.0, 4.0)   # pausa entre posts
            else:
                print(" [Erro] Tweet pode não ter sido enviado. Verifique o X.\n")

        context.close()
        browser.close()

    print("=" * 70)
    print(f" Concluído. {novos} post(s) publicado(s) nesta sessão.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n Interrompido.")
