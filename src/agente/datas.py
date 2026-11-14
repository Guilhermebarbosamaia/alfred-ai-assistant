import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


_HORARIO = re.compile(
    r"(?:às?\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:h|horas?)?", re.IGNORECASE
)
_INTERVALO = re.compile(
    r"(?:daqui\s+a|em)\s+(\d+)\s*(horas?|h|minutos?|min)\b", re.IGNORECASE
)
_DATA_NUMERICA = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")


def resolver_data_relativa(
    expressao: str, agora: datetime, fuso: ZoneInfo
) -> datetime | None:
    texto = expressao.strip().lower()
    if not texto:
        return None
    horario_local = (
        agora.astimezone(fuso)
        if agora.tzinfo
        else agora.replace(tzinfo=fuso)
    )

    intervalo = _INTERVALO.search(texto)
    if intervalo:
        quantidade = int(intervalo.group(1))
        unidade = intervalo.group(2)
        delta = (
            timedelta(minutes=quantidade)
            if unidade.startswith("min")
            else timedelta(hours=quantidade)
        )
        return (horario_local.astimezone(timezone.utc) + delta).astimezone(fuso)

    data_numerica = _DATA_NUMERICA.search(texto)
    if data_numerica:
        dia, mes = int(data_numerica.group(1)), int(data_numerica.group(2))
        ano = int(data_numerica.group(3) or horario_local.year)
        try:
            data = datetime(ano, mes, dia).date()
        except ValueError:
            return None
        if data_numerica.group(3) is None and data < horario_local.date():
            data = data.replace(year=data.year + 1)
        texto_horario = texto.replace(data_numerica.group(0), " ")
    elif "amanhã" in texto or "amanha" in texto:
        data = horario_local.date() + timedelta(days=1)
        texto_horario = texto
    elif "hoje" in texto:
        data = horario_local.date()
        texto_horario = texto
    else:
        return None

    hora = _HORARIO.search(texto_horario)
    if hora:
        horas = int(hora.group(1))
        minutos = int(hora.group(2) or 0)
        if horas > 23 or minutos > 59:
            return None
    else:
        if "noite" in texto:
            horas, minutos = 20, 0
        else:
            return None
    resultado = datetime.combine(
        data, datetime.min.time(), tzinfo=fuso
    ).replace(hour=horas, minute=minutos)
    if "hoje" in texto and resultado <= horario_local:
        return None
    return resultado
