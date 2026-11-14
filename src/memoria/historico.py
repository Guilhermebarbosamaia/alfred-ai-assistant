from src.memoria.banco import Banco


class Historico:
    def __init__(
        self, banco: Banco, limite: int = 10, sessao_id: str = "terminal"
    ) -> None:
        self._banco = banco
        self._limite = limite
        self._sessao_id = sessao_id

    def registrar(self, papel: str, conteudo: str) -> None:
        self._banco.registrar_mensagem(papel, conteudo, self._sessao_id)

    def recentes(self) -> list[dict[str, str]]:
        return self._banco.ultimas_mensagens(self._limite, self._sessao_id)
