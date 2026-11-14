from src.interfaces.terminal import executar_terminal


class AgenteFalso:
    def processar(self, pedido: str) -> str:
        raise AssertionError(f"O pedido {pedido!r} não deveria ir ao agente.")

    def listar_lembretes(self, estado: str) -> str:
        return f"lembretes {estado}"

    def concluir_lembrete(self, lembrete_id: int) -> str:
        return f"concluído {lembrete_id}"

    def marcar_folga(self, habito_id: int) -> str:
        return f"folga {habito_id}"

    def excluir_registro(
        self, tipo: str, registro_id: int, confirmado: bool = False
    ) -> str:
        return f"apagado {tipo} {registro_id} confirmado={confirmado}"

    def visualizar_dados(self) -> str:
        return "dados"


class TarefasFalsas:
    def __init__(self) -> None:
        self.briefings = 0
        self.iniciada = False
        self.parada = False
        self.briefing_pendente = False
        self.checkins = 0
        self.resumos = 0

    def iniciar(self) -> None:
        self.iniciada = True

    def executar_briefing(self) -> str:
        self.briefings += 1
        return "Resumo do dia."

    def executar_checkin(self, manual=True, responder=None) -> str:
        self.checkins += 1
        return "Perguntas de check-in."

    def executar_resumo(self) -> str:
        self.resumos += 1
        return "Resumo semanal."

    def parar(self) -> None:
        self.parada = True


def test_comando_manual_briefing_no_terminal(monkeypatch) -> None:
    pedidos = iter(["Briefing", "sair"])
    tarefas = TarefasFalsas()
    monkeypatch.setattr("builtins.input", lambda _: next(pedidos))

    executar_terminal(AgenteFalso(), tarefas)

    assert tarefas.iniciada
    assert tarefas.briefings == 1
    assert tarefas.parada


def test_comandos_de_ajuda_dados_e_gestao_no_terminal(monkeypatch, capsys) -> None:
    pedidos = iter(
        [
            "ajuda",
            "lembretes atrasados",
            "concluir 12",
            "dados",
            "apagar nota 3",
            "sim",
            "sair",
        ]
    )
    tarefas = TarefasFalsas()
    monkeypatch.setattr("builtins.input", lambda _: next(pedidos))

    executar_terminal(AgenteFalso(), tarefas)

    saida = capsys.readouterr().out
    assert "Comandos:" in saida
    assert "lembretes atrasados" in saida
    assert "concluído 12" in saida
    assert "dados" in saida
    assert "apagado nota 3 confirmado=True" in saida


def test_comandos_de_habitos_no_terminal(monkeypatch, capsys) -> None:
    pedidos = iter(["checkin", "resumo", "folga 2", "ajuda", "sair"])
    tarefas = TarefasFalsas()
    monkeypatch.setattr("builtins.input", lambda _: next(pedidos))

    executar_terminal(AgenteFalso(), tarefas)

    saida = capsys.readouterr().out
    assert tarefas.checkins == 1
    assert tarefas.resumos == 1
    assert "folga 2" in saida
    assert "checkin" in saida
    assert "resumo" in saida
