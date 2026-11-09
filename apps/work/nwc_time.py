# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_time
conversões de data/hora usadas pelos estágios e pelos dry-runs.

regra do projeto: todo instante é UTC.  No banco, um instante é um epoch
(segundos desde 1970-01-01 00:00:00 UTC, inteiro); na tela e na linha de
comando, um datetime "aware" (com tzinfo = UTC).  Este módulo é o único lugar
que converte entre os dois, para que ninguém crie sem querer um datetime
"naive" (sem fuso), que o Python interpretaria no fuso local da máquina.

(o pacote tline tem a sua própria cópia de epoch -> datetime: ele não depende
de nada em work/, ver o Guia de Manutenção)

2026.out  mlabru  initial version: reúne o _from_epoch/_parse_utc do
                  nwcInterpolate (que o nwcSequences importava como nomes
                  privados) e a conversão em linha do nwc_series
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import datetime

# ─────────────────────────────────────────────────────────────────────────────────────
def epoch_to_utc(fi_epoch: int) -> datetime.datetime:
    """
    converte um epoch (segundos, UTC) em datetime com fuso UTC.

    :param fi_epoch: segundos desde 1970-01-01 00:00:00 UTC (como em
                     volumes.timestamp e cappi_products.timestamp)
    :returns: datetime aware, tzinfo = UTC

    exemplo: epoch_to_utc(0) -> 1970-01-01 00:00:00+00:00
    """
    return datetime.datetime.fromtimestamp(fi_epoch, tz=datetime.timezone.utc)

# ─────────────────────────────────────────────────────────────────────────────────────
def parse_utc_iso(fs_value: str) -> datetime.datetime:
    """
    converte um texto ISO 8601 em datetime UTC.  Usado como `type=` dos
    argumentos --from/--to do argparse.

    - sem fuso ("2026-08-01", "2026-08-01T12:00"): o valor já é UTC;
    - com fuso ("2026-08-01T09:00-03:00"): convertido para UTC.

    :param fs_value: data ("AAAA-MM-DD") ou data e hora em ISO 8601
    :returns: datetime aware, tzinfo = UTC
    :raises ValueError: texto fora do formato ISO 8601 (o argparse transforma
                        em mensagem de uso)
    """
    ldt_parsed = datetime.datetime.fromisoformat(fs_value)

    if ldt_parsed.tzinfo is None:
        # sem fuso: o projeto inteiro é UTC, então só anota o fuso
        return ldt_parsed.replace(tzinfo=datetime.timezone.utc)

    # com fuso: converte o instante para UTC
    return ldt_parsed.astimezone(datetime.timezone.utc)

# < the end >──────────────────────────────────────────────────────────────────────────
