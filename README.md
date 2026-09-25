# BuscaEM — reconstrução do zero

Projeto limpo para um buscador internacional de periódicos especializados em Educação Matemática.

## Princípios
1. Só entram no site registros classificados como artigos.
2. Metadados canônicos vêm da fonte oficial (OAI/OJS, SciELO ou editora).
3. Crossref é fallback/complemento.
4. OpenAlex pode ser usado para descoberta/métricas, nunca para sobrescrever metadados oficiais.
5. Metadados em inglês só entram quando a fonte os fornece.
6. Registros incompletos ou de tipo duvidoso ficam em quarentena, não no site.

## Fluxo
config/journals.json
→ coleta
→ harvest/raw/
→ normalização
→ curadoria
→ harvest/normalized/catalog.json
→ scripts/publish.py
→ dist/data/records-*.json
→ Cloudflare Pages

## Primeiro uso
1. Cadastre revistas em `config/journals.json`.
2. Rode `python scripts/build_empty.py` para validar a estrutura.
3. Implemente/ative os coletores por fonte.
4. Rode `python scripts/publish.py`.
5. Publique `dist/` no Cloudflare Pages.

O repositório começa deliberadamente sem corpus.
