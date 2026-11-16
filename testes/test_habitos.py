import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.agendador.resumo import ResumoSemanal
from src.agendador.tarefas import TarefasAgendadas
from src.agente.provedor import ErroProvedorOllama
from src.memoria.banco import Banco


FUSO = ZoneInfo("America/Araguaina")
AGORA = datetime(2026, 11, 14, 21, 0, tzinfo=FUSO)


class ProvedorFalso:
    def __init__(self, resposta: str | Exception) -> None:
        self.resposta = resposta
        self.mensagens: list[dict[str, str]] = []

    def gerar(
        self, mensagens: list[dict[str, str]], _: dict[str, object]
    ) -> str:
        self.mensagens = mensagens
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


class NotificadorFalso:
    def __init__(self) -> None:
        self.mensagens: list[str] = []

    def notificar(self, texto: str) -> None:
        self.mensagens.append(texto)


def criar_banco(tmp_path) -> Banco:
    return Banco(
        tmp_path / "assistente.db",
        fuso_horario=str(FUSO),
        relogio=lambda: AGORA,
    )


def test_sequencia_e_quebrada_por_dia_sem_registro(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habito_id = banco.criar_habito("Treinar", "", 4)
    banco.registrar_habito(habito_id, True, dia=date(2026, 11, 12))
    banco.registrar_habito(habito_id, True, dia=date(2026, 11, 14))

    progresso = banco.progresso_habito(habito_id)

    assert progresso["sequencia_atual"] == 1
    assert progresso["melhor_sequencia"] == 1


def test_folga_preserva_sequencia_sem_contar_como_dia_feito(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habito_id = banco.criar_habito("Estudar", "", 4)
    banco.registrar_habito(habito_id, True, dia=date(2026, 11, 12))
    banco.marcar_folga(habito_id, dia=date(2026, 11, 13))
    banco.registrar_habito(habito_id, True, dia=date(2026, 11, 14))

    progresso = banco.progresso_habito(habito_id)

    assert progresso["sequencia_atual"] == 3
    assert progresso["dias_feitos"] == 2
    assert progresso["percentual_periodo"] == 50


def test_registro_duplicado_no_mesmo_dia_e_rejeitado(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habito_id = banco.criar_habito("Ler", "", 3)
    banco.registrar_habito(habito_id, True)

    with pytest.raises(ValueError, match="já tem um registro"):
        banco.registrar_habito(habito_id, True)
    with banco._conectar() as conexao:
        data_salva = datetime.fromisoformat(
            conexao.execute(
                "SELECT data FROM registros_habito"
            ).fetchone()["data"]
        )
    assert data_salva.utcoffset().total_seconds() == 0
    assert banco.para_local(data_salva).date() == date(2026, 11, 14)


def test_limite_de_habitos_ativos_e_reaproveitado_apos_desativar(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habitos = [banco.criar_habito(f"Hábito {indice}", "", 3) for indice in range(5)]

    with pytest.raises(ValueError, match="cinco hábitos ativos"):
        banco.criar_habito("Sexto", "", 3)
    assert banco.desativar_habito(habitos[0])
    assert banco.criar_habito("Sexto", "", 3)


def test_checkin_agendado_nao_e_enviado_mais_de_uma_vez(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    banco.criar_habito("Caminhar", "", 3)
    notificador = NotificadorFalso()
    tarefas = TarefasAgendadas(
        banco,
        60,
        notificador=notificador,
        horario_checkin=time(21, 0),
        relogio=lambda: AGORA,
    )

    tarefas.executar_checkin(manual=False)
    resposta_repetida = tarefas.executar_checkin(manual=False)

    assert len(notificador.mensagens) == 1
    assert "Caminhar" in notificador.mensagens[0]
    assert "já foi enviado" in resposta_repetida
    assert banco.checkin()["status"] == "pendente"
    with banco._conectar() as conexao:
        assert conexao.execute("SELECT COUNT(*) FROM checkins").fetchone()[0] == 1


def test_checkin_sem_resposta_e_marcado_como_pulado(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    banco.criar_habito("Alongar", "", 3)
    tarefas = TarefasAgendadas(
        banco, 60, relogio=lambda: AGORA
    )

    texto = tarefas.executar_checkin(manual=True)
    tarefas.pular_checkin_pendente()

    assert "sim, não ou quantidade" in texto
    assert banco.checkin()["status"] == "pulado"


def test_checkin_registra_resposta_e_finaliza(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habito_id = banco.criar_habito("Pedalar", "", 3)
    tarefas = TarefasAgendadas(
        banco, 60, relogio=lambda: AGORA
    )

    resultado = tarefas.executar_checkin(
        manual=True, responder=lambda _: "12 km"
    )

    assert resultado == "Check-in respondido e registrado."
    assert banco.checkin()["status"] == "respondido"
    with banco._conectar() as conexao:
        registro = conexao.execute(
            """SELECT feito, quantidade, observacao FROM registros_habito
               WHERE habito_id = ?""",
            (habito_id,),
        ).fetchone()
    assert tuple(registro) == (1, 12.0, "km")


def test_resumo_semanal_usa_dados_calculados_pelo_codigo(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habito_id = banco.criar_habito("Estudar", "", 3)
    for dia in (9, 10, 11):
        banco.registrar_habito(
            habito_id, True, dia=date(2026, 11, dia)
        )
    provedor = ProvedorFalso(json.dumps({"texto": "Boa semana de estudos."}))
    resumo = ResumoSemanal(banco, provedor)

    texto = resumo.gerar(AGORA)
    dados = json.loads(provedor.mensagens[1]["content"])

    assert texto == "Boa semana de estudos."
    assert dados["habitos"] == [
        {
            "nome": "Estudar",
            "dias_feitos": 3,
            "meta_semanal": 3,
            "meta_atingida": True,
            "melhor_sequencia": 3,
        }
    ]


def test_resumo_semanal_tem_fallback_quando_provedor_falha(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    habito_id = banco.criar_habito("Meditar", "", 5)
    banco.registrar_habito(habito_id, True, dia=date(2026, 11, 14))
    resumo = ResumoSemanal(
        banco, ProvedorFalso(ErroProvedorOllama("indisponível"))
    )

    texto = resumo.gerar(AGORA)

    assert "Resumo semanal (2026-11-09 a 2026-11-14)" in texto
    assert "Meditar: 1/5 dias, meta não atingida" in texto
    assert "melhor sequência: 1 dia(s)" in texto
    assert len(texto.splitlines()) <= 6


def test_migracao_de_habitos_e_idempotente(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    banco.inicializar()
    with banco._conectar() as conexao:
        primeira_versao = banco._versao(conexao)
        contagem = conexao.execute(
            "SELECT COUNT(*) FROM versao_esquema WHERE versao = 4"
        ).fetchone()[0]

    banco.inicializar()

    with banco._conectar() as conexao:
        contagem_depois = conexao.execute(
            "SELECT COUNT(*) FROM versao_esquema WHERE versao = 4"
        ).fetchone()[0]
        indices = {
            linha["name"]
            for linha in conexao.execute(
                "PRAGMA index_list(registros_habito)"
            )
        }
    assert primeira_versao == Banco._VERSAO_ATUAL == 6
    assert contagem == contagem_depois == 1
    assert "sqlite_autoindex_registros_habito_1" in indices


def test_agendador_configura_checkin_e_resumo_semanal(tmp_path) -> None:
    banco = criar_banco(tmp_path)
    resumo = ResumoSemanal(
        banco, ProvedorFalso(json.dumps({"texto": "Resumo."}))
    )
    tarefas = TarefasAgendadas(
        banco,
        60,
        resumo=resumo,
        horario_checkin=time(22, 30),
        dia_resumo="sun",
        horario_resumo=time(19, 15),
        relogio=lambda: AGORA,
    )

    tarefas.iniciar()
    try:
        checkin = tarefas._agendador.get_job("checkin_noturno")
        resumo_semanal = tarefas._agendador.get_job("resumo_semanal")
        pular = tarefas._agendador.get_job("pular_checkin_pendente")
        assert checkin is not None
        assert resumo_semanal is not None
        assert pular is not None
        campos_checkin = {
            campo.name: str(campo) for campo in checkin.trigger.fields
        }
        campos_resumo = {
            campo.name: str(campo) for campo in resumo_semanal.trigger.fields
        }
        campos_pular = {campo.name: str(campo) for campo in pular.trigger.fields}
        assert campos_checkin["hour"] == "22"
        assert campos_checkin["minute"] == "30"
        assert campos_resumo["day_of_week"] == "sun"
        assert campos_resumo["hour"] == "19"
        assert campos_resumo["minute"] == "15"
        assert campos_pular["hour"] == "23"
        assert campos_pular["minute"] == "59"
    finally:
        tarefas.parar()
