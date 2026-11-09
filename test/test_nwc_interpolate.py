# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_nwc_interpolate
testes da interpolação: política do fill −9999, média em Z, optical flow,
estatísticas e o fluxo completo plano -> worker -> banco

2026.out  mlabru  lacuna ocupada (observado FAILED no horário do slot); IntegrityError
                  da corrida vira aviso, não falha
2026.out  mlabru  staging: observado convertido depois do plano no mesmo horário
                  fica intacto; worker morto vira falha (ProcessPoolExecutor)

2026.set  mlabru  cappi_products: interpolados como produtos do preset ativo
                  (<cappi>/<preset>/), build_filename/SGapTask/SSlotResult com preset
2026.set  mlabru  média em Z, Farnebäck (disco em movimento, borda −9999),
                  alpha ponderado; fluxo completo com o default de produção
                  (flow + Z)

2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import dataclasses
import os
import pathlib
import pickle

# numPy
import numpy as np
# pytest
import pytest

# locals
import work.nwc_config as cf
import work.nwc_database as db
import nwcInterpolate as ni

# < helpers >──────────────────────────────────────────────────────────────────────────

MI_T0 = 1785642167
MF_ND = ni.CF_NODATA

def _worker_que_morre(f_task):
    """
    worker_gap que derruba o processo (no nível do módulo: vai por pickle)
    """
    os._exit(1)

# < interpolate_linear >───────────────────────────────────────────────────────────────

class TestInterpolateLinear:

    def test_media_ponderada(self):
        lnp = ni.interpolate_linear(np.full((2, 2), 10.0), np.full((2, 2), 40.0), 1 / 3)
        assert np.allclose(lnp, 20.0)
        assert np.float32 == lnp.dtype

    def test_fill_nos_dois_permanece_fill(self):
        lnp = ni.interpolate_linear(np.array([[MF_ND]]), np.array([[MF_ND]]), 0.5)
        assert MF_ND == lnp[0, 0]

    def test_fill_de_um_lado_copia_o_valido(self):
        # antes: −9999 contava como dado e dava 0,5·(−9999) + 0,5·30
        lnp = ni.interpolate_linear(np.array([[MF_ND, 30.0]]), np.array([[30.0, MF_ND]]), 0.5)
        assert np.allclose(lnp, [[30.0, 30.0]])

    def test_nan_tambem_e_invalido(self):
        lnp = ni.interpolate_linear(np.array([[np.nan]]), np.array([[np.nan]]), 0.5)
        assert MF_ND == lnp[0, 0]

    def test_saida_sem_nan(self):
        lnp = ni.interpolate_linear(np.array([[np.nan, 1.0]]), np.array([[MF_ND, np.nan]]), 0.5)
        assert np.isfinite(lnp).all()

    @pytest.mark.parametrize("lf_alpha", [0.0, 1.0, -0.1, 1.5])
    def test_alpha_fora_do_intervalo(self, lf_alpha):
        with pytest.raises(ValueError):
            ni.interpolate_linear(np.zeros((2, 2)), np.zeros((2, 2)), lf_alpha)

    def test_dimensoes_diferentes(self):
        with pytest.raises(ValueError):
            ni.interpolate_linear(np.zeros((2, 2)), np.zeros((3, 3)), 0.5)

# < média em Z / dBZ >─────────────────────────────────────────────────────────────────

class TestBlend:

    def test_z_de_valores_iguais_e_identidade(self):
        lnp = ni.interpolate_linear(np.full((2, 2), 35.0), np.full((2, 2), 35.0), 0.3, "z")
        assert np.allclose(lnp, 35.0)

    def test_z_preserva_o_nucleo(self):
        # 40 dBZ com céu limpo (−30): dBZ dá 5, Z dá ~37
        lnp_a, lnp_b = np.array([[40.0]]), np.array([[-30.0]])
        assert ni.interpolate_linear(lnp_a, lnp_b, 0.5, "dbz")[0, 0] == pytest.approx(5.0)
        assert ni.interpolate_linear(lnp_a, lnp_b, 0.5, "z")[0, 0] == pytest.approx(36.99, abs=0.01)

    def test_peso_pelo_alpha(self):
        lnp = ni.interpolate_linear(np.array([[10.0]]), np.array([[40.0]]), 1 / 3, "dbz")
        assert lnp[0, 0] == pytest.approx(20.0)

    def test_blend_invalido(self):
        with pytest.raises(ValueError):
            ni.interpolate_linear(np.zeros((2, 2)), np.zeros((2, 2)), 0.5, "rms")

