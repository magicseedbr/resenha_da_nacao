#!/usr/bin/env python3
"""
Rotina de engajamento — Resenha da Nação.

Posta 1 vídeo e faz comentários em outros posts. Duas fases:

  1. video       — extrai os vídeos dos perfis-alvo (x_video_post_extractor),
                   cura (só passa vídeo com relação ao Flamengo) e reposta 1
                   vídeo com crédito ao perfil de origem.
  2. comentarios — gera comentários nos perfis-alvo (x_commenter) e publica.

Filosofia (supervisão): igual à routine.py — as fases que postam no X SEMPRE
geram/exibem o conteúdo primeiro e só publicam de fato com --publish. Assim quem
roda revisa antes de qualquer coisa ir ao ar.

Uso:
  python routine_engajamento.py                       # tudo, sem postar (revisão)
  python routine_engajamento.py --publish             # posta 1 vídeo + comentários
  python routine_engajamento.py video --publish       # só o vídeo
  python routine_engajamento.py comentarios --keep geglobo,Brasileirao --publish
  python routine_engajamento.py video --no-extract    # reposta de vídeos já extraídos
  python routine_engajamento.py video --videos 2 --publish

Flags:
  --publish       Publica de fato no X. Sem ela, só gera/exibe.
  --videos N      Quantos vídeos repostar na fase video (padrão 1).
  --no-extract    Pula a extração (usa o que já está em extracted/).
  --no-curate     Pula a curadoria de relação com o Flamengo.
  --keep h1,h2    Em comentarios, limita a fila aos perfis indicados (curadoria).
"""

import os
import sys
import json
import argparse
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))

# Python do venv (com as dependências); cai para o interpretador atual se não houver venv.
_VENV_PY = os.path.join(ROOT, "venv", "bin", "python")
PY = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable

# Caminhos
X_COMMENTER  = os.path.join(ROOT, "x_commenter")
X_VIDEO      = os.path.join(ROOT, "x_video_post_extractor")
PENDING_FILE = os.path.join(X_COMMENTER, "pending_comments.json")
COMMENTS_DIR = os.path.join(X_COMMENTER, "comments")

FASES_VALIDAS = ["video", "comentarios"]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def banner(texto):
    print("\n" + "=" * 72)
    print(f" {texto}")
    print("=" * 72)


def run(label, args, cwd=None):
    """Roda um script do projeto com o python do venv, transmitindo a saída.
    Lança SystemExit se o processo falhar."""
    print(f"\n--- {label} ---")
    result = subprocess.run([PY] + args, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"[Rotina] Falha em: {label} (exit {result.returncode}). Interrompendo.")


# ── Fase 1: vídeo ────────────────────────────────────────────────────────────────

def fase_video(publish=False, videos=1, extract=True, curate=True):
    banner("FASE 1/2 — Repostar vídeo")

    if extract:
        # Extrai o post mais recente com vídeo de cada perfil-alvo (insumo).
        run("Extrai vídeos dos perfis-alvo", ["x_video_post_extractor_0_1.py"], cwd=X_VIDEO)
    else:
        print("\n[Rotina] --no-extract: usando os vídeos já em extracted/.")

    repost_args = ["x_video_reposter_0_1.py", "--limit", str(videos)]
    if not curate:
        repost_args.append("--no-curate")
    if publish:
        repost_args.append("--publish")

    label = f"Reposta {videos} vídeo(s) curado(s)" + (" + publica" if publish else " (revisão)")
    run(label, repost_args, cwd=X_VIDEO)

    if not publish:
        print("\n[Rotina] Vídeo(s) gerado(s) para revisão. Para postar:")
        print("         python routine_engajamento.py video --publish")


# ── Fase 2: comentários ──────────────────────────────────────────────────────────

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
    banner("FASE 2/2 — Comentários no X")
    run("Gera comentários nos perfis-alvo", ["x_comment_creator_0_1.py"], cwd=X_COMMENTER)
    _mostrar_comentarios()

    # Curadoria: limita a fila aos perfis indicados em --keep.
    if keep:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False, indent=4)
        print(f"\n[Rotina] Fila reduzida a: {keep}")

    if not publish:
        print("\n[Rotina] Gerado para revisão. Para publicar (curadoria recomendada):")
        print("         python routine_engajamento.py comentarios --keep h1,h2 --publish")
        return

    run("Publica os comentários", ["x_comment_publisher_0_1.py"], cwd=X_COMMENTER)


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    # Linha-a-linha: mantém a ordem dos logs do orquestrador e dos subprocessos.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(
        description="Rotina de engajamento Resenha da Nação (video -> comentarios).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("fases", nargs="*", default=["tudo"],
                        help="fases a rodar: video comentarios (ou 'tudo'). Padrão: tudo.")
    parser.add_argument("--publish", action="store_true",
                        help="publica de fato no X. Sem ela, só gera/exibe.")
    parser.add_argument("--videos", type=int, default=1,
                        help="quantos vídeos repostar na fase video (padrão 1).")
    parser.add_argument("--no-extract", action="store_true",
                        help="em video, pula a extração e usa o que já está em extracted/.")
    parser.add_argument("--no-curate", action="store_true",
                        help="em video, pula a curadoria de relação com o Flamengo.")
    parser.add_argument("--keep", default="",
                        help="em comentarios, limita a fila a estes perfis (ex.: geglobo,Brasileirao).")
    args = parser.parse_args()

    fases = FASES_VALIDAS if (not args.fases or "tudo" in args.fases) else args.fases
    invalidas = [f for f in fases if f not in FASES_VALIDAS]
    if invalidas:
        parser.error(f"fase(s) inválida(s): {invalidas}. Válidas: {FASES_VALIDAS} ou 'tudo'.")

    keep = [h.strip().lstrip("@") for h in args.keep.split(",") if h.strip()] or None

    banner(f"ROTINA DE ENGAJAMENTO — fases: {fases} | publish={args.publish}")
    if "video" in fases:
        fase_video(publish=args.publish, videos=args.videos,
                   extract=not args.no_extract, curate=not args.no_curate)
    if "comentarios" in fases:
        fase_comentarios(publish=args.publish, keep=keep)

    banner("ROTINA CONCLUÍDA")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Rotina] Interrompida.")
        sys.exit(1)
