"""Montagem do Alfred para a Cloudflare (Worker + Durable Object).

Equivale ao `main.py`, mas sem processo contínuo: o Telegram chega por webhook
(`processar_webhook`) e o agendamento roda pelo Cron Trigger (`executar_cron`).
Só usa o provedor Claude; o Ollama continua restrito ao modo local.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agendador.briefing import Briefing
from src.agendador.resumo import ResumoSemanal
from src.agendador.tarefas import TarefasAgendadas
from src.agente.agente import Agente
from src.agente.fabrica import criar_provedor
from src.config import Configuracao, ErroConfiguracao
from src.ferramentas.registro import criar_registro
from src.interfaces.telegram_api import (
    ClienteTelegram,
    NotificadorTelegramHttp,
    proteger_logs,
)
from src.interfaces.telegram_webhook import InterfaceTelegramWebhook
from src.memoria.banco import Banco


@dataclass
class AppNuvem:
    banco: Banco
    agente: Agente
    tarefas: TarefasAgendadas
    telegram: InterfaceTelegramWebhook

    def processar_webhook(self, update: Any, segredo: str | None) -> int:
        return self.telegram.processar(update, segredo)

    def executar_cron(self) -> None:
        self.tarefas.executar_ciclo(
            iniciar_checkin=lambda: self.telegram.iniciar_checkin(manual=False)
        )


def validar_configuracao_nuvem(configuracao: Configuracao) -> None:
    if configuracao.provedor != "claude":
        raise ErroConfiguracao(
            "Na Cloudflare só PROVEDOR=claude é suportado (o Ollama é local)."
        )
    configuracao.validar_provedor()
    faltando = [
        nome
        for nome, valor in (
            ("TELEGRAM_BOT_TOKEN", configuracao.telegram_bot_token.get_secret_value()),
            (
                "TELEGRAM_WEBHOOK_SEGREDO",
                configuracao.telegram_webhook_segredo.get_secret_value(),
            ),
            (
                "TELEGRAM_ID_AUTORIZADO",
                ""
                if configuracao.telegram_id_autorizado is None
                else str(configuracao.telegram_id_autorizado),
            ),
        )
        if not valor.strip()
    ]
    if faltando:
        raise ErroConfiguracao(
            "Configuração da Cloudflare incompleta: defina "
            + ", ".join(faltando)
            + "."
        )


def montar_app(
    ambiente: Mapping[str, str],
    conectar: Callable[[], Any],
    http_telegram: Any | None = None,
) -> AppNuvem:
    """Monta o Alfred sobre a conexão do Durable Object (`conectar`)."""
    configuracao = Configuracao.carregar(ambiente)
    validar_configuracao_nuvem(configuracao)
    token = configuracao.telegram_bot_token.get_secret_value()
    chat_id = configuracao.telegram_id_autorizado
    assert chat_id is not None
    proteger_logs(token)

    banco = Banco(
        Path("durable-object"),
        fuso_horario=configuracao.fuso_horario,
        conectar=conectar,
    )
    provedor = criar_provedor(configuracao, banco)
    agente = Agente(provedor, banco, criar_registro(banco), pendencias_no_banco=True)
    cliente = ClienteTelegram(token, http=http_telegram)
    tarefas = TarefasAgendadas(
        banco,
        configuracao.intervalo_lembretes_segundos,
        Briefing(
            banco,
            provedor,
            clima_ativo=configuracao.clima_ativo,
            cidade=configuracao.cidade,
        ),
        notificador=NotificadorTelegramHttp(cliente, chat_id),
        horario_briefing=configuracao.horario_briefing,
        fuso_horario=configuracao.fuso_horario,
        briefing_ativo=configuracao.briefing_ativo,
        horario_checkin=configuracao.horario_checkin,
        checkin_ativo=configuracao.checkin_ativo,
        resumo=ResumoSemanal(banco, provedor),
        dia_resumo=configuracao.dia_resumo,
        horario_resumo=configuracao.horario_resumo,
    )
    telegram = InterfaceTelegramWebhook(
        agente,
        tarefas,
        banco,
        cliente,
        chat_id,
        configuracao.telegram_webhook_segredo.get_secret_value(),
    )
    return AppNuvem(banco, agente, tarefas, telegram)
