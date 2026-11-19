import logging
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.agendador.tarefas import TarefasAgendadas
from src.agente.agente import Agente
from src.config import Configuracao, ErroConfiguracao
from src.ferramentas.registro import criar_registro
from src.interfaces.comandos import TEXTO_AJUDA
from src.interfaces.telegram_api import (
    ClienteTelegram,
    ErroTelegram,
    NotificadorTelegramHttp,
)
from src.interfaces.telegram_webhook import InterfaceTelegramWebhook
from src.memoria.banco import Banco
from src.memoria.conexao_do import ConexaoDO
from src.nuvem import montar_app
from testes.test_conexao_do import StorageSQLFalso

CHAT = 4242
SEGREDO = "segredo-do-webhook"
TOKEN = "123:TOKEN-FALSO"


class HttpFalso:
    """Substitui o httpx.Client: registra as chamadas à Bot API, sem rede."""

    def __init__(self, falhar: Exception | None = None) -> None:
        self.chamadas: list[tuple[str, dict]] = []
        self._falhar = falhar
        self._proximo_id = 100

    def post(self, url: str, json: dict) -> SimpleNamespace:
        if self._falhar is not None:
            raise self._falhar
        metodo = url.rsplit("/", 1)[-1]
        self.chamadas.append((metodo, json))
        self._proximo_id += 1
        resultado = {"message_id": self._proximo_id} if metodo == "sendMessage" else True
        return SimpleNamespace(json=lambda: {"ok": True, "result": resultado})

    def textos(self, metodo: str = "sendMessage") -> list[str]:
        return [dados["text"] for nome, dados in self.chamadas if nome == metodo]


class Relogio:
    def __init__(self, inicio: datetime) -> None:
        self.agora = inicio

    def __call__(self) -> datetime:
        return self.agora


@pytest.fixture
def ambiente(tmp_path):
    relogio = Relogio(datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc))
    banco = Banco(tmp_path / "assistente.db", relogio=relogio)
    http = HttpFalso()

    def nova_interface(agente=None) -> InterfaceTelegramWebhook:
        # Cada chamada simula uma requisição nova, sem memória da anterior.
        agente = agente or Agente(
            SimpleNamespace(gerar=lambda *_: "{}"),
            banco,
            criar_registro(banco),
            pendencias_no_banco=True,
        )
        tarefas = TarefasAgendadas(
            banco,
            60,
            notificador=NotificadorTelegramHttp(ClienteTelegram(TOKEN, http), CHAT),
        )
        return InterfaceTelegramWebhook(
            agente, tarefas, banco, ClienteTelegram(TOKEN, http), CHAT, SEGREDO
        )

    return SimpleNamespace(banco=banco, http=http, relogio=relogio, nova=nova_interface)


def _mensagem(texto: str, update_id: int = 1, chat: int = CHAT) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": chat}, "text": texto},
    }


def test_segredo_invalido_e_recusado_sem_enviar_nada(ambiente) -> None:
    interface = ambiente.nova()

    assert interface.processar(_mensagem("/ajuda"), "errado") == 403
    assert interface.processar(_mensagem("/ajuda"), None) == 403
    assert ambiente.http.chamadas == []


def test_segredo_vazio_na_configuracao_recusa_tudo(ambiente) -> None:
    interface = InterfaceTelegramWebhook(
        None, None, ambiente.banco, ClienteTelegram(TOKEN, ambiente.http), CHAT, ""
    )

    assert interface.processar(_mensagem("/ajuda"), "") == 403


def test_chat_nao_autorizado_e_ignorado_sem_logar_conteudo(ambiente, caplog) -> None:
    interface = ambiente.nova()

    with caplog.at_level(logging.INFO):
        status = interface.processar(
            _mensagem("texto secreto do intruso", chat=999), SEGREDO
        )

    assert status == 200
    assert ambiente.http.chamadas == []
    assert "texto secreto do intruso" not in caplog.text
    assert "999" in caplog.text


