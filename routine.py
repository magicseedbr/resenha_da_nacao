#!/usr/bin/env python3
"""
Rotina Resenha da Nação — orquestra o fluxo diário em 4 fases.

  1. criar       — extrai notícias do dia (Coluna do Fla + Globo), coleta tweets,
                   cura a melhor (Gemini) e gera o artigo original.
  2. site        — aprova (auto), busca a capa real, gera o site estático e faz
                   o deploy (commit + push no main -> GitHub Pages).
  3. xpost       — gera o post do X (thread hook + reply com link).
  4. comentarios — gera comentários nos perfis-alvo (profiles.txt).

Filosofia (supervisão): as fases que postam no X (xpost, comentarios) sempre
GERAM o conteúdo e o EXIBEM na tela primeiro. Só publicam de fato com --publish.
Assim quem roda (você ou o Claude) revisa e decide o que vai ao ar — evitando
que conteúdo fraco/errado seja postado sem checagem.

Uso:
  python routine.py                          # tudo, mas sem postar no X (só gera/exibe)
  python routine.py criar site               # só cria o artigo e publica no site
  python routine.py xpost                     # gera o post do X (revisão)
  python routine.py xpost --publish           # gera e publica o post do X
  python routine.py comentarios               # gera comentários (revisão)
  python routine.py comentarios --keep geglobo,Brasileirao --publish
  python routine.py tudo --publish            # fluxo completo, postando no X
  python routine.py site --no-deploy          # gera o site local, sem push

Flags:
  --publish        Publica de fato no X (xpost e/ou comentarios). Sem ela, só gera.
  --keep h1,h2     Em comentarios, limita a fila aos perfis indicados (curadoria).
  --no-deploy      Em site, gera o site local mas não commita/push no main.
"""

import os
import sys
import json
import time
import base64
import argparse
import subprocess
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))

# Python do venv (com as dependências); cai para o interpretador atual se não houver venv.
_VENV_PY = os.path.join(ROOT, "venv", "bin", "python")
PY = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable

# Caminhos
NEWS_EXTRACTOR = os.path.join(ROOT, "news_extractor")
NEWS_CURATOR   = os.path.join(ROOT, "news_curator")
ARTICLE_WRITER = os.path.join(ROOT, "article_writer")
SITE_BUILDER   = os.path.join(ROOT, "site_builder")
X_CREATOR      = os.path.join(ROOT, "x_creator")
X_COMMENTER    = os.path.join(ROOT, "x_commenter")

APPROVED_JSON  = os.path.join(SITE_BUILDER, "approved.json")
X_SESSION_FILE = os.path.join(X_CREATOR, "x_session.json")
TO_POST_FILE   = os.path.join(X_CREATOR, "x_posts", "to_post.json")
PENDING_FILE   = os.path.join(X_COMMENTER, "pending_comments.json")
COMMENTS_DIR   = os.path.join(X_COMMENTER, "comments")

FASES_VALIDAS = ["criar", "site", "xpost", "comentarios"]

# Arquivos versionados que o deploy persiste no main (espelha o pipeline.yml).
DEPLOY_PATHS = [
    "news_extractor/processed_links.json", "news_extractor/processed_tweets.json",
    "news_extractor/raw_news/", "news_extractor/raw_tweets/",
    "news_curator/used_stories.json", "news_curator/selected_story.json",
    "article_writer/elenco_flamengo.json", "article_writer/generated_articles/",
    "site_builder/approved.json", "site_builder/static/covers/",
    "x_creator/x_posts/to_post.json", "x_creator/x_posts/published_x_posted.json",
    "x_commenter/published_comments.json",
]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def site_base_url():
    """SITE_BASE_URL do .env (se houver), senão o domínio de produção padrão."""
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SITE_BASE_URL=") and "=" in line:
                    return line.partition("=")[2].strip().strip('"').strip("'").rstrip("/")
    return "https://www.resenhadanacao.com.br"


def banner(texto):
    print("\n" + "=" * 72)
    print(f" {texto}")
    print("=" * 72)


