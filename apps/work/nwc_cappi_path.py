# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_cappi_path
localização dos CAPPIs: <base>/<R>_<A>/<R>_<A>_<timestamp>.npy, com radar e
alcance tirados do nome do arquivo — a mesma regra do 21.mvol.

os produtos de cappi_products ficam em <cappi_dir>/<preset>/<R>_<A>/<nome>
(base = preset_dir(cappi_dir, preset)): dois presets do mesmo volume têm o
mesmo nome de arquivo.  O layout sem <preset>/ (base = cappi_dir) é o do
volProcess do 21.mvol e dos .npy de antes de cappi_products, usado só pelas
migrações.

regras espelhadas do 21.mvol:

- build_series_dir / build_output_filename: 21.mvol apps/volProcess.py
- parse_filename_metadata: nwcCapGen (que veio do capGen do 21.mvol)
- o diretório gravado em volumes.cappi_path é ignorado, e o arquivo é
  procurado pelo nome (como em 21.mvol apps/volMigrateUTC.py::plan_renames)

cópia (e não import) porque volProcess importa Py-ART e capGen importa
matplotlib, e o nowcast não precisa de nenhum dos dois para localizar um
arquivo.  test/test_nwc_cappi_path.py compara com o capGen quando o 21.mvol
está disponível.

2026.out  mlabru  refatoração para legibilidade: build_product_filename (antes
                  build_filename copiado em nwcConvert e nwcInterpolate),
                  is_usable_cappi_file (antes neighbor_ok/_nonempty) e
                  _file_in_series_dir (final comum de duas funções)
2026.set  mlabru  preset_dir(): produtos em <cappi_dir>/<preset>/<R>_<A>/
2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import pathlib

# ─────────────────────────────────────────────────────────────────────────────────────
def build_output_filename(fpth_cappi_dir: pathlib.Path, fs_radar: str, fi_alcance: int,
                          fi_timestamp: int) -> pathlib.Path:
    """
    caminho do .npy: <cappi_dir>/<radar>_<alcance>/<radar>_<alcance>_<timestamp>.npy
    (absoluto, como o volProcess grava em volumes.cappi_path)

    só o diretório é resolvido (resolve()), não o arquivo: para um arquivo
    comum dá o mesmo que o volProcess (lá o .npy ainda não existe quando o
    caminho é montado), e um .npy que seja symlink continua sendo o link —
    mover ou apagar o caminho nunca atinge o arquivo apontado
    """
    ls_name = f"{fs_radar}_{fi_alcance}_{fi_timestamp}.npy"

    return _file_in_series_dir(fpth_cappi_dir, fs_radar, fi_alcance, ls_name)

# ─────────────────────────────────────────────────────────────────────────────────────
def build_product_filename(fpth_cappi_dir: pathlib.Path, fs_preset: str, fs_radar: str,
                           fi_alcance: int, fi_timestamp: int) -> pathlib.Path:
    """
    caminho do .npy de um produto de cappi_products:
    <cappi_dir>/<preset>/<R>_<A>/<R>_<A>_<timestamp>.npy (absoluto).

    é o nome que nwcConvert (observado) e nwcInterpolate (interpolado) gravam;
    um observado e um interpolado no mesmo horário têm o MESMO nome.

    :param fpth_cappi_dir: raiz dos CAPPIs ([paths] cappi_dir)
    :param fs_preset: id do preset (nwc_presets), ex.: "nwc250v2"
    :param fs_radar: estação, ex.: "CGU"
    :param fi_alcance: alcance (km)
    :param fi_timestamp: epoch UTC do volume (s)
    """
    return build_output_filename(preset_dir(fpth_cappi_dir, fs_preset), fs_radar,
                                 fi_alcance, fi_timestamp)

# ─────────────────────────────────────────────────────────────────────────────────────
def build_series_dir(fpth_base_dir: pathlib.Path, fs_radar: str,
                     fi_alcance: int) -> pathlib.Path:
    """
    subdiretório da série (estação/alcance): <base_dir>/<radar>_<alcance>
    """
    return pathlib.Path(fpth_base_dir) / f"{fs_radar}_{fi_alcance}"

