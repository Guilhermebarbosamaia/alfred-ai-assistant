# CLAUDE.md

Assistente pessoal em Python 3.12 (terminal e Telegram) que usa um modelo pela
API do Claude (padrão, `PROVEDOR=claude`) ou pelo Ollama local
(`PROVEDOR=ollama`). Veja o `README.md` para instalação, comandos e variáveis.

## Idioma

- Responda ao usuário em português do Brasil.
- Código, identificadores, docstrings, comentários, mensagens ao usuário e
  logs em português (ex.: `Configuracao`, `salvar_nota`, `testes/`).
- Mensagens de commit seguem o padrão já usado no histórico: em inglês, curtas,
  no imperativo (ex.: `Limit model history by token budget`).

## Testes

- Rode com `pytest` na raiz (usa `pytest.ini`; testes ficam em `testes/`).
- Testes **nunca** acessam a rede: nada de API do Claude, Ollama real, API do
  Telegram, Open-Meteo ou qualquer outro serviço, e nunca usam chave real. Use
  o provedor falso, `SimpleNamespace` ou substitua o cliente
  (`provedor._cliente.chat = ...` no Ollama, `provedor._cliente =
  SimpleNamespace(messages=...)` no Claude), como em `testes/test_provedor.py`
  e `testes/test_provedor_claude.py`.
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
- Tokens e segredos (incluindo `ANTHROPIC_API_KEY`) nunca aparecem em logs nem
  em mensagens de erro; o `FiltroToken` de `src/interfaces/telegram.py` existe
  para isso e deve continuar aplicado.
- Ao mexer em logs, adicione teste com `caplog` verificando que o conteúdo
  **não** aparece (padrão de `test_chamada_registra_metricas_sem_conteudo_da_resposta`).

## Segredos e arquivos que não vão para o git

- Nunca commite o `.env` nem o leia ou exiba sem necessidade. Variáveis novas
  vão para `.env.example` (sem valores reais), `src/config.py` e o `README.md`.
- Nunca commite `dados/` (banco SQLite e `dados/backup/`), `.venv/` ou caches.
- Antes de commitar, confira `git status` e adicione arquivos pelo nome, não
  com `git add -A`/`git add .`.

## Arquitetura e regras do código

- Serviços externos: a aplicação só se conecta ao provedor escolhido em
  `PROVEDOR` (API do Claude ou Ollama local), à API do Telegram (quando ativa)
  e ao Open-Meteo (quando `CLIMA_ATIVO=true`). Não adicione outros serviços
  externos sem pedir.
- Provedores ficam em `src/agente/` (`provedor_claude.py`, `provedor.py`) e
  são montados em `src/agente/fabrica.py`; só o provedor escolhido é
  instanciado (com o Claude, `ollama` não é importado). Erros de provedor
  herdam de `ErroProvedor` (`src/agente/erros.py`) e o código captura essa
  base, nunca a classe de um provedor específico.
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
- Respeite os limites de geração (`CLAUDE_MAX_TOKENS`, `OLLAMA_NUM_PREDICT`) e
  de contexto (`OLLAMA_NUM_CTX`, orçamento do histórico) e evite prompts
  grandes: o Ollama roda em máquinas com 8 GB de RAM e o Claude cobra por token.
- Dependências novas vão para `requirements.txt` com faixa de versão e só com
  justificativa.

## Documentação

- Ao mudar comportamento, comando ou variável de ambiente, atualize o
  `README.md` no mesmo commit.
