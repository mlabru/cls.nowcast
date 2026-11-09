# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_convert
testes do conversor .mvol -> CAPPI (cappi_products): fila, reserva, preset
por alcance, -9999 e estatísticas, retry, PROCESSING preso, observado
substituindo sintético, e o que nunca é tocado (volumes, .mvol, legado)

2026.out  mlabru  tentativa contada na reserva (preso esgotado vira FAILED),
                  reserva perdida, worker morto, rc 3, atomic_write,
                  banco ocupado na reserva (rc 0, nada reservado)
2026.set  mlabru  extração com cobertura (três estados, presets v2)
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import dataclasses
import os
import pickle
import sqlite3
import types
from unittest import mock

# numPy
import numpy as np

# pytest
import pytest

# locals
import work.nwc_atomic_io as aio
import work.nwc_config as cf
import work.nwc_database as db
import nwcConvert as nc

# < helpers >──────────────────────────────────────────────────────────────────────────

MI_TS = 1781530822

# CAPPI devolvido pela extração mockada: 1 pixel fora da cobertura, 1 coberto
# sem eco (-32), 1 com chuva e 1 com eco fraco
MNP_CAPPI = np.array([[-9999., -32.], [10., -5.]], dtype=np.float32)

def _worker_que_morre(f_task):
    """
    worker_convert que derruba o processo (no nível do módulo: vai por pickle)
    """
    os._exit(1)

def _age(fpth_db, fi_ts, fi_attempts=None):
    """
    envelhece a reserva do produto (processo morto há muito tempo)
    """
    lconn = sqlite3.connect(fpth_db)
    lconn.execute("UPDATE cappi_products SET updated_at = '2000-01-01T00:00:00+00:00' "
                  "WHERE timestamp = ?", (fi_ts,))
    if fi_attempts is not None:
        lconn.execute("UPDATE cappi_products SET attempts = ? WHERE timestamp = ?",
                      (fi_attempts, fi_ts))
    lconn.commit()
    lconn.close()

# -------------------------------------------------------------------------------------
@pytest.fixture
def add_mvol(mem_db, tmp_path):
    """
    registra um volume (como o volScan do 21.mvol) com um .mvol real no disco

    uso: add_mvol(ts, alcance=250, status=NEW, write=True) -> caminho do .mvol
    """
    def _add(fi_ts, fi_alcance=250, fi_status=db.CI_STATUS_NEW, fb_write=True):
        lpth = tmp_path / "raw" / f"CGU-{fi_alcance}--{fi_ts}.mvol"

        if fb_write:
            lpth.parent.mkdir(exist_ok=True)
            lpth.write_bytes(b"x")

        lconn = sqlite3.connect(mem_db)
        lconn.execute("INSERT INTO volumes (radar, alcance, timestamp, fullpath, has_mvol, status) "
                      "VALUES ('CGU', ?, ?, ?, 1, ?)", (fi_alcance, fi_ts, str(lpth), fi_status))
        lconn.commit()
        lconn.close()

        return lpth

    return _add

# -------------------------------------------------------------------------------------
def _fake_radar():
    return types.SimpleNamespace(latitude={"data": np.array([-31.4])},
                                 longitude={"data": np.array([-52.7])},
                                 altitude={"data": np.array([460.])})

# -------------------------------------------------------------------------------------
@pytest.fixture
def pyart_ok():
    """
    leitura/gridding mockados: extract_level_coverage devolve MNP_CAPPI
    """
    with mock.patch.object(nc.cc, "read_radar", return_value=_fake_radar()) as lread, \
         mock.patch.object(nc.cc, "add_coverage_field", return_value="cov"), \
         mock.patch.object(nc.cc, "grid_radar", return_value="G") as lgrid, \
         mock.patch.object(nc.cc, "extract_level_coverage", return_value=MNP_CAPPI) as lext:
        yield lread, lgrid, lext

# -------------------------------------------------------------------------------------
def _products(query):
    return query("SELECT alcance, timestamp, preset, origin, status, attempts, cappi_path, "
                 "rain_fraction, valid_fraction FROM cappi_products ORDER BY alcance, timestamp")

# < conversão >────────────────────────────────────────────────────────────────────────

