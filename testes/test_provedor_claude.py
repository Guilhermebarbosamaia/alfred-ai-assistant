import logging
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, ConfigDict, Field

from src.agente.erros import ErroProvedor
from src.agente.fabrica import criar_provedor
from src.agente.provedor import ErroProvedorOllama
from src.agente.provedor_claude import (
    ErroProvedorClaude,
    ProvedorClaude,
    adaptar_esquema,
    preparar_mensagens,
)
from src.config import Configuracao, ErroConfiguracao
from src.main import main

CHAVE_TESTE = "sk-ant-chave-de-teste-nao-real"


class Detalhe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantidade: float | None = Field(default=None, ge=0)


class RespostaTeste(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(min_length=1, max_length=50)
    nota: int | None = Field(default=None, ge=1, le=7)
    detalhe: Detalhe | None = None


def _resposta(texto: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=texto),
        ],
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
        _request_id="req_teste",
    )


class MensagensFalsas:
    def __init__(self, texto: str = "{}", fragmentos: list[str] | None = None):
        self.texto = texto
        self.fragmentos = fragmentos or []
        self.argumentos: dict[str, object] = {}
        self.erro: Exception | None = None

    def create(self, **argumentos: object) -> SimpleNamespace:
        self.argumentos = argumentos
        if self.erro is not None:
            raise self.erro
        return _resposta(self.texto)

    @contextmanager
    def stream(self, **argumentos: object):
        self.argumentos = argumentos
        if self.erro is not None:
            raise self.erro
        yield SimpleNamespace(
            text_stream=iter(self.fragmentos),
            get_final_message=lambda: _resposta("".join(self.fragmentos)),
        )


def criar_provedor_teste(
    mensagens: MensagensFalsas, modelo: str = "claude-haiku-5-5"
) -> ProvedorClaude:
    provedor = ProvedorClaude(CHAVE_TESTE, modelo, timeout=5, max_tokens=128)
    provedor._cliente = SimpleNamespace(messages=mensagens)
    return provedor


def _requisicao() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _erro_status(classe: type, status: int) -> Exception:
    return classe(
        f"erro com {CHAVE_TESTE}",
        response=httpx2.Response(status, request=_requisicao()),
        body=None,
    )


def test_system_e_separado_e_papeis_alternam() -> None:
    sistema, conversa = preparar_mensagens(
        [
            {"role": "system", "content": "Instruções"},
            {"role": "assistant", "content": "resposta antiga solta"},
            {"role": "user", "content": "primeira"},
            {"role": "user", "content": "segunda"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "terceira"},
        ]
    )

    assert sistema == "Instruções"
    assert conversa == [
        {"role": "user", "content": "primeira\n\nsegunda"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "terceira"},
    ]


def test_conversa_sem_mensagem_do_usuario_gera_erro_do_provedor() -> None:
    with pytest.raises(ErroProvedorClaude):
        preparar_mensagens([{"role": "system", "content": "Instruções"}])


def test_esquema_adaptado_remove_restricoes_nao_aceitas() -> None:
    esquema = adaptar_esquema(RespostaTeste.model_json_schema())
    texto = str(esquema)

    assert esquema["additionalProperties"] is False
    assert esquema["$defs"]["Detalhe"]["additionalProperties"] is False
    assert esquema["required"] == ["texto"]
    assert "'maxLength'" not in texto
    assert "'minimum'" not in texto
    assert "'maximum'" not in texto


