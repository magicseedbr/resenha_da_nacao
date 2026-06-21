"""
Extrator de posts com vídeo — Resenha da Nação.

Para cada perfil em x_commenter/profiles.txt:
  1. Abre o perfil no X (sessão logada de x_creator/x_session.json).
  2. Acha o post original MAIS RECENTE que tenha VÍDEO (ignora fixados/reposts).
  3. Baixa o vídeo (captura o HLS .m3u8 do video.twimg.com e remuxa com ffmpeg).
  4. Guarda o texto do post.
  5. Guarda o comentário (resposta) MAIS CURTIDO do post.

Saída (uma pasta por perfil):
  extracted/<handle>/{video.mp4, post_data.json}
  processed_posts.json  — URLs já extraídas (dedup por post).

Pré-requisitos:
  - venv com playwright + playwright-stealth (mesmo do x_commenter).
  - ffmpeg no PATH (brew install ffmpeg).
  - Sessão logada salva em x_creator/x_session.json (rode x_creator/x_publish_0_1.py).

Uso:
    python x_video_post_extractor_0_1.py
    python x_video_post_extractor_0_1.py --max-scrolls 12   # rola mais p/ achar vídeo
"""

import os
import re
import sys
import json
import time
import base64
import random
import argparse
import tempfile
import subprocess
from datetime import datetime
from urllib.parse import urljoin

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

PROFILES_FILE  = os.path.join(ROOT_DIR, "x_commenter", "profiles.txt")
OUTPUT_DIR     = os.path.join(SCRIPT_DIR, "extracted")
PROCESSED_FILE = os.path.join(SCRIPT_DIR, "processed_posts.json")
# Reusa a sessão já logada do processo de posts (uso local).
SESSION_FILE   = os.path.join(ROOT_DIR, "x_creator", "x_session.json")

_STEALTH = Stealth(navigator_user_agent=False)

# Rótulos de "social context" que indicam fixado/repost (pt-BR e en).
_SKIP_CONTEXT = ("fixado", "fixou", "pinned", "repost", "você repostou", "reposted")

# Marcadores de que um article tem vídeo.
_VIDEO_SELECTORS = (
    '[data-testid="videoPlayer"]',
    '[data-testid="videoComponent"]',
    '[data-testid="playButton"]',
)

_HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://x.com/",
}


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


load_env_file(os.path.join(ROOT_DIR, ".env"))


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


# ── Detecção do post com vídeo ──────────────────────────────────────────────────

def _article_has_video(art):
    for sel in _VIDEO_SELECTORS:
        if art.query_selector(sel):
            return True
    return False


def find_latest_video_post(page, handle, max_scrolls=8):
    """Retorna {url, text} do post original mais recente COM vídeo (pula
    fixado/repost). Rola a timeline até max_scrolls vezes procurando."""
    page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=25_000)
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20_000)
    except PWTimeout:
        return None
    _human_delay(1.0, 2.0)

    seen = set()
    for _ in range(max_scrolls):
        articles = page.query_selector_all('article[data-testid="tweet"]')
        for art in articles:
            link = art.query_selector("a:has(time)")
            href = link.get_attribute("href") if link else None
            if not href or "/status/" not in href:
                continue
            url = "https://x.com" + href.split("?")[0]
            if url in seen:
                continue
            seen.add(url)

            ctx = art.query_selector('[data-testid="socialContext"]')
            if ctx:
                label = (ctx.inner_text() or "").lower()
                if any(k in label for k in _SKIP_CONTEXT):
                    continue

            if not _article_has_video(art):
                continue

            text_el = art.query_selector('[data-testid="tweetText"]')
            text = (text_el.inner_text() if text_el else "").strip()
            return {"url": url, "text": text}

        # Não achou ainda: rola pra carregar mais posts.
        page.mouse.wheel(0, 2400)
        _human_delay(1.0, 1.8)

    return None


# ── Download do vídeo ────────────────────────────────────────────────────────────