class TestConverte:

    def test_sucesso(self, add_mvol, pyart_ok, query, tmp_config):
        lpth_mvol = add_mvol(MI_TS)

        assert (1, 0) == nc.convert_all()

        ((li_a, li_ts, ls_preset, ls_origin, li_status, li_att, ls_path, lf_rain, lf_valid),) = \
            _products(query)

        assert (250, MI_TS, "nwc250v2", "observed", db.CI_STATUS_DONE, 1) == \
               (li_a, li_ts, ls_preset, ls_origin, li_status, li_att)
        assert ls_path == str(nc.build_filename(tmp_config.cappi_dir, "nwc250v2", "CGU", 250, MI_TS))
        assert ls_path.endswith(f"nwc250v2/CGU_250/CGU_250_{MI_TS}.npy")

        # .npy float32 com os três estados; valid = coberto (o -32 conta),
        # rain = eco > 0 dBZ
        lnp = np.load(ls_path)
        assert np.float32 == lnp.dtype and [[-9999., -32.], [10., -5.]] == lnp.tolist()
        assert (pytest.approx(0.25), pytest.approx(0.75)) == (lf_rain, lf_valid)

        assert [("CGU", -31.4, -52.7, 460.)] == query(
            "SELECT radar, latitude, longitude, altitude FROM radar_sites")
        assert [(db.CS_STEP_PROCESS, db.CS_LOG_SUCCESS)] == query(
            "SELECT step, status FROM processing_log")

        # volumes (ingestão do 21.mvol) e o .mvol ficam intactos
        assert [(db.CI_STATUS_NEW, 1, None)] == query(
            "SELECT status, has_mvol, cappi_path FROM volumes")
        assert lpth_mvol.exists()

    def test_preset_por_alcance(self, add_mvol, pyart_ok, query):
        add_mvol(MI_TS, fi_alcance=400)

        nc.convert_all()

        ((_, _, ls_preset, *_, ls_path, _, _),) = _products(query)
        assert "nwc400v2" == ls_preset
        assert ls_path.endswith(f"nwc400v2/CGU_400/CGU_400_{MI_TS}.npy")

        # o worker gridou com a extensão do 400 (±300 km), com a cobertura
        # junto, e extraiu com o "sem eco" de nwc_presets
        _, lgrid, lext = pyart_ok
        assert 300000.0 == lgrid.call_args.args[1].extent_m
        assert ("cov",) == lgrid.call_args.args[2]
        assert -32.0 == lext.call_args.args[3]

    def test_idempotente(self, add_mvol, pyart_ok, query):
        add_mvol(MI_TS)

        assert (1, 0) == nc.convert_all()
        assert (0, 0) == nc.convert_all()
        assert 1 == len(_products(query))

    def test_legado_nao_conta_e_fica(self, add_mvol, add_volume, pyart_ok, query, mem_db):
        # volume já tinha um produto de legado: continua na fila do preset ativo
        add_mvol(MI_TS, fi_status=db.CI_STATUS_DONE)
        add_volume(MI_TS + 1, fs_preset="nwc250v0")
        lconn = sqlite3.connect(mem_db)
        lconn.execute("UPDATE cappi_products SET timestamp = ?", (MI_TS,))
        lconn.execute("DELETE FROM volumes WHERE timestamp = ?", (MI_TS + 1,))
        lconn.commit()
        lconn.close()

        assert (1, 0) == nc.convert_all()
        assert [("nwc250v0", "observed", 2), ("nwc250v2", "observed", 2)] == query(
            "SELECT preset, origin, status FROM cappi_products ORDER BY preset")

    def test_sem_mvol_ou_sintetico_fora_da_fila(self, add_mvol, mem_db, pyart_ok):
        add_mvol(MI_TS)
        add_mvol(MI_TS + 600, fi_status=db.CI_STATUS_INTERPOLATED)
        lconn = sqlite3.connect(mem_db)
        lconn.execute("UPDATE volumes SET has_mvol = 0 WHERE timestamp = ?", (MI_TS,))
        lconn.commit()
        lconn.close()

        assert (0, 0) == nc.convert_all()

    def test_alcance_sem_preset_fora_da_fila(self, add_mvol, pyart_ok):
        add_mvol(MI_TS, fi_alcance=150)

        assert (0, 0) == nc.convert_all()

# < falhas >───────────────────────────────────────────────────────────────────────────

