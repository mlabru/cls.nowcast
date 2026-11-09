# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwcCapGen
gera e visualiza a imagem (.png) de um CAPPI (.npy) de cappi_products.
Ferramenta de inspeção manual: não faz parte do cron, não lê o config.ini.

fluxo (main):
  1. load_cappi: lê o .npy e troca o -9999 (fora da cobertura) por NaN;
  2. parse_filename_metadata: radar/alcance/horário do nome do arquivo,
     para o título;
  3. plot_cappi: imagem com paleta meteorológica (estilo NWS) em escala fixa
     de dBZ, eixos em km a partir do radar, anéis de alcance (a cada 50 km,
     até a borda) e barra de cores.  Três estados de pixel (nwc_presets):
     -9999 no fundo, -32 (coberto, sem eco) numa cor própria, dBZ na paleta.
     A extensão sai do tamanho da grade (todos os presets têm 1 km/px, ver
     CF_RESOLUTION_M): 500×500 = ±250 km, 600×600 = ±300 km;
  4. salva o .png e, com --show, abre a janela interativa.

uso:
    python nwcCapGen.py <arquivo.npy> [--out-dir dir] [--out-file saida.png] [--show] [--no-rings]

exemplo:
    python nwcCapGen.py data/cappi/nwc400v2/CGU_400/CGU_400_1751270400.npy --out-dir /tmp --show

2026.nov  mlabru  colormap com with_extremes(bad, under) em vez de
                  set_bad/set_under (PendingDeprecationWarning no matplotlib 3.11)

2026.out  mlabru  refatoração para legibilidade: plot_cappi dividido (anéis,
                  eixos, título, barra de cores); main com argv opcional;
                  parse_filename_metadata e o -9999 vêm de nwc_cappi_path e
                  nwc_presets (eram cópias); comentários errados corrigidos

2026.set  mlabru  - "coberto, sem eco" (-32, presets v2) numa cor própria
                    (set_under), separado do fundo de "fora da cobertura"
                  - extensão do grid pelo tamanho do array (1 km/px) em vez
                    de ±250 km fixo (CF_GRID_HALF_RANGE_M), que plotava o
                    nwc400v1 (±300 km) com escala errada; anéis até a borda

2026.ago  mlabru  - desmembrado --out em --out-dir/--out-file, permitindo
                    escolher diretório e nome do arquivo de saída
                    separadamente
                  - docstring do módulo corrigida que não corresponde à CLI
                    real implementada em main() (npy_file, --out, --show,
                    --no-rings); comentário cruzando limites de dBZ com
                    rv_radar.py/cp_cappi.py

2026.jun  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import sys

# numPy
import numpy as np

# matplotlib: o backend é escolhido em main() (Agg, ou TkAgg com --show)
# antes do primeiro import de matplotlib.pyplot, por isso o pyplot é
# importado dentro das funções
import matplotlib

# locals (módulos leves: nenhum deles puxa Py-ART/h5py)
import work.nwc_cappi_path as cp
import work.nwc_presets as ps

# < constants >────────────────────────────────────────────────────────────────────────

# resolução horizontal (m/px) de todos os presets (nwc_cappi_cfg; um teste
# garante que continua 1 km): a extensão do grid sai do tamanho do array.
# Não importa nwc_cappi_cfg aqui para não puxar Py-ART/h5py só para
# visualizar um .npy
CF_RESOLUTION_M = 1000.0

# escala de refletividade (dBZ) — faixa típica de exibição meteorológica.
# Os outros dois pares de limites (rd_radar.CI_PPI_VMIN e cp_cappi.CI_VMIN,
# do 21.mvol) saíram com esses módulos: hoje esta é a única escala de dBZ
CF_DBZ_MIN = -10.0
CF_DBZ_MAX = 70.0

# cor dos pixels cobertos sem eco (nwc_presets.MF_NO_ECHO, abaixo de
# CF_DBZ_MIN): distinta do fundo (fora da cobertura) e do cinza de < 0 dBZ
CS_NO_ECHO_COLOR = "#2e3440"