def run(label, args, cwd=None, env_extra=None):
    """Roda um script do projeto com o python do venv, transmitindo a saída.
    Lança SystemExit se o processo falhar."""
    print(f"\n--- {label} ---")
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run([PY] + args, cwd=cwd, env=env)
    if result.returncode != 0:
        raise SystemExit(f"[Rotina] Falha em: {label} (exit {result.returncode}). Interrompendo.")


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def latest_slug():
    """Slug do último artigo aprovado (para conferir o deploy)."""
    try:
        with open(APPROVED_JSON, encoding="utf-8") as f:
            approved = json.load(f)
        return approved[-1].get("slug") if approved else None
    except (json.JSONDecodeError, IOError, IndexError):
        return None


def url_status(url):
    req = urllib.request.Request(url, method="HEAD")
    try:
        return urllib.request.urlopen(req, timeout=15).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def wait_for_deploy(slug, timeout=300):
    """Espera o artigo ficar acessível no site (deploy do GitHub Pages é assíncrono)."""
    if not slug:
        print("[Rotina] Sem slug para conferir; pulando a espera do deploy.")
        return
    url = f"{site_base_url()}/artigo/{slug}.html"
    print(f"\n--- Aguardando deploy: {url}")
    inicio = time.time()
    while time.time() - inicio < timeout:
        code = url_status(url)
        if code == 200:
            print(f"  [OK] Artigo no ar (HTTP 200) após {int(time.time()-inicio)}s.")
            return
        print(f"  ... ainda não (HTTP {code}). Aguardando 15s.")
        time.sleep(15)
    print(f"  [Aviso] Deploy não confirmado em {timeout}s. O link do post pode dar 404 por ora.")


# ── Fases ────────────────────────────────────────────────────────────────────────

def fase_criar():
    banner("FASE 1/4 — Criação do artigo")
    run("Extrai Coluna do Fla (RSS, só hoje)", ["news_extractor_0_3.py"], cwd=NEWS_EXTRACTOR)
    run("Extrai Globo Esporte (só hoje)", ["news_extractor_globo_0_3.py"], cwd=NEWS_EXTRACTOR)
    run("Coleta tweets", ["news_extractor_x_0_1.py"], cwd=NEWS_EXTRACTOR)
    run("Curadoria (Hype Score + Gemini)", ["news_extractor_curator_0_1.py"], cwd=NEWS_CURATOR)
    run("Atualiza crias da base", ["update_crias.py"], cwd=ARTICLE_WRITER)
    run("Gera artigo original", ["article_writer_0_1.py"], cwd=ARTICLE_WRITER)


