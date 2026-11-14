from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.memoria.banco import Banco


class ParametrosListarLembretes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estado: Literal["pendentes", "concluidos", "atrasados"]


class ParametrosConcluirLembrete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)


class ParametrosExcluirRegistro(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: Literal["nota", "lembrete"]
    id: int = Field(gt=0)


def listar_lembretes(
    banco: Banco, parametros: ParametrosListarLembretes
) -> str:
    lembretes = banco.listar_lembretes(parametros.estado)
    if not lembretes:
        estado = {
            "pendentes": "pendente",
            "concluidos": "concluído",
            "atrasados": "atrasado",
        }[parametros.estado]
        return f"Nenhum lembrete {estado}."
    linhas = []
    for lembrete in lembretes:
        quando = banco.para_local(str(lembrete["quando"]))
        linhas.append(
            f"#{lembrete['id']} ({quando:%d/%m/%Y %H:%M}): {lembrete['texto']}"
        )
    return "\n".join(linhas)


def concluir_lembrete(
    banco: Banco, parametros: ParametrosConcluirLembrete
) -> str:
    if not banco.concluir_lembrete(parametros.id):
        return f"Lembrete #{parametros.id} não encontrado ou já concluído."
    return f"Lembrete #{parametros.id} concluído."


def excluir_registro(banco: Banco, parametros: ParametrosExcluirRegistro) -> str:
    apagado = (
        banco.apagar_nota(parametros.id)
        if parametros.tipo == "nota"
        else banco.apagar_lembrete(parametros.id)
    )
    if not apagado:
        return f"{parametros.tipo.capitalize()} #{parametros.id} não encontrado."
    return f"{parametros.tipo.capitalize()} #{parametros.id} apagado."
