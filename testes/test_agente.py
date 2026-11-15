import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.agente.agente import Agente
from src.agente.provedor import ErroProvedorOllama
from src.ferramentas.registro import criar_registro
from src.memoria.banco import Banco


class ProvedorFalso:
    def __init__(self, respostas: list[str]) -> None:
        self.respostas = iter(respostas)
        self.chamadas = 0
        self.mensagens: list[dict[str, str]] = []
        self.chamadas_mensagens: list[list[dict[str, str]]] = []

    def gerar(
        self, mensagens: list[dict[str, str]], _: dict[str, object]
    ) -> str:
        self.chamadas += 1
        self.mensagens = mensagens
        self.chamadas_mensagens.append([dict(mensagem) for mensagem in mensagens])
        return next(self.respostas)


class ProvedorStreamingFalso(ProvedorFalso):
    def __init__(self, respostas: list[str], fragmentos: list[str]) -> None:
        super().__init__(respostas)
        self.fragmentos = fragmentos
        self.chamadas_streaming = 0

    def gerar_texto_streaming(
        self,
        mensagens: list[dict[str, str]],
        ao_receber: Callable[[str], None] | None = None,
    ) -> str:
        self.chamadas_streaming += 1
        self.mensagens = mensagens
        for fragmento in self.fragmentos:
            if ao_receber is not None:
                ao_receber(fragmento)
        return "".join(self.fragmentos)


def criar_agente(tmp_path, respostas: list[str]) -> tuple[Agente, Banco, ProvedorFalso]:
    banco = Banco(tmp_path / "assistente.db")
    provedor = ProvedorFalso(respostas)
    return Agente(provedor, banco, criar_registro(banco)), banco, provedor


