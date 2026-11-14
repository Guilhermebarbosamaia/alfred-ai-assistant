from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.memoria.banco import Banco


class ParametrosCriarHabito(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: str = Field(min_length=1, max_length=100)
    descricao: str = Field(default="", max_length=500)
    meta_semanal: int = Field(ge=1, le=7)

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, valor: str) -> str:
        nome = valor.strip()
        if not nome:
            raise ValueError("O nome do hábito não pode ficar vazio.")
        return nome

    @field_validator("descricao")
    @classmethod
    def limpar_descricao(cls, valor: str) -> str:
        return valor.strip()


class ParametrosDesativarHabito(BaseModel):
    model_config = ConfigDict(extra="forbid")

    habito_id: int = Field(gt=0)


class ParametrosListarHabitos(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ativos_apenas: bool = True


class ParametrosRegistrarHabito(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    habito_id: int = Field(gt=0)
    feito: bool
    quantidade: float | None = Field(default=None, ge=0)
    observacao: str = Field(default="", max_length=500)

    @field_validator("observacao")
    @classmethod
    def validar_observacao(cls, valor: str) -> str:
        observacao = valor.strip()
        if observacao == "[FOLGA]":
            raise ValueError("Use a ferramenta de marcar folga.")
        return observacao


class ParametrosVerProgresso(BaseModel):
    model_config = ConfigDict(extra="forbid")

    habito_id: int | None = Field(default=None, gt=0)


class ParametrosMarcarFolga(BaseModel):
    model_config = ConfigDict(extra="forbid")

    habito_id: int = Field(gt=0)


def criar_habito(banco: Banco, parametros: ParametrosCriarHabito) -> str:
    habito_id = banco.criar_habito(
        parametros.nome, parametros.descricao, parametros.meta_semanal
    )
    return f"Hábito #{habito_id} criado: {parametros.nome}."


def desativar_habito(
    banco: Banco, parametros: ParametrosDesativarHabito
) -> str:
    if not banco.desativar_habito(parametros.habito_id):
        return f"Hábito #{parametros.habito_id} não encontrado ou já desativado."
    return f"Hábito #{parametros.habito_id} desativado."


def listar_habitos(
    banco: Banco, parametros: ParametrosListarHabitos
) -> str:
    habitos = banco.listar_habitos(parametros.ativos_apenas)
    if not habitos:
        return "Nenhum hábito encontrado."
    return "\n".join(
        f"#{habito['id']} {habito['nome']} — meta "
        f"{habito['meta_semanal']}x por semana"
        for habito in habitos
    )


def registrar_habito(
    banco: Banco, parametros: ParametrosRegistrarHabito
) -> str:
    banco.registrar_habito(
        parametros.habito_id,
        parametros.feito,
        parametros.quantidade,
        parametros.observacao,
    )
    habito = banco.obter_habito(parametros.habito_id)
    if habito is None:
        raise ValueError("Hábito não encontrado.")
    estado = "feito" if parametros.feito else "não feito"
    detalhe = (
        f" (quantidade: {parametros.quantidade:g})"
        if parametros.quantidade is not None
        else ""
    )
    return f"Hábito {habito['nome']} registrado como {estado}{detalhe}."


def ver_progresso(
    banco: Banco, parametros: ParametrosVerProgresso
) -> str:
    habitos = (
        [banco.progresso_habito(parametros.habito_id)]
        if parametros.habito_id is not None
        else [
            banco.progresso_habito(int(habito["id"]))
            for habito in banco.listar_habitos()
        ]
    )
    if not habitos:
        return "Nenhum hábito ativo para acompanhar."
    return "\n".join(
        f"{progresso['habito']}: sequência atual de "
        f"{progresso['sequencia_atual']} dia(s); "
        f"{progresso['percentual_periodo']}% da meta semanal "
        f"({progresso['dias_feitos']}/{progresso['meta_semanal']} dias)."
        for progresso in habitos
    )


def marcar_folga(banco: Banco, parametros: ParametrosMarcarFolga) -> str:
    banco.marcar_folga(parametros.habito_id)
    habito = banco.obter_habito(parametros.habito_id)
    if habito is None:
        raise ValueError("Hábito não encontrado.")
    return f"Folga registrada para {habito['nome']} hoje."
