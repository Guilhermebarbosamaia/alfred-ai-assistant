import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from telegram import Bot


def dividir_mensagem(texto: str, limite: int = 4096) -> list[str]:
    if limite < 1:
        raise ValueError("O limite de mensagem deve ser positivo.")
    partes: list[str] = []
    atual: list[str] = []
    unidades = 0
    for caractere in texto:
        tamanho = 2 if ord(caractere) > 0xFFFF else 1
        if tamanho > limite:
            raise ValueError("O limite não comporta um caractere da mensagem.")
        if atual and unidades + tamanho > limite:
            partes.append("".join(atual))
            atual = []
            unidades = 0
        atual.append(caractere)
        unidades += tamanho
    if atual:
        partes.append("".join(atual))
    return partes or [""]


class Notificador(Protocol):
    def notificar(self, texto: str) -> None: ...


class NotificadorTerminal:
    def notificar(self, texto: str) -> None:
        print(f"\n🔔 {texto}", flush=True)


class NotificadorCombinado:
    def __init__(self, notificadores: list[Notificador]) -> None:
        self._notificadores = notificadores

    def notificar(self, texto: str) -> None:
        erros: list[Exception] = []
        for notificador in self._notificadores:
            try:
                notificador.notificar(texto)
            except Exception as erro:
                erros.append(erro)
        if erros:
            raise RuntimeError(
                f"Falha ao enviar aviso por {len(erros)} canal(is)."
            ) from erros[0]


class NotificadorTelegram:
    def __init__(
        self,
        chat_id: int,
        espera: Callable[[float], None] = time.sleep,
    ) -> None:
        self._chat_id = chat_id
        self._espera = espera
        self._bot: Bot | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def configurar(
        self, bot: Bot, loop: asyncio.AbstractEventLoop
    ) -> None:
        self._bot = bot
        self._loop = loop

    def notificar(self, texto: str) -> None:
        if self._bot is None or self._loop is None:
            raise RuntimeError("O notificador Telegram ainda não foi iniciado.")
        if not self._loop.is_running():
            raise RuntimeError("O laço assíncrono do Telegram está encerrado.")
        for parte in dividir_mensagem(texto):
            enviado = False
            for tentativa in range(3):
                try:
                    futuro = asyncio.run_coroutine_threadsafe(
                        self._bot.send_message(
                            chat_id=self._chat_id, text=parte
                        ),
                        self._loop,
                    )
                    futuro.result()
                    enviado = True
                    break
                except Exception as erro:
                    logging.error(
                        "Falha no envio Telegram (tentativa %s/3): %s",
                        tentativa + 1,
                        type(erro).__name__,
                    )
                    if tentativa < 2:
                        self._espera(2**tentativa)
            if not enviado:
                raise RuntimeError(
                    "Não foi possível enviar a notificação pelo Telegram "
                    "após 3 tentativas."
                ) from None


def notificar(texto: str) -> None:
    NotificadorTerminal().notificar(f"LEMBRETE: {texto}")
