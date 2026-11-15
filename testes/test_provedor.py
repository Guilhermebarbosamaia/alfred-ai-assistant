import logging
from types import SimpleNamespace

from src.agente.provedor import ProvedorOllama, _nanosegundos_em_ms
from src.config import Configuracao


def test_nanosegundos_sao_convertidos_em_milisegundos() -> None:
    assert _nanosegundos_em_ms(1_234_567) == 1.235
    assert _nanosegundos_em_ms(None) is None


def test_chamada_registra_metricas_sem_conteudo_da_resposta(caplog) -> None:
    provedor = ProvedorOllama("http://localhost:11434", "modelo-teste", 5)
    argumentos: dict[str, object] = {}

    def responder(**dados: object) -> SimpleNamespace:
        argumentos.update(dados)
        return SimpleNamespace(
            message=SimpleNamespace(content="segredo da conversa"),
            total_duration=1_000_000_000,
            load_duration=200_000_000,
            prompt_eval_count=50,
            prompt_eval_duration=300_000_000,
            eval_count=20,
            eval_duration=500_000_000,
        )

    provedor._cliente.chat = responder
    esquema = {"type": "object", "properties": {"acao": {"type": "string"}}}

    with caplog.at_level(logging.INFO):
        resultado = provedor.gerar_com_metricas(
            [{"role": "user", "content": "conteúdo privado"}],
            esquema,
            tentativa=2,
        )

    assert resultado == "segredo da conversa"
    assert "total_duration_ms=1000.0" in caplog.text
    assert "load_duration_ms=200.0" in caplog.text
    assert "prompt_eval_count=50" in caplog.text
    assert "prompt_eval_duration_ms=300.0" in caplog.text
    assert "eval_count=20" in caplog.text
    assert "eval_duration_ms=500.0" in caplog.text
    assert "tentativa=2" in caplog.text
    assert "segredo da conversa" not in caplog.text
    assert "conteúdo privado" not in caplog.text
    assert argumentos["keep_alive"] == "30m"
    assert argumentos["options"] == {
        "temperature": 0.1,
        "num_ctx": 4096,
        "num_predict": 256,
    }
    assert argumentos["format"] == esquema


def test_configuracao_usa_padrao_de_keep_alive_e_limites() -> None:
    config = Configuracao()

    assert config.ollama_keep_alive == "30m"
    assert config.ollama_num_ctx == 4096
    assert config.ollama_num_predict == 256


def test_conversa_streaming_emite_fragmentos_e_registra_metricas(caplog) -> None:
    provedor = ProvedorOllama("http://localhost:11434", "modelo-teste", 5)
    argumentos: dict[str, object] = {}

    def responder(**dados: object) -> list[SimpleNamespace]:
        argumentos.update(dados)
        return [
            SimpleNamespace(message=SimpleNamespace(content="Oi")),
            SimpleNamespace(
                message=SimpleNamespace(content="!"),
                total_duration=10_000_000,
                load_duration=1_000_000,
                prompt_eval_count=4,
                prompt_eval_duration=2_000_000,
                eval_count=2,
                eval_duration=3_000_000,
            ),
        ]

    provedor._cliente.chat = responder
    fragmentos: list[str] = []

    with caplog.at_level(logging.INFO):
        resultado = provedor.gerar_texto_streaming(
            [{"role": "user", "content": "Oi"}], fragmentos.append
        )

    assert resultado == "Oi!"
    assert fragmentos == ["Oi", "!"]
    assert argumentos["stream"] is True
    assert "format" not in argumentos
    assert "total_duration_ms=10.0" in caplog.text
    assert "eval_duration_ms=3.0" in caplog.text


def test_configuracao_carrega_opcoes_ollama_do_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "45m")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "2048")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "128")

    config = Configuracao.carregar()

    assert config.ollama_keep_alive == "45m"
    assert config.ollama_num_ctx == 2048
    assert config.ollama_num_predict == 128
