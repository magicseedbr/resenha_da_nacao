import os
import json
import glob
import random
from datetime import datetime
from google import genai
from groq import Groq

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
GROQ_MODEL_NAME = "llama-3.3-70b-versatile"

# API Key carregada do arquivo .env na raiz do projeto (um nível acima deste script)
load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

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


def _via_gemini(prompt):
    """Gera o JSON do artigo via Gemini. Retorna o texto (JSON) bruto."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=prompt,
        config={"response_mime_type": "application/json", "temperature": 0.7},
    )
    return response.text


def _via_groq(prompt):
    """Fallback: gera o JSON do artigo via Groq (llama-3.3). Retorna o texto bruto."""
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    return resp.choices[0].message.content


def _generate_json(prompt):
    """Tenta Gemini; se falhar (ex.: quota 429), cai para o Groq. Retorna texto JSON ou None."""
    try:
        text = _via_gemini(prompt)
        print(" (via Gemini)")
        return text
    except Exception as err:
        es = str(err)
        tag = "[Gemini 429]" if ("429" in es or "quota" in es.lower()) else "[Gemini falhou]"
        print(f" {tag} {es[:90]} — tentando Groq...")
        if not GROQ_API_KEY:
            print(" [Falha] GROQ_API_KEY não definida no .env — sem fallback.")
            return None
        try:
            text = _via_groq(prompt)
            print(" (via Groq fallback)")
            return text
        except Exception as err2:
            print(f" [Falha] Groq também falhou: {err2}")
            return None


def generate_article(story_data, persona, glossary="", elenco=None):
    if not GEMINI_API_KEY and not GROQ_API_KEY:
        raise ValueError("[Falha de Autenticação] Defina GEMINI_API_KEY (e/ou GROQ_API_KEY) no .env da raiz do projeto.")

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

    prompt = f"""Você é o(a) jornalista abaixo, escrevendo uma MATÉRIA para o portal Resenha da Nação — não um desabafo de arquibancada. Sua missão número 1 é INFORMAR: o leitor tem que terminar o texto sabendo mais do que sabia pela manchete. A personalidade do jornalista é o tempero; a informação e a análise são o prato.

--- PERFIL DO JORNALISTA ---
{persona['content']}
--- FIM DO PERFIL ---
{glossary_block}{elenco_block}
Com base no material jornalístico abaixo, escreva um artigo original de 500 a 800 palavras.

=== O QUE FAZ UM TEXTO BOM (leia primeiro) ===
- INFORME antes de opinar. Entregue os fatos, o contexto e o que está em jogo. Quem lê quer sair sabendo o que aconteceu, por quê e qual a consequência — não só sentir emoção.
- DENSIDADE: cada parágrafo precisa carregar um fato, um dado, um antecedente ou uma análise. Corte adjetivação vazia, exagero, melodrama e frase de efeito que não acrescentam informação. Se um parágrafo só transmite emoção, ele sobra.
- CRÍTICA FUNDAMENTADA, não sensacionalismo: opinião forte é bem-vinda, mas vem DEPOIS do fato e SEMPRE justificada com argumento ("é bom/ruim porque...", trade-offs, consequências). Nada de manchete-apelo nem abertura puramente emocional.
- Jornalismo honesto, não bajulação: se o Fla jogou mal, diga e explique por quê; se a decisão foi errada, aponte o erro; se a contratação é questionável, mostre o risco. Torcedor que ama o clube exige verdade, não jabá.

=== ESTRUTURA OBRIGATÓRIA DO CORPO (parágrafos separados por \\n\\n) ===
1. LIDE: já nos primeiros parágrafos, entregue o fato principal — o quê, quem, quando e por quê. Sem suspense vazio nem enrolação.
2. CONTEXTO: antecedentes e situação (do time, do jogador, da negociação) que explicam por que o fato importa e o que o motivou.
3. ANÁLISE ANCORADA: sua leitura crítica do fato, com argumento, apoiada SÓ no que está no material.
4. FECHO: conclusão ou o que observar a seguir.