def test_saida_estruturada_envia_esquema_e_devolve_string() -> None:
    mensagens = MensagensFalsas('{"texto": "oi"}')
    provedor = criar_provedor_teste(mensagens)

    resultado = provedor.gerar_com_metricas(
        [
            {"role": "system", "content": "Extraia a intenção."},
            {"role": "user", "content": "oi"},
        ],
        RespostaTeste.model_json_schema(),
        tentativa=2,
    )

    assert resultado == '{"texto": "oi"}'
    assert RespostaTeste.model_validate_json(resultado).texto == "oi"
    argumentos = mensagens.argumentos
    assert argumentos["model"] == "claude-haiku-5-5"
    assert argumentos["max_tokens"] == 128
    assert argumentos["system"] == "Extraia a intenção."
    assert argumentos["messages"] == [{"role": "user", "content": "oi"}]
    assert "temperature" not in argumentos
    assert argumentos["thinking"] == {"type": "disabled"}
    formato = argumentos["output_config"]["format"]
    assert formato["type"] == "json_schema"
    assert formato["schema"] == adaptar_esquema(RespostaTeste.model_json_schema())


def test_esquema_vazio_nao_envia_formato_e_outros_modelos_nao_desligam_pensamento() -> None:
    mensagens = MensagensFalsas("texto livre")
    provedor = criar_provedor_teste(mensagens, modelo="claude-sonnet-5-5")

    assert provedor.gerar([{"role": "user", "content": "oi"}], {}) == "texto livre"
    assert "output_config" not in mensagens.argumentos
    assert "thinking" not in mensagens.argumentos


def test_gerar_agendado_funciona_sem_fila() -> None:
    provedor = criar_provedor_teste(MensagensFalsas('{"texto": "Bom dia."}'))

    assert provedor.ocupado is False
    assert provedor.gerar_agendado(
        [{"role": "user", "content": "dados"}], RespostaTeste.model_json_schema()
    ) == '{"texto": "Bom dia."}'


def test_streaming_chama_ao_receber_e_registra_metricas(caplog) -> None:
    mensagens = MensagensFalsas(fragmentos=["Olá", "", ", tudo bem?"])
    provedor = criar_provedor_teste(mensagens)
    recebidos: list[str] = []

    with caplog.at_level(logging.INFO):
        resultado = provedor.gerar_texto_streaming(
            [
                {"role": "system", "content": "Converse."},
                {"role": "user", "content": "conteúdo privado"},
            ],
            recebidos.append,
        )

    assert resultado == "Olá, tudo bem?"
    assert recebidos == ["Olá", ", tudo bem?"]
    assert "output_config" not in mensagens.argumentos or (
        "format" not in mensagens.argumentos["output_config"]
    )
    assert "operacao=streaming" in caplog.text
    assert "input_tokens=42" in caplog.text
    assert "conteúdo privado" not in caplog.text
    assert "tudo bem" not in caplog.text


def test_logs_tem_metricas_sem_conteudo_nem_chave(caplog) -> None:
    provedor = criar_provedor_teste(MensagensFalsas('{"texto": "segredo da conversa"}'))

    with caplog.at_level(logging.DEBUG):
        provedor.gerar_com_metricas(
            [{"role": "user", "content": "conteúdo privado"}],
            RespostaTeste.model_json_schema(),
            tentativa=3,
        )

    assert "input_tokens=42" in caplog.text
    assert "output_tokens=7" in caplog.text
    assert "duracao_ms=" in caplog.text
    assert "tentativa=3" in caplog.text
    assert "segredo da conversa" not in caplog.text
    assert "conteúdo privado" not in caplog.text
    assert CHAVE_TESTE not in caplog.text


@pytest.mark.parametrize(
    ("erro", "trecho"),
    [
        (anthropic.APIConnectionError(request=_requisicao()), "conexão"),
        (anthropic.APITimeoutError(request=_requisicao()), "demorou"),
        (_erro_status(anthropic.AuthenticationError, 401), "chave"),
        (_erro_status(anthropic.RateLimitError, 429), "limite"),
        (_erro_status(anthropic.NotFoundError, 404), "MODELO_CLAUDE"),
        (_erro_status(anthropic.InternalServerError, 500), "instável"),
    ],
)
def test_erro_da_api_vira_erro_provedor_claude(caplog, erro, trecho) -> None:
    mensagens = MensagensFalsas()
    mensagens.erro = erro
    provedor = criar_provedor_teste(mensagens)

    with caplog.at_level(logging.WARNING), pytest.raises(ErroProvedorClaude) as capturado:
        provedor.gerar([{"role": "user", "content": "conteúdo privado"}], {})

    assert isinstance(capturado.value, ErroProvedor)
    assert trecho in str(capturado.value)
    assert CHAVE_TESTE not in str(capturado.value)
    assert CHAVE_TESTE not in caplog.text
    assert "conteúdo privado" not in caplog.text
    assert type(erro).__name__ in caplog.text