# < optical flow >─────────────────────────────────────────────────────────────────────

# -------------------------------------------------------------------------------------
def _disc(fi_cx: int, fi_cy: int = 32, fi_r: int = 6, fi_n: int = 64) -> np.ndarray:
    """
    célula de 40 dBZ sobre céu limpo (−30 dBZ)
    """
    lnp = np.full((fi_n, fi_n), -30.0, dtype=np.float32)
    ly, lx = np.ogrid[:fi_n, :fi_n]
    lnp[(lx - fi_cx) ** 2 + (ly - fi_cy) ** 2 <= fi_r ** 2] = 40.0

    return lnp

class TestFlow:

    def test_flow_move_a_celula(self):
        # célula anda 12 px: o flow a põe no meio do caminho, inteira; o
        # linear deixa dois meios discos sem núcleo
        lnp_a, lnp_b, lnp_mid = _disc(20), _disc(32), _disc(26)
        li_core = int((lnp_mid >= 35).sum())

        lnp_flow = ni.interpolate_fields(lnp_a, lnp_b, 0.5, "flow", "z")
        lnp_lin = ni.interpolate_fields(lnp_a, lnp_b, 0.5, "linear", "dbz")

        assert abs(int((lnp_flow >= 35).sum()) - li_core) <= 0.1 * li_core
        assert (lnp_lin >= 35).sum() < 0.1 * li_core
        assert lnp_flow[32, 26] == pytest.approx(40.0, abs=1.0)

    def test_flow_reaproveitado(self):
        lnp_a, lnp_b = _disc(20), _disc(32)
        lnp_f = ni.compute_flow(ni.to_nan(lnp_a), ni.to_nan(lnp_b))

        assert np.array_equal(ni.interpolate_fields(lnp_a, lnp_b, 0.5, "flow", "z", lnp_f),
                              ni.interpolate_fields(lnp_a, lnp_b, 0.5, "flow", "z"))

    def test_fill_na_borda_nao_vaza(self):
        # antes: o −9999 entrava no remap bilinear e saíam valores como −5000
        lnp_a, lnp_b = _disc(20), _disc(32)
        ly, lx = np.ogrid[:64, :64]
        lnp_out = (lx - 31.5) ** 2 + (ly - 31.5) ** 2 > 30 ** 2
        lnp_a[lnp_out] = MF_ND
        lnp_b[lnp_out] = MF_ND

        lnp = ni.interpolate_fields(lnp_a, lnp_b, 0.5, "flow", "z")

        assert not ((lnp > MF_ND) & (lnp < -100)).any()
        assert np.isfinite(lnp).all()
        assert (lnp[lnp_out] == MF_ND).all()

    def test_borda_da_imagem_nao_e_apagada(self):
        # antes: remap com borda NaN apagava a última linha/coluna (NaN·0)
        lnp = ni.interpolate_fields(np.full((8, 8), 10.0), np.full((8, 8), 40.0), 0.5, "flow", "z")
        assert not (lnp == MF_ND).any()

    def test_method_invalido(self):
        with pytest.raises(ValueError):
            ni.interpolate_fields(np.zeros((2, 2)), np.zeros((2, 2)), 0.5, "spline")

# < estatísticas e nomes >─────────────────────────────────────────────────────────────

