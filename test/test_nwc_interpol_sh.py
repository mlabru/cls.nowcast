# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_interpol_sh
testes do nwc_interpol.sh:
- funções (log, check_log_size) carregadas com NWC_RUN_NO_MAIN=true, sem
  executar o main (mesmo padrão do test_vol_sync.py do 21.mvol);
- main rodado numa cópia do script em tmp_path, com estágios Python falsos
  (nunca toca no data/ compartilhado): ordem dos estágios, rc, lock de
  instância, destino de stdout/stderr, cwd e PYTHONPATH.

2026.out  mlabru  NWC_VENV escolhe o virtualenv (padrão ~/.venv)
2026.out  mlabru  nwc_cfg.sh/nwc_common.sh/cls_common.sh copiados para o projeto falso; a
                  trava checa as sobreposições NWC_* (o config.ini é do Python)
2026.set  mlabru  test_nwc_run.py -> test_nwc_interpol_sh.py (o script foi renomeado);
                  stdout dos estágios agora em logs/<estágio>.log
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import os
import pathlib
import re
import shutil
import subprocess

# pytest
import pytest

# < helpers >──────────────────────────────────────────────────────────────────────────

# raiz do projeto (onde está nwc_interpol.sh)
MPTH_ROOT = pathlib.Path(__file__).resolve().parent.parent

# nome do script
CS_SCRIPT = "nwc_interpol.sh"

# linha de log(): "[AAAA-MM-DD HH:MM:SS] <mensagem>"
CS_LOG_LINE = r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] "

