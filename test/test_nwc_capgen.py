# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_capgen
testes do nwcCapGen: extensão do grid pelo tamanho do array (1 km/px, igual
à de cada preset), anéis até a borda, -9999 como NaN e a imagem gerada

2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import sys

# matplotlib sem display
import matplotlib
matplotlib.use("Agg")

# numPy
import numpy as np

# pytest
import pytest

# locals
import work.nwc_cappi_cfg as pc
import nwcCapGen as cg

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("li_alcance", sorted(pc.MDCT_PRESETS))
def test_extensao_igual_a_do_preset(li_alcance):
    lcfg = pc.preset_for(li_alcance).cfg

    assert lcfg.extent_m == cg.half_range_m(np.zeros(lcfg.shape_xy))

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("lt_shape", [(500,), (500, 600), (2, 500, 500)])
def test_array_nao_quadrado_levanta_value_error(lt_shape):
    with pytest.raises(ValueError):
        cg.half_range_m(np.zeros(lt_shape))

# ─────────────────────────────────────────────────────────────────────────────────────
def test_nodata_vira_nan(tmp_path):
    lpth = tmp_path / "CGU_400_1.npy"
    np.save(lpth, np.array([[-9999., 10.], [0., 20.]], dtype=np.float32))

    lnp = cg.load_cappi(lpth)

    assert np.isnan(lnp[0, 0]) and 0.0 == lnp[1, 0]

# ─────────────────────────────────────────────────────────────────────────────────────
def test_sem_eco_tem_cor_propria():
    import matplotlib.colors as mcolors
    import work.nwc_presets as ps

    lcmap, lnorm = cg.build_reflectivity_colormap()

    lt_no_echo = lcmap(lnorm(ps.MF_NO_ECHO))
    assert mcolors.to_rgba(cg.CS_NO_ECHO_COLOR) == lt_no_echo
    assert lt_no_echo not in (lcmap(lnorm(-5.0)), lcmap(np.nan))

# ─────────────────────────────────────────────────────────────────────────────────────
def test_aneis_ate_a_borda():
    lfig, lax = cg.plot_cappi(np.full((600, 600), 10.0), {"radar": "CGU", "alcance": 400,
                                                          "timestamp": 0})

    # ±300 km: anéis de 50 a 300 km
    assert [50, 100, 150, 200, 250, 300] == [int(lp.get_radius()) for lp in lax.patches]
    assert (-300.0, 300.0) == tuple(lax.get_xlim())

# ─────────────────────────────────────────────────────────────────────────────────────
def test_main_grava_png(tmp_path, monkeypatch):
    lpth = tmp_path / "CGU_250_1781530822.npy"
    np.save(lpth, np.full((500, 500), -9999.0, dtype=np.float32))

    monkeypatch.setattr(sys, "argv", ["nwcCapGen.py", str(lpth), "--out-dir", str(tmp_path / "o")])

    assert 0 == cg.main()
    assert (tmp_path / "o" / "CGU_250_1781530822.png").stat().st_size > 0

# < the end >──────────────────────────────────────────────────────────────────────────
