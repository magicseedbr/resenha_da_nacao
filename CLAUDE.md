# Resenha da Nação — News Curator

Site automatizado de notícias sobre o Flamengo. O pipeline extrai notícias de portais, mede relevância social via tweets, seleciona a melhor história com Gemini AI, gera um artigo original por um dos 5 jornalistas fictícios e (etapa futura) publica no site.

## Estrutura do Projeto

```
resenha_da_nacao/
├── CLAUDE.md
├── requirements.txt
├── venv/
├── news_extractor/          # Etapa 1: Extração de artigos e tweets
│   ├── news_extractor_0_3.py          # Scraper RSS do Coluna do Fla
│   ├── news_extractor_globo_0_3.py    # Scraper do Globo Esporte
│   ├── news_extractor_x_0_1.py        # Extrator de tweets via DuckDuckGo
│   ├── raw_news/                       # JSONs das notícias extraídas
│   ├── raw_tweets/                     # JSONs dos tweets extraídos
│   ├── processed_links.json            # Histórico de URLs de artigos já processadas
│   └── processed_tweets.json           # Histórico de URLs de tweets já processadas
├── news_curator/            # Etapa 2: Curadoria — seleciona a melhor notícia
│   ├── news_extractor_curator_0_1.py   # Calcula Hype Score + decisão via Gemini
│   └── selected_story.json             # Saída: notícia vencedora com justificativa
├── image_searcher/          # Etapa 3: Busca de imagem para o artigo
│   ├── image_searcher_0_1.py           # Scraping orgânico Google (sem API)
│   └── downloaded_images/              # Imagens baixadas
├── article_writer/          # Etapa 4: Geração do artigo original com Gemini
│   ├── personas/
│   │   ├── rodrigo_marques.md          # Cronista veterano com perspectiva histórica
│   │   ├── thiago_vasconcelos.md       # Analista descolado com leitura tática acessível
│   │   ├── fernanda_aguiar.md          # Direta e incisiva, veredicto fundamentado
│   │   ├── bruno_tavares.md            # Analista tático (ex-base), explica o porquê do jogo
│   │   └── carla_menezes.md            # Repórter de bastidores/mercado, separa fato de rumor
│   ├── elenco_flamengo.json            # Elenco atual + crias_da_base (mantido por update_crias.py)
│   ├── glossario_carioca.md            # Guia de sotaque/vocabulário rubro-negro
│   ├── update_crias.py                 # Detecta via Gemini quem é cria da base e atualiza crias_da_base
│   ├── article_writer_0_1.py           # Seleciona o jornalista que menos escreveu (balanceado) e gera artigo
│   └── generated_articles/             # JSONs dos artigos gerados
├── site_builder/            # Etapa 5: Publicação — gera o site estático rubro-negro
│   ├── shared.py                       # Utils: slug, editoria por palavra-chave, personas
│   ├── approve.py                      # Gate de aprovação (CLI): revisa e aprova artigos
│   ├── image_searcher_og_0_1.py        # Capa real: extrai og:image da matéria original de cada aprovado
│   ├── build_site.py                   # Gerador estático (Jinja2): home/artigo/editoria/jornalista
│   ├── approved.json                   # Registro dos artigos aprovados (fonte de verdade do site)
│   ├── templates/                      # Templates HTML (base, index, article, editoria, jornalista)
│   ├── static/                         # CSS responsivo rubro-negro, logo.png da marca, escudo, capa-padrão e covers/
│   └── output/                         # Site estático gerado (abrir output/index.html)
└── instagram_creator/       # Etapa 6: Gera posts prontos para o Instagram
    ├── instagram_post_creator_0_1.py   # Gera legenda (Gemini) + copia capa por artigo aprovado
    └── instagram_posts/                # Saída: uma pasta por artigo com caption.txt e image.*
```

## Pipeline de Execução

Rodar na ordem abaixo a partir do diretório `news_extractor/`:

