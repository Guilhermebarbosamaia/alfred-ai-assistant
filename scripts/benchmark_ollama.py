import argparse
from datetime import datetime

from ollama import Client

from src.agente.agente import Intencao
from src.agente.prompts import prompt_com_horario
from src.config import Configuracao


PEDIDO_BENCHMARK = "Me lembra de comprar café amanhã às 9h."


def _milissegundos(nanosegundos: int | None) -> float | None:
    return (
        round(nanosegundos / 1_000_000, 3)
        if nanosegundos is not None
        else None
    )


def _imprimir_metricas(rotulo: str, resposta: object) -> None:
    metricas = {
        "total_duration_ms": _milissegundos(
            getattr(resposta, "total_duration", None)
        ),
        "load_duration_ms": _milissegundos(
            getattr(resposta, "load_duration", None)
        ),
        "prompt_eval_count": getattr(resposta, "prompt_eval_count", None),
        "prompt_eval_duration_ms": _milissegundos(
            getattr(resposta, "prompt_eval_duration", None)
        ),
        "eval_count": getattr(resposta, "eval_count", None),
        "eval_duration_ms": _milissegundos(
            getattr(resposta, "eval_duration", None)
        ),
    }
    print(f"{rotulo}: {metricas}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compara uma chamada fria e uma aquecida ao Ollama."
    )
    parser.add_argument("--modelo", help="Sobrescreve MODELO_OLLAMA.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=600,
        help="Timeout por chamada em segundos (padrão: 600).",
    )
    argumentos = parser.parse_args()

    config = Configuracao.carregar()
    modelo = argumentos.modelo or config.modelo_ollama
    cliente = Client(host=str(config.ollama_host), timeout=argumentos.timeout)
    esquema = Intencao.model_json_schema()
    opcoes = {
        "temperature": 0.1,
        "num_ctx": config.ollama_num_ctx,
        "num_predict": config.ollama_num_predict,
    }
    mensagens = [
        {
            "role": "system",
            "content": (
                f"{prompt_com_horario(datetime.now().astimezone().isoformat())} "
                "Hábitos ativos disponíveis: nenhum."
            ),
        },
        {"role": "user", "content": PEDIDO_BENCHMARK},
    ]

    print(f"Modelo: {modelo}", flush=True)
    print("Descarregando o modelo antes da medição fria...", flush=True)
    cliente.chat(
        model=modelo,
        messages=[{"role": "user", "content": "ok"}],
        format=esquema,
        keep_alive=0,
        options={**opcoes, "num_predict": 1},
    )
    fria = cliente.chat(
        model=modelo,
        messages=mensagens,
        format=esquema,
        keep_alive=config.ollama_keep_alive,
        options=opcoes,
    )
    _imprimir_metricas("fria", fria)
    aquecida = cliente.chat(
        model=modelo,
        messages=mensagens,
        format=esquema,
        keep_alive=config.ollama_keep_alive,
        options=opcoes,
    )
    _imprimir_metricas("aquecida", aquecida)


if __name__ == "__main__":
    main()
