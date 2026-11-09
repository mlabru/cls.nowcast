# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_helpers
testes dos módulos auxiliares criados na refatoração para legibilidade:
nwc_time (conversões UTC), nwc_cli (filtros do dry-run, banner do log),
nwc_pool (execução em série/paralela) e os helpers novos de
nwc_cappi_path e nwc_atomic_io

2026.out  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import argparse
import datetime
import logging

# numPy
import numpy as np

# pytest
import pytest

# locals
import work.nwc_atomic_io as aio
import work.nwc_cappi_path as cp
import work.nwc_cli as cli
import work.nwc_pool as pool
import work.nwc_time as nt

# < constants >────────────────────────────────────────────────────────────────────────

UTC = datetime.timezone.utc

# < helpers >──────────────────────────────────────────────────────────────────────────

def _double(fi_value):
    """
    worker de teste (nível de módulo: precisa ser picklable)
    """
    return 2 * fi_value

def _parser():
    """
    parser com --dry-run e os filtros de listagem
    """
    l_parser = argparse.ArgumentParser()
    l_parser.add_argument("--dry-run", action="store_true")
    cli.add_listing_filter_arguments(l_parser, "de", "até")
    return l_parser

# < nwc_time >─────────────────────────────────────────────────────────────────────────

def test_epoch_to_utc():
    assert datetime.datetime(1970, 1, 1, tzinfo=UTC) == nt.epoch_to_utc(0)
    assert UTC == nt.epoch_to_utc(1781481623).tzinfo

@pytest.mark.parametrize("ls_text, ldt_expected", [
    ("2026-08-01", datetime.datetime(2026, 8, 1, tzinfo=UTC)),
    ("2026-08-01T12:30", datetime.datetime(2026, 8, 1, 12, 30, tzinfo=UTC)),
    ("2026-08-01T09:00-03:00", datetime.datetime(2026, 8, 1, 12, 0, tzinfo=UTC)),
])
def test_parse_utc_iso(ls_text, ldt_expected):
    ldt_parsed = nt.parse_utc_iso(ls_text)
    assert ldt_expected == ldt_parsed
    assert UTC == ldt_parsed.tzinfo

def test_parse_utc_iso_invalido():
    with pytest.raises(ValueError):
        nt.parse_utc_iso("ontem")

# < nwc_cli >──────────────────────────────────────────────────────────────────────────

def test_filtro_sem_argumentos_aceita_tudo():
    l_filter = cli.SListingFilter.from_arguments(_parser().parse_args(["--dry-run"]))

    assert l_filter.wants_product("XYZ-999")
    assert l_filter.wants_epoch(0)
    assert l_filter.overlaps(0, 10)

def test_filtro_series_em_maiusculas():
    l_filter = cli.SListingFilter.from_arguments(
        _parser().parse_args(["--dry-run", "-s", "cgu-250", "-s", "STI-400"]))

    assert frozenset({"CGU-250", "STI-400"}) == l_filter.products
    assert not l_filter.wants_product("CGU-400")

def test_filtro_intervalo_inclusivo_e_sobreposicao():
    l_filter = cli.SListingFilter.from_arguments(
        _parser().parse_args(["--dry-run", "--from", "1970-01-01T00:00:10",
                              "--to", "1970-01-01T00:00:20"]))

    assert [False, True, True, True, False] == [l_filter.wants_epoch(li)
                                                for li in (9, 10, 15, 20, 21)]
    # período que atravessa uma das pontas aparece; totalmente fora, não
    assert l_filter.overlaps(5, 10) and l_filter.overlaps(20, 30)
    assert not l_filter.overlaps(0, 9) and not l_filter.overlaps(21, 30)

@pytest.mark.parametrize("llst_argv", [["-s", "CGU-250"], ["--from", "2026-01-01"],
                                       ["--to", "2026-01-01"]])
def test_filtros_exigem_dry_run(llst_argv):
    l_parser = _parser()

    with pytest.raises(SystemExit):
        cli.reject_filters_without_dry_run(l_parser, l_parser.parse_args(llst_argv), "erro")

def test_opcao_extra_exige_dry_run():
    l_parser = _parser()
    l_args = l_parser.parse_args([])

    # sem filtros: passa
    cli.reject_filters_without_dry_run(l_parser, l_args, "erro", [False])

    with pytest.raises(SystemExit):
        cli.reject_filters_without_dry_run(l_parser, l_args, "erro", [True])

def test_logged_run_registra_duracao_mesmo_com_excecao(caplog):
    caplog.set_level(logging.INFO, logger=cli.M_LOG.name)

    with pytest.raises(RuntimeError):
        with cli.logged_run("Teste"):
            raise RuntimeError("falhou")

    llst_messages = [l_record.getMessage() for l_record in caplog.records]
    assert ">>> Teste" in llst_messages
    assert any(ls_msg.startswith("duração: ") for ls_msg in llst_messages)
    assert cli.CS_RUN_SEPARATOR == llst_messages[-1]

# < nwc_pool >─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("li_configured, li_tasks, li_expected", [(1, 10, 1), (4, 10, 4),
                                                                  (4, 2, 2)])
def test_resolve_worker_count(li_configured, li_tasks, li_expected):
    assert li_expected == pool.resolve_worker_count(li_configured, li_tasks)

def test_resolve_worker_count_zero_usa_cpus():
    assert 1 <= pool.resolve_worker_count(0, 1000)

@pytest.mark.parametrize("li_workers", [1, 2])
def test_run_in_pool_mantem_a_ordem(li_workers):
    assert [2, 4, 6] == pool.run_in_pool(_double, [1, 2, 3], li_workers, None)

def test_run_in_pool_vazio():
    assert [] == pool.run_in_pool(_double, [], 0, None)

def test_run_in_pool_processo_morto_vira_falha():
    # um worker que não é picklable faz o future falhar (como um processo morto)
    llst_results = pool.run_in_pool(lambda fi: fi, [1, 2], 2,
                                    lambda f_task, f_error: ("perdido", f_task))

    assert [("perdido", 1), ("perdido", 2)] == llst_results

# < nwc_cappi_path / nwc_atomic_io >───────────────────────────────────────────────────

def test_build_product_filename(tmp_path):
    lpth = cp.build_product_filename(tmp_path, "nwc250v2", "CGU", 250, 1781481623)

    assert tmp_path.resolve() / "nwc250v2" / "CGU_250" / "CGU_250_1781481623.npy" == lpth

def test_is_usable_cappi_file(tmp_path):
    lpth_empty = tmp_path / "vazio.npy"
    lpth_empty.touch()

    lpth_full = tmp_path / "cheio.npy"
    lpth_full.write_bytes(b"x")

    assert not cp.is_usable_cappi_file(tmp_path / "ausente.npy")
    assert not cp.is_usable_cappi_file(lpth_empty)
    assert not cp.is_usable_cappi_file(tmp_path)
    assert cp.is_usable_cappi_file(str(lpth_full))

def test_save_npy_cria_diretorio_e_nao_deixa_temporario(tmp_path):
    lpth = tmp_path / "sub" / "a.npy"
    lnp = np.arange(4, dtype=np.float32)

    aio.save_npy(lnp, lpth)

    assert np.array_equal(lnp, np.load(lpth))
    assert [lpth] == list(lpth.parent.iterdir())

# < the end >──────────────────────────────────────────────────────────────────────────