# estágio falso: registra a chamada (nome, cwd, PYTHONPATH), escreve em
# stdout e stderr e sai com o rc pedido
CS_STAGE = """\
import os, pathlib, sys
lpth = pathlib.Path(__file__).resolve().parent.parent / "calls.txt"
with lpth.open("a") as lfh:
    lfh.write(f"{{pathlib.Path(__file__).stem}}|{{os.getcwd()}}|{{os.environ.get('PYTHONPATH', '')}}\\n")
print("stdout de {name}")
print("stderr de {name}", file=sys.stderr)
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
def _source(fs_body: str, fpth_home: pathlib.Path,
            fpth_script: pathlib.Path = MPTH_ROOT / CS_SCRIPT) -> subprocess.CompletedProcess:
    """
    carrega o script com NWC_RUN_NO_MAIN=true e roda fs_body no mesmo shell
    """
    ls_cmd = f'NWC_RUN_NO_MAIN=true; source "{fpth_script}"; {fs_body}'

    # ok
    return subprocess.run(["bash", "-c", ls_cmd], capture_output=True, text=True,
                          env=_env(fpth_home), cwd=fpth_home)

# -------------------------------------------------------------------------------------
def _project(fpth_tmp: pathlib.Path, fi_rc_interp: int = 0, fi_rc_seq: int = 0) -> pathlib.Path:
    """
    cópia do nwc_interpol.sh num projeto falso, com os dois estágios substituídos
    """
    lpth_proj = fpth_tmp / "proj"
    (lpth_proj / "apps").mkdir(parents=True)

    for ls_file in (CS_SCRIPT, "nwc_cfg.sh", "nwc_common.sh", "cls_common.sh"):
        shutil.copy(MPTH_ROOT / ls_file, lpth_proj / ls_file)

    for ls_name, li_rc in (("nwcInterpolate", fi_rc_interp), ("nwcSequences", fi_rc_seq)):
        (lpth_proj / "apps" / f"{ls_name}.py").write_text(CS_STAGE.format(name=ls_name, rc=li_rc))

    # ok
    return lpth_proj

# -------------------------------------------------------------------------------------
def _run(fpth_proj: pathlib.Path, flst_prefix: list = ()) -> subprocess.CompletedProcess:
    """
    roda o main do script copiado (de outro diretório, para provar o cd)
    """
    # ok
    return subprocess.run([*flst_prefix, "bash", str(fpth_proj / CS_SCRIPT)],
                          capture_output=True, text=True, env=_env(fpth_proj.parent),
                          cwd=fpth_proj.parent)

# -------------------------------------------------------------------------------------
def _calls(fpth_proj: pathlib.Path) -> list:
    """
    [(estágio, cwd, PYTHONPATH)] na ordem em que foram chamados
    """
    lpth = fpth_proj / "calls.txt"

    if not lpth.exists():
        return []

    # ok
    return [tuple(ls.split("|")) for ls in lpth.read_text().splitlines()]

# < NWC_RUN_NO_MAIN >──────────────────────────────────────────────────────────────────

class TestNoMain:

    def test_carrega_funcoes_sem_rodar_o_main(self, tmp_path):
        lpth_proj = _project(tmp_path)

        lproc = _source("type -t log check_log_size", tmp_path, lpth_proj / CS_SCRIPT)

        assert 0 == lproc.returncode, lproc.stderr
        assert ["function", "function"] == lproc.stdout.split()
        # o main não rodou: nada criado, nenhum estágio chamado
        assert not (lpth_proj / "logs").exists()
        assert not (lpth_proj / "data").exists()
        assert [] == _calls(lpth_proj)

    def test_caminhos_relativos_ao_script(self, tmp_path):
        lpth_proj = _project(tmp_path)

        lproc = _source('echo "$NWC_LOG_APP|$NWC_LOG_ERR|$NWC_INSTANCE_LOCK"', tmp_path,
                        lpth_proj / CS_SCRIPT)

        assert 0 == lproc.returncode, lproc.stderr
        assert [str(lpth_proj / "logs" / "nwc_interpol.log"),
                str(lpth_proj / "logs" / "nwc_interpol.err"),
                str(lpth_proj / "data" / "state" / ".nowcast_instance.lock")] == \
               lproc.stdout.strip().split("|")

# < log >──────────────────────────────────────────────────────────────────────────────

class TestLog:

    def test_grava_no_log_e_em_stderr(self, tmp_path):
        lproc = _source(f'NWC_LOG_APP="{tmp_path}/a.log"; NWC_LOG_ERR="{tmp_path}/e.log"; '
                        'log "[info]: um"; log "[warn]: dois"', tmp_path)

        assert 0 == lproc.returncode, lproc.stderr
        # nada em stdout: o cron só vê stderr
        assert "" == lproc.stdout

        llst_file = (tmp_path / "a.log").read_text().splitlines()
        assert 2 == len(llst_file)
        assert re.match(CS_LOG_LINE + r"\[info\]: um$", llst_file[0])
        assert re.match(CS_LOG_LINE + r"\[warn\]: dois$", llst_file[1])
        assert llst_file == lproc.stderr.splitlines()

    def test_rotaciona_o_log_grande(self, tmp_path):
        (tmp_path / "a.log").write_text("x" * 100)

        lproc = _source(f'NWC_LOG_APP="{tmp_path}/a.log"; NWC_LOG_ERR="{tmp_path}/e.log"; '
                        'GI_LOG_MAX_SIZE=50; log "[info]: novo"', tmp_path)

        assert 0 == lproc.returncode, lproc.stderr
        assert "x" * 100 == (tmp_path / "a.log.old").read_text()
        assert re.match(CS_LOG_LINE + r"\[info\]: novo$", (tmp_path / "a.log").read_text())

# < check_log_size >───────────────────────────────────────────────────────────────────

class TestCheckLogSize:

    def test_so_rotaciona_acima_do_limite(self, tmp_path):
        (tmp_path / "grande.log").write_text("x" * 11)
        (tmp_path / "limite.log").write_text("x" * 10)

        lproc = _source(f'GI_LOG_MAX_SIZE=10; check_log_size "{tmp_path}/grande.log" '
                        f'"{tmp_path}/limite.log" "{tmp_path}/ausente.log"', tmp_path)

        assert 0 == lproc.returncode, lproc.stderr
        # acima do limite: vira .old e é recriado vazio
        assert "x" * 11 == (tmp_path / "grande.log.old").read_text()
        assert "" == (tmp_path / "grande.log").read_text()
        # no limite: intacto
        assert "x" * 10 == (tmp_path / "limite.log").read_text()
        assert not (tmp_path / "limite.log.old").exists()
        # ausente: não é criado
        assert not (tmp_path / "ausente.log").exists()

# < main >─────────────────────────────────────────────────────────────────────────────

class TestMain:

    def test_sucesso(self, tmp_path):
        lpth_proj = _project(tmp_path)

        lproc = _run(lpth_proj)

        assert 0 == lproc.returncode, lproc.stderr
        # os dois estágios, em ordem, com cwd na raiz do projeto e apps/ no path
        llst_calls = _calls(lpth_proj)
        assert ["nwcInterpolate", "nwcSequences"] == [lt[0] for lt in llst_calls]
        assert all(pathlib.Path(lt[1]) == lpth_proj for lt in llst_calls)
        assert all(lt[2].split(":")[:2] == [str(lpth_proj / "apps"), str(lpth_proj / "apps" / "util")]
                   for lt in llst_calls)

        ls_log = (lpth_proj / "logs" / "nwc_interpol.log").read_text()
        assert "iniciando nowcast" in ls_log
        assert "nowcast interpolate concluído." in ls_log
        assert "[warn]" not in ls_log and "[error]" not in ls_log
        # diretório do lock criado pelo script
        assert (lpth_proj / "data" / "state" / ".nowcast_instance.lock").exists()

    def test_stdout_no_log_stderr_no_err(self, tmp_path):
        lpth_proj = _project(tmp_path)

        # duas execuções: os .log/.err dos estágios são anexados, não sobrescritos
        for _ in range(2):
            lproc = _run(lpth_proj)
            assert 0 == lproc.returncode, lproc.stderr

        # stdout de cada estágio no seu .log, fora do cron e do log do shell
        assert "stdout de" not in lproc.stdout + lproc.stderr
        assert "stdout de" not in (lpth_proj / "logs" / "nwc_interpol.log").read_text()
        for ls_name in ("nwcInterpolate", "nwcSequences"):
            assert f"stdout de {ls_name}\n" * 2 == (lpth_proj / "logs" / f"{ls_name}.log").read_text()
            assert f"stderr de {ls_name}\n" * 2 == (lpth_proj / "logs" / f"{ls_name}.err").read_text()

    def test_falha_na_interpolacao_nao_impede_as_sequencias(self, tmp_path):
        lpth_proj = _project(tmp_path, fi_rc_interp=1)

        lproc = _run(lpth_proj)

        assert 0 == lproc.returncode, lproc.stderr
        assert ["nwcInterpolate", "nwcSequences"] == [lt[0] for lt in _calls(lpth_proj)]

        ls_log = (lpth_proj / "logs" / "nwc_interpol.log").read_text()
        assert "[warn]: nwcInterpolate.py terminou com rc=1" in ls_log
        assert "nowcast interpolate concluído." in ls_log

    def test_falha_nas_sequencias_sai_com_o_rc(self, tmp_path):
        lpth_proj = _project(tmp_path, fi_rc_seq=3)

        lproc = _run(lpth_proj)

        assert 3 == lproc.returncode
        ls_log = (lpth_proj / "logs" / "nwc_interpol.log").read_text()
        assert "[error]: nwcSequences.py falhou (rc=3)" in ls_log
        assert "nowcast interpolate concluído." not in ls_log

    def test_lock_ocupado_sai_sem_rodar(self, tmp_path):
        lpth_proj = _project(tmp_path)

        lpth_lock = lpth_proj / "data" / "state" / ".nowcast_instance.lock"
        lpth_lock.parent.mkdir(parents=True)

        # outro processo segura o lock durante toda a execução
        lproc = _run(lpth_proj, ["flock", str(lpth_lock)])

        assert 0 == lproc.returncode, lproc.stderr
        assert [] == _calls(lpth_proj)
        assert "nowcast já em execução; saindo." in (lpth_proj / "logs" / "nwc_interpol.log").read_text()

    def test_ativa_o_venv_se_existir(self, tmp_path):
        lpth_proj = _project(tmp_path)

        # venv falso em $HOME (= tmp_path): marca que foi carregado
        lpth_venv = tmp_path / ".venv" / "bin"
        lpth_venv.mkdir(parents=True)
        (lpth_venv / "activate").write_text(f'touch "{tmp_path}/venv_ativado"\n')

        lproc = _run(lpth_proj)

        assert 0 == lproc.returncode, lproc.stderr
        assert (tmp_path / "venv_ativado").exists()

    def test_nwc_venv_escolhe_o_virtualenv(self, tmp_path):
        lpth_proj = _project(tmp_path)

        # o ~/.venv (HOME = tmp_path) existe, mas NWC_VENV aponta para outro
        for ls_name in (".venv", "outro_venv"):
            lpth_bin = tmp_path / ls_name / "bin"
            lpth_bin.mkdir(parents=True)
            (lpth_bin / "activate").write_text(f'touch "{tmp_path}/{ls_name}_ativado"\n')

        lproc = subprocess.run(["bash", str(lpth_proj / CS_SCRIPT)], capture_output=True,
                               text=True, cwd=lpth_proj,
                               env={**_env(tmp_path), "NWC_VENV": str(tmp_path / "outro_venv")})

        assert 0 == lproc.returncode, lproc.stderr
        assert (tmp_path / "outro_venv_ativado").exists()
        assert not (tmp_path / ".venv_ativado").exists()

# < NWC_HOME e trava MET_ENV=test >───────────────────────────────────────────────────

class TestNwcHome:

    def _home(self, fpth_tmp: pathlib.Path, fs_database: str) -> pathlib.Path:
        lpth_home = fpth_tmp / "tst" / "nowcast"
        lpth_home.mkdir(parents=True)
        (lpth_home / "config.ini").write_text(f"[paths]\ndatabase = {fs_database}\n")
        return lpth_home

    def _run(self, fpth_proj: pathlib.Path, fdct_env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(fpth_proj / CS_SCRIPT)], capture_output=True,
                              text=True, env={**_env(fpth_proj.parent), **fdct_env},
                              cwd=fpth_proj.parent)

    def test_nwc_home_move_cwd_logs_e_lock(self, tmp_path):
        lpth_proj = _project(tmp_path)
        lpth_home = self._home(tmp_path, "../mvol/catalog.db")

        lproc = self._run(lpth_proj, {"NWC_HOME": str(lpth_home), "MET_ENV": "test",
                                      "MET_TST_ROOT": str(tmp_path / "tst")})

        assert 0 == lproc.returncode, lproc.stderr
        assert [str(lpth_home)] * 2 == [lt[1] for lt in _calls(lpth_proj)]
        assert (lpth_home / "logs" / "nwc_interpol.log").exists()
        assert (lpth_home / "data" / "state" / ".nowcast_instance.lock").exists()

    def test_trava_recusa_sobreposicao_fora_da_raiz(self, tmp_path):
        lpth_proj = _project(tmp_path)
        lpth_home = self._home(tmp_path, "../mvol/catalog.db")

        lproc = self._run(lpth_proj, {"NWC_HOME": str(lpth_home), "MET_ENV": "test",
                                      "MET_TST_ROOT": str(tmp_path / "tst"),
                                      "NWC_DBASE_FILE": "../../../03.dat/data/dbase/catalog.db"})

        assert 1 == lproc.returncode
        assert "fora de" in lproc.stderr
        assert [] == _calls(lpth_proj)

# < the end >──────────────────────────────────────────────────────────────────────────
