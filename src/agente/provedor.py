from collections.abc import Sequence
from threading import Condition


class ErroProvedorOllama(RuntimeError):
    """Falha de comunicação com o servidor Ollama local."""


class ProvedorOllama:
    def __init__(self, host: str, modelo: str, timeout: float) -> None:
        try:
            from httpx import HTTPError
            from ollama import Client, ResponseError
        except ImportError as erro:
            raise RuntimeError(
                "Dependências ausentes. Instale os pacotes de requirements.txt."
            ) from erro

        self._http_error = HTTPError
        self._response_error = ResponseError
        self._cliente = Client(host=str(host), timeout=timeout)
        self._modelo = modelo
        self._condicao = Condition()
        self._ocupado = False
        self._conversas_aguardando = 0

    @property
    def ocupado(self) -> bool:
        with self._condicao:
            return self._ocupado

    def gerar(
        self, mensagens: Sequence[dict[str, str]], esquema: dict[str, object]
    ) -> str:
        with self._condicao:
            self._conversas_aguardando += 1
            try:
                while self._ocupado:
                    self._condicao.wait()
                self._ocupado = True
            finally:
                self._conversas_aguardando -= 1
        try:
            return self._chamar(mensagens, esquema)
        finally:
            with self._condicao:
                self._ocupado = False
                self._condicao.notify_all()

    def gerar_agendado(
        self, mensagens: Sequence[dict[str, str]], esquema: dict[str, object]
    ) -> str:
        with self._condicao:
            if self._ocupado or self._conversas_aguardando:
                raise ErroProvedorOllama(
                    "O Ollama está ocupado com uma conversa; use o briefing fixo."
                )
            self._ocupado = True
        try:
            return self._chamar(mensagens, esquema)
        finally:
            with self._condicao:
                self._ocupado = False
                self._condicao.notify_all()

    def _chamar(
        self, mensagens: Sequence[dict[str, str]], esquema: dict[str, object]
    ) -> str:
        try:
            resposta = self._cliente.chat(
                model=self._modelo,
                messages=list(mensagens),
                format=esquema,
                options={"temperature": 0.1},
            )
        except (self._http_error, self._response_error) as erro:
            raise ErroProvedorOllama(
                "Não foi possível conversar com o Ollama local. "
                "Verifique se ele está em execução e se o modelo foi baixado."
            ) from erro

        conteudo = resposta.message.content
        if not conteudo.strip():
            raise ErroProvedorOllama("O Ollama retornou uma resposta vazia.")
        return conteudo
