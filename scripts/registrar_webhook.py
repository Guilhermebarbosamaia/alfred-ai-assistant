"""Aponta o bot do Telegram para o Worker do Alfred na Cloudflare.

Uso (na raiz do projeto, com o `.env` preenchido):

    python scripts/registrar_webhook.py https://alfred.<sua-conta>.workers.dev

Lê TELEGRAM_BOT_TOKEN e TELEGRAM_WEBHOOK_SEGREDO do `.env` e chama
`setWebhook` (com o segredo, que o Worker confere em cada update) e
`setMyCommands`. Para voltar ao modo local (polling), use `--remover`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import Configuracao  # noqa: E402
from src.interfaces.comandos import DESCRICOES_COMANDOS  # noqa: E402
from src.interfaces.telegram_api import ClienteTelegram, ErroTelegram  # noqa: E402


def main(argumentos: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", nargs="?", help="URL do Worker (https://...)")
    parser.add_argument(
        "--remover", action="store_true", help="Remove o webhook (volta ao polling)."
    )
    opcoes = parser.parse_args(argumentos)
    configuracao = Configuracao.carregar()
    token = configuracao.telegram_bot_token.get_secret_value()
    segredo = configuracao.telegram_webhook_segredo.get_secret_value()
    if not token:
        print("Defina TELEGRAM_BOT_TOKEN no .env.")
        return 1
    cliente = ClienteTelegram(token)
    try:
        if opcoes.remover:
            cliente.chamar("deleteWebhook")
            print("Webhook removido; o bot volta a funcionar por polling.")
            return 0
        if not opcoes.url or not opcoes.url.startswith("https://"):
            print("Informe a URL https:// do Worker.")
            return 1
        if not segredo:
            print("Defina TELEGRAM_WEBHOOK_SEGREDO no .env (o mesmo do Worker).")
            return 1
        cliente.chamar(
            "setWebhook",
            url=opcoes.url.rstrip("/") + "/telegram",
            secret_token=segredo,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        cliente.chamar(
            "setMyCommands",
            commands=[
                {"command": comando, "description": descricao}
                for comando, descricao in DESCRICOES_COMANDOS
            ],
        )
    except ErroTelegram as erro:
        print(f"Não foi possível configurar o webhook: {erro}")
        return 1
    print("Webhook registrado. Envie /ajuda ao bot para testar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