def test_comando_ajuda_e_update_repetido(ambiente) -> None:
    interface = ambiente.nova()

    assert interface.processar(_mensagem("/ajuda", update_id=7), SEGREDO) == 200
    assert interface.processar(_mensagem("/ajuda", update_id=7), SEGREDO) == 200

    assert ambiente.http.textos() == [TEXTO_AJUDA]
    assert ("sendChatAction", {"chat_id": CHAT, "action": "typing"}) in (
        ambiente.http.chamadas
    )


def test_midia_nao_suportada(ambiente) -> None:
    update = {"update_id": 1, "message": {"chat": {"id": CHAT}, "photo": []}}

    ambiente.nova().processar(update, SEGREDO)

    assert "não são suportados" in ambiente.http.textos()[0]


def test_exclusao_confirmada_em_outra_requisicao(ambiente) -> None:
    nota_id = ambiente.banco.salvar_nota("nota a apagar", [])

    ambiente.nova().processar(_mensagem(f"apagar nota {nota_id}", 1), SEGREDO)

    _, envio = ambiente.http.chamadas[-1]
    assert envio["text"] == f"Confirma apagar nota #{nota_id}? Esta ação não pode ser desfeita."
    botoes = envio["reply_markup"]["inline_keyboard"][0]
    assert botoes[0]["callback_data"].startswith("confirmar:")

    callback = {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "data": botoes[0]["callback_data"],
            "message": {"chat": {"id": CHAT}},
        },
    }
    ambiente.nova().processar(callback, SEGREDO)

    assert ambiente.banco.buscar_notas("apagar") == []
    assert ("answerCallbackQuery", {"callback_query_id": "cb1"}) in ambiente.http.chamadas
    assert ambiente.banco.estado_conversa(str(CHAT), "confirmacao_telegram") is None
    assert ambiente.banco.estado_conversa(str(CHAT), "acao_pendente") is None


def test_exclusao_por_texto_e_confirmacao_expirada(ambiente) -> None:
    primeira = ambiente.banco.salvar_nota("primeira", [])
    segunda = ambiente.banco.salvar_nota("segunda", [])

    ambiente.nova().processar(_mensagem(f"apagar nota {primeira}", 1), SEGREDO)
    ambiente.nova().processar(_mensagem("talvez", 2), SEGREDO)
    ambiente.nova().processar(_mensagem("sim", 3), SEGREDO)
    assert [n["id"] for n in ambiente.banco.ultimos_registros("notas")] == [segunda]

    ambiente.nova().processar(_mensagem(f"apagar nota {segunda}", 4), SEGREDO)
    ambiente.relogio.agora += timedelta(minutes=3)
    ambiente.nova().processar(_mensagem("sim", 5), SEGREDO)

    textos = ambiente.http.textos()
    assert "Responda “sim” ou “não” para a confirmação pendente." in textos
    assert textos[-1] == "A confirmação expirou."
    assert [n["id"] for n in ambiente.banco.ultimos_registros("notas")] == [segunda]


def test_checkin_conversado_entre_requisicoes(ambiente) -> None:
    ler = ambiente.banco.criar_habito("Ler", "", 3)
    correr = ambiente.banco.criar_habito("Correr", "", 3)

    ambiente.nova().processar(_mensagem("/checkin", 1), SEGREDO)
    ambiente.nova().processar(_mensagem("sim", 2), SEGREDO)
    ambiente.nova().processar(_mensagem("2 km", 3), SEGREDO)

    assert ambiente.http.textos() == [
        "Você fez Ler hoje? Responda sim, não ou quantidade.",
        "Você fez Correr hoje? Responda sim, não ou quantidade.",
        "Check-in respondido e registrado.",
    ]
    assert ambiente.banco.registros_do_checkin() == {ler, correr}
    assert ambiente.banco.checkin()["status"] == "respondido"
    assert ambiente.banco.estado_conversa(str(CHAT), "checkin") is None


