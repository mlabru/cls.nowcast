# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_config
testes da leitura do config.ini

2026.out  mlabru  [paths] sobreposto pelo ambiente; trava MET_ENV=test em init()
2026.set  mlabru  seção opcional [convert]
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import logging
import pathlib

# pytest
import pytest

# locals
import work.nwc_config as cf

# < helpers >──────────────────────────────────────────────────────────────────────────

# config.ini do projeto
MPTH_PROJECT_INI = pathlib.Path(__file__).resolve().parents[1] / "config.ini"

MS_INI = """
[paths]
cappi_dir = {tmp}/cappi
database = {tmp}/catalog.db
log_dir = {tmp}/logs

[log]
level = info
file = x.log

[database]
busy_timeout = 5000

[timeline]
interval_s = 600
{tolerance}

[interpolate]
max_gap = 2

[sequences]
min_valid_ratio = 0.95
"""

# -------------------------------------------------------------------------------------
def _write(tmp_path, fs_tolerance="", fs_drop=None) -> str:
    ls_text = MS_INI.format(tmp=tmp_path, tolerance=fs_tolerance)

    if fs_drop:
        ls_text = ls_text.replace(f"[{fs_drop}]", "[ignorada]")

    lpth = tmp_path / "config.ini"
    lpth.write_text(ls_text, encoding="utf-8")

    return str(lpth)

# ─────────────────────────────────────────────────────────────────────────────────────
def test_config_do_projeto_carrega():
    lcfg = cf.load_config(str(MPTH_PROJECT_INI))
    assert (600, 300, 2) == (lcfg.interval_s, lcfg.tolerance_s, lcfg.max_gap)
    assert ("flow", "z") == (lcfg.method, lcfg.blend)

def test_defaults(tmp_path):
    lcfg = cf.load_config(_write(tmp_path))
    # tolerância default = interval/2; workers 0 = automático; sem filtro de chuva
    assert (300, 0, 0.0, 5000) == (lcfg.tolerance_s, lcfg.workers, lcfg.min_rain_fraction,
                                   lcfg.busy_timeout_ms)

def test_tolerancia_acima_de_meio_intervalo(tmp_path):
    with pytest.raises(ValueError):
        cf.load_config(_write(tmp_path, "tolerance_s = 301"))

@pytest.mark.parametrize("ls_section", ["timeline", "interpolate", "sequences"])
def test_secao_obrigatoria(tmp_path, ls_section):
    with pytest.raises(ValueError, match=ls_section):
        cf.load_config(_write(tmp_path, fs_drop=ls_section))

def test_interpolate_defaults_e_invalidos(tmp_path):
    lcfg = cf.load_config(_write(tmp_path))
    assert ("flow", "z") == (lcfg.method, lcfg.blend)

    lpth = tmp_path / "config.ini"
    lpth.write_text(lpth.read_text().replace("max_gap = 2", "max_gap = 2\nmethod = spline"))

    with pytest.raises(ValueError, match="method"):
        cf.load_config(str(lpth))

@pytest.mark.parametrize("ls_option", ["max_gap", "workers"])
@pytest.mark.parametrize("ls_value", ["-1", "1.5", "dois"])
def test_inteiro_nao_negativo_invalido(tmp_path, ls_option, ls_value):
    lpth = pathlib.Path(_write(tmp_path))
    lpth.write_text(lpth.read_text().replace("max_gap = 2", f"max_gap = 2\n{ls_option} = {ls_value}")
                    if "workers" == ls_option else
                    lpth.read_text().replace("max_gap = 2", f"max_gap = {ls_value}"))

    with pytest.raises(ValueError, match=ls_option):
        cf.load_config(str(lpth))

def test_workers_explicito(tmp_path):
    lpth = pathlib.Path(_write(tmp_path))
    lpth.write_text(lpth.read_text().replace("max_gap = 2", "max_gap = 2\nworkers = 4"))

    assert 4 == cf.load_config(str(lpth)).workers

def test_max_gap_zero_desliga(tmp_path):
    lpth = pathlib.Path(_write(tmp_path))
    lpth.write_text(lpth.read_text().replace("max_gap = 2", "max_gap = 0"))

    assert 0 == cf.load_config(str(lpth)).max_gap

