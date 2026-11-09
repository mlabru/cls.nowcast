# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

cappi_record
volume de uma série com CAPPI disponível (linha de `volumes` com cappi_path)

VolumeRecord é compartilhado com o 21.mvol (symlink apps/dbase) e
não pode ganhar campos do nowcast — por isso a composição em vez de herança
ou cópia.

2026.out  mlabru  refatoração para legibilidade: docstrings; origens válidas em
                  slot_status.CT_FILLED_STATUSES
2026.set  mlabru  vol_record/vol_product_key importados de dbase (apps/dbase)
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import dataclasses
import datetime
import pathlib

# locals
import tline.slot_status as ss
import dbase.vol_record as vr
import dbase.vol_product_key as pk

# < CappiRecord >─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class CappiRecord:
    """
    volume (VolumeRecord) + o que o nowcast precisa dele: o CAPPI, a origem
    (observado, interpolado ou previsto) e as frações de pixels
    """
    # volume (filename = volumes.fullpath)
    volume: vr.VolumeRecord
    # CAPPI .npy
    cappi_path: pathlib.Path
    # origem do CAPPI
    origin: ss.SlotStatus = ss.SlotStatus.OBSERVED
    # frações de pixels (0–1), calculadas sem o fill −9999
    valid_fraction: float | None = None
    rain_fraction: float | None = None

    # ---------------------------------------------------------------------------------
    def __post_init__(self) -> None:
        """
        valida a origem e converte cappi_path para Path (chamado pelo
        dataclass logo após o __init__)

        :raises ValueError: origem que não é de CAPPI (MISSING, INVALID)
        """
        if self.origin not in ss.CT_FILLED_STATUSES:
            raise ValueError(f"origem inválida para um CAPPI: {self.origin}")

        # dataclass frozen: a atribuição normal levantaria FrozenInstanceError
        if not isinstance(self.cappi_path, pathlib.Path):
            object.__setattr__(self, "cappi_path", pathlib.Path(self.cappi_path))

    # ---------------------------------------------------------------------------------
    @property
    def epoch(self) -> int:
        """
        timestamp em epoch UTC (s), a chave de volumes.timestamp
        """
        return int(self.volume.timestamp_utc.timestamp())

    @property
    def product(self) -> pk.ProductKey:
        """
        produto (estação + alcance) do volume
        """
        return self.volume.product

    @property
    def timestamp_utc(self) -> datetime.datetime:
        """
        instante do volume (datetime aware, UTC)
        """
        return self.volume.timestamp_utc

# < the end >──────────────────────────────────────────────────────────────────────────