def fase_site(deploy=True):
    banner("FASE 2/4 — Publicação no site")
    run("Aprova artigos pendentes", ["auto_approve.py"], cwd=SITE_BUILDER)
    run("Busca a capa real (og:image)", ["image_searcher_og_0_1.py"], cwd=SITE_BUILDER)
    run("Gera o site estático", ["build_site.py"], cwd=SITE_BUILDER)

    if not deploy:
        print("\n[Rotina] --no-deploy: site gerado em site_builder/output/, sem push.")
        return

    print("\n--- Deploy (commit + push no main) ---")
    git("pull", "--rebase", "--autostash", "origin", "main", check=False)
    for path in DEPLOY_PATHS:
        git("add", path, check=False)
    staged = git("diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        print("  Nada novo para commitar. Site provavelmente já está no ar.")
        return
    git("commit", "-m", "Rotina: novo artigo publicado")
    push = git("push", "origin", "HEAD:main", check=False)
    if push.returncode != 0:
        print(f"  [Aviso] push falhou:\n{push.stderr.strip()}")
        print("  Resolva o git e rode 'git push origin HEAD:main' manualmente.")
        return
    print("  [OK] Push no main feito — deploy do GitHub Pages disparado.")
    wait_for_deploy(latest_slug())


def _mostrar_post_x():
    """Lê a fila to_post.json e exibe o hook + reply de cada post gerado."""
    if not os.path.exists(TO_POST_FILE):
        print("  (sem to_post.json — nenhum post novo gerado)")
        return []
    with open(TO_POST_FILE, encoding="utf-8") as f:
        fila = json.load(f)
    for dirname in fila:
        post_dir = os.path.join(X_CREATOR, "x_posts", dirname)
        tweet = os.path.join(post_dir, "tweet.txt")
        reply = os.path.join(post_dir, "reply.txt")
        print(f"\n  • {dirname}")
        if os.path.exists(tweet):
            print(f"    HOOK : {open(tweet, encoding='utf-8').read().strip()}")
        if os.path.exists(reply):
            print(f"    REPLY: {open(reply, encoding='utf-8').read().strip()}")
    return fila


def fase_xpost(publish=False):
    banner("FASE 3/4 — Post do artigo no X")
    run("Gera o post do X (hook + reply)", ["x_creator/x_post_creator_0_1.py"], cwd=ROOT)
    fila = _mostrar_post_x()

    if not fila:
        print("\n[Rotina] Nada na fila do X (artigo já postado ou sem post novo).")
        return
    if not publish:
        print("\n[Rotina] Gerado para revisão. Para publicar: python routine.py xpost --publish")
        return

    if not os.path.exists(X_SESSION_FILE):
        print(f"\n[Rotina] Sessão {X_SESSION_FILE} não encontrada.")
        print("         Rode 'python x_creator/x_publish_0_1.py' uma vez para logar e salvar a sessão.")
        return
    with open(X_SESSION_FILE, "rb") as f:
        session_b64 = base64.b64encode(f.read()).decode("utf-8")
    run("Publica no X (headless)", ["x_creator/x_ci_publisher_0_1.py"],
        cwd=ROOT, env_extra={"X_SESSION_JSON": session_b64})


def _mostrar_comentarios():
    """Exibe os comentários gerados (comments/<handle>/post_data.json)."""
    if not os.path.isdir(COMMENTS_DIR):
        return
    for handle in sorted(os.listdir(COMMENTS_DIR)):
        pd = os.path.join(COMMENTS_DIR, handle, "post_data.json")
        if not os.path.exists(pd):
            continue
        data = json.load(open(pd, encoding="utf-8"))
        print(f"\n  • @{handle}")
        print(f"    POST : {' '.join(data.get('post_text','').split())[:110] or '(sem texto)'}")
        print(f"    REPLY: {data.get('comment','')}")


def fase_comentarios(publish=False, keep=None):
    banner("FASE 4/4 — Ciclo de comentários no X")
    run("Gera comentários nos perfis-alvo", ["x_comment_creator_0_1.py"], cwd=X_COMMENTER)
    _mostrar_comentarios()

    # Curadoria: limita a fila aos perfis indicados em --keep.
    if keep:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=4)
        print(f"\n[Rotina] Fila reduzida a: {keep}")

    if not publish:
        print("\n[Rotina] Gerado para revisão. Para publicar (curadoria recomendada):")
        print("         python routine.py comentarios --keep h1,h2 --publish")
        return

    run("Publica os comentários", ["x_comment_publisher_0_1.py"], cwd=X_COMMENTER)


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Rotina Resenha da Nação (criar -> site -> xpost -> comentarios).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("fases", nargs="*", default=["tudo"],
                        help="fases a rodar: criar site xpost comentarios (ou 'tudo'). Padrão: tudo.")
    parser.add_argument("--publish", action="store_true",
                        help="publica de fato no X (xpost/comentarios). Sem ela, só gera.")
    parser.add_argument("--keep", default="",
                        help="em comentarios, limita a fila a estes perfis (ex.: geglobo,Brasileirao).")
    parser.add_argument("--no-deploy", action="store_true",
                        help="em site, gera local mas não faz push no main.")
    args = parser.parse_args()

    fases = FASES_VALIDAS if (not args.fases or "tudo" in args.fases) else args.fases
    invalidas = [f for f in fases if f not in FASES_VALIDAS]
    if invalidas:
        parser.error(f"fase(s) inválida(s): {invalidas}. Válidas: {FASES_VALIDAS} ou 'tudo'.")

    keep = [h.strip().lstrip("@") for h in args.keep.split(",") if h.strip()] or None

    banner(f"ROTINA RESENHA DA NAÇÃO — fases: {fases} | publish={args.publish}")
    if "criar" in fases:
        fase_criar()
    if "site" in fases:
        fase_site(deploy=not args.no_deploy)
    if "xpost" in fases:
        fase_xpost(publish=args.publish)
    if "comentarios" in fases:
        fase_comentarios(publish=args.publish, keep=keep)

    banner("ROTINA CONCLUÍDA")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Rotina] Interrompida.")
        sys.exit(1)
