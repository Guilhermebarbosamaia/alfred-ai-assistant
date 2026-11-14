from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.memoria.banco import Banco


class ParametrosNota(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(min_length=1, max_length=500)
    etiquetas: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("texto")
    @classmethod
    def validar_texto(cls, valor: str) -> str:
        texto = valor.strip()
        if not texto:
            raise ValueError("A nota não pode ficar vazia.")
        return texto

    @field_validator("etiquetas")
    @classmethod
    def limpar_etiquetas(cls, valores: list[str]) -> list[str]:
        etiquetas = (etiqueta.strip().lstrip("#") for etiqueta in valores)
        return list(dict.fromkeys(etiqueta for etiqueta in etiquetas if etiqueta))


def salvar_nota(banco: Banco, parametros: ParametrosNota) -> str:
    nota_id = banco.salvar_nota(parametros.texto, parametros.etiquetas)
    return f"Nota salva (#{nota_id}): {parametros.texto}"
