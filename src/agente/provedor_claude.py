import logging
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from src.agente.erros import ErroProvedor

if TYPE_CHECKING:
    from src.agente.uso import ControleUso


class ErroProvedorClaude(ErroProvedor):
    """Falha de comunicação com a API do Claude."""


_MENSAGEM_SEM_CONEXAO = (
    "Não consegui falar com a API do Claude. Verifique sua conexão com a "
    "internet."
)
_MENSAGEM_INDISPONIVEL = (
    "A API do Claude está instável no momento. Tente de novo em alguns minutos."
)


class ProvedorClaude:
    """Provedor que usa a API do Claude pelo SDK oficial `anthropic`.

    A API aceita chamadas simultâneas, então não há fila como no Ollama:
    `ocupado` é sempre falso e `gerar_agendado` chama a API diretamente.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str,
        timeout: float = 60,
        max_tokens: int = 256,
        max_retries: int = 2,
        controle_uso: "ControleUso | None" = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as erro:
            raise RuntimeError(
                "Dependências ausentes. Instale os pacotes de requirements.txt."
            ) from erro

        self._anthropic = anthropic
        self._cliente = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries
        )
        self._modelo = modelo
        self._max_tokens = max_tokens
        self._controle_uso = controle_uso

    @property
    def ocupado(self) -> bool:
        return False

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
        if self._controle_uso is not None:
            self._controle_uso.verificar()
        parametros = self._parametros(mensagens, esquema)
        inicio = time.perf_counter()
        try:
            resposta = self._cliente.messages.create(**parametros)
        except self._anthropic.AnthropicError as erro:
            raise self._converter_erro(erro, inicio, tentativa) from erro
        self._registrar_metricas(resposta, inicio, tentativa, "estruturada")
        self._registrar_uso(resposta, "estruturada")
        return self._extrair_texto(resposta)

    def gerar_texto_streaming(
        self,
        mensagens: Sequence[dict[str, str]],
        ao_receber: Callable[[str], None] | None = None,
    ) -> str:
        if self._controle_uso is not None:
            self._controle_uso.verificar()
        parametros = self._parametros(mensagens, {})
        inicio = time.perf_counter()
        partes: list[str] = []
        try:
            with self._cliente.messages.stream(**parametros) as fluxo:
                for fragmento in fluxo.text_stream:
                    if not fragmento:
                        continue
                    partes.append(fragmento)
                    if ao_receber is not None:
                        ao_receber(fragmento)
                resposta = fluxo.get_final_message()
        except self._anthropic.AnthropicError as erro:
            raise self._converter_erro(erro, inicio, 1) from erro
        self._registrar_metricas(resposta, inicio, 1, "streaming")
        self._registrar_uso(resposta, "streaming")
        self._verificar_recusa(resposta)
        conteudo = "".join(partes)
        if not conteudo.strip():
            raise ErroProvedorClaude("O modelo retornou uma resposta vazia.")
        return conteudo

    def gerar_agendado(
        self, mensagens: Sequence[dict[str, str]], esquema: dict[str, object]
    ) -> str:
        return self.gerar_com_metricas(mensagens, esquema)

    def _parametros(
        self, mensagens: Sequence[dict[str, str]], esquema: dict[str, object]
    ) -> dict[str, Any]:
        sistema, conversa = preparar_mensagens(mensagens)
        parametros: dict[str, Any] = {
            "model": self._modelo,
            "max_tokens": self._max_tokens,
            "messages": conversa,
        }
        if sistema:
            parametros["system"] = sistema
        # `temperature` não é enviada: o Claude Haiku 5.5 (modelo padrão)
        # rejeita qualquer valor diferente do padrão com erro 400.
        output_config: dict[str, Any] = {}
        if self._modelo.startswith("claude-haiku-5"):
            # Sem raciocínio estendido: com max_tokens pequeno, o pensamento
            # consumiria o limite antes do JSON.
            parametros["thinking"] = {"type": "disabled"}
            output_config["effort"] = "low"
        if esquema:
            output_config["format"] = {
                "type": "json_schema",
                "schema": adaptar_esquema(esquema),
            }
        if output_config:
            parametros["output_config"] = output_config
        return parametros

    def _registrar_uso(self, resposta: Any, operacao: str) -> None:
        # Registrado mesmo em recusa ou max_tokens: esses tokens são cobrados.
        uso = getattr(resposta, "usage", None)
        if self._controle_uso is not None and uso is not None:
            self._controle_uso.registrar(self._modelo, operacao, uso)

    def _extrair_texto(self, resposta: Any) -> str:
        self._verificar_recusa(resposta)
        conteudo = "".join(
            bloco.text
            for bloco in resposta.content
            if getattr(bloco, "type", None) == "text"
        )
        if not conteudo.strip():
            raise ErroProvedorClaude("O modelo retornou uma resposta vazia.")
        return conteudo

    @staticmethod
    def _verificar_recusa(resposta: Any) -> None:
        if getattr(resposta, "stop_reason", None) == "refusal":
            raise ErroProvedorClaude(
                "O modelo se recusou a responder a este pedido."
            )

    def _converter_erro(
        self, erro: Exception, inicio: float, tentativa: int
    ) -> ErroProvedorClaude:
        anthropic = self._anthropic
        logging.warning(
            "Falha na chamada ao Claude: tentativa=%d duracao_ms=%.3f erro=%s",
            tentativa,
            (time.perf_counter() - inicio) * 1000,
            type(erro).__name__,
        )
        if isinstance(erro, anthropic.AuthenticationError):
            mensagem = (
                "A chave da API do Claude é inválida. Confira ANTHROPIC_API_KEY "
                "no arquivo .env."
            )
        elif isinstance(erro, anthropic.PermissionDeniedError):
            mensagem = (
                "A chave da API do Claude não tem permissão para usar este "
                "modelo. Confira a chave e MODELO_CLAUDE."
            )
        elif isinstance(erro, anthropic.RateLimitError):
            mensagem = (
                "O limite de uso da API do Claude foi atingido. Aguarde um "
                "pouco ou confira os créditos da sua conta."
            )
        elif isinstance(erro, anthropic.NotFoundError):
            mensagem = (
                "O modelo configurado em MODELO_CLAUDE não foi encontrado."
            )
        elif isinstance(erro, anthropic.APIStatusError):
            if erro.status_code >= 500:
                mensagem = _MENSAGEM_INDISPONIVEL
            else:
                mensagem = "A API do Claude recusou a requisição."
        elif isinstance(erro, anthropic.APITimeoutError):
            mensagem = "A API do Claude demorou demais para responder."
        elif isinstance(erro, anthropic.APIConnectionError):
            mensagem = _MENSAGEM_SEM_CONEXAO
        else:
            mensagem = "Não foi possível conversar com a API do Claude."
        return ErroProvedorClaude(mensagem)

    @staticmethod
    def _registrar_metricas(
        resposta: Any, inicio: float, tentativa: int, operacao: str
    ) -> None:
        uso = getattr(resposta, "usage", None)
        logging.info(
            "Métricas Claude: operacao=%s tentativa=%d duracao_ms=%.3f "
            "input_tokens=%s output_tokens=%s stop_reason=%s request_id=%s",
            operacao,
            tentativa,
            (time.perf_counter() - inicio) * 1000,
            getattr(uso, "input_tokens", None),
            getattr(uso, "output_tokens", None),
            getattr(resposta, "stop_reason", None),
            getattr(resposta, "_request_id", None),
        )


def preparar_mensagens(
    mensagens: Sequence[dict[str, str]],
) -> tuple[str, list[dict[str, str]]]:
    """Separa o `system` e devolve a conversa no formato da API do Claude.

    Mensagens seguidas do mesmo papel são juntadas e a conversa sempre começa
    com `user` (mensagens iniciais do assistente são descartadas).
    """
    sistemas: list[str] = []
    conversa: list[dict[str, str]] = []
    for mensagem in mensagens:
        papel = mensagem["role"]
        conteudo = mensagem["content"]
        if papel == "system":
            sistemas.append(conteudo)
            continue
        if papel not in {"user", "assistant"}:
            raise ValueError(f"Papel de mensagem desconhecido: {papel}")
        if not conversa and papel == "assistant":
            continue
        if conversa and conversa[-1]["role"] == papel:
            conversa[-1] = {
                "role": papel,
                "content": f"{conversa[-1]['content']}\n\n{conteudo}",
            }
        else:
            conversa.append({"role": papel, "content": conteudo})
    if not conversa:
        raise ErroProvedorClaude("Não há mensagem do usuário para enviar.")
    return "\n\n".join(sistemas), conversa


def adaptar_esquema(esquema: dict[str, object]) -> dict[str, Any]:
    """Adapta o JSON Schema do Pydantic aos structured outputs do Claude.

    Usa o `transform_schema` do SDK, que remove restrições não aceitas
    (`maxLength`, `minimum`...), movendo-as para a descrição, e força
    `additionalProperties: false`. A validação do Pydantic continua valendo.
    """
    from anthropic import transform_schema

    return transform_schema(dict(esquema))
