# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_presets
identificadores dos produtos CAPPI (coluna cappi_products.preset) e o preset
ativo de cada alcance.

só os ids, sem os parâmetros de gridding: estes ficam em nwc_cappi_cfg, que
importa o cappi.cp_core (Py-ART).  Banco, séries, interpolação e sequências
precisam saber qual produto usar, mas não de como ele é gerado, e não devem
depender do Py-ART por isso.

um id é versionado: mudar qualquer parâmetro de um preset (geometria, nível,
campo, valor de "sem dado") exige um id novo, para que .npy diferentes nunca
se misturem numa série.

valores de um .npy dos presets ativos (float32, dBZ):
  MF_NODATA  (-9999)  fora da cobertura do feixe no nível
  MF_NO_ECHO (-32)    na cobertura, sem eco
  outro               refletividade medida

2026.out  mlabru  refatoração para legibilidade: MF_NODATA daqui é a única
                  definição do -9999 no nowcast (nwcConvert, nwcInterpolate,
                  nwcCapGen e as migrações apontam para cá); _raise_unknown_range
                  com a mensagem que nwc_cappi_cfg também usa
2026.set  mlabru  v2: cobertura separada de eco (MF_NODATA x MF_NO_ECHO); os v1
                  (-9999 também nos gates sem eco, que o Py-ART mascara)
                  passam a legado: neles valid_fraction era a fração com eco,
                  e o filtro de cobertura do nwcSequences descartava as
                  janelas de tempo bom
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import typing

# < constants >────────────────────────────────────────────────────────────────────────

# pixel fora da cobertura do feixe.  Mesmo valor de cp_core.MF_NODATA (o núcleo
# CAPPI é compartilhado com o 21.mvol e não importa o nowcast); é a única
# definição do lado do nowcast
MF_NODATA = -9999.0

# pixel coberto, sem eco.  -32 dBZ é o piso de ruído que a escala do PPI
# do 21.mvol usava (rd_radar.CI_PPI_VMIN, módulo já removido) e fica abaixo de todo eco medido (o
# menor visto nos volumes reais foi -10.7 dBZ); em Z linear (~6e-4 mm⁶/m³)
# é praticamente 0, o que a média em Z do nwcInterpolate trata como céu limpo
MF_NO_ECHO = -32.0

# alcance (km) -> id do preset ativo (o que o conversor gera e as séries
# usam).  Os parâmetros de cada um estão em nwc_cappi_cfg.
MDCT_ACTIVE = {250: "nwc250v2",
               400: "nwc400v2"}

# ids de legado: nunca são gerados e ficam fora das séries.  Só identificam
# produtos migrados (nwc_migrate_products):
# - v0: .npy de antes da correção do cp_core.extract_level (nc_process_volume
#   e o volProcess do 21.mvol até 2026.set), ±250 km 500×500 nos dois
#   alcances, sem dado gravado como 0.0: "sem dado" e "0 dBZ" não se
#   distinguem;
# - v1: -9999 correto, mas também nos gates sem eco (o Py-ART os mascara):
#   "fora do alcance" e "sem eco" não se distinguem.  O volProcess do 21.mvol
#   gera o 250 nessa forma até o corte.
MDCT_LEGACY = {"nwc250v0": "±250 km 500² (250 km), sem dado = 0.0",
               "nwc400v0": "±250 km 500² (400 km), sem dado = 0.0",
               "nwc250v1": "±250 km 500², -9999 = sem cobertura ou sem eco",
               "nwc400v1": "±300 km 600², -9999 = sem cobertura ou sem eco"}

# ─────────────────────────────────────────────────────────────────────────────────────
def active_id(fi_alcance: int) -> str:
    """
    id do preset ativo de um alcance

    :param fi_alcance: alcance do radar (km), como em volumes.alcance
    :returns: id do preset, ex.: "nwc250v2"
    :raises ValueError: alcance sem preset
    """
    li_alcance = int(fi_alcance)

    if li_alcance not in MDCT_ACTIVE:
        raise_unknown_range(fi_alcance)

    return MDCT_ACTIVE[li_alcance]

# ─────────────────────────────────────────────────────────────────────────────────────
def legacy_id(fi_alcance: int) -> str:
    """
    id de legado v0 (ver MDCT_LEGACY) dos .npy antigos de um alcance

    :param fi_alcance: alcance do radar (km)
    :returns: ex.: "nwc250v0"
    :raises ValueError: alcance sem id de legado
    """
    ls_id = f"nwc{int(fi_alcance)}v0"

    if ls_id not in MDCT_LEGACY:
        raise ValueError(f"alcance sem id de legado: {fi_alcance}")

    return ls_id

# ─────────────────────────────────────────────────────────────────────────────────────
def raise_unknown_range(fi_alcance: int) -> typing.NoReturn:
    """
    erro padrão de "alcance sem preset" (usado aqui e em nwc_cappi_cfg)

    :raises ValueError: sempre
    """
    raise ValueError(f"alcance sem preset CAPPI: {fi_alcance} "
                     f"(definidos: {sorted(MDCT_ACTIVE)})")

# < the end >──────────────────────────────────────────────────────────────────────────