class TestFalhas:

    def test_retenta_ate_o_limite_e_deixa_o_mvol(self, add_mvol, query):
        lpth_mvol = add_mvol(MI_TS)

        with mock.patch.object(nc.cc, "read_radar", side_effect=OSError("hdf5 corrompido")):
            for _ in range(3):
                assert (0, 1) == nc.convert_all()

            # esgotou ([convert] max_retries = 3): sai da fila
            assert (0, 0) == nc.convert_all()

        ((*_, li_status, li_att, ls_path, _, _),) = _products(query)
        assert (db.CI_STATUS_FAILED, 3, None) == (li_status, li_att, ls_path)
        assert 3 == len(query("SELECT * FROM processing_log WHERE status = 'FAILED'"))
        assert lpth_mvol.exists()

    def test_mvol_ausente(self, add_mvol, query):
        add_mvol(MI_TS, fb_write=False)

        assert (0, 1) == nc.convert_all()
        ((ls_msg,),) = query("SELECT message FROM processing_log")
        assert "FileNotFoundError" in ls_msg

    def test_processing_preso_e_retomado(self, add_mvol, mem_db, pyart_ok):
        add_mvol(MI_TS)
        add_mvol(MI_TS + 600)

        with db.transaction_write() as ldb:
            for ldct in ldb.volumes_to_convert(3, 3600):
                ldb.claim_conversion(ldct)

        # o primeiro preso há muito tempo; o segundo acabou de ser reservado
        lconn = sqlite3.connect(mem_db)
        lconn.execute("UPDATE cappi_products SET updated_at = '2000-01-01T00:00:00+00:00' "
                      "WHERE timestamp = ?", (MI_TS,))
        lconn.commit()
        lconn.close()

        assert (1, 0) == nc.convert_all()

# < observado x sintético >────────────────────────────────────────────────────────────

class TestSubstituiSintetico:

    def test_observado_substitui_interpolado(self, add_mvol, add_volume, pyart_ok, query, mem_db):
        # interpolado com a mesma chave, gravado num caminho diferente do final
        lpth_int = add_volume(MI_TS, fi_status=db.CI_STATUS_INTERPOLATED)
        lpth_old = lpth_int.with_name("velho.npy")
        lpth_int.rename(lpth_old)
        lconn = sqlite3.connect(mem_db)
        lconn.execute("UPDATE cappi_products SET cappi_path = ?", (str(lpth_old),))
        lconn.commit()
        lconn.close()

        add_mvol(MI_TS)

        assert (1, 0) == nc.convert_all()

        ((*_, ls_origin, li_status, li_att, ls_path, _, _),) = _products(query)
        assert ("observed", db.CI_STATUS_DONE, 1) == (ls_origin, li_status, li_att)
        # o .npy do interpolado sumiu depois do commit
        assert not lpth_old.exists()
        assert MNP_CAPPI.tolist() == np.load(ls_path).tolist()

    def test_mesmo_caminho_nao_apaga_o_observado(self, add_mvol, add_volume, pyart_ok, query):
        add_volume(MI_TS, fi_status=db.CI_STATUS_INTERPOLATED, ff_fill=42.0)
        add_mvol(MI_TS)

        nc.convert_all()

        ((*_, ls_path, _, _),) = _products(query)
        # o observado foi gravado por cima do arquivo do interpolado e ficou
        assert MNP_CAPPI.tolist() == np.load(ls_path).tolist()

    def test_falha_na_substituicao_apaga_o_sintetico(self, add_mvol, add_volume, query):
        lpth_int = add_volume(MI_TS, fi_status=db.CI_STATUS_INTERPOLATED)
        add_mvol(MI_TS, fb_write=False)

        assert (0, 1) == nc.convert_all()

        # a linha agora é do observado (FAILED); o .npy do interpolado não é
        # mais referenciado
        assert [("observed", db.CI_STATUS_FAILED)] == query(
            "SELECT origin, status FROM cappi_products")
        assert not lpth_int.exists()

# < auxiliares >───────────────────────────────────────────────────────────────────────

