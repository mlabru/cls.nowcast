# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_timeline
testes da Timeline (tline/): associação encadeada, lacunas, duplicatas e
janelas.  As cadências usadas vêm do catalog.db real (BVA 610 s, SRO 594 s,
mudança de fase do CGU-250).

2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import datetime
import time

# pytest
import pytest

# locals
import tline.cappi_record as cr
import tline.slot_status as ss
import tline.timeline as tl
import tline.tl_collection as tc
import dbase.vol_record as vr
import dbase.vol_product_key as pk

# < helpers >──────────────────────────────────────────────────────────────────────────

# origem arbitrária das séries sintéticas (2026-08-02 03:42:47 UTC)
MI_T0 = 1785642167

MS_OBS = ss.SlotStatus.OBSERVED
MS_INT = ss.SlotStatus.INTERPOLATED

# -------------------------------------------------------------------------------------
def _rec(fi_epoch: int, f_origin=MS_OBS, fs_station="BVA", fi_range=250,
         ff_valid=1.0, ff_rain=0.2) -> cr.CappiRecord:
    """
    CappiRecord sintético
    """
    lvol = vr.VolumeRecord(product=pk.ProductKey(fs_station, fi_range),
                           timestamp=datetime.datetime.fromtimestamp(fi_epoch,
                                                                     tz=datetime.timezone.utc),
                           filename=f"/raw/{fs_station}-{fi_range}-{fi_epoch}.mvol")

    return cr.CappiRecord(volume=lvol, cappi_path=f"/cappi/{fi_epoch}.npy", origin=f_origin,
                          valid_fraction=ff_valid, rain_fraction=ff_rain)

# -------------------------------------------------------------------------------------
def _series(*fi_deltas, f_origins=None) -> list:
    """
    records a partir de MI_T0 com os Δt dados (s) entre consecutivos
    """
    llst_epochs = [MI_T0]

    for li_delta in fi_deltas:
        llst_epochs.append(llst_epochs[-1] + li_delta)

    llst_origins = f_origins or [MS_OBS] * len(llst_epochs)

    return [_rec(li_e, lo) for li_e, lo in zip(llst_epochs, llst_origins)]

# -------------------------------------------------------------------------------------
def _statuses(f_timeline) -> str:
    """
    status dos slots como string: O I P M X
    """
    ldct = {ss.SlotStatus.OBSERVED: "O", ss.SlotStatus.INTERPOLATED: "I",
            ss.SlotStatus.PREDICTED: "P", ss.SlotStatus.MISSING: "M",
            ss.SlotStatus.INVALID: "X"}

    return "".join(ldct[l_slot.status] for l_slot in f_timeline)

# < associação >───────────────────────────────────────────────────────────────────────

