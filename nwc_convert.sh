#!/usr/bin/env bash
#
# Centro Logístico de Simulação Meteorológica
# Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo
#
# nwc_convert.sh
# script cron-driven da conversão .mvol -> CAPPI (uma execução por chamada):
# apps/nwcConvert.py converte os volumes registrados em volumes e grava os
# produtos em cappi_products, no preset ativo de cada alcance ([convert] do
# config.ini).
#
# ordem no cron: nwc_convert.sh -> nwc_interpol.sh (interpolação e
# sequências).  Cada um tem o seu lock e tolera o outro rodando: a
# interpolação adia as lacunas com volume ainda pendente (view
# cappi_pending) e a retenção não apaga um .mvol pendente.
#
# rc do nwcConvert.py: 3 = algum volume falhou (registrado em processing_log
# e retentado na próxima execução) -> só avisa; outro diferente de 0 = erro
# fatal -> sai com o rc (2 = esquema/banco, 1 = exceção do Python fora do
# main: import, config.ini).
#
# lock de instância próprio (.nwc_convert_instance.lock), independente do
# nwc_interpol.sh: uma interpolação lenta não atrasa a conversão, e
# vice-versa.
#
# 2026.out  mlabru  - normalização com 21.mvol/25.oflow: corpo do topo em
#                     funções de um passo (acquire_instance_lock,
#                     run_convert) e main(), como o vol_register.sh;
#                     NWC_CONVERT_NO_MAIN vem logo antes da chamada do main
#                   - início comum (cd, mkdir, venv, PYTHONPATH) em
#                     nwc_common.sh (nwc_enter_run_dir, nwc_setup_python);
#                     comentários de ruído removidos
#                   - mensagens de falha apontam também o [log] file do
#                     config.ini: o .err só recebe tracebacks fora do
#                     logging
#                   - configuração de ambiente em nwc_cfg.sh e
#                     log/check_log_size em nwc_common.sh (eram copiados
#                     aqui e no outro nwc_*.sh); a trava MET_ENV=test roda
#                     no source do nwc_cfg.sh e não lê mais o config.ini com
#                     sed: os caminhos dele são checados pelo Python
#                     (nwc_config.init)
#                   - rc 3 (não mais 1) = algum volume falhou: o rc 1 é o do
#                     Python numa exceção não capturada (venv sem Py-ART,
#                     config.ini ausente) e era logado como [warn], exit 0
#                   - NWC_HOME (padrão: o diretório do script): raiz de
#                     execução com config.ini, logs/ e data/state/; o código
#                     continua vindo de LIB_DIR.  Antes o lock e o state
#                     caíam em data/ -> 03.dat/data (compartilhado), e não
#                     havia como rodar um ciclo de teste isolado
#                   - check_test_env: com MET_ENV=test (21.mvol/tst_env),
#                     NWC_HOME e database/cappi_dir/log_dir do config.ini
#                     precisam estar em MET_TST_ROOT
#
# 2026.set  mlabru  initial version (Linux/Bash)

# depends on: nwc_cfg.sh, nwc_common.sh, python3, flock, apps/nwcConvert.py
#             (e as dependências dele: numPy, Py-ART, h5py)

set -euo pipefail

# < configuração >---------------------------------------------------------------------

# diretório deste script
LS_SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ambiente (NWC_LIB_DIR, NWC_PATH, NWC_LOGS, NWC_STATE, locks, trava
# MET_ENV=test) e funções comuns (log, check_log_size)
source "${LS_SCRIPT_DIR}/nwc_cfg.sh"
source "${LS_SCRIPT_DIR}/nwc_common.sh"

# log do próprio shell (log()) e erros
NWC_LOG_APP="${NWC_LOGS}/nwc_convert.log"
NWC_LOG_ERR="${NWC_LOGS}/nwc_convert.err"

# stderr do processo Python (tracebacks fora do logging); o stdout repete o
# [log] file do config.ini e é descartado
NWC_LOG_CONVERT_ERR="${NWC_LOGS}/nwcConvert.err"

# -------------------------------------------------------------------------------------
# adquire o lock de instância (fd 204, mantido aberto até o fim do script; o
# nwc_interpol.sh usa o fd 203, com outro arquivo).  se outra instância já
# estiver rodando, sai com 0
#
acquire_instance_lock()
{
    exec 204>"${NWC_CONVERT_INSTANCE_LOCK}"

    if ! flock -n 204; then
        log "[info]: conversão já em execução; saindo."
        exit 0
    fi

} # acquire_instance_lock

# -------------------------------------------------------------------------------------
# conversão .mvol -> CAPPI (nwcConvert.py); o stdout repete o [log] file do
# config.ini e é descartado
#
# retorno: o código de saída do nwcConvert.py (0 = ok, 3 = algum volume
#          falhou, 2 = fatal, 1 = exceção do Python fora do main)
#
run_convert()
{
    # rc capturado via '||' (com 'if !' o rc original se perderia)
    local li_rc=0

    python3 "${NWC_LIB_DIR}/apps/nwcConvert.py" \
            > /dev/null 2>> "${NWC_LOG_CONVERT_ERR}" || li_rc=$?

    return "${li_rc}"

} # run_convert

# -------------------------------------------------------------------------------------
# fluxo principal:
#   1. raiz de execução, diretórios, lock de instância, rotação do log do
#      filho, venv e PYTHONPATH;
#   2. conversão (nwcConvert.py) -- rc 3 só avisa; outro rc != 0 encerra
#      com o mesmo rc.
#
main()
{
    local li_rc=0

    # raiz de execução (caminhos relativos do config.ini) e os diretórios de
    # log e de estado -- precisa ser a primeira ação (nwc_common.sh)
    nwc_enter_run_dir

    log "-----------------------------------------------------------------"
    log "[info]: iniciando conversão (host=$(hostname))..."

    acquire_instance_lock

    # rotação do log do processo filho
    check_log_size "${NWC_LOG_CONVERT_ERR}"

    # venv (se existir) e PYTHONPATH (nwc_common.sh)
    nwc_setup_python

    # 2. conversão
    run_convert || li_rc=$?

    if [ "${li_rc}" -eq 3 ]; then
        # falha em algum volume: rotineiro, retentado na próxima execução
        log "[warn]: nwcConvert.py: algum volume falhou (rc=3); veja processing_log, o [log] file do config.ini e ${NWC_LOG_CONVERT_ERR}"

    elif [ "${li_rc}" -ne 0 ]; then
        log "[error]: nwcConvert.py falhou (rc=${li_rc}); veja o [log] file do config.ini e ${NWC_LOG_CONVERT_ERR}"
        exit "${li_rc}"
    fi

    log "[info]: conversão concluída."

} # main

# < main >-----------------------------------------------------------------------------

# permite carregar a configuração e as funções sem executar o main (testes)
[ "${NWC_CONVERT_NO_MAIN:-false}" = true ] && return 0

main

# < the end >--------------------------------------------------------------------------