class TestAuxiliares:

    def test_sem_eco_e_dado_valido_na_media_em_z(self):
        # -32 (coberto, sem eco) não é -9999: entra na média, e em Z quase não pesa
        lnp_a = np.array([[-32.0, -32.0]], dtype=np.float32)
        lnp_b = np.array([[-32.0, 30.0]], dtype=np.float32)

        lnp = ni.blend_fields(ni.to_nan(lnp_a), ni.to_nan(lnp_b), 0.5, "z")

        assert pytest.approx(-32.0, abs=1e-3) == lnp[0, 0]
        assert pytest.approx(10.0 * np.log10(0.5 * 10 ** -3.2 + 0.5 * 1e3), abs=1e-3) == lnp[0, 1]

    def test_estatisticas_sem_o_fill(self):
        lnp = np.array([[MF_ND, MF_ND, 10.0, -5.0]], dtype=np.float32)
        assert (0.25, 0.5) == ni.compute_statistics(lnp)

    def test_nome_no_layout_de_produto(self, tmp_path):
        lpth = ni.build_filename(tmp_path, "nwc250v2", "CGU", 250, 123)
        assert tmp_path.resolve() / "nwc250v2" / "CGU_250" / "CGU_250_123.npy" == lpth

    def test_task_e_resultado_sao_pickle_safe(self):
        ltask = ni.SGapTask("CGU", 250, "nwc250v2", "/a.npy", "/b.npy", ((1, 0.5),), "/cappi")
        lres = ni.SSlotResult("CGU", 250, "nwc250v2", 1, True, cappi_path="/c.npy")
        assert ltask == pickle.loads(pickle.dumps(ltask))
        assert lres == pickle.loads(pickle.dumps(lres))

    def test_worker_nao_levanta_excecao(self, tmp_path):
        ltask = ni.SGapTask("CGU", 250, "nwc250v2", str(tmp_path / "x.npy"), str(tmp_path / "y.npy"),
                            ((1, 0.5), (2, 0.7)), str(tmp_path))
        llst = ni.worker_gap(ltask)
        assert [False, False] == [lr.success for lr in llst]

# < fluxo completo >───────────────────────────────────────────────────────────────────