def test_checkin_com_resposta_invalida_fica_parcial(ambiente) -> None:
    ambiente.banco.criar_habito("Ler", "", 3)

    ambiente.nova().processar(_mensagem("/checkin", 1), SEGREDO)
    ambiente.nova().processar(_mensagem("quem sabe", 2), SEGREDO)

    assert ambiente.http.textos()[-1].startswith("Check-in parcial; sem resposta válida para: Ler")


def test_resposta_do_modelo_publicada_por_streaming(ambiente) -> None:
    def processar(pedido, sessao_id, ao_receber):
        ao_receber("Olá, ")
        ao_receber("x" * 100)
        return "Olá, " + "x" * 100

    agente = SimpleNamespace(processar=processar, tem_confirmacao_pendente=lambda _: False)
    ambiente.nova(agente).processar(_mensagem("oi", 1), SEGREDO)

    assert ambiente.http.textos() == ["Olá, "]
    assert ambiente.http.textos("editMessageText") == ["Olá, " + "x" * 100]


def test_erro_no_agente_responde_generico_sem_logar_conteudo(ambiente, caplog) -> None:
    def processar(pedido, sessao_id, ao_receber):
        raise RuntimeError("detalhe interno")

    agente = SimpleNamespace(processar=processar)
    with caplog.at_level(logging.INFO):
        status = ambiente.nova(agente).processar(
            _mensagem("meu pedido confidencial", 1), SEGREDO
        )

    assert status == 200
    assert ambiente.http.textos() == ["algo deu errado, tente de novo"]
    assert "meu pedido confidencial" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_cliente_telegram_nao_expoe_token_em_erros(caplog) -> None:
    cliente = ClienteTelegram(TOKEN, HttpFalso(falhar=ConnectionError(f"url com {TOKEN}")))

    with pytest.raises(ErroTelegram) as erro:
        cliente.enviar_mensagem(CHAT, "oi")

    assert TOKEN not in str(erro.value)
    assert erro.value.__cause__ is None
    assert erro.value.__suppress_context__


def test_notificador_http_tenta_tres_vezes_e_desiste() -> None:
    http = HttpFalso(falhar=ConnectionError("fora do ar"))
    notificador = NotificadorTelegramHttp(ClienteTelegram(TOKEN, http), CHAT)

    with pytest.raises(RuntimeError, match="3 tentativas"):
        notificador.notificar("aviso")


def test_ciclo_do_cron_dispara_tarefas_uma_vez(tmp_path) -> None:
    # 08:05 em Araguaína (UTC-3) num domingo.
    relogio = Relogio(datetime(2026, 11, 22, 11, 5, tzinfo=timezone.utc))
    banco = Banco(tmp_path / "assistente.db", relogio=relogio)
    avisos: list[str] = []
    checkins: list[str] = []
    tarefas = TarefasAgendadas(
        banco,
        60,
        briefing=SimpleNamespace(gerar=lambda agora, agendado: "Bom dia."),
        notificador=SimpleNamespace(notificar=avisos.append),
        horario_briefing=time(8, 0),
        horario_checkin=time(21, 0),
        resumo=SimpleNamespace(gerar=lambda agora, agendado: "Semana boa."),
        dia_resumo="sun",
        horario_resumo=time(20, 0),
    )
    banco.criar_lembrete("ligar", relogio.agora - timedelta(minutes=1))

    tarefas.executar_ciclo(lambda: checkins.append("x"))
    tarefas.executar_ciclo(lambda: checkins.append("x"))
    assert avisos == ["LEMBRETE ATRASADO #1: ligar", "BRIEFING: Bom dia."]
    assert checkins == []

    relogio.agora = datetime(2026, 11, 22, 23, 30, tzinfo=timezone.utc)  # 20:30
    tarefas.executar_ciclo(lambda: checkins.append("x"))
    tarefas.executar_ciclo(lambda: checkins.append("x"))
    assert avisos[-1] == "RESUMO SEMANAL: Semana boa."
    assert len(avisos) == 3

    relogio.agora = datetime(2026, 11, 23, 0, 0, tzinfo=timezone.utc)  # 21:00
    tarefas.executar_ciclo(lambda: checkins.append("x"))
    assert checkins == ["x"]


