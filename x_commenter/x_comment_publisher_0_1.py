"""
Comentador de posts — Resenha da Nação (etapa 2: PUBLICAÇÃO).

Lê pending_comments.json (a fila aprovada) e, para cada handle, posta o
comentário salvo (comments/<handle>/comment.txt) como REPLY no post original
(comments/<handle>/post_data.json -> post_url). Registra em
published_comments.json e não reposta o que já foi.

Revisão: o conteúdo já foi conferido na etapa de geração. Para postar só um
subconjunto, edite pending_comments.json (remova os handles indesejados) antes
de rodar.

Uso:
    python x_comment_publisher_0_1.py
"""

import os
import json
import time
import base64
import random
import tempfile
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

COMMENTS_DIR     = os.path.join(SCRIPT_DIR, "comments")
PENDING_FILE     = os.path.join(SCRIPT_DIR, "pending_comments.json")
PUBLISHED_FILE   = os.path.join(SCRIPT_DIR, "published_comments.json")
SESSION_FILE     = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "x_creator", "x_session.json"))

_STEALTH = Stealth(navigator_user_agent=False)


# ── Fila / log ──────────────────────────────────────────────────────────────────

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_post_data(handle):
    path = os.path.join(COMMENTS_DIR, handle, "post_data.json")
    return load_json(path, None)


# ── Browser ──────────────────────────────────────────────────────────────────────

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


# ── Postar o reply ────────────────────────────────────────────────────────────────

def post_reply(page, post_url, comment_text):
    """Abre o post e responde com o comentário. Retorna True em sucesso."""
    page.goto(post_url, wait_until="domcontentloaded", timeout=25_000)
    _human_delay(2.0, 3.0)

    try:
        box = page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=12_000)
    except PWTimeout:
        # Em alguns layouts é preciso clicar no "inline reply" primeiro
        try:
            page.click('[data-testid="reply"]', timeout=6_000)
            box = page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=10_000)
        except PWTimeout:
            page.screenshot(path=os.path.join(SCRIPT_DIR, "debug_no_reply_box.png"))
            return False

    box.click()
    _human_delay(0.5, 1.0)
    page.keyboard.type(comment_text, delay=random.randint(25, 55))
    _human_delay(0.8, 1.5)

    clicked = False
    for sel in ('[data-testid="tweetButtonInline"]', '[data-testid="tweetButton"]'):
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
        return False

    _human_delay(3.5, 4.5)
    el = page.query_selector('[data-testid="tweetTextarea_0"]')
    if el is None or not el.is_visible():
        return True
    if not (el.text_content() or "").strip():
        return True

    page.screenshot(path=os.path.join(SCRIPT_DIR, "debug_reply_failed.png"))
    return False


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Comentador (publicação)")
    print("=" * 70)

    pending = load_json(PENDING_FILE, [])
    if not pending:
        print(" Fila pending_comments.json vazia. Nada a postar.")
        return
    session_path = resolve_session()
    if not session_path:
        print(f" [Erro] Sessão não encontrada (nem X_SESSION_JSON nem {SESSION_FILE}).")
        return

    published = load_json(PUBLISHED_FILE, {})
    done_urls = {v["post_url"] for v in published.values()
                 if isinstance(v, dict) and v.get("post_url")} | \
                {k for k in published if "/status/" in k}
    print(f" {len(pending)} comentário(s) na fila: {pending}\n")

    posted = 0
    still_pending = []

    with sync_playwright() as pw:
        browser = make_browser(pw)
        context = make_context(browser, session_path)
        page = new_page(context)

        if not is_logged_in(page):
            print(" [Erro] Sessão inválida/expirada. Renove com x_creator/x_publish_0_1.py.")
            context.close(); browser.close()
            return

        for handle in pending:
            data = load_post_data(handle)
            if not data or not data.get("post_url") or not data.get("comment"):
                print(f"-> @{handle}: dados ausentes em comments/{handle}/. Pulando.")
                still_pending.append(handle)
                continue

            if data["post_url"] in done_urls:
                print(f"-> @{handle}: esse post já foi comentado antes. Pulando.")
                continue

            print(f"-> @{handle}")
            print(f"   Post : {data['post_url']}")
            print(f"   Reply: {data['comment']}")
            try:
                ok = post_reply(page, data["post_url"], data["comment"])
            except Exception as err:
                print(f"   [Erro] {err}\n")
                still_pending.append(handle)
                continue

            if ok:
                published[data["post_url"]] = {
                    "handle": handle,
                    "post_url": data["post_url"],
                    "comment": data["comment"],
                    "published_at": datetime.now().isoformat(),
                }
                save_json(PUBLISHED_FILE, published)
                done_urls.add(data["post_url"])
                posted += 1
                print("   [OK] Comentado!\n")
                _human_delay(8.0, 16.0)   # ritmo conservador entre comentários
            else:
                print("   [Erro] Pode não ter postado. Verifique o X.\n")
                still_pending.append(handle)

        context.close(); browser.close()

    save_json(PENDING_FILE, still_pending)
    print("-" * 70)
    print(f" Concluído. {posted}/{len(pending)} comentário(s) postado(s).")
    if still_pending:
        print(f" Restaram na fila (revisar): {still_pending}")
    print("=" * 70)


if __name__ == "__main__":
    main()
