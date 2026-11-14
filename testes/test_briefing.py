import json
from datetime import datetime, time, timedelta

from src.config import Configuracao
from src.agendador.briefing import Briefing
from src.agendador.notificador import NotificadorTerminal
from src.agendador.tarefas import TarefasAgendadas
from src.agente.provedor import ErroProvedorOllama
from src.memoria.banco import Banco


class ProvedorFalso:
    def __init__(self, resposta: str | Exception) -> None:
        self.resposta = resposta
        self.chamadas = 0
        self.mensagens: list[dict[str, str]] = []

    def gerar(
        self, mensagens: list[dict[str, str]], _: dict[str, object]
    ) -> str:
        self.chamadas += 1
        self.mensagens = mensagens
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


class NotificadorFalso:
    def __init__(self) -> None:
        self.mensagens: list[str] = []

    def notificar(self, texto: str) -> None:
        self.mensagens.append(texto)


def test_horario_do_briefing_e_configuravel_por_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("BRIEFING_HORA", "07:15")
    monkeypatch.setenv("BRIEFING_ATIVO", "false")
    monkeypatch.setenv("CLIMA_ATIVO", "true")
    monkeypatch.setenv("FUSO_HORARIO", "America/Araguaina")
    monkeypatch.setenv("CHECKIN_HORA", "21:30")
    monkeypatch.setenv("CHECKIN_ATIVO", "false")
    monkeypatch.setenv("RESUMO_DIA", "sábado")
    monkeypatch.setenv("RESUMO_HORA", "19:45")

    configuracao = Configuracao.carregar()

    assert configuracao.horario_briefing == time(7, 15)
    assert not configuracao.briefing_ativo
    assert configuracao.clima_ativo
    assert configuracao.fuso_horario == "America/Araguaina"
    assert configuracao.horario_checkin == time(21, 30)
    assert not configuracao.checkin_ativo
    assert configuracao.dia_resumo == "sat"
    assert configuracao.horario_resumo == time(19, 45)


