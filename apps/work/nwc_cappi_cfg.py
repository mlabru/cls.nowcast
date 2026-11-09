# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_cappi_cfg
presets de geração dos CAPPIs .npy do nowcast, um por alcance do radar.

os parâmetros de gridding são uma decisão do nowcast (entrada do RainNet),
por isso vivem aqui e não no núcleo cappi.cp_core (apps/cappi, também usado
pelo visualizador do 21.mvol via symlink), que fica só com a mecânica (CappiConfig, read_radar, grid_radar, ...).

cada preset tem um identificador versionado (SCappiPreset.id, definido em
nwc_presets) que é a chave do produto gerado: mudar qualquer parâmetro de um
preset exige um id novo, para que .npy diferentes nunca se misturem numa
série.

2026.out  mlabru  refatoração para legibilidade: logger sem uso removido;
                  preset_for usa a mesma mensagem de erro de nwc_presets
2026.set  mlabru  v2 (nwc_presets): mesma CappiConfig do v1; a diferença (cobertura
                  x eco) está na extração, em nwcConvert
2026.set  mlabru  ids (ativos e de legado) em nwc_presets, sem Py-ART
2026.set  mlabru  initial version (Linux/Python) -- CFG_NWC_250 idêntico ao
                  antigo cp_core.CFG_NOWCAST (21.mvol); CFG_NWC_400 novo,
                  com extensão limitada à cobertura real a 3000 m
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import dataclasses

# cappi (núcleo CAPPI deste projeto; o 21.mvol o usa via symlink)
import cappi.cp_core as cc

# locals
import work.nwc_presets as ps

# < SCappiPreset >─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SCappiPreset:
    """
    preset de um produto CAPPI: identificador versionado + parâmetros
    """
    # identificador do produto (chave junto com radar/alcance/timestamp)
    id: str
    # parâmetros de leitura e gridding
    cfg: cc.CappiConfig

# < presets >──────────────────────────────────────────────────────────────────────────

# alcance 250 km: 1 nível a 3000 m acima do radar, ±250 km, 500×500 (1 km/px).
# idêntico ao antigo cp_core.CFG_NOWCAST, para que os .npy já gerados pelo
# 21.mvol continuem na mesma série sem reprocessamento.
#
# (a justificativa de min_radius 1750 m, Barnes2 e dist_beam está no preset
# cp_core.CFG_VIEWER: o default de 250 m causa artefatos de "penas" em radar
# de longo alcance; Barnes2 dá campos contínuos, melhores para a interpolação
# por optical flow; dist_beam faz o raio de influência crescer com a
# distância, acompanhando o alargamento do feixe)
CFG_NWC_250 = cc.CappiConfig(reader="pyart",
                             field_name="corrected_reflectivity",
                             z_min_m=3000.0,
                             z_max_m=3000.0,
                             n_levels=1,
                             altitude_ref="radar",
                             extent_mode="fixed",
                             extent_m=250000.0,
                             shape_xy=(500, 500),
                             resolution_m=None,
                             weighting_function="Barnes2",
                             gridding_algo="map_gates_to_grid",
                             roi_func="dist_beam",
                             min_radius=1750.0)

# alcance 400 km: mesmos parâmetros de gridding e a mesma resolução (1 km/px),
# mas ±300 km, 600×600.  a 3000 m acima do radar o feixe mais baixo (0.5°)
# passa por cima do nível a partir de ~300 km (curvatura da Terra, 4/3 R):
# medido em volumes reais STI-400 e CGU-400 com um campo sintético constante,
# a cobertura é ~1.0 até 250 km, ~0.93 em 250-300 km, 0.12 em 300-350 km e
# 0 além de ~320 km -- estender a ±400 km só acrescentaria borda sem dado.
# os volumes de 400 km só têm 3 elevações (0.5/1.5/2.5°), então o centro
# (0-50 km) tem um cone de silêncio (~0.68 de cobertura), coberto pelo 250.
CFG_NWC_400 = dataclasses.replace(CFG_NWC_250,
                                  extent_m=300000.0,
                                  shape_xy=(600, 600))

# alcance (km) -> preset ativo
MDCT_PRESETS = {250: SCappiPreset(id=ps.active_id(250), cfg=CFG_NWC_250),
                400: SCappiPreset(id=ps.active_id(400), cfg=CFG_NWC_400)}

# ─────────────────────────────────────────────────────────────────────────────────────
def preset_for(fi_alcance: int) -> SCappiPreset:
    """
    preset do CAPPI para um alcance

    :param fi_alcance: alcance do radar (km), como em volumes.alcance

    :returns: SCappiPreset do alcance

    :raises ValueError: alcance sem preset definido
    """
    li_alcance = int(fi_alcance)

    if li_alcance not in MDCT_PRESETS:
        ps.raise_unknown_range(fi_alcance)

    return MDCT_PRESETS[li_alcance]

# < the end >──────────────────────────────────────────────────────────────────────────
