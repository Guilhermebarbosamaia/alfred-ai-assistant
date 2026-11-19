import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.memoria.banco import Banco
from src.memoria.conexao_do import ConexaoDO


class ErroJs(Exception):
    """Simula o erro que o runtime do Workers repassa ao Python."""


class StorageSQLFalso:
    """Imita `ctx.storage.sql` de um Durable Object sobre um SQLite real."""

    def __init__(self, caminho) -> None:
        self._conexao = sqlite3.connect(caminho, isolation_level=None)
        self.consultas: list[str] = []

    def exec(self, consulta: str, *parametros: object) -> SimpleNamespace:
        self.consultas.append(consulta)
        if consulta.lstrip().upper().startswith(("BEGIN", "COMMIT", "ROLLBACK")):
            raise ErroJs("transações explícitas não são permitidas")
        try:
            if parametros:
                cursor = self._conexao.execute(consulta, parametros)
            else:
                cursor = self._conexao.cursor()
                cursor.executescript(consulta) if ";" in consulta.strip().rstrip(
                    ";"
                ) else cursor.execute(consulta)
        except sqlite3.Error as erro:
            raise ErroJs(f"Error: {erro}") from None
        nomes = [coluna[0] for coluna in cursor.description or []]
        linhas = [list(linha) for linha in cursor.fetchall()]
        return SimpleNamespace(
            columnNames=nomes,
            raw=lambda: SimpleNamespace(toArray=lambda: linhas),
        )


@pytest.fixture
def banco_do(tmp_path):
    storage = StorageSQLFalso(tmp_path / "do.sqlite")
    agora = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)
    banco = Banco(
        tmp_path / "rotulo.db",
        relogio=lambda: agora,
        conectar=lambda: ConexaoDO(storage),
    )
    return banco, storage, tmp_path


def test_migracoes_rodam_sem_arquivo_local_nem_backup(banco_do) -> None:
    banco, storage, pasta = banco_do

    assert banco._versao(banco._conectar()) == Banco._VERSAO_ATUAL
    assert not (pasta / "rotulo.db").exists()
    assert not (pasta / "backup").exists()
    assert not any(c.lstrip().upper().startswith("BEGIN") for c in storage.consultas)

    banco.inicializar()
    assert banco._versao(banco._conectar()) == Banco._VERSAO_ATUAL


def test_lastrowid_rowcount_e_integridade(banco_do) -> None:
    banco, _, _ = banco_do

    primeiro = banco.criar_habito("Ler", "", 3)
    segundo = banco.criar_habito("Correr", "", 2)
    assert (primeiro, segundo) == (1, 2)
    with pytest.raises(ValueError, match="Já existe"):
        banco.criar_habito("Ler", "", 3)

    assert banco.registrar_habito(primeiro, True) >= 1
    with pytest.raises(ValueError, match="já tem um registro"):
        banco.registrar_habito(primeiro, True)
    assert banco.desativar_habito(segundo) is True
    assert banco.desativar_habito(segundo) is False


def test_notas_com_busca_e_lembretes_vencidos(banco_do) -> None:
    banco, _, _ = banco_do

    nota_id = banco.salvar_nota("comprar café especial", ["casa"])
    assert [n["id"] for n in banco.buscar_notas("café")] == [nota_id]

    agora = banco.agora_utc()
    lembrete_id = banco.criar_lembrete("ligar", agora - timedelta(minutes=1))
    vencidos = banco.lembretes_vencidos(agora)
    assert [l["id"] for l in vencidos] == [lembrete_id]
    assert banco.lembretes_vencidos(agora) == []
    assert banco.tokens_usados_desde("2000-01-01") == 0


def test_linha_aceita_nome_e_posicao(tmp_path) -> None:
    conexao = ConexaoDO(StorageSQLFalso(tmp_path / "x.sqlite"))
    linha = conexao.execute("SELECT 7 AS sete, 'a' AS letra").fetchone()

    assert linha[0] == 7
    assert linha["letra"] == "a"
    assert dict(linha) == {"sete": 7, "letra": "a"}
    assert conexao.execute("SELECT 1 WHERE 0").fetchone() is None


def test_erro_generico_vira_erro_do_sqlite(tmp_path) -> None:
    conexao = ConexaoDO(StorageSQLFalso(tmp_path / "x.sqlite"))

    with pytest.raises(sqlite3.OperationalError):
        conexao.execute("SELECT * FROM tabela_inexistente")
