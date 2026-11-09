# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

cp_core
núcleo comum de geração de CAPPI: leitura do .mvol, gridding (único ponto de
chamada a pyart.map.grid_from_radars), extração de nível e estatísticas.

sem matplotlib -- a apresentação dos .npy do nowcast fica em
27.nowcast/.../nwcCapGen.py.

fluxo típico (é o que o nwcConvert faz):
    l_radar = read_radar(mvol, cfg.reader, cfg.field_name)
    ls_cov  = add_coverage_field(l_radar)                 # campo sintético
    l_grid  = grid_radar(l_radar, cfg, (ls_cov,))         # Py-ART gridding
    lnp     = extract_level_coverage(l_grid, cfg.field_name, cfg.z_min_m, -32)

os presets CFG_VIEWER e CFG_RAINFLOW reproduzem os parâmetros dos
consumidores antigos (a GUI do 21.mvol e o gerador RainFlow); hoje só os
testes do 21.mvol os usam.  ATENÇÃO: este arquivo é compartilhado com o
21.mvol (symlink apps/cappi): rode a suíte de lá ao alterá-lo, e não
renomeie os imports pyart/gr/pa (os testes os substituem com mock).

os presets dos .npy do nowcast ficam no próprio nowcast
(27.nowcast/.../work/nwc_cappi_cfg.py), que importa este módulo direto (o
diretório é do nowcast; o 21.mvol o vê pelo symlink apps/cappi).

as divergências entre os presets (Barnes x Barnes2, ROI constante x
dist_beam, altitude MSL x acima do radar, extensão/resolução) são herdadas
dos três caminhos originais -- convergi-las é uma decisão separada, que muda
as saídas.

2026.out  mlabru  limpeza para legibilidade: traços de debug ">> função" e o
                  setLevel do módulo removidos (quem configura o nível é a
                  aplicação), logs com % no lugar de f-string, comentários de
                  ruído removidos; nenhuma mudança de comportamento
2026.set  mlabru  CFG_NOWCAST removido: a conversão (volProcess.py) foi para o
                  25.nowcast, com presets próprios (nwc_cappi_cfg)
2026.set  mlabru  cobertura x eco: add_coverage_field, grid_radar(ft_extra_fields)
                  e extract_level_coverage (-9999 fora da cobertura, um valor
                  de "sem eco" dentro dela, escolhido pelo consumidor)
2026.set  mlabru  extract_level(fb_nan=False) preenche a máscara com
                  MF_NODATA (-9999) e devolve float32: antes o np.asarray
                  descartava a máscara e expunha o valor guardado por baixo
                  dela (0.0 no Py-ART 2.2.0), então nenhum .npy tinha -9999,
                  "sem cobertura" virava 0 dBZ e valid_fraction era sempre 1.0
2026.set  mlabru  CFG_BLEND -> CFG_VIEWER (o blend/GIF foi removido; só a GUI
                  usa o preset); CFG_NOWCAST marcado como transitório (a
                  conversão vai para o 25.nowcast).  Este módulo e radar/
                  passam a ser importados pelo 25.nowcast via symlink de
                  diretório
2026.set  mlabru  initial version (Linux/Python) -- extraído de
                  rd_radar.generate_grid/_load_radar, cappi.py e
                  nc_process_volume.py
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import dataclasses
import logging
import pathlib

# numPy
import numpy as np

# ARM Py-ART
import pyart

# radar
import radar.rd_gamic_reader as gr
import radar.rd_pyart_adapter as pa

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# < constantes >─────────────────────────────────────────────────────────────────────

# leitores de .mvol suportados por read_radar()
#   "gamic": GamicReader próprio (h5py -> RadarVolume -> to_pyart); só um
#            campo, sem gamic_date/meters_between_gates/instrument_parameters
#   "pyart": pyart.aux_io.read_gamic, com fallback para pyart.io.read
MT_READERS = ("gamic", "pyart")

# modos de cálculo da extensão horizontal (±extent em X e Y)
#   "fixed":     usa CappiConfig.extent_m
#   "last_gate": último gate do radar (radar.range["data"][-1])
#   "nominal":   250 km se o alcance < 300 km, senão 400 km
MT_EXTENT_MODES = ("fixed", "last_gate", "nominal")

# valor gravado nos pontos sem dado por extract_level(fb_nan=False) e
# extract_level_coverage (nwcCapGen.CF_NODATA_VALUE e os consumidores do .npy
# do nowcast)
MF_NODATA = -9999.0

