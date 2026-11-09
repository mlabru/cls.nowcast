# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_pool
execução de uma lista de tarefas em processos paralelos (fase 2 do
nwcConvert e do nwcInterpolate).

regras, iguais para os dois estágios:
- [convert|interpolate] workers = 0 usa CPUs − 1 processos; 1 roda em série
  no próprio processo (é o que os testes usam: sem fork, com monkeypatch
  funcionando);
- nunca mais processos do que tarefas;
- a função worker NÃO pode levantar exceção (ela devolve o erro no
  resultado).  Se mesmo assim o future falhar, é porque o processo do worker
  morreu (falta de memória, segfault no Py-ART/OpenCV): o pool vira
  BrokenProcessPool e as tarefas afetadas recebem o resultado de falha que
  `fn_on_lost` monta.  É por isso que se usa ProcessPoolExecutor e não
  multiprocessing.Pool.map, que nesse caso esperava para sempre.

2026.out  mlabru  initial version: o laço de ProcessPoolExecutor que estava
                  copiado em nwcConvert.run_workers e nwcInterpolate.run_workers
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import concurrent.futures
import os
import typing

# ─────────────────────────────────────────────────────────────────────────────────────
def resolve_worker_count(fi_configured_workers: int, fi_task_count: int) -> int:
    """
    quantos processos usar.

    :param fi_configured_workers: valor do config.ini (0 = CPUs − 1)
    :param fi_task_count: número de tarefas (> 0)
    :returns: entre 1 e fi_task_count
    """
    li_workers = fi_configured_workers

    if 0 == li_workers:
        # deixa uma CPU livre para o sistema (e para os outros estágios do cron)
        li_workers = max(1, (os.cpu_count() or 1) - 1)

    return min(li_workers, fi_task_count)

# ─────────────────────────────────────────────────────────────────────────────────────
def run_in_pool(fn_worker: typing.Callable,
                flst_tasks: list,
                fi_configured_workers: int,
                fn_on_lost: typing.Callable,
                fn_initializer: typing.Callable | None = None) -> list:
    """
    executa fn_worker(tarefa) para cada tarefa e devolve os resultados na
    mesma ordem das tarefas.

    :param fn_worker: função de nível de módulo (precisa ser "picklable"),
                      que recebe uma tarefa e devolve um resultado sem
                      levantar exceção
    :param flst_tasks: tarefas (objetos picklable)
    :param fi_configured_workers: [seção] workers do config.ini
    :param fn_on_lost: (tarefa, exceção) -> resultado de falha, chamada quando
                       o processo que rodava a tarefa morreu
    :param fn_initializer: chamada uma vez em cada processo novo (não é
                           chamada na execução em série)
    :returns: lista de resultados, um por tarefa
    """
    if not flst_tasks:
        return []

    li_workers = resolve_worker_count(fi_configured_workers, len(flst_tasks))

    if 1 == li_workers:
        return [fn_worker(l_task) for l_task in flst_tasks]

    llst_results = []

    with concurrent.futures.ProcessPoolExecutor(max_workers=li_workers,
                                                initializer=fn_initializer) as l_pool:
        llst_futures = [l_pool.submit(fn_worker, l_task) for l_task in flst_tasks]

        for l_task, l_future in zip(flst_tasks, llst_futures):
            try:
                llst_results.append(l_future.result())

            # o worker não levanta exceção: se o future falhou, o processo morreu
            except Exception as l_error:
                llst_results.append(fn_on_lost(l_task, l_error))

    return llst_results

# < the end >──────────────────────────────────────────────────────────────────────────
