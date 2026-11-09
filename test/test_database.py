# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_database
testes do nwc_database: transações, operações do nowcast e o contrato de
DDL com o 21.mvol

2026.set  mlabru  vol_dbase.py do 21.mvol agora em apps/dbase/ (o contrato
                  de DDL era pulado em silêncio com o caminho antigo)
2026.set  mlabru  view cappi_pending (criada no banco do 21.mvol, recriada
                  quando os presets mudam)
2026.set  mlabru  cappi_products: interpolado/sintético/séries sobre produtos,
                  filtro do preset ativo, pending_rows, sequences.preset e
                  ALTER TABLE num sequences antigo

2026.set  mlabru  reescrito: operações de scan/process saíram (21.mvol);
                  contrato de DDL com vol_dbase.py
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import pathlib
import re
import sqlite3

# pytest
import pytest

# locals
import work.nwc_database as db

# < helpers >──────────────────────────────────────────────────────────────────────────

# vol_dbase.py do 21.mvol (dono dos volumes observados)
MPTH_VOL_DBASE = (pathlib.Path(__file__).resolve().parents[3] /
                  "21.mvol" / "04.dvl" / "apps" / "dbase" / "vol_dbase.py")

# -------------------------------------------------------------------------------------
def _ddl(fs_source: str) -> dict:
    """
    CS_CREATE_* de um módulo, com espaços normalizados
    """
    ldct = dict(re.findall(r'^(CS_CREATE_\w+)\s*=\s*"""(.*?)"""', fs_source, re.S | re.M))
    ldct.update(re.findall(r'^(CS_CREATE_INDEX_\d)\s*=\s*"(.*?)"', fs_source, re.M))

    return {lk: " ".join(lv.split()) for lk, lv in ldct.items()}

# < contrato >─────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not MPTH_VOL_DBASE.exists(), reason="21.mvol não disponível")
def test_ddl_identico_ao_21_mvol():
    ldct_nc = _ddl(pathlib.Path(db.__file__).read_text(encoding="utf-8"))
    ldct_vd = _ddl(MPTH_VOL_DBASE.read_text(encoding="utf-8"))

    # tudo o que o 21.mvol define tem de ser idêntico aqui (sequences é só nosso)
    for ls_name, ls_sql in ldct_vd.items():
        assert ldct_nc[ls_name] == ls_sql, ls_name

# < transações >───────────────────────────────────────────────────────────────────────

class TestTransacoes:

    def test_commit(self, add_volume, query):
        add_volume(1000)

        with db.transaction_write() as ldb:
            ldb.insert_log("CGU", 250, 1000, "x", db.CS_LOG_SUCCESS, "ok")

        assert [(1,)] == query("SELECT COUNT(*) FROM processing_log")

    def test_rollback_em_excecao(self, mem_db, query):
        with pytest.raises(RuntimeError):
            with db.transaction_write() as ldb:
                ldb.insert_log("CGU", 250, 1000, "x", db.CS_LOG_SUCCESS, "ok")
                raise RuntimeError("falha")

        assert [(0,)] == query("SELECT COUNT(*) FROM processing_log")

    def test_chave_unica(self, add_volume, mem_db):
        add_volume(1000)

        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction_write() as ldb:
                ldb.insert_interpolated("CGU", 250, 1000, "nwc250v2", "/x.npy", 0.1, 1.0)

# < operações >────────────────────────────────────────────────────────────────────────

