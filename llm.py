"""
Helper único de LLM do pipeline.

Centraliza a chamada de modelo que antes estava espalhada em 6 scripts (curador,
artigo, update_crias, post do X, comentários, instagram). Em vez do Gemini (com
teto de 20 req/dia no free tier), usa o CLI `claude -p` em modo headless — que
reaproveita a assinatura do Claude Code (sem custo marginal e sem o teto diário).

Uso típico (substitui o antigo `_via_gemini`):

    import llm
    texto = llm.generate(prompt)          # devolve a resposta como string
    dados = json.loads(texto)             # os callers continuam fazendo json.loads

Trocar de provedor no futuro = mexer só neste arquivo.
"""

import json
import shutil
import subprocess

# Modelo padrão: Haiku é barato/rápido e suficiente p/ comentários, hooks e
# classificação. Quem quiser mais qualidade (ex.: o artigo) passa model="sonnet".
DEFAULT_MODEL = "haiku"

_CLAUDE_BIN = shutil.which("claude") or "claude"
_TIMEOUT = 180  # s por chamada


class LLMError(RuntimeError):
    """Falha ao gerar texto via CLI do Claude."""


def _strip_fences(text):
    """Remove cercas de código markdown (```json ... ```) que o modelo às vezes põe."""
    t = (text or "").strip()
    if t.startswith("```"):
        # tira a primeira linha (``` ou ```json) e a cerca final
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def generate(prompt, model=DEFAULT_MODEL, json_output=True):
    """Roda o prompt no Claude (CLI `claude -p`) e devolve o texto da resposta.

    Com json_output=True (padrão), reforça a instrução de responder só JSON e
    limpa eventuais cercas de código — assim os callers seguem usando json.loads.
    Levanta LLMError em qualquer falha (sem fallback silencioso).
    """
    full = prompt.rstrip()
    if json_output:
        full += "\n\nResponda APENAS com JSON válido, sem markdown e sem cercas ```."

    cmd = [_CLAUDE_BIN, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(
            cmd, input=full, capture_output=True, text=True, timeout=_TIMEOUT
        )
    except FileNotFoundError:
        raise LLMError("CLI 'claude' não encontrado no PATH.")
    except subprocess.TimeoutExpired:
        raise LLMError(f"CLI 'claude' excedeu {_TIMEOUT}s.")

    if proc.returncode != 0:
        raise LLMError(f"CLI 'claude' falhou (rc={proc.returncode}): {proc.stderr.strip()[:300]}")

    # Envelope do --output-format json: {"type":"result","result":"<texto>", ...}
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # Sem envelope (modo texto puro): usa o stdout cru.
        return _strip_fences(proc.stdout) if json_output else proc.stdout.strip()

    if env.get("is_error"):
        raise LLMError(f"CLI 'claude' retornou erro: {str(env.get('result'))[:300]}")

    text = env.get("result", "")
    return _strip_fences(text) if json_output else text.strip()
