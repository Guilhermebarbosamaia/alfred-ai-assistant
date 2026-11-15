import logging
import time
from collections.abc import Sequence
from threading import Condition


class ErroProvedorOllama(RuntimeError):
    """Falha de comunicação com o servidor Ollama local."""


def _nanosegundos_em_ms(valor: int | None) -> float | None:
    return round(valor / 1_000_000, 3) if valor is not None else None


class ProvedorOllama:
    def __init__(
        self,
        host: str,
        modelo: str,
        timeout: float,
        keep_alive: str = "30m",
        num_ctx: int = 4096,
        num_predict: int = 256,
    ) -> None:
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
        self._keep_alive = keep_alive
        self._num_ctx = num_ctx
        self._num_predict = num_predict
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
        return self.gerar_com_metricas(mensagens, esquema)

    def gerar_com_metricas(
        self,
        mensagens: Sequence[dict[str, str]],
        esquema: dict[str, object],
        tentativa: int = 1,
    ) -> str:
        inicio_fila = time.perf_counter()
        with self._condicao:
            self._conversas_aguardando += 1
            try:
                while self._ocupado:
                    self._condicao.wait()
                self._ocupado = True
            finally:
                self._conversas_aguardando -= 1
        espera_fila_ms = (time.perf_counter() - inicio_fila) * 1000
        try:
            return self._chamar(mensagens, esquema, espera_fila_ms, tentativa)
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
            return self._chamar(mensagens, esquema, 0.0, 1)
        finally:
            with self._condicao:
                self._ocupado = False
                self._condicao.notify_all()

    def _chamar(
        self,
        mensagens: Sequence[dict[str, str]],
        esquema: dict[str, object],
        espera_fila_ms: float,
        tentativa: int,
    ) -> str:
        try:
            resposta = self._cliente.chat(
                model=self._modelo,
                messages=list(mensagens),
                format=esquema,
                keep_alive=self._keep_alive,
                options={
                    "temperature": 0.1,
                    "num_ctx": self._num_ctx,
                    "num_predict": self._num_predict,
                },
            )
        except (self._http_error, self._response_error) as erro:
            logging.warning(
                "Falha na chamada Ollama: fila_ms=%.3f tentativa=%d erro=%s",
                espera_fila_ms,
                tentativa,
                type(erro).__name__,
            )
            raise ErroProvedorOllama(
                "Não foi possível conversar com o Ollama local. "
                "Verifique se ele está em execução e se o modelo foi baixado."
            ) from erro

        logging.info(
            "Métricas Ollama: tentativa=%d fila_ms=%.3f "
            "total_duration_ms=%s load_duration_ms=%s "
            "prompt_eval_count=%s prompt_eval_duration_ms=%s "
            "eval_count=%s eval_duration_ms=%s",
            tentativa,
            espera_fila_ms,
            _nanosegundos_em_ms(resposta.total_duration),
            _nanosegundos_em_ms(resposta.load_duration),
            resposta.prompt_eval_count,
            _nanosegundos_em_ms(resposta.prompt_eval_duration),
            resposta.eval_count,
            _nanosegundos_em_ms(resposta.eval_duration),
        )
        conteudo = resposta.message.content
        if not conteudo.strip():
            raise ErroProvedorOllama("O Ollama retornou uma resposta vazia.")
        return conteudo
