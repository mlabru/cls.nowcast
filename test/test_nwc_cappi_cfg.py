# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_cappi_cfg
testes dos presets de CAPPI do nowcast: kwargs exatos de grid_from_radars
por alcance, resolução, ids e seleção por alcance

2026.set  mlabru  identidade com cp_core.CFG_NOWCAST removida (o preset saiu do
                  21.mvol junto com o volProcess)
2026.set  mlabru  nwc_presets (ids ativos/legado) e nwc_cappi_path.preset_dir
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import types
from unittest import mock

# numPy
import numpy as np

# pytest
import pytest

# cappi (daqui; o 21.mvol o vê pelo symlink apps/cappi)
import cappi.cp_core as cc

# locals
import work.nwc_cappi_cfg as pc
import work.nwc_cappi_path as cp
import work.nwc_presets as ps

# < helpers >──────────────────────────────────────────────────────────────────────────

def _fake_radar(ff_last_gate=400000.):
    """
    dublê mínimo de pyart.core.Radar: só expõe o que grid_radar usa
    """
    return types.SimpleNamespace(range={"data": np.array([0., ff_last_gate])},
                                 latitude={"data": np.array([-31.4])},
                                 longitude={"data": np.array([-52.7])},
                                 altitude={"data": np.array([460.])})

# ─────────────────────────────────────────────────────────────────────────────────────
def _grid_kwargs(fcfg):
    """
    roda grid_radar com grid_from_radars mockado e retorna os kwargs
    """
    with mock.patch.object(cc.pyart.map, "grid_from_radars") as lmock:
        cc.grid_radar(_fake_radar(), fcfg)

    assert 1 == lmock.call_count
    return lmock.call_args.kwargs

# ─────────────────────────────────────────────────────────────────────────────────────
def _expected(ff_extent, li_n):
    """
    kwargs esperados: só extensão e tamanho variam entre os alcances
    """
    return {"grid_shape": (1, li_n, li_n),
            "grid_limits": ((3000.0, 3000.0),
                            (-ff_extent, ff_extent),
                            (-ff_extent, ff_extent)),
            "fields": ["corrected_reflectivity"],
            "gridding_algo": "map_gates_to_grid",
            "weighting_function": "Barnes2",
            "roi_func": "dist_beam",
            "min_radius": 1750.0}

# < presets >──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("li_alcance, ff_extent, li_n", [(250, 250000.0, 500),
                                                         (400, 300000.0, 600)])
def test_kwargs_de_grid_por_alcance(li_alcance, ff_extent, li_n):
    # extensão fixa, independente do último gate do radar
    assert _grid_kwargs(pc.preset_for(li_alcance).cfg) == _expected(ff_extent, li_n)

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("li_alcance", sorted(pc.MDCT_PRESETS))
def test_resolucao_de_1_km_em_todos_os_alcances(li_alcance):
    lcfg = pc.preset_for(li_alcance).cfg

    assert 1000.0 == 2 * lcfg.extent_m / lcfg.shape_xy[0] == 2 * lcfg.extent_m / lcfg.shape_xy[1]

# ─────────────────────────────────────────────────────────────────────────────────────
def test_ids_unicos():
    llst_ids = [l_preset.id for l_preset in pc.MDCT_PRESETS.values()]

    assert len(set(llst_ids)) == len(llst_ids)

# < preset_for >───────────────────────────────────────────────────────────────────────

def test_preset_for_aceita_alcance_texto():
    assert pc.preset_for("400") is pc.MDCT_PRESETS[400]

# ─────────────────────────────────────────────────────────────────────────────────────
def test_preset_for_alcance_desconhecido_levanta_value_error():
    with pytest.raises(ValueError, match="alcance sem preset"):
        pc.preset_for(150)

# < nwc_presets >──────────────────────────────────────────────────────────────────────

def test_ids_ativos_iguais_aos_dos_presets():
    # nwc_presets (sem Py-ART) e nwc_cappi_cfg (parâmetros) falam dos mesmos ids
    assert {li: l_preset.id for li, l_preset in pc.MDCT_PRESETS.items()} == ps.MDCT_ACTIVE

# ─────────────────────────────────────────────────────────────────────────────────────
def test_legado_nunca_e_ativo():
    assert not set(ps.MDCT_LEGACY) & set(ps.MDCT_ACTIVE.values())
    assert ("nwc250v0", "nwc400v0") == (ps.legacy_id(250), ps.legacy_id("400"))

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fv_func", [ps.active_id, ps.legacy_id])
def test_alcance_sem_id_levanta_value_error(fv_func):
    with pytest.raises(ValueError):
        fv_func(150)

# < preset_dir >───────────────────────────────────────────────────────────────────────

def test_preset_dir(tmp_path):
    assert tmp_path / "nwc250v2" == cp.preset_dir(tmp_path, "nwc250v2")

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fs_preset", ["", ".", "..", "a/b"])
def test_preset_dir_rejeita_id_que_sai_do_diretorio(tmp_path, fs_preset):
    with pytest.raises(ValueError):
        cp.preset_dir(tmp_path, fs_preset)

# < the end >──────────────────────────────────────────────────────────────────────────
