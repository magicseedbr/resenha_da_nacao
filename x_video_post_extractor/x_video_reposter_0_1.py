"""
Repostador de vídeos no X — Resenha da Nação.

Consome a saída do x_video_post_extractor_0_1.py (extracted/<handle>/{video.mp4,
post_data.json}) e, para cada vídeo ainda não repostado:
  1. NÃO reposta o mesmo vídeo duas vezes (dedup por URL do post de origem em
     reposted_videos.json).
  2. SEMPRE dá crédito ao perfil de origem (linha "🎥 via @handle" anexada ao
     texto — garantida no código, não depende do modelo).
  3. Cria um texto NOVO pro nosso post a partir do texto do post original + o
     comentário mais relevante (via llm.generate).
  4. Dá 1 minuto de intervalo entre um post e outro (ao publicar).

CURADORIA: antes de gerar legenda/publicar, só passam vídeos com relação ao
FLAMENGO (clube, torcida, jogos, mercado) ou a um jogador/técnico ATUAL do elenco
(lido de article_writer/elenco_flamengo.json) — decidido por 1 chamada de LLM.
Use --no-curate pra pular esse filtro.

Por padrão (publicação supervisionada, como o resto do pipeline) ele só GERA e
EXIBE as legendas pra revisão — salvas em extracted/<handle>/repost_caption.txt.
Publica de fato só com --publish.

Sessão: x_creator/x_session.json localmente, ou o secret X_SESSION_JSON no CI.

Uso:
    python x_video_reposter_0_1.py              # gera/exibe as legendas (não posta)
    python x_video_reposter_0_1.py --publish    # posta de fato (1 min entre posts)
"""

import os
import sys
import json
import time
import base64
import random
import argparse
import tempfile
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, ROOT_DIR)
import llm

EXTRACTED_DIR = os.path.join(SCRIPT_DIR, "extracted")
REPOSTED_FILE = os.path.join(SCRIPT_DIR, "reposted_videos.json")
SESSION_FILE  = os.path.join(ROOT_DIR, "x_creator", "x_session.json")
ELENCO_FILE   = os.path.join(ROOT_DIR, "article_writer", "elenco_flamengo.json")

_STEALTH      = Stealth(navigator_user_agent=False)
_MAX_TWEET    = 280
_CAPTION_MAX  = 230          # deixa folga pra linha de crédito dentro dos 280
_MAX_VIDEO_MB = 512          # limite do X; acima disso ele rejeita
_INTERVAL_S   = 60           # 1 min entre posts (requisito 4)


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


# ── Dedup (não repostar o mesmo vídeo) ──────────────────────────────────────────

def load_reposted():
    if not os.path.exists(REPOSTED_FILE):
        return {}
    try:
        with open(REPOSTED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, IOError):
        return {}