class TestOperacoes:

    def test_get_volume(self, add_volume):
        add_volume(1000)

        with db.transaction() as ldb:
            assert 1000 == ldb.get_volume("CGU", 250, 1000)["timestamp"]
            assert ldb.get_volume("CGU", 400, 1000) is None

    def test_insert_interpolated(self, mem_db, query):
        with db.transaction_write() as ldb:
            ldb.insert_interpolated("CGU", 250, 1000, "nwc250v2", "/c/x.npy", 0.2, 0.8)

        # produto DONE; volumes não é tocado (o interpolado não veio de um .mvol)
        assert [("nwc250v2", "interpolated", 2, "/c/x.npy", 0.2, 0.8)] == query(
            "SELECT preset, origin, status, cappi_path, rain_fraction, valid_fraction "
            "FROM cappi_products")
        assert [(0,)] == query("SELECT COUNT(*) FROM volumes")

    def test_delete_so_sintetico(self, add_volume, query):
        add_volume(1000)
        add_volume(1600, fi_status=db.CI_STATUS_INTERPOLATED)
        add_volume(2200, fi_status=db.CI_STATUS_PREDICTED)

        with db.transaction_write() as ldb:
            assert 0 == ldb.delete_product("CGU", 250, 1000, "nwc250v2")
            assert 0 == ldb.delete_product("CGU", 250, 1600, "nwc400v2")
            assert 1 == ldb.delete_product("CGU", 250, 1600, "nwc250v2")
            assert 1 == ldb.delete_product("CGU", 250, 2200, "nwc250v2")

        assert [(1000,)] == query("SELECT timestamp FROM cappi_products")
        assert [(1000,)] == query("SELECT timestamp FROM volumes")

    def test_series_rows_filtra_status_e_cappi(self, add_volume):
        add_volume(1000)
        add_volume(1600, fi_status=db.CI_STATUS_INTERPOLATED)
        add_volume(2200, fi_status=db.CI_STATUS_NEW)
        add_volume(2800, fi_status=db.CI_STATUS_FAILED)

        with db.transaction() as ldb:
            llst = ldb.series_rows()

        assert [1000, 1600] == [ld["timestamp"] for ld in llst]
        assert isinstance(llst[0], dict)

        # observado: fullpath é o .mvol; interpolado: o próprio CAPPI
        assert "/raw/CGU_250_1000.mvol" == llst[0]["fullpath"]
        assert llst[1]["fullpath"] == llst[1]["cappi_path"]
        assert ["observed", "interpolated"] == [ld["origin"] for ld in llst]

    def test_series_rows_so_preset_ativo(self, add_volume):
        # legado e preset de outro alcance ficam de fora
        add_volume(1000)
        add_volume(1600, fs_preset="nwc250v0")
        add_volume(2200, fs_preset="nwc400v2")
        add_volume(1000, fi_alcance=400, fb_write_npy=False)

        with db.transaction() as ldb:
            llst = ldb.series_rows()

        assert [(250, 1000, "nwc250v2"), (400, 1000, "nwc400v2")] == \
               [(ld["alcance"], ld["timestamp"], ld["preset"]) for ld in llst]

    def test_pending_rows(self, add_volume, mem_db):
        add_volume(1000)                                          # DONE no ativo
        add_volume(1600, fi_status=db.CI_STATUS_NEW)              # sem produto
        add_volume(2200, fi_status=db.CI_STATUS_FAILED)           # FAILED no ativo
        add_volume(2800, fs_preset="nwc250v0")                    # só legado
        add_volume(3400, fi_status=db.CI_STATUS_INTERPOLATED)     # sintético, sem volume
        add_volume(4600, fi_status=db.CI_STATUS_NEW, fi_alcance=999,  # sem preset ativo
                   fs_preset="nwc250v2", fb_write_npy=False)

        # volume com um interpolado na chave: a conversão vai substituí-lo,
        # então continua pendente (e o 21.mvol mantém o .mvol)
        add_volume(5200, fi_status=db.CI_STATUS_NEW)
        with db.transaction_write() as ldb:
            ldb.insert_interpolated("CGU", 250, 5200, "nwc250v2", "/x/CGU_250_5200.npy", 0.1, 0.8)

        # .mvol removido: não há mais o que converter
        add_volume(4000, fi_status=db.CI_STATUS_NEW)
        lconn = sqlite3.connect(mem_db)
        lconn.execute("UPDATE volumes SET has_mvol = 0 WHERE timestamp = 4000")
        lconn.commit()
        lconn.close()

        with db.transaction() as ldb:
            llst = ldb.pending_rows()

        assert [1600, 2800, 5200] == sorted(ld["timestamp"] for ld in llst)

    def test_log_em_iso_utc(self, mem_db, query):
        with db.transaction_write() as ldb:
            ldb.insert_log("CGU", 250, 1000, "x", db.CS_LOG_FAILED, "erro")

        ((ls_created,),) = query("SELECT created_at FROM processing_log")
        assert ls_created.endswith("+00:00")

    def test_replace_sequences(self, mem_db, query):
        lrow = ("CGU", 250, 1, 2, 3, 4, 5, 0, "nwc250v2")

        with db.transaction_write() as ldb:
            ldb.replace_sequences([lrow, lrow])

        with db.transaction_write() as ldb:
            ldb.replace_sequences([lrow])

        assert [(1, "nwc250v2")] == query("SELECT COUNT(*), preset FROM sequences")