# ─────────────────────────────────────────────────────────────────────────────────────
def preset_dir(fpth_cappi_dir: pathlib.Path, fs_preset: str) -> pathlib.Path:
    """
    raiz dos produtos de um preset: <cappi_dir>/<preset>.  É a base para
    build_output_filename e resolve_cappi_path de um produto.
    """
    # o id vira nome de diretório: não pode ser vazio nem sair de cappi_dir
    if not fs_preset or "/" in fs_preset or fs_preset in (".", ".."):
        raise ValueError(f"id de preset inválido para diretório: {fs_preset!r}")

    return pathlib.Path(fpth_cappi_dir) / fs_preset

# ─────────────────────────────────────────────────────────────────────────────────────
def parse_filename_metadata(fpth_npy: pathlib.Path) -> dict:
    """
    extrai radar/alcance/timestamp do nome <radar>_<alcance>_<timestamp>.npy

    :returns: {"radar", "alcance", "timestamp"}, ou dict vazio se o nome não
              casar com o padrão (mesmo contrato do capGen)
    """
    llst_parts = pathlib.Path(fpth_npy).stem.split("_")

    # precisa de pelo menos <radar>_<alcance>_<timestamp>
    if len(llst_parts) < 3:
        return {}

    # os dois últimos campos são alcance e timestamp; o radar pode conter "_"
    try:
        li_alcance = int(llst_parts[-2])
        li_timestamp = int(llst_parts[-1])

    except ValueError:
        return {}

    ls_radar = "_".join(llst_parts[:-2])

    return {"radar": ls_radar, "alcance": li_alcance, "timestamp": li_timestamp}

# ─────────────────────────────────────────────────────────────────────────────────────
def resolve_cappi_path(fs_cappi_path: str, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                       fpth_cappi_dir: pathlib.Path) -> pathlib.Path:
    """
    onde está o CAPPI de uma linha de volumes: <cappi_dir>/<R>_<A>/<nome>, com
    R e A tirados do nome do arquivo.  O diretório de fs_cappi_path não é usado.

    :raises ValueError: nome fora do padrão, ou radar/alcance/timestamp do nome
                        diferentes dos da linha do banco (dado inconsistente)
    """
    ls_name = pathlib.Path(fs_cappi_path).name

    ldct_meta = parse_filename_metadata(ls_name)

    if not ldct_meta or pathlib.Path(ls_name).suffix != ".npy":
        raise ValueError(f"nome de CAPPI fora do padrão <R>_<A>_<ts>.npy: {fs_cappi_path!r}")

    ltpl_from_filename = (ldct_meta["radar"], ldct_meta["alcance"], ldct_meta["timestamp"])
    ltpl_from_database = (fs_radar, fi_alcance, fi_timestamp)

    if ltpl_from_filename != ltpl_from_database:
        raise ValueError(f"nome do CAPPI {ls_name!r} indica {ltpl_from_filename}, "
                         f"mas a linha do banco é {ltpl_from_database}")

    return _file_in_series_dir(fpth_cappi_dir, ldct_meta["radar"], ldct_meta["alcance"],
                               ls_name)

# ─────────────────────────────────────────────────────────────────────────────────────
def is_usable_cappi_file(fpth_cappi: pathlib.Path | str) -> bool:
    """
    o CAPPI pode ser lido? (existe, é arquivo e não está vazio).  É o teste
    que o dry-run faz nos vizinhos de uma lacuna: um vizinho ausente faria o
    worker falhar.
    """
    lpth_cappi = pathlib.Path(fpth_cappi)

    return lpth_cappi.is_file() and lpth_cappi.stat().st_size > 0

# ─────────────────────────────────────────────────────────────────────────────────────
def _file_in_series_dir(fpth_base_dir: pathlib.Path, fs_radar: str, fi_alcance: int,
                        fs_name: str) -> pathlib.Path:
    """
    <base resolvida>/<radar>_<alcance>/<nome>.  Só o diretório é resolvido
    (resolve()), não o arquivo — ver build_output_filename.
    """
    lpth_base_dir = pathlib.Path(fpth_base_dir).resolve()

    return build_series_dir(lpth_base_dir, fs_radar, fi_alcance) / fs_name

# < the end >──────────────────────────────────────────────────────────────────────────
