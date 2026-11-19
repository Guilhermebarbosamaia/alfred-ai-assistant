import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Literal, TypeVar

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.agendador.notificador import NotificadorTelegram, dividir_mensagem
from src.agendador.tarefas import TarefasAgendadas
from src.agente.agente import Agente
from src.interfaces.comandos import (
    COMANDOS,
    DESCRICOES_COMANDOS,
    TEXTO_AJUDA,
    TEXTO_NAO_SUPORTADO,
    estado_lembretes,
    id_exclusao,
    interpretar_confirmacao,
    mapear_comando,
)
from src.interfaces.telegram_api import FiltroToken, proteger_logs  # noqa: F401 (reexportado)

T = TypeVar("T")
INTERVALO_DIGITANDO_SEGUNDOS = 4


@dataclass(frozen=True)
class ConfirmacaoPendente:
    referencia: str
    expira_em: float
    sessao_id: str

    def esta_valida(self, agora: float | None = None) -> bool:
        return (agora if agora is not None else time.monotonic()) < self.expira_em


@dataclass
class RespostaCheckin:
    evento: threading.Event
    texto: str = ""


@dataclass(frozen=True)
class Solicitacao:
    chat_id: int
    texto: str
    tipo: Literal["mensagem", "confirmar", "cancelar"] = "mensagem"


