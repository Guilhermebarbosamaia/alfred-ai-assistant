import logging
from typing import Any

from src.agente.erros import ErroProvedor
from src.memoria.banco import Banco


class ErroLimiteUso(ErroProvedor):
    """O limite de tokens do dia ou do mês foi atingido."""


class ControleUso:
    """Conta os tokens gastos com o modelo e bloqueia chamadas acima do limite.

    O limite é suave: a checagem acontece antes de cada chamada, então a última
    chamada permitida pode ultrapassar o teto em até uma resposta. Limite 0
    significa sem limite. Dia e mês seguem o fuso de FUSO_HORARIO.
    """

    def __init__(
        self,
        banco: Banco,
        limite_dia: int = 0,
        limite_mes: int = 0,
        provedor: str = "claude",
    ) -> None:
        self._banco = banco
        self._limite_dia = limite_dia
        self._limite_mes = limite_mes
        self._provedor = provedor

    def verificar(self) -> None:
        if not self._limite_dia and not self._limite_mes:
            return
        inicio_dia, inicio_mes = self._banco.inicio_dia_e_mes_utc()
        if self._limite_dia:
            usados = self._banco.tokens_usados_desde(inicio_dia)
            if usados >= self._limite_dia:
                self._bloquear("dia", usados, self._limite_dia)
        if self._limite_mes:
            usados = self._banco.tokens_usados_desde(inicio_mes)
            if usados >= self._limite_mes:
                self._bloquear("mes", usados, self._limite_mes)

    def registrar(self, modelo: str, operacao: str, uso: Any) -> None:
        self._banco.registrar_uso_modelo(
            self._provedor,
            modelo,
            operacao,
            int(getattr(uso, "input_tokens", 0) or 0),
            int(getattr(uso, "output_tokens", 0) or 0),
        )

    @staticmethod
    def _bloquear(periodo: str, usados: int, limite: int) -> None:
        logging.warning(
            "Limite de uso do modelo atingido: limite=%s usados=%d teto=%d",
            periodo,
            usados,
            limite,
        )
        if periodo == "dia":
            mensagem = (
                f"O limite diário de uso do modelo ({limite} tokens) foi "
                "atingido. Ele volta amanhã; para mudar, ajuste "
                "CLAUDE_LIMITE_TOKENS_DIA no .env."
            )
        else:
            mensagem = (
                f"O limite mensal de uso do modelo ({limite} tokens) foi "
                "atingido. Ele volta no próximo mês; para mudar, ajuste "
                "CLAUDE_LIMITE_TOKENS_MES no .env."
            )
        raise ErroLimiteUso(mensagem)
