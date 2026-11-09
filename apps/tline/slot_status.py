# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

slot_status
estado de um slot temporal

2026.out  mlabru  CT_FILLED_STATUSES (antes a mesma tupla em timeline.validate e
                  CappiRecord.__post_init__)
2026.set  mlabru  PREDICTED adicionado (volumes.status = 5); INVALID passa a
                  marcar quebra de cadência na série

2026.aug  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import enum

# < SlotStatus >─────────────────────────────────────────────────────────────────────

class SlotStatus(str, enum.Enum):
    """
    estado de um slot temporal.

    OBSERVED, INTERPOLATED e PREDICTED são slots preenchidos (têm CAPPI);
    MISSING é uma lacuna; INVALID é uma quebra de cadência — nunca é
    interpolado e interrompe qualquer janela que passe por ele.
    """
    OBSERVED = "observed"
    MISSING = "missing"
    INTERPOLATED = "interpolated"
    PREDICTED = "predicted"
    INVALID = "invalid"

# < constants >────────────────────────────────────────────────────────────────────────

# status de um slot com CAPPI (a origem possível de um CappiRecord)
CT_FILLED_STATUSES = (SlotStatus.OBSERVED, SlotStatus.INTERPOLATED, SlotStatus.PREDICTED)

# < the end >──────────────────────────────────────────────────────────────────────────
