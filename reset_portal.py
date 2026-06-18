#!/usr/bin/env python3
"""Reset do portal Resenha da Nação — clean slate total.

Apaga todo o conteúdo gerado e zera o histórico de deduplicação, deixando o
portal vazio para recomeçar do absoluto zero. NÃO toca em credenciais, sessões
de login nem arquivos de configuração/referência.

Uso:
    python reset_portal.py --dry-run   # apenas lista o que faria
    python reset_portal.py             # executa de fato

O script é idempotente: rodar duas vezes não causa erro.
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- JSONs git-tracked: esvaziar mantendo o arquivo --------------------------
# Listas de dedup/histórico e fonte de verdade do site -> []
EMPTY_LIST_JSON = [
    "news_extractor/processed_links.json",
    "news_extractor/processed_tweets.json",
    "news_curator/used_stories.json",
    "site_builder/approved.json",
    "x_commenter/published_comments.json",
]

# Registros/saídas efêmeras em formato de dict -> {}
EMPTY_DICT_JSON = [
    "x_creator/x_posts/published_x_posted.json",
    "news_curator/selected_story.json",
    "news_extractor/selected_story.json",
]

# --- Diretórios: apagar todo o conteúdo e recriar vazio -----------------------
CLEAR_DIRS = [
    "news_extractor/raw_news",
    "news_extractor/raw_tweets",
    "article_writer/generated_articles",
    "site_builder/static/covers",
    "image_searcher/downloaded_images",
    "instagram_creator/instagram_posts",
    "x_commenter/comments",
    "site_builder/output",
]

# --- x_creator/x_posts: remover só as subpastas por artigo, manter os JSONs ---
CLEAR_SUBDIRS_ONLY = [
    "x_creator/x_posts",
]

# --- Arquivos soltos de fila/estado/legado a remover --------------------------
REMOVE_FILES = [
    "x_creator/x_posts/to_post.json",
    "x_creator/x_posts/published_x.json",
    "x_commenter/pending_comments.json",
    "x_creator/debug_post_failed.png",
]

# --- Preservados (apenas para o log; o script nunca os toca) -------------------
PRESERVED = [
    ".env",
    "x_creator/x_session.json",
    "x_commenter/profiles.txt",
    "article_writer/elenco_flamengo.json",
    "article_writer/personas/",
    "site_builder/templates/",
    "site_builder/static/ (logo, escudo, capa_padrao, css)",
]


def log(action, target):
    print(f"  {action:<14} {target}")


def write_json(rel, value, dry):
    path = ROOT / rel
    if not path.exists():
        log("skip (ausente)", rel)
        return
    log("zerar json", rel)
    if not dry:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def clear_dir(rel, dry):
    path = ROOT / rel
    if not path.exists():
        log("skip (ausente)", rel + "/")
        return
    n = sum(1 for _ in path.iterdir())
    log("limpar dir", f"{rel}/ ({n} itens)")
    if not dry:
        shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)


def clear_subdirs_only(rel, dry):
    path = ROOT / rel
    if not path.exists():
        log("skip (ausente)", rel + "/")
        return
    subdirs = [p for p in path.iterdir() if p.is_dir()]
    log("limpar subdirs", f"{rel}/ ({len(subdirs)} pastas)")
    if not dry:
        for sub in subdirs:
            shutil.rmtree(sub)


def remove_file(rel, dry):
    path = ROOT / rel
    if not path.exists():
        log("skip (ausente)", rel)
        return
    log("remover", rel)
    if not dry:
        path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="apenas lista o que faria, sem alterar nada")
    args = parser.parse_args()
    dry = args.dry_run

    print("=" * 64)
    print(" RESET DO PORTAL — clean slate total" +
          ("  [DRY-RUN]" if dry else ""))
    print("=" * 64)

    print("\n[1] JSONs de lista -> []")
    for rel in EMPTY_LIST_JSON:
        write_json(rel, [], dry)

    print("\n[2] JSONs de dict -> {}")
    for rel in EMPTY_DICT_JSON:
        write_json(rel, {}, dry)

    print("\n[3] Diretórios de conteúdo -> vazios")
    for rel in CLEAR_DIRS:
        clear_dir(rel, dry)

    print("\n[4] x_creator/x_posts -> remover subpastas (manter registros)")
    for rel in CLEAR_SUBDIRS_ONLY:
        clear_subdirs_only(rel, dry)

    print("\n[5] Arquivos soltos de fila/estado/legado")
    for rel in REMOVE_FILES:
        remove_file(rel, dry)

    print("\n[6] PRESERVADOS (nunca tocados):")
    for rel in PRESERVED:
        log("manter", rel)

    print("\n" + "=" * 64)
    if dry:
        print(" DRY-RUN concluído. Nada foi alterado.")
    else:
        print(" Reset concluído. Portal zerado.")
    print("=" * 64)


if __name__ == "__main__":
    main()