def test_erro_no_streaming_vira_erro_provedor_claude() -> None:
    mensagens = MensagensFalsas()
    mensagens.erro = anthropic.APIConnectionError(request=_requisicao())
    provedor = criar_provedor_teste(mensagens)

    with pytest.raises(ErroProvedorClaude):
        provedor.gerar_texto_streaming([{"role": "user", "content": "oi"}])


def test_recusa_do_modelo_vira_erro_provedor_claude() -> None:
    mensagens = MensagensFalsas()
    mensagens.create = lambda **_: _resposta("", stop_reason="refusal")
    provedor = criar_provedor_teste(mensagens)

    with pytest.raises(ErroProvedorClaude, match="recusou"):
        provedor.gerar([{"role": "user", "content": "oi"}], {})


def test_nome_antigo_do_erro_continua_funcionando() -> None:
    assert issubclass(ErroProvedorOllama, ErroProvedor)
    assert issubclass(ErroProvedorClaude, ErroProvedor)


def test_configuracao_usa_claude_por_padrao() -> None:
    config = Configuracao()

    assert config.provedor == "claude"
    assert config.modelo_claude == "claude-haiku-5-5"
    assert config.timeout_claude == 60
    assert config.claude_max_tokens == 256


def test_configuracao_carrega_variaveis_do_claude(monkeypatch) -> None:
    monkeypatch.setenv("PROVEDOR", "Claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", f" {CHAVE_TESTE} ")
    monkeypatch.setenv("MODELO_CLAUDE", "claude-sonnet-5-5")
    monkeypatch.setenv("TIMEOUT_CLAUDE", "30")
    monkeypatch.setenv("CLAUDE_MAX_TOKENS", "512")

    config = Configuracao.carregar()

    assert config.provedor == "claude"
    assert config.anthropic_api_key.get_secret_value() == CHAVE_TESTE
    assert CHAVE_TESTE not in repr(config)
    assert config.modelo_claude == "claude-sonnet-5-5"
    assert config.timeout_claude == 30
    assert config.claude_max_tokens == 512
    config.validar_provedor()


def test_configuracao_sem_chave_explica_o_que_configurar() -> None:
    config = Configuracao(provedor="claude", anthropic_api_key="  ")

    with pytest.raises(ErroConfiguracao, match="ANTHROPIC_API_KEY"):
        config.validar_provedor()


def test_ollama_nao_exige_chave() -> None:
    Configuracao(provedor="ollama").validar_provedor()