# campo sintético de cobertura (add_coverage_field): 1 em todo gate, sem
# máscara.  Gridado junto com a refletividade, marca onde o feixe alcança
# o nível -- a refletividade sozinha não distingue "fora do alcance" de
# "sem eco", porque o Py-ART mascara também os gates abaixo do limiar
CS_COVERAGE_FIELD = "cappi_coverage"

# < CappiConfig >----------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CappiConfig:
    """
    parâmetros de geração do CAPPI (leitura + gridding).

    os opcionais de gridding (gridding_algo, roi_func, min_radius,
    constant_roi) só são repassados a grid_from_radars quando não são None,
    para que cada preset gere exatamente os mesmos kwargs do código original
    """
    # leitor do .mvol (ver MT_READERS)
    reader: str = "gamic"
    # campo a interpolar
    field_name: str = "DBZH"

    # eixo Z: de z_min_m a z_max_m em n_levels níveis
    z_min_m: float = 3000.0
    z_max_m: float = 3000.0
    n_levels: int = 1
    # "radar": altitudes relativas ao radar (origem do grid)
    # "msl":   altitudes sobre o nível do mar (subtrai a altitude do radar)
    altitude_ref: str = "radar"

    # extensão horizontal (ver MT_EXTENT_MODES)
    extent_mode: str = "nominal"
    extent_m: float | None = None

    # pontos em (Y, X); se None, calculado a partir de resolution_m
    shape_xy: tuple[int, int] | None = None
    # resolução horizontal (m), usada quando shape_xy é None
    resolution_m: float | None = 1000.0

    # função de peso
    weighting_function: str = "Barnes"
    # opcionais (None = não repassa, usa o default do Py-ART)
    gridding_algo: str | None = None
    roi_func: str | None = None
    min_radius: float | None = None
    constant_roi: float | None = None

    # usa (lat, lon) do radar como grid_origin
    use_radar_origin: bool = False

    # ---------------------------------------------------------------------------------
    def __post_init__(self) -> None:
        """
        valida os parâmetros
        """

        if self.reader not in MT_READERS:
            raise ValueError(f"reader inválido: {self.reader!r} (use {MT_READERS})")

        if self.extent_mode not in MT_EXTENT_MODES:
            raise ValueError(f"extent_mode inválido: {self.extent_mode!r} (use {MT_EXTENT_MODES})")

        if "fixed" == self.extent_mode and self.extent_m is None:
            raise ValueError("extent_mode 'fixed' exige extent_m")

        if self.altitude_ref not in ("radar", "msl"):
            raise ValueError(f"altitude_ref inválido: {self.altitude_ref!r}")

        if self.shape_xy is None and not self.resolution_m:
            raise ValueError("informe shape_xy ou resolution_m")

        if self.n_levels < 1:
            raise ValueError(f"n_levels deve ser >= 1, recebido {self.n_levels}")

# < presets >--------------------------------------------------------------------------

# CRadar (rd_radar.py): GUI (volViewer).
#
# - 50 níveis entre 3000 m e 15000 m MSL (z_min_m é sobrescrito por
#   CRadar.cappi via dataclasses.replace).  50 níveis é um compromisso entre
#   resolução vertical do CAPPI e custo de gridding (cada nível a mais soma
#   ~250k pontos a interpolar via Barnes2); 500×500 horizontal casa com o
#   range de radares de longo alcance (240 km) sem downsampling perceptível.
#
# - min_radius (m) é o parâmetro mais importante para ajustar.  O default de
#   250 m é inadequado para radar de precipitação de longo alcance (240 km):
#   o próprio tutorial oficial do Py-ART demonstra que aumentar o min_radius
#   para 1750 m elimina os artefatos de "penas" no topo do grid causados
#   pela projeção polar→cartesiana. Regra prática: min_radius ≈ resolução
#   azimutal em metros na borda do grid.  Para feixe de 2° e 240 km:
#   240 000 m × tan(2° / 2) ≈ 4 200 m na borda; 1 750 m é o compromisso típico.
#
# - Barnes2 é a implementação correta de Barnes (Pauley & Wu 1990) e produz
#   interpolação suave; "Nearest" é muito mais rápido porém introduz
#   artefatos em blocos.  Para CAPPI destinado a interpolação óptica Barnes2
#   é preferível pois produz campos mais contínuos.
#
# - gridding_algo/roi_func não são repassados (None): o código original
#   calculava "map_gates_to_grid"/"dist_beam" mas nunca os passava; como são
#   os defaults do Py-ART, o resultado é o mesmo.  dist_beam faz o raio de
#   influência crescer com a distância ao radar, simulando o alargamento
#   físico do feixe.
#
# - grid_origin = posição do radar
CFG_VIEWER = CappiConfig(reader="pyart",
                         field_name="corrected_reflectivity",
                         z_min_m=3000.0,
                         z_max_m=15000.0,
                         n_levels=50,
                         altitude_ref="msl",
                         extent_mode="last_gate",
                         shape_xy=(500, 500),
                         resolution_m=None,
                         weighting_function="Barnes2",
                         min_radius=1750.0,
                         use_radar_origin=True)

