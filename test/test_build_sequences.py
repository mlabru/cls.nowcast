# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_build_sequences
testes da montagem das janelas 4 + 1 sobre a Timeline

2026.out  mlabru  ni._from_epoch -> nwc_time.epoch_to_utc
2026.out  mlabru  TestDryRun: o dry-run (com projeção) lista exatamente o que
                  nwcInterpolate + nwcSequences gravam
2026.set  mlabru  sequences.preset conferido em test_series_separadas
2026.set  mlabru  reescrito para as janelas sobre slots (jitter aceito, alvo
                  observado, cobertura relativa à série)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import dataclasses

# pytest
import pytest

# locals
import nwcInterpolate as ni
import nwcSequences as bs
import work.nwc_config as cf
import work.nwc_database as db
import work.nwc_time as nt

# < helpers >──────────────────────────────────────────────────────────────────────────

MI_T0 = 1785642167

# -------------------------------------------------------------------------------------
def _series(add_volume, fi_count, fi_step=600, **kwargs):
    """
    fi_count volumes observados consecutivos; devolve os timestamps
    """
    llst = [MI_T0 + li * fi_step for li in range(fi_count)]

    for li_ts in llst:
        add_volume(li_ts, **kwargs)

    return llst

# < build_sequences >──────────────────────────────────────────────────────────────────

class TestBuildSequences:

    def test_janela_com_jitter_real_e_aceita(self, add_volume, query):
        # antes: Δt precisava ser exatamente 600 s
        llst = [MI_T0, MI_T0 + 601, MI_T0 + 1199, MI_T0 + 1802, MI_T0 + 2398]

        for li_ts in llst:
            add_volume(li_ts)

        assert 1 == bs.build_sequences()[0]
        assert [("CGU", 250, *llst, 0)] == query(
            "SELECT radar, alcance, input_1, input_2, input_3, input_4, target, quality "
            "FROM sequences")

    def test_deriva_bva(self, add_volume):
        _series(add_volume, 12, fi_step=617, fs_radar="BVA")
        assert 8 == bs.build_sequences()[0]

    def test_janelas_deslizantes_e_reconstrucao(self, add_volume, query):
        _series(add_volume, 7)
        assert 3 == bs.build_sequences()[0]
        # a tabela é refeita, não acumulada
        assert 3 == bs.build_sequences()[0]
        assert [(3,)] == query("SELECT COUNT(*) FROM sequences")

    def test_lacuna_quebra_a_janela(self, add_volume):
        for li_k in (0, 1, 2, 4, 5, 6, 7):
            add_volume(MI_T0 + li_k * 600)

        # só [4..8) não tem lacuna, e tem 4 slots: nenhuma janela de 5
        assert 0 == bs.build_sequences()[0]

    def test_alvo_interpolado_rejeitado(self, add_volume):
        llst = _series(add_volume, 4)
        add_volume(llst[-1] + 600, fi_status=db.CI_STATUS_INTERPOLATED)

        li_created, lctr = bs.build_sequences()
        assert 0 == li_created
        assert 1 == lctr[bs.CS_REJ_TARGET]

    def test_entrada_interpolada_aceita_com_quality_1(self, add_volume, query):
        add_volume(MI_T0)
        add_volume(MI_T0 + 600, fi_status=db.CI_STATUS_INTERPOLATED)

        for li_k in (2, 3, 4):
            add_volume(MI_T0 + li_k * 600)

        assert 1 == bs.build_sequences()[0]
        assert [(1,)] == query("SELECT quality FROM sequences")

    def test_previsto_tem_quality_2(self, add_volume, query):
        add_volume(MI_T0, fi_status=db.CI_STATUS_PREDICTED)
        add_volume(MI_T0 + 600, fi_status=db.CI_STATUS_INTERPOLATED)

        for li_k in (2, 3, 4):
            add_volume(MI_T0 + li_k * 600)

        bs.build_sequences()
        assert [(2,)] == query("SELECT quality FROM sequences")

    def test_cobertura_relativa_a_serie(self, add_volume):
        # radar de 250 km no grid de ±250 km: ~0,785 em todos os volumes.
        # com o limiar absoluto de 95% nenhuma janela passaria
        _series(add_volume, 5, ff_valid=0.785)
        assert 1 == bs.build_sequences()[0]

    def test_volume_com_cobertura_baixa_rejeitado(self, add_volume):
        llst = _series(add_volume, 4, ff_valid=0.785)
        add_volume(llst[-1] + 600, ff_valid=0.5)

        li_created, lctr = bs.build_sequences()
        assert 0 == li_created
        assert 1 == lctr[bs.CS_REJ_VALID]

    def test_cappi_ausente_em_disco(self, add_volume):
        llst = _series(add_volume, 4)
        add_volume(llst[-1] + 600, fb_write_npy=False)

        li_created, lctr = bs.build_sequences()
        assert 0 == li_created
        assert 1 == lctr[bs.CS_REJ_FILES]

    def test_filtro_de_chuva(self, add_volume, tmp_config):
        _series(add_volume, 5, ff_rain=0.01)
        cf.G_CONFIG = dataclasses.replace(tmp_config, min_rain_fraction=0.05)

        li_created, lctr = bs.build_sequences()
        assert 0 == li_created
        assert 1 == lctr[bs.CS_REJ_RAIN]

    def test_series_separadas(self, add_volume, query):
        _series(add_volume, 5)
        _series(add_volume, 5, fi_alcance=400)

        assert 2 == bs.build_sequences()[0]
        # cada sequência leva o preset ativo da sua série
        assert [("CGU", 250, "nwc250v2"), ("CGU", 400, "nwc400v2")] == query(
            "SELECT radar, alcance, preset FROM sequences ORDER BY alcance")

    def test_banco_vazio(self, mem_db):
        assert 0 == bs.build_sequences()[0]

