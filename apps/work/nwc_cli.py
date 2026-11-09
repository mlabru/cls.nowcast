# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_cli
peças de linha de comando compartilhadas pelos programas de apps/.

- logged_run(): o "cabeçalho" e o "rodapé" de toda execução real no log
  (linha de '=', título, duração);
- os filtros de listagem do --dry-run (-s/--series, --from, --to), iguais no
  nwcInterpolate e no nwcSequences:
    add_listing_filter_arguments()  declara os argumentos;
    reject_filters_without_dry_run() recusa filtros num run real (o run real
                                     sempre processa todas as séries, e um
                                     filtro ignorado em silêncio enganaria);
    SListingFilter.from_arguments()  monta o filtro que a listagem consulta.

2026.out  mlabru  initial version: a parte de argparse e o banner/duração que
                  estavam copiados em nwcConvert, nwcInterpolate,
                  nwcSequences e nwcSchema
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime
import logging
import typing

# locals
import work.nwc_time as nt

# < constants >────────────────────────────────────────────────────────────────────────

# linha que delimita uma execução no log
CS_RUN_SEPARATOR = "=" * 45

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def logged_run(fs_title: str) -> typing.Iterator[None]:
    """
    delimita uma execução no log: separador e título no início; duração e
    separador no fim, mesmo que o bloco termine com exceção.

    uso:
        with nwc_cli.logged_run("Convert"):
            ...trabalho...

    :param fs_title: texto que segue o ">>> " na primeira linha
    """
    ldt_start = datetime.datetime.now(datetime.timezone.utc)

    M_LOG.info(CS_RUN_SEPARATOR)
    M_LOG.info(">>> %s", fs_title)

    try:
        yield

    finally:
        ldt_elapsed = datetime.datetime.now(datetime.timezone.utc) - ldt_start

        M_LOG.info("duração: %s", ldt_elapsed)
        M_LOG.info(CS_RUN_SEPARATOR)

# ─────────────────────────────────────────────────────────────────────────────────────
def add_listing_filter_arguments(f_parser: argparse.ArgumentParser, fs_from_help: str,
                                 fs_to_help: str) -> None:
    """
    declara -s/--series, --from e --to (filtros que só valem com --dry-run).

    :param f_parser: parser do programa (que já tem --dry-run)
    :param fs_from_help: texto de ajuda de --from (o que o início filtra)
    :param fs_to_help: texto de ajuda de --to
    """
    f_parser.add_argument("-s", "--series", action="append", metavar="EEE-RRR",
                          help="com --dry-run: série a listar (repetível), ex.: CGU-250")
    f_parser.add_argument("--from", dest="dt_from", type=nt.parse_utc_iso, metavar="ISO",
                          help=fs_from_help)
    f_parser.add_argument("--to", dest="dt_to", type=nt.parse_utc_iso, metavar="ISO",
                          help=fs_to_help)

# ─────────────────────────────────────────────────────────────────────────────────────
def reject_filters_without_dry_run(f_parser: argparse.ArgumentParser,
                                   f_args: argparse.Namespace,
                                   fs_error_message: str,
                                   flst_extra_options: list | None = None) -> None:
    """
    encerra o programa (argparse error, rc 2) se algum filtro de listagem foi
    passado sem --dry-run.

    :param f_parser: parser do programa
    :param f_args: argumentos já lidos (precisam ter dry_run, series, dt_from,
                   dt_to)
    :param fs_error_message: mensagem mostrada ao usuário
    :param flst_extra_options: valores de outras opções que também exigem
                               --dry-run (ex.: [args.timeline])
    """
    if f_args.dry_run:
        return

    llst_filter_values = [f_args.series, f_args.dt_from, f_args.dt_to]
    llst_filter_values.extend(flst_extra_options or [])

    if any(llst_filter_values):
        f_parser.error(fs_error_message)

# < SListingFilter >───────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SListingFilter:
    """
    o que o dry-run deve mostrar.  Um campo None significa "sem filtro".
    """
    # séries a listar, em maiúsculas ("CGU-250")
    products: frozenset | None = None
    # início e fim do intervalo (UTC, inclusivos)
    dt_from: datetime.datetime | None = None
    dt_to: datetime.datetime | None = None

    # ---------------------------------------------------------------------------------
    @classmethod
    def from_arguments(cls, f_args: argparse.Namespace) -> SListingFilter:
        """
        monta o filtro a partir de -s/--series, --from e --to
        """
        lset_products = None

        if f_args.series:
            lset_products = frozenset(ls_series.upper() for ls_series in f_args.series)

        return cls(products=lset_products, dt_from=f_args.dt_from, dt_to=f_args.dt_to)

    # ---------------------------------------------------------------------------------
    def wants_product(self, fs_product: str) -> bool:
        """
        a série ("CGU-250") deve ser listada?
        """
        if self.products is None:
            return True

        return fs_product in self.products

    # ---------------------------------------------------------------------------------
    def wants_time(self, fdt_instant: datetime.datetime) -> bool:
        """
        o instante está dentro do intervalo [dt_from, dt_to]?
        """
        if self.dt_from is not None and fdt_instant < self.dt_from:
            return False

        if self.dt_to is not None and fdt_instant > self.dt_to:
            return False

        return True

    # ---------------------------------------------------------------------------------
    def wants_epoch(self, fi_epoch: int) -> bool:
        """
        wants_time() para um epoch (s, UTC)
        """
        return self.wants_time(nt.epoch_to_utc(fi_epoch))

    # ---------------------------------------------------------------------------------
    def overlaps(self, fi_first_epoch: int, fi_last_epoch: int) -> bool:
        """
        o período [primeiro, último] tem alguma parte dentro do intervalo?
        (usado para lacunas: uma lacuna que começa antes de --from e termina
        depois dele aparece na listagem)
        """
        if self.dt_from is not None and nt.epoch_to_utc(fi_last_epoch) < self.dt_from:
            return False

        if self.dt_to is not None and nt.epoch_to_utc(fi_first_epoch) > self.dt_to:
            return False

        return True

# < the end >──────────────────────────────────────────────────────────────────────────
