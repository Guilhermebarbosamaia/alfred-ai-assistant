import sqlite3
from datetime import datetime, timedelta

from src.memoria.banco import Banco


def test_inicializacao_cria_tabelas_e_pode_ser_repetida(tmp_path) -> None:
    banco = Banco(tmp_path / "dados" / "assistente.db")
    banco.inicializar()

    with banco._conectar() as conexao:
        tabelas = {
            linha["name"]
            for linha in conexao.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "mensagens",
        "notas",
        "lembretes",
        "log_ferramentas",
        "versao_esquema",
        "execucoes_agendador",
        "notas_fts",
        "habitos",
        "registros_habito",
        "checkins",
    } <= tabelas
    with banco._conectar() as conexao:
        colunas_lembretes = {
            linha["name"] for linha in conexao.execute("PRAGMA table_info(lembretes)")
        }
    assert {"concluido", "avisado_em", "tentativas_aviso"} <= colunas_lembretes


def test_historico_retorna_apenas_as_mensagens_mais_recentes(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    for indice in range(12):
        banco.registrar_mensagem("user", f"mensagem {indice}")

    recentes = banco.ultimas_mensagens()

    assert len(recentes) == 10
    assert recentes[0]["conteudo"] == "mensagem 2"
    assert recentes[-1]["conteudo"] == "mensagem 11"


def test_historico_isola_sessoes_de_chat(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    banco.registrar_mensagem("user", "mensagem local")
    banco.registrar_mensagem("user", "mensagem telegram", "1234")

    assert banco.ultimas_mensagens(sessao_id="terminal")[0]["conteudo"] == (
        "mensagem local"
    )
    assert banco.ultimas_mensagens(sessao_id="1234")[0]["conteudo"] == (
        "mensagem telegram"
    )


def test_lembretes_vencidos_sao_marcados_e_entregues_uma_vez(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    vencido = datetime.now().astimezone() - timedelta(minutes=1)
    banco.criar_lembrete("buscar encomenda", vencido)

    primeiro_resultado = banco.lembretes_vencidos(datetime.now().astimezone())
    segundo_resultado = banco.lembretes_vencidos(datetime.now().astimezone())

    assert [item["texto"] for item in primeiro_resultado] == ["buscar encomenda"]
    assert segundo_resultado == []


def test_busca_ignora_acentos_e_maiusculas_e_indexa_notas_novas(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    nota_id = banco.salvar_nota("Café com açúcar", ["Receita"])

    resultados_sem_acentos = banco.buscar_notas("CAFE ACUCAR", limite=3)
    resultados_com_acentos = banco.buscar_notas("café açúcar", limite=3)

    assert len(resultados_sem_acentos) == 1
    assert len(resultados_com_acentos) == 1
    assert resultados_sem_acentos[0]["id"] == nota_id
    assert resultados_sem_acentos[0]["criada_em"]
    assert "[Café]" in resultados_sem_acentos[0]["trecho"]


def test_migracao_indexa_notas_existentes(tmp_path) -> None:
    caminho = tmp_path / "assistente.db"
    with sqlite3.connect(caminho) as conexao:
        conexao.execute(
            """CREATE TABLE notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                texto TEXT NOT NULL,
                etiquetas TEXT NOT NULL DEFAULT '[]',
                criada_em TEXT NOT NULL
            )"""
        )
        conexao.execute(
            "INSERT INTO notas (texto, criada_em) VALUES (?, ?)",
            ("Reunião sobre orçamento", "2026-01-02T09:00:00-03:00"),
        )

    banco = Banco(caminho)
    with banco._conectar() as conexao:
        versoes = conexao.execute(
            "SELECT versao FROM versao_esquema ORDER BY versao"
        ).fetchall()
        aplicacoes = conexao.execute(
            "SELECT versao, aplicada_em FROM versao_esquema ORDER BY versao"
        ).fetchall()
        notas = conexao.execute("SELECT COUNT(*) FROM notas").fetchone()[0]
    backups = list((tmp_path / "backup").glob("assistente_*.db"))
    banco.inicializar()
    backups_apos_repetir = list((tmp_path / "backup").glob("assistente_*.db"))
    with banco._conectar() as conexao:
        aplicacoes_apos_repetir = conexao.execute(
            "SELECT versao, aplicada_em FROM versao_esquema ORDER BY versao"
        ).fetchall()

    resultados = banco.buscar_notas("REUNIAO orcamento")

    assert len(resultados) == 1
    assert resultados[0]["id"] == 1
    assert "2026-01-02" in str(resultados[0]["criada_em"])
    assert [linha["versao"] for linha in versoes] == [1, 2, 3, 4, 5]
    assert [tuple(linha) for linha in aplicacoes] == [
        tuple(linha) for linha in aplicacoes_apos_repetir
    ]
    assert notas == 1
    assert backups
    assert len(backups_apos_repetir) == len(backups)
    with sqlite3.connect(backups[0]) as conexao:
        assert conexao.execute("SELECT texto FROM notas").fetchone()[0] == (
            "Reunião sobre orçamento"
        )


def test_busca_rejeita_limite_fora_da_faixa(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")

    try:
        banco.buscar_notas("qualquer coisa", limite=6)
    except ValueError as erro:
        assert "entre 3 e 5" in str(erro)
    else:
        raise AssertionError("O limite inválido deveria ser rejeitado.")


def test_busca_sanitiza_caracteres_especiais_da_consulta(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    nota_id = banco.salvar_nota("Reunião de equipe", [])

    resultados = banco.buscar_notas('reunião " * (')

    assert len(resultados) == 1
    assert resultados[0]["id"] == nota_id


def test_busca_usa_relevancia_e_limita_resultados_a_cinco(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    for indice in range(8):
        banco.salvar_nota(f"reunião de equipe item {indice}", [])

    resultados = banco.buscar_notas("reuniao equipe")

    assert len(resultados) == 5
    assert all("relevancia" in resultado for resultado in resultados)


def test_triggers_fts_sincronizam_insercao_atualizacao_e_exclusao(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    nota_id = banco.salvar_nota("Reunião de equipe", [])

    assert banco.buscar_notas("reuniao")
    with banco._conectar() as conexao:
        conexao.execute(
            "UPDATE notas SET texto = ? WHERE id = ?",
            ("Consulta médica", nota_id),
        )
    assert banco.buscar_notas("consulta")
    assert banco.buscar_notas("reuniao") == []
    assert banco.apagar_nota(nota_id)
    assert banco.buscar_notas("consulta") == []


def test_datas_novas_sao_gravadas_em_utc(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    momento_local = datetime(2026, 11, 14, 10, 0, tzinfo=banco.fuso)
    banco.criar_lembrete("compromisso", momento_local)

    with banco._conectar() as conexao:
        lembrete = conexao.execute("SELECT quando FROM lembretes").fetchone()

    data_salva = datetime.fromisoformat(lembrete["quando"])
    assert data_salva.utcoffset().total_seconds() == 0
    assert banco.para_local(data_salva) == momento_local