def save_reposted(reposted):
    with open(REPOSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(reposted, f, ensure_ascii=False, indent=4)


# ── Descoberta dos vídeos extraídos ─────────────────────────────────────────────

def discover_videos():
    """Lista os vídeos prontos em extracted/<handle>/. Retorna lista de dicts:
    {handle, post_dir, video_path, post_url, post_text, top_comment}."""
    out = []
    if not os.path.isdir(EXTRACTED_DIR):
        return out
    for handle in sorted(os.listdir(EXTRACTED_DIR)):
        post_dir = os.path.join(EXTRACTED_DIR, handle)
        if not os.path.isdir(post_dir):
            continue
        video_path = os.path.join(post_dir, "video.mp4")
        pd_path    = os.path.join(post_dir, "post_data.json")
        if not os.path.exists(video_path) or not os.path.exists(pd_path):
            continue
        try:
            with open(pd_path, "r", encoding="utf-8") as f:
                pd = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        out.append({
            "handle":      pd.get("handle", handle),
            "post_dir":    post_dir,
            "video_path":  video_path,
            "post_url":    pd.get("post_url", ""),
            "post_text":   pd.get("post_text", ""),
            "top_comment": pd.get("top_comment") or {},
        })
    return out


# ── Curadoria: só vídeos com relação ao Flamengo ────────────────────────────────

def _clean_player_name(raw):
    """'Pedro (atacante)' -> 'Pedro'."""
    return raw.split("(")[0].strip()


def load_elenco_names():
    """Nomes do contexto rubro-negro (elenco + técnico + transferências) pra
    ajudar o classificador a saber quem é do Flamengo."""
    names = ["Flamengo"]
    try:
        with open(ELENCO_FILE, "r", encoding="utf-8") as f:
            elenco = json.load(f)
    except (json.JSONDecodeError, IOError):
        return names
    if elenco.get("tecnico"):
        names.append(elenco["tecnico"])
    for j in elenco.get("jogadores_principais", []):
        names.append(_clean_player_name(j))
    for c in elenco.get("crias_da_base", []):
        names.append(_clean_player_name(c))
    for t in elenco.get("transferencias_recentes", []):
        if t.get("nome"):
            names.append(t["nome"])
    # Dedup preservando ordem.
    seen, out = set(), []
    for n in names:
        if n and n not in seen:
            seen.add(n); out.append(n)
    return out


def _build_curation_prompt(targets, elenco_names):
    blocos = []
    for t in targets:
        post = (t["post_text"] or "").strip() or "(sem texto — só vídeo)"
        com  = ""
        if t["top_comment"] and t["top_comment"].get("text"):
            com = f'\nComentário top: {t["top_comment"]["text"].strip()}'
        blocos.append(f'### @{t["handle"]}\n{post}{com}')
    posts_block = "\n\n".join(blocos)
    elenco = ", ".join(elenco_names)

    return f"""Você faz a curadoria de vídeos pra repostar no @ResenhadaNacao, página
de TORCIDA do FLAMENGO. Só queremos repostar vídeo cujo CONTEÚDO tenha relação
com o FLAMENGO (o clube, sua torcida, seus jogos, sua diretoria/mercado) OU com
algum JOGADOR/TÉCNICO ATUAL do Flamengo.

IMPORTANTE: julgue pelo CONTEÚDO do post/vídeo, NÃO pela conta que postou. Uma
conta rubro-negra (ex.: "Coluna do Fla") postando conteúdo GENÉRICO — workshop,
sorteio, propaganda, recado da página, outro time — NÃO conta como relação.

Para CADA post abaixo, decida se o CONTEÚDO tem relação com o Flamengo.
- related=true: o conteúdo fala do Flamengo, de um jogo/treino/bastidor do
  Flamengo, de contratação/negociação envolvendo o Flamengo, de um
  jogador/técnico ATUAL do elenco (mesmo jogando pela seleção), OU de um
  ÍDOLO/LENDA/ex-jogador histórico do Flamengo — ex.: Zico, Júnior, Adriano,
  Petkovic, Bebeto, Gabigol — mesmo que não jogue mais no clube.
- related=false: é sobre outro clube, outro jogador (sem passado relevante no
  Flamengo), tema genérico/promocional, ou qualquer coisa sem o Flamengo no
  conteúdo (ex.: gol de rival, jogador de outra equipe, seleção genérica,
  divulgação da própria página).
  Na dúvida sem ligação clara no conteúdo, marque false.

ELENCO/CONTEXTO ATUAL DO FLAMENGO (jogadores/técnico que contam como relação):
{elenco}

--- POSTS ---
{posts_block}
--- FIM DOS POSTS ---

Responda SOMENTE com JSON válido, sem markdown (um item por post, handle EXATO sem @):
{{"results": [{{"handle": "<handle>", "related": true, "reason": "<motivo curto>"}}]}}
"""


def curate_flamengo(targets, elenco_names):
    """Classifica cada candidato. Retorna {handle: {"related": bool, "reason": str}}.
    Em falha do LLM, retorna {} (o chamador trata como 'não classificado')."""
    if not targets:
        return {}
    try:
        data = json.loads(llm.generate(_build_curation_prompt(targets, elenco_names)))
        items = data.get("results", []) if isinstance(data, dict) else []
    except Exception as err:
        print(f"  [Curadoria falhou] {str(err)[:120]}")
        return {}
    out = {}
    for it in items:
        handle = (it.get("handle") or "").lstrip("@").strip()
        if handle:
            out[handle] = {"related": bool(it.get("related")),
                           "reason": (it.get("reason") or "").strip()}
    return out


# ── Geração do texto novo (requisito 3) ─────────────────────────────────────────

def _build_caption_prompt(post_text, top_comment):
    post = (post_text or "").strip() or "(o post de origem não tem texto)"
    if top_comment and top_comment.get("text"):
        com = (f'@{top_comment.get("author","")}: "{top_comment["text"].strip()}" '
               f'({top_comment.get("likes", 0)} curtidas)')
    else:
        com = "(sem comentário relevante coletado)"

    return f"""Você administra o @ResenhadaNacao, uma página de TORCIDA do Flamengo no X.
Vamos REPOSTAR um vídeo de outro perfil. Escreva um TEXTO NOVO e original pra
acompanhar o vídeo — não copie o post de origem, reinterprete com a nossa voz.

OBJETIVO: fazer o torcedor PARAR o scroll e reagir. Use o conteúdo do vídeo
(descrito pelo post de origem) e aproveite o ângulo do comentário mais curtido
como tempero/opinião, se ele agregar.

COMO ESCREVER:
- 1 a 2 frases, português informal com sotaque carioca leve.
- Tom de torcida: opinião, provocação leve ou curiosidade. Nada de legenda
  genérica que serviria pra qualquer vídeo.
- NÃO copie literalmente o post de origem nem o comentário.
- NÃO escreva o crédito ao perfil (isso é adicionado automaticamente depois).

PROIBIDO:
- NÃO use hashtag.
- NÃO use @menção.
- NÃO invente fato que não esteja no post/comentário.
- No máximo 1 emoji (zero é ótimo).
- LIMITE: {_CAPTION_MAX} caracteres.

--- POST DE ORIGEM ---
{post}
--- COMENTÁRIO MAIS CURTIDO ---
{com}
--- FIM ---

Responda SOMENTE com JSON válido, sem markdown:
{{"caption": "<texto novo, sem crédito, sem @menção, sem hashtag, até {_CAPTION_MAX} chars>"}}
"""


def generate_caption(post_text, top_comment):
    """Gera o texto novo via LLM. Retorna a legenda (sem crédito) ou ''."""
    try:
        data = json.loads(llm.generate(_build_caption_prompt(post_text, top_comment)))
        caption = (data.get("caption", "") if isinstance(data, dict) else "").strip()
    except Exception as err:
        print(f"    [Claude falhou] {str(err)[:120]} — sem legenda.")
        return ""
    caption = " ".join(caption.split())
    return caption[:_CAPTION_MAX].rstrip()


def credit_line(handle):
    """Linha de crédito ao perfil de origem (requisito 2)."""
    return f"🎥 via @{handle}"


def compose(caption, handle):
    """Junta a legenda + crédito, garantindo o limite de 280 chars."""
    credit = credit_line(handle)
    text = f"{caption}\n\n{credit}" if caption else credit
    if len(text) > _MAX_TWEET:
        # Corta a legenda pra caber o crédito (o crédito é inegociável).
        room = _MAX_TWEET - len(credit) - 2
        caption = caption[:max(0, room)].rstrip()
        text = f"{caption}\n\n{credit}"
    return text


# ── Browser / sessão ────────────────────────────────────────────────────────────

def _human_delay(lo=0.4, hi=1.0):
    time.sleep(random.uniform(lo, hi))


def resolve_session():
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
        viewport={"width": 1280, "height": 800},
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


# ── Publicação do vídeo ─────────────────────────────────────────────────────────

def post_video(page, text, video_path):
    """Posta o tweet com o vídeo anexado. Retorna True em sucesso."""
    page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=20_000)
    _human_delay(2.0, 3.0)

    page.click('[data-testid="SideNav_NewTweet_Button"]')
    _human_delay(1.0, 2.0)

    composer = page.wait_for_selector('[data-testid="tweetTextarea_0"]', timeout=10_000)
    composer.click()
    _human_delay(0.5, 1.0)
    page.keyboard.type(text, delay=random.randint(25, 55))
    _human_delay(0.8, 1.5)

    # Anexa o vídeo (mesmo input usado pra imagem).
    file_input = page.locator('input[data-testid="fileInput"]').first
    file_input.set_input_files(video_path)
    try:
        page.wait_for_selector('[data-testid="attachments"]', timeout=120_000)
    except PWTimeout:
        print("    [Aviso] preview do vídeo não apareceu — upload pode ter falhado.")
        return False

    # O vídeo ainda PROCESSA depois do preview aparecer; o botão Postar
    # ([data-testid=tweetButton]) só habilita quando o processamento termina —
    # esse é o sinal de "pronto pra enviar" (progressbars globais do X nunca
    # somem, então não servem). Esperamos o botão habilitar.
    print("    Subindo o vídeo (pode demorar)...")
    try:
        page.wait_for_selector('[data-testid="tweetButton"]:not([disabled])',
                               timeout=300_000)
    except PWTimeout:
        print("    [Aviso] o botão Postar não habilitou (vídeo pode ter sido rejeitado).")
        return False
    _human_delay(1.0, 2.0)

    # Envia por atalho de teclado (Cmd/Ctrl+Enter) — robusto contra overlays que
    # interceptam o clique. O preview do vídeo cobre TODO o compositor (inclusive
    # a textarea), então usamos .focus() (foco via DOM, sem clique/hit-test) em
    # vez de .click(), que seria interceptado.
    composer = page.query_selector('[data-testid="tweetTextarea_0"]')
    if composer:
        try:
            composer.focus()
        except Exception:
            pass
    page.keyboard.press("ControlOrMeta+Enter")
    _human_delay(3.5, 4.5)

    el = page.query_selector('[data-testid="tweetTextarea_0"]')
    if el is None or not el.is_visible() or not (el.text_content() or "").strip():
        return True

    # Fallback: clique no botão com force=True (ignora o overlay) se o atalho
    # não tiver enviado. Seguro: o upload já terminou (botão habilitado).
    btn = page.query_selector('[data-testid="tweetButton"]:not([disabled])')
    if btn:
        try:
            btn.click(force=True, timeout=10_000)
            _human_delay(3.5, 4.5)
            el = page.query_selector('[data-testid="tweetTextarea_0"]')
            if el is None or not el.is_visible() or not (el.text_content() or "").strip():
                return True
        except PWTimeout:
            pass

    page.screenshot(path=os.path.join(SCRIPT_DIR, "repost_debug_failed.png"))
    print("    [Debug] Screenshot: repost_debug_failed.png")
    return False


