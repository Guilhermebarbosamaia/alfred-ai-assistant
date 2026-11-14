from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.memoria.banco import Banco


class ParametrosLembrete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(min_length=1, max_length=500)
    quando: datetime

    @field_validator("texto")
    @classmethod
    def validar_texto(cls, valor: str) -> str:
        texto = valor.strip()
        if not texto:
            raise ValueError("O lembrete não pode ficar vazio.")
        return texto


def criar_lembrete(banco: Banco, parametros: ParametrosLembrete) -> str:
    lembrete_id = banco.criar_lembrete(parametros.texto, parametros.quando)
    horario_local = banco.para_local(banco.para_utc(parametros.quando))
    horario = horario_local.strftime("%d/%m/%Y às %H:%M")
    return f"Lembrete criado (#{lembrete_id}) para {horario}: {parametros.texto}"
