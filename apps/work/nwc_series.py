# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_series
séries temporais (TimelineCollection) a partir dos produtos de
`cappi_products` (só o preset ativo de cada alcance, ver nwc_presets)

ponte entre o banco e o domínio: tline não conhece SQLite, e as etapas
(interpolação, sequências) não montam Timeline à mão.

2026.out  mlabru  refatoração para legibilidade: row_to_record dividido
                  (_volume_record_of); epoch -> datetime por nwc_time
2026.out  mlabru  collection_of(): parâmetros das Timelines num lugar só (a
                  projeção do nwcSequences --dry-run monta a coleção com os
                  interpolados planejados)
2026.set  mlabru  lê cappi_products (series_rows) em vez de volumes: origem
                  pela coluna origin, CAPPI em <cappi_dir>/<preset>/<R>_<A>/
2026.set  mlabru  cappi_path resolvido pela regra do 21.mvol
                  (<cappi_dir>/<R>_<A>/<nome>, R e A tirados do nome; ver
                  nwc_cappi_path): o diretório gravado no banco é ignorado, e
                  nome divergente da linha deixa o volume fora da série

2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import datetime
import logging

# locals
import work.nwc_cappi_path as cp
import work.nwc_config as cf
import work.nwc_database as db
import work.nwc_time as nt

import tline.cappi_record as cr
import tline.slot_status as ss
import tline.tl_collection as tc
import dbase.vol_record as vr
import dbase.vol_product_key as pk

# < constants >────────────────────────────────────────────────────────────────────────

# cappi_products.origin -> origem do CAPPI na série
CDCT_ORIGIN = { db.CS_ORIGIN_OBSERVED:     ss.SlotStatus.OBSERVED,
                db.CS_ORIGIN_INTERPOLATED: ss.SlotStatus.INTERPOLATED,
                db.CS_ORIGIN_PREDICTED:    ss.SlotStatus.PREDICTED }

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────────────
def build_collection(flst_rows: list[dict]) -> tc.TimelineCollection:
    """
    monta as Timelines com os parâmetros de [timeline]/[interpolate] do
    config.  Linhas com radar/alcance fora do que ProductKey aceita, ou cujo
    nome de CAPPI está fora do padrão ou diverge da linha, são logadas e
    ignoradas (não derrubam as demais séries).
    """
    llst_records = []

    for ldct_row in flst_rows:
        try:
            llst_records.append(row_to_record(ldct_row))

        except ValueError as l_error:
            M_LOG.warning("volume ignorado (%s %s %s): %s", ldct_row["radar"],
                          ldct_row["alcance"], ldct_row["timestamp"], l_error)

    return collection_of(llst_records)

# ─────────────────────────────────────────────────────────────────────────────────────
def collection_of(flst_records: list) -> tc.TimelineCollection:
    """
    CappiRecords -> Timelines, com os parâmetros de [timeline]/[interpolate]
    do config (também usado pela projeção do nwcSequences --dry-run, que
    acrescenta os interpolados planejados)
    """
    return tc.TimelineCollection.from_records(
               flst_records,
               f_interval=datetime.timedelta(seconds=cf.G_CONFIG.interval_s),
               f_tolerance=datetime.timedelta(seconds=cf.G_CONFIG.tolerance_s),
               fi_max_interpolated_gap=cf.G_CONFIG.max_gap)

# ─────────────────────────────────────────────────────────────────────────────────────
def load_collection() -> tc.TimelineCollection:
    """
    lê as séries do banco (transação de leitura) e monta as Timelines
    """
    with db.transaction() as ldb:
        llst_rows = ldb.series_rows()

    return build_collection(llst_rows)

# ─────────────────────────────────────────────────────────────────────────────────────
def row_to_record(fdct_row: dict) -> cr.CappiRecord:
    """
    linha de `cappi_products` (series_rows) -> CappiRecord

    :raises ValueError: radar/alcance inválidos, cappi_path fora do padrão ou
                        divergente da linha (ver nwc_cappi_path), ou origem
                        desconhecida
    """
    if fdct_row["origin"] not in CDCT_ORIGIN:
        raise ValueError(f"origem desconhecida: {fdct_row['origin']!r}")

    # o arquivo é localizado pelo nome (o diretório gravado no banco é ignorado)
    lpth_preset_dir = cp.preset_dir(cf.G_CONFIG.cappi_dir, fdct_row["preset"])
    lpth_cappi = cp.resolve_cappi_path(fdct_row["cappi_path"], fdct_row["radar"],
                                       fdct_row["alcance"], fdct_row["timestamp"],
                                       lpth_preset_dir)

    return cr.CappiRecord(volume=_volume_record_of(fdct_row),
                          cappi_path=lpth_cappi,
                          origin=CDCT_ORIGIN[fdct_row["origin"]],
                          valid_fraction=fdct_row["valid_fraction"],
                          rain_fraction=fdct_row["rain_fraction"])

# ─────────────────────────────────────────────────────────────────────────────────────
def _volume_record_of(fdct_row: dict) -> vr.VolumeRecord:
    """
    a parte "volume" de uma linha de series_rows (VolumeRecord é do 21.mvol)

    :raises ValueError: radar/alcance que ProductKey não aceita
    """
    l_product = pk.ProductKey(station=fdct_row["radar"], range_km=fdct_row["alcance"])

    return vr.VolumeRecord(product=l_product,
                           timestamp=nt.epoch_to_utc(fdct_row["timestamp"]),
                           filename=fdct_row["fullpath"])

# < the end >──────────────────────────────────────────────────────────────────────────
