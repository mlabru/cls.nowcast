# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

test_vb_blend
testes unitários de vb_blend (geração de frames reais, interpolação
entre frames e despacho de tasks)

2026.jul  mlabru  initial version (Linux/Python)
"""
# < imports >--------------------------------------------------------------------------

# python library
import time

# pytest
import pytest

# numPy
import numpy as np

# openCV
import cv2

# PIL
from PIL import Image

# model
import blend.vb_blend as bl

# < helpers >----------------------------------------------------------------------------

def make_img(fpth_dir, fs_name, ft_color, ft_shape=(10, 10, 3)):
    """
    cria uma imagem BGR sólida (via openCV) para uso nos testes
    """
    lnp_img = np.full(ft_shape, ft_color, dtype=np.uint8)
    lpth_img = fpth_dir / fs_name
    cv2.imwrite(str(lpth_img), lnp_img)

    # ok
    return lpth_img

# < interpol_animes >---------------------------------------------------------------------

def test_interpol_animes_mistura_pesos(tmp_path):
    """
    peso 0.5 deve gerar um blend aproximadamente na média das duas imagens
    """
    lpth_a = make_img(tmp_path, "a.png", (0, 0, 0))
    lpth_b = make_img(tmp_path, "b.png", (200, 200, 200))
    lpth_final = tmp_path / "final.png"

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final, "peso": 0.5}

    assert bl.interpol_animes(ldct_task) is None
    assert lpth_final.exists()

    lnp_out = cv2.imread(str(lpth_final))
    assert np.allclose(lnp_out[0, 0], [100, 100, 100], atol=2)

# -------------------------------------------------------------------------------------
def test_interpol_animes_usa_peso_default_quando_ausente(tmp_path):
    """
    sem 'peso' explícito, o blend deve usar 0.5 (default de fdct_task.get)
    """
    lpth_a = make_img(tmp_path, "a.png", (0, 0, 0))
    lpth_b = make_img(tmp_path, "b.png", (100, 100, 100))
    lpth_final = tmp_path / "final.png"

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final}

    assert bl.interpol_animes(ldct_task) is None

    lnp_out = cv2.imread(str(lpth_final))
    assert np.allclose(lnp_out[0, 0], [50, 50, 50], atol=2)

# -------------------------------------------------------------------------------------
def test_interpol_animes_redimensiona_b_para_tamanho_de_a(tmp_path):
    """
    imagens de tamanhos diferentes: B deve ser redimensionada para o
    tamanho de A antes do blend
    """
    lpth_a = make_img(tmp_path, "a.png", (10, 10, 10), ft_shape=(20, 20, 3))
    lpth_b = make_img(tmp_path, "b.png", (50, 50, 50), ft_shape=(40, 40, 3))
    lpth_final = tmp_path / "final.png"

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final}

    assert bl.interpol_animes(ldct_task) is None

    lnp_out = cv2.imread(str(lpth_final))
    assert (20, 20, 3) == lnp_out.shape

# -------------------------------------------------------------------------------------
def test_interpol_animes_falha_ao_carregar_arquivo_inexistente(tmp_path):
    """
    arquivos ausentes devem retornar mensagem de erro em vez de lançar exceção
    """
    ldct_task = {"antes": tmp_path / "nao_existe_a.png",
                 "depois": tmp_path / "nao_existe_b.png",
                 "final": tmp_path / "final.png"}

    ls_err = bl.interpol_animes(ldct_task)

    assert ls_err is not None
    assert "falha ao carregar" in ls_err
    assert not (tmp_path / "final.png").exists()

# -------------------------------------------------------------------------------------
def test_interpol_animes_erro_no_calculo_retorna_mensagem(tmp_path, monkeypatch):
    """
    falha na etapa aritmética (cv2.addWeighted/cv2.imwrite) deve ser
    capturada e reportada como string de erro, não propagada
    """
    lpth_a = make_img(tmp_path, "a.png", (0, 0, 0))
    lpth_b = make_img(tmp_path, "b.png", (100, 100, 100))
    lpth_final = tmp_path / "final.png"

    def _raise(*_a, **_kw):
        raise RuntimeError("falha simulada de opencv")

    monkeypatch.setattr(cv2, "addWeighted", _raise)

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final}

    ls_err = bl.interpol_animes(ldct_task)

    assert ls_err is not None
    assert "problema no cálculo" in ls_err
    assert not lpth_final.exists()

# < create_blend >-------------------------------------------------------------------------

def test_create_blend_sucesso_sem_espera(tmp_path, monkeypatch):
    """
    quando ambos os arquivos já existem e têm conteúdo, não deve haver
    espera e a interpolação deve ser executada
    """
    lpth_a = make_img(tmp_path, "a.png", (0, 0, 0))
    lpth_b = make_img(tmp_path, "b.png", (100, 100, 100))
    lpth_final = tmp_path / "final.png"

    def _fail_se_dormir(fi_segundos):
        raise AssertionError("create_blend não deveria dormir quando os arquivos já existem")

    monkeypatch.setattr(time, "sleep", _fail_se_dormir)

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final, "peso": 0.5}

    assert bl.create_blend(ldct_task, fi_timeout_max=5) is None
    assert lpth_final.exists()

# -------------------------------------------------------------------------------------
def test_create_blend_timeout_relata_arquivos_faltantes_ou_corrompidos(tmp_path, monkeypatch):
    """
    timeout deve relatar, separadamente, arquivo ausente ('antes') e
    arquivo corrompido de 0 bytes ('depois')
    """
    lpth_a = tmp_path / "a.png"                      # nunca criado
    lpth_b = make_img(tmp_path, "b.png", (0, 0, 0))
    lpth_b.write_bytes(b"")                          # corrompe (0 bytes)
    lpth_final = tmp_path / "final.png"

    monkeypatch.setattr(time, "sleep", lambda fi_segundos: None)

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final}

    ls_err = bl.create_blend(ldct_task, fi_timeout_max=2)

    assert ls_err is not None
    assert "FALTOU ANTES" in ls_err
    assert "CORROMPIDO (0 bytes) DEPOIS" in ls_err
    assert not lpth_final.exists()

# -------------------------------------------------------------------------------------
def test_create_blend_timeout_relata_corrompido_antes_e_faltou_depois(tmp_path, monkeypatch):
    """
    espelha o teste acima para a outra combinação de erro: 'antes'
    corrompido (0 bytes) e 'depois' totalmente ausente
    """
    lpth_a = make_img(tmp_path, "a.png", (0, 0, 0))
    lpth_a.write_bytes(b"")                          # corrompe (0 bytes)
    lpth_b = tmp_path / "b.png"                       # nunca criado
    lpth_final = tmp_path / "final.png"

    monkeypatch.setattr(time, "sleep", lambda fi_segundos: None)

    ldct_task = {"antes": lpth_a, "depois": lpth_b, "final": lpth_final}

    ls_err = bl.create_blend(ldct_task, fi_timeout_max=2)

    assert ls_err is not None
    assert "CORROMPIDO (0 bytes) ANTES" in ls_err
    assert "FALTOU DEPOIS" in ls_err
    assert not lpth_final.exists()

# < create_anime >-------------------------------------------------------------------------

class _FakeRadarOK:
    """dublê de CRadar: grava um PNG de verdade no path solicitado"""

    def __init__(self, *_a, **_kw):
        self.field = "corrected_reflectivity"

    def field_change(self, fs_field):
        self.field = fs_field

    def cappi(self, _fi_val, fs_save_path=None):
        Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(fs_save_path)

    def ppi(self, _fi_val, fs_save_path=None):
        Image.new("RGBA", (10, 10), (0, 255, 0, 255)).save(fs_save_path)

    def rhi(self, _fi_val, fs_save_path=None):
        Image.new("RGBA", (10, 10), (0, 0, 255, 255)).save(fs_save_path)

    def rhi_artificial(self, _fi_val, fs_save_path=None):
        Image.new("RGBA", (10, 10), (0, 0, 0, 255)).save(fs_save_path)


class _FakeRadarSemArquivo:
    """dublê de CRadar cujo backend de plot não grava nenhum arquivo"""

    def __init__(self, *_a, **_kw):
        self.field = "corrected_reflectivity"

    def field_change(self, fs_field):
        self.field = fs_field

    def cappi(self, _fi_val, fs_save_path=None):
        pass


def _task_base(tmp_path, **lkw_overrides):
    ldct_task = {"mvol": tmp_path / "x.mvol", "final": tmp_path / "anime_000.png",
                 "mode_ani": "CAPPI", "field_ani": "corrected_reflectivity",
                 "val_ani": 3000}
    ldct_task.update(lkw_overrides)
    return ldct_task

# -------------------------------------------------------------------------------------
def test_create_anime_gera_e_redimensiona_para_701x511(tmp_path, monkeypatch):
    """
    o PNG gerado pelo backend de plot deve ser convertido para RGB e
    redimensionado para 701x511 antes de ser persistido
    """
    monkeypatch.setattr(bl.rd, "CRadar", _FakeRadarOK)

    ldct_task = _task_base(tmp_path)
    lpth_final = ldct_task["final"]

    assert bl.create_anime(ldct_task) is None
    assert lpth_final.exists()

    with Image.open(lpth_final) as limg:
        assert (701, 511) == limg.size
        assert "RGB" == limg.mode

# -------------------------------------------------------------------------------------
@pytest.mark.parametrize("fs_mode_ani, ft_cor_esperada", [
    ("CAPPI", (255, 0, 0)),   # _FakeRadarOK.cappi
    ("PPI", (0, 255, 0)),     # _FakeRadarOK.ppi
    ("RHI-R", (0, 0, 255)),   # _FakeRadarOK.rhi
    ("RHI-A", (0, 0, 0)),     # _FakeRadarOK.rhi_artificial
])
def test_create_anime_despacha_para_o_metodo_do_modo_solicitado(
        tmp_path, monkeypatch, fs_mode_ani, ft_cor_esperada):
    """
    cada mode_ani deve chamar o método correspondente de CRadar
    (cappi/ppi/rhi/rhi_artificial); a cor sólida gravada por cada método
    do dublê comprova qual foi de fato chamado
    """
    monkeypatch.setattr(bl.rd, "CRadar", _FakeRadarOK)

    ldct_task = _task_base(tmp_path, mode_ani=fs_mode_ani)

    assert bl.create_anime(ldct_task) is None

    with Image.open(ldct_task["final"]) as limg:
        assert ft_cor_esperada == limg.getpixel((0, 0))

# -------------------------------------------------------------------------------------
def test_create_anime_troca_campo_quando_diferente_do_default(tmp_path, monkeypatch):
    """
    field_change só deve ser chamado quando o campo solicitado difere do
    campo atual do radar
    """
    llst_chamadas = []

    class _FakeRadar(_FakeRadarOK):
        def field_change(self, fs_field):
            llst_chamadas.append(fs_field)
            self.field = fs_field

    monkeypatch.setattr(bl.rd, "CRadar", _FakeRadar)

    ldct_task = _task_base(tmp_path, field_ani="velocity")

    assert bl.create_anime(ldct_task) is None
    assert ["velocity"] == llst_chamadas

# -------------------------------------------------------------------------------------
def test_create_anime_modo_invalido_retorna_erro(tmp_path, monkeypatch):
    """
    mode_ani desconhecido deve retornar mensagem de erro sem lançar exceção
    """
    monkeypatch.setattr(bl.rd, "CRadar", _FakeRadarOK)

    ldct_task = _task_base(tmp_path, mode_ani="XYZ")

    ls_err = bl.create_anime(ldct_task)
    assert ls_err is not None
    assert "Mode XYZ inválido" in ls_err

# -------------------------------------------------------------------------------------
def test_create_anime_arquivo_nao_gerado_pelo_backend(tmp_path, monkeypatch):
    """
    se o backend de plot não gravar o PNG esperado, create_anime deve
    reportar erro em vez de deixar o objeto/estado inconsistente
    """
    monkeypatch.setattr(bl.rd, "CRadar", _FakeRadarSemArquivo)

    ldct_task = _task_base(tmp_path)

    ls_err = bl.create_anime(ldct_task)
    assert ls_err is not None
    assert "não foi gerado" in ls_err

# < run_task >-----------------------------------------------------------------------------

def test_run_task_despacha_para_create_anime_quando_real(monkeypatch):
    llst_chamadas = []
    monkeypatch.setattr(bl, "create_anime", lambda fdct_task: llst_chamadas.append(fdct_task) or None)

    ldct_task = {"tipo": "REAL"}
    assert bl.run_task(ldct_task) is None
    assert [ldct_task] == llst_chamadas

# -------------------------------------------------------------------------------------
def test_run_task_despacha_para_create_blend_quando_interpolado(monkeypatch):
    monkeypatch.setattr(bl, "create_blend", lambda fdct_task: "erro-simulado")

    ldct_task = {"tipo": "INTERPOLADO"}
    assert "erro-simulado" == bl.run_task(ldct_task)

# -------------------------------------------------------------------------------------
def test_run_task_tipo_desconhecido_retorna_erro():
    ls_err = bl.run_task({"tipo": "DESCONHECIDO"})

    assert ls_err is not None
    assert ls_err.startswith("run_task:")
    assert "não reconhecida" in ls_err

# < the end >--------------------------------------------------------------------------
