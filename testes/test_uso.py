import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.agente.erros import ErroProvedor
from src.agente.uso import ControleUso, ErroLimiteUso
from src.memoria.banco import Banco


class Relogio:
    def __init__(self, agora: datetime) -> None:
        self.agora = agora

    def __call__(self) -> datetime:
        return self.agora


def _uso(entrada: int, saida: int) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=entrada, output_tokens=saida)


def criar(tmp_path, agora: datetime) -> tuple[Banco, Relogio]:
    relogio = Relogio(agora)
    banco = Banco(
        tmp_path / "assistente.db",
        fuso_horario="America/Araguaina",
        relogio=relogio,
    )
    return banco, relogio


def test_banco_soma_tokens_a_partir_do_inicio(tmp_path) -> None:
    banco, relogio = criar(tmp_path, datetime(2026, 11, 15, 12, tzinfo=timezone.utc))
    banco.registrar_uso_modelo("claude", "modelo", "estruturada", 100, 20)
    relogio.agora = datetime(2026, 11, 15, 13, tzinfo=timezone.utc)
    banco.registrar_uso_modelo("claude", "modelo", "streaming", 30, 5)

    assert banco.tokens_usados_desde("2026-11-15T00:00:00+00:00") == 155
    assert banco.tokens_usados_desde("2026-11-15T12:30:00+00:00") == 35


def test_abaixo_do_limite_permite_e_no_limite_bloqueia(tmp_path, caplog) -> None:
    banco, _ = criar(tmp_path, datetime(2026, 11, 15, 12, tzinfo=timezone.utc))
    controle = ControleUso(banco, limite_dia=100, limite_mes=0)

    controle.registrar("modelo", "estruturada", _uso(60, 30))
    controle.verificar()
    controle.registrar("modelo", "estruturada", _uso(5, 5))

    with caplog.at_level(logging.WARNING), pytest.raises(ErroLimiteUso) as erro:
        controle.verificar()

    assert isinstance(erro.value, ErroProvedor)
    assert "limite diário" in str(erro.value)
    assert "CLAUDE_LIMITE_TOKENS_DIA" in str(erro.value)
    assert "limite=dia usados=100 teto=100" in caplog.text


def test_limite_diario_zera_na_virada_do_dia_no_fuso_local(tmp_path) -> None:
    # 23h em Araguaína (UTC-3) ainda é o dia 15; 03h UTC do dia 16 já é 00h local.
    banco, relogio = criar(tmp_path, datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
    controle = ControleUso(banco, limite_dia=50)
    controle.registrar("modelo", "estruturada", _uso(40, 10))

    with pytest.raises(ErroLimiteUso):
        controle.verificar()

    relogio.agora = datetime(2026, 11, 16, 3, 1, tzinfo=timezone.utc)
    controle.verificar()


def test_limite_mensal_bloqueia_e_zera_no_mes_seguinte(tmp_path) -> None:
    banco, relogio = criar(tmp_path, datetime(2026, 11, 3, 12, tzinfo=timezone.utc))
    controle = ControleUso(banco, limite_dia=0, limite_mes=100)
    controle.registrar("modelo", "estruturada", _uso(70, 0))
    relogio.agora = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    controle.registrar("modelo", "estruturada", _uso(30, 0))

    with pytest.raises(ErroLimiteUso, match="limite mensal"):
        controle.verificar()

    relogio.agora = datetime(2026, 12, 1, 3, 1, tzinfo=timezone.utc)
    controle.verificar()


def test_limite_zero_significa_sem_limite(tmp_path) -> None:
    banco, _ = criar(tmp_path, datetime(2026, 11, 15, 12, tzinfo=timezone.utc))
    controle = ControleUso(banco, limite_dia=0, limite_mes=0)
    controle.registrar("modelo", "estruturada", _uso(10_000_000, 0))

    controle.verificar()