def test_main_para_sem_chave_com_mensagem_clara(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PROVEDOR", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("INTERFACE", "terminal")

    with pytest.raises(SystemExit) as saida:
        main([])

    assert saida.value.code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_fabrica_com_claude_nao_importa_ollama(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "ollama", None)
    config = Configuracao(provedor="claude", anthropic_api_key=CHAVE_TESTE)

    provedor = criar_provedor(config)

    assert isinstance(provedor, ProvedorClaude)


def test_fabrica_monta_ollama_quando_escolhido() -> None:
    from src.agente.provedor import ProvedorOllama

    provedor = criar_provedor(Configuracao(provedor="ollama"))

    assert isinstance(provedor, ProvedorOllama)


def _controle(tmp_path, limite_dia: int = 0):
    from src.agente.uso import ControleUso
    from src.memoria.banco import Banco

    banco = Banco(tmp_path / "assistente.db")
    return banco, ControleUso(banco, limite_dia=limite_dia)


def test_uso_e_registrado_apos_chamada_estruturada_e_streaming(tmp_path, caplog) -> None:
    banco, controle = _controle(tmp_path)
    mensagens = MensagensFalsas('{"texto": "segredo da conversa"}', fragmentos=["Oi"])
    provedor = criar_provedor_teste(mensagens)
    provedor._controle_uso = controle

    with caplog.at_level(logging.DEBUG):
        provedor.gerar([{"role": "user", "content": "conteúdo privado"}], {})
        provedor.gerar_texto_streaming([{"role": "user", "content": "conteúdo privado"}])

    assert banco.tokens_usados_desde("2000-01-01T00:00:00+00:00") == 2 * (42 + 7)
    with banco._conectar() as conexao:
        operacoes = [
            linha["operacao"]
            for linha in conexao.execute("SELECT operacao FROM uso_modelo ORDER BY id")
        ]
    assert operacoes == ["estruturada", "streaming"]
    assert "conteúdo privado" not in caplog.text
    assert "segredo da conversa" not in caplog.text
    assert CHAVE_TESTE not in caplog.text


def test_limite_atingido_nao_chama_a_api(tmp_path) -> None:
    from src.agente.uso import ErroLimiteUso

    banco, controle = _controle(tmp_path, limite_dia=10)
    banco.registrar_uso_modelo("claude", "claude-haiku-5-5", "estruturada", 10, 0)
    mensagens = MensagensFalsas()
    chamadas: list[object] = []
    mensagens.create = lambda **argumentos: chamadas.append(argumentos)
    provedor = criar_provedor_teste(mensagens)
    provedor._controle_uso = controle

    with pytest.raises(ErroLimiteUso):
        provedor.gerar([{"role": "user", "content": "oi"}], {})
    with pytest.raises(ErroLimiteUso):
        provedor.gerar_texto_streaming([{"role": "user", "content": "oi"}])

    assert chamadas == []
    assert mensagens.argumentos == {}


def test_erro_da_api_nao_registra_uso(tmp_path) -> None:
    banco, controle = _controle(tmp_path)
    mensagens = MensagensFalsas()
    mensagens.erro = anthropic.APIConnectionError(request=_requisicao())
    provedor = criar_provedor_teste(mensagens)
    provedor._controle_uso = controle

    with pytest.raises(ErroProvedorClaude):
        provedor.gerar([{"role": "user", "content": "oi"}], {})

    assert banco.tokens_usados_desde("2000-01-01T00:00:00+00:00") == 0


def test_configuracao_carrega_limites_de_uso(monkeypatch) -> None:
    assert Configuracao().claude_limite_tokens_dia == 200_000
    assert Configuracao().claude_limite_tokens_mes == 3_000_000
    monkeypatch.setenv("CLAUDE_LIMITE_TOKENS_DIA", "1500")
    monkeypatch.setenv("CLAUDE_LIMITE_TOKENS_MES", "0")

    config = Configuracao.carregar()

    assert config.claude_limite_tokens_dia == 1500
    assert config.claude_limite_tokens_mes == 0


def test_fabrica_passa_controle_de_uso_quando_recebe_banco(tmp_path) -> None:
    from src.agente.uso import ControleUso
    from src.memoria.banco import Banco

    config = Configuracao(
        provedor="claude",
        anthropic_api_key=CHAVE_TESTE,
        claude_limite_tokens_dia=1500,
    )

    com_banco = criar_provedor(config, Banco(tmp_path / "assistente.db"))
    sem_banco = criar_provedor(config)

    assert isinstance(com_banco._controle_uso, ControleUso)
    assert com_banco._controle_uso._limite_dia == 1500
    assert sem_banco._controle_uso is None
