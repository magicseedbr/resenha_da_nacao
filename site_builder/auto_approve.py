"""
Aprovação automática de todos os artigos pendentes — Resenha da Nação.

Equivalente não-interativo do approve.py: aprova todos os artigos de
generated_articles/ que ainda não estão em approved.json, sem confirmação
manual. Usado pelo pipeline automatizado no GitHub Actions.

Uso:
    python auto_approve.py
"""

from datetime import datetime
import shared


def _unique_slug(base_slug, used_slugs):
    slug = base_slug
    counter = 2
    while slug in used_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def main():
    approved = shared.load_approved()
    approved_files = {entry["file"] for entry in approved}
    used_slugs = {entry["slug"] for entry in approved}

    pending = [f for f in shared.list_generated_articles() if f not in approved_files]

    if not pending:
        print("Nenhum artigo pendente.")
        return

    for filename in pending:
        try:
            data = shared.load_article(filename)
        except Exception as err:
            print(f"[Aviso] Falha ao ler '{filename}': {err}. Pulando.")
            continue

        article = data.get("article", {})
        title = article.get("title", "(sem título)")
        body = article.get("body", "")
        editoria = shared.derive_editoria(title, body)

        slug = _unique_slug(shared.slugify(title), used_slugs)
        used_slugs.add(slug)

        entry = {
            "file": filename,
            "slug": slug,
            "editoria": editoria,
            "approved_at": datetime.now().isoformat(),
        }
        approved.append(entry)
        shared.save_approved(approved)
        print(f"Aprovado: {slug} (editoria: {editoria})")


if __name__ == "__main__":
    main()
