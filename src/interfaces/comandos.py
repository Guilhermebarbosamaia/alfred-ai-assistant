"""Comandos e textos do Telegram compartilhados entre polling e webhook."""

COMANDOS: dict[str, str] = {
    "start": "ajuda",
    "ajuda": "ajuda",
    "briefing": "briefing",
    "checkin": "checkin",
    "resumo": "resumo",
    "lembretes": "lembretes",
    "notas": "notas",
    "dados": "dados",
}

TEXTO_AJUDA = (
    "Comandos: /start, /ajuda, /briefing, /checkin, /resumo, "
    "/lembretes [pendentes|concluidos|atrasados], /notas e /dados. "
    "Também posso salvar notas, criar lembretes, conversar sobre "
    "notas e acompanhar hábitos. Para apagar, envie "
    "apagar nota <id> ou apagar lembrete <id>. Fotos, áudios e "
    "arquivos ainda não são suportados."
)

TEXTO_NAO_SUPORTADO = (
    "Ainda não são suportados fotos, áudios e arquivos. "
    "Envie uma mensagem de texto."
)

DESCRICOES_COMANDOS: tuple[tuple[str, str], ...] = (
    ("start", "Iniciar e ver ajuda"),
    ("ajuda", "Mostrar comandos"),
    ("briefing", "Gerar o briefing do dia"),
    ("checkin", "Iniciar check-in de hábitos"),
    ("resumo", "Gerar o resumo semanal"),
    ("lembretes", "Listar lembretes"),
    ("notas", "Listar notas recentes"),
    ("dados", "Visualizar dados salvos"),
)


def mapear_comando(comando: str) -> str | None:
    return COMANDOS.get(comando.removeprefix("/").split("@", 1)[0].lower())


def interpretar_confirmacao(texto: str) -> bool | None:
    resposta = texto.strip().casefold()
    if resposta in {"sim", "s"}:
        return True
    if resposta in {"não", "nao", "n"}:
        return False
    return None


def estado_lembretes(argumentos: list[str]) -> str | None:
    """Estado pedido em /lembretes, ou None se o uso estiver errado."""
    estado = argumentos[0].lower() if argumentos else "pendentes"
    if estado == "concluídos":
        estado = "concluidos"
    if len(argumentos) > 1 or estado not in {
        "pendentes",
        "concluidos",
        "atrasados",
    }:
        return None
    return estado


def id_exclusao(argumentos: list[str]) -> tuple[str, int] | str:
    """Valida `apagar nota|lembrete <id>`; devolve (tipo, id) ou o erro."""
    if len(argumentos) != 2 or argumentos[0].lower() not in {"nota", "lembrete"}:
        return "Uso: apagar nota <id> ou apagar lembrete <id>"
    try:
        registro_id = int(argumentos[1].removeprefix("#"))
        if registro_id < 1:
            raise ValueError
    except ValueError:
        return "O id deve ser um número positivo."
    return argumentos[0].lower(), registro_id