# ── Main ────────────────────────────────────────────────────────────────────────

def _short(text, n=90):
    flat = " ".join((text or "").split())
    return flat[:n] + ("..." if len(flat) > n else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true",
                    help="posta de fato no X (sem isso, só gera/exibe pra revisão)")
    ap.add_argument("--limit", type=int, default=0,
                    help="reposta no máximo N vídeos nesta execução (0 = sem limite)")
    ap.add_argument("--no-curate", action="store_true",
                    help="pula a curadoria (por padrão só passa vídeo ligado ao Flamengo)")
    args = ap.parse_args()

    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Repostador de vídeos" +
          ("  [PUBLICAR]" if args.publish else "  [revisão]"))
    print("=" * 70)

    reposted = load_reposted()
    videos   = discover_videos()
    if not videos:
        print(f" Nenhum vídeo em {EXTRACTED_DIR}. Rode o extrator antes.")
        return

    # Passo 1 — descarta já repostados (requisito 1) e vídeos grandes demais.
    raw = []
    for v in videos:
        print(f"-> @{v['handle']}")
        if v["post_url"] and v["post_url"] in reposted:
            print("    [Ignorado] Esse vídeo já foi repostado antes.")
            continue
        size_mb = os.path.getsize(v["video_path"]) / (1024 * 1024)
        if size_mb > _MAX_VIDEO_MB:
            print(f"    [Pulado] Vídeo de {size_mb:.0f} MB excede o limite do X "
                  f"({_MAX_VIDEO_MB} MB).")
            continue
        v["size_mb"] = size_mb
        raw.append(v)

    if not raw:
        print("-" * 70)
        print(" Nada novo pra repostar.")
        return

    # Passo 2 — CURADORIA: só passa vídeo com relação ao Flamengo (1 chamada LLM).
    if not args.no_curate:
        print("-" * 70)
        print(f" Curando {len(raw)} candidato(s) (relação com o Flamengo)...")
        verdicts = curate_flamengo(
            [{"handle": v["handle"], "post_text": v["post_text"],
              "top_comment": v["top_comment"]} for v in raw],
            load_elenco_names(),
        )
        kept = []
        for v in raw:
            verdict = verdicts.get(v["handle"])
            if verdict is None:
                print(f"    [Pulado] @{v['handle']}: curadoria não classificou.")
                continue
            if not verdict["related"]:
                print(f"    [Fora] @{v['handle']}: {verdict['reason'] or 'sem relação com o Flamengo'}")
                continue
            print(f"    [Mantido] @{v['handle']}: {verdict['reason']}")
            kept.append(v)
        raw = kept

    if not raw:
        print("-" * 70)
        print(" Nenhum vídeo com relação ao Flamengo pra repostar.")
        return

    # Passo 3 — gera a legenda nova (requisito 3) só pros que passaram.
    candidates = []
    for v in raw:
        cap_file = os.path.join(v["post_dir"], "repost_caption.txt")
        if os.path.exists(cap_file):
            with open(cap_file, "r", encoding="utf-8") as f:
                caption = f.read().strip()
        else:
            caption = generate_caption(v["post_text"], v["top_comment"])
            if caption:
                with open(cap_file, "w", encoding="utf-8") as f:
                    f.write(caption)
        if not caption:
            print(f"    [Pulado] @{v['handle']}: sem legenda gerada.")
            continue
        v["text"] = compose(caption, v["handle"])
        candidates.append(v)
        print(f"-> @{v['handle']}")
        print(f"    Origem : {_short(v['post_text']) or '(sem texto)'}")
        print(f"    Texto  : {v['text']}  ({len(v['text'])}/280)")
        print(f"    Vídeo  : {v['size_mb']:.1f} MB")

    if not candidates:
        print("-" * 70)
        print(" Nada novo pra repostar.")
        return

    if args.limit and len(candidates) > args.limit:
        print("-" * 70)
        print(f" Limitando a {args.limit} de {len(candidates)} candidato(s) nesta execução.")
        candidates = candidates[:args.limit]

    if not args.publish:
        print("-" * 70)
        print(f" {len(candidates)} vídeo(s) prontos. Revise os repost_caption.txt e")
        print(" rode com --publish pra postar (1 min entre cada).")
        return

    # Publicação (requisito 4: 1 min entre posts).
    session_path = resolve_session()
    if not session_path:
        print(f" [Erro] Sessão não encontrada (nem X_SESSION_JSON nem {SESSION_FILE}).")
        return

    posted = 0
    with sync_playwright() as pw:
        browser = make_browser(pw)
        context = make_context(browser, session_path)
        page    = new_page(context)

        if not is_logged_in(page):
            print(" [Erro] Sessão inválida/expirada. Renove com x_creator/x_publish_0_1.py.")
            context.close(); browser.close()
            return

        for i, v in enumerate(candidates):
            print("-" * 70)
            print(f" [{i+1}/{len(candidates)}] repostando vídeo de @{v['handle']}")
            try:
                ok = post_video(page, v["text"], v["video_path"])
            except Exception as err:
                print(f"    [Erro] {err}")
                ok = False

            if ok:
                reposted[v["post_url"]] = {
                    "handle":       v["handle"],
                    "our_text":     v["text"],
                    "reposted_at":  datetime.now().isoformat(),
                }
                save_reposted(reposted)
                posted += 1
                print("    [OK] Repostado!")
            else:
                print("    [Erro] Pode não ter sido enviado. Verifique o X.")

            # 1 minuto entre posts (não espera depois do último).
            if i < len(candidates) - 1:
                print(f"    Aguardando {_INTERVAL_S}s até o próximo...")
                time.sleep(_INTERVAL_S)

        context.close(); browser.close()

    print("=" * 70)
    print(f" Concluído. {posted}/{len(candidates)} vídeo(s) repostado(s).")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n Interrompido.")