class TestInterpolateAll:

    def test_lacuna_de_um_slot(self, add_volume, query):
        add_volume(MI_T0, ff_fill=10.0)
        add_volume(MI_T0 + 1210, ff_fill=40.0)

        assert (1, 0, 0) == ni.interpolate_all()

        ((li_ts, ls_preset, li_status, ls_path, lf_valid),) = query(
            "SELECT timestamp, preset, status, cappi_path, valid_fraction "
            "FROM cappi_products WHERE origin = ?", (db.CS_ORIGIN_INTERPOLATED,))

        # horário estimado entre os reais (MI_T0 + 605), alpha = 0,5
        assert MI_T0 + 605 == li_ts
        assert ("nwc250v2", db.CI_STATUS_DONE) == (ls_preset, li_status)
        assert ls_path.endswith(f"nwc250v2/CGU_250/CGU_250_{MI_T0 + 605}.npy")
        # interpolado não entra em volumes (não veio de um .mvol)
        assert [(2,)] == query("SELECT COUNT(*) FROM volumes")
        # default de produção: flow (nulo em campos constantes) + média em Z
        assert np.allclose(np.load(ls_path), 10.0 * np.log10(0.5 * 10.0 + 0.5 * 1e4), atol=1e-3)
        assert 1.0 == lf_valid

        assert [(db.CS_STEP_INTERPOLATE, db.CS_LOG_SUCCESS)] == query(
            "SELECT step, status FROM processing_log")

    def test_lacuna_maior_que_max_gap_nao_e_interpolada(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 2400)       # 3 faltantes, max_gap = 2

        assert (0, 0, 0) == ni.interpolate_all()
        assert [(0,)] == query("SELECT COUNT(*) FROM cappi_products WHERE origin = 'interpolated'")

    def test_idempotente(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1800)

        assert (2, 0, 0) == ni.interpolate_all()
        assert (0, 0, 0) == ni.interpolate_all()
        assert [(2,)] == query("SELECT COUNT(*) FROM cappi_products WHERE origin = 'interpolated'")

    def test_lacuna_com_volume_pendente_e_adiada(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 600, fi_status=db.CI_STATUS_NEW, fb_write_npy=False)
        add_volume(MI_T0 + 1200)

        assert (0, 0, 0) == ni.interpolate_all()

    def test_observado_atrasado_apaga_o_interpolado(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1200)
        ni.interpolate_all()

        ((ls_path,),) = query("SELECT cappi_path FROM cappi_products WHERE origin = 'interpolated'")

        # o 21.mvol converte o volume que faltava
        add_volume(MI_T0 + 603)

        assert (0, 0, 1) == ni.interpolate_all()
        assert [] == query("SELECT * FROM cappi_products WHERE origin = 'interpolated'")
        assert not __import__("pathlib").Path(ls_path).exists()

    def test_vizinho_sem_arquivo_registra_falha(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1200, fb_write_npy=False)

        assert (0, 1, 0) == ni.interpolate_all()
        assert [(db.CS_LOG_FAILED,)] == query("SELECT status FROM processing_log")
        assert [] == query("SELECT * FROM cappi_products WHERE origin = 'interpolated'")

    def test_observado_convertido_depois_do_plano_fica_intacto(self, add_volume, query):
        # mesmo nome de arquivo: antes o worker sobrescrevia o .npy do
        # observado e o IntegrityError o apagava
        add_volume(MI_T0, ff_fill=10.0)
        add_volume(MI_T0 + 1210, ff_fill=40.0)

        l_plan = ni.plan()
        llst_results = ni.run_workers(l_plan.tasks)

        # o nwcConvert grava o observado no horário estimado (MI_T0 + 605)
        # enquanto os workers rodam
        lpth_obs = add_volume(MI_T0 + 605, ff_fill=99.0)

        # corrida inofensiva: aviso, não falha (não muda o rc)
        assert (0, 0, 0) == ni.persist(llst_results, l_plan.superseded)
        assert np.all(99.0 == np.load(lpth_obs))
        assert not ni.staging_path(lpth_obs).exists()
        assert [] == query("SELECT * FROM cappi_products WHERE origin = 'interpolated'")
        ((ls_status, ls_message),) = query("SELECT status, message FROM processing_log "
                                           "WHERE step = ?", (db.CS_STEP_INTERPOLATE,))
        assert db.CS_LOG_FAILED == ls_status
        assert ls_message.startswith("produto já existe")

    def test_observado_failed_no_horario_do_slot_ocupa_a_lacuna(self, add_volume, query):
        # cadência regular: o slot faltante cai exatamente no horário do
        # observado cuja conversão falhou, e o INSERT dava IntegrityError a
        # cada ciclo (BVA_400 1790477193 em produção)
        add_volume(MI_T0)
        add_volume(MI_T0 + 600, fi_status=db.CI_STATUS_FAILED, fb_write_npy=False)
        add_volume(MI_T0 + 1200)

        l_plan = ni.plan()

        assert [] == l_plan.tasks
        assert [ni.CS_GAP_OCCUPIED] == [ls for _, _, ls in l_plan.skipped]
        assert "ocupadas 1" in l_plan.summary_lines()[0]

        assert 0 == ni.main([])
        assert [] == query("SELECT * FROM cappi_products WHERE origin = 'interpolated'")
        assert [] == query("SELECT * FROM processing_log WHERE step = ?",
                           (db.CS_STEP_INTERPOLATE,))

    def test_observado_failed_fora_do_horario_do_slot_nao_ocupa(self, add_volume, query):
        # com deriva o slot estimado não coincide com o FAILED: interpola
        add_volume(MI_T0)
        add_volume(MI_T0 + 590, fi_status=db.CI_STATUS_FAILED, fb_write_npy=False)
        add_volume(MI_T0 + 1210)

        assert (1, 0, 0) == ni.interpolate_all()

    def test_sem_staging_depois_do_run(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1800)

        assert (2, 0, 0) == ni.interpolate_all()

        for (ls_path,) in query("SELECT cappi_path FROM cappi_products "
                                "WHERE origin = 'interpolated'"):
            assert pathlib.Path(ls_path).is_file()
            assert not ni.staging_path(pathlib.Path(ls_path)).exists()

    def test_worker_morto_vira_falha(self, add_volume, tmp_config, monkeypatch):
        # um processo que morre (OOM, segfault) travava o mp.Pool.map
        # duas lacunas: com uma só o run_workers é serial (e mataria o pytest)
        add_volume(MI_T0)
        add_volume(MI_T0 + 1200)
        add_volume(MI_T0 + 2400)

        cf.G_CONFIG = dataclasses.replace(tmp_config, workers=2)
        llst_tasks = ni.plan().tasks
        assert 2 == len(llst_tasks)

        monkeypatch.setattr(ni, "worker_gap", _worker_que_morre)
        llst_results = ni.run_workers(llst_tasks)

        assert [False, False] == [lres.success for lres in llst_results]
        assert all("worker perdido" in lres.message for lres in llst_results)

    def test_nunca_apaga_observado(self, add_volume, query, mem_db):
        add_volume(MI_T0)

        with db.transaction_write() as ldb:
            assert 0 == ldb.delete_product("CGU", 250, MI_T0, "nwc250v2")

        assert [(1,)] == query("SELECT COUNT(*) FROM cappi_products")
        assert [(1,)] == query("SELECT COUNT(*) FROM volumes")

# < dry-run >──────────────────────────────────────────────────────────────────────────

