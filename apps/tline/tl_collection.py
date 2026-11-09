# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

tl_collection
conjunto de Timelines, uma por produto (estação + alcance)

2026.out  mlabru  refatoração para legibilidade: campos interval/tolerance/
                  max_interpolated_gap removidos (eram gravados e nunca
                  lidos: cada Timeline guarda os seus); docstrings
2026.set  mlabru  from_files() -> from_records() (fonte é o banco, não nomes
                  de .mvol); get_or_create() removido junto com o modo
                  incremental da Timeline; imports que só existiam nas
                  anotações (timedelta, ProductKey, Iterable, Path) corrigidos

2026.aug  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import dataclasses
import datetime
import typing

# locals
import tline.cappi_record as cr
import tline.timeline as tl
import tline.tl_consts as ct
import dbase.vol_product_key as pk

# < TimelineCollection >───────────────────────────────────────────────────────────────

@dataclasses.dataclass
class TimelineCollection:
    """
    conjunto de Timelines, uma para cada station + range.  Iterar dá pares
    (ProductKey, Timeline) em ordem de produto.
    """
    # produto -> Timeline
    timelines: dict = dataclasses.field(default_factory=dict)

    # ---------------------------------------------------------------------------------
    def __getitem__(self, f_product: pk.ProductKey) -> tl.Timeline:
        """
        Timeline de um produto (KeyError se não existe)
        """
        return self.timelines[f_product]

    def __iter__(self) -> typing.Iterator[tuple]:
        """
        pares (ProductKey, Timeline), ordenados por produto
        """
        return iter(sorted(self.timelines.items()))

    def __len__(self) -> int:
        """
        número de produtos
        """
        return len(self.timelines)

    # ---------------------------------------------------------------------------------
    @classmethod
    def from_records(cls, f_records: typing.Iterable[cr.CappiRecord], *,
                     f_interval: datetime.timedelta = ct.CI_DEFAULT_INTERVAL,
                     f_tolerance: datetime.timedelta = ct.CI_DEFAULT_TOLERANCE,
                     fi_max_interpolated_gap: int = ct.CI_DEFAULT_MAX_INTERPOLATED_GAP,
                     ) -> TimelineCollection:
        """
        agrupa os records por produto e cria uma Timeline para cada um, com
        os mesmos parâmetros (ver Timeline.from_records)
        """
        ldct_records_by_product: dict = {}

        for l_record in f_records:
            ldct_records_by_product.setdefault(l_record.product, []).append(l_record)

        l_collection = cls()

        for l_product, llst_records in sorted(ldct_records_by_product.items()):
            l_collection.timelines[l_product] = tl.Timeline.from_records(
                llst_records,
                f_interval=f_interval,
                f_tolerance=f_tolerance,
                fi_max_interpolated_gap=fi_max_interpolated_gap)

        return l_collection

    # ---------------------------------------------------------------------------------
    def get(self, f_product: pk.ProductKey, f_default=None):
        """
        Timeline de um produto, ou f_default se o produto não existe
        """
        return self.timelines.get(f_product, f_default)

    # ---------------------------------------------------------------------------------
    def summary(self) -> str:
        """
        Timeline.summary() de todos os produtos
        """
        llst_lines = ["Timeline Collection", "=" * 32]

        for _, l_timeline in self:
            llst_lines.append("")
            llst_lines.append(l_timeline.summary())

        return "\n".join(llst_lines)

    # ---------------------------------------------------------------------------------
    @property
    def products(self) -> list:
        """
        produtos, ordenados
        """
        return sorted(self.timelines.keys())

# < the end >──────────────────────────────────────────────────────────────────────────
