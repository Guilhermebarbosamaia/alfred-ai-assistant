"""Adaptador do SQLite de um Durable Object da Cloudflare.

Imita a parte da API de `sqlite3.Connection` usada por `Banco`, sobre
`ctx.storage.sql` (que é síncrono). O Durable Object executa uma requisição
por vez e grava de forma atômica tudo o que acontece sem `await` no meio; por
isso `BEGIN` vira operação vazia e o bloco `with` não precisa de commit.
"""

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Protocol

_COMANDOS_DE_ESCRITA = ("INSERT", "UPDATE", "DELETE", "REPLACE")


class CursorSQL(Protocol):
    columnNames: Any
    def raw(self) -> Any: ...


class StorageSQL(Protocol):
    def exec(self, consulta: str, *parametros: object) -> CursorSQL: ...


class Linha(dict):
    """Linha acessível por nome ou posição, como `sqlite3.Row`."""

    def __init__(self, nomes: Sequence[str], valores: Sequence[object]) -> None:
        super().__init__(zip(nomes, valores))
        self._valores = tuple(valores)

    def __getitem__(self, chave: object) -> object:
        if isinstance(chave, int):
            return self._valores[chave]
        return super().__getitem__(chave)


class CursorDO:
    def __init__(
        self,
        linhas: list[Linha],
        rowcount: int = -1,
        lastrowid: int | None = None,
    ) -> None:
        self._linhas = linhas
        self._posicao = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> Linha | None:
        if self._posicao >= len(self._linhas):
            return None
        linha = self._linhas[self._posicao]
        self._posicao += 1
        return linha

    def fetchall(self) -> list[Linha]:
        restantes = self._linhas[self._posicao :]
        self._posicao = len(self._linhas)
        return restantes

    def __iter__(self) -> Iterator[Linha]:
        return iter(self.fetchall())


def _para_lista(valor: Any) -> list[Any]:
    if hasattr(valor, "toArray"):
        valor = valor.toArray()
    if hasattr(valor, "to_py"):
        valor = valor.to_py()
    return list(valor)


def _converter_erro(erro: Exception) -> sqlite3.Error:
    mensagem = str(erro)
    if "constraint failed" in mensagem.lower():
        return sqlite3.IntegrityError(mensagem)
    return sqlite3.OperationalError(mensagem)


class ConexaoDO:
    """Conexão compatível com o uso que `Banco` faz de `sqlite3.Connection`."""

    def __init__(self, sql: StorageSQL) -> None:
        self._sql = sql

    def __enter__(self) -> "ConexaoDO":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def close(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def _exec(self, consulta: str, parametros: Sequence[object] = ()) -> CursorSQL:
        try:
            return self._sql.exec(consulta, *parametros)
        except sqlite3.Error:
            raise
        except Exception as erro:
            raise _converter_erro(erro) from None

    def _escalar(self, consulta: str) -> int:
        cursor = self._exec(consulta)
        return int(_para_lista(cursor.raw())[0][0])

    def execute(
        self, consulta: str, parametros: Sequence[object] = ()
    ) -> CursorDO:
        comando = consulta.lstrip().upper()
        if comando.startswith("BEGIN"):
            return CursorDO([])
        cursor = self._exec(consulta, tuple(parametros))
        nomes = [str(nome) for nome in _para_lista(cursor.columnNames)]
        linhas = [
            Linha(nomes, _para_lista(valores)) for valores in _para_lista(cursor.raw())
        ]
        if not comando.startswith(_COMANDOS_DE_ESCRITA):
            return CursorDO(linhas)
        rowcount = self._escalar("SELECT changes()")
        lastrowid = (
            self._escalar("SELECT last_insert_rowid()")
            if comando.startswith(("INSERT", "REPLACE"))
            else None
        )
        return CursorDO(linhas, rowcount=rowcount, lastrowid=lastrowid)

    def executemany(
        self, consulta: str, sequencia: Iterable[Sequence[object]]
    ) -> CursorDO:
        total = 0
        for parametros in sequencia:
            total += max(self.execute(consulta, parametros).rowcount, 0)
        return CursorDO([], rowcount=total)

    def executescript(self, script: str) -> CursorDO:
        self._exec(script)
        return CursorDO([])
