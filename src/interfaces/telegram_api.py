"""Cliente síncrono e mínimo da Bot API do Telegram (usado na Cloudflare).

Não depende do python-telegram-bot: usa `httpx` síncrono, que funciona tanto
localmente quanto nos Python Workers. Erros nunca carregam a URL (que contém o
token) nem o corpo das mensagens.
"""

import logging
import traceback
from typing import Any

from src.agendador.notificador import dividir_mensagem

URL_API = "https://api.telegram.org"


class ErroTelegram(RuntimeError):
    """Falha ao chamar a Bot API; a mensagem só tem metadados."""


class FiltroToken(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def filter(self, registro: logging.LogRecord) -> bool:
        if not self._token:
            return True
        mensagem = registro.getMessage()
        if self._token in mensagem:
            registro.msg = mensagem.replace(self._token, "[TOKEN REMOVIDO]")
            registro.args = ()
        if registro.exc_info:
            excecao = "".join(traceback.format_exception(*registro.exc_info))
            if self._token in excecao:
                registro.exc_info = None
                registro.exc_text = None
                registro.msg = "Erro Telegram ocultado para proteger o token."
                registro.args = ()
        return True


def proteger_logs(token: str) -> None:
    """Aplica o `FiltroToken` ao logger raiz e aos clientes HTTP."""
    filtro = FiltroToken(token)
    logging.basicConfig()
    raiz = logging.getLogger()
    raiz.addFilter(filtro)
    for manipulador in raiz.handlers:
        manipulador.addFilter(filtro)
    for nome in ("telegram", "httpx", "httpcore"):
        logger = logging.getLogger(nome)
        logger.addFilter(filtro)
        for manipulador in logger.handlers:
            manipulador.addFilter(filtro)


class ClienteTelegram:
    def __init__(
        self, token: str, http: Any | None = None, timeout: float = 15
    ) -> None:
        if http is None:
            import httpx

            http = httpx.Client(timeout=timeout)
        self._token = token
        self._http = http

    def chamar(self, metodo: str, **dados: object) -> Any:
        try:
            resposta = self._http.post(
                f"{URL_API}/bot{self._token}/{metodo}", json=dados
            )
            corpo = resposta.json()
        except Exception as erro:
            raise ErroTelegram(
                f"Falha ao chamar {metodo}: {type(erro).__name__}"
            ) from None
        if not isinstance(corpo, dict) or not corpo.get("ok"):
            codigo = corpo.get("error_code") if isinstance(corpo, dict) else None
            raise ErroTelegram(f"Telegram recusou {metodo} (código {codigo}).")
        return corpo.get("result")

    def enviar_mensagem(
        self,
        chat_id: int,
        texto: str,
        teclado: dict[str, object] | None = None,
    ) -> int:
        dados: dict[str, object] = {"chat_id": chat_id, "text": texto}
        if teclado is not None:
            dados["reply_markup"] = teclado
        resultado = self.chamar("sendMessage", **dados)
        return int(resultado["message_id"])

    def editar_mensagem(self, chat_id: int, mensagem_id: int, texto: str) -> None:
        self.chamar(
            "editMessageText", chat_id=chat_id, message_id=mensagem_id, text=texto
        )

    def acao_digitando(self, chat_id: int) -> None:
        self.chamar("sendChatAction", chat_id=chat_id, action="typing")

    def responder_callback(self, callback_id: str) -> None:
        self.chamar("answerCallbackQuery", callback_query_id=callback_id)


class NotificadorTelegramHttp:
    """Envia avisos do agendador pela Bot API, sem laço assíncrono."""

    def __init__(
        self, cliente: ClienteTelegram, chat_id: int, tentativas: int = 3
    ) -> None:
        self._cliente = cliente
        self._chat_id = chat_id
        self._tentativas = tentativas

    def notificar(self, texto: str) -> None:
        for parte in dividir_mensagem(texto):
            for tentativa in range(self._tentativas):
                try:
                    self._cliente.enviar_mensagem(self._chat_id, parte)
                    break
                except ErroTelegram as erro:
                    logging.error(
                        "Falha no envio Telegram (tentativa %s/%s): %s",
                        tentativa + 1,
                        self._tentativas,
                        type(erro).__name__,
                    )
            else:
                raise RuntimeError(
                    "Não foi possível enviar a notificação pelo Telegram "
                    f"após {self._tentativas} tentativas."
                )