def _collect_media_urls(page, post_url):
    """Abre o post, dispara a reprodução e captura as URLs de mídia
    (video.twimg.com) que o player solicita. Retorna lista de URLs."""
    media = []

    def on_response(resp):
        u = resp.url
        if "video.twimg.com" in u and (".m3u8" in u or ".mp4" in u):
            media.append(u.split("?tag=")[0] if u.endswith("?tag=") else u)

    page.on("response", on_response)
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=25_000)
        try:
            page.wait_for_selector('article[data-testid="tweet"]', timeout=15_000)
        except PWTimeout:
            pass
        _human_delay(1.5, 2.5)

        # Tenta clicar no play pra forçar o carregamento do stream.
        try:
            btn = page.query_selector('[data-testid="playButton"]')
            if btn:
                btn.click()
        except Exception:
            pass

        # Dá tempo do player puxar o m3u8/segmentos.
        deadline = time.time() + 8
        while time.time() < deadline and not media:
            page.wait_for_timeout(500)
        page.wait_for_timeout(1500)
    finally:
        page.remove_listener("response", on_response)

    # Dedup preservando ordem.
    out, seen = [], set()
    for u in media:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


_MEDIA_ID_RE = re.compile(r"/(amplify_video|ext_tw_video|tweet_video)/(\d+)/")
_RES_RE      = re.compile(r"/(\d+)x(\d+)/")
_AUD_BR_RE   = re.compile(r"/mp4a/(\d+)/")


def _focal_media_id(media):
    """O id do vídeo do post em foco = o do PRIMEIRO media capturado (o player
    do post autoplaya antes dos vídeos recomendados/'Descubra mais')."""
    for u in media:
        m = _MEDIA_ID_RE.search(u)
        if m:
            return m.group(2)
    return None


def _pick_best_m3u8(m3u8_urls):
    """De uma lista de .m3u8, escolhe o master e retorna a URL da variante de
    maior banda. Se já for media-playlist, retorna ela mesma."""
    # Master = o que NÃO aponta direto pra uma faixa (avc1/mp4a no path).
    masters = [u for u in m3u8_urls if "/avc1/" not in u and "/mp4a/" not in u]
    for master_url in (masters or m3u8_urls):
        try:
            r = requests.get(master_url, headers=_HTTP_HEADERS, timeout=20)
            r.raise_for_status()
        except requests.RequestException:
            continue
        body = r.text
        if "#EXT-X-STREAM-INF" not in body:
            return master_url  # já é uma media-playlist (variante única)

        best_bw, best_url = -1, None
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF"):
                m = re.search(r"BANDWIDTH=(\d+)", line)
                bw = int(m.group(1)) if m else 0
                for nxt in lines[i + 1:]:
                    nxt = nxt.strip()
                    if nxt and not nxt.startswith("#"):
                        if bw > best_bw:
                            best_bw, best_url = bw, urljoin(master_url, nxt)
                        break
        if best_url:
            return best_url
    return None


def _pick_best_video(urls):
    """Escolhe a variante de vídeo de maior resolução (pela pasta WxH na URL)."""
    best_area, best_url = -1, None
    for u in urls:
        m = _RES_RE.search(u)
        area = (int(m.group(1)) * int(m.group(2))) if m else 0
        if area > best_area:
            best_area, best_url = area, u
    return best_url


def _pick_best_audio(urls):
    """Escolhe a variante de áudio de maior bitrate (mp4a/<bitrate>)."""
    best_br, best_url = -1, None
    for u in urls:
        m = _AUD_BR_RE.search(u)
        br = int(m.group(1)) if m else 0
        if br > best_br:
            best_br, best_url = br, u
    return best_url


def _ffmpeg(inputs, dest_path):
    """Baixa/remuxa um ou mais inputs (m3u8/mp4) pra um .mp4 via ffmpeg.
    Com 2 inputs (vídeo + áudio), muxa as faixas."""
    headers = f"Referer: https://x.com/\r\nUser-Agent: {_HTTP_HEADERS['User-Agent']}\r\n"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for src in inputs:
        cmd += ["-headers", headers, "-i", src]
    cmd += ["-c", "copy", "-bsf:a", "aac_adtstoasc"]
    if len(inputs) > 1:
        cmd += ["-shortest"]
    cmd += [dest_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(dest_path) \
            or os.path.getsize(dest_path) < 10_000:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr.strip()[:300]}")
    return dest_path