# < banco criado pelo 21.mvol >───────────────────────────────────────────────────────

# -------------------------------------------------------------------------------------
def _mvol_only_db(fpth_db):
    """
    banco como o vol_dbase do 21.mvol o cria: só as tabelas compartilhadas
    """
    lconn = sqlite3.connect(fpth_db)

    for ls_sql in (db.CS_CREATE_VOLUMES, db.CS_CREATE_LOG, db.CS_CREATE_RADAR_SITES,
                   db.CS_CREATE_INDEX_1, db.CS_CREATE_INDEX_2, db.CS_CREATE_INDEX_3):
        lconn.execute(ls_sql)

    lconn.commit()
    lconn.close()

def _schema(fpth_db):
    lconn = sqlite3.connect(fpth_db)

    try:
        return dict(lconn.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"))

    finally:
        lconn.close()

class TestBancoDoMvol:

    def test_build_sequences_cria_a_tabela(self, tmp_config):
        import nwcSequences as bs

        _mvol_only_db(tmp_config.database)
        ldct_before = _schema(tmp_config.database)

        assert 0 == bs.main([])

        ldct_after = _schema(tmp_config.database)
        assert {"sequences", "idx_sequences_target", "cappi_products",
                "idx_products_status", db.CS_VIEW_PENDING} == set(ldct_after) - set(ldct_before)
        # as tabelas do 21.mvol não mudam
        assert all(ldct_after[ls] == ldct_before[ls] for ls in ldct_before)

    def test_interpolate_cria_a_tabela(self, tmp_config):
        import nwcInterpolate as ni

        _mvol_only_db(tmp_config.database)

        assert 0 == ni.main([])
        assert "sequences" in _schema(tmp_config.database)

    def test_sequences_antigo_ganha_preset(self, tmp_config, query):
        # sequences de antes de cappi_products (sem a coluna preset)
        lconn = sqlite3.connect(tmp_config.database)
        lconn.execute(db.CS_CREATE_SEQUENCES.replace(",\n    preset     TEXT", ""))
        lconn.execute("INSERT INTO sequences (radar, alcance, input_1, input_2, input_3, "
                      "input_4, target) VALUES ('CGU', 250, 1, 2, 3, 4, 5)")
        lconn.commit()
        lconn.close()

        db.create_schema()

        assert [("CGU", None)] == query("SELECT radar, preset FROM sequences")

    def test_view_recriada_quando_presets_mudam(self, mem_db, query, monkeypatch):
        import work.nwc_presets as ps

        monkeypatch.setitem(ps.MDCT_ACTIVE, 250, "nwc250v9")
        db.create_schema()

        ((ls_sql,),) = query("SELECT sql FROM sqlite_master WHERE name = ?", (db.CS_VIEW_PENDING,))
        assert "'nwc250v9'" in ls_sql and "'nwc250v2'" not in ls_sql

    def test_create_schema_idempotente(self, mem_db):
        ldct = _schema(mem_db)
        db.create_schema()
        assert ldct == _schema(mem_db)

    def test_ensure_schema_falha_sem_derrubar(self, tmp_config, tmp_path):
        import dataclasses
        import work.nwc_config as cf

        # diretório no lugar do arquivo do banco
        (tmp_path / "dir.db").mkdir()
        cf.G_CONFIG = dataclasses.replace(tmp_config, database=tmp_path / "dir.db")

        assert db.ensure_schema() is False

# < the end >──────────────────────────────────────────────────────────────────────────