def test_arquivo_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        cf.load_config(str(tmp_path / "nada.ini"))

def test_convert_opcional_defaults_e_valores(tmp_path):
    # sem [convert]: defaults
    lcfg = cf.load_config(_write(tmp_path))
    assert (0, 3, 3600) == (lcfg.convert_workers, lcfg.convert_max_retries,
                            lcfg.convert_stale_after_s)

    lpth = tmp_path / "config.ini"
    lpth.write_text(lpth.read_text() + "\n[convert]\nworkers = 2\nmax_retries = 5\n"
                                       "stale_after_s = 60\n")
    lcfg = cf.load_config(str(lpth))
    assert (2, 5, 60) == (lcfg.convert_workers, lcfg.convert_max_retries,
                          lcfg.convert_stale_after_s)

    lpth.write_text(lpth.read_text().replace("max_retries = 5", "max_retries = -1"))
    with pytest.raises(ValueError, match="max_retries"):
        cf.load_config(str(lpth))

def test_convert_do_projeto():
    lcfg = cf.load_config(str(MPTH_PROJECT_INI))
    assert (0, 3, 3600) == (lcfg.convert_workers, lcfg.convert_max_retries,
                            lcfg.convert_stale_after_s)

# < ambiente (nwc_cfg.sh) >────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _restore_logging():
    """
    cf.init() troca os handlers do root (basicConfig force=True); devolve os
    originais para não vazar o FileHandler do tmp_path para outros testes
    """
    llog = logging.getLogger()
    llst_handlers, li_level = llog.handlers[:], llog.level

    yield

    for lhandler in llog.handlers:
        if lhandler not in llst_handlers:
            lhandler.close()

    llog.handlers[:] = llst_handlers
    llog.setLevel(li_level)

def test_paths_sobrepostos_pelo_ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("NWC_DBASE_FILE", "/outro/catalog.db")
    monkeypatch.setenv("NWC_CAPPI_DIR", "")

    lcfg = cf.load_config(_write(tmp_path))

    assert pathlib.Path("/outro/catalog.db") == lcfg.database
    # vazio = config.ini
    assert tmp_path / "cappi" == lcfg.cappi_dir
    assert tmp_path / "logs" / "x.log" == lcfg.log_file

def test_log_dir_do_ambiente_leva_o_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NWC_LOG_DIR", str(tmp_path / "outros_logs"))

    lcfg = cf.load_config(_write(tmp_path))

    assert tmp_path / "outros_logs" / "x.log" == lcfg.log_file

def test_trava_aceita_caminhos_dentro_da_raiz(tmp_path, monkeypatch):
    monkeypatch.setenv("MET_ENV", "test")
    monkeypatch.setenv("MET_TST_ROOT", str(tmp_path))

    lcfg = cf.init(_write(tmp_path))

    assert lcfg.log_dir.is_dir()

@pytest.mark.parametrize("fs_database", ["{tmp}/../catalog.db", "/srv/prod/catalog.db", "{tmp}"])
def test_trava_recusa_caminho_fora_da_raiz(tmp_path, monkeypatch, fs_database):
    ls_database = fs_database.format(tmp=tmp_path)
    monkeypatch.setenv("MET_ENV", "test")
    monkeypatch.setenv("MET_TST_ROOT", str(tmp_path))
    monkeypatch.setenv("NWC_DBASE_FILE", ls_database)

    with pytest.raises(ValueError, match="fora de"):
        cf.init(_write(tmp_path))

    # nada criado antes da recusa
    assert not (tmp_path / "logs").exists()

def test_trava_exige_raiz(tmp_path, monkeypatch):
    monkeypatch.setenv("MET_ENV", "test")

    with pytest.raises(ValueError, match="MET_TST_ROOT"):
        cf.init(_write(tmp_path))

def test_sem_met_env_nao_trava(tmp_path, monkeypatch):
    monkeypatch.setenv("MET_TST_ROOT", str(tmp_path / "outra"))

    cf.init(_write(tmp_path))

# < the end >──────────────────────────────────────────────────────────────────────────
