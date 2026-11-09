# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_vol_blender
testes de vol_blender.sh::lista_mvol/station_prefix na árvore real
(<NomeDaEstação>/<RRR>/), carregando o script com VOL_BLEND_NO_MAIN=true e
substituindo gera_anima/log por stubs que só imprimem

2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import datetime
import pathlib
import shutil
import subprocess

# pytest
import pytest

# < helpers >──────────────────────────────────────────────────────────────────────────

# raiz do projeto (onde está vol_blender.sh)
MPTH_ROOT = pathlib.Path(__file__).resolve().parent.parent

# -------------------------------------------------------------------------------------
def _lista_mvol(fpth_raw: pathlib.Path) -> list:
    """
    roda lista_mvol com gera_anima substituída: imprime "<prefixo> <n> <arquivos>"
    """
    ls_cmd = (f'VOL_BLEND_NO_MAIN=true; source "{MPTH_ROOT}/vol_blender.sh"; '
              'log() { :; }; '
              'gera_anima() { local p="$1"; shift; echo "$p $# $(for f in "$@"; do basename "$f"; done | tr "\\n" " ")"; }; '
              f'lista_mvol "{fpth_raw}"')

    lproc = subprocess.run(["bash", "-c", ls_cmd], capture_output=True, text=True, check=True)

    return [ls.strip() for ls in lproc.stdout.splitlines()]

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponível")
def test_lista_mvol_arvore_real(tmp_path):
    # "hoje" em UTC, como o horário no nome dos .mvol
    ls_today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    # árvore real: nome completo da estação / alcance
    lpth_cgu = tmp_path / "Cangucu" / "250"
    lpth_cgu.mkdir(parents=True)
    (lpth_cgu / f"CGU-250--{ls_today}--10-00-22.mvol").touch()
    (lpth_cgu / f"CGU-250--{ls_today}--09-50-22.mvol").touch()
    (lpth_cgu / "CGU-250--2000-01-01--00-00-00.mvol").touch()

    # só arquivos antigos: prefixo vem deles, lista de hoje vazia (fallback)
    lpth_bel = tmp_path / "Belem" / "400"
    lpth_bel.mkdir(parents=True)
    (lpth_bel / "BEL-400--2000-01-01--00-00-00.mvol").touch()

    # sem nenhum .mvol: não há como saber a sigla -> ignorado
    (tmp_path / "Macapa" / "250").mkdir(parents=True)

    # layout antigo (diretório já nomeado pela sigla)
    lpth_sti = tmp_path / "STI" / "250"
    lpth_sti.mkdir(parents=True)

    llst = _lista_mvol(tmp_path)

    assert sorted(llst) == sorted([
        "BEL-400 0",
        f"CGU-250 2 CGU-250--{ls_today}--09-50-22.mvol CGU-250--{ls_today}--10-00-22.mvol",
        "STI-250 0",
    ])

# -------------------------------------------------------------------------------------
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash indisponível")
def test_lista_mvol_raiz_vazia(tmp_path):
    assert [] == _lista_mvol(tmp_path)

# < the end >──────────────────────────────────────────────────────────────────────────
