import json
import logging
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.agente.erros import ErroProvedor
from src.memoria.banco import Banco


class ProvedorResumo(Protocol):
    def gerar(
        self, mensagens: list[dict[str, str]], esquema: dict[str, object]
    ) -> str: ...


class RespostaResumo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(min_length=1, max_length=1200)

    @field_validator("texto")
    @classmethod
    def validar_texto(cls, valor: str) -> str:
        texto = valor.strip()
        if not texto:
            raise ValueError("O resumo não pode ficar vazio.")
        if len(texto.splitlines()) > 6:
            raise ValueError("O resumo não pode exceder seis linhas.")
        return texto


class ResumoSemanal:
    def __init__(self, banco: Banco, provedor: ProvedorResumo) -> None:
        self._banco = banco
        self._provedor = provedor

    def gerar(self, agora: datetime | None = None, agendado: bool = False) -> str:
        dados = self._banco.dados_resumo_semanal(agora)
        mensagens = [
            {
                "role": "system",
                "content": (
                    "Redija em português um resumo semanal de hábitos em até "
                    "seis linhas. Use exclusivamente os dados fornecidos. "
                    "Não recalcule nem invente números, metas ou sequências. "
                    "Se não houver hábitos, diga isso brevemente."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(dados, ensure_ascii=False),
            },
        ]
        try:
            gerador_agendado = getattr(self._provedor, "gerar_agendado", None)
            if agendado and callable(gerador_agendado):
                conteudo = gerador_agendado(
                    mensagens, RespostaResumo.model_json_schema()
                )
            else:
                conteudo = self._provedor.gerar(
                    mensagens, RespostaResumo.model_json_schema()
                )
            resposta = RespostaResumo.model_validate_json(conteudo)
        except (
            ErroProvedor,
            ValidationError,
            ValueError,
            TimeoutError,
            OSError,
            RuntimeError,
        ) as erro:
            logging.warning(
                "Falha ao redigir o resumo semanal: erro=%s", type(erro).__name__
            )
            return self._texto_fixo(dados)
        return resposta.texto

    @staticmethod
    def _texto_fixo(dados: dict[str, object]) -> str:
        habitos = dados["habitos"]
        assert isinstance(habitos, list)
        linhas = [f"Resumo semanal ({dados['inicio']} a {dados['fim']}):"]
        if not habitos:
            linhas.append("Nenhum hábito ativo para acompanhar.")
        for habito in habitos:
            assert isinstance(habito, dict)
            meta = "meta atingida" if habito["meta_atingida"] else "meta não atingida"
            linhas.append(
                f"- {habito['nome']}: {habito['dias_feitos']}/"
                f"{habito['meta_semanal']} dias, {meta}; melhor sequência: "
                f"{habito['melhor_sequencia']} dia(s)."
            )
        return "\n".join(linhas)
