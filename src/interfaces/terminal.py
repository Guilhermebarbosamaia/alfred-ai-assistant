from src.agendador.tarefas import TarefasAgendadas
from src.agente.agente import Agente


def executar_terminal(
    agente: Agente,
    tarefas: TarefasAgendadas,
    iniciar_tarefas: bool = True,
) -> None:
    print(
        "Assistente local pronto. Digite 'ajuda' para ver os comandos."
    )
    try:
        if iniciar_tarefas:
            tarefas.iniciar()
        if tarefas.briefing_pendente:
            try:
                resposta = input(
                    "O briefing de hoje ainda não foi enviado. Enviar agora? (s/N): "
                ).strip().lower()
            except EOFError:
                resposta = ""
            if resposta in {"s", "sim"}:
                tarefas.executar_briefing()
        while True:
            try:
                pedido = input("Você: ").strip()
            except EOFError:
                break
            if pedido.lower() in {"sair", "fim"}:
                break
            if not pedido:
                continue
            partes = pedido.split()
            comando = partes[0].lower()
            if comando == "briefing" and len(partes) == 1:
                tarefas.executar_briefing()
                continue
            if comando == "checkin" and len(partes) == 1:
                def responder_checkin(pergunta: str) -> str:
                    try:
                        return input(f"{pergunta} ")
                    except EOFError:
                        return ""

                print(
                    tarefas.executar_checkin(
                        manual=True, responder=responder_checkin
                    )
                )
                continue
            if comando == "resumo" and len(partes) == 1:
                tarefas.executar_resumo()
                continue
            if comando == "folga":
                try:
                    if len(partes) != 2:
                        raise ValueError
                    habito_id = int(partes[1])
                    if habito_id < 1:
                        raise ValueError
                except ValueError:
                    print("Uso: folga <id numérico do hábito>")
                else:
                    print(agente.marcar_folga(habito_id))
                continue
            if comando == "ajuda":
                print(
                    "Comandos: anota: <texto>; me lembra de <texto> <data/hora>; "
                    "perguntar sobre uma nota; briefing; checkin; resumo; "
                    "hábitos por conversa; folga <id do hábito>; lembretes "
                    "[pendentes|concluídos|atrasados]; concluir <id>; apagar "
                    "nota <id>; apagar lembrete <id>; notas; dados ou --ver-dados; "
                    "ajuda; sair/fim."
                )
                continue
            if comando == "lembretes":
                estado = partes[1].lower() if len(partes) > 1 else "pendentes"
                if estado == "concluídos":
                    estado = "concluidos"
                if len(partes) > 2 or estado not in {
                    "pendentes",
                    "concluidos",
                    "atrasados",
                }:
                    print("Uso: lembretes [pendentes|concluídos|atrasados]")
                else:
                    print(agente.listar_lembretes(estado))
                continue
            if comando == "notas" and len(partes) == 1:
                print(agente.listar_notas())
                continue
            if comando == "concluir":
                try:
                    if len(partes) != 2:
                        raise ValueError
                    print(agente.concluir_lembrete(int(partes[1])))
                except ValueError:
                    print("Uso: concluir <id numérico>")
                continue
            if comando == "apagar":
                if len(partes) != 3 or partes[1].lower() not in {
                    "nota",
                    "lembrete",
                }:
                    print("Uso: apagar nota <id> ou apagar lembrete <id>")
                    continue
                try:
                    registro_id = int(partes[2])
                    if registro_id < 1:
                        raise ValueError
                except ValueError:
                    print("O id deve ser um número positivo.")
                    continue
                confirmar = input(
                    f"Apagar {partes[1]} #{registro_id}? (sim/não): "
                ).strip().lower()
                if confirmar in {"sim", "s"}:
                    print(
                        agente.excluir_registro(
                            partes[1].lower(), registro_id, confirmado=True
                        )
                    )
                else:
                    print("Ação cancelada.")
                continue
            if comando in {"dados", "--ver-dados"} and len(partes) == 1:
                print(agente.visualizar_dados())
                continue
            print(f"Assistente: {agente.processar(pedido)}")
    except KeyboardInterrupt:
        print("\nAté logo!")
    finally:
        tarefas.parar()