```bash
# 1. Extrai artigos do Coluna do Fla via RSS
python news_extractor_0_3.py

# 2. Extrai artigos do Globo Esporte via scraping
python news_extractor_globo_0_3.py

# 3. Coleta tweets recentes sobre Flamengo via DuckDuckGo
python news_extractor_x_0_1.py

# 4. Seleciona a melhor notícia (Hype Score + Gemini)
cd ../news_curator && python news_extractor_curator_0_1.py

# 5. Busca imagem relevante para o artigo vencedor
cd ../image_searcher && python image_searcher_0_1.py

# 6. Atualiza a lista de crias da base (Gemini; só chama a API se o elenco mudou)
cd ../article_writer && python update_crias.py

# 6b. Gera artigo original por um dos 5 jornalistas fictícios
python article_writer_0_1.py

# 7. Revisa e aprova artigos para publicação (etapa manual)
cd ../site_builder && python approve.py

# 8. Busca a capa real de cada artigo aprovado (og:image da matéria original)
python image_searcher_og_0_1.py

# 9. Gera o site estático com os artigos aprovados (abre output/index.html)
python build_site.py

# 10. Gera posts prontos para publicar no Instagram (legenda + imagem por artigo aprovado)
cd ../instagram_creator && python instagram_post_creator_0_1.py

# 11. Revisa e publica os posts no Instagram via Meta Graph API (etapa manual)
python instagram_publish_0_1.py
```

### Rotina consolidada (`routine.py`)
`routine.py` (na raiz) encapsula o fluxo diário em 4 fases: **criar** (extrai notícias do dia + cura + gera o artigo), **site** (auto_approve + capa + build + commit/push no main → deploy do GitHub Pages, esperando o artigo subir), **xpost** (gera o post do X) e **comentarios** (gera comentários nos perfis-alvo). As fases que postam no X **só geram e exibem** o conteúdo por padrão; publicam de fato apenas com `--publish` (mantém a publicação supervisionada). Para publicar o post usa o `x_ci_publisher` (headless, sem gate), gerando o `X_SESSION_JSON` a partir do `x_creator/x_session.json` local.

```bash
python routine.py                       # tudo, sem postar no X (gera/exibe para revisão)
python routine.py criar site            # cria o artigo e publica no site (com deploy)
python routine.py xpost --publish       # gera e publica o post do X
python routine.py comentarios --keep geglobo,Brasileirao --publish   # publica só os perfis curados
python routine.py tudo --publish        # fluxo completo postando no X
python routine.py site --no-deploy      # gera o site local, sem push no main
```

## Lógica Principal

### Apenas notícias do dia corrente
O pipeline trabalha **só com notícias publicadas no dia em que roda**:
- **Coluna do Fla** (`news_extractor_0_3.py`): filtra os entries do RSS por `published_parsed` (helper `is_published_today`) e para a varredura cedo ao passar do dia (o RSS é reverso-cronológico).
- **Globo Esporte** (`news_extractor_globo_0_3.py`): extrai a data **real** da matéria (`extract_published_date`: meta `article:published_time` → `datePublished` → `<time datetime>`) e grava em `published_at`. Regra **rigorosa**: sem data confirmada ou data ≠ hoje, o artigo é descartado.
- **Curador** (`news_extractor_curator_0_1.py`): como `raw_news/` acumula arquivos, o curador só considera itens com `extracted_at` de hoje (helper `is_extracted_today`), garantindo o dia corrente mesmo com sobras de dias anteriores.

### Não repetir notícias curadas
O curador grava a URL de cada vencedora em `news_curator/used_stories.json` e exclui essas URLs nas próximas execuções (dedup por `source_url`). As candidatas que perderam não são marcadas — só a notícia efetivamente curada.

### Hype Score (news_curator)
Mede relevância social cruzando palavras-chave do título da notícia com o corpo dos tweets coletados. Notícias com mais palavras presentes nos tweets ganham score mais alto.

### Curadoria com Gemini
Os 3 artigos com maior Hype Score são enviados ao Gemini 2.5 Flash como candidatos. O modelo escolhe o mais adequado para um artigo longo considerando valor jornalístico + engajamento social.

### Ninho do Urubu vs. Cria do Ninho (regra importante)
**"Ninho do Urubu"** é o CT onde **todo o elenco** treina — citar o Ninho não diz nada sobre a origem do jogador. **"Cria do Ninho"** (e variantes: "cria da base", "moleque/garoto do ninho", "joia da base", "revelado pelo Flamengo") vale **apenas** para jogadores formados nas categorias de base do Flamengo. Por isso:
- `article_writer/update_crias.py` consulta o Gemini para descobrir quais jogadores do elenco são crias e grava em `crias_da_base` (no `elenco_flamengo.json`), com cache por hash do elenco para não chamar a API à toa. O prompt de geração só autoriza o termo para nomes dessa lista.
- A editoria "Crias do Ninho" (`site_builder/shared.py`) só é atribuída por frases reais de base (`sub-20`, `cria do ninho`, `categoria de base`, `joia da base`, etc.) — não mais por mencionar o CT ("ninho"/"gávea"/"base" soltos foram removidos).

