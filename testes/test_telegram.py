import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.agendador.notificador import NotificadorTelegram
from src.agendador.tarefas import TarefasAgendadas
from src.interfaces.telegram import (
    ConfirmacaoPendente,
    InterfaceTelegram,
    Solicitacao,
    dividir_mensagem,
    mapear_comando,
)
from src.memoria.banco import Banco


class AgenteFalso:
    ollama_ocupado = False
    ha_confirmacao_pendente = False

    def __init__(self) -> None:
        self.cancelamentos: list[str] = []
        self.confirmacoes: list[str] = []
        self.pendente = False

    def solicitar_exclusao_registro(
        self, tipo: str, registro_id: int, sessao_id: str
    ) -> str:
        self.pendente = True
        return "Esta ação exige confirmação explícita."

    def tem_confirmacao_pendente(self, _: str) -> bool:
        return self.pendente

    def descrever_acao_pendente(self, _: str) -> str:
        return "Confirma apagar nota #3?"

    def confirmar_acao_pendente(self, sessao_id: str) -> str:
        self.confirmacoes.append(sessao_id)
        self.pendente = False
        return "Nota apagada."

    def cancelar_acao_pendente(self, sessao_id: str) -> str:
        self.cancelamentos.append(sessao_id)
        return "Ação cancelada."

    def processar(
        self, pedido: str, sessao_id: str, ao_receber=None
    ) -> str:
        if ao_receber is not None:
            ao_receber("Resposta ")
            ao_receber("completa.")
        return "Resposta completa."


class TarefasFalsas:
    def parar(self) -> None:
        pass


class BotFalso:
    def __init__(self, falhas: int = 0) -> None:
        self.falhas = falhas
        self.chamadas = 0
        self.mensagens: list[dict[str, object]] = []
        self.edicoes: list[dict[str, object]] = []

    async def send_message(self, **dados: object) -> SimpleNamespace:
        self.chamadas += 1
        if self.chamadas <= self.falhas:
            raise RuntimeError("falha simulada")
        self.mensagens.append(dados)
        return SimpleNamespace(message_id=self.chamadas)

    async def send_chat_action(self, **_: object) -> None:
        pass

    async def edit_message_text(self, **dados: object) -> None:
        self.edicoes.append(dados)


def criar_interface(
    agente: AgenteFalso | None = None,
) -> InterfaceTelegram:
    return InterfaceTelegram(
        agente or AgenteFalso(),
        TarefasFalsas(),
        "token-falso",
        1234,
        NotificadorTelegram(1234, espera=lambda _: None),
    )


def executar_com_loop(funcao) -> None:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    try:
        funcao(loop)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


def test_chat_nao_autorizado_e_ignorado_sem_resposta(caplog) -> None:
    interface = criar_interface()
    bot = BotFalso()
    interface._application = SimpleNamespace(bot=bot)
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="conteúdo privado"),
        effective_chat=SimpleNamespace(id=9999),
    )
    context = SimpleNamespace()

    with caplog.at_level(logging.WARNING):
        asyncio.run(interface._receber_texto(update, context))

    assert bot.mensagens == []
    assert interface._fila.qsize() == 0
    assert "9999" in caplog.text
    assert "conteúdo privado" not in caplog.text


def test_mensagem_com_ollama_ocupado_e_enfileirada_com_aviso() -> None:
    interface = criar_interface()
    interface._processando = True
    bot = BotFalso()
    interface._application = SimpleNamespace(bot=bot)
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="outra mensagem"),
        effective_chat=SimpleNamespace(id=1234),
    )

    asyncio.run(interface._receber_texto(update, SimpleNamespace()))

    assert interface._fila.qsize() == 1
    assert bot.mensagens == [
        {
            "chat_id": 1234,
            "text": "um instante, ainda estou processando",
        }
    ]


def test_divisao_de_mensagem_respeita_limite_do_telegram() -> None:
    partes = dividir_mensagem("a" * 5000)

    assert [len(parte) for parte in partes] == [4096, 904]
    assert "".join(partes) == "a" * 5000


def test_conversa_streaming_edita_resposta_sem_enviar_duplicata() -> None:
    interface = criar_interface()
    bot = BotFalso()
    interface._application = SimpleNamespace(bot=bot)

    asyncio.run(
        interface._processar_solicitacao(
            Solicitacao(1234, "Oi!")
        )
    )

    assert bot.mensagens == [
        {"chat_id": 1234, "text": "Resposta "},
    ]
    assert bot.edicoes == [
        {
            "chat_id": 1234,
            "message_id": 1,
            "text": "Resposta completa.",
        }
    ]


def test_divisao_de_mensagem_respeita_caracteres_unicode() -> None:
    partes = dividir_mensagem("🙂" * 2049)

    assert [len(parte.encode("utf-16-le")) // 2 for parte in partes] == [
        4096,
        2,
    ]


