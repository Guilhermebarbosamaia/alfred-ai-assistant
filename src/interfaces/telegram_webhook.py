"""Interface do Telegram por webhook, para rodar na Cloudflare.

Cada `Update` chega numa requisição e é tratado até o fim de forma síncrona.
Nada fica em memória entre requisições: confirmações de ações sensíveis e o
check-in em andamento ficam na tabela `estado_conversa` do banco.
"""

import hmac
import logging
import time
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from src.agendador.notificador import dividir_mensagem
from src.agendador.tarefas import TarefasAgendadas
from src.agente.agente import Agente
from src.interfaces.comandos import (
    TEXTO_AJUDA,
    TEXTO_NAO_SUPORTADO,
    estado_lembretes,
    id_exclusao,
    interpretar_confirmacao,
    mapear_comando,
)
from src.interfaces.telegram_api import ClienteTelegram
from src.memoria.banco import Banco

VALIDADE_CONFIRMACAO = timedelta(minutes=2)
VALIDADE_CHECKIN = timedelta(minutes=30)
INTERVALO_EDICAO_SEGUNDOS = 1.0
CARACTERES_POR_EDICAO = 80
_SESSAO_WEBHOOK = "webhook"


class PublicadorStreaming:
    """Publica a resposta do modelo aos poucos, editando a mesma mensagem."""

    def __init__(
        self,
        cliente: ClienteTelegram,
        chat_id: int,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cliente = cliente
        self._chat_id = chat_id
        self._relogio = relogio
        self._texto = ""
        self._publicado = ""
        self._mensagem_id: int | None = None
        self._ultima_edicao = 0.0
        self._falhou = False

    def receber(self, fragmento: str) -> None:
        if self._falhou:
            return
        self._texto += fragmento
        if not self._texto.strip():
            return
        try:
            if self._mensagem_id is None:
                self._mensagem_id = self._cliente.enviar_mensagem(
                    self._chat_id, self._texto
                )
                self._marcar_publicado()
            elif (
                len(self._texto) - len(self._publicado) >= CARACTERES_POR_EDICAO
                and self._relogio() - self._ultima_edicao
                >= INTERVALO_EDICAO_SEGUNDOS
            ):
                self._cliente.editar_mensagem(
                    self._chat_id, self._mensagem_id, self._texto
                )
                self._marcar_publicado()
        except Exception as erro:
            self._falhou = True
            _registrar_erro("publicar resposta em streaming", erro)

    def finalizar(self) -> bool:
        """True se a resposta completa já está publicada no chat."""
        if self._mensagem_id is None or self._falhou:
            return False
        if self._texto != self._publicado:
            try:
                self._cliente.editar_mensagem(
                    self._chat_id, self._mensagem_id, self._texto
                )
            except Exception as erro:
                _registrar_erro("publicar resposta em streaming", erro)
                return False
        return True

    def _marcar_publicado(self) -> None:
        self._publicado = self._texto
        self._ultima_edicao = self._relogio()


class InterfaceTelegramWebhook:
    def __init__(
        self,
        agente: Agente,
        tarefas: TarefasAgendadas,
        banco: Banco,
        cliente: ClienteTelegram,
        chat_id_autorizado: int,
        segredo: str,
        relogio_monotonico: Callable[[], float] = time.monotonic,
    ) -> None:
        self._agente = agente
        self._tarefas = tarefas
        self._banco = banco
        self._cliente = cliente
        self._chat_id_autorizado = chat_id_autorizado
        self._segredo = segredo
        self._relogio_monotonico = relogio_monotonico

    # Entrada -----------------------------------------------------------------

    def segredo_valido(self, recebido: str | None) -> bool:
        return bool(self._segredo) and hmac.compare_digest(
            (recebido or "").encode(), self._segredo.encode()
        )

    def processar(self, update: Any, segredo_recebido: str | None) -> int:
        """Trata um `Update` e devolve o status HTTP da resposta ao Telegram."""
        if not self.segredo_valido(segredo_recebido):
            logging.warning("Webhook Telegram recusado: segredo inválido.")
            return 403
        if not isinstance(update, dict):
            return 400
        update_id = update.get("update_id")
        if isinstance(update_id, int) and not self._registrar_update(update_id):
            logging.info("Update %s repetido ignorado.", update_id)
            return 200
        try:
            self._despachar(update)
        except Exception as erro:
            _registrar_erro("processar update", erro)
            chat_id = self._chat_do_update(update)
            if chat_id == self._chat_id_autorizado:
                try:
                    self._enviar(chat_id, "algo deu errado, tente de novo")
                except Exception as erro_envio:
                    _registrar_erro("responder erro de processamento", erro_envio)
        return 200

    def _registrar_update(self, update_id: int) -> bool:
        """Guarda o último update tratado; False se este já foi visto."""
        estado = self._banco.estado_conversa(_SESSAO_WEBHOOK, "ultimo_update")
        if estado is not None and int(estado["dados"].get("id", -1)) >= update_id:
            return False
        self._banco.salvar_estado_conversa(
            _SESSAO_WEBHOOK,
            "ultimo_update",
            {"id": update_id},
            self._banco.agora_utc() + timedelta(days=3650),
        )
        return True

    @staticmethod
    def _chat_id(mensagem: object) -> int | None:
        if not isinstance(mensagem, dict):
            return None
        chat = mensagem.get("chat")
        if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
            return None
        return int(chat["id"])

    def _chat_do_update(self, update: dict[str, Any]) -> int | None:
        consulta = update.get("callback_query")
        if isinstance(consulta, dict):
            return self._chat_id(consulta.get("message"))
        return self._chat_id(update.get("message"))

    def _autorizado(self, chat_id: int) -> bool:
        if chat_id == self._chat_id_autorizado:
            return True
        logging.warning("Mensagem ignorada de chat não autorizado: %s", chat_id)
        return False

    def _despachar(self, update: dict[str, Any]) -> None:
        consulta = update.get("callback_query")
        if isinstance(consulta, dict):
            self._receber_confirmacao(consulta)
            return
        mensagem = update.get("message")
        chat_id = self._chat_id(mensagem)
        if chat_id is None or not self._autorizado(chat_id):
            return
        texto = mensagem.get("text")
        if not isinstance(texto, str):
            self._enviar(chat_id, TEXTO_NAO_SUPORTADO)
            return
        texto = texto.strip()
        if texto:
            self._receber_texto(chat_id, texto)

    # Texto -------------------------------------------------------------------

    def _receber_texto(self, chat_id: int, texto: str) -> None:
        sessao_id = str(chat_id)
        comando = texto.startswith("/")
        if not comando and self._responder_checkin(chat_id, texto):
            return
        confirmacao = self._banco.estado_conversa(sessao_id, "confirmacao_telegram")
        if confirmacao is not None and not comando:
            if confirmacao["expirado"]:
                self._expirar_confirmacao(chat_id)
                return
            escolha = interpretar_confirmacao(texto)
            if escolha is None:
                self._enviar(
                    chat_id, "Responda “sim” ou “não” para a confirmação pendente."
                )
                return
            self._resolver_confirmacao(chat_id, escolha)
            return
        self._acao_digitando(chat_id)
        partes = texto.split()
        if comando:
            resposta = self._executar_comando(chat_id, partes[0], partes[1:])
            if resposta:
                self._enviar(chat_id, resposta)
            return
        if partes[0].casefold() == "apagar":
            validado = id_exclusao(partes[1:])
            if isinstance(validado, str):
                self._enviar(chat_id, validado)
                return
            tipo, registro_id = validado
            resposta = self._agente.solicitar_exclusao_registro(
                tipo, registro_id, sessao_id
            )
            self._enviar_com_confirmacao(chat_id, resposta)
            return
        publicador = PublicadorStreaming(
            self._cliente, chat_id, self._relogio_monotonico
        )
        resposta = self._agente.processar(texto, sessao_id, publicador.receber)
        if not publicador.finalizar():
            self._enviar_com_confirmacao(chat_id, resposta)

    def _executar_comando(
        self, chat_id: int, comando: str, argumentos: list[str]
    ) -> str | None:
        acao = mapear_comando(comando)
        if acao is None:
            return "Comando não reconhecido. Use /ajuda."
        if acao == "ajuda":
            return TEXTO_AJUDA
        if acao == "briefing":
            self._tarefas.executar_briefing()
            return None
        if acao == "checkin":
            return self.iniciar_checkin(manual=True)
        if acao == "resumo":
            self._tarefas.executar_resumo()
            return None
        if acao == "lembretes":
            estado = estado_lembretes(argumentos)
            if estado is None:
                return "Uso: /lembretes [pendentes|concluidos|atrasados]"
            return self._agente.listar_lembretes(estado)
        if acao == "notas":
            return self._agente.listar_notas()
        return self._agente.visualizar_dados()

    # Confirmação de ações sensíveis ----------------------------------------

    def _enviar_com_confirmacao(self, chat_id: int, resposta: str) -> None:
        sessao_id = str(chat_id)
        if not self._agente.tem_confirmacao_pendente(sessao_id):
            self._enviar(chat_id, resposta)
            return
        referencia = uuid.uuid4().hex
        self._banco.salvar_estado_conversa(
            sessao_id,
            "confirmacao_telegram",
            {"referencia": referencia},
            self._banco.agora_utc() + VALIDADE_CONFIRMACAO,
        )
        teclado = {
            "inline_keyboard": [
                [
                    {"text": "Confirmar", "callback_data": f"confirmar:{referencia}"},
                    {"text": "Cancelar", "callback_data": f"cancelar:{referencia}"},
                ]
            ]
        }
        self._cliente.enviar_mensagem(
            chat_id, self._agente.descrever_acao_pendente(sessao_id), teclado
        )

    def _receber_confirmacao(self, consulta: dict[str, Any]) -> None:
        chat_id = self._chat_id(consulta.get("message"))
        if chat_id is None or not self._autorizado(chat_id):
            return
        try:
            self._cliente.responder_callback(str(consulta.get("id", "")))
        except Exception as erro:
            _registrar_erro("responder callback", erro)
        partes = str(consulta.get("data") or "").split(":", 1)
        estado = self._banco.estado_conversa(str(chat_id), "confirmacao_telegram")
        if (
            len(partes) != 2
            or partes[0] not in {"confirmar", "cancelar"}
            or estado is None
            or partes[1] != estado["dados"].get("referencia")
        ):
            return
        if estado["expirado"]:
            self._expirar_confirmacao(chat_id)
            return
        self._resolver_confirmacao(chat_id, partes[0] == "confirmar")

    def _resolver_confirmacao(self, chat_id: int, confirmar: bool) -> None:
        sessao_id = str(chat_id)
        self._banco.remover_estado_conversa(sessao_id, "confirmacao_telegram")
        if confirmar:
            resposta = self._agente.confirmar_acao_pendente(sessao_id)
        else:
            resposta = self._agente.cancelar_acao_pendente(sessao_id)
        self._enviar(chat_id, resposta)

    def _expirar_confirmacao(self, chat_id: int) -> None:
        sessao_id = str(chat_id)
        self._banco.remover_estado_conversa(sessao_id, "confirmacao_telegram")
        self._agente.cancelar_acao_pendente(sessao_id)
        self._enviar(chat_id, "A confirmação expirou.")

    # Check-in conversado ----------------------------------------------------

    def iniciar_checkin(self, manual: bool = True) -> str | None:
        """Começa o check-in de hoje, uma pergunta por mensagem.

        Manual: devolve o texto a enviar. Agendado: envia a pergunta e devolve
        None (e fica em silêncio se não houver o que perguntar).
        """
        chat_id = self._chat_id_autorizado
        preparado = self._tarefas.preparar_checkin(manual)
        if isinstance(preparado, str):
            return preparado if manual else None
        dia, perguntas = preparado
        if not perguntas:
            texto = self._tarefas.concluir_checkin(dia, [])
            if manual:
                return texto
            self._enviar(chat_id, texto)
            return None
        self._salvar_checkin(
            chat_id,
            {
                "dia": dia.isoformat(),
                "habitos": [
                    [int(habito["id"]), str(habito["nome"]), pergunta]
                    for habito, pergunta in perguntas
                ],
                "pendencias": [],
            },
        )
        self._banco.registrar_execucao("checkin", dia.isoformat(), "enviado")
        primeira = perguntas[0][1]
        if manual:
            return primeira
        self._enviar(chat_id, f"CHECK-IN NOTURNO:\n{primeira}")
        return None

    def _salvar_checkin(self, chat_id: int, dados: dict[str, object]) -> None:
        self._banco.salvar_estado_conversa(
            str(chat_id),
            "checkin",
            dados,
            self._banco.agora_utc() + VALIDADE_CHECKIN,
        )

    def _responder_checkin(self, chat_id: int, texto: str) -> bool:
        sessao_id = str(chat_id)
        estado = self._banco.estado_conversa(sessao_id, "checkin")
        if estado is None:
            return False
        if estado["expirado"]:
            self._banco.remover_estado_conversa(sessao_id, "checkin")
            return False
        dados = estado["dados"]
        restantes = list(dados.get("habitos", []))
        if not restantes:
            self._banco.remover_estado_conversa(sessao_id, "checkin")
            return False
        dia = date.fromisoformat(str(dados["dia"]))
        habito_id, nome, _ = restantes.pop(0)
        pendencias = list(dados.get("pendencias", []))
        if not self._tarefas.registrar_resposta_checkin(dia, int(habito_id), texto):
            pendencias.append(str(nome))
        if restantes:
            self._salvar_checkin(
                chat_id,
                {"dia": dia.isoformat(), "habitos": restantes, "pendencias": pendencias},
            )
            self._enviar(chat_id, str(restantes[0][2]))
            return True
        self._banco.remover_estado_conversa(sessao_id, "checkin")
        self._enviar(chat_id, self._tarefas.concluir_checkin(dia, pendencias))
        return True

    # Saída -------------------------------------------------------------------

    def _acao_digitando(self, chat_id: int) -> None:
        try:
            self._cliente.acao_digitando(chat_id)
        except Exception as erro:
            _registrar_erro("enviar ação digitando", erro)

    def _enviar(self, chat_id: int, texto: str) -> None:
        for parte in dividir_mensagem(texto):
            self._cliente.enviar_mensagem(chat_id, parte)


def _registrar_erro(operacao: str, erro: Exception) -> None:
    logging.error("Falha em %s: %s", operacao, type(erro).__name__)