# cor dos pixels sem dado (NaN: fora da cobertura) e do fundo do gráfico
CS_NO_DATA_COLOR = "#1a1a2e"
# cor da moldura da figura
CS_FIGURE_COLOR = "#0d0d1a"

# espaçamento (km) dos anéis de alcance sobrepostos no gráfico
CI_RING_STEP_KM = 50

# valor de "sem dado" no array; pixels com este valor viram NaN
CF_NODATA_VALUE = ps.MF_NODATA

# radar/alcance/timestamp a partir do nome do arquivo (mesma regra do resto
# do nowcast; o nome é mantido porque os testes o usam)
parse_filename_metadata = cp.parse_filename_metadata

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────────────
def build_reflectivity_colormap():
    """
    monta um colormap discreto no estilo dos produtos de refletividade NWS
    (azul/verde para chuva fraca, amarelo/laranja para moderada, vermelho/
    magenta para forte), sem depender de uma paleta externa.

    :returns: (matplotlib.colors.Colormap, matplotlib.colors.BoundaryNorm)
    """
    # importa aqui: o backend é escolhido em main(), antes do pyplot
    import matplotlib.colors as mcolors

    # cores em ordem crescente de dBZ
    llst_colors = [
        "#646464",  # < 0 dBZ  — cinza (ruído/sem chuva)
        "#04e9e7",  # 0-5
        "#019ff4",  # 5-10
        "#0300f4",  # 10-15
        "#02fd02",  # 15-20
        "#01c501",  # 20-25
        "#008e00",  # 25-30
        "#fdf802",  # 30-35
        "#e5bc00",  # 35-40
        "#fd9500",  # 40-45
        "#fd0000",  # 45-50
        "#d40000",  # 50-55
        "#bc0000",  # 55-60
        "#f800fd",  # 60-65
        "#9854c6",  # 65-70
    ]

    # limites em dBZ das cores acima (len(bounds) = len(colors) + 1).  O
    # primeiro intervalo (cinza, "< 0 dBZ") vai de CF_DBZ_MIN até 0; os
    # demais são de 5 em 5 dBZ, de 0 até 70
    llst_bounds = [CF_DBZ_MIN] + list(np.arange(0.0, 5.0 * len(llst_colors), 5.0))

    # bad (NaN): fora da cobertura; under (abaixo de CF_DBZ_MIN): o
    # MF_NO_ECHO (-32) de "coberto, sem eco".  with_extremes em vez de
    # set_bad/set_under, deprecados no matplotlib 3.11
    l_colormap = mcolors.ListedColormap(llst_colors).with_extremes(bad=CS_NO_DATA_COLOR,
                                                                    under=CS_NO_ECHO_COLOR)

    l_norm = mcolors.BoundaryNorm(llst_bounds, l_colormap.N)

    return l_colormap, l_norm

# ─────────────────────────────────────────────────────────────────────────────────────
def half_range_m(fnp_cappi: np.ndarray) -> float:
    """
    meia extensão (m) do grid, pelo tamanho do array (CF_RESOLUTION_M/px).
    Ex.: 500×500 -> 250 000 m.

    :raises ValueError: array não 2D ou não quadrado
    """
    if 2 != fnp_cappi.ndim or fnp_cappi.shape[0] != fnp_cappi.shape[1]:
        raise ValueError(f"CAPPI deve ser 2D e quadrado: {fnp_cappi.shape}")

    return fnp_cappi.shape[1] * CF_RESOLUTION_M / 2.0

