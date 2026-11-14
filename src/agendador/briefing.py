import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.agendador.clima import obter_clima
from src.agente.provedor import ErroProvedorOllama
from src.memoria.banco import Banco


class ProvedorBriefing(Protocol):
    def gerar(
        self, mensagens: list[dict[str, str]], esquema: dict[str, object]
    ) -> str: ...


class RespostaBriefing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(min_length=1, max_length=1200)

    @field_validator("texto")
    @classmethod
    def validar_texto(cls, valor: str) -> str:
        texto = valor.strip()
        if not texto:
            raise ValueError("O briefing não pode ficar vazio.")
        if len(texto.splitlines()) > 6:
            raise ValueError("O briefing não pode exceder seis linhas.")
        return texto


Clima = Callable[[str], str | None]


class Briefing:
    def __init__(
        self,
        banco: Banco,
        provedor: ProvedorBriefing,
        clima_ativo: bool = False,
        cidade: str = "",
        obter_clima_configurado: Clima = obter_clima,
    ) -> None:
        self._banco = banco
        self._provedor = provedor
        self._clima_ativo = clima_ativo
        self._cidade = cidade
        self._obter_clima = obter_clima_configurado

    def gerar(self, agora: datetime | None = None, agendado: bool = False) -> str:
        horario = agora or self._banco.agora_utc()
        dados = self._banco.dados_briefing(horario)
        if self._clima_ativo:
            try:
                clima = self._obter_clima(self._cidade)
            except Exception as erro:
                logging.warning("Não foi possível consultar o clima: %s", erro)
            else:
                if clima:
                    dados["clima"] = clima
        mensagens = [
            {
                "role": "system",
                "content": (
                    "Redija em português um briefing pessoal em até seis linhas. "
                    "Use exclusivamente os dados fornecidos; não invente "
                    "tarefas, horários ou clima. Se não houver itens, diga isso "
                    "brevemente. Quando mais_lembretes ou mais_pendencias forem "
                    "maiores que zero, informe esse total como 'e mais N'. "
                    "Não calcule nem descreva hábitos; o código acrescentará "
                    "uma linha fixa com os hábitos e as sequências atuais."
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
                    mensagens, RespostaBriefing.model_json_schema()
                )
            else:
                conteudo = self._provedor.gerar(
                    mensagens, RespostaBriefing.model_json_schema()
                )
            resposta = RespostaBriefing.model_validate_json(conteudo)
        except (
            ErroProvedorOllama,
            ValidationError,
            ValueError,
            TimeoutError,
            OSError,
            RuntimeError,
        ) as erro:
            logging.warning("Falha ao redigir o briefing: %s", erro)
            return self._texto_fixo(horario, dados)
        return self._incluir_habitos(resposta.texto, dados)

    def _texto_fixo(
        self, agora: datetime, dados: dict[str, object]
    ) -> str:
        local = self._banco.para_local(agora)
        linhas = [f"Briefing de {local:%d/%m/%Y} às {local:%H:%M}"]
        lembretes = dados["lembretes_do_dia"]
        pendencias = dados["pendencias_atrasadas"]
        habitos = dados["habitos_do_dia"]
        assert isinstance(lembretes, list)
        assert isinstance(pendencias, list)
        assert isinstance(habitos, list)
        texto_lembretes = ", ".join(
            self._formatar_item(item) for item in lembretes if isinstance(item, dict)
        )
        mais_lembretes = int(dados["mais_lembretes"])
        if mais_lembretes:
            texto_lembretes += f", e mais {mais_lembretes}"
        linhas.append(f"Lembretes de hoje: {texto_lembretes or 'nenhum'}")
        texto_pendencias = ", ".join(
            self._formatar_item(item) for item in pendencias if isinstance(item, dict)
        )
        mais_pendencias = int(dados["mais_pendencias"])
        if mais_pendencias:
            texto_pendencias += f", e mais {mais_pendencias}"
        linhas.append(f"Pendências atrasadas: {texto_pendencias or 'nenhuma'}")
        linha_habitos = self._linha_habitos(habitos)
        if linha_habitos:
            linhas.append(linha_habitos)
        clima = dados.get("clima")
        if clima:
            linhas.append(f"Clima: {clima}")
        return "\n".join(linhas)[:1200]

    @staticmethod
    def _linha_habitos(habitos: list[object]) -> str:
        resumo_habitos = "; ".join(
            f"{item['nome']} (sequência {item['sequencia_atual']} dia(s))"
            for item in habitos
            if isinstance(item, dict)
        )
        return f"Hábitos do dia: {resumo_habitos}" if resumo_habitos else ""

    def _incluir_habitos(self, texto: str, dados: dict[str, object]) -> str:
        habitos = dados["habitos_do_dia"]
        assert isinstance(habitos, list)
        linha_habitos = self._linha_habitos(habitos)
        if not linha_habitos:
            return texto
        linhas = texto.splitlines()
        if len(linhas) >= 6:
            linhas = linhas[:5]
        linhas.append(linha_habitos)
        return "\n".join(linhas)[:1200]

    def _formatar_item(self, item: dict[str, object]) -> str:
        horario = self._banco.para_local(str(item["quando"]))
        return f"{horario:%H:%M} {str(item['texto'])[:80]}"