class InterfaceTelegram:
    def __init__(
        self,
        agente: Agente,
        tarefas: TarefasAgendadas,
        token: str,
        chat_id_autorizado: int,
        notificador: NotificadorTelegram,
    ) -> None:
        self._agente = agente
        self._tarefas = tarefas
        self._token = token
        self._chat_id_autorizado = chat_id_autorizado
        self._notificador = notificador
        self._fila: asyncio.Queue[Solicitacao] = asyncio.Queue()
        self._confirmacoes: dict[int, ConfirmacaoPendente] = {}
        self._respostas_checkin: dict[int, RespostaCheckin] = {}
        self._trava_checkin = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._application: Application | None = None
        self._tarefa_fila: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._tarefa_inicio_tarefas: asyncio.Task[None] | None = None
        self._pronto = threading.Event()
        self._erro_inicio: str | None = None
        self._parar_solicitado = threading.Event()
        self._processando = False

    def executar(self) -> None:
        try:
            self._proteger_logs()
            application = (
                Application.builder()
                .token(self._token)
                .post_init(self._iniciar)
                .post_shutdown(self._encerrar)
                .build()
            )
            self._application = application
            application.add_handler(
                CommandHandler(
                    list(COMANDOS),
                    self._receber_comando,
                )
            )
            application.add_handler(
                MessageHandler(filters.COMMAND, self._receber_comando)
            )
            application.add_handler(
                CallbackQueryHandler(self._receber_confirmacao)
            )
            application.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    self._receber_texto,
                )
            )
            application.add_handler(
                MessageHandler(filters.ALL & ~filters.TEXT, self._nao_suportado)
            )
            application.add_error_handler(self._registrar_erro_handler)
            application.run_polling(stop_signals=None, bootstrap_retries=-1)
        except Exception as erro:
            self._erro_inicio = type(erro).__name__
            logging.error(
                "Falha ao iniciar a interface Telegram: %s",
                type(erro).__name__,
            )
            self._pronto.set()
            if not self._parar_solicitado.is_set():
                print(
                    "Não foi possível iniciar o bot do Telegram. "
                    "Verifique a configuração e a conexão."
                )

    def _proteger_logs(self) -> None:
        proteger_logs(self._token)

    def iniciar_em_thread(self) -> threading.Thread:
        self._thread = threading.Thread(
            target=self.executar, name="interface-telegram", daemon=True
        )
        self._thread.start()
        return self._thread

    def aguardar_inicio(self, timeout: float = 10) -> bool:
        return self._pronto.wait(timeout) and self._erro_inicio is None

    def parar(self) -> None:
        self._parar_solicitado.set()
        application = self._application
        loop = self._loop
        if application is not None and loop is not None and loop.is_running():
            loop.call_soon_threadsafe(application.stop_running)

    async def _iniciar(self, application: Application) -> None:
        self._loop = asyncio.get_running_loop()
        self._notificador.configurar(application.bot, self._loop)
        self._tarefa_fila = asyncio.create_task(self._processar_fila())
        try:
            await application.bot.set_my_commands(
                [
                    BotCommand(comando, descricao)
                    for comando, descricao in DESCRICOES_COMANDOS
                ]
            )
        except Exception as erro:
            self._registrar_erro("registrar comandos no BotFather", erro)
        self._tarefa_inicio_tarefas = asyncio.create_task(
            asyncio.to_thread(self._tarefas.iniciar)
        )
        self._pronto.set()

    async def _encerrar(self, _: Application) -> None:
        if self._tarefa_fila is not None:
            self._tarefa_fila.cancel()
            await asyncio.gather(self._tarefa_fila, return_exceptions=True)
        with self._trava_checkin:
            for resposta in self._respostas_checkin.values():
                resposta.evento.set()
        if self._tarefa_inicio_tarefas is not None:
            await asyncio.gather(
                self._tarefa_inicio_tarefas, return_exceptions=True
            )
        await asyncio.to_thread(self._tarefas.parar)

    async def _receber_comando(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        mensagem = getattr(update, "effective_message", None)
        chat = getattr(update, "effective_chat", None)
        if mensagem is None or chat is None:
            return
        if not self._autorizado(int(chat.id)):
            return
        comando = mapear_comando(str(mensagem.text).split()[0])
        if comando is None:
            await self._enviar(int(chat.id), "Comando não reconhecido. Use /ajuda.")
            return
        argumentos = str(mensagem.text).split()[1:]
        await self._enfileirar(int(chat.id), " ".join(argumentos), comando)

    async def _receber_texto(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        mensagem = getattr(update, "effective_message", None)
        chat = getattr(update, "effective_chat", None)
        if mensagem is None or chat is None:
            return
        chat_id = int(chat.id)
        if not self._autorizado(chat_id):
            return
        texto = str(mensagem.text).strip()
        if not texto:
            return
        resposta = self._receber_resposta_checkin(chat_id, texto)
        if resposta:
            return
        confirmacao = self._confirmacoes.get(chat_id)
        if confirmacao is not None:
            if not confirmacao.esta_valida():
                self._expirar_confirmacao(chat_id, confirmacao)
                await self._enviar(chat_id, "A confirmação expirou.")
                return
            escolha = self._interpretar_confirmacao(texto)
            if escolha is None:
                await self._enviar(
                    chat_id, "Responda “sim” ou “não” para a confirmação pendente."
                )
                return
            await self._enfileirar(
                chat_id, texto, "confirmar" if escolha else "cancelar"
            )
            return
        await self._enfileirar(chat_id, texto)

    async def _receber_confirmacao(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        consulta = getattr(update, "callback_query", None)
        mensagem = getattr(consulta, "message", None)
        if consulta is None or mensagem is None:
            return
        chat_id = int(mensagem.chat_id)
        if not self._autorizado(chat_id):
            return
        await consulta.answer()
        dados = str(consulta.data or "")
        partes = dados.split(":", 1)
        confirmacao = self._confirmacoes.get(chat_id)
        if (
            len(partes) != 2
            or partes[0] not in {"confirmar", "cancelar"}
            or confirmacao is None
            or partes[1] != confirmacao.referencia
        ):
            return
        if not confirmacao.esta_valida():
            self._expirar_confirmacao(chat_id, confirmacao)
            await self._enviar(chat_id, "A confirmação expirou.")
            return
        tipo = "confirmar" if partes[0] == "confirmar" else "cancelar"
        await self._enfileirar(chat_id, "", tipo)

    async def _nao_suportado(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = getattr(update, "effective_chat", None)
        if chat is None or not self._autorizado(int(chat.id)):
            return
        await self._enviar(
            int(chat.id),
            TEXTO_NAO_SUPORTADO,
        )

    async def _enfileirar(
        self,
        chat_id: int,
        texto: str,
        comando: str | None = None,
    ) -> None:
        ocupado = self._fila.qsize() > 0 or self._processando
        tipo: Literal["mensagem", "confirmar", "cancelar"] = (
            comando if comando in {"confirmar", "cancelar"} else "mensagem"
        )
        conteudo = (
            f"/{comando} {texto}".strip()
            if comando and tipo == "mensagem"
            else texto
        )
        await self._fila.put(Solicitacao(chat_id, conteudo, tipo))
        if ocupado:
            await self._enviar(chat_id, "um instante, ainda estou processando")

    async def _processar_fila(self) -> None:
        while True:
            solicitacao = await self._fila.get()
            self._processando = True
            try:
                await self._processar_solicitacao(solicitacao)
            except Exception as erro:
                self._registrar_erro("processar mensagem", erro)
                try:
                    await self._enviar(
                        solicitacao.chat_id, "algo deu errado, tente de novo"
                    )
                except Exception as erro_envio:
                    self._registrar_erro(
                        "responder erro de processamento", erro_envio
                    )
            finally:
                self._processando = False
                self._fila.task_done()

    async def _processar_solicitacao(self, solicitacao: Solicitacao) -> None:
        await self._enviar_acao_digitando(solicitacao.chat_id)
        if solicitacao.tipo in {"confirmar", "cancelar"}:
            await self._resolver_confirmacao(solicitacao)
            return
        partes = solicitacao.texto.split()
        if partes and partes[0].startswith("/"):
            executar_comando = self._executar_comando(
                solicitacao.chat_id, partes[0], partes[1:]
            )
            if mapear_comando(partes[0]) in {"briefing", "resumo"}:
                resposta = await self._aguardar_com_digitando(
                    solicitacao.chat_id, executar_comando
                )
            else:
                resposta = await executar_comando
            if resposta:
                await self._enviar(solicitacao.chat_id, resposta)
            return
        if partes and partes[0].casefold() == "apagar":
            resposta = await self._solicitar_exclusao(
                solicitacao.chat_id, partes[1:]
            )
            await self._enviar_resposta_com_confirmacao(
                solicitacao.chat_id, resposta
            )
            return
        resposta, enviada_por_streaming = await self._executar_com_agente(
            solicitacao
        )
        if not enviada_por_streaming:
            await self._enviar_resposta_com_confirmacao(
                solicitacao.chat_id, resposta
            )

    async def _solicitar_exclusao(
        self, chat_id: int, argumentos: list[str]
    ) -> str:
        validado = id_exclusao(argumentos)
        if isinstance(validado, str):
            return validado
        tipo, registro_id = validado
        return await asyncio.to_thread(
            self._agente.solicitar_exclusao_registro,
            tipo,
            registro_id,
            str(chat_id),
        )

    async def _enviar_resposta_com_confirmacao(
        self, chat_id: int, resposta: str
    ) -> None:
        sessao_id = str(chat_id)
        if not self._agente.tem_confirmacao_pendente(sessao_id):
            await self._enviar(chat_id, resposta)
            return
        referencia = uuid.uuid4().hex
        self._confirmacoes[chat_id] = ConfirmacaoPendente(
            referencia, time.monotonic() + 120, sessao_id
        )
        teclado = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Confirmar", callback_data=f"confirmar:{referencia}"
                    ),
                    InlineKeyboardButton(
                        "Cancelar", callback_data=f"cancelar:{referencia}"
                    ),
                ]
            ]
        )
        await self._enviar(
            chat_id,
            self._agente.descrever_acao_pendente(sessao_id),
            reply_markup=teclado,
        )

    async def _executar_com_agente(
        self, solicitacao: Solicitacao
    ) -> tuple[str, bool]:
        loop = asyncio.get_running_loop()
        fragmentos: asyncio.Queue[str | None] = asyncio.Queue()

        def publicar_fragmento(fragmento: str) -> None:
            loop.call_soon_threadsafe(fragmentos.put_nowait, fragmento)

        publicador = asyncio.create_task(
            self._publicar_resposta_streaming(solicitacao.chat_id, fragmentos)
        )
        try:
            resposta = await self._aguardar_com_digitando(
                solicitacao.chat_id,
                asyncio.to_thread(
                    self._agente.processar,
                    solicitacao.texto,
                    str(solicitacao.chat_id),
                    publicar_fragmento,
                ),
            )
        except BaseException:
            publicador.cancel()
            await asyncio.gather(publicador, return_exceptions=True)
            raise
        await fragmentos.put(None)
        enviada = await publicador
        return resposta, enviada

    async def _publicar_resposta_streaming(
        self, chat_id: int, fragmentos: asyncio.Queue[str | None]
    ) -> bool:
        try:
            primeiro = await fragmentos.get()
            if primeiro is None:
                return False
            texto = primeiro
            if self._application is None:
                raise RuntimeError("O cliente Telegram ainda não foi iniciado.")
            mensagem = await self._application.bot.send_message(
                chat_id=chat_id, text=texto
            )
            texto_publicado = texto
            mensagem_id = mensagem.message_id

            while True:
                try:
                    fragmento = await asyncio.wait_for(
                        fragmentos.get(), timeout=0.8
                    )
                except TimeoutError:
                    fragmento = ""
                if fragmento is None:
                    if texto != texto_publicado:
                        await self._application.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=mensagem_id,
                            text=texto,
                        )
                    return True
                if fragmento:
                    texto += fragmento
                if texto != texto_publicado and (
                    not fragmento or len(texto) - len(texto_publicado) >= 80
                ):
                    await self._application.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=mensagem_id,
                        text=texto,
                    )
                    texto_publicado = texto
        except Exception as erro:
            self._registrar_erro("publicar resposta em streaming", erro)
            return False

    async def _aguardar_com_digitando(
        self, chat_id: int, operacao: Awaitable[T]
    ) -> T:
        tarefa = asyncio.ensure_future(operacao)
        while not tarefa.done():
            await asyncio.sleep(INTERVALO_DIGITANDO_SEGUNDOS)
            if not tarefa.done():
                await self._enviar_acao_digitando(chat_id)
        return await tarefa

    async def _executar_comando(
        self, chat_id: int, comando: str, argumentos: list[str]
    ) -> str | None:
        acao = mapear_comando(comando)
        if acao == "ajuda":
            return TEXTO_AJUDA
        if acao == "briefing":
            await asyncio.to_thread(self._tarefas.executar_briefing)
            return None
        if acao == "checkin":
            return await asyncio.to_thread(
                self._tarefas.executar_checkin,
                True,
                self.responder_checkin,
            )
        if acao == "resumo":
            await asyncio.to_thread(self._tarefas.executar_resumo)
            return None
        if acao == "lembretes":
            estado = estado_lembretes(argumentos)
            if estado is None:
                return "Uso: /lembretes [pendentes|concluidos|atrasados]"
            return await asyncio.to_thread(self._agente.listar_lembretes, estado)
        if acao == "notas":
            return await asyncio.to_thread(self._agente.listar_notas)
        if acao == "dados":
            return await asyncio.to_thread(self._agente.visualizar_dados)
        return f"Comando não reconhecido. Use /ajuda. ({chat_id})"

    async def _resolver_confirmacao(self, solicitacao: Solicitacao) -> None:
        confirmacao = self._confirmacoes.pop(solicitacao.chat_id, None)
        if confirmacao is None:
            await self._enviar(solicitacao.chat_id, "Não há confirmação pendente.")
            return
        if not confirmacao.esta_valida():
            self._expirar_confirmacao(solicitacao.chat_id, confirmacao)
            await self._enviar(solicitacao.chat_id, "A confirmação expirou.")
            return
        if solicitacao.tipo == "confirmar":
            resposta = await asyncio.to_thread(
                self._agente.confirmar_acao_pendente, confirmacao.sessao_id
            )
        else:
            resposta = await asyncio.to_thread(
                self._agente.cancelar_acao_pendente, confirmacao.sessao_id
            )
        await self._enviar(solicitacao.chat_id, resposta)

    def responder_checkin(self, pergunta: str) -> str:
        if self._loop is None:
            raise RuntimeError("A interface Telegram ainda não foi iniciada.")
        resposta = RespostaCheckin(threading.Event())
        with self._trava_checkin:
            self._respostas_checkin[self._chat_id_autorizado] = resposta
        self._notificador.notificar(pergunta)
        resposta.evento.wait(120)
        with self._trava_checkin:
            atual = self._respostas_checkin.get(self._chat_id_autorizado)
            if atual is resposta:
                self._respostas_checkin.pop(self._chat_id_autorizado, None)
        return resposta.texto

    def _receber_resposta_checkin(self, chat_id: int, texto: str) -> bool:
        with self._trava_checkin:
            resposta = self._respostas_checkin.get(chat_id)
            if resposta is None:
                return False
            resposta.texto = texto
            resposta.evento.set()
            return True

    @staticmethod
    def _interpretar_confirmacao(texto: str) -> bool | None:
        return interpretar_confirmacao(texto)

    def _expirar_confirmacao(
        self, chat_id: int, confirmacao: ConfirmacaoPendente
    ) -> None:
        self._confirmacoes.pop(chat_id, None)
        self._agente.cancelar_acao_pendente(confirmacao.sessao_id)

    def _autorizado(self, chat_id: int) -> bool:
        if chat_id == self._chat_id_autorizado:
            return True
        logging.warning("Mensagem ignorada de chat não autorizado: %s", chat_id)
        return False

    async def _enviar_acao_digitando(self, chat_id: int) -> None:
        if self._application is not None:
            try:
                await self._application.bot.send_chat_action(
                    chat_id=chat_id, action=ChatAction.TYPING
                )
            except Exception as erro:
                self._registrar_erro("enviar ação digitando", erro)

    async def _enviar(
        self, chat_id: int, texto: str, **opcoes: object
    ) -> None:
        if self._application is None:
            raise RuntimeError("O cliente Telegram ainda não foi iniciado.")
        for parte in dividir_mensagem(texto):
            await self._application.bot.send_message(
                chat_id=chat_id, text=parte, **opcoes
            )

    async def _registrar_erro_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        erro = getattr(context, "error", None)
        self._registrar_erro(
            "handler Telegram", erro or RuntimeError("erro desconhecido")
        )
        chat = getattr(update, "effective_chat", None)
        if chat is not None and self._autorizado(int(chat.id)):
            try:
                await self._enviar(
                    int(chat.id), "algo deu errado, tente de novo"
                )
            except Exception as erro_envio:
                self._registrar_erro("responder erro do handler", erro_envio)

    @staticmethod
    def _registrar_erro(operacao: str, erro: Exception) -> None:
        logging.error("Falha em %s: %s", operacao, type(erro).__name__)