class TestAssociacao:

    def test_serie_regular(self):
        ltl = tl.Timeline.from_records(_series(600, 600, 600))
        assert "OOOO" == _statuses(ltl)
        ltl.validate()

    def test_jitter_real_nao_cria_lacunas(self):
        # CGU-400: Δt entre 597 e 604 s
        ltl = tl.Timeline.from_records(_series(597, 604, 599, 601, 603, 598))
        assert "O" * 7 == _statuses(ltl)

    def test_deriva_bva_nao_acumula(self):
        # BVA: 610 s por volume; com a grade fixa T0 ± 60 s o 7º volume já
        # ficava órfão.  100 volumes = ~17 min de deriva acumulada.
        ltl = tl.Timeline.from_records(_series(*[610] * 99))
        assert "O" * 100 == _statuses(ltl)
        assert [] == ltl.duplicates

    def test_deriva_sro_negativa(self):
        ltl = tl.Timeline.from_records(_series(*[594] * 99))
        assert "O" * 100 == _statuses(ltl)

    def test_mudanca_de_fase_cgu_250(self):
        # :x0:23 -> :x0:02 depois de uma lacuna de 2 h (7800 - 21 s)
        ltl = tl.Timeline.from_records(_series(600, 7779, 600))
        assert "OO" + "M" * 12 + "OO" == _statuses(ltl)
        ltl.validate()

    def test_lacuna_com_horarios_estimados_entre_os_reais(self):
        # dois faltantes entre volumes separados por 1830 s (3 × 610)
        ltl = tl.Timeline.from_records(_series(1830))
        assert "OMMO" == _statuses(ltl)
        assert [MI_T0 + 610, MI_T0 + 1220] == [ltl[1].epoch, ltl[2].epoch]
        assert (2, 1) == (ltl[1].gap_length, ltl[1].gap_index)
        assert (2, 2) == (ltl[2].gap_length, ltl[2].gap_index)

    def test_meio_intervalo_arredonda_para_cima(self):
        # 900 s = 1,5 intervalo: round() do Python daria 2 aqui mas 0 em 300 s
        # (round(0.5) == 0); o half-up dá 2 slots de forma consistente
        ltl = tl.Timeline.from_records(_series(900))
        assert "OMO" == _statuses(ltl)

    def test_tolerancia_estrita_quebra_a_serie(self):
        ltl = tl.Timeline.from_records(_series(600, 750, 600),
                                       f_tolerance=datetime.timedelta(seconds=60))
        assert "OOXOO" == _statuses(ltl)
        # a quebra não é lacuna interpolável e interrompe janelas
        assert [] == ltl.interpolatable_gaps()
        assert [] == list(ltl.windows(4))

    def test_ordem_de_entrada_irrelevante(self):
        llst = _series(600, 1200, 600)
        ltl_a = tl.Timeline.from_records(llst)
        ltl_b = tl.Timeline.from_records(reversed(llst))
        assert [s.epoch for s in ltl_a] == [s.epoch for s in ltl_b]

    def test_interpolado_do_banco_vira_interpolated(self):
        ltl = tl.Timeline.from_records(_series(600, 600, f_origins=[MS_OBS, MS_INT, MS_OBS]))
        assert "OIO" == _statuses(ltl)
        ltl.validate()

    def test_on_linear(self):
        # um ano de volumes a cada 10 min (~52 mil): a versão O(N²) levava
        # horas; o limite é folgado para não ser frágil em máquina lenta
        llst = _series(*[600] * 52_559)
        lf_t0 = time.perf_counter()
        ltl = tl.Timeline.from_records(llst)
        assert time.perf_counter() - lf_t0 < 10.0
        assert 52_560 == len(ltl)

# < duplicatas >───────────────────────────────────────────────────────────────────────

class TestDuplicatas:

    def test_observado_atrasado_substitui_interpolado(self):
        # interpolado em T0+600; o observado real chega em T0+607
        llst = [_rec(MI_T0), _rec(MI_T0 + 600, MS_INT), _rec(MI_T0 + 607), _rec(MI_T0 + 1200)]
        ltl = tl.Timeline.from_records(llst)
        assert "OOO" == _statuses(ltl)
        assert MI_T0 + 607 == ltl[1].epoch
        assert [MI_T0 + 600] == [lr.epoch for lr in ltl.superseded]
        ltl.validate()

    def test_interpolado_depois_do_observado_tambem_e_substituido(self):
        llst = [_rec(MI_T0), _rec(MI_T0 + 598), _rec(MI_T0 + 600, MS_INT), _rec(MI_T0 + 1200)]
        ltl = tl.Timeline.from_records(llst)
        assert "OOO" == _statuses(ltl)
        assert MI_T0 + 598 == ltl[1].epoch
        assert [MI_T0 + 600] == [lr.epoch for lr in ltl.superseded]

    def test_dois_observados_no_mesmo_slot(self):
        ltl = tl.Timeline.from_records([_rec(MI_T0), _rec(MI_T0 + 100), _rec(MI_T0 + 600)])
        assert "OO" == _statuses(ltl)
        assert [MI_T0 + 100] == [lr.epoch for lr in ltl.duplicates]
        assert [] == ltl.superseded

# < lacunas interpoláveis >────────────────────────────────────────────────────────────

class TestLacunas:

    def test_lacunas_ate_max_gap(self):
        # O M O M M O M M M O
        ltl = tl.Timeline.from_records(_series(1200, 1800, 2400))
        llst_gaps = ltl.interpolatable_gaps()
        assert [1, 2] == [len(lg.missing) for lg in llst_gaps]

    def test_max_gap_configuravel(self):
        ltl = tl.Timeline.from_records(_series(1200, 1800), fi_max_interpolated_gap=1)
        assert [1] == [len(lg.missing) for lg in ltl.interpolatable_gaps()]

    def test_alpha_pelos_horarios_reais(self):
        ltl = tl.Timeline.from_records(_series(1830))
        (lgap,) = ltl.interpolatable_gaps()
        assert [pytest.approx(1 / 3), pytest.approx(2 / 3)] == [lgap.alpha(ls) for ls in lgap.missing]

    def test_interpolado_nao_serve_de_ancora(self):
        # O I M O: o faltante tem um interpolado à esquerda
        ltl = tl.Timeline.from_records([_rec(MI_T0), _rec(MI_T0 + 600, MS_INT),
                                        _rec(MI_T0 + 1800)])
        assert "OIMO" == _statuses(ltl)
        assert [] == ltl.interpolatable_gaps()