### Publicação (site_builder)
Site estático, sem servidor nem banco. `approve.py` é um gate manual: lista os artigos de `generated_articles/` ainda não publicados, mostra prévia e registra os aprovados em `approved.json` (com slug e editoria derivada por palavras-chave do título/corpo). `build_site.py` lê `approved.json` e renderiza com Jinja2 a home (manchete + grade de cards estilo ge.globo), as páginas de artigo, as editorias (Mercado da Bola, Crias do Ninho, Seleção, Bastidores, Geral) e a página de cada jornalista (bio extraída da persona). Tema rubro-negro inspirado em flamengo.com.br, com a marca `static/logo.png` (PNG transparente) sobre o cabeçalho preto + barra de navegação, e layout responsivo (desktop, tablet e mobile via `clamp()` e media queries). A capa de cada artigo é a imagem real obtida por `image_searcher_og_0_1.py` (campo `cover` no `approved.json`); artigos sem capa caem em `static/capa_padrao.svg`.

### Schema dos JSONs em raw_news/
```json
{
  "title": "...",
  "source_name": "Coluna do Fla Archive | Globo Esporte Cluster",
  "source_url": "...",
  "published_at": "...",
  "full_text": "...",
  "extracted_at": "ISO timestamp"
}
```

### Schema de saída do curador (selected_story.json)
```json
{
  "curated_at": "ISO timestamp",
  "hype_score": 17,
  "justification": "Justificativa do Gemini em português",
  "article_data": { "title", "source_name", "source_url", "full_text" },
  "media_data": { ... }  // adicionado pelo image_searcher
}
```

## Stack Técnica

- **Python 3.12** com venv
- **LLM:** Gemini 2.5 Flash (`google-generativeai`)
- **Scraping:** BeautifulSoup4, requests, feedparser
- **Concorrência:** ThreadPoolExecutor
- **Twitter/X:** DuckDuckGo HTML search (sem API oficial)
- **Armazenamento:** Arquivos JSON locais (sem banco de dados)

## Configuração

A `GEMINI_API_KEY` é carregada de um arquivo `.env` na raiz do projeto (por um leitor embutido `load_env_file`, sem dependências externas). Antes de rodar o pipeline, crie o `.env` a partir do template:

```bash
cp .env.example .env
# edite .env e coloque sua chave do Google AI Studio
```

O `.env` está no `.gitignore` e nunca deve ser commitado. Os scripts que usam Gemini (`news_curator/news_extractor_curator_0_1.py` e `article_writer/article_writer_0_1.py`) leem a chave de `os.environ`.

## Problemas Conhecidos

- **Google bloqueando image_searcher (legado):** O `image_searcher/image_searcher_0_1.py` raspava o Google orgânico, que passou a bloquear com challenge de JavaScript (`google_blocked_page.html`). Esse caminho foi substituído por `site_builder/image_searcher_og_0_1.py`, que extrai a capa direto da matéria original (og:image) e não sofre bloqueio.

## Jornalistas Fictícios (article_writer/personas/)

Cada persona tem uma voz distinta, mas todas partem da mesma base: **informar primeiro, opinar com base no fato**. A personalidade é o tempero; a informação e a análise são o prato.

- **Rodrigo Marques** — cronista veterano; perspectiva histórica e leitura de longo prazo, crítica fundamentada
- **Thiago Vasconcelos** — analista jovem e descolado; leitura tática acessível e dados, humor a serviço do argumento
- **Fernanda Aguiar** — direta e incisiva; síntese e veredicto sempre com o porquê na sequência
- **Bruno Tavares** — analista tático (ex-categorias de base); explica o *porquê* do jogo (esquema, funções, espaço) de forma acessível
- **Carla Menezes** — repórter de bastidores e mercado; contexto de negociação/gestão/finanças, rigor em separar fato de rumor

## Etapas Futuras (a implementar)

- [x] Publicação no site (solução custom: `site_builder/`, site estático)
- [x] Religar a imagem real do artigo (`site_builder/image_searcher_og_0_1.py`: og:image da matéria original -> `cover` no approved.json -> exibido pelo `build_site.py`)
- [x] Mover chaves de API para variáveis de ambiente (`.env` + leitor embutido `load_env_file`)
- [ ] Deploy do site gerado (Netlify, Vercel ou GitHub Pages)
- [ ] Automação do pipeline completo (cron job ou agendamento)