def download_video(page, post_url, dest_path):
    """Pipeline completo de download. Retorna o caminho do arquivo ou None.

    Usa HLS: o X serve vídeo (variantes /avc1/<WxH>/) e áudio (/mp4a/<bitrate>/)
    em playlists separadas — pegamos a melhor de cada e o ffmpeg muxa. Os .mp4
    diretos do amplify_video são só init segments DASH, então são ignorados.
    A página de status também carrega vídeos recomendados, então filtramos a
    mídia pelo id do vídeo do post em foco antes de escolher as faixas."""
    media = _collect_media_urls(page, post_url)
    if not media:
        return None

    focal_id = _focal_media_id(media)
    if focal_id:
        media = [u for u in media if f"/{focal_id}/" in u]

    m3u8s = [u for u in media if ".m3u8" in u]
    vid_variants = [u for u in m3u8s if "/avc1/" in u]
    aud_variants = [u for u in m3u8s if "/mp4a/" in u]

    if vid_variants:
        best_vid = _pick_best_video(vid_variants)
        best_aud = _pick_best_audio(aud_variants)
        inputs = [best_vid] + ([best_aud] if best_aud else [])
        try:
            return _ffmpeg(inputs, dest_path)
        except RuntimeError as err:
            print(f"    [Aviso] mux HLS falhou ({err}); tentando master.")

    # Fallback: master m3u8 (ffmpeg escolhe a variante padrão + áudio do grupo).
    master = _pick_best_m3u8(m3u8s)
    if not master:
        return None
    try:
        return _ffmpeg([master], dest_path)
    except RuntimeError as err:
        print(f"    [Aviso] download HLS falhou: {err}")
        return None


# ── Comentário mais curtido ──────────────────────────────────────────────────────

def _parse_likes(aria_label):
    """Extrai o nº de curtidas do aria-label do grupo de engajamento do tweet.
    Ex.: '2 respostas, 5 reposts, 47 curtidas, ...' -> 47."""
    if not aria_label:
        return 0
    m = re.search(r"([\d.,]+)\s*(curtida|curtidas|like|likes|gostei)", aria_label, re.I)
    if not m:
        return 0
    num = m.group(1).replace(".", "").replace(",", "")
    return int(num) if num.isdigit() else 0


_DISCOVER_MORE = ("descubra mais", "discover more")


def get_top_comment(page, post_url, max_scrolls=18):
    """Na página do post, varre as RESPOSTAS e retorna a mais curtida:
    {text, author, likes, url}. Para no divisor 'Descubra mais' pra não pegar
    tweets recomendados. None se não houver respostas.

    A timeline do X é virtualizada (só as células visíveis ficam no DOM e são
    recicladas ao rolar), por isso rolamos em passos pequenos acumulando por
    URL — assim não pulamos o divisor nem perdemos respostas."""
    # A página do post já foi carregada por _collect_media_urls; garante estado.
    if not page.url.startswith(post_url):
        page.goto(post_url, wait_until="domcontentloaded", timeout=25_000)
        try:
            page.wait_for_selector('article[data-testid="tweet"]', timeout=15_000)
        except PWTimeout:
            return None

    best = None
    seen = set()
    hit_divider = False
    for _ in range(max_scrolls):
        cells = page.query_selector_all('[data-testid="cellInnerDiv"]')
        for cell in cells:
            art = cell.query_selector('article[data-testid="tweet"]')
            if not art:
                # Divisor "Descubra mais" → daqui pra baixo é recomendação.
                txt = (cell.inner_text() or "").strip().lower()
                if txt and any(k in txt for k in _DISCOVER_MORE):
                    hit_divider = True
                    break
                continue

            link = art.query_selector("a:has(time)")
            href = link.get_attribute("href") if link else None
            if not href or "/status/" not in href:
                continue
            url = "https://x.com" + href.split("?")[0]
            if url == post_url or url in seen:  # pula o próprio post
                continue
            seen.add(url)

            group = art.query_selector('[role="group"]')
            likes = _parse_likes(group.get_attribute("aria-label") if group else "")

            text_el = art.query_selector('[data-testid="tweetText"]')
            text = (text_el.inner_text() if text_el else "").strip()
            if not text:
                continue

            author = ""
            user_el = art.query_selector('[data-testid="User-Name"]')
            if user_el:
                m = re.search(r"@(\w+)", user_el.inner_text() or "")
                author = m.group(1) if m else ""

            if best is None or likes > best["likes"]:
                best = {"text": text, "author": author, "likes": likes, "url": url}

        if hit_divider:
            break
        page.mouse.wheel(0, 900)  # passo pequeno: não pular o divisor (virtualização)
        _human_delay(0.6, 1.1)

    return best