class TestDryRun:

    def _dry(self, capsys, *flst_args):
        assert 0 == ni.main(["--dry-run", *flst_args])
        return capsys.readouterr().out

    def test_nao_escreve_e_lista_o_que_seria_feito(self, add_volume, query, capsys, tmp_config):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1200)          # 1 faltante: interpolável
        add_volume(MI_T0 + 3600)          # 3 faltantes: longa

        ls_out = self._dry(capsys)

        assert [(3,)] == query("SELECT COUNT(*) FROM volumes")
        assert [(0,)] == query("SELECT COUNT(*) FROM processing_log")
        assert 3 == len(list(tmp_config.cappi_dir.rglob("*.npy")))

        assert str(ni.build_filename(tmp_config.cappi_dir, "nwc250v2", "CGU", 250, MI_T0 + 600)) in ls_out
        assert "     3  longa" in ls_out
        assert "nada será gravado" in ls_out

    def test_lista_exatamente_o_que_o_run_gera(self, add_volume, query, capsys):
        for li_k in (0, 2, 5, 6, 8):
            add_volume(MI_T0 + li_k * 600)

        ls_out = self._dry(capsys)
        lset_listed = {ll.split("=>  ")[1].split()[0] for ll in ls_out.splitlines() if "=>  /" in ll}

        ni.interpolate_all()

        assert lset_listed == {lr[0] for lr in query("SELECT cappi_path FROM cappi_products WHERE origin = 'interpolated'")}
        # lacunas: [1], [3, 4], [7]
        assert 4 == len(lset_listed)

    def test_motivos_sem_ancora_e_adiada(self, add_volume, capsys):
        # O I M O: o faltante tem um interpolado como vizinho
        add_volume(MI_T0)
        add_volume(MI_T0 + 600, fi_status=db.CI_STATUS_INTERPOLATED)
        add_volume(MI_T0 + 1800)
        # na outra série, lacuna com volume ainda pendente no 21.mvol
        add_volume(MI_T0, fi_alcance=400)
        add_volume(MI_T0 + 600, fi_alcance=400, fi_status=db.CI_STATUS_NEW, fb_write_npy=False)
        add_volume(MI_T0 + 1200, fi_alcance=400)

        ls_out = self._dry(capsys)

        assert "sem_ancora" in ls_out
        assert "adiada" in ls_out
        assert "total: 0" in ls_out.split("== interpolados a apagar")[0]

    def test_lacunas_lista_todas_com_o_destino(self, add_volume, capsys):
        # O M O M M M O I M O: interpolar (1), longa (3), sem_ancora (1)
        for li_k in (0, 2, 6, 9):
            add_volume(MI_T0 + li_k * 600)
        add_volume(MI_T0 + 7 * 600, fi_status=db.CI_STATUS_INTERPOLATED)

        ls_gaps = self._dry(capsys).split("== lacunas\n")[1]
        llst_lines = [ll.split() for ll in ls_gaps.splitlines()[1:4]]

        # (slots, destino), na ordem do início
        assert [("1", "interpolar"), ("3", "longa"), ("1", "sem_ancora")] == \
               [(ll[-2], ll[-1]) for ll in llst_lines]
        assert "total: 3, interpolar 1, longa 1, sem_ancora 1" in ls_gaps

    def test_marcas_sobrescreve_e_vizinho_ausente(self, add_volume, capsys, tmp_config):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1200)
        # arquivo órfão de um run que caiu antes do commit
        lpth = ni.build_filename(tmp_config.cappi_dir, "nwc250v2", "CGU", 250, MI_T0 + 600)
        lpth.parent.mkdir(parents=True, exist_ok=True)
        lpth.write_bytes(b"x")
        # outra série com vizinho sem arquivo
        add_volume(MI_T0, fi_alcance=400)
        add_volume(MI_T0 + 1200, fi_alcance=400, fb_write_npy=False)

        ls_out = self._dry(capsys)

        assert "[sobrescreve]" in ls_out
        assert "[vizinho ausente]" in ls_out

    def test_filtro_de_serie_e_timeline(self, add_volume, capsys):
        add_volume(MI_T0)
        add_volume(MI_T0 + 1200)
        add_volume(MI_T0, fi_alcance=400)

        ls_out = self._dry(capsys, "-s", "cgu-250", "--timeline")

        assert "== timeline CGU-250" in ls_out
        assert "CGU-400" not in ls_out

    def test_filtros_exigem_dry_run(self, mem_db):
        with pytest.raises(SystemExit):
            ni.main(["-s", "CGU-250"])

# < the end >──────────────────────────────────────────────────────────────────────────