def test_notificador_telegram_envia_com_sucesso() -> None:
    bot = BotFalso()
    atrasos: list[float] = []

    def enviar(loop) -> None:
        notificador = NotificadorTelegram(1234, espera=atrasos.append)
        notificador.configurar(bot, loop)
        notificador.notificar("lembrete")

    executar_com_loop(enviar)

    assert bot.chamadas == 1
    assert bot.mensagens[0] == {"chat_id": 1234, "text": "lembrete"}
    assert atrasos == []


def test_notificador_telegram_tenta_novamente_com_espera_crescente() -> None:
    bot = BotFalso(falhas=2)
    atrasos: list[float] = []

    def enviar(loop) -> None:
        notificador = NotificadorTelegram(1234, espera=atrasos.append)
        notificador.configurar(bot, loop)
        notificador.notificar("briefing")

    executar_com_loop(enviar)

    assert bot.chamadas == 3
    assert atrasos == [1, 2]
    assert bot.mensagens[0]["text"] == "briefing"


def test_lembrete_nao_e_marcado_avisado_apos_falha(tmp_path) -> None:
    agora = datetime.now(timezone.utc)
    banco = Banco(tmp_path / "assistente.db", relogio=lambda: agora)
    banco.criar_lembrete("buscar encomenda", agora - timedelta(minutes=1))

    class NotificadorComFalha:
        def notificar(self, _: str) -> None:
            raise RuntimeError("indisponível")

    tarefas = TarefasAgendadas(
        banco,
        60,
        notificador=NotificadorComFalha(),
        relogio=lambda: agora,
    )
    tarefas.verificar_lembretes()

    with banco._conectar() as conexao:
        lembrete = conexao.execute(
            "SELECT avisado, avisado_em FROM lembretes"
        ).fetchone()
    assert lembrete["avisado"] == 0
    assert lembrete["avisado_em"] is None


def test_confirmacao_expira_apos_dois_minutos() -> None:
    confirmacao = ConfirmacaoPendente("id", expira_em=220, sessao_id="1234")

    assert confirmacao.esta_valida(219.99)
    assert not confirmacao.esta_valida(220)


def test_confirmacao_expirada_cancela_acao_e_responde() -> None:
    agente = AgenteFalso()
    interface = criar_interface(agente)
    bot = BotFalso()
    interface._application = SimpleNamespace(bot=bot)
    interface._confirmacoes[1234] = ConfirmacaoPendente(
        "id", expira_em=0, sessao_id="1234"
    )
    update = SimpleNamespace(
        effective_message=SimpleNamespace(text="sim"),
        effective_chat=SimpleNamespace(id=1234),
    )

    asyncio.run(interface._receber_texto(update, SimpleNamespace()))

    assert agente.cancelamentos == ["1234"]
    assert bot.mensagens == [
        {"chat_id": 1234, "text": "A confirmação expirou."}
    ]


def test_confirmacao_sensivel_usa_botoes_e_executa_apos_confirmar() -> None:
    agente = AgenteFalso()
    interface = criar_interface(agente)
    bot = BotFalso()
    interface._application = SimpleNamespace(bot=bot)

    async def fluxo() -> None:
        await interface._processar_solicitacao(
            Solicitacao(1234, "apagar nota #3")
        )
        teclado = bot.mensagens[-1]["reply_markup"]
        callback_data = teclado.inline_keyboard[0][0].callback_data
        consulta = SimpleNamespace(
            message=SimpleNamespace(chat_id=1234),
            data=callback_data,
            answer=lambda: asyncio.sleep(0),
        )
        await interface._receber_confirmacao(
            SimpleNamespace(callback_query=consulta), SimpleNamespace()
        )
        await interface._resolver_confirmacao(interface._fila.get_nowait())

    asyncio.run(fluxo())

    assert bot.mensagens[-1]["text"] == "Nota apagada."
    assert agente.confirmacoes == ["1234"]


def test_comandos_do_bot_sao_mapeados_para_fluxos_existentes() -> None:
    assert mapear_comando("/start") == "ajuda"
    assert mapear_comando("/ajuda") == "ajuda"
    assert mapear_comando("/briefing") == "briefing"
    assert mapear_comando("/checkin") == "checkin"
    assert mapear_comando("/resumo") == "resumo"
    assert mapear_comando("/lembretes@assistente_bot") == "lembretes"
    assert mapear_comando("/notas") == "notas"
    assert mapear_comando("/dados") == "dados"
    assert mapear_comando("/desconhecido") is None


def test_bot_nao_inicia_sem_id_autorizado(monkeypatch, capsys) -> None:
    from src.config import Configuracao
    from src.main import main

    configuracao = Configuracao(
        interface="telegram",
        telegram_bot_token="token-falso",
    )
    monkeypatch.setattr(
        "src.main.Configuracao.carregar", lambda: configuracao
    )

    main([])

    saida = capsys.readouterr().out
    assert "TELEGRAM_ID_AUTORIZADO" in saida
    assert "token-falso" not in saida
