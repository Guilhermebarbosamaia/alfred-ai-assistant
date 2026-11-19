"""Copia o pacote `src/` da raiz para `cloudflare/worker/src` antes do build.

O Worker empacota todos os arquivos da pasta do `main` (`worker/`); por isso
ela só tem o `entry.py` e esta cópia, sem `.venv/`, `dados/`, `testes/` ou
o `.env`.
"""

import shutil
from pathlib import Path

PASTA = Path(__file__).resolve().parent
ORIGEM = PASTA.parent / "src"
DESTINO = PASTA / "worker" / "src"

shutil.rmtree(DESTINO, ignore_errors=True)
shutil.copytree(
    ORIGEM, DESTINO, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
)
print(f"Código copiado de {ORIGEM} para {DESTINO}.")
