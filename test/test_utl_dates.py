# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_utl_dates
testes unitários para utl_dates.py — sem dependências externas.
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
import datetime

# pytest
import pytest

# locals
import utl_dates as ud

# < TestTimestampToDatetime >──────────────────────────────────────────────────────────

class TestTimestampToDatetime:
    """
    timestamp_to_datetime: converte Unix timestamp para datetime com timezone UTC.
    """

    def test_resultado_tem_timezone_utc(self):
        ldt = ud.timestamp_to_datetime(0)
        assert ldt.tzinfo == datetime.timezone.utc

    def test_epoch_zero(self):
        ldt = ud.timestamp_to_datetime(0)
        assert ldt == datetime.datetime(1970, 1, 1, 0, 0, 0,
                                        tzinfo=datetime.timezone.utc)

    def test_valor_conhecido(self):
        # 2026-06-15 12:00:00 UTC
        li_ts  = int(datetime.datetime(2026, 6, 15, 12, 0, 0,
                                       tzinfo=datetime.timezone.utc).timestamp())
        ldt    = ud.timestamp_to_datetime(li_ts)
        assert ldt.year   == 2026
        assert ldt.month  == 6
        assert ldt.day    == 15
        assert ldt.hour   == 12
        assert ldt.minute == 0

# < TestDatetimeToTimestamp >──────────────────────────────────────────────────────────

class TestDatetimeToTimestamp:
    """
    datetime_to_timestamp: converte datetime para int.
    """

    def test_resultado_e_int(self):
        ldt = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        assert isinstance(ud.datetime_to_timestamp(ldt), int)

    def test_epoch_zero(self):
        ldt = datetime.datetime(1970, 1, 1, 0, 0, 0,
                                tzinfo=datetime.timezone.utc)
        assert ud.datetime_to_timestamp(ldt) == 0

# < TestRoundtrip >────────────────────────────────────────────────────────────────────

class TestRoundtrip:
    """
    roundtrip: datetime → timestamp → datetime deve ser idempotente.
    """

    @pytest.mark.parametrize("year, month, day, hour, minute", [
        (2026,  1,  1,  0,  0),
        (2026,  6, 15, 12,  0),
        (2025, 12, 31, 23, 59),
        (2000,  2, 29, 12,  0),   # ano bissexto
    ])

    def test_roundtrip_datetime_ts_datetime(self, year, month, day, hour, minute):
        ldt_original = datetime.datetime(year, month, day, hour, minute,
                                         tzinfo=datetime.timezone.utc)
        li_ts        = ud.datetime_to_timestamp(ldt_original)
        ldt_volta    = ud.timestamp_to_datetime(li_ts)
        assert ldt_volta == ldt_original

    def test_roundtrip_ts_datetime_ts(self):
        li_ts_original = int(datetime.datetime(2026, 6, 15, 10, 0, 0,
                                               tzinfo=datetime.timezone.utc).timestamp())
        ldt = ud.timestamp_to_datetime(li_ts_original)
        assert ud.datetime_to_timestamp(ldt) == li_ts_original

# < TestGetDateFromMvol >──────────────────────────────────────────────────────────────

class TestGetDateFromMvol:
    """
    get_date_from_mvol: extrai datetime do nome do arquivo .mvol.
    """

    @pytest.mark.parametrize("fname, esperado", [
        ("CGU-250--2025-12-21--02-30-23.mvol", datetime.datetime(2025, 12, 21,  2, 30, 23)),
        ("SJP-500--2026-06-15--12-00-00.mvol", datetime.datetime(2026,  6, 15, 12,  0,  0)),
        ("ABC-100--2000-01-01--00-00-00.mvol", datetime.datetime(2000,  1,  1,  0,  0,  0)),
    ])

    def test_nomes_validos(self, fname, esperado):
        ldt = ud.get_date_from_mvol(fname)
        assert ldt == esperado

    def test_apenas_nome_sem_caminho(self):
        """
        deve aceitar apenas o nome do arquivo, sem diretório
        """
        ldt = ud.get_date_from_mvol("CGU-250--2026-01-01--10-00-00.mvol")
        assert ldt is not None

    def test_caminho_completo_e_aceito(self):
        """
        deve aceitar path completo e extrair apenas o nome
        """
        ldt = ud.get_date_from_mvol("/dados/radar/CGU-250--2026-01-01--10-00-00.mvol")
        assert ldt is not None
        assert ldt == datetime.datetime(2026, 1, 1, 10, 0, 0)

    @pytest.mark.parametrize("nome_invalido", [
        "arquivo_sem_padrao.mvol",
        "",
        "CGU-250.mvol",
        "nao_tem_extensao",
        "CGU-250--data-errada--hora.mvol",
    ])
    
    def test_nomes_invalidos_retornam_none(self, nome_invalido):
        assert ud.get_date_from_mvol(nome_invalido) is None

# < the end >──────────────────────────────────────────────────────────────────────────
