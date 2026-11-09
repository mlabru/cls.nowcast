# Centro Logístico de Simulação Meteorológica
# Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo
#
# nwc_cfg.sh
# configuração de ambiente (onde: código, dados, logs, locks) compartilhada
# por nwc_convert.sh e nwc_interpol.sh
#
# os parâmetros de algoritmo (como: timeline, interpolação, sequências,
# conversão) continuam no config.ini, lido e validado pelo Python
# (apps/work/nwc_config.py).  Precedência de um caminho do pipeline:
# argumento de linha de comando > NWC_* do ambiente > [paths] do config.ini. 
# O shell nunca lê o config.ini, e o Python nunca lê este arquivo.
#
# sempre "source"ado, nunca executado -- não define "set -e": herda as
# opções de shell de quem importa.
#
# 2026.out  mlabru  - NWC_VENV (padrão ~/.venv), sobreponível pelo ambiente,
#                     como o OFL_VENV do 25.oflow
#
#                   - NWC_PATH (antes NWC_HOME, mantido como alias);
#                     NWC_DBASE_FILE/NWC_CAPPI_DIR/NWC_LOG_DIR sobrepõem o
#                     [paths] do config.ini; a trava MET_ENV=test não lê
#                     mais o config.ini com sed (os caminhos dele são
#                     checados pelo próprio Python, em nwc_config.init)
#
#                   - trava via met_check_test_env (cls_common.sh, symlink),
#                     comum aos outros projetos
#
# 2026.set  mlabru  initial version (Bash/Linux)

# depends on: bash, cls_common.sh
# shellcheck shell=bash

# < configuração >---------------------------------------------------------------------

# código (apps/*.py) -- o diretório deste arquivo
NWC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# raiz de execução: config.ini, logs/ e data/state/ (o ambiente de teste
# aponta para 03.dat/tst/nowcast).  NWC_HOME é o nome antigo, aceito por
# compatibilidade
NWC_PATH="${NWC_PATH:-${NWC_HOME:-${NWC_LIB_DIR}}}"
NWC_HOME="${NWC_PATH}"

# logs do shell e dos processos filhos; arquivos de estado (locks)
NWC_LOGS="${NWC_PATH}/logs"
NWC_STATE="${NWC_PATH}/data/state"

# locks de instância (um por script: a conversão e a interpolação não se
# atrasam).  O nome .nowcast_instance.lock exclui também um nwc_run.sh
# antigo
NWC_CONVERT_INSTANCE_LOCK="${NWC_STATE}/.nwc_convert_instance.lock"
NWC_INSTANCE_LOCK="${NWC_STATE}/.nowcast_instance.lock"

# sobreposições do [paths] do config.ini (vazio = usar o config.ini);
# relativos a NWC_PATH, como os do config.ini
NWC_DBASE_FILE="${NWC_DBASE_FILE:-}"
NWC_CAPPI_DIR="${NWC_CAPPI_DIR:-}"
NWC_LOG_DIR="${NWC_LOG_DIR:-}"

export NWC_DBASE_FILE NWC_CAPPI_DIR NWC_LOG_DIR

# virtualenv dos estágios (Py-ART, h5py, OpenCV); sem ele, o python3 do
# sistema (nwc_setup_python, em nwc_common.sh)
NWC_VENV="${NWC_VENV:-${HOME:-/home/$(id -un)}/.venv}"

# limite de tamanho dos logs (10MB), usado na rotação simples
GI_LOG_MAX_SIZE=$((10 * 1024 * 1024))

# < trava do ambiente de teste >-------------------------------------------------------

# met_check_test_env (cls_common.sh, compartilhado pelos projetos met)
source "${NWC_LIB_DIR}/cls_common.sh"

# com MET_ENV=test (21.mvol/tst_env), NWC_PATH e as sobreposições acima
# (relativas a NWC_PATH) precisam estar dentro de MET_TST_ROOT.  Roda no
# source, antes de qualquer mkdir/log/lock de quem importou
met_check_test_env -b "${NWC_PATH}" "${NWC_PATH}" "${NWC_DBASE_FILE}" \
                      "${NWC_CAPPI_DIR}" "${NWC_LOG_DIR}" || exit 1

# < the end >--------------------------------------------------------------------------
