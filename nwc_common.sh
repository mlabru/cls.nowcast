# Centro Logístico de Simulação Meteorológica
# Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo
#
# nwc_common.sh
# funções compartilhadas por nwc_convert.sh e nwc_interpol.sh: o log() do
# nowcast, por cima do met_log, e o check_log_size (os dois do
# cls_common.sh, comum aos projetos met).  Só define funções: nada roda no
# source.
#
# 2026.out  mlabru  - normalização com 21.mvol/25.oflow: estilo das funções
#                     (separador "# ---", chave na linha seguinte);
#                     virtualenv em NWC_VENV (nwc_cfg.sh), como o OFL_VENV
#                   - nwc_enter_run_dir e nwc_setup_python: o início comum
#                     de nwc_convert.sh e nwc_interpol.sh (era copiado nos
#                     dois)
#                   - check_log_size e o corpo do log() vêm do cls_common.sh
#                     (symlink para 04.dvl/bash)
#
# 2026.set  mlabru  initial version (Bash/Linux)
#
# depends on: bash, cls_common.sh, GI_LOG_MAX_SIZE (nwc_cfg.sh) para
#             check_log_size, NWC_LOG_APP/NWC_LOG_ERR (definidas por cada
#             script) para log(), NWC_PATH/NWC_LOGS/NWC_STATE/NWC_LIB_DIR
#             e NWC_VENV (nwc_cfg.sh) para as nwc_*
# shellcheck shell=bash

# check_log_size e met_log (cls_common.sh, compartilhado pelos projetos met)
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cls_common.sh"

# -------------------------------------------------------------------------------------
# logger com timestamp UTC e rotação (met_log): grava em NWC_LOG_APP,
# ecoando em stderr -- o nível ([info]/[warn]/[error]) vem no início de $*
#
# uso: log "[info]: mensagem"
#
log()
{
    met_log "${NWC_LOG_APP}" "${NWC_LOG_ERR}" "$@"

} # log

# -------------------------------------------------------------------------------------
# entra na raiz de execução (NWC_PATH: onde estão o config.ini, cujos
# caminhos são relativos a ela, logs/ e data/state/) e cria logs/ e
# data/state/.  Tem de ser a primeira ação do main: log() escreve em
# NWC_LOGS e o lock fica em NWC_STATE.
#
# uso: nwc_enter_run_dir
#
nwc_enter_run_dir()
{
    cd "${NWC_PATH}"
    mkdir -p "${NWC_LOGS}" "${NWC_STATE}"

} # nwc_enter_run_dir

# -------------------------------------------------------------------------------------
# prepara o python3 dos estágios: ativa o virtualenv NWC_VENV (Py-ART, h5py,
# OpenCV) só se ele existir, e põe apps/ e apps/util no PYTHONPATH (apps/
# não é um package; os módulos são importados como work.*, tline.*, ...).
#
# uso: nwc_setup_python
#
nwc_setup_python()
{
    if [ -f "${NWC_VENV}/bin/activate" ]; then
        source "${NWC_VENV}/bin/activate"
    fi

    export PYTHONPATH="${NWC_LIB_DIR}/apps:${NWC_LIB_DIR}/apps/util${PYTHONPATH:+:${PYTHONPATH}}"

} # nwc_setup_python

# < the end >--------------------------------------------------------------------------
