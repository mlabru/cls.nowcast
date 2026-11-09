# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

tl_slot
representa um instante dentro da série temporal

2026.out  mlabru  refatoração para legibilidade: docstrings; is_invalid (sem uso)
                  removido
2026.set  mlabru  expected_timestamp -> timestamp: com a associação encadeada
                  não existe mais uma grade fixa de instantes esperados.  Um
                  slot preenchido tem o horário do seu CAPPI; uma lacuna tem
                  o horário estimado entre os vizinhos reais.  record passa a
                  ser um CappiRecord (o status vem da origem do CAPPI)

2026.aug  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import dataclasses
import datetime
import pathlib

# locals
import tline.cappi_record as cr
import tline.slot_status as ss

# < TimeSlot >────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class TimeSlot:
    """
    um instante da série temporal: OBSERVED, INTERPOLATED ou PREDICTED
    (preenchido, com record), MISSING (lacuna) ou INVALID (quebra de cadência)
    """
    # instante UTC (real se preenchido, estimado se lacuna)
    timestamp: datetime.datetime
    # CAPPI do slot
    record: cr.CappiRecord | None = None
    # status
    status: ss.SlotStatus = ss.SlotStatus.MISSING

    # número de slots faltantes consecutivos pertencentes à mesma lacuna
    gap_length: int = 0

    # posição dentro da lacuna (1..gap_length)
    gap_index: int = 0

    # ---------------------------------------------------------------------------------
    @property
    def epoch(self) -> int:
        """
        timestamp em epoch UTC (s)
        """
        return int(self.timestamp.timestamp())

    @property
    def cappi_path(self) -> pathlib.Path | None:
        """
        .npy do slot (None se o slot não tem CAPPI)
        """
        if self.record is None:
            return None

        return self.record.cappi_path

    @property
    def is_filled(self) -> bool:
        """
        slot com CAPPI (observado, interpolado ou previsto)
        """
        return self.record is not None

    @property
    def is_interpolated(self) -> bool:
        """
        slot preenchido pelo nwcInterpolate
        """
        return self.status == ss.SlotStatus.INTERPOLATED

    @property
    def is_missing(self) -> bool:
        """
        slot faltante (parte de uma lacuna)
        """
        return self.status == ss.SlotStatus.MISSING

    @property
    def is_observed(self) -> bool:
        """
        slot com CAPPI convertido de um volume real
        """
        return self.status == ss.SlotStatus.OBSERVED

    @property
    def is_predicted(self) -> bool:
        """
        slot com CAPPI previsto pelo modelo
        """
        return self.status == ss.SlotStatus.PREDICTED

# < the end >──────────────────────────────────────────────────────────────────────────