# < janelas >──────────────────────────────────────────────────────────────────────────

class TestJanelas:

    def test_janelas_deslizantes(self):
        ltl = tl.Timeline.from_records(_series(*[600] * 6))
        assert 3 == len(list(ltl.windows(5)))

    def test_lacuna_interrompe(self):
        # O O O M O O O O O
        ltl = tl.Timeline.from_records(_series(600, 600, 1200, 600, 600, 600, 600))
        llst = list(ltl.windows(5))
        assert 1 == len(llst)
        assert all(ls.is_filled for ls in llst[0])

    def test_interpolados_contam_como_preenchidos(self):
        ltl = tl.Timeline.from_records(_series(600, 600, 600, 600,
                                               f_origins=[MS_OBS, MS_INT, MS_OBS, MS_INT, MS_OBS]))
        assert 1 == len(list(ltl.windows(5)))

# < motivos e tabela >─────────────────────────────────────────────────────────────────

class TestMotivosETabela:

    def test_motivo_de_cada_lacuna(self):
        # O M O MMM O I M O
        llst = [_rec(MI_T0), _rec(MI_T0 + 1200), _rec(MI_T0 + 3600),
                _rec(MI_T0 + 4200, MS_INT), _rec(MI_T0 + 5400)]
        ltl = tl.Timeline.from_records(llst)
        assert [tl.CS_GAP_INTERPOLATABLE, tl.CS_GAP_LONG, tl.CS_GAP_NO_ANCHOR] == \
               [lg.reason for lg in ltl.gaps()]
        assert 1 == len(ltl.interpolatable_gaps())

    def test_tabela_compacta_lacuna_longa_numa_linha(self):
        ltl = tl.Timeline.from_records(_series(600, 6001 * 600, 600))
        ls_tab = ltl.table()
        assert 2 + 5 == len(ls_tab.splitlines())
        assert "×6000 [longa]" in ls_tab
        # sem compactar: um slot por linha
        assert 2 + 6004 == len(ltl.table(fb_compact=False).splitlines())

    def test_tabela_filtrada_por_intervalo(self):
        ltl = tl.Timeline.from_records(_series(*[600] * 9))
        ldt = ltl[3].timestamp
        assert 2 + 3 == len(ltl.table(fdt_from=ldt, fdt_to=ldt + datetime.timedelta(minutes=20)).splitlines())

# < validação e coleção >──────────────────────────────────────────────────────────────

class TestValidacao:

    def test_produtos_diferentes(self):
        with pytest.raises(ValueError):
            tl.Timeline.from_records([_rec(MI_T0), _rec(MI_T0 + 600, fs_station="CGU")])

    def test_sem_registros(self):
        with pytest.raises(ValueError):
            tl.Timeline.from_records([])

    def test_tolerancia_acima_de_meio_intervalo(self):
        with pytest.raises(ValueError):
            tl.Timeline(product=pk.ProductKey("BVA", 250),
                        tolerance=datetime.timedelta(seconds=301))

    def test_origem_invalida_no_record(self):
        with pytest.raises(ValueError):
            _rec(MI_T0, ss.SlotStatus.MISSING)

    def test_colecao_agrupa_por_produto(self):
        llst = [_rec(MI_T0), _rec(MI_T0 + 600),
                _rec(MI_T0 + 5, fs_station="CGU"), _rec(MI_T0 + 5, fs_station="CGU", fi_range=400)]
        lcol = tc.TimelineCollection.from_records(llst)
        assert ["BVA-250", "CGU-250", "CGU-400"] == [str(lp) for lp in lcol.products]
        assert 2 == len(lcol[pk.ProductKey("BVA", 250)])
        assert "BVA-250" in lcol.summary()

# < the end >──────────────────────────────────────────────────────────────────────────