# RainFlow (cappi.py / PyArtCappiGenerator): GamicReader próprio, 1 nível a
# 3000 m, extensão nominal (250/400 km), 1 km de resolução, ROI constante
CFG_RAINFLOW = CappiConfig(reader="gamic",
                           field_name="DBZH",
                           z_min_m=3000.0,
                           z_max_m=3000.0,
                           n_levels=1,
                           altitude_ref="radar",
                           extent_mode="nominal",
                           resolution_m=1000.0,
                           weighting_function="Barnes",
                           roi_func="constant",
                           constant_roi=2000.0)

# -------------------------------------------------------------------------------------
def add_coverage_field(fradar: pyart.core.Radar) -> str:
    """
    acrescenta ao radar o campo CS_COVERAGE_FIELD (1 em todo gate, sem
    máscara), para ser gridado junto com a refletividade (grid_radar,
    ft_extra_fields) e separar cobertura de eco (extract_level_coverage)

    :returns: nome do campo
    """

    fradar.add_field(CS_COVERAGE_FIELD,
                     {"data": np.ma.array(np.ones((fradar.nrays, fradar.ngates), dtype=np.float32),
                                          mask=False),
                      "_FillValue": MF_NODATA},
                     replace_existing=True)

    return CS_COVERAGE_FIELD

# -------------------------------------------------------------------------------------
def compute_statistics(fnp_cappi: np.ndarray) -> tuple[float, float]:
    """
    calcula estatísticas simples do CAPPI para armazenamento no banco.

    :param fnp_cappi: array numpy com os valores de refletividade

    :returns: rain_fraction:  fração de pixels com refletividade > 0
              valid_fraction: fração de pixels com valor finito (não-NaN/inf)
    """

    # máscara de valores válidos (finitos)
    lnp_valid = np.isfinite(fnp_cappi)
    lv_valid_fraction = lnp_valid.mean()

    # máscara de pixels com chuva (válido e positivo)
    lnp_rain = np.logical_and(lnp_valid, fnp_cappi > 0.0)
    lv_rain_fraction = lnp_rain.mean()

    return lv_rain_fraction, lv_valid_fraction

# -------------------------------------------------------------------------------------
def extract_level_coverage(fgrid_art: pyart.core.Grid, fs_field: str, ff_alt_m: float,
                           ff_no_echo: float) -> np.ndarray:
    """
    nível horizontal com três estados, usando o campo CS_COVERAGE_FIELD
    gridado junto (add_coverage_field + grid_radar(ft_extra_fields)):

      - fora da cobertura (coverage mascarado)       -> MF_NODATA (-9999)
      - na cobertura, refletividade mascarada        -> ff_no_echo
      - na cobertura, com refletividade              -> o valor

    :param ff_no_echo: valor de "coberto, sem eco" (decisão do consumidor)

    :returns: float32 2D (Y, X)
    """

    # índice do nível (o mesmo para os dois campos: mesma grade)
    li_level = resolve_level(fgrid_art, ff_alt_m)

    l_refl = np.ma.asarray(fgrid_art.fields[fs_field]["data"])[li_level]
    l_cov = np.ma.asarray(fgrid_art.fields[CS_COVERAGE_FIELD]["data"])[li_level]

    lnp_covered = ~np.ma.getmaskarray(l_cov)

    # dentro da cobertura: eco ou "sem eco"; fora: sem dado
    lnp_out = np.where(lnp_covered, np.ma.filled(l_refl, ff_no_echo), MF_NODATA)

    return np.asarray(lnp_out, dtype=np.float32)

