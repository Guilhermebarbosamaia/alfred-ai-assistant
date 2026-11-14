from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.memoria.banco import Banco


class ParametrosHora(BaseModel):
    model_config = ConfigDict(extra="forbid")


def consultar_hora(_: ParametrosHora, banco: Banco | None = None) -> str:
    horario = (
        banco.para_local(banco.agora_utc())
        if banco is not None
        else datetime.now().astimezone()
    )
    return f"Agora são {horario:%H:%M} de {horario:%d/%m/%Y}."