class TestAuxiliares:

    def test_dry_run_nao_reserva(self, add_mvol, query, capsys):
        add_mvol(MI_TS)

        assert 0 == nc.main(["--dry-run"])
        assert f"CGU_250 {MI_TS}" in capsys.readouterr().out
        assert [] == _products(query)

    def test_main_rc(self, add_mvol, pyart_ok, tmp_config, tmp_path):
        import dataclasses
        import work.nwc_config as cf

        add_mvol(MI_TS)
        assert 0 == nc.main([])

        # volume que falha: 3 (rotineiro; 1 é o rc do Python numa exceção)
        add_mvol(MI_TS + 600, fb_write=False)
        assert nc.CI_RC_PARTIAL == nc.main([])

        # banco inutilizável: 2 (fatal)
        (tmp_path / "dir.db").mkdir()
        cf.G_CONFIG = dataclasses.replace(tmp_config, database=tmp_path / "dir.db")
        assert nc.CI_RC_FATAL == nc.main([])

    def test_banco_ocupado_na_reserva_nao_e_fatal(self, add_mvol, pyart_ok, query, tmp_config,
                                                  caplog):
        # outro escritor (o volScan do 21.mvol) segura o lock: rc 0, nada reservado
        add_mvol(MI_TS)
        cf.G_CONFIG = dataclasses.replace(tmp_config, busy_timeout_ms=50)

        lconn = sqlite3.connect(tmp_config.database)
        lconn.execute("BEGIN IMMEDIATE")

        try:
            assert nc.CI_RC_OK == nc.main([])

        finally:
            lconn.rollback()
            lconn.close()

        assert "banco ocupado" in caplog.text
        assert [] == _products(query)

        # lock liberado: o ciclo seguinte converte
        assert nc.CI_RC_OK == nc.main([])
        assert [(db.CI_STATUS_DONE,)] == query("SELECT status FROM cappi_products")

    def test_tentativa_conta_na_reserva(self, add_mvol, mem_db, pyart_ok, query):
        # processo morto depois da reserva: a tentativa já ficou registrada
        add_mvol(MI_TS)
        nc.plan()
        assert [(db.CI_STATUS_PROCESSING, 1)] == query("SELECT status, attempts FROM cappi_products")

        _age(mem_db, MI_TS)
        assert (1, 0) == nc.convert_all()
        assert [(db.CI_STATUS_DONE, 2)] == query("SELECT status, attempts FROM cappi_products")

    def test_preso_que_esgotou_vira_failed(self, add_mvol, mem_db, pyart_ok, query):
        # o volume que derruba o processo toda vez: antes voltava para sempre
        lpth_mvol = add_mvol(MI_TS)
        nc.plan()
        _age(mem_db, MI_TS, fi_attempts=3)

        assert (0, 0) == nc.convert_all()
        assert [(db.CI_STATUS_FAILED, 3)] == query("SELECT status, attempts FROM cappi_products")
        ((ls_status, ls_msg),) = query("SELECT status, message FROM processing_log")
        assert db.CS_LOG_FAILED == ls_status and "preso" in ls_msg
        assert lpth_mvol.exists()

        # FAILED definitivo: sai da fila e da view de pendentes
        assert (0, 0) == nc.convert_all()
        with db.transaction() as ldb:
            assert [] == ldb.pending_rows()

    def test_reserva_perdida_nao_muda_o_produto(self, add_mvol, mem_db, pyart_ok, query):
        add_mvol(MI_TS)
        llst_results = nc.run_workers(nc.plan())

        # outra execução retomou o volume e já terminou
        lconn = sqlite3.connect(mem_db)
        lconn.execute("UPDATE cappi_products SET status = ?, cappi_path = '/outra.npy'",
                      (db.CI_STATUS_DONE,))
        lconn.commit()
        lconn.close()

        assert (0, 0) == nc.persist(llst_results)
        assert [(db.CI_STATUS_DONE, "/outra.npy")] == query(
            "SELECT status, cappi_path FROM cappi_products")
        assert [] == query("SELECT * FROM processing_log")

    def test_worker_morto_vira_falha(self, add_mvol, tmp_config, monkeypatch):
        # um processo que morre (OOM, segfault) travava o mp.Pool.map
        add_mvol(MI_TS)
        add_mvol(MI_TS + 600)

        cf.G_CONFIG = dataclasses.replace(tmp_config, convert_workers=2)
        llst_tasks = nc.plan()

        monkeypatch.setattr(nc, "worker_convert", _worker_que_morre)
        llst_results = nc.run_workers(llst_tasks)

        assert [False, False] == [lres.success for lres in llst_results]
        assert all("worker perdido" in lres.message for lres in llst_results)
        assert (0, 2) == nc.persist(llst_results)

    def test_atomic_write_sem_temporario_na_falha(self, tmp_path):
        lpth = tmp_path / "x.npy"

        def _falha(fpth):
            fpth.write_bytes(b"meio")
            raise OSError("disco cheio")

        with pytest.raises(OSError):
            aio.atomic_write(lpth, _falha)

        assert [] == list(tmp_path.iterdir())

        # permissões de um arquivo comum (mkstemp cria com 0600)
        aio.atomic_write(lpth, lambda fpth: fpth.write_bytes(b"ok"))
        li_umask = os.umask(0)
        os.umask(li_umask)
        assert (0o666 & ~li_umask) == (lpth.stat().st_mode & 0o777)
        assert [lpth] == list(tmp_path.iterdir())

    def test_task_e_resultado_sao_pickle_safe(self):
        ltask = nc.SConvertTask("CGU", 250, 1, "/a.mvol", "nwc250v2", "/cappi")
        lres = nc.SConvertResult(ltask, True, cappi_path="/c.npy")

        assert ltask == pickle.loads(pickle.dumps(ltask))
        assert lres == pickle.loads(pickle.dumps(lres))

    def test_preset_divergente_falha_no_worker(self, tmp_path):
        ltask = nc.SConvertTask("CGU", 250, 1, str(tmp_path / "a.mvol"), "nwc250v0", str(tmp_path))

        lres = nc.worker_convert(ltask)
        assert not lres.success and "diferente do ativo" in lres.message

# < the end >──────────────────────────────────────────────────────────────────────────
