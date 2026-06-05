"""
Gerador do site estático — Resenha da Nação.

Lê approved.json, carrega os artigos aprovados de generated_articles/ e renderiza
o site completo (home, páginas de artigo, editorias e jornalistas) em output/,
com tema rubro-negro. Copia os assets de static/ e abre a home no navegador.

Uso:
    python build_site.py
"""

import os
import shutil
import webbrowser
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape

import shared

MESES = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def format_date(iso_string):
    """ISO -> '04 de junho de 2026' (cai no texto cru se não parsear)."""
    try:
        dt = datetime.fromisoformat(iso_string)
        return f"{dt.day:02d} de {MESES[dt.month]} de {dt.year}"
    except (ValueError, TypeError, IndexError):
        return iso_string or ""


def build_article_view(entry):
    """Monta o dicionário de um artigo pronto para os templates."""
    data = shared.load_article(entry["file"])
    article = data.get("article", {})
    source = data.get("source_ref", {})
    author = data.get("author", "Redação")
    editoria_slug = entry.get("editoria", "geral")
    body = article.get("body", "")

    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]

    return {
        "slug": entry["slug"],
        "title": article.get("title", "(sem título)"),
        "subtitle": article.get("subtitle", ""),
        "paragraphs": paragraphs,
        "author": author,
        "author_slug": shared.slugify(author),
        "author_initials": shared.author_initials(author),
        "editoria_slug": editoria_slug,
        "editoria_label": shared.editoria_label(editoria_slug),
        "cover": entry.get("cover") or "static/capa_padrao.svg",
        "source_name": source.get("source_name", ""),
        "source_url": source.get("source_url", ""),
        "generated_at": data.get("generated_at", ""),
        "date_display": format_date(data.get("generated_at", "")),
    }


def reset_output():
    if os.path.exists(shared.OUTPUT_DIR):
        shutil.rmtree(shared.OUTPUT_DIR)
    os.makedirs(shared.OUTPUT_DIR)
    shutil.copytree(shared.STATIC_DIR, os.path.join(shared.OUTPUT_DIR, "static"))


def write_page(rel_path, html):
    full = os.path.join(shared.OUTPUT_DIR, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    print("--- Gerando o site Resenha da Nação ---")

    approved = shared.load_approved()
    if not approved:
        print(" [Aviso] Nenhum artigo aprovado em approved.json.")
        print(" Rode 'python approve.py' antes de gerar o site.")

    articles = []
    for entry in approved:
        try:
            articles.append(build_article_view(entry))
        except Exception as err:
            print(f" [Aviso] Falha ao montar '{entry.get('file')}': {err}. Pulando.")

    # Mais recente primeiro (manchete da home).
    articles.sort(key=lambda a: a["generated_at"], reverse=True)

    editorias_menu = [
        {"slug": slug, "label": label}
        for slug, label in shared.EDITORIAS.items()
    ]

    env = Environment(
        loader=FileSystemLoader(shared.TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )

    base_ctx = {
        "site_name": "Resenha da Nação",
        "editorias": editorias_menu,
    }

    reset_output()

    # CNAME para domínio customizado no GitHub Pages
    with open(os.path.join(shared.OUTPUT_DIR, "CNAME"), "w", encoding="utf-8") as f:
        f.write("resenhadanacao.com.br")

    # --- Home ---------------------------------------------------------------
    home_tpl = env.get_template("index.html")
    write_page("index.html", home_tpl.render(
        **base_ctx,
        root="",
        active_editoria="home",
        featured=articles[0] if articles else None,
        rest=articles[1:],
    ))

    # --- Páginas de artigo --------------------------------------------------
    article_tpl = env.get_template("article.html")
    for a in articles:
        write_page(f"artigo/{a['slug']}.html", article_tpl.render(
            **base_ctx,
            root="../",
            active_editoria=a["editoria_slug"],
            article=a,
        ))

    # --- Editorias (uma página por editoria definida) -----------------------
    editoria_tpl = env.get_template("editoria.html")
    for slug, label in shared.EDITORIAS.items():
        in_editoria = [a for a in articles if a["editoria_slug"] == slug]
        write_page(f"editoria/{slug}.html", editoria_tpl.render(
            **base_ctx,
            root="../",
            active_editoria=slug,
            editoria_label=label,
            articles=in_editoria,
        ))

    # --- Jornalistas (um por autor com artigo publicado) --------------------
    jornalista_tpl = env.get_template("jornalista.html")
    authors = {}
    for a in articles:
        authors.setdefault(a["author_slug"], a["author"])
    for author_slug, author_name in authors.items():
        in_author = [a for a in articles if a["author_slug"] == author_slug]
        write_page(f"jornalista/{author_slug}.html", jornalista_tpl.render(
            **base_ctx,
            root="../",
            active_editoria=None,
            journalist={
                "name": author_name,
                "slug": author_slug,
                "initials": shared.author_initials(author_name),
                "bio": shared.extract_persona_bio(author_name),
            },
            articles=in_author,
        ))

    index_path = os.path.join(shared.OUTPUT_DIR, "index.html")
    print(f" Artigos publicados : {len(articles)}")
    print(f" Editorias geradas  : {len(shared.EDITORIAS)}")
    print(f" Jornalistas        : {len(authors)}")
    print(f" Site gerado em      : {shared.OUTPUT_DIR}")
    print(f" Abra no navegador   : file://{index_path}")

    try:
        webbrowser.open(f"file://{index_path}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