=== FIDELIDADE AOS FATOS (inegociável) ===
- REGRA DE OURO: escreva APENAS com base nos fatos do MATERIAL abaixo. É PROIBIDO inventar ou "preencher lacunas" com nomes de clubes, jogadores, técnicos ou dirigentes; valores, propostas ou salários; datas, placares ou números; declarações e aspas; transferências, negociações, recusas ou interesses de outros clubes que NÃO estejam no material. Se o material não diz, você NÃO sabe — não afirme.
- Rumor não é fato: se o material trata algo como possibilidade, trate como possibilidade ("pode", "estuda", "especula-se") — nunca como certeza. Não nomeie um "clube interessado" que o material não nomeou.
- Na dúvida entre ser específico (com risco de inventar) e ser fiel ao material, seja FIEL — mesmo que o texto fique mais genérico. Credibilidade vale mais que detalhe.

=== ESTILO E VOZ (secundário à substância) ===
- Escreva na voz e no estilo do jornalista do perfil acima, mas sem deixar a personalidade atropelar a informação.
- Crie um título próprio (não copie o original) e uma linha de apoio (subtitle) curta — informativos, não apelativos.
- O corpo deve ser original: reescreva com sua voz, não copie trechos do material.
- Sotaque CARIOCA conforme o glossário: 2 a 4 gírias por texto (Seções 1/2), sem virar caricatura; respeite a parcimônia da persona (Thiago usa mais, Rodrigo pouca, Fernanda as mais secas). NUNCA use os termos paulistas/genéricos da Seção 3 ("mano", "meu" como vocativo, "véio", "bagulho", "da hora", "é nóis").
- Pode ser irreverente e apaixonado, mas nunca chulo.

=== REGRAS FACTUAIS DE CONTEXTO (técnico / Ninho / elenco) ===
- Leonardo Jardim é o técnico do FLAMENGO. Só chame alguém de "nosso técnico"/"o Mister" e atribua estratégias a ele quando a matéria for sobre o FLAMENGO. Se for sobre um jogador na SELEÇÃO ou outro time, o técnico e as táticas são DAQUELE time — não atribua nada ao Jardim; só nomeie o treinador se ele estiver no material, senão fale de forma genérica ("o técnico da Seleção"). NUNCA mencione Tite ou técnicos antigos do Flamengo.
- Ao citar jogadores do FLAMENGO, use APENAS os nomes do elenco listado acima — não invente nem cite quem saiu. Para gente de fora do Fla, só cite quem está NO MATERIAL e não atribua a eles vínculo com o Flamengo que o material não afirme.
- "Ninho"/"Ninho do Urubu" é o CT onde TODO o elenco treina — dizer que alguém "treina no Ninho" NÃO o torna cria da base. Só chame de "cria do ninho", "cria da base", "moleque/garoto do ninho", "joia da base" ou "revelado pelo Flamengo" quem estiver na lista CRIAS DA BASE acima; para qualquer outro, é PROIBIDO. Em matéria de Seleção ou outro time, não chame companheiros ou adversários de "cria do Ninho". Na dúvida sobre a origem de um jogador, NÃO afirme que ele veio da base do Flamengo.

Responda SOMENTE com JSON válido, sem markdown, sem texto fora do JSON.

--- MATERIAL JORNALÍSTICO ---
Título original: {original_title}

Texto completo:
{full_text[:6000]}
--- FIM DO MATERIAL ---

Responda com este JSON exato:
{{
    "title": "<título criado pelo jornalista>",
    "subtitle": "<linha de apoio curta>",
    "body": "<corpo completo do artigo, com parágrafos separados por \\n\\n>"
}}
"""

    raw = _generate_json(prompt)
    if raw is None:
        return None, source_name, source_url, original_title
    try:
        article = json.loads(raw)
        return article, source_name, source_url, original_title
    except json.JSONDecodeError as err:
        print(f"[Falha] Resposta da API não é JSON válido: {err}")
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