# ─────────────────────────────────────────────────────────────────────────────────────
def load_cappi(fpth_npy: pathlib.Path) -> np.ndarray:
    """
    carrega um CAPPI .npy, trocando o valor de "sem dado" por NaN (o
    colormap pinta NaN com a cor de fundo)

    :param fpth_npy: caminho do arquivo .npy
    :returns: array 2D float64 de refletividade (dBZ), com NaN onde sem dado
    :raises FileNotFoundError: arquivo ausente
    """
    M_LOG.info("carregando %s", fpth_npy.name)

    if not fpth_npy.exists():
        raise FileNotFoundError(f"arquivo não encontrado: {fpth_npy}")

    lnp_cappi = np.load(fpth_npy).astype(np.float64)

    return np.where(np.isclose(lnp_cappi, CF_NODATA_VALUE), np.nan, lnp_cappi)

# ─────────────────────────────────────────────────────────────────────────────────────
def plot_cappi(fnp_cappi: np.ndarray, fdct_meta: dict | None = None,
               fb_show_rings: bool = True):
    """
    desenha o CAPPI em um eixo matplotlib com escala em km, anéis de
    alcance opcionais e colorbar em dBZ.

    :param fnp_cappi: array 2D de refletividade (dBZ), NaN onde sem dado
    :param fdct_meta: dict opcional com radar/alcance/timestamp para o título
    :param fb_show_rings: se True, sobrepõe anéis de alcance e marca o radar
    :returns: (matplotlib.figure.Figure, matplotlib.axes.Axes)
    """
    import matplotlib.pyplot as plt

    lf_half_km = half_range_m(fnp_cappi) / 1000.0

    l_colormap, l_norm = build_reflectivity_colormap()

    # figura quadrada, fundo escuro estilo "radar scope"
    l_figure, l_axes = plt.subplots(figsize=(8, 8), facecolor=CS_FIGURE_COLOR)
    l_axes.set_facecolor(CS_NO_DATA_COLOR)

    # origin="lower": a linha 0 do array fica embaixo (eixo Y cresce para o norte)
    l_image = l_axes.imshow(fnp_cappi, origin="lower", cmap=l_colormap, norm=l_norm,
                            extent=(-lf_half_km, lf_half_km, -lf_half_km, lf_half_km),
                            interpolation="nearest")

    if fb_show_rings:
        _draw_range_rings(l_axes, lf_half_km)

    _style_axes(l_axes)
    l_axes.set_title(_build_title(fdct_meta), color="white", fontsize=12)
    _add_colorbar(l_figure, l_axes, l_image)

    l_figure.tight_layout()

    return l_figure, l_axes

# ─────────────────────────────────────────────────────────────────────────────────────
def _draw_range_rings(f_axes, ff_half_km: float) -> None:
    """
    anéis concêntricos a cada CI_RING_STEP_KM até a borda, com o rótulo em
    km, e uma cruz no centro (posição do radar)
    """
    import matplotlib.pyplot as plt

    for li_ring_km in range(CI_RING_STEP_KM, int(ff_half_km) + 1, CI_RING_STEP_KM):
        l_circle = plt.Circle((0, 0), li_ring_km, fill=False,
                              color="white", linewidth=0.5, alpha=0.35)
        f_axes.add_patch(l_circle)
        f_axes.text(0, li_ring_km, f"{li_ring_km} km", color="white",
                    fontsize=7, alpha=0.6, ha="center", va="bottom")

    f_axes.plot(0, 0, marker="+", color="white", markersize=10, markeredgewidth=1.5)

# ─────────────────────────────────────────────────────────────────────────────────────
def _style_axes(f_axes) -> None:
    """
    rótulos dos eixos e moldura em branco (o fundo é escuro)
    """
    f_axes.set_xlabel("distância leste-oeste (km)", color="white")
    f_axes.set_ylabel("distância norte-sul (km)", color="white")
    f_axes.tick_params(colors="white")

    for l_spine in f_axes.spines.values():
        l_spine.set_color("white")

