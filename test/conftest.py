# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

conftest
fixtures compartilhadas pela suite de testes.

2026.out  mlabru  isolated_env (autouse): sem NWC_*/MET_* herdados do shell
                  (um env.sh do tst_env sourceado mudaria os caminhos do
                  config.ini e ligaria a trava MET_ENV=test)
2026.set  mlabru  tmp_config com convert_workers=1 (nwcConvert serial)
2026.set  mlabru  add_volume no modelo de cappi_products (observado = volumes +
                  produto, sintético = só produto, em <cappi>/<preset>/);
                  o modelo antigo, só volumes, virou add_legacy_volume
                  (usado pelos testes das migrações)

2026.set  mlabru  nc_* -> nwc_* (nc_interpolate -> nwcInterpolate,
                  nc_build_sequences -> nwcSequences), acompanhando a
                  renomeação dos módulos de apps/

2026.set  mlabru  add_volume grava em cappi_path um caminho plano "antigo"
                  (<tmp>/legado/<nome>, que não existe), com o arquivo físico em
                  <cappi>/<R>_<A>/: prova que a leitura segue a regra do 21.mvol
                  (nome do arquivo), não o diretório gravado no banco

2026.set  mlabru  config/database -> nc_config/nc_database; fixture
                  add_volume (volume com .npy no layout do 21.mvol);
                  cappi_pair (optical flow) removida com o Farnebäck;
                  testes de blend fora da coleta (ver collect_ignore)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import sqlite3

# numPy
import numpy as np
# pytest
import pytest

# locals
import work.nwc_config as cf
import work.nwc_database as db
import work.nwc_presets as ps

# < collection >───────────────────────────────────────────────────────────────────────

