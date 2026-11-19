"""Ponto de entrada do Alfred na Cloudflare.

- `Default.fetch`: aceita só `POST /telegram` (webhook) e repassa ao Durable
  Object.
- `Default.scheduled`: o Cron Trigger pede uma rodada de agendamento.
- `Alfred`: Durable Object único que guarda o SQLite e processa uma
  requisição por vez.
"""

import asyncio
import logging
from urllib.parse import urlparse

from workers import DurableObject, Response, WorkerEntrypoint

NOME_OBJETO = "alfred"
CAMINHO_WEBHOOK = "/telegram"
URL_CRON = "https://alfred.interno/cron"

VARIAVEIS = (
    "PROVEDOR",
    "ANTHROPIC_API_KEY",
    "MODELO_CLAUDE",
    "TIMEOUT_CLAUDE",
    "CLAUDE_MAX_TOKENS",
    "CLAUDE_LIMITE_TOKENS_DIA",
    "CLAUDE_LIMITE_TOKENS_MES",
    "BRIEFING_HORA",
    "BRIEFING_ATIVO",
    "CHECKIN_HORA",
    "CHECKIN_ATIVO",
    "RESUMO_DIA",
    "RESUMO_HORA",
    "CLIMA_ATIVO",
    "CIDADE",
    "FUSO_HORARIO",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ID_AUTORIZADO",
    "TELEGRAM_WEBHOOK_SEGREDO",
)


def _ambiente(env: object) -> dict[str, str]:
    valores: dict[str, str] = {}
    for nome in VARIAVEIS:
        valor = getattr(env, nome, None)
        if valor is not None and str(valor) not in {"undefined", "null"}:
            valores[nome] = str(valor)
    return valores


class Alfred(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self._app = None
        self._trava = asyncio.Lock()

    def _obter_app(self):
        if self._app is None:
            from src.memoria.conexao_do import ConexaoDO
            from src.nuvem import montar_app

            sql = self.ctx.storage.sql
            self._app = montar_app(_ambiente(self.env), lambda: ConexaoDO(sql))
        return self._app

    async def fetch(self, request):
        # Uma requisição por vez: as chamadas ao Claude e ao Telegram liberam
        # o Durable Object para outros eventos enquanto esperam a rede.
        async with self._trava:
            try:
                app = self._obter_app()
            except Exception as erro:
                logging.error("Alfred não iniciado: %s", type(erro).__name__)
                return Response("Configuração inválida.", status=500)
            if urlparse(str(request.url)).path == "/cron":
                app.executar_cron()
                return Response("", status=204)
            try:
                update = await request.json()
            except Exception:
                return Response("JSON inválido.", status=400)
            segredo = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            status = app.processar_webhook(update, segredo)
            return Response("", status=status)


class Default(WorkerEntrypoint):
    def _objeto(self):
        return self.env.ALFRED.getByName(NOME_OBJETO)

    async def fetch(self, request):
        caminho = urlparse(str(request.url)).path
        if request.method != "POST" or caminho != CAMINHO_WEBHOOK:
            return Response("Não encontrado.", status=404)
        return await self._objeto().fetch(request)

    async def scheduled(self, controller, env=None, ctx=None):
        await self._objeto().fetch(URL_CRON, method="POST")