# ─────────────────────────────────────────────────────────────────────────────────────
def _build_title(fdct_meta: dict | None) -> str:
    """
    título do gráfico: "CAPPI — radar <R> (<A> km) — <data UTC>", ou só
    "CAPPI" quando o nome do arquivo não tinha metadados
    """
    if not fdct_meta:
        return "CAPPI"

    ldt_volume = datetime.datetime.fromtimestamp(fdct_meta["timestamp"],
                                                 tz=datetime.timezone.utc)

    return (f"CAPPI — radar {fdct_meta['radar']} ({fdct_meta['alcance']} km) — "
            f"{ldt_volume.strftime('%Y-%m-%d %H:%M UTC')}")

# ─────────────────────────────────────────────────────────────────────────────────────
def _add_colorbar(f_figure, f_axes, f_image) -> None:
    """
    barra de cores de refletividade (dBZ), com texto em branco
    """
    l_colorbar = f_figure.colorbar(f_image, ax=f_axes, label="refletividade (dBZ)",
                                   shrink=0.85, pad=0.02)
    l_colorbar.ax.yaxis.label.set_color("white")
    l_colorbar.ax.tick_params(colors="white")

# ─────────────────────────────────────────────────────────────────────────────────────
def main(flst_argv: list | None = None) -> int:
    """
    ponto de entrada do programa.

    :param flst_argv: argumentos (None = sys.argv)
    :returns: 0 se sucesso, 1 em caso de erro
    """
    l_args = _build_argument_parser().parse_args(flst_argv)

    # backend antes do primeiro import de pyplot: Agg só grava arquivo;
    # TkAgg abre janela
    matplotlib.use("TkAgg" if l_args.show else "Agg")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        lnp_cappi = load_cappi(l_args.npy_file)
        ldct_meta = parse_filename_metadata(l_args.npy_file)

        l_figure, _ = plot_cappi(lnp_cappi, ldct_meta, fb_show_rings=not l_args.no_rings)

        lpth_output = _output_path(l_args)
        lpth_output.parent.mkdir(parents=True, exist_ok=True)

        l_figure.savefig(lpth_output, dpi=150, facecolor=l_figure.get_facecolor())
        M_LOG.info("imagem salva em %s", lpth_output)

        if l_args.show:
            import matplotlib.pyplot as plt
            plt.show()

    except Exception:
        M_LOG.exception("erro ao visualizar CAPPI.")
        return 1

    return 0

# ─────────────────────────────────────────────────────────────────────────────────────
def _build_argument_parser() -> argparse.ArgumentParser:
    """
    npy_file, --out-dir, --out-file, --show, --no-rings
    """
    l_parser = argparse.ArgumentParser(description="visualiza um CAPPI (.npy) de cappi_products")
    l_parser.add_argument("npy_file", type=pathlib.Path,
                          help="caminho do arquivo CAPPI .npy")
    l_parser.add_argument("--out-dir", type=pathlib.Path, default=None,
                          help="diretório do arquivo de imagem de saída; "
                               "se omitido, usa o diretório de npy_file")
    l_parser.add_argument("--out-file", type=pathlib.Path, default=None,
                          help="nome do arquivo de imagem de saída (.png); "
                               "se omitido, usa <npy_file>.png")
    l_parser.add_argument("--show", action="store_true",
                          help="exibe a janela interativa além de salvar a imagem")
    l_parser.add_argument("--no-rings", action="store_true",
                          help="não desenha anéis de alcance nem marca o radar")
    return l_parser

# ─────────────────────────────────────────────────────────────────────────────────────
def _output_path(f_args: argparse.Namespace) -> pathlib.Path:
    """
    caminho do .png: --out-dir (default: diretório do .npy) +
    --out-file (default: nome do .npy com extensão .png)
    """
    lpth_output_dir = f_args.out_dir or f_args.npy_file.parent
    lpth_output_name = f_args.out_file or f_args.npy_file.with_suffix(".png").name

    return lpth_output_dir / lpth_output_name

# ─────────────────────────────────────────────────────────────────────────────────────
# this is the bootstrap process

if "__main__" == __name__:

    sys.exit(main())

# < the end >──────────────────────────────────────────────────────────────────────────
