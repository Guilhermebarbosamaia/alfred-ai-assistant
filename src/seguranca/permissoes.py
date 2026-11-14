from enum import StrEnum


class NivelRisco(StrEnum):
    LEITURA = "leitura"
    ESCRITA = "escrita"
    SENSIVEL = "sensível"


def exigir_permissao(nivel: NivelRisco, confirmado: bool = False) -> None:
    if nivel == NivelRisco.SENSIVEL and not confirmado:
        raise PermissionError("Esta ação exige confirmação explícita.")
