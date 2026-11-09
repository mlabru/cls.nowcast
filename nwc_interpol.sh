#!/usr/bin/env bash
#
# Centro Logístico de Simulação Meteorológica
# Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo
#
# nwc_interpol.sh
# script cron-driven do pipeline nowcast (uma execução por chamada).  O
# nowcast só consome o que ele grava no catalog.db compartilhado.
#
# workflow:
# - apps/nwcInterpolate.py: interpola as lacunas curtas das séries
#   ([interpolate] method, default optical flow + média em Z); uma falha
#   aqui não impede o passo seguinte;
# - apps/nwcSequences.py: reconstrói a tabela sequences (janelas de 4
#   entradas + 1 alvo).
#
# 2026.out  mlabru  - normalização com 21.mvol/25.oflow: corpo do topo em
#                     funções de um passo (acquire_instance_lock,
#                     run_interpolate, run_sequences) e main(), como o
#                     vol_sync.sh/vol_register.sh; NWC_RUN_NO_MAIN vem logo
#                     antes da chamada do main
#                   - início comum (cd, mkdir, venv, PYTHONPATH) em
#                     nwc_common.sh (nwc_enter_run_dir, nwc_setup_python);
#                     comentários de ruído removidos
#                   - mensagens de falha apontam também o .log da etapa: o
#                     logging vai para o stdout (.log), e o .err só recebe
#                     tracebacks fora do logging (ficava vazio e o aviso
#                     apontava só para ele)
#                   - configuração de ambiente em nwc_cfg.sh e
#                     log/check_log_size em nwc_common.sh (eram copiados
#                     aqui e no outro nwc_*.sh); a trava MET_ENV=test roda
#                     no source do nwc_cfg.sh e não lê mais o config.ini com
#                     sed: os caminhos dele são checados pelo Python
#                     (nwc_config.init)
#                   - NWC_HOME (padrão: o diretório do script): raiz de
#                     execução com config.ini, logs/ e data/state/; o código
#                     continua vindo de LIB_DIR.  Antes o lock e o state
#                     caíam em data/ -> 03.dat/data (compartilhado), e não
#                     havia como rodar um ciclo de teste isolado
#                   - check_test_env: com MET_ENV=test (21.mvol/tst_env),
#                     NWC_HOME e database/cappi_dir/log_dir do config.ini
#                     precisam estar em MET_TST_ROOT
#
# 2026.set  mlabru  - nwc_run.sh -> nwc_interpol.sh (log
#                     nwc_interpol.log/.err); stdout dos estágios em
#                     logs/<estágio>.log (>>, rotacionado); venv reativado
#                     (comentado, o cron rodava o python3 do sistema, sem
#                     Py-ART/h5py); lock mantido em
#                     .nowcast_instance.lock para excluir um nwc_run.sh antigo
#
#                   - nowcast.sh -> nwc_run.sh; chama nwcInterpolate.py e
#                     nwcSequences.py (os apps/nc_*.py foram renomeados e o
#                     cron quebrava)
#
#                   - lock de instância próprio (.nowcast_instance.lock). 
#                     Uma interpolação lenta não é atropelada pela próxima
#                     do cron nem bloqueia a conversão (o nowcast replaneja
#                     com o que já estiver no banco).
#
# 2026.ago  mlabru  - venv só se existir, NWC_RUN_NO_MAIN
#
#                   - stderr de cada estágio em logs/<estágio>.err; o log de
#                     execução continua no [log] file do config.ini
#
#                   - falha em nwcSequences.py sai com o rc do estágio
#
#                   - cada estágio garante o esquema do banco no início
#                     (nwc_database.ensure_schema): no deploy o catalog.db é
#                     criado antes, sem a tabela sequences.
#
# 2026.jun  mlabru  initial version (Linux/Bash)
#
# depends on: nwc_cfg.sh, nwc_common.sh, python3, flock,
#             apps/nwcInterpolate.py, apps/nwcSequences.py (e as
#             dependências destes: numPy, openCV)

set -euo pipefail

# < configuração >---------------------------------------------------------------------

