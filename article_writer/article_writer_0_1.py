import os
import json
import glob
import random
from datetime import datetime
from google import genai

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
INPUT_FILE = os.path.abspath(os.path.join(SCRIPT_DIR, "../news_curator/selected_story.json"))
PERSONAS_DIR = os.path.join(SCRIPT_DIR, "personas")
GLOSSARY_FILE = os.path.join(SCRIPT_DIR, "glossario_carioca.md")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "generated_articles")
GEMINI_MODEL_NAME = "gemini-2.5-flash"

# API Key carregada do arquivo .env na raiz do projeto (um nível acima deste script)
load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)


def load_selected_story():
    if not os.path.exists(INPUT_FILE):
        print(f"[Erro] Arquivo de entrada não encontrado: '{INPUT_FILE}'")
        return None
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as err:
        print(f"[Erro de Leitura] Falha ao ler selected_story.json: {err}")
        return None


def load_personas():
    personas = []
    for path in glob.glob(os.path.join(PERSONAS_DIR, "*.md")):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            name = os.path.splitext(os.path.basename(path))[0].replace("_", " ").title()
            personas.append({"name": name, "content": content})
        except IOError as err:
            print(f"[Aviso] Não foi possível carregar persona '{path}': {err}")
    return personas


def load_glossary():
    """Carrega o glossário carioca como string; vazio se o arquivo não existir."""
    if not os.path.exists(GLOSSARY_FILE):
        print(f"[Aviso] Glossário não encontrado em '{GLOSSARY_FILE}'. Seguindo sem ele.")
        return ""
    try:
        with open(GLOSSARY_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except IOError as err:
        print(f"[Aviso] Falha ao ler o glossário: {err}. Seguindo sem ele.")
        return ""


def load_elenco():
    """Carrega o elenco do Flamengo como dict; vazio se o arquivo não existir."""
    elenco_file = os.path.join(SCRIPT_DIR, "elenco_flamengo.json")
    if not os.path.exists(elenco_file):
        return {}
    try:
        with open(elenco_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as err:
        print(f"[Aviso] Falha ao ler elenco_flamengo.json: {err}. Seguindo sem ele.")
        return {}


def generate_article(story_data, persona, glossary="", elenco=None):
    if not GEMINI_API_KEY:
        raise ValueError("[Falha de Autenticação] Defina GEMINI_API_KEY no arquivo .env da raiz do projeto.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    article_data = story_data.get("article_data", {})
    original_title = article_data.get("title", "")
    full_text = article_data.get("full_text", "")
    source_name = article_data.get("source_name", "")
    source_url = article_data.get("source_url", "")

    glossary_block = ""
    if glossary:
        glossary_block = f"""
--- GLOSSÁRIO CARIOCA (guia de sotaque) ---
{glossary}
--- FIM DO GLOSSÁRIO ---
"""

    elenco_block = ""
    if elenco:
        tecnico = elenco.get("tecnico", "Leonardo Jardim")
        jogadores_str = "\n".join(f"- {j}" for j in elenco.get("jogadores_principais", []))
        crias = elenco.get("crias_da_base", [])
        crias_str = "\n".join(f"- {c}" for c in crias) if crias else "- (nenhuma cria confirmada no elenco atual)"
        elenco_block = f"""
--- ELENCO DO FLAMENGO ATUALIZADO ---
Técnico: {tecnico}
Principais jogadores:
{jogadores_str}

CRIAS DA BASE (jogadores formados nas categorias de base do Flamengo):
{crias_str}
--- FIM DO ELENCO ---
"""

    prompt = f"""Você é o(a) jornalista abaixo. Leia o perfil com atenção e incorpore completamente o estilo descrito.

--- PERFIL DO JORNALISTA ---
{persona['content']}
--- FIM DO PERFIL ---
{glossary_block}{elenco_block}
Agora, com base no material jornalístico abaixo, escreva um artigo original de 400 a 600 palavras para o portal Resenha da Nação.

REGRAS:
- **REGRA DE OURO — FATOS (acima de qualquer outra):** Escreva APENAS com base nos fatos presentes no MATERIAL JORNALÍSTICO abaixo. É TERMINANTEMENTE PROIBIDO inventar ou "preencher lacunas" com nomes de clubes, jogadores, técnicos ou dirigentes; valores, propostas ou salários; datas, placares ou números; declarações e aspas; transferências, negociações, recusas ou interesses de outros clubes que NÃO estejam escritos no material. Se o material não diz, você NÃO sabe — não afirme.
- **Rumor não é fato:** se o material trata algo como possibilidade ou especulação, trate como possibilidade ("pode", "estuda", "especula-se") — NUNCA como certeza consumada. Não dê nome a um "clube interessado" que o material não nomeou.
- **Opinião sim, invenção não:** tom quente, indignação, provocação e crítica são LIVRES e bem-vindos — mas têm que se apoiar SÓ nos fatos do material. Tenha opinião forte sobre o que aconteceu DE VERDADE; nunca crie um acontecimento pra sustentar a opinião.
- Na dúvida entre ser específico (com risco de inventar) e ser fiel ao material, seja FIEL ao material, mesmo que o texto fique mais genérico. Credibilidade vale mais que detalhe.
- Escreva na voz e no estilo EXATO do jornalista descrito no perfil acima
- Crie um título próprio (não copie o título original)
- Crie uma linha de apoio (subtitle) curta e impactante
- O corpo do artigo deve ser original — não copie trechos do material, reescreva com sua voz
- Use linguagem brasileira informal e termos típicos de torcedores do Flamengo
- Escreva com sotaque CARIOCA seguindo o glossário acima: use as gírias da Seção 1/2 com a dose certa (2 a 4 por texto, sem virar caricatura)
- NUNCA use os termos paulistas/genéricos da Seção 3 do glossário (ex.: "mano", "meu" como vocativo, "véio", "bagulho", "da hora", "é nóis")
- Respeite a parcimônia indicada por persona (Thiago usa mais gíria; Rodrigo, pouca; Fernanda, as mais secas)
- **IMPORTANTE:** Sempre use o técnico ATUAL do elenco acima (Leonardo Jardim) — NUNCA mencione Tite ou técnicos antigos
- **IMPORTANTE:** Ao mencionar jogadores, use APENAS os nomes do elenco listado acima — não invente nomes nem mencione jogadores que saíram
- **IMPORTANTE (Ninho do Urubu):** "Ninho" / "Ninho do Urubu" é o CT onde TODO o elenco treina. Dizer que um jogador "treina/está no Ninho" NÃO o torna cria da base.
- **IMPORTANTE (Cria do Ninho):** Só chame um jogador de "cria do ninho", "cria da base", "moleque do ninho", "garoto do ninho", "joia da base" ou "revelado pelo Flamengo" se o nome dele estiver na lista CRIAS DA BASE acima. Para QUALQUER outro jogador, é PROIBIDO usar esses termos.
- **IMPORTANTE:** Na dúvida sobre a origem de um jogador, NÃO afirme que ele veio da base do Flamengo.
- Jornalismo honesto, não bajulação: se o Fla jogou mal, diga; se a decisão foi errada, aponte; se a contratação foi questionável, questione. Torcedor que ama o clube exige verdade, não jabá
- Pode ser irreverente, apaixonado, provocador — mas nunca chulo
- Responda SOMENTE com JSON válido, sem markdown, sem texto fora do JSON

--- MATERIAL JORNALÍSTICO ---
Título original: {original_title}

Texto completo:
{full_text[:3000]}
--- FIM DO MATERIAL ---

Responda com este JSON exato:
{{
    "title": "<título criado pelo jornalista>",
    "subtitle": "<linha de apoio curta>",
    "body": "<corpo completo do artigo, com parágrafos separados por \\n\\n>"
}}
"""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        article = json.loads(response.text)
        return article, source_name, source_url, original_title
    except Exception as err:
        print(f"[Falha na API] Erro durante geração com Gemini: {err}")
        return None, source_name, source_url, original_title


def main():
    print("--- Iniciando Gerador de Artigos ---")

    story_data = load_selected_story()
    if not story_data:
        return

    personas = load_personas()
    if not personas:
        print("[Erro] Nenhum arquivo de persona encontrado em 'personas/'.")
        return

    glossary = load_glossary()
    elenco = load_elenco()

    chosen_persona = random.choice(personas)
    print(f" Jornalista selecionado(a): {chosen_persona['name']}")
    print(f" Glossário carioca: {'carregado' if glossary else 'ausente'}")
    print(f" Elenco Flamengo: {'carregado' if elenco else 'ausente'}")
    print(f" Notícia base: {story_data.get('article_data', {}).get('title', '')[:60]}...")

    print("\n--- Enviando para o Gemini ---")
    article, source_name, source_url, original_title = generate_article(story_data, chosen_persona, glossary, elenco)

    if not article:
        print("[Execução Interrompida] Falha na geração do artigo.")
        return

    print(f"\n--- Artigo Gerado ---")
    print(f" Título: {article.get('title', '')}")
    print(f" Subtítulo: {article.get('subtitle', '')}")
    print(f" Palavras: ~{len(article.get('body', '').split())}")

    output = {
        "generated_at": datetime.now().isoformat(),
        "author": chosen_persona["name"],
        "article": {
            "title": article.get("title", ""),
            "subtitle": article.get("subtitle", ""),
            "body": article.get("body", "")
        },
        "source_ref": {
            "original_title": original_title,
            "source_name": source_name,
            "source_url": source_url
        }
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"article_{timestamp}.json")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=4)

    print(f"\nSucesso! Artigo salvo em: '{output_path}'.")


if __name__ == "__main__":
    main()