# -------------------------------------------------------------------------------------
def extract_level(fgrid_art: pyart.core.Grid, fs_field: str, ff_alt_m: float,
                  fb_nan: bool = True) -> np.ndarray:
    """
    extrai o nível horizontal mais próximo de ff_alt_m do grid.

    :param fgrid_art: grid Py-ART (pyart.core.Grid)
    :param fs_field: campo de interesse
    :param ff_alt_m: altitude desejada, no mesmo referencial do eixo Z do
                     grid (relativo à origem/radar)
    :param fb_nan: True  -> float32 com pontos mascarados convertidos em NaN
                           (comportamento de cappi.py);
                   False -> float32 com pontos mascarados preenchidos com
                           MF_NODATA (-9999)

    :returns: array 2D (Y, X)
    """

    # índice do nível
    li_level = resolve_level(fgrid_art, ff_alt_m)

    # dados do campo
    l_data = fgrid_art.fields[fs_field]["data"]

    # nível como masked array
    l_cappi = np.ma.asarray(l_data)[li_level]

    # preenche a máscara explicitamente: o valor guardado por baixo dela não
    # é o _FillValue do campo (no Py-ART 2.2.0 o grid guarda 0.0), e um
    # np.asarray direto o expunha como se fosse eco de 0 dBZ
    l_cappi = np.ma.filled(l_cappi, np.nan if fb_nan else MF_NODATA)

    return np.asarray(l_cappi, dtype=np.float32)

# -------------------------------------------------------------------------------------
def grid_radar(fradar: pyart.core.Radar, fcfg: CappiConfig,
               ft_extra_fields: tuple = ()) -> pyart.core.Grid:
    """
    interpola o volume polar do radar em uma grade cartesiana, conforme
    fcfg -- único ponto de chamada a pyart.map.grid_from_radars.

    :param fradar: pyart.core.Radar
    :param fcfg: parâmetros de gridding (ver presets CFG_*)
    :param ft_extra_fields: campos a gridar junto com fcfg.field_name, na
                            mesma chamada e com os mesmos parâmetros (ex.:
                            CS_COVERAGE_FIELD)

    :returns: pyart.core.Grid com o campo fcfg.field_name (e os extras)
              interpolado
    """

    # extensão horizontal
    lf_extent = _resolve_extent(fradar, fcfg)

    # pontos em Y, X
    if fcfg.shape_xy is not None:
        lt_shape_xy = tuple(fcfg.shape_xy)

    else:
        li_n = int(round(2.0 * lf_extent / fcfg.resolution_m)) + 1
        lt_shape_xy = (li_n, li_n)

    # eixo Z (relativo à origem do grid, que fica na altitude do radar)
    lf_z_offset = float(fradar.altitude["data"][0]) if "msl" == fcfg.altitude_ref else 0.0
    lt_z_limits = (fcfg.z_min_m - lf_z_offset, fcfg.z_max_m - lf_z_offset)

    # limites horizontais simétricos ao redor do radar
    lt_xy_limits = (-lf_extent, lf_extent)

    # argumentos obrigatórios
    ldct_kwargs = {"grid_shape": (fcfg.n_levels,) + lt_shape_xy,
                   "grid_limits": (lt_z_limits, lt_xy_limits, lt_xy_limits),
                   "fields": [fcfg.field_name, *ft_extra_fields],
                   "weighting_function": fcfg.weighting_function}

    # origem do grid na posição do radar (quando há um único radar e
    # grid_origin não é fornecido, o Py-ART usa a própria posição do radar
    # como origem, pulando a transformação de projeção)
    if fcfg.use_radar_origin:
        ldct_kwargs["grid_origin"] = (fradar.latitude["data"][0],
                                      fradar.longitude["data"][0])

    # opcionais: só repassa o que o preset define
    for ls_key in ("gridding_algo", "roi_func", "min_radius", "constant_roi"):
        lv_value = getattr(fcfg, ls_key)

        if lv_value is not None:
            ldct_kwargs[ls_key] = lv_value

    M_LOG.info("[+] gridding: shape=%s  z=%s  extent=±%.0f km  campo=%s",
               ldct_kwargs["grid_shape"], lt_z_limits, lf_extent / 1000, fcfg.field_name)

    # gera a grade
    return pyart.map.grid_from_radars((fradar,), **ldct_kwargs)