# apps/blend/ ficou como versão antiga (depende de radar.rd_radar, que foi
# para o 21.mvol) e vol_blender.sh só existe no 21.mvol — lá esses testes rodam
collect_ignore = ["test_vb_blend.py", "test_vol_blender.py"]

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """
    remove do ambiente as sobreposições de caminho do nwc_cfg.sh e a trava
    MET_ENV=test; os subprocessos dos testes herdam o ambiente limpo
    """
    for ls_var in (*cf.CDCT_PATH_ENV.values(), "NWC_PATH", "NWC_HOME", "NWC_VENV", "MET_ENV",
                   "MET_TST_ROOT"):
        monkeypatch.delenv(ls_var, raising=False)

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_config(tmp_path):
    """
    SConfig mínimo apontando para diretórios temporários do pytest, injetado
    em cf.G_CONFIG (restaurado para None ao final)
    """
    lpth_cappi = tmp_path / "cappi"
    lpth_logs = tmp_path / "logs"

    for lpth in (lpth_cappi, lpth_logs):
        lpth.mkdir()

    lcfg = cf.SConfig(cappi_dir=lpth_cappi,
                      log_dir=lpth_logs,
                      database=tmp_path / "catalog.db",
                      busy_timeout_ms=1000,
                      log_level=10,
                      log_file=lpth_logs / "test.log",
                      interval_s=600,
                      tolerance_s=300,
                      max_gap=2,
                      workers=1,
                      min_valid_ratio=0.95,
                      min_rain_fraction=0.0,
                      convert_workers=1)

    cf.G_CONFIG = lcfg

    yield lcfg

    cf.G_CONFIG = None

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def mem_db(tmp_config):
    """
    esquema completo no banco temporário de tmp_config (arquivo, não
    :memory:, porque SDatabase abre uma conexão por transação)
    """
    db.create_schema()

    yield tmp_config.database

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def add_volume(mem_db, tmp_config):
    """
    insere um volume no modelo atual:
      - DONE (observado): linha em volumes + produto observed DONE
      - INTERPOLATED/PREDICTED: só o produto (sintético não tem .mvol)
      - FAILED: linha em volumes + produto observed FAILED, sem CAPPI
      - NEW/PROCESSING: só a linha em volumes (conversão pendente)

    arquivo físico em <cappi>/<preset>/<R>_<A>/<R>_<A>_<ts>.npy (preset ativo
    do alcance, ou fs_preset), mas cappi_path gravado como <tmp>/legado/<nome>
    — o nowcast tem de achar o arquivo pelo nome, como o 21.mvol

    uso: add_volume(ts, fill=10.0, radar="CGU", alcance=250,
                    status=db.CI_STATUS_DONE, valid=1.0, rain=0.5,
                    data=None, write_npy=True, stored_path=None,
                    preset=None) -> caminho físico do .npy
    """
    def _add(fi_ts, ff_fill=10.0, fs_radar="CGU", fi_alcance=250,
             fi_status=db.CI_STATUS_DONE, ff_valid=1.0, ff_rain=0.5,
             fnp_data=None, fb_write_npy=True, fs_stored_path=None, fs_preset=None):

        ls_preset = fs_preset or ps.active_id(fi_alcance)
        ls_series = f"{fs_radar}_{fi_alcance}"
        lpth_npy = (tmp_config.cappi_dir / ls_preset / ls_series /
                    f"{ls_series}_{fi_ts}.npy").resolve()

        # caminho gravado no banco: diferente do físico (ver docstring)
        ls_stored = fs_stored_path or str(tmp_config.cappi_dir.parent / "legado" / lpth_npy.name)

        if fb_write_npy:
            lpth_npy.parent.mkdir(parents=True, exist_ok=True)

            if fnp_data is None:
                fnp_data = np.full((8, 8), ff_fill, dtype=np.float32)

            np.save(lpth_npy, fnp_data)

        ldct_origin = {db.CI_STATUS_INTERPOLATED: db.CS_ORIGIN_INTERPOLATED,
                       db.CI_STATUS_PREDICTED:    db.CS_ORIGIN_PREDICTED}

        lconn = sqlite3.connect(mem_db)

        if fi_status not in ldct_origin:
            lconn.execute("""INSERT INTO volumes (radar, alcance, timestamp, fullpath, has_mvol,
                                                  status)
                             VALUES (?, ?, ?, ?, 1, ?)""",
                          (fs_radar, fi_alcance, fi_ts, f"/raw/{ls_series}_{fi_ts}.mvol",
                           fi_status))

        if fi_status in (db.CI_STATUS_DONE, db.CI_STATUS_FAILED, *ldct_origin):
            lb_done = db.CI_STATUS_FAILED != fi_status

            lconn.execute("""INSERT INTO cappi_products (radar, alcance, timestamp, preset,
                                                         origin, status, cappi_path,
                                                         rain_fraction, valid_fraction)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (fs_radar, fi_alcance, fi_ts, ls_preset,
                           ldct_origin.get(fi_status, db.CS_ORIGIN_OBSERVED),
                           db.CI_STATUS_DONE if lb_done else db.CI_STATUS_FAILED,
                           ls_stored if lb_done else None,
                           ff_rain if lb_done else None, ff_valid if lb_done else None))

        lconn.commit()
        lconn.close()

        return lpth_npy

    return _add

@pytest.fixture
def add_legacy_volume(mem_db, tmp_config):
    """
    modelo de antes de cappi_products (só as migrações usam): tudo em
    volumes, inclusive interpolados/previstos (status 4/5), com o arquivo
    físico em <cappi>/<R>_<A>/<R>_<A>_<ts>.npy e cappi_path gravado como
    <tmp>/legado/<nome> — o arquivo é achado pelo nome, como no 21.mvol

    uso: add_legacy_volume(ts, fill=10.0, radar="CGU", alcance=250,
                    status=db.CI_STATUS_DONE, valid=1.0, rain=0.5,
                    data=None, write_npy=True, stored_path=None) -> caminho físico do .npy
    """
    def _add(fi_ts, ff_fill=10.0, fs_radar="CGU", fi_alcance=250,
             fi_status=db.CI_STATUS_DONE, ff_valid=1.0, ff_rain=0.5,
             fnp_data=None, fb_write_npy=True, fs_stored_path=None):

        ls_series = f"{fs_radar}_{fi_alcance}"
        lpth_npy = (tmp_config.cappi_dir / ls_series / f"{ls_series}_{fi_ts}.npy").resolve()

        # caminho gravado no banco: diferente do físico (ver docstring)
        ls_stored = fs_stored_path or str(tmp_config.cappi_dir.parent / "legado" / lpth_npy.name)

        if fb_write_npy:
            lpth_npy.parent.mkdir(parents=True, exist_ok=True)

            if fnp_data is None:
                fnp_data = np.full((8, 8), ff_fill, dtype=np.float32)

            np.save(lpth_npy, fnp_data)

        lconn = sqlite3.connect(mem_db)
        lconn.execute("""INSERT INTO volumes (radar, alcance, timestamp, fullpath, has_mvol,
                                              has_cappi, cappi_path, interpolated, status,
                                              rain_fraction, valid_fraction)
                         VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?)""",
                      (fs_radar, fi_alcance, fi_ts, f"/raw/{ls_series}_{fi_ts}.mvol",
                       ls_stored, int(fi_status == db.CI_STATUS_INTERPOLATED), fi_status,
                       ff_rain, ff_valid))
        lconn.commit()
        lconn.close()

        return lpth_npy

    return _add

# ─────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def query(mem_db):
    """
    executa um SELECT no banco temporário e devolve list[tuple]
    """
    def _query(fs_sql, ft_params=()):
        lconn = sqlite3.connect(mem_db)

        try:
            return lconn.execute(fs_sql, ft_params).fetchall()

        finally:
            lconn.close()

    return _query

# < the end >──────────────────────────────────────────────────────────────────────────
