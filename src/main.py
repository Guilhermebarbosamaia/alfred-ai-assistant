import argparse

from src.agendador.briefing import Briefing
from src.agendador.notificador import (
    Notificador,
    NotificadorCombinado,
    NotificadorTelegram,
    NotificadorTerminal,
)
from src.agendador.resumo import ResumoSemanal
from src.agendador.tarefas import TarefasAgendadas
from src.agente.agente import Agente
from src.agente.fabrica import criar_provedor
from src.config import Configuracao, ErroConfiguracao
from src.ferramentas.registro import criar_registro
from src.interfaces.terminal import executar_terminal
from src.interfaces.telegram import InterfaceTelegram
from src.memoria.banco import Banco


def main(argumentos: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Assistente pessoal local")
    parser.add_argument(
        "--interface",
        choices=("terminal", "telegram", "ambas"),
        help="Interface de execução (sobrescreve INTERFACE no .env).",
    )
    opcoes = parser.parse_args(argumentos)
    configuracao = Configuracao.carregar()
    interface = opcoes.interface or configuracao.interface
    telegram_token = configuracao.telegram_bot_token.get_secret_value()
    ativar_telegram = interface in {"telegram", "ambas"}
    if ativar_telegram and configuracao.telegram_id_autorizado is None:
        print(
            "Telegram não iniciado: defina TELEGRAM_ID_AUTORIZADO "
            "com o ID numérico do chat permitido."
        )
        if interface == "telegram":
            return
        interface = "terminal"
        ativar_telegram = False
    if ativar_telegram and not telegram_token:
        print(
            "Telegram não iniciado: defina TELEGRAM_BOT_TOKEN no arquivo .env."
        )
        if interface == "telegram":
            return
        interface = "terminal"
        ativar_telegram = False

    try:
        configuracao.validar_provedor()
    except ErroConfiguracao as erro:
        print(f"Alfred não iniciado: {erro}")
        raise SystemExit(1) from None
    banco = Banco(
        configuracao.caminho_banco,
        fuso_horario=configuracao.fuso_horario,
    )
    provedor = criar_provedor(configuracao, banco)
    agente = Agente(provedor, banco, criar_registro(banco))
    notificador_telegram = (
        NotificadorTelegram(configuracao.telegram_id_autorizado)
        if ativar_telegram and configuracao.telegram_id_autorizado is not None
        else None
    )
    notificadores: list[Notificador] = []
    if interface in {"terminal", "ambas"}:
        notificadores.append(NotificadorTerminal())
    if notificador_telegram is not None:
        notificadores.append(notificador_telegram)
    notificador = (
        notificadores[0]
        if len(notificadores) == 1
        else NotificadorCombinado(notificadores)
    )
    tarefas = TarefasAgendadas(
        banco,
        configuracao.intervalo_lembretes_segundos,
        Briefing(
            banco,
            provedor,
            clima_ativo=configuracao.clima_ativo,
            cidade=configuracao.cidade,
        ),
        notificador=notificador,
        horario_briefing=configuracao.horario_briefing,
        fuso_horario=configuracao.fuso_horario,
        briefing_ativo=configuracao.briefing_ativo,
        horario_checkin=configuracao.horario_checkin,
        checkin_ativo=configuracao.checkin_ativo,
        resumo=ResumoSemanal(banco, provedor),
        dia_resumo=configuracao.dia_resumo,
        horario_resumo=configuracao.horario_resumo,
    )
    if not ativar_telegram:
        executar_terminal(agente, tarefas)
        return

    if notificador_telegram is None or configuracao.telegram_id_autorizado is None:
        raise RuntimeError("A configuração do Telegram está incompleta.")
    telegram = InterfaceTelegram(
        agente,
        tarefas,
        telegram_token,
        configuracao.telegram_id_autorizado,
        notificador_telegram,
    )
    tarefas.definir_responder_checkin(telegram.responder_checkin)
    if interface == "telegram":
        telegram.executar()
        return

    thread = telegram.iniciar_em_thread()
    try:
        executar_terminal(agente, tarefas, iniciar_tarefas=False)
    finally:
        telegram.parar()
        thread.join(timeout=15)


if __name__ == "__main__":
    main()