# -------------------------------------------------------------------------------------
def read_radar(fpth_file: str | pathlib.Path, fs_reader: str = "gamic",
               fs_field_name: str = "DBZH") -> pyart.core.Radar:
    """
    lê um volume GAMIC HDF5 (.mvol) e retorna um pyart.core.Radar.

    :param fpth_file: caminho do arquivo .mvol
    :param fs_reader: "gamic" (GamicReader próprio -> to_pyart; só o campo
                      fs_field_name) ou "pyart" (read_gamic, com fallback
                      para o leitor genérico pyart.io.read)
    :param fs_field_name: campo lido pelo leitor "gamic" (ignorado por "pyart",
                          que lê todos os campos do arquivo)

    :raises ValueError: se fs_reader for inválido
    :raises RuntimeError: se nenhum leitor Py-ART conseguir abrir o arquivo
    """

    # converte para Path
    fpth_file = pathlib.Path(fpth_file)

    if "gamic" == fs_reader:
        # leitor próprio: h5py -> RadarVolume -> pyart.core.Radar
        l_volume = gr.GamicReader(fpth_file, quantity=fs_field_name).read()
        return pa.to_pyart(l_volume, fs_field_name=fs_field_name)

    if "pyart" != fs_reader:
        raise ValueError(f"reader inválido: {fs_reader!r} (use {MT_READERS})")

    try:
        # leitor otimizado para GAMIC HDF5
        l_radar = pyart.aux_io.read_gamic(str(fpth_file))
        M_LOG.debug("[+] arquivo lido via read_gamic: %s", fpth_file.name)
        return l_radar

    # em caso de erro,...
    except Exception as l_err:
        M_LOG.warning("[!] read_gamic falhou (%s); tentando leitor genérico...", l_err)

    try:
        # leitor genérico — detecta o formato automaticamente
        l_radar = pyart.io.read(str(fpth_file))
        M_LOG.debug("[+] arquivo lido via pyart.io.read")
        return l_radar

    # em caso de erro,...
    except Exception as l_err2:
        # monta a mensagem
        ls_msg = (f"não foi possível ler '{fpth_file.name}' "
                  f"com nenhum leitor Py-ART: {l_err2}")
        M_LOG.error("[x] %s", ls_msg)
        raise RuntimeError(ls_msg) from l_err2

# -------------------------------------------------------------------------------------
def resolve_level(fgrid_art: pyart.core.Grid, ff_alt_m: float) -> int:
    """
    localiza o índice Z no grid Py-ART mais próximo da altitude solicitada.

    :param fgrid_art: grid Py-ART (pyart.core.Grid)
    :param ff_alt_m: altitude desejada em metros

    :returns: índice Z válido (clampado a [0, nz - 1])
    """
    # coordenadas Z reais do grid (metros, relativas à origem do grid)
    lnp_z = fgrid_art.z["data"]

    # índice do nível com menor distância à altitude solicitada
    li_level = int(np.argmin(np.abs(lnp_z - ff_alt_m)))

    # garante que o índice está dentro dos limites (defesa extra)
    return int(np.clip(li_level, 0, len(lnp_z) - 1))

# -------------------------------------------------------------------------------------
def save_cappi(fnp_cappi: np.ndarray, fpth_filename: pathlib.Path):
    """
    salva o CAPPI em formato NumPy (.npy) como float32.
    raise IOError se o arquivo não for criado corretamente.
    """
    M_LOG.info("salvando %s", fpth_filename.name)

    # salva como float32 para economizar espaço em disco
    np.save(fpth_filename, fnp_cappi.astype(np.float32))

    # verifica se o arquivo foi gravado corretamente
    if not fpth_filename.exists():
        raise IOError(f"falha ao gravar {fpth_filename}")

# -------------------------------------------------------------------------------------
def _resolve_extent(fradar: pyart.core.Radar, fcfg: CappiConfig) -> float:
    """
    calcula a extensão horizontal (±m) conforme fcfg.extent_mode
    """

    if "fixed" == fcfg.extent_mode:
        return float(fcfg.extent_m)

    # último gate = alcance máximo em metros
    lf_last_gate = float(fradar.range["data"][-1])

    if "last_gate" == fcfg.extent_mode:
        return lf_last_gate

    # nominal: extensão do produto baseada no alcance nominal do radar
    return 250_000.0 if lf_last_gate < 300_000 else 400_000.0

# < the end >──────────────────────────────────────────────────────────────────────────
