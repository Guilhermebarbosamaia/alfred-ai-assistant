from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.memoria.banco import Banco


class ParametrosBuscaNota(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consulta: str = Field(max_length=500)

    @field_validator("consulta")
    @classmethod
    def limpar_consulta(cls, valor: str) -> str:
        consulta = valor.strip()
        return consulta


def buscar_notas(banco: Banco, parametros: ParametrosBuscaNota) -> str:
    if not parametros.consulta.strip():
        return "Informe uma consulta para buscar nas notas."
    resultados = banco.buscar_notas(parametros.consulta)
    if not resultados:
        return "Nenhuma nota encontrada."
    trechos: list[str] = []
    for nota in resultados:
        data = banco.para_local(str(nota["criada_em"])).strftime("%d/%m/%Y")
        trechos.append(
            f"Nota #{nota['id']} ({data}): {nota['trecho']}"
        )
    return "\n".join(trechos)
