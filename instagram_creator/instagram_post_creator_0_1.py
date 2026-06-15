import os
import json
import shutil
import subprocess
from datetime import datetime
from google import genai
from groq import Groq
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file(env_path):
    """Lê um arquivo .env simples (KEY=VALUE) e popula os.environ, sem dependências externas."""
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_from_git(git_path):
    """Lê um arquivo do Git via 'git show origin/main:<path>' e retorna como string."""
    try:
        result = subprocess.run(
            ["git", "show", f"origin/main:{git_path}"],
            cwd=os.path.dirname(SCRIPT_DIR),
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise IOError(f"Erro ao ler {git_path} do Git: {e.stderr}")


def read_json_from_git(git_path):
    """Lê um JSON do Git e retorna como dict."""
    content = read_from_git(git_path)
    return json.loads(content)


# --- Visual identity ---
_FONT_PATH = "/System/Library/Fonts/HelveticaNeue.ttc"
_LOGO_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "../site_builder/static/logo.png"))
_RED       = (227, 6, 19)
_BLACK     = (10, 10, 10)
_WHITE     = (255, 255, 255)
_GRAY      = (150, 150, 150)
_EDITORIA_LABELS = {
    "mercado":   "Mercado da Bola",
    "base":      "Crias do Ninho",
    "selecao":   "Seleção",
    "bastidores":"Bastidores",
    "geral":     "Geral",
}

APPROVED_JSON = os.path.abspath(os.path.join(SCRIPT_DIR, "../site_builder/approved.json"))
ARTICLES_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../article_writer/generated_articles"))
COVERS_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../site_builder/static/covers"))
PERSONAS_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../article_writer/personas"))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "instagram_posts")
PUBLISHED_FILE = os.path.join(OUTPUT_DIR, "published_posts.json")
GEMINI_MODEL_NAME = "gemini-2.5-flash"
GROQ_MODEL_NAME = "llama-3.3-70b-versatile"

load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

HASHTAGS_BASE = "#Flamengo #Mengao #VaiFlamengo #CRF #NacaoRubroNegra #ResenhadaNacao"

HASHTAGS_BY_EDITORIA = {
    "mercado": "#MercadoDaBola #Transferencias",
    "base": "#CriasDoNinho #GeracaoFlamengo",
    "selecao": "#Selecao #CopaDoMundo",
    "bastidores": "#Bastidores",
    "geral": "",
}

PERSONA_FILENAME_MAP = {
    "Rodrigo Marques": "rodrigo_marques.md",
    "Thiago Vasconcelos": "thiago_vasconcelos.md",
    "Fernanda Aguiar": "fernanda_aguiar.md",
}


def load_published():
    if not os.path.exists(PUBLISHED_FILE):
        return []
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_published(published):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(published, f, ensure_ascii=False, indent=4)