def test_ciclo_agendado_inicia_checkin_pelo_telegram(ambiente) -> None:
    ambiente.banco.criar_habito("Ler", "", 3)
    ambiente.relogio.agora = datetime(2026, 11, 20, 0, 1, tzinfo=timezone.utc)  # 21:01
    interface = ambiente.nova()
    tarefas = interface._tarefas

    tarefas.executar_ciclo(lambda: interface.iniciar_checkin(manual=False))
    tarefas.executar_ciclo(lambda: interface.iniciar_checkin(manual=False))

    assert ambiente.http.textos() == [
        "CHECK-IN NOTURNO:\nVocê fez Ler hoje? Responda sim, não ou quantidade."
    ]


def test_configuracao_le_mapeamento_sem_tocar_no_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("CIDADE", "Palmas")

    configuracao = Configuracao.carregar(
        {"CIDADE": "Imperatriz", "TELEGRAM_WEBHOOK_SEGREDO": " abc "}
    )

    assert configuracao.cidade == "Imperatriz"
    assert configuracao.telegram_webhook_segredo.get_secret_value() == "abc"


def _ambiente_nuvem(**extras: str) -> dict[str, str]:
    base = {
        "PROVEDOR": "claude",
        "ANTHROPIC_API_KEY": "chave-falsa",
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "TELEGRAM_ID_AUTORIZADO": str(CHAT),
        "TELEGRAM_WEBHOOK_SEGREDO": SEGREDO,
    }
    base.update(extras)
    return base


@pytest.mark.parametrize(
    "extras, mensagem",
    [
        ({"PROVEDOR": "ollama"}, "PROVEDOR=claude"),
        ({"TELEGRAM_WEBHOOK_SEGREDO": ""}, "TELEGRAM_WEBHOOK_SEGREDO"),
        ({"TELEGRAM_ID_AUTORIZADO": ""}, "TELEGRAM_ID_AUTORIZADO"),
    ],
)
def test_montar_app_exige_configuracao_completa(tmp_path, extras, mensagem) -> None:
    storage = StorageSQLFalso(tmp_path / "do.sqlite")

    with pytest.raises(ErroConfiguracao, match=mensagem):
        montar_app(_ambiente_nuvem(**extras), lambda: ConexaoDO(storage))


def test_montar_app_sobre_durable_object_responde_comando(tmp_path) -> None:
    storage = StorageSQLFalso(tmp_path / "do.sqlite")
    http = HttpFalso()

    app = montar_app(
        _ambiente_nuvem(), lambda: ConexaoDO(storage), http_telegram=http
    )
    status = app.processar_webhook(_mensagem("/notas"), SEGREDO)

    assert status == 200
    assert http.textos() == ["Nenhuma nota salva."]
    assert not (tmp_path / "durable-object").exists()


def test_script_registra_webhook_com_segredo(monkeypatch, capsys) -> None:
    from scripts import registrar_webhook

    http = HttpFalso()
    monkeypatch.setattr(
        registrar_webhook.Configuracao,
        "carregar",
        classmethod(
            lambda cls: Configuracao(
                telegram_bot_token=TOKEN, telegram_webhook_segredo=SEGREDO
            )
        ),
    )
    monkeypatch.setattr(
        registrar_webhook, "ClienteTelegram", lambda token: ClienteTelegram(token, http)
    )

    assert registrar_webhook.main(["https://alfred.exemplo.workers.dev/"]) == 0
    assert registrar_webhook.main(["http://inseguro"]) == 1

    metodo, dados = http.chamadas[0]
    assert metodo == "setWebhook"
    assert dados["url"] == "https://alfred.exemplo.workers.dev/telegram"
    assert dados["secret_token"] == SEGREDO
    assert http.chamadas[1][0] == "setMyCommands"
    assert TOKEN not in capsys.readouterr().out
