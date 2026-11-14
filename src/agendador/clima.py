import json
import logging
from http.client import HTTPException
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def obter_clima(cidade: str) -> str | None:
    try:
        parametros = urlencode({"name": cidade, "count": 1, "language": "pt"})
        with urlopen(
            f"https://geocoding-api.open-meteo.com/v1/search?{parametros}",
            timeout=3,
        ) as resposta:
            local = json.loads(resposta.read())
        resultados = local.get("results", [])
        if not resultados:
            return None
        latitude = resultados[0]["latitude"]
        longitude = resultados[0]["longitude"]
        parametros = urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m",
                "timezone": "UTC",
            }
        )
        with urlopen(
            f"https://api.open-meteo.com/v1/forecast?{parametros}",
            timeout=3,
        ) as resposta:
            previsao = json.loads(resposta.read())
        temperatura = previsao["current"]["temperature_2m"]
        return f"{cidade}: {temperatura} °C"
    except (
        URLError,
        OSError,
        TimeoutError,
        HTTPException,
        json.JSONDecodeError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as erro:
        logging.warning("Não foi possível consultar o clima: %s", erro)
        return None
