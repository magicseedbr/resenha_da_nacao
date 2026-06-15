"""
Mantém automaticamente a lista de "crias da base" do Flamengo em
elenco_flamengo.json.

"Cria do Ninho" é o jogador FORMADO nas categorias de base do Flamengo — não
qualquer jogador que treina no Ninho do Urubu (o CT é usado por todo o elenco).
Este script pergunta ao Gemini quais jogadores do elenco subiram da base e grava
o resultado no campo `crias_da_base`.

Para não gastar API a cada rodada do pipeline, guarda um hash do conjunto de
nomes do elenco (`crias_hash`): se o elenco não mudou desde a última detecção,
sai sem chamar a API. Em caso de falha na API, mantém a lista existente.
"""

import os
import re
import json
import hashlib
from datetime import datetime
from google import genai

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ELENCO_FILE = os.path.join(SCRIPT_DIR, "elenco_flamengo.json")
GEMINI_MODEL_NAME = "gemini-2.5-flash"


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


load_env_file(os.path.join(SCRIPT_DIR, "..", ".env"))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


def player_name(entry):
    """'Pedro (atacante)' -> 'Pedro'. Remove a posição entre parênteses."""
    return re.sub(r"\s*\(.*?\)\s*$", "", entry).strip()


def squad_hash(names):
    """Hash estável do conjunto de nomes do elenco (ordem não importa)."""
    payload = "|".join(sorted(n.lower() for n in names))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_elenco():
    if not os.path.exists(ELENCO_FILE):
        print(f"[Erro] Elenco não encontrado: '{ELENCO_FILE}'")
        return None
    try:
        with open(ELENCO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as err:
        print(f"[Erro de Leitura] Falha ao ler elenco_flamengo.json: {err}")
        return None


def detect_crias(names):
    """Pergunta ao Gemini quais nomes são crias da base do Flamengo. Retorna lista (ou None em falha)."""
    if not GEMINI_API_KEY:
        raise ValueError("[Falha de Autenticação] Defina GEMINI_API_KEY no arquivo .env da raiz do projeto.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    jogadores_str = "\n".join(f"- {n}" for n in names)
    prompt = f"""Você é um especialista na história do Clube de Regatas do Flamengo.

Para CADA jogador da lista abaixo, determine se ele é uma "cria da base do
Flamengo", ou seja: se ele foi FORMADO nas categorias de base do Flamengo
(subiu do Ninho do Urubu para o time profissional do PRÓPRIO Flamengo).

REGRAS RÍGIDAS:
- NÃO conta como cria: jogador contratado/comprado de outro clube, mesmo que hoje
  treine no Ninho do Urubu (o CT é usado por todo o elenco).
- NÃO conta como cria: jogador formado na base de OUTRO clube.
- Só inclua jogadores dos quais você tem CERTEZA de que se formaram na base do
  Flamengo. Na menor dúvida, NÃO inclua.

Jogadores:
{jogadores_str}

Responda SOMENTE com JSON válido, sem markdown:
{{
    "crias_da_base": ["<nome exato do jogador, como na lista, sem a posição>"]
}}
"""
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(response.text)
        crias = data.get("crias_da_base", [])
        # Mantém apenas nomes que realmente estão no elenco enviado (defesa contra alucinação).
        valid = {n.lower(): n for n in names}
        return [valid[c.lower()] for c in crias if c.lower() in valid]
    except Exception as err:
        print(f"[Falha na API] Erro durante detecção de crias com Gemini: {err}")
        return None


def main():
    print("--- Atualizando lista de crias da base ---")
    elenco = load_elenco()
    if elenco is None:
        return

    names = [player_name(j) for j in elenco.get("jogadores_principais", [])]
    if not names:
        print("[Aviso] Elenco sem jogadores principais. Nada a fazer.")
        return

    current_hash = squad_hash(names)
    if elenco.get("crias_hash") == current_hash and "crias_da_base" in elenco:
        print(f"Elenco inalterado (hash {current_hash[:12]}). Pulando — sem chamada à API.")
        print(f" Crias atuais: {elenco.get('crias_da_base', [])}")
        return

    print(f" Elenco mudou (ou primeira execução). Consultando Gemini para {len(names)} jogadores...")
    crias = detect_crias(names)
    if crias is None:
        print("[Mantido] Falha na API — lista de crias existente preservada.")
        return

    elenco["crias_da_base"] = crias
    elenco["crias_hash"] = current_hash
    elenco["crias_atualizadas_em"] = datetime.now().isoformat()

    with open(ELENCO_FILE, "w", encoding="utf-8") as f:
        json.dump(elenco, f, ensure_ascii=False, indent=2)

    print(f"Sucesso! Crias da base detectadas: {crias if crias else '(nenhuma confirmada)'}")


if __name__ == "__main__":
    main()