def load_persona(author):
    filename = PERSONA_FILENAME_MAP.get(author)
    if not filename:
        return ""
    path = os.path.join(PERSONAS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except IOError:
        return ""


def find_cover(slug):
    """Retorna (caminho_absoluto, extensão) da capa do artigo, ou (None, None) se não encontrada."""
    for ext in ("webp", "jpg", "jpeg", "png", "gif"):
        path = os.path.join(COVERS_DIR, f"{slug}.{ext}")
        if os.path.exists(path):
            return path, ext
    return None, None


def _wrap(text, font, max_w, draw):
    words = text.split()
    lines, cur = [], []
    for word in words:
        test = " ".join(cur + [word])
        if draw.textbbox((0, 0), test, font=font)[2] <= max_w:
            cur.append(word)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _fade_band(w, h, color, alpha_from, alpha_to):
    """Retorna imagem RGBA com gradiente vertical de alpha_from a alpha_to."""
    r, g, b = color
    data = bytearray()
    for y in range(h):
        a = int(alpha_from + (alpha_to - alpha_from) * y / max(h - 1, 1))
        data += bytes([r, g, b, a] * w)
    return Image.frombytes("RGBA", (w, h), bytes(data))


def fit_to_3x4(src_path, dest_path, title="", author="", editoria="geral"):
    """Salva em 3:4 com identidade visual nas barras superior e inferior."""
    with Image.open(src_path) as src:
        src = src.convert("RGB")
        ow, oh = src.size

    target_w = max(ow, 1080)
    target_h = target_w * 4 // 3

    scale = min(target_w / ow, target_h / oh)
    photo_w = int(ow * scale)
    photo_h = int(oh * scale)
    offset_x = (target_w - photo_w) // 2
    offset_y = (target_h - photo_h) // 2

    canvas = Image.new("RGB", (target_w, target_h), _BLACK)
    with Image.open(src_path) as src:
        photo = src.convert("RGB").resize((photo_w, photo_h), Image.LANCZOS)
    canvas.paste(photo, (offset_x, offset_y))

    bar_top = offset_y
    bar_bot = target_h - photo_h - offset_y

    # ── Fade gradiente nas bordas da foto ─────────────────────────────
    fade_h = min(140, photo_h // 3)
    if fade_h > 0:
        top_fade = _fade_band(photo_w, fade_h, _BLACK, 220, 0)
        bot_fade = _fade_band(photo_w, fade_h, _BLACK, 0, 220)
        canvas_rgba = canvas.convert("RGBA")
        canvas_rgba.paste(top_fade, (offset_x, offset_y), top_fade)
        canvas_rgba.paste(bot_fade, (offset_x, offset_y + photo_h - fade_h), bot_fade)
        canvas = canvas_rgba.convert("RGB")

    draw = ImageDraw.Draw(canvas)
    fnt = lambda size, idx: ImageFont.truetype(_FONT_PATH, size, index=idx)

    BAND = 16   # espessura das bandas vermelhas topo/rodapé
    SEP  = 4    # linha vermelha separando barra da foto
    px   = 72   # padding horizontal do texto

    # ── Bandas vermelhas externas ─────────────────────────────────────
    draw.rectangle([0, 0, target_w, BAND], fill=_RED)
    draw.rectangle([0, target_h - BAND, target_w, target_h], fill=_RED)

    # ── Linhas vermelhas separadoras foto/barra ───────────────────────
    draw.rectangle([0, offset_y - SEP, target_w, offset_y], fill=_RED)
    draw.rectangle([0, offset_y + photo_h, target_w, offset_y + photo_h + SEP], fill=_RED)

    # ── BARRA SUPERIOR — logo grande centralizado ─────────────────────
    if bar_top >= 60:
        usable_top = bar_top - BAND - SEP
        logo_h = int(usable_top * 0.78)
        with Image.open(_LOGO_PATH) as logo_img:
            logo_img = logo_img.convert("RGBA")
            logo_w_px = int(logo_img.width * logo_h / logo_img.height)
            logo_img = logo_img.resize((logo_w_px, logo_h), Image.LANCZOS)
        logo_x = (target_w - logo_w_px) // 2
        logo_y = BAND + (usable_top - logo_h) // 2
        canvas.paste(logo_img, (logo_x, logo_y), logo_img)

    # ── BARRA INFERIOR — título + linha decorativa + URL ─────────────
    if bar_bot >= 60:
        usable_bot = bar_bot - BAND - SEP
        f_title_sz = min(52, usable_bot // 5)
        f_title = fnt(f_title_sz, 1)
        f_url   = fnt(min(24, usable_bot // 14), 0)
        line_h  = int(f_title_sz * 1.32)
        max_text_w = target_w - px * 2

        lines   = _wrap(title, f_title, max_text_w, draw)[:3]
        url_txt = "resenhadanacao.com.br"
        url_h   = draw.textbbox((0, 0), url_txt, font=f_url)[3]
        DECO_H  = 24   # linha decorativa + margem acima

        total_h = len(lines) * line_h + DECO_H + url_h
        bot_area_start = offset_y + photo_h + SEP
        ty = bot_area_start + (usable_bot - total_h) // 2

        for line in lines:
            lw = draw.textbbox((0, 0), line, font=f_title)[2]
            draw.text(((target_w - lw) // 2, ty), line, font=f_title, fill=_WHITE)
            ty += line_h

        # linha decorativa vermelha abaixo do título
        deco_w = 180
        deco_y = ty + 10
        draw.rectangle(
            [(target_w - deco_w) // 2, deco_y, (target_w + deco_w) // 2, deco_y + 3],
            fill=_RED,
        )

        # URL
        uw = draw.textbbox((0, 0), url_txt, font=f_url)[2]
        uy = target_h - BAND - url_h - 14
        draw.text(((target_w - uw) // 2, uy), url_txt, font=f_url, fill=_GRAY)

    canvas.save(dest_path, "PNG", optimize=True)


def build_hashtags(editoria):
    extra = HASHTAGS_BY_EDITORIA.get(editoria, "")
    return f"{HASHTAGS_BASE} {extra}".strip() if extra else HASHTAGS_BASE


def _build_prompt(article_data, persona_content, editoria):
    title = article_data.get("title", "")
    subtitle = article_data.get("subtitle", "")
    body = article_data.get("body", "")
    hashtags = build_hashtags(editoria)
    return f"""Você é o(a) jornalista abaixo. Leia o perfil com atenção e incorpore completamente o estilo descrito.

--- PERFIL DO JORNALISTA ---
{persona_content}
--- FIM DO PERFIL ---

Agora, adapte o artigo abaixo para uma LEGENDA DO INSTAGRAM do portal Resenha da Nação.

REGRAS DA LEGENDA:
- Primeira linha: 1 frase de gancho poderosa que prende a leitura (no máximo 1 emoji)
- Linha em branco
- 2 a 3 parágrafos curtos resumindo a história na voz do jornalista (máximo 3 linhas cada)
- Linha em branco
- Última linha de chamada para ação: "👉 Link na bio pra ler o artigo completo"
- Linha em branco
- Bloco de hashtags (já fornecido abaixo — inclua exatamente esse bloco, sem alterar)
- LIMITE TOTAL: 2000 caracteres
- Mantenha o estilo e voz do jornalista (dramático, irreverente ou direto conforme o perfil)
- Use linguagem brasileira informal, sotaque carioca
- Responda SOMENTE com JSON válido, sem markdown, sem texto fora do JSON

--- ARTIGO ---
Título: {title}
Linha de apoio: {subtitle}

Corpo:
{body[:2000]}
--- FIM DO ARTIGO ---

Bloco de hashtags a incluir ao final:
{hashtags}

Responda com este JSON exato:
{{
    "caption": "<legenda completa pronta para o Instagram, com quebras de linha \\n>"
}}
"""


def _generate_via_gemini(prompt):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY não definida")
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(response.text).get("caption", "")


def _generate_via_groq(prompt):
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY não definida")
    client = Groq(api_key=GROQ_API_KEY)
    response = client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content).get("caption", "")


def generate_caption(article_data, persona_content, editoria):
    prompt = _build_prompt(article_data, persona_content, editoria)

    try:
        caption = _generate_via_gemini(prompt)
        print(f"  (via Gemini)")
        return caption
    except Exception as err:
        err_str = str(err)
        if "429" in err_str or "quota" in err_str.lower():
            print(f"  [Gemini 429] Quota esgotada — tentando Groq...")
        else:
            print(f"  [Gemini falhou] {err_str[:120]} — tentando Groq...")

    try:
        caption = _generate_via_groq(prompt)
        print(f"  (via Groq fallback)")
        return caption
    except Exception as err:
        print(f"  [Falha na API] Groq também falhou: {err}")
        return ""


def folder_name(entry):
    """Retorna o nome da pasta: YYYY-MM-DD_<slug>."""
    approved_at = entry.get("approved_at", "")
    date_str = approved_at[:10] if approved_at else datetime.now().strftime("%Y-%m-%d")
    return f"{date_str}_{entry.get('slug', '')}"


def process_article(entry, published):
    slug = entry.get("slug", "")
    editoria = entry.get("editoria", "geral")
    article_file = entry.get("file", "")

    if slug in published:
        print(f"  [Ignorado] Já processado anteriormente.")
        return False

    try:
        git_article_path = f"article_writer/generated_articles/{article_file}"
        article_json = read_json_from_git(git_article_path)
    except IOError as e:
        print(f"  [Erro] {e}")
        return False

    author = article_json.get("author", "")
    article_data = article_json.get("article", {})

    cover_src, cover_ext = find_cover(slug)
    if not cover_src:
        print(f"  [Aviso] Capa não encontrada — post criado sem imagem.")

    persona_content = load_persona(author)
    if not persona_content:
        print(f"  [Aviso] Persona de '{author}' não encontrada — seguindo sem perfil de jornalista.")

    print(f"  Jornalista: {author}")
    print(f"  Gerando legenda...")
    caption = generate_caption(article_data, persona_content, editoria)

    if not caption:
        print(f"  [Erro] Falha ao gerar legenda.")
        return False

    dirname = folder_name(entry)
    post_dir = os.path.join(OUTPUT_DIR, dirname)
    os.makedirs(post_dir, exist_ok=True)

    with open(os.path.join(post_dir, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(caption)

    if cover_src:
        dest_path = os.path.join(post_dir, "image.png")
        fit_to_3x4(
            cover_src, dest_path,
            title=article_data.get("title", ""),
            author=author,
            editoria=editoria,
        )

    post_data = {
        "created_at": datetime.now().isoformat(),
        "slug": slug,
        "editoria": editoria,
        "author": author,
        "article_title": article_data.get("title", ""),
        "has_cover": cover_src is not None,
        "caption_chars": len(caption),
    }
    with open(os.path.join(post_dir, "post_data.json"), "w", encoding="utf-8") as f:
        json.dump(post_data, f, ensure_ascii=False, indent=4)

    print(f"  [OK] Salvo em: instagram_posts/{dirname}/  ({len(caption)} caracteres)")
    return True


def main():
    print("--- Criador de Posts para Instagram ---\n")

    try:
        approved = read_json_from_git("site_builder/approved.json")
    except IOError as e:
        print(f"[Erro] {e}")
        return

    published = load_published()
    new_posts = 0

    for entry in approved:
        slug = entry.get("slug", "")
        print(f"-> {slug[:70]}")
        created = process_article(entry, published)
        if created:
            published.append(slug)
            save_published(published)
            new_posts += 1

    print(f"\n--- Concluído: {new_posts} post(s) novo(s) criado(s) ---")


if __name__ == "__main__":
    main()
