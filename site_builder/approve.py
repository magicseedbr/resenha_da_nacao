"""
Gate de aprovação — Resenha da Nação.

Revisa os artigos gerados em article_writer/generated_articles/ que ainda não
foram publicados e permite aprovar um a um. Os aprovados são registrados em
approved.json (slug + editoria + data), que é a única fonte de verdade do que
vai para o site. Os JSONs originais não são alterados nem movidos.

Uso:
    python approve.py
"""

import sys
from datetime import datetime

import shared


def _unique_slug(base_slug, used_slugs):
    """Garante slug único anexando um sufixo numérico se necessário."""
    slug = base_slug
    counter = 2
    while slug in used_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def _preview_body(body, limit=280):
    text = " ".join((body or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def main():
    approved = shared.load_approved()
    approved_files = {entry["file"] for entry in approved}
    used_slugs = {entry["slug"] for entry in approved}

    all_articles = shared.list_generated_articles()
    pending = [f for f in all_articles if f not in approved_files]

    print("=" * 70)
    print(" RESENHA DA NAÇÃO — Aprovação de artigos")
    print("=" * 70)
    print(f" Gerados: {len(all_articles)} | Já publicados: {len(approved_files)} "
          f"| Pendentes: {len(pending)}")
    print()

    if not pending:
        print(" Nenhum artigo pendente. Tudo já está publicado. ✓")
        return

    novos = 0
    for filename in pending:
        try:
            data = shared.load_article(filename)
        except Exception as err:
            print(f" [Aviso] Falha ao ler '{filename}': {err}. Pulando.")
            continue

        article = data.get("article", {})
        author = data.get("author", "Desconhecido")
        title = article.get("title", "(sem título)")
        subtitle = article.get("subtitle", "")
        body = article.get("body", "")

        editoria = shared.derive_editoria(title, body)

        print("-" * 70)
        print(f" Arquivo : {filename}")
        print(f" Autor   : {author}")
        print(f" Editoria: {shared.editoria_label(editoria)}  (sugerida)")
        print(f" Título  : {title}")
        if subtitle:
            print(f" Linha   : {subtitle}")
        print(f" Prévia  : {_preview_body(body)}")
        print("-" * 70)

        resp = input(" [a] aprovar  /  [p] pular  /  [s] sair  > ").strip().lower()

        if resp in ("s", "sair", "q", "quit"):
            print(" Encerrando. Alterações já aprovadas foram salvas.")
            break
        if resp not in ("a", "aprovar", "sim", "y", "yes"):
            print(" Pulado.\n")
            continue

        base_slug = shared.slugify(title)
        slug = _unique_slug(base_slug, used_slugs)
        used_slugs.add(slug)

        entry = {
            "file": filename,
            "slug": slug,
            "editoria": editoria,
            "approved_at": datetime.now().isoformat(),
        }
        approved.append(entry)
        shared.save_approved(approved)
        novos += 1
        print(f" ✓ Aprovado como /artigo/{slug}.html (editoria: {editoria})\n")

    print("=" * 70)
    print(f" Concluído. {novos} novo(s) artigo(s) aprovado(s).")
    print(" Rode 'python build_site.py' para (re)gerar o site.")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n Interrompido.")
        sys.exit(0)
