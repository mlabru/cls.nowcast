# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_convert_sh
testes do nwc_convert.sh, no padrão do test_nwc_interpol_sh.py: funções
carregadas com NWC_CONVERT_NO_MAIN=true, e o main rodado numa cópia do
script em tmp_path, com um nwcConvert.py falso (nunca toca no data/
compartilhado): rc (3 só avisa, outro sai), lock de instância,
stdout/stderr, cwd e PYTHONPATH.

2026.out  mlabru  nwc_cfg.sh/nwc_common.sh/cls_common.sh copiados para o projeto falso; a
                  trava checa as sobreposições NWC_* (o config.ini é do Python)
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import os
import pathlib
import shutil
import subprocess

# pytest
import pytest

# < helpers >──────────────────────────────────────────────────────────────────────────

# raiz do projeto (onde está nwc_convert.sh)
MPTH_ROOT = pathlib.Path(__file__).resolve().parent.parent

# nome do script
CS_SCRIPT = "nwc_convert.sh"

# estágio falso: registra a chamada (cwd, PYTHONPATH), escreve em stdout e
# stderr e sai com o rc pedido
CS_STAGE = """\
import os, pathlib, sys
lpth = pathlib.Path(__file__).resolve().parent.parent / "calls.txt"
with lpth.open("a") as lfh:
    lfh.write(f"{{os.getcwd()}}|{{os.environ.get('PYTHONPATH', '')}}\\n")
print("stdout do convert")
print("stderr do convert", file=sys.stderr)
sys.exit({rc})
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("flock") is None,
                                reason="bash/flock indisponíveis")

# -------------------------------------------------------------------------------------
def _env(fpth_home: pathlib.Path) -> dict:
    """
    ambiente dos subprocessos: HOME isolado (sem ~/.venv) e sem PYTHONPATH herdado
    """
    ldct_env = {ls: lv for ls, lv in os.environ.items()
                if "PYTHONPATH" != ls and not ls.startswith(("MET_", "NWC_"))}
    ldct_env["HOME"] = str(fpth_home)

    # ok
    return ldct_env

# -------------------------------------------------------------------------------------
def _project(fpth_tmp: pathlib.Path, fi_rc: int = 0) -> pathlib.Path:
    """
    cópia do nwc_convert.sh num projeto falso, com o nwcConvert.py substituído
    """
    lpth_proj = fpth_tmp / "proj"
    (lpth_proj / "apps").mkdir(parents=True)

    for ls_file in (CS_SCRIPT, "nwc_cfg.sh", "nwc_common.sh", "cls_common.sh"):
        shutil.copy(MPTH_ROOT / ls_file, lpth_proj / ls_file)
    (lpth_proj / "apps" / "nwcConvert.py").write_text(CS_STAGE.format(rc=fi_rc))

    # ok
    return lpth_proj

# -------------------------------------------------------------------------------------
def _run(fpth_proj: pathlib.Path, flst_prefix: list = (),
         fdct_env: dict | None = None) -> subprocess.CompletedProcess:
    """
    roda o main do script copiado (de outro diretório, para provar o cd)
    """
    # ok
    return subprocess.run([*flst_prefix, "bash", str(fpth_proj / CS_SCRIPT)],
                          capture_output=True, text=True,
                          env={**_env(fpth_proj.parent), **(fdct_env or {})},
                          cwd=fpth_proj.parent)

# -------------------------------------------------------------------------------------
def _tst_home(fpth_tmp: pathlib.Path, fs_database: str = "../mvol/catalog.db") -> pathlib.Path:
    """
    NWC_HOME de um ambiente de teste em <tmp>/tst, com config.ini
    """
    lpth_home = fpth_tmp / "tst" / "nowcast"
    lpth_home.mkdir(parents=True)
    (lpth_home / "config.ini").write_text(f"[paths]\ncappi_dir = ./data/cappi\n"
                                          f"database = {fs_database}\nlog_dir = ./logs\n")

    # ok
    return lpth_home

# -------------------------------------------------------------------------------------
def _calls(fpth_proj: pathlib.Path) -> list:
    lpth = fpth_proj / "calls.txt"

    # ok
    return [tuple(ls.split("|")) for ls in lpth.read_text().splitlines()] if lpth.exists() else []

# < testes >───────────────────────────────────────────────────────────────────────────

def test_no_main_so_carrega_as_funcoes(tmp_path):
    lpth_proj = _project(tmp_path)

    lproc = subprocess.run(["bash", "-c", f'NWC_CONVERT_NO_MAIN=true; source "{lpth_proj / CS_SCRIPT}"; '
                                          'type -t log check_log_size; echo "$NWC_CONVERT_INSTANCE_LOCK"'],
                           capture_output=True, text=True, env=_env(tmp_path), cwd=tmp_path)

    assert 0 == lproc.returncode, lproc.stderr
    assert ["function", "function",
            str(lpth_proj / "data" / "state" / ".nwc_convert_instance.lock")] == lproc.stdout.split()
    assert [] == _calls(lpth_proj)

# ─────────────────────────────────────────────────────────────────────────────────────
def test_sucesso(tmp_path):
    lpth_proj = _project(tmp_path)

    lproc = _run(lpth_proj)

    assert 0 == lproc.returncode, lproc.stderr
    ((ls_cwd, ls_path),) = _calls(lpth_proj)
    assert pathlib.Path(ls_cwd) == lpth_proj
    assert ls_path.split(":")[:2] == [str(lpth_proj / "apps"), str(lpth_proj / "apps" / "util")]

    ls_log = (lpth_proj / "logs" / "nwc_convert.log").read_text()
    assert "iniciando conversão" in ls_log and "conversão concluída." in ls_log
    # stdout descartado; stderr no .err
    assert "stdout do convert" not in lproc.stdout + lproc.stderr + ls_log
    assert "stderr do convert\n" == (lpth_proj / "logs" / "nwcConvert.err").read_text()

# ─────────────────────────────────────────────────────────────────────────────────────
def test_volume_que_falhou_so_avisa(tmp_path):
    lpth_proj = _project(tmp_path, fi_rc=3)

    lproc = _run(lpth_proj)

    assert 0 == lproc.returncode, lproc.stderr
    ls_log = (lpth_proj / "logs" / "nwc_convert.log").read_text()
    assert "[warn]: nwcConvert.py: algum volume falhou (rc=3)" in ls_log
    assert "conversão concluída." in ls_log

# ─────────────────────────────────────────────────────────────────────────────────────
def test_excecao_do_python_e_erro(tmp_path):
    # rc 1 = exceção fora do main (ImportError, config.ini ausente): antes
    # era logado como volume que falhou e o cron saía com 0
    lpth_proj = _project(tmp_path, fi_rc=1)

    lproc = _run(lpth_proj)

    assert 1 == lproc.returncode
    ls_log = (lpth_proj / "logs" / "nwc_convert.log").read_text()
    assert "[error]: nwcConvert.py falhou (rc=1)" in ls_log
    assert "conversão concluída." not in ls_log

# ─────────────────────────────────────────────────────────────────────────────────────
def test_erro_fatal_sai_com_o_rc(tmp_path):
    lpth_proj = _project(tmp_path, fi_rc=2)

    lproc = _run(lpth_proj)

    assert 2 == lproc.returncode
    ls_log = (lpth_proj / "logs" / "nwc_convert.log").read_text()
    assert "[error]: nwcConvert.py falhou (rc=2)" in ls_log
    assert "conversão concluída." not in ls_log

# ─────────────────────────────────────────────────────────────────────────────────────
def test_lock_ocupado_sai_sem_converter(tmp_path):
    lpth_proj = _project(tmp_path)
    lpth_lock = lpth_proj / "data" / "state" / ".nwc_convert_instance.lock"
    lpth_lock.parent.mkdir(parents=True)

    # segura o lock enquanto o script roda
    lproc = _run(lpth_proj, ["flock", str(lpth_lock)])

    assert 0 == lproc.returncode, lproc.stderr
    assert [] == _calls(lpth_proj)
    assert "conversão já em execução" in (lpth_proj / "logs" / "nwc_convert.log").read_text()

# < NWC_HOME e trava MET_ENV=test >───────────────────────────────────────────────────

def test_nwc_home_move_cwd_logs_e_lock(tmp_path):
    lpth_proj = _project(tmp_path)
    lpth_home = _tst_home(tmp_path)

    lproc = _run(lpth_proj, fdct_env={"NWC_HOME": str(lpth_home)})

    assert 0 == lproc.returncode, lproc.stderr
    # código do projeto, cwd/logs/lock na raiz de execução
    assert [(str(lpth_home), f"{lpth_proj}/apps:{lpth_proj}/apps/util")] == _calls(lpth_proj)
    assert (lpth_home / "logs" / "nwc_convert.log").exists()
    assert (lpth_home / "data" / "state" / ".nwc_convert_instance.lock").exists()
    assert not (lpth_proj / "logs").exists()

def test_trava_aceita_ambiente_coerente(tmp_path):
    lpth_proj = _project(tmp_path)
    lpth_home = _tst_home(tmp_path)

    lproc = _run(lpth_proj, fdct_env={"NWC_HOME": str(lpth_home), "MET_ENV": "test",
                                      "MET_TST_ROOT": str(tmp_path / "tst")})

    assert 0 == lproc.returncode, lproc.stderr
    assert 1 == len(_calls(lpth_proj))

@pytest.mark.parametrize("fs_database", ["../../catalog.db", "/srv/prod/catalog.db"])
def test_trava_recusa_sobreposicao_fora_da_raiz(tmp_path, fs_database):
    lpth_proj = _project(tmp_path)
    lpth_home = _tst_home(tmp_path)

    lproc = _run(lpth_proj, fdct_env={"NWC_HOME": str(lpth_home), "MET_ENV": "test",
                                      "MET_TST_ROOT": str(tmp_path / "tst"),
                                      "NWC_DBASE_FILE": fs_database})

    assert 1 == lproc.returncode
    assert f"'{fs_database}' fora de" in lproc.stderr
    # nada rodou nem foi escrito
    assert [] == _calls(lpth_proj)
    assert not (lpth_home / "logs").exists()

def test_config_ini_fica_com_o_python(tmp_path):
    # o shell não lê mais o config.ini (antes: sed); um [paths] fora da raiz
    # é recusado pelo nwc_config.init do estágio, não aqui
    lpth_proj = _project(tmp_path)
    lpth_home = _tst_home(tmp_path, "/srv/prod/catalog.db")

    lproc = _run(lpth_proj, fdct_env={"NWC_HOME": str(lpth_home), "MET_ENV": "test",
                                      "MET_TST_ROOT": str(tmp_path / "tst")})

    assert 0 == lproc.returncode, lproc.stderr
    assert 1 == len(_calls(lpth_proj))

def test_nwc_path_tem_precedencia_sobre_nwc_home(tmp_path):
    lpth_proj = _project(tmp_path)
    lpth_home = _tst_home(tmp_path)

    lproc = _run(lpth_proj, fdct_env={"NWC_PATH": str(lpth_home),
                                      "NWC_HOME": str(tmp_path / "antigo")})

    assert 0 == lproc.returncode, lproc.stderr
    assert str(lpth_home) == _calls(lpth_proj)[0][0]
    assert not (tmp_path / "antigo").exists()

def test_trava_recusa_nwc_home_padrao(tmp_path):
    lpth_proj = _project(tmp_path)

    lproc = _run(lpth_proj, fdct_env={"MET_ENV": "test", "MET_TST_ROOT": str(tmp_path / "tst")})

    assert 1 == lproc.returncode
    assert [] == _calls(lpth_proj)

# < the end >──────────────────────────────────────────────────────────────────────────