def test_fallback_do_briefing_usa_dados_coletados_pelo_codigo(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    agora = datetime.now().astimezone().replace(
        hour=9, minute=30, second=0, microsecond=0
    )
    banco.criar_lembrete("pagar a conta", agora - timedelta(days=2))
    banco.criar_lembrete(
        "ligar para Ana", agora.replace(hour=14)
    )
    provedor = ProvedorFalso(ErroProvedorOllama("indisponível"))

    texto = Briefing(banco, provedor).gerar(agora)

    assert f"{agora:%d/%m/%Y}" in texto
    assert "pagar a conta" in texto
    assert "ligar para Ana" in texto
    assert "Pendências atrasadas" in texto
    assert provedor.chamadas == 1


def test_clima_offline_e_omitido_sem_afetar_briefing(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    provedor = ProvedorFalso(json.dumps({"texto": "Bom dia."}))
    briefing = Briefing(
        banco,
        provedor,
        clima_ativo=True,
        cidade="Araguaína",
        obter_clima_configurado=lambda _: None,
    )

    assert briefing.gerar() == "Bom dia."
    dados = json.loads(provedor.mensagens[1]["content"])
    assert "clima" not in dados


def test_falha_do_clima_nao_interrompe_o_briefing(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    provedor = ProvedorFalso(json.dumps({"texto": "Bom dia."}))

    def falhar_clima(_: str) -> str | None:
        raise RuntimeError("serviço indisponível")

    briefing = Briefing(
        banco,
        provedor,
        clima_ativo=True,
        cidade="Araguaína",
        obter_clima_configurado=falhar_clima,
    )

    assert briefing.gerar() == "Bom dia."
    assert "clima" not in json.loads(provedor.mensagens[1]["content"])


def test_briefing_agendado_usa_fallback_se_ollama_prioriza_conversa(tmp_path) -> None:
    class ProvedorOcupado(ProvedorFalso):
        def gerar_agendado(
            self, _mensagens: list[dict[str, str]], _esquema: dict[str, object]
        ) -> str:
            raise ErroProvedorOllama("conversa aguardando")

    banco = Banco(tmp_path / "assistente.db")
    provedor = ProvedorOcupado(json.dumps({"texto": "não deveria ser usado"}))

    texto = Briefing(banco, provedor).gerar(agendado=True)

    assert "Briefing de" in texto
    assert provedor.chamadas == 0


def test_briefing_usa_resposta_curta_do_provedor(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    provedor = ProvedorFalso(json.dumps({"texto": "Você tem um compromisso hoje."}))

    texto = Briefing(banco, provedor).gerar()

    assert texto == "Você tem um compromisso hoje."
    assert provedor.chamadas == 1
    dados = json.loads(provedor.mensagens[1]["content"])
    assert {
        "agora",
        "lembretes_do_dia",
        "mais_lembretes",
        "pendencias_atrasadas",
        "mais_pendencias",
        "habitos_do_dia",
    } == set(dados)


def test_briefing_inclui_habitos_e_respeita_seis_linhas(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    habito_id = banco.criar_habito("Caminhar", "", 4)
    banco.registrar_habito(habito_id, True)
    provedor = ProvedorFalso(
        json.dumps({"texto": "Linha 1\nLinha 2\nLinha 3\nLinha 4\nLinha 5\nLinha 6"})
    )

    texto = Briefing(banco, provedor).gerar()

    assert "Hábitos do dia: Caminhar (sequência 1 dia(s))" in texto
    assert len(texto.splitlines()) == 6


def test_agendador_dispara_briefing_diario_com_provedor_falso(tmp_path) -> None:
    from zoneinfo import ZoneInfo

    agora = datetime(2026, 11, 14, 6, 0, tzinfo=ZoneInfo("America/Araguaina"))
    banco = Banco(tmp_path / "assistente.db", relogio=lambda: agora)
    provedor = ProvedorFalso(json.dumps({"texto": "Resumo do dia."}))
    notificador = NotificadorFalso()
    tarefas = TarefasAgendadas(
        banco,
        60,
        Briefing(banco, provedor),
        notificador,
        horario_briefing=time(7, 15),
        relogio=lambda: agora,
    )

    tarefas.iniciar()
    try:
        trabalho = tarefas._agendador.get_job("briefing_diario")
        assert trabalho is not None
        assert trabalho.max_instances == 1
        campos = {campo.name: str(campo) for campo in trabalho.trigger.fields}
        assert campos["hour"] == "7"
        assert campos["minute"] == "15"
        assert trabalho.trigger.timezone == banco.fuso
        verificacao = tarefas._agendador.get_job("verificar_lembretes")
        assert verificacao is not None
        assert verificacao.trigger.interval.total_seconds() == 60

        trabalho.func(*trabalho.args)

        assert provedor.chamadas == 1
        assert notificador.mensagens == ["BRIEFING: Resumo do dia."]
        assert banco.execucao_agendador("briefing", "2026-11-14")["status"] == (
            "enviado"
        )
        assert trabalho.func(*trabalho.args) == "O briefing de hoje já foi executado."
        assert provedor.chamadas == 1
    finally:
        tarefas.parar()

    notificacoes_reinicio = NotificadorFalso()
    reinicio = TarefasAgendadas(
        banco,
        60,
        Briefing(banco, provedor),
        notificacoes_reinicio,
        horario_briefing=time(7, 15),
        relogio=lambda: agora,
    )
    reinicio.iniciar()
    try:
        assert not reinicio.briefing_pendente
        job_reinicio = reinicio._agendador.get_job("briefing_diario")
        assert job_reinicio is not None
        assert job_reinicio.func(*job_reinicio.args) == (
            "O briefing de hoje já foi executado."
        )
        assert provedor.chamadas == 1
        assert notificacoes_reinicio.mensagens == []
    finally:
        reinicio.parar()


def test_agendador_oferece_briefing_atrasado_ao_iniciar(tmp_path) -> None:
    from zoneinfo import ZoneInfo

    agora = datetime(2026, 11, 14, 9, 0, tzinfo=ZoneInfo("America/Araguaina"))
    banco = Banco(tmp_path / "assistente.db", relogio=lambda: agora)
    notificador = NotificadorFalso()
    tarefas = TarefasAgendadas(
        banco,
        60,
        Briefing(banco, ProvedorFalso(json.dumps({"texto": "Bom dia."}))),
        notificador,
        horario_briefing=time(7, 15),
        relogio=lambda: agora,
    )

    tarefas.iniciar()
    try:
        assert tarefas.briefing_pendente
        assert "pendente" in notificador.mensagens[0]
    finally:
        tarefas.parar()


def test_lembrete_vencido_ao_abrir_e_notificado_uma_vez(tmp_path) -> None:
    from zoneinfo import ZoneInfo

    agora = datetime(2026, 11, 14, 9, 0, tzinfo=ZoneInfo("America/Araguaina"))
    banco = Banco(tmp_path / "assistente.db", relogio=lambda: agora)
    banco.criar_lembrete("buscar encomenda", agora.replace(hour=8))
    primeiro_notificador = NotificadorFalso()
    primeiro = TarefasAgendadas(
        banco, 60, notificador=primeiro_notificador, relogio=lambda: agora
    )

    primeiro.iniciar()
    primeiro.parar()
    segundo_notificador = NotificadorFalso()
    segundo = TarefasAgendadas(
        banco, 60, notificador=segundo_notificador, relogio=lambda: agora
    )
    segundo.iniciar()
    segundo.parar()

    assert len(primeiro_notificador.mensagens) == 1
    assert "ATRASADO" in primeiro_notificador.mensagens[0]
    assert segundo_notificador.mensagens == []
    with banco._conectar() as conexao:
        lembrete = conexao.execute(
            "SELECT avisado_em, tentativas_aviso FROM lembretes"
        ).fetchone()
    assert lembrete["avisado_em"] is not None
    assert lembrete["tentativas_aviso"] == 1


def test_falha_de_tarefa_e_registrada_sem_derrubar_agendador(tmp_path) -> None:
    from zoneinfo import ZoneInfo

    agora = datetime(2026, 11, 14, 9, 0, tzinfo=ZoneInfo("America/Araguaina"))
    banco = Banco(tmp_path / "assistente.db", relogio=lambda: agora)
    banco.criar_lembrete("buscar encomenda", agora.replace(hour=8))

    class NotificadorComFalha:
        def notificar(self, _: str) -> None:
            raise RuntimeError("terminal indisponível")

    tarefas = TarefasAgendadas(
        banco,
        60,
        notificador=NotificadorComFalha(),
        relogio=lambda: agora,
    )

    tarefas.iniciar()
    tarefas.parar()

    with banco._conectar() as conexao:
        erro = conexao.execute(
            "SELECT ferramenta, erro FROM log_ferramentas"
        ).fetchone()
    assert erro["ferramenta"] == "verificar_lembretes"
    assert "terminal indisponível" in erro["erro"]


def test_notificador_terminal_preserva_aviso_legivel(capsys) -> None:
    NotificadorTerminal().notificar("BRIEFING: resumo")

    assert "🔔 BRIEFING: resumo" in capsys.readouterr().out
