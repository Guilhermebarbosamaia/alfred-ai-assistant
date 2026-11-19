import os
from collections.abc import Mapping
from datetime import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, HttpUrl, SecretStr, field_validator


class ErroConfiguracao(ValueError):
    """Configuração incompleta que impede o Alfred de iniciar."""


class Configuracao(BaseModel):
    provedor: Literal["claude", "ollama"] = "claude"
    anthropic_api_key: SecretStr = SecretStr("")
    modelo_claude: str = "claude-haiku-5-5"
    timeout_claude: float = Field(default=60, gt=0)
    claude_max_tokens: int = Field(default=256, gt=0)
    claude_limite_tokens_dia: int = Field(default=200_000, ge=0)
    claude_limite_tokens_mes: int = Field(default=3_000_000, ge=0)
    ollama_host: HttpUrl = HttpUrl("http://localhost:11434")
    modelo_ollama: str = "llama3.2:3b"
    timeout_ollama: float = Field(default=120, gt=0)
    ollama_keep_alive: str = "30m"
    ollama_num_ctx: int = Field(default=4096, gt=0)
    ollama_num_predict: int = Field(default=256, gt=0)
    caminho_banco: Path = Path("dados/assistente.db")
    intervalo_lembretes_segundos: int = Field(default=60, gt=0)
    horario_briefing: time = time(8, 0)
    briefing_ativo: bool = True
    horario_checkin: time = time(21, 0)
    checkin_ativo: bool = True
    dia_resumo: str = "sun"
    horario_resumo: time = time(20, 0)
    clima_ativo: bool = False
    cidade: str = "Araguaína"
    fuso_horario: str = "America/Araguaina"
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_id_autorizado: int | None = None
    telegram_webhook_segredo: SecretStr = SecretStr("")
    interface: Literal["terminal", "telegram", "ambas"] = "terminal"

    @field_validator("fuso_horario")
    @classmethod
    def validar_fuso_horario(cls, valor: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(valor)
        except ZoneInfoNotFoundError as erro:
            raise ValueError(f"Fuso horário inválido: {valor}") from erro
        return valor

    @field_validator("modelo_ollama", "modelo_claude")
    @classmethod
    def validar_modelo(cls, valor: str) -> str:
        modelo = valor.strip()
        if not modelo:
            raise ValueError("O nome do modelo não pode ficar vazio.")
        return modelo

    @field_validator("ollama_keep_alive")
    @classmethod
    def validar_keep_alive(cls, valor: str) -> str:
        keep_alive = valor.strip()
        if not keep_alive:
            raise ValueError("OLLAMA_KEEP_ALIVE não pode ficar vazio.")
        return keep_alive

    @field_validator("dia_resumo")
    @classmethod
    def validar_dia_resumo(cls, valor: str) -> str:
        dias = {
            "segunda": "mon",
            "segunda-feira": "mon",
            "seg": "mon",
            "mon": "mon",
            "terça": "tue",
            "terca": "tue",
            "terça-feira": "tue",
            "ter": "tue",
            "tue": "tue",
            "quarta": "wed",
            "quarta-feira": "wed",
            "qua": "wed",
            "wed": "wed",
            "quinta": "thu",
            "quinta-feira": "thu",
            "qui": "thu",
            "thu": "thu",
            "sexta": "fri",
            "sexta-feira": "fri",
            "sex": "fri",
            "fri": "fri",
            "sábado": "sat",
            "sabado": "sat",
            "sab": "sat",
            "sat": "sat",
            "domingo": "sun",
            "dom": "sun",
            "sun": "sun",
        }
        dia = valor.strip().casefold()
        if dia not in dias:
            raise ValueError(
                "RESUMO_DIA deve ser um dia da semana, como domingo."
            )
        return dias[dia]

    def validar_provedor(self) -> None:
        """Confere se o provedor escolhido tem o necessário para iniciar."""
        if (
            self.provedor == "claude"
            and not self.anthropic_api_key.get_secret_value().strip()
        ):
            raise ErroConfiguracao(
                "ANTHROPIC_API_KEY não está definida. Com PROVEDOR=claude "
                "(o padrão), coloque sua chave da API do Claude no arquivo "
                ".env, na linha ANTHROPIC_API_KEY=..., ou use PROVEDOR=ollama "
                "para usar o modelo local."
            )

    @classmethod
    def carregar(cls, ambiente: Mapping[str, str] | None = None) -> "Configuracao":
        """Lê as variáveis de `ambiente` ou, sem ele, do `.env` e do sistema.

        Na Cloudflare o Worker passa suas variáveis e segredos em `ambiente`.
        """
        if ambiente is None:
            load_dotenv()
            ambiente = os.environ

        def getenv(nome: str, padrao: str = "") -> str:
            valor = ambiente.get(nome)
            return padrao if valor is None else str(valor)

        return cls(
            provedor=getenv("PROVEDOR", "claude").strip().lower(),
            anthropic_api_key=getenv("ANTHROPIC_API_KEY", "").strip(),
            modelo_claude=getenv("MODELO_CLAUDE", "claude-haiku-5-5"),
            timeout_claude=float(getenv("TIMEOUT_CLAUDE", "60")),
            claude_max_tokens=int(getenv("CLAUDE_MAX_TOKENS", "256")),
            claude_limite_tokens_dia=int(
                getenv("CLAUDE_LIMITE_TOKENS_DIA", "200000")
            ),
            claude_limite_tokens_mes=int(
                getenv("CLAUDE_LIMITE_TOKENS_MES", "3000000")
            ),
            ollama_host=getenv("OLLAMA_HOST", "http://localhost:11434"),
            modelo_ollama=getenv("MODELO_OLLAMA", "llama3.2:3b"),
            timeout_ollama=float(getenv("TIMEOUT_OLLAMA", "120")),
            ollama_keep_alive=getenv("OLLAMA_KEEP_ALIVE", "30m"),
            ollama_num_ctx=int(getenv("OLLAMA_NUM_CTX", "4096")),
            ollama_num_predict=int(getenv("OLLAMA_NUM_PREDICT", "256")),
            caminho_banco=Path(getenv("CAMINHO_BANCO", "dados/assistente.db")),
            intervalo_lembretes_segundos=int(
                getenv("INTERVALO_LEMBRETES_SEGUNDOS", "60")
            ),
            horario_briefing=getenv(
                "BRIEFING_HORA", getenv("HORARIO_BRIEFING", "08:00")
            ),
            briefing_ativo=getenv("BRIEFING_ATIVO", "true"),
            horario_checkin=getenv("CHECKIN_HORA", "21:00"),
            checkin_ativo=getenv("CHECKIN_ATIVO", "true"),
            dia_resumo=getenv("RESUMO_DIA", "domingo"),
            horario_resumo=getenv("RESUMO_HORA", "20:00"),
            clima_ativo=getenv("CLIMA_ATIVO", "false"),
            cidade=getenv("CIDADE", "Araguaína"),
            fuso_horario=getenv("FUSO_HORARIO", "America/Araguaina"),
            telegram_bot_token=getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_id_autorizado=(
                int(getenv("TELEGRAM_ID_AUTORIZADO"))
                if getenv("TELEGRAM_ID_AUTORIZADO", "").strip()
                else None
            ),
            interface=getenv("INTERFACE", "terminal").strip().lower(),
            telegram_webhook_segredo=getenv(
                "TELEGRAM_WEBHOOK_SEGREDO", ""
            ).strip(),
        )
