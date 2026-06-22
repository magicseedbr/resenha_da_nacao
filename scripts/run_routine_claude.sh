#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
PROJ="/Users/diego.louvisse/Desktop/Pessoal/Codes/resenha_da_nacao"
LOG="$PROJ/logs/routine_cron.log"
cd "$PROJ" || exit 1

echo "===== $(date '+%F %T') — disparo agendado =====" >> "$LOG"

read -r -d '' PROMPT <<'EOF'
Execução agendada da rotina Resenha da Nação no diretório atual. Rode `./venv/bin/python routine.py`. Se a curadoria não trouxer notícia inédita do dia, a rotina para sozinha — normal, encerre sem gerar comentários. Se gerar artigo novo (já deployado), publique no X com `./venv/bin/python routine.py xpost --publish`. Gere os comentários com `./venv/bin/python routine.py comentarios`, cure pela qualidade (descarte fabricação factual, palavrão e genéricos/fora do tema), reduza pending_comments.json aos bons e publique com `./venv/bin/python -u x_commenter/x_comment_publisher_0_1.py`. Se a notícia repetir uma já usada, escolha outra do pool. REGRA DE OURO: nunca publique nada com erro factual. Reporte um resumo curto.
EOF

/opt/homebrew/bin/claude -p "$PROMPT" --dangerously-skip-permissions >> "$LOG" 2>&1
echo "----- fim ($(date '+%T')) -----" >> "$LOG"
