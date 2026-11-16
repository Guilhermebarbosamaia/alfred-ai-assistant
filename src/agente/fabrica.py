from src.agente.provedor_claude import ProvedorClaude
from src.agente.uso import ControleUso
from src.config import Configuracao
from src.memoria.banco import Banco


def criar_provedor(
    configuracao: Configuracao, banco: Banco | None = None
) -> object:
    """Monta o provedor escolhido em PROVEDOR.

    Só o provedor escolhido é instanciado: com o Claude, o pacote `ollama` não
    é importado e o servidor local não é necessário. Um fallback futuro
    (Claude → Ollama) pode ser montado aqui, envolvendo os dois provedores.
    Com `banco`, o Claude conta os tokens e respeita os limites de uso.
    """
    configuracao.validar_provedor()
    if configuracao.provedor == "ollama":
        from src.agente.provedor import ProvedorOllama

        return ProvedorOllama(
            host=str(configuracao.ollama_host),
            modelo=configuracao.modelo_ollama,
            timeout=configuracao.timeout_ollama,
            keep_alive=configuracao.ollama_keep_alive,
            num_ctx=configuracao.ollama_num_ctx,
            num_predict=configuracao.ollama_num_predict,
        )
    controle_uso = (
        ControleUso(
            banco,
            limite_dia=configuracao.claude_limite_tokens_dia,
            limite_mes=configuracao.claude_limite_tokens_mes,
        )
        if banco is not None
        else None
    )
    return ProvedorClaude(
        api_key=configuracao.anthropic_api_key.get_secret_value(),
        modelo=configuracao.modelo_claude,
        timeout=configuracao.timeout_claude,
        max_tokens=configuracao.claude_max_tokens,
        controle_uso=controle_uso,
    )