def test_conversa_casual_usa_rota_direta_em_streaming(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    provedor = ProvedorStreamingFalso([], ["Olá", "!"])
    agente = Agente(provedor, banco, criar_registro(banco))
    fragmentos: list[str] = []

    resposta = agente.processar("Oi!", ao_receber=fragmentos.append)

    assert resposta == "Olá!"
    assert fragmentos == ["Olá", "!"]
    assert provedor.chamadas_streaming == 1
    assert provedor.chamadas == 0
    assert provedor.mensagens[0]["role"] == "system"
    assert "não execute ações" in provedor.mensagens[0]["content"]
    assert banco.ultimas_mensagens()[-1] == {
        "papel": "assistant",
        "conteudo": "Olá!",
    }


def test_pedido_de_acao_nao_usa_rota_de_conversa_direta(tmp_path) -> None:
    agente, _, provedor = criar_agente(
        tmp_path, [json.dumps({"acao": "conversa", "resposta": "Pronto."})]
    )

    assert agente.processar("Oi, anota comprar leite") == "Pronto."
    assert provedor.chamadas == 1


def test_historico_e_cortado_por_orcamento_estimado_de_tokens() -> None:
    mensagens = [
        {"role": papel, "content": conteudo}
        for papel, conteudo in (
            ("user", "a" * 40),
            ("assistant", "b" * 40),
            ("user", "c" * 40),
        )
    ]

    resultado = Agente._limitar_historico(mensagens, orcamento_tokens=20)

    assert resultado == mensagens[-2:]


def test_historico_preserva_pedido_atual_acima_do_orcamento() -> None:
    atual = {"role": "user", "content": "pedido atual " * 20}

    resultado = Agente._limitar_historico(
        [
            {"role": "assistant", "content": "histórico antigo"},
            atual,
        ],
        orcamento_tokens=2,
    )

    assert resultado == [atual]


def test_extracao_invalida_tenta_ate_duas_vezes_e_depois_salva(tmp_path) -> None:
    saidas = [
        "isto não é json",
        json.dumps(
            {"acao": "nota", "texto": "rascunho", "etiquetas": [], "extra": True}
        ),
        json.dumps(
            {"acao": "nota", "texto": "comprar cabo USB", "etiquetas": ["compras"]}
        ),
    ]
    agente, banco, provedor = criar_agente(tmp_path, saidas)

    resposta = agente.processar("anota: comprar cabo USB")

    assert resposta.startswith("Nota salva")
    assert provedor.chamadas == 3
    with banco._conectar() as conexao:
        nota = conexao.execute("SELECT texto, etiquetas FROM notas").fetchone()
    assert nota["texto"] == "comprar cabo USB"
    assert json.loads(nota["etiquetas"]) == ["compras"]


def test_retry_usa_somente_pedido_e_erro_sem_historico(tmp_path, caplog) -> None:
    agente, banco, provedor = criar_agente(
        tmp_path,
        [
            "resposta inválida",
            json.dumps({"acao": "esclarecer", "resposta": "Tudo certo."}),
        ],
    )
    banco.registrar_mensagem("user", "mensagem anterior privada")
    banco.registrar_mensagem("assistant", "resposta anterior privada")

    with caplog.at_level(logging.INFO):
        resposta = agente.processar("me diga algo")

    assert resposta == "Tudo certo."
    primeira, segunda = provedor.chamadas_mensagens
    assert any(
        "mensagem anterior privada" in mensagem["content"]
        for mensagem in primeira
    )
    assert len(segunda) == 2
    assert segunda[0]["role"] == "system"
    assert segunda[1]["role"] == "user"
    assert "Pedido original: me diga algo" in segunda[1]["content"]
    assert "Erro de validação:" in segunda[1]["content"]
    assert "mensagem anterior privada" not in str(segunda)
    assert "resposta anterior privada" not in str(segunda)
    assert "mensagem anterior privada" not in caplog.text
    assert "tentativas=2 retries=1" in caplog.text


def test_lembrete_sem_horario_pede_esclarecimento(tmp_path) -> None:
    agente, banco, _ = criar_agente(
        tmp_path,
        [
            json.dumps(
                {
                    "acao": "lembrete",
                    "texto": "ligar para João",
                    "expressao_quando": "amanhã",
                }
            )
        ],
    )

    resposta = agente.processar("me lembra de ligar para João amanhã")

    assert "data e horário" in resposta
    with banco._conectar() as conexao:
        assert conexao.execute("SELECT COUNT(*) FROM lembretes").fetchone()[0] == 0


def test_lembrete_valido_salva_data_e_hora_extraidas(tmp_path) -> None:
    fuso = ZoneInfo("America/Araguaina")
    quando = (datetime.now(fuso) + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    agente, banco, _ = criar_agente(
        tmp_path,
        [
            json.dumps(
                {
                    "acao": "lembrete",
                    "texto": "ligar para João",
                    "expressao_quando": "amanhã às 10h",
                }
            )
        ],
    )

    resposta = agente.processar("me lembra de ligar para João amanhã às 10h")

    assert "Lembrete criado" in resposta
    with banco._conectar() as conexao:
        lembrete = conexao.execute(
            "SELECT texto, quando, avisado FROM lembretes"
        ).fetchone()
    assert lembrete["texto"] == "ligar para João"
    assert banco.para_local(lembrete["quando"]) == quando
    assert lembrete["avisado"] == 0


def test_expressao_relativa_e_resolvida_pelo_codigo_com_relogio_injetado(
    tmp_path,
) -> None:
    fuso = ZoneInfo("America/Araguaina")
    agora = datetime(2026, 11, 14, 10, 0, tzinfo=fuso)
    banco = Banco(tmp_path / "assistente.db", relogio=lambda: agora)
    provedor = ProvedorFalso(
        [
            json.dumps(
                {
                    "acao": "lembrete",
                    "texto": "comprar leite",
                    "expressao_quando": "daqui a 2 horas",
                }
            )
        ]
    )
    agente = Agente(provedor, banco, criar_registro(banco), relogio=lambda: agora)

    resposta = agente.processar("me lembra de comprar leite daqui a 2 horas")

    with banco._conectar() as conexao:
        lembrete = conexao.execute("SELECT quando FROM lembretes").fetchone()
    assert "Lembrete criado" in resposta
    assert banco.para_local(lembrete["quando"]) == datetime(
        2026, 11, 14, 12, 0, tzinfo=fuso
    )


def test_respostas_invalidas_nao_derrubam_o_agente(tmp_path) -> None:
    agente, banco, provedor = criar_agente(tmp_path, ["{}", "{}", "{}"])

    resposta = agente.processar("anota alguma coisa")

    assert "Não consegui interpretar" in resposta
    assert provedor.chamadas == 3
    assert banco.ultimas_mensagens()[-1]["papel"] == "assistant"


def test_pedido_de_horario_usa_ferramenta_local(tmp_path) -> None:
    agente, _, _ = criar_agente(
        tmp_path, [json.dumps({"acao": "hora"})]
    )

    resposta = agente.processar("que horas são?")

    assert resposta.startswith("Agora são ")


def test_frase_de_habito_e_validada_e_registrada_com_data_do_codigo(
    tmp_path,
) -> None:
    banco = Banco(tmp_path / "assistente.db")
    habito_id = banco.criar_habito("Treinar", "", 4)
    provedor = ProvedorFalso(
        [
            json.dumps(
                {
                    "acao": "registrar_habito",
                    "registro_habito": {
                        "habito": "treinar",
                        "feito": True,
                        "quantidade": 1,
                        "observacao": "1h",
                    },
                }
            )
        ]
    )
    agente = Agente(provedor, banco, criar_registro(banco))

    resposta = agente.processar("treinei hoje por 1h")

    assert "registrado como feito" in resposta
    with banco._conectar() as conexao:
        registro = conexao.execute(
            """SELECT habito_id, data, feito, quantidade, observacao
               FROM registros_habito"""
        ).fetchone()
    assert registro["habito_id"] == habito_id
    assert registro["feito"] == 1
    assert registro["quantidade"] == 1
    assert registro["observacao"] == "1h"
    assert banco.para_local(registro["data"]).date() == banco.para_local(
        banco.agora_utc()
    ).date()
    assert "Treinar" in provedor.mensagens[0]["content"]


def test_habito_ambiguo_nao_e_adivinhado(tmp_path) -> None:
    banco = Banco(tmp_path / "assistente.db")
    banco.criar_habito("Treinar", "", 4)
    agente = Agente(
        ProvedorFalso(
            [
                json.dumps(
                    {
                        "acao": "registrar_habito",
                        "registro_habito": {
                            "habito": "corrida",
                            "feito": True,
                        },
                    }
                )
            ]
        ),
        banco,
        criar_registro(banco),
    )

    resposta = agente.processar("treinei hoje")

    assert "Não encontrei um hábito ativo chamado" in resposta
    assert "Treinar" in resposta
    with banco._conectar() as conexao:
        assert conexao.execute(
            "SELECT COUNT(*) FROM registros_habito"
        ).fetchone()[0] == 0


def test_falha_do_modelo_pede_reformulacao_sem_inferir(tmp_path) -> None:
    class ProvedorIndisponivel:
        def gerar(
            self, _: list[dict[str, str]], __: dict[str, object]
        ) -> str:
            raise ErroProvedorOllama("modelo indisponível")

    banco = Banco(tmp_path / "assistente.db")
    banco.criar_habito("Treinar", "", 4)
    agente = Agente(ProvedorIndisponivel(), banco, criar_registro(banco))

    resposta = agente.processar("treinei hoje")

    assert "Tente novamente" in resposta
    assert "se for um registro de hábito" in resposta
    with banco._conectar() as conexao:
        assert conexao.execute(
            "SELECT COUNT(*) FROM registros_habito"
        ).fetchone()[0] == 0


def test_busca_nas_notas_responde_apenas_com_trechos_encontrados(tmp_path) -> None:
    provedor = ProvedorFalso(
        [
            json.dumps(
                {"acao": "buscar_notas", "texto": "cafe", "resposta": None}
            ),
            json.dumps({"resposta": "A nota diz para comprar café."}),
        ]
    )
    banco = Banco(tmp_path / "assistente.db")
    banco.salvar_nota("Comprar café para a reunião", [])
    agente = Agente(provedor, banco, criar_registro(banco))

    resposta = agente.processar("O que preciso comprar?")

    contexto = json.loads(provedor.mensagens[-1]["content"])
    assert resposta == "A nota diz para comprar café."
    assert "Comprar" in contexto["trechos"]
    assert "para a reunião" in contexto["trechos"]
    assert "Nota #1" in contexto["trechos"]


def test_busca_sem_resultados_pede_que_o_modelo_admita_ausencia(tmp_path) -> None:
    provedor = ProvedorFalso(
        [
            json.dumps(
                {"acao": "buscar_notas", "texto": "viagem", "resposta": None}
            ),
            json.dumps(
                {"resposta": "Não encontrei essa informação nas suas notas."}
            ),
        ]
    )
    banco = Banco(tmp_path / "assistente.db")
    agente = Agente(provedor, banco, criar_registro(banco))
    resposta = agente.processar("O que anotei sobre viagens?")

    contexto = json.loads(provedor.mensagens[-1]["content"])
    assert contexto["trechos"] == "Nenhuma nota encontrada."
    assert "não encontrou" in provedor.mensagens[0]["content"].lower()
    assert resposta == "Não encontrei essa informação nas suas notas."
