from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from src.ferramentas.busca_notas import ParametrosBuscaNota, buscar_notas
from src.ferramentas.gestao import (
    ParametrosConcluirLembrete,
    ParametrosExcluirRegistro,
    ParametrosListarLembretes,
    concluir_lembrete,
    excluir_registro,
    listar_lembretes,
)
from src.ferramentas.habitos import (
    ParametrosCriarHabito,
    ParametrosDesativarHabito,
    ParametrosListarHabitos,
    ParametrosMarcarFolga,
    ParametrosRegistrarHabito,
    ParametrosVerProgresso,
    criar_habito,
    desativar_habito,
    listar_habitos,
    marcar_folga,
    registrar_habito,
    ver_progresso,
)
from src.ferramentas.lembretes import ParametrosLembrete, criar_lembrete
from src.ferramentas.notas import ParametrosNota, salvar_nota
from src.ferramentas.tempo import ParametrosHora, consultar_hora
from src.memoria.banco import Banco
from src.seguranca.permissoes import NivelRisco, exigir_permissao


@dataclass(frozen=True)
class Ferramenta:
    nome: str
    descricao: str
    parametros: type[BaseModel]
    nivel_risco: NivelRisco
    executar: Callable[[BaseModel], str]


class RegistroFerramentas:
    def __init__(self, ferramentas: list[Ferramenta]) -> None:
        self._ferramentas = {ferramenta.nome: ferramenta for ferramenta in ferramentas}

    @property
    def ferramentas(self) -> tuple[Ferramenta, ...]:
        return tuple(self._ferramentas.values())

    def executar(
        self, nome: str, entrada: BaseModel, confirmado: bool = False
    ) -> str:
        ferramenta = self._ferramentas[nome]
        exigir_permissao(ferramenta.nivel_risco, confirmado)
        parametros = ferramenta.parametros.model_validate(entrada)
        return ferramenta.executar(parametros)


def criar_registro(banco: Banco) -> RegistroFerramentas:
    def executar_nota(parametros: BaseModel) -> str:
        return salvar_nota(banco, ParametrosNota.model_validate(parametros))

    def executar_lembrete(parametros: BaseModel) -> str:
        return criar_lembrete(
            banco, ParametrosLembrete.model_validate(parametros)
        )

    def executar_hora(parametros: BaseModel) -> str:
        return consultar_hora(ParametrosHora.model_validate(parametros), banco)

    def executar_busca_notas(parametros: BaseModel) -> str:
        return buscar_notas(
            banco, ParametrosBuscaNota.model_validate(parametros)
        )

    def executar_listar_lembretes(parametros: BaseModel) -> str:
        return listar_lembretes(
            banco, ParametrosListarLembretes.model_validate(parametros)
        )

    def executar_concluir_lembrete(parametros: BaseModel) -> str:
        return concluir_lembrete(
            banco, ParametrosConcluirLembrete.model_validate(parametros)
        )

    def executar_excluir_registro(parametros: BaseModel) -> str:
        return excluir_registro(
            banco, ParametrosExcluirRegistro.model_validate(parametros)
        )

    def executar_criar_habito(parametros: BaseModel) -> str:
        return criar_habito(banco, ParametrosCriarHabito.model_validate(parametros))

    def executar_desativar_habito(parametros: BaseModel) -> str:
        return desativar_habito(
            banco, ParametrosDesativarHabito.model_validate(parametros)
        )

    def executar_listar_habitos(parametros: BaseModel) -> str:
        return listar_habitos(
            banco, ParametrosListarHabitos.model_validate(parametros)
        )

    def executar_registrar_habito(parametros: BaseModel) -> str:
        return registrar_habito(
            banco, ParametrosRegistrarHabito.model_validate(parametros)
        )

    def executar_ver_progresso(parametros: BaseModel) -> str:
        return ver_progresso(
            banco, ParametrosVerProgresso.model_validate(parametros)
        )

    def executar_marcar_folga(parametros: BaseModel) -> str:
        return marcar_folga(
            banco, ParametrosMarcarFolga.model_validate(parametros)
        )

    return RegistroFerramentas(
        [
            Ferramenta(
                nome="salvar_nota",
                descricao="Salva uma nota e suas etiquetas.",
                parametros=ParametrosNota,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_nota,
            ),
            Ferramenta(
                nome="criar_lembrete",
                descricao="Cria um lembrete com data e hora.",
                parametros=ParametrosLembrete,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_lembrete,
            ),
            Ferramenta(
                nome="consultar_hora",
                descricao="Informa a data e hora locais atuais.",
                parametros=ParametrosHora,
                nivel_risco=NivelRisco.LEITURA,
                executar=executar_hora,
            ),
            Ferramenta(
                nome="buscar_notas",
                descricao="Busca trechos relevantes nas notas salvas.",
                parametros=ParametrosBuscaNota,
                nivel_risco=NivelRisco.LEITURA,
                executar=executar_busca_notas,
            ),
            Ferramenta(
                nome="listar_lembretes",
                descricao="Lista lembretes pendentes, concluídos ou atrasados.",
                parametros=ParametrosListarLembretes,
                nivel_risco=NivelRisco.LEITURA,
                executar=executar_listar_lembretes,
            ),
            Ferramenta(
                nome="concluir_lembrete",
                descricao="Marca um lembrete como concluído.",
                parametros=ParametrosConcluirLembrete,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_concluir_lembrete,
            ),
            Ferramenta(
                nome="excluir_registro",
                descricao="Apaga uma nota ou lembrete após confirmação explícita.",
                parametros=ParametrosExcluirRegistro,
                nivel_risco=NivelRisco.SENSIVEL,
                executar=executar_excluir_registro,
            ),
            Ferramenta(
                nome="criar_habito",
                descricao="Cadastra um hábito com uma meta semanal de 1 a 7 dias.",
                parametros=ParametrosCriarHabito,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_criar_habito,
            ),
            Ferramenta(
                nome="desativar_habito",
                descricao="Desativa um hábito pelo identificador.",
                parametros=ParametrosDesativarHabito,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_desativar_habito,
            ),
            Ferramenta(
                nome="listar_habitos",
                descricao="Lista hábitos ativos ou todos os hábitos.",
                parametros=ParametrosListarHabitos,
                nivel_risco=NivelRisco.LEITURA,
                executar=executar_listar_habitos,
            ),
            Ferramenta(
                nome="registrar_habito",
                descricao="Registra o resultado de hoje para um hábito ativo.",
                parametros=ParametrosRegistrarHabito,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_registrar_habito,
            ),
            Ferramenta(
                nome="ver_progresso",
                descricao="Consulta a sequência atual e o percentual da meta semanal.",
                parametros=ParametrosVerProgresso,
                nivel_risco=NivelRisco.LEITURA,
                executar=executar_ver_progresso,
            ),
            Ferramenta(
                nome="marcar_folga",
                descricao="Marca hoje como folga para um hábito.",
                parametros=ParametrosMarcarFolga,
                nivel_risco=NivelRisco.ESCRITA,
                executar=executar_marcar_folga,
            ),
        ]
    )
