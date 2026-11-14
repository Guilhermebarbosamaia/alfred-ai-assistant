from datetime import datetime
from zoneinfo import ZoneInfo

from src.agente.datas import resolver_data_relativa


def test_resolve_expressao_amanha_com_horario_local() -> None:
    fuso = ZoneInfo("America/Araguaina")
    agora = datetime(2026, 11, 14, 10, 0, tzinfo=fuso)

    resultado = resolver_data_relativa("amanhã às 10h", agora, fuso)

    assert resultado == datetime(2026, 11, 15, 10, 0, tzinfo=fuso)


def test_resolve_hoje_a_noite() -> None:
    fuso = ZoneInfo("America/Araguaina")
    agora = datetime(2026, 11, 14, 10, 0, tzinfo=fuso)

    resultado = resolver_data_relativa("hoje à noite", agora, fuso)

    assert resultado == datetime(2026, 11, 14, 20, 0, tzinfo=fuso)


def test_resolve_intervalo_a_partir_do_relogio_injetado() -> None:
    fuso = ZoneInfo("America/Araguaina")
    agora = datetime(2026, 11, 14, 10, 0, tzinfo=fuso)

    resultado = resolver_data_relativa("daqui a 2 horas", agora, fuso)

    assert resultado == datetime(2026, 11, 14, 12, 0, tzinfo=fuso)


def test_expressao_sem_horario_nao_inventa_hora() -> None:
    fuso = ZoneInfo("America/Araguaina")
    agora = datetime(2026, 11, 14, 10, 0, tzinfo=fuso)

    assert resolver_data_relativa("amanhã", agora, fuso) is None


def test_resolve_data_numerica_e_horario() -> None:
    fuso = ZoneInfo("America/Araguaina")
    agora = datetime(2026, 11, 14, 10, 0, tzinfo=fuso)

    resultado = resolver_data_relativa("15/11/2026 às 18h", agora, fuso)

    assert resultado == datetime(2026, 11, 15, 18, 0, tzinfo=fuso)
