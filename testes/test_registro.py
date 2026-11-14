import pytest

from src.ferramentas.busca_notas import ParametrosBuscaNota, buscar_notas
from src.ferramentas.gestao import ParametrosExcluirRegistro
from src.ferramentas.registro import criar_registro
from src.memoria.banco import Banco
from src.seguranca.permissoes import NivelRisco, exigir_permissao


def test_catalogo_inclui_busca_e_gerenciamento_de_lembretes(tmp_path) -> None:
    registro = criar_registro(Banco(tmp_path / "assistente.db"))

    assert {ferramenta.nome for ferramenta in registro.ferramentas} == {
        "salvar_nota",
        "criar_lembrete",
        "consultar_hora",
        "buscar_notas",
        "listar_lembretes",
        "concluir_lembrete",
        "excluir_registro",
        "criar_habito",
        "desativar_habito",
        "listar_habitos",
        "registrar_habito",
        "ver_progresso",
        "marcar_folga",
    }


def test_ferramentas_de_escrita_informam_risco_e_hora_e_leitura(tmp_path) -> None:
    registro = criar_registro(Banco(tmp_path / "assistente.db"))
    riscos = {
        ferramenta.nome: ferramenta.nivel_risco
        for ferramenta in registro.ferramentas
    }

    assert riscos["salvar_nota"] == NivelRisco.ESCRITA
    assert riscos["criar_lembrete"] == NivelRisco.ESCRITA
    assert riscos["consultar_hora"] == NivelRisco.LEITURA
    assert riscos["buscar_notas"] == NivelRisco.LEITURA
    assert riscos["listar_lembretes"] == NivelRisco.LEITURA
    assert riscos["concluir_lembrete"] == NivelRisco.ESCRITA
    assert riscos["excluir_registro"] == NivelRisco.SENSIVEL
    for nome in (
        "criar_habito",
        "desativar_habito",
        "registrar_habito",
        "marcar_folga",
    ):
        assert riscos[nome] == NivelRisco.ESCRITA
    for nome in ("listar_habitos", "ver_progresso"):
        assert riscos[nome] == NivelRisco.LEITURA


def test_acao_sensivel_exige_confirmacao() -> None:
    try:
        exigir_permissao(NivelRisco.SENSIVEL)
    except PermissionError:
        pass
    else:
        raise AssertionError("A confirmação deveria ser obrigatória.")

    exigir_permissao(NivelRisco.SENSIVEL, confirmado=True)


def test_exclusao_de_nota_exige_confirmacao_explicita(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    nota_id = banco.salvar_nota("nota para excluir", [])
    registro = criar_registro(banco)
    parametros = ParametrosExcluirRegistro(tipo="nota", id=nota_id)

    with pytest.raises(PermissionError):
        registro.executar("excluir_registro", parametros)

    assert registro.executar(
        "excluir_registro", parametros, confirmado=True
    ) == f"Nota #{nota_id} apagado."
    assert banco.buscar_notas("excluir") == []


def test_busca_com_consulta_vazia_retorna_instrucao_clara(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")

    mensagem = buscar_notas(banco, ParametrosBuscaNota(consulta="   "))

    assert mensagem == "Informe uma consulta para buscar nas notas."