# ── Persistência ───────────────────────────────────────────────────────────────

def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, IOError):
        return set()


def save_processed(urls):
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False, indent=4)


def save_result(handle, post, video_path, top_comment):
    handle_dir = os.path.join(OUTPUT_DIR, handle)
    os.makedirs(handle_dir, exist_ok=True)
    data = {
        "handle": handle,
        "post_url": post["url"],
        "post_text": post["text"],
        "video_file": os.path.basename(video_path) if video_path else None,
        "top_comment": top_comment,
        "extracted_at": datetime.now().isoformat(),
    }
    with open(os.path.join(handle_dir, "post_data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# ── Main ────────────────────────────────────────────────────────────────────────

def _short(text, n=90):
    flat = " ".join((text or "").split())
    return flat[:n] + ("..." if len(flat) > n else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-scrolls", type=int, default=8,
                    help="quantas vezes rolar a timeline procurando vídeo")
    args = ap.parse_args()

    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Extrator de posts com vídeo")
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
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    processed = load_processed()

    ok = 0
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
                post = find_latest_video_post(page, handle, args.max_scrolls)
            except Exception as err:
                print(f"    [Erro] Falha ao ler o perfil: {err}")
                continue
            if not post:
                print("    [Aviso] Nenhum post recente com vídeo encontrado. Pulando.")
                continue
            if post["url"] in processed:
                print("    [Ignorado] Post de vídeo mais recente já foi extraído antes.")
                continue

            print(f"    Post: {_short(post['text']) or '(sem texto)'}")
            print(f"    URL:  {post['url']}")

            handle_dir = os.path.join(OUTPUT_DIR, handle)
            os.makedirs(handle_dir, exist_ok=True)
            video_path = os.path.join(handle_dir, "video.mp4")

            # Baixa o vídeo (abre a página do post; reaproveitada p/ os comentários).
            try:
                saved = download_video(page, post["url"], video_path)
            except Exception as err:
                print(f"    [Erro] download do vídeo: {err}")
                saved = None
            if saved:
                size = os.path.getsize(saved) / (1024 * 1024)
                print(f"    Vídeo: {os.path.basename(saved)} ({size:.1f} MB)")
            else:
                print("    [Aviso] Não consegui baixar o vídeo (segue salvando o resto).")

            # Comentário mais curtido.
            try:
                top = get_top_comment(page, post["url"])
            except Exception as err:
                print(f"    [Erro] leitura de comentários: {err}")
                top = None
            if top:
                print(f"    Top comentário (@{top['author']}, {top['likes']} ❤): "
                      f"{_short(top['text'], 70)}")
            else:
                print("    [Aviso] Sem comentário identificado.")

            save_result(handle, post, saved, top)
            processed.add(post["url"])
            save_processed(processed)
            ok += 1
            _human_delay(1.0, 2.0)

        context.close(); browser.close()

    print("-" * 70)
    print(f" Extraídos: {ok} | saída em {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
