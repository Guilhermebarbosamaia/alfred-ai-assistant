# CLAUDE.md

Assistente pessoal local em Python 3.12 (terminal e Telegram) que usa um modelo
via Ollama local. Veja o `README.md` para instalação, comandos e variáveis.

## Idioma

- Responda ao usuário em português do Brasil.
- Código, identificadores, docstrings, comentários, mensagens ao usuário e
  logs em português (ex.: `Configuracao`, `salvar_nota`, `testes/`).
- Mensagens de commit seguem o padrão já usado no histórico: em inglês, curtas,
  no imperativo (ex.: `Limit model history by token budget`).

## Testes

- Rode com `pytest` na raiz (usa `pytest.ini`; testes ficam em `testes/`).
- Testes **nunca** acessam a rede: nada de Ollama real, API do Telegram,
  Open-Meteo ou qualquer outro serviço. Use o provedor falso, `SimpleNamespace`
  ou substitua o cliente (`provedor._cliente.chat = ...`), como em
  `testes/test_provedor.py`.
- Bancos de teste ficam em `tmp_path`, nunca em `dados/`.
- Todo comportamento novo ou corrigido ganha teste. Rode a suíte inteira antes
  de dar uma tarefa por concluída.
- `scripts/benchmark_ollama.py` precisa do Ollama real; não é teste e não deve
  ser chamado pela suíte.

## Logs e privacidade

- Nunca registre conteúdo nos logs: texto de mensagens do usuário, respostas do
  modelo, notas, lembretes, hábitos, prompts ou histórico.
- Logs podem conter apenas metadados: tempos, contagens de tokens, tentativas,
  nomes de operação/ferramenta, IDs e o tipo da exceção
  (`type(erro).__name__`).
- Tokens e segredos nunca aparecem em logs; o `FiltroToken` de
  `src/interfaces/telegram.py` existe para isso e deve continuar aplicado.
- Ao mexer em logs, adicione teste com `caplog` verificando que o conteúdo
  **não** aparece (padrão de `test_chamada_registra_metricas_sem_conteudo_da_resposta`).

## Segredos e arquivos que não vão para o git

- Nunca commite o `.env` nem o leia ou exiba sem necessidade. Variáveis novas
  vão para `.env.example` (sem valores reais), `src/config.py` e o `README.md`.
- Nunca commite `dados/` (banco SQLite e `dados/backup/`), `.venv/` ou caches.
- Antes de commitar, confira `git status` e adicione arquivos pelo nome, não
  com `git add -A`/`git add .`.

## Arquitetura e regras do código

- Execução local: a aplicação só se conecta ao Ollama local, à API do Telegram
  (quando ativa) e ao Open-Meteo (quando `CLIMA_ATIVO=true`). Não adicione
  outros serviços externos sem pedir.
- O modelo só interpreta e redige; o código decide. Banco, horários, cálculos
  (resumo, progresso de hábitos) e disparo de lembretes ficam no código.
- Saídas do modelo são validadas com Pydantic antes de qualquer ação; nunca
  execute ação com dado não validado.
- Ferramentas ficam em `src/ferramentas/` e são registradas em
  `src/ferramentas/registro.py` com um `NivelRisco`. Ações destrutivas são
  `SENSIVEL` e exigem confirmação explícita (`src/seguranca/permissoes.py`).
- O Telegram atende apenas `TELEGRAM_ID_AUTORIZADO`; não afrouxe essa checagem.
- Mudanças no esquema do banco exigem nova migração versionada em
  `src/memoria/banco.py` (incrementar `_VERSAO_ATUAL`), com backup e teste.
  Nunca altere migrações existentes.
- Datas são gravadas em UTC e exibidas no fuso de `FUSO_HORARIO`.
- O modelo padrão roda em máquinas com 8 GB de RAM: respeite os limites de
  contexto e geração (`OLLAMA_NUM_CTX`, `OLLAMA_NUM_PREDICT`, orçamento do
  histórico) e evite prompts grandes.
- Dependências novas vão para `requirements.txt` com faixa de versão e só com
  justificativa.

## Documentação

- Ao mudar comportamento, comando ou variável de ambiente, atualize o
  `README.md` no mesmo commit.