# diretório deste script
LS_SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ambiente (NWC_LIB_DIR, NWC_PATH, NWC_LOGS, NWC_STATE, locks, trava
# MET_ENV=test) e funções comuns (log, check_log_size)
source "${LS_SCRIPT_DIR}/nwc_cfg.sh"
source "${LS_SCRIPT_DIR}/nwc_common.sh"

# log do próprio shell (log()) e erros
NWC_LOG_APP="${NWC_LOGS}/nwc_interpol.log"
NWC_LOG_ERR="${NWC_LOGS}/nwc_interpol.err"

# stdout e stderr (tracebacks fora do logging) dos processos Python filhos;
# anexados e rotacionados por check_log_size
NWC_LOG_INTERP_APP="${NWC_LOGS}/nwcInterpolate.log"
NWC_LOG_INTERP_ERR="${NWC_LOGS}/nwcInterpolate.err"
NWC_LOG_SEQ_APP="${NWC_LOGS}/nwcSequences.log"
NWC_LOG_SEQ_ERR="${NWC_LOGS}/nwcSequences.err"

# -------------------------------------------------------------------------------------
# adquire o lock de instância (fd 203, mantido aberto até o fim do script; o
# nwc_convert.sh usa o fd 204, com outro arquivo).  se outra instância já
# estiver rodando, sai com 0
#
acquire_instance_lock()
{
    exec 203>"${NWC_INSTANCE_LOCK}"

    if ! flock -n 203; then
        log "[info]: nowcast já em execução; saindo."
        exit 0
    fi

} # acquire_instance_lock

# -------------------------------------------------------------------------------------
# interpolação temporal das lacunas curtas (nwcInterpolate.py)
#
# retorno: o código de saída do nwcInterpolate.py (0 = sucesso)
#
run_interpolate()
{
    # rc capturado via '||' (com 'if !' o rc original se perderia)
    local li_rc=0

    python3 "${NWC_LIB_DIR}/apps/nwcInterpolate.py" >> "${NWC_LOG_INTERP_APP}" \
            2>> "${NWC_LOG_INTERP_ERR}" || li_rc=$?

    return "${li_rc}"

} # run_interpolate

# -------------------------------------------------------------------------------------
# reconstrução da tabela sequences (nwcSequences.py)
#
# retorno: o código de saída do nwcSequences.py (0 = sucesso)
#
run_sequences()
{
    local li_rc=0

    python3 "${NWC_LIB_DIR}/apps/nwcSequences.py" >> "${NWC_LOG_SEQ_APP}" \
            2>> "${NWC_LOG_SEQ_ERR}" || li_rc=$?

    return "${li_rc}"

} # run_sequences

# -------------------------------------------------------------------------------------
# fluxo principal:
#   1. raiz de execução, diretórios, lock de instância, rotação dos logs dos
#      filhos, venv e PYTHONPATH;
#   2. interpolação (nwcInterpolate.py) -- falha aqui só avisa;
#   3. sequências (nwcSequences.py) -- falha aqui encerra com o mesmo rc.
#
main()
{
    local li_rc=0

    # raiz de execução (caminhos relativos do config.ini) e os diretórios de
    # log e de estado -- precisa ser a primeira ação (nwc_common.sh)
    nwc_enter_run_dir

    log "-----------------------------------------------------------------"
    log "[info]: iniciando nowcast interpolate (host=$(hostname))..."

    acquire_instance_lock

    # rotação dos logs dos processos filhos
    check_log_size "${NWC_LOG_INTERP_APP}" "${NWC_LOG_INTERP_ERR}" \
                   "${NWC_LOG_SEQ_APP}" "${NWC_LOG_SEQ_ERR}"

    # venv (se existir) e PYTHONPATH (nwc_common.sh)
    nwc_setup_python

    # 2. interpolação; uma falha (rc != 0 também quando algum slot falhou,
    #    registrado em processing_log e retentado) não impede as sequências
    run_interpolate || li_rc=$?

    if [ "${li_rc}" -ne 0 ]; then
        log "[warn]: nwcInterpolate.py terminou com rc=${li_rc}; veja ${NWC_LOG_INTERP_APP} e ${NWC_LOG_INTERP_ERR}"
    fi

    # 3. sequências
    li_rc=0
    run_sequences || li_rc=$?

    if [ "${li_rc}" -ne 0 ]; then
        log "[error]: nwcSequences.py falhou (rc=${li_rc}); veja ${NWC_LOG_SEQ_APP} e ${NWC_LOG_SEQ_ERR}"
        exit "${li_rc}"
    fi

    log "[info]: nowcast interpolate concluído."

} # main

# < main >-----------------------------------------------------------------------------

# permite carregar a configuração e as funções sem executar o main (testes)
[ "${NWC_RUN_NO_MAIN:-false}" = true ] && return 0

main

# < the end >--------------------------------------------------------------------------
