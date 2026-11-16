# Assistente pessoal local

Assistente em português executado no terminal e/ou pelo Telegram. O modelo faz a extração
de notas e lembretes e responde a conversas e buscas nas notas; SQLite,
horário e disparo dos lembretes ficam sob controle do código. O provedor do
modelo é escolhido em `PROVEDOR`: `claude` (padrão, API do Claude) ou `ollama`
(servidor Ollama local). Além dele, a aplicação só se conecta à API do Telegram
(quando ativa) e ao Open-Meteo (quando `CLIMA_ATIVO=true`).

## Requisitos

- Linux e Python 3.12
- Com `PROVEDOR=claude`: uma chave da API do Claude (crie em
  [console.anthropic.com](https://console.anthropic.com)) e acesso à internet
- Com `PROVEDOR=ollama`: Ollama em execução em `http://localhost:11434` e
  8 GB de RAM; o modelo padrão é `llama3.2:3b`

## Provedor Claude (padrão)

Defina no `.env`:

```bash
PROVEDOR=claude
ANTHROPIC_API_KEY=sua-chave
```

Sem `ANTHROPIC_API_KEY`, o Alfred não inicia e explica o que configurar. As
demais variáveis são opcionais: `MODELO_CLAUDE` (padrão `claude-haiku-5-5`),
`TIMEOUT_CLAUDE` (segundos, padrão 60) e `CLAUDE_MAX_TOKENS` (limite de
geração, padrão 256). As ações são extraídas com structured outputs da API
(JSON Schema derivado dos modelos Pydantic, sem as restrições que a API não
aceita); a validação do Pydantic continua decidindo antes de qualquer ação. Os
logs registram apenas tokens de entrada e saída, duração e o motivo de parada,
nunca o conteúdo nem a chave.

O Alfred conta os tokens (entrada + saída) de cada chamada ao Claude no banco
e para de chamar a API quando atinge `CLAUDE_LIMITE_TOKENS_DIA` (padrão
200000) ou `CLAUDE_LIMITE_TOKENS_MES` (padrão 3000000); use `0` para desativar
um deles. Dia e mês seguem `FUSO_HORARIO`. Com o limite atingido, o assistente
avisa na conversa e o briefing e o resumo usam o texto fixo. O limite é
verificado antes de cada chamada, então a última chamada permitida pode passar
um pouco do teto (no máximo uma resposta). Como garantia extra, defina também
um limite de gasto da conta em
[console.anthropic.com](https://console.anthropic.com).

## Provedor Ollama (opcional)

Com `PROVEDOR=claude`, o pacote `ollama` não é importado e o servidor local
não é necessário. Para usar o modelo local, defina `PROVEDOR=ollama`, instale o
Ollama seguindo as instruções oficiais em
[ollama.com/download](https://ollama.com/download) e baixe o modelo:

```bash
ollama pull llama3.2:3b
```

Para usar a alternativa menor, baixe `qwen2.5:3b` e defina
`MODELO_OLLAMA=qwen2.5:3b` no arquivo `.env`.

## Instalação e execução

Na raiz do projeto:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m src.main
```

O arquivo `.env` permite escolher o provedor e ajustar o modelo, o endereço
local do Ollama e os timeouts. Com o Ollama, as ações são extraídas com o JSON
Schema enviado pelo parâmetro `format`; com qualquer provedor, a aplicação
valida a resposta antes de executar qualquer ação.
Saudações e frases curtas de conversa casual usam uma rota direta em streaming,
sem esquema de extração. Mensagens fora dessa lista conservadora continuam no
fluxo estruturado de ações. O histórico enviado ao modelo é limitado a cerca de
2048 tokens estimados, mantendo sempre a mensagem atual.
Com o Ollama, `OLLAMA_KEEP_ALIVE` mantém o modelo carregado por 30 minutos após o último uso
(padrão), evitando que a primeira mensagem após alguns minutos de inatividade
tenha de carregá-lo novamente. `OLLAMA_NUM_CTX` limita o contexto (padrão 4096)
e `OLLAMA_NUM_PREDICT` limita a geração (padrão 256 tokens). Também é possível
ajustar o caminho do banco, frequência de verificação, fuso (`FUSO_HORARIO`, padrão
`America/Araguaina`), briefing (`BRIEFING_HORA`, `BRIEFING_ATIVO`) e clima
(`CLIMA_ATIVO`, `CIDADE`). O check-in noturno usa `CHECKIN_HORA` e
`CHECKIN_ATIVO`; o resumo semanal usa `RESUMO_DIA` (nome do dia em português,
padrão domingo) e `RESUMO_HORA`. O clima consulta a API pública Open-Meteo e é
omitido quando indisponível. Migrações versionadas criam uma cópia em
`dados/backup/` antes de alterar um banco existente. Datas são armazenadas em
UTC e exibidas no fuso local. As notas existentes são reindexadas para buscas
sem diferenciar acentos ou maiúsculas.

## Interface Telegram

1. No Telegram, converse com `@BotFather`, envie `/newbot` e siga as instruções.
   Copie o token fornecido e mantenha-o privado.
2. Para descobrir o ID numérico do seu chat, converse com `@userinfobot` e use
   o valor de `Id` informado.
3. Preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_ID_AUTORIZADO` no `.env`.
   Mensagens de outros chats são ignoradas.
4. Defina `INTERFACE=telegram` para usar somente o bot ou `INTERFACE=ambas`
   para usar Telegram e terminal. Também é possível escolher na execução:

```bash
python -m src.main --interface telegram
python -m src.main --interface ambas
```

O bot usa long polling e não precisa de webhook nem servidor público. Os
comandos `/start`, `/ajuda`, `/briefing`, `/checkin`, `/resumo`, `/lembretes`,
`/notas` e `/dados` são registrados no BotFather ao iniciar. Ferramentas
sensíveis pedem confirmação por botão ou com `sim`/`não`; a confirmação expira
em dois minutos. Fotos, áudios e arquivos ainda não são aceitos.

## Uso

- `anota: comprar cabo USB` salva uma nota.
- `me lembra de ligar para o João amanhã às 10h` cria um lembrete.
- `briefing` mostra o resumo do dia imediatamente.
- O briefing diário é enviado no horário configurado em `BRIEFING_HORA`; se
  estiver atrasado ao iniciar, o assistente oferece enviá-lo.
- Cadastre hábitos conversando com o assistente, informando nome e meta semanal
  de 1 a 7 dias; também é possível pedir para listar, desativar ou ver o
  progresso. São permitidos até cinco hábitos ativos.
- Registre uma atividade com frases como `treinei hoje` ou `estudei 1h`. O
  assistente pede esclarecimento quando não consegue identificar o hábito com
  segurança. Um hábito só pode ter um registro por dia.
- `folga <id do hábito>` marca hoje como folga, preservando a sequência sem
  contar como dia feito.
- O check-in pergunta sobre cada hábito ativo no horário de `CHECKIN_HORA`.
  `checkin` inicia o fluxo manual; responda sim, não ou com uma quantidade.
  Check-ins sem resposta são marcados como pulados no fim do dia.
- O resumo semanal é enviado em `RESUMO_DIA` e `RESUMO_HORA`; `resumo` o gera
  manualmente. Os números são calculados pelo código e o modelo apenas redige
  o texto; se o provedor falhar, é usado um resumo fixo.
- Pergunte sobre uma informação salva para buscar nas notas.
- `lembretes [pendentes|concluidos|atrasados]` lista lembretes.
- `concluir <id>` conclui um lembrete.
- `apagar nota <id>` e `apagar lembrete <id>` pedem confirmação antes de apagar.
- No Telegram, envie `apagar nota <id>` ou `apagar lembrete <id>` e confirme
  pela tecla ou respondendo `sim`/`não`.
- `dados` ou `--ver-dados` mostra contagens e registros recentes.
- `notas` lista as notas recentes.
- `ajuda` lista os comandos.
- `que horas são?` informa o horário local.
- Lembretes vencidos são exibidos no terminal enquanto o assistente está
  aberto. Digite `sair` ou `fim` para encerrar.

Se o modelo retornar dados inválidos, o assistente tenta novamente até duas
vezes e então informa que não conseguiu interpretar o pedido. Os testes usam
um provedor falso e não precisam do Ollama nem da API do Claude:

```bash
pytest
```

Para medir carregamento frio e resposta aquecida com o mesmo pedido em
português, execute:

```bash
python -m scripts.benchmark_ollama
```

O benchmark descarrega o modelo antes da chamada fria e imprime os tempos e
contagens fornecidos pelo Ollama, sem exibir a resposta. Para comparar versões,
use o mesmo modelo, hardware, pedido, `OLLAMA_NUM_CTX` e `OLLAMA_NUM_PREDICT`.
O timeout do benchmark é 600 segundos por chamada; ajuste com `--timeout` se
necessário.
Compare `load_duration_ms` para o custo de carregamento e
`eval_count`/`eval_duration_ms` para a geração; nos logs do assistente, também
compare `fila_ms` e `tentativas` para separar espera e retries.

Os testes adicionais cobrem migrações, cópia de segurança, índice FTS5, datas
relativas e execução idempotente do agendador. A integração Telegram usa
`python-telegram-bot` e os testes não acessam a rede, o Ollama nem a API do
Claude.