# < --dry-run >────────────────────────────────────────────────────────────────────────

def _listed(fs_out: str, fs_section: str) -> set:
    """
    {(série, alvo, quality, marcas)} das linhas de uma seção do dry-run
    """
    lset = set()

    for ls_line in fs_out.split(f"== {fs_section}")[1].split("\n\n")[0].splitlines()[2:]:
        if ls_line.startswith("total:"):
            break

        llst = ls_line.split()
        # série, data, hora, epoch, 4 entradas, quality, marcas...
        lset.add((llst[0], int(llst[3]), int(llst[8]), " ".join(llst[9:])))

    return lset

class TestDryRun:

    def _dry(self, capsys, *flst_args):
        assert 0 == bs.main(["--dry-run", *flst_args])
        return capsys.readouterr().out

    def test_nao_escreve(self, add_volume, query, capsys):
        # O O M O O O: uma lacuna interpolável
        for li_k in (0, 1, 3, 4, 5):
            add_volume(MI_T0 + li_k * 600, ff_rain=1.0)
        with db.transaction_write() as ldb:
            ldb.replace_sequences([("CGU", 250, 1, 2, 3, 4, 5, 0, "nwc250v2")])

        ls_out = self._dry(capsys)

        assert "nada será gravado" in ls_out
        assert [(1,)] == query("SELECT COUNT(*) FROM sequences")
        assert [(5,)] == query("SELECT COUNT(*) FROM cappi_products")

    def test_lista_exatamente_o_que_o_pipeline_gera(self, add_volume, query, capsys):
        # lacunas: [3] e [8] interpoláveis, [12..14] longa; fill 10 dBZ => o
        # interpolado tem valid 1.0 e rain 1.0, iguais à estimativa
        for li_k in (0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 15, 16, 17, 18, 19):
            add_volume(MI_T0 + li_k * 600, ff_rain=1.0)

        lset_listed = {(ls, li_t, li_q) for ls, li_t, li_q, _ in
                       _listed(self._dry(capsys), "sequências a gerar")}

        ni.interpolate_all()
        bs.build_sequences()

        assert lset_listed == {(f"{ls}-{li}", li_t, li_q) for ls, li, li_t, li_q in
                               query("SELECT radar, alcance, target, quality FROM sequences")}
        # primeiro trecho (12 slots): alvos 4..11 menos o 8 (interpolado) = 7;
        # último trecho: 1
        assert 8 == len(lset_listed)

    def test_marcas_nova_projetada_e_removidas(self, add_volume, capsys):
        # O O O O M O: as janelas que passam pelo slot 4 dependem da interpolação
        llst_ts = [MI_T0 + li_k * 600 for li_k in (0, 1, 2, 3, 5)]
        for li_ts in llst_ts:
            add_volume(li_ts, ff_rain=1.0)
        # tabela atual: a 1a janela (todos observados) e uma que não existe mais
        with db.transaction_write() as ldb:
            ldb.replace_sequences([("CGU", 250, *llst_ts[:4], MI_T0 + 4 * 600 + 1, 0, "nwc250v2"),
                                   ("CGU", 250, 1, 2, 3, 4, 5, 0, "nwc250v2")])

        ls_out = self._dry(capsys)
        lset = _listed(ls_out, "sequências a gerar")

        # alvo no slot 4 (interpolado) rejeitado; alvo no slot 5 aceito, projetado
        assert {("CGU-250", llst_ts[4], 1, "[nova] [projetada]")} == lset
        assert {("CGU-250", 5, 0, ""), ("CGU-250", MI_T0 + 4 * 600 + 1, 0, "")} == \
               _listed(ls_out, "sequências a remover")
        assert "novas 1 mantidas 0 removidas 2" in ls_out

    def test_lacuna_adiada_nao_e_projetada(self, add_volume, capsys):
        for li_k in (0, 1, 2, 3, 5):
            add_volume(MI_T0 + li_k * 600, ff_rain=1.0)
        # volume ainda não convertido dentro da lacuna
        add_volume(MI_T0 + 4 * 600, fi_status=db.CI_STATUS_NEW, fb_write_npy=False)

        ls_out = self._dry(capsys)

        assert set() == _listed(ls_out, "sequências a gerar")
        assert "0 slots interpolados" in ls_out
        assert "1 lacunas não projetadas" in ls_out

    def test_vizinho_ausente_nao_e_projetado(self, add_volume, capsys):
        for li_k in (0, 1, 2, 3):
            add_volume(MI_T0 + li_k * 600, ff_rain=1.0)
        add_volume(MI_T0 + 5 * 600, ff_rain=1.0, fb_write_npy=False)

        ls_out = self._dry(capsys)

        assert "0 slots interpolados" in ls_out
        assert "1 lacunas não projetadas" in ls_out

    def test_filtros(self, add_volume, capsys):
        _series(add_volume, 6)
        _series(add_volume, 5, fi_alcance=400)

        ls_out = self._dry(capsys, "-s", "cgu-400")
        assert {"CGU-400"} == {lt[0] for lt in _listed(ls_out, "sequências a gerar")}

        ls_out = self._dry(capsys, "-s", "CGU-250", "--from",
                           nt.epoch_to_utc(MI_T0 + 5 * 600).isoformat())
        assert {MI_T0 + 5 * 600} == {lt[1] for lt in _listed(ls_out, "sequências a gerar")}

    def test_filtros_exigem_dry_run(self, mem_db):
        with pytest.raises(SystemExit):
            bs.main(["-s", "CGU-250"])

# < the end >──────────────────────────────────────────────────────────────────────────
