# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwcConvert
converte os volumes observados (.mvol registrados pelo 21.mvol em volumes)
em CAPPI .npy, no preset ativo de cada alcance (nwc_cappi_cfg), gravando o
produto em cappi_products.

fluxo (convert_all), em três fases, como o volProcess do 21.mvol de onde veio:
  1) reserva serial (plan): numa transação de escrita,
     a. os PROCESSING presos que esgotaram as tentativas viram FAILED
        (_fail_stale_conversions);
     b. a fila é lida (db.volumes_to_convert);
     c. cada volume da fila é marcado observed PROCESSING
        (db.claim_conversion; a tentativa conta aqui, então um processo que
        morre no meio também gasta uma tentativa).
     Assim duas execuções simultâneas nunca pegam o mesmo volume;
  2) cômputo paralelo (run_workers -> worker_convert, via nwc_pool): leitura
     do .mvol, gridding, gravação atômica do .npy e estatísticas.  O worker
     não toca no banco nem em cf.G_CONFIG (tudo vem no SConvertTask) e nunca
     levanta exceção (a falha volta no SConvertResult);
  3) gravação serial (persist): uma transação com DONE/FAILED, radar_sites e
     processing_log; só depois do commit os .npy de sintéticos substituídos
     são apagados.

a fila (db.volumes_to_convert): volume com .mvol sem produto no preset
ativo, com um sintético (interpolado/previsto) no lugar -- o observado tem
prioridade e o substitui --, FAILED com tentativas sobrando ou PROCESSING
preso (processo morto).  Um FAILED que esgota [convert] max_retries fica no
banco e sai da fila; o .mvol nunca é apagado nem movido aqui (retenção:
21.mvol volPurge.py, que não apaga um volume pendente na view
cappi_pending).

saída: <cappi_dir>/<preset>/<R>_<A>/<R>_<A>_<timestamp>.npy, float32 com
três estados (nwc_presets): -9999 fora da cobertura do feixe, -32 coberto
sem eco, dBZ onde há eco.  A cobertura sai de um campo sintético gridado
junto com a refletividade (cp_core.add_coverage_field): a refletividade
sozinha não distingue "fora do alcance" de "sem eco", porque o Py-ART
mascara os gates abaixo do limiar.  valid_fraction = fração coberta,
rain_fraction = fração com eco > 0 dBZ.

    python3 apps/nwcConvert.py             # converte os pendentes
    python3 apps/nwcConvert.py --dry-run   # só lista a fila

códigos de retorno (contrato com o nwc_convert.sh): 0 ok, 3 algum volume
falhou (retentado no próximo ciclo), 2 erro fatal (esquema, banco); 1 é o
rc do próprio Python numa exceção não capturada (import, config.ini).

2026.out  mlabru  refatoração para legibilidade: persist/plan/worker_convert
                  divididos em funções de um passo só; pool em nwc_pool;
                  build_filename e save_cappi vindos de nwc_cappi_path e
                  nwc_atomic_io; banner do log por nwc_cli.logged_run

2026.out  mlabru  banco ocupado na reserva ("database is locked" no BEGIN
                  IMMEDIATE do plan(), além do busy_timeout) não é mais fatal:
                  nada foi reservado, então loga um aviso e sai com 0 -- o
                  próximo ciclo retenta.  A causa vista em produção era o
                  volScan do 21.mvol, que segurava o lock de escrita durante
                  o SHA1 dos .mvol novos (corrigido lá)
2026.out  mlabru  - ProcessPoolExecutor no lugar de mp.Pool.map: um worker morto
                    (OOM, segfault no Py-ART/h5py) travava o map para sempre,
                    com o flock preso e todo cron seguinte saindo com 0 ("já
                    em execução"); agora os volumes dele falham
                  - a tentativa conta na reserva (db.claim_conversion), e o
                    PROCESSING preso que esgotou max_retries vira FAILED
                    (db.fail_stale_conversions), com processing_log
                  - reserva perdida (outra execução retomou o volume como
                    preso e já o terminou): finish_conversion não muda nada
                    e o resultado desta execução é só logado
2026.out  mlabru  rc 3 (não mais 1) = algum volume falhou: 1 é o rc do Python
                  numa exceção fora do main (ImportError de Py-ART/h5py no
                  venv, config.ini ausente), que o nwc_convert.sh tratava
                  como rotineiro ([warn], exit 0) -- a conversão parava sem
                  alarme

2026.set  mlabru  - rc 2 para erro fatal (1 = algum volume falhou), para o
                    nwc_convert.sh distinguir o rotineiro do grave
                  - cobertura separada de eco (presets v2): campo de
                    cobertura no mesmo gridding, extract_level_coverage com
                    MF_NO_ECHO

2026.set  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import sqlite3
import sys

# numPy
import numpy as np

# cappi (núcleo CAPPI deste projeto; o 21.mvol o usa via symlink).  Os testes
# substituem funções de "cc" (mock.patch.object(nc.cc, ...)): sempre chame
# cc.read_radar etc. pelo módulo, nunca importe as funções soltas
import cappi.cp_core as cc

# locals
import work.nwc_atomic_io as aio
import work.nwc_cappi_cfg as pc
import work.nwc_cappi_path as cp
import work.nwc_cli as cli
import work.nwc_config as cf
import work.nwc_database as db
import work.nwc_pool as pool
import work.nwc_presets as ps

# < constants >────────────────────────────────────────────────────────────────────────

# códigos de retorno (contrato com o nwc_convert.sh).  1 fica de fora: é o
# rc do Python numa exceção não capturada (import, cf.init)
CI_RC_OK = 0
CI_RC_FATAL = 2
CI_RC_PARTIAL = 3

# caminho do .npy de um produto (nome mantido: os testes usam nc.build_filename)
build_filename = cp.build_product_filename

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# < SConvertTask >─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SConvertTask:
    """
    um volume a converter.  Pickle-safe (só tipos simples): é o que vai para
    o processo worker.
    """
    radar: str
    alcance: int
    # epoch UTC do volume (s)
    timestamp: int
    # caminho do .mvol
    fullpath: str
    # id do preset ativo do alcance (o worker confere com nwc_cappi_cfg)
    preset: str
    # raiz dos CAPPIs (absoluta)
    cappi_dir: str
    # .npy do sintético que este observado substitui (None se não havia)
    replaced_path: str | None = None

# < SConvertResult >───────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SConvertResult:
    """
    resultado da conversão de um volume, devolvido pelo worker.  Pickle-safe.
    Com success=False só task e message têm valor.
    """
    task: SConvertTask
    success: bool
    # .npy gravado
    cappi_path: str | None = None
    # frações de pixels (0–1): com eco > 0 dBZ e cobertos pelo feixe
    rain_fraction: float | None = None
    valid_fraction: float | None = None
    # posição da antena, lida do .mvol (vai para radar_sites)
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    # motivo da falha
    message: str = ""

# ═════════════════════════════════════════════════════════════════════════════════════
# orquestração
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def convert_all(fb_dry_run: bool = False) -> tuple[int, int]:
    """
    executa as três fases (com fb_dry_run, só lista a fila, sem reservar)

    :returns: (convertidos, falhas); no dry-run, (tamanho da fila, 0)
    """
    # 1) reserva (serial)
    try:
        llst_tasks = plan(fb_dry_run)

    # banco ocupado por outro escritor além do busy_timeout: nada foi
    # reservado (a transação sofreu rollback), o próximo ciclo retenta
    except sqlite3.OperationalError as l_error:
        if not _is_database_busy(l_error):
            raise

        M_LOG.warning("banco ocupado na reserva (%s); tenta no próximo ciclo", l_error)
        return 0, 0

    M_LOG.info("volumes a converter: %d", len(llst_tasks))

    if fb_dry_run:
        _print_queue(llst_tasks)
        return len(llst_tasks), 0

    # 2) cômputo (paralelo, sem banco)
    llst_results = run_workers(llst_tasks)

    # 3) gravação (serial)
    return persist(llst_results)

# ─────────────────────────────────────────────────────────────────────────────────────
def _print_queue(flst_tasks: list) -> None:
    """
    lista a fila no stdout (--dry-run), uma linha por volume
    """
    for l_task in flst_tasks:
        ls_mark = ""

        if l_task.replaced_path:
            ls_mark = "  [substitui sintético]"

        print(f"{l_task.radar}_{l_task.alcance} {l_task.timestamp:>10d} {l_task.preset:<9s} "
              f"{l_task.fullpath}{ls_mark}")

# ─────────────────────────────────────────────────────────────────────────────────────
def _is_database_busy(f_error: sqlite3.OperationalError) -> bool:
    """
    erro de lock (SQLITE_BUSY/SQLITE_LOCKED) e não outro OperationalError
    (tabela ausente, disco, ...), que continua fatal
    """
    ls_message = str(f_error).lower()

    return "locked" in ls_message or "busy" in ls_message

# ═════════════════════════════════════════════════════════════════════════════════════
# fase 1: reserva
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def plan(fb_dry_run: bool = False) -> list[SConvertTask]:
    """
    seleciona a fila e, fora do dry-run, reserva cada volume (PROCESSING)
    na mesma transação de escrita: duas execuções nunca pegam o mesmo volume

    :param fb_dry_run: só lê (transação de leitura, nada é reservado)
    :returns: tarefas, na ordem de timestamp
    :raises sqlite3.OperationalError: banco ocupado além do busy_timeout
    """
    if fb_dry_run:
        with db.transaction() as ldb:
            llst_rows = _read_queue(ldb)

    else:
        with db.transaction_write() as ldb:
            _fail_stale_conversions(ldb)

            llst_rows = _read_queue(ldb)

            for ldct_row in llst_rows:
                ldb.claim_conversion(ldct_row)

    return [_task_of(ldct_row) for ldct_row in llst_rows]

# ─────────────────────────────────────────────────────────────────────────────────────
def _read_queue(f_db: db.SDatabase) -> list[dict]:
    """
    volumes a converter (ver db.volumes_to_convert), com os limites do config
    """
    return f_db.volumes_to_convert(cf.G_CONFIG.convert_max_retries,
                                   cf.G_CONFIG.convert_stale_after_s)

# ─────────────────────────────────────────────────────────────────────────────────────
def _fail_stale_conversions(f_db: db.SDatabase) -> None:
    """
    PROCESSING preso que já esgotou as tentativas vira FAILED, com uma linha
    em processing_log: é o volume que derruba o processo toda vez
    """
    for ldct_row in f_db.fail_stale_conversions(cf.G_CONFIG.convert_max_retries,
                                                cf.G_CONFIG.convert_stale_after_s):
        ls_message = (f"{ldct_row['preset']}: PROCESSING preso após {ldct_row['attempts']} "
                      "tentativas (processo morto); FAILED")

        f_db.insert_log(ldct_row["radar"], ldct_row["alcance"], ldct_row["timestamp"],
                        db.CS_STEP_PROCESS, db.CS_LOG_FAILED, ls_message)

        M_LOG.error("%s_%d %d: %s", ldct_row["radar"], ldct_row["alcance"],
                    ldct_row["timestamp"], ls_message)

# ─────────────────────────────────────────────────────────────────────────────────────
def _task_of(fdct_row: dict) -> SConvertTask:
    """
    linha da fila -> SConvertTask (o que vai para o worker)
    """
    return SConvertTask(radar=fdct_row["radar"],
                        alcance=fdct_row["alcance"],
                        timestamp=fdct_row["timestamp"],
                        fullpath=fdct_row["fullpath"],
                        preset=fdct_row["preset"],
                        cappi_dir=str(cf.G_CONFIG.cappi_dir.resolve()),
                        replaced_path=_replaced_synthetic_path(fdct_row))

# ─────────────────────────────────────────────────────────────────────────────────────
def _replaced_synthetic_path(fdct_row: dict) -> str | None:
    """
    .npy do sintético que a conversão substitui, localizado pela regra do
    nome (nwc_cappi_path: o diretório gravado no banco não é usado); se o
    nome gravado não casar com a linha, usa o caminho gravado como está

    :returns: None se não há produto anterior ou ele não tem arquivo
    """
    if fdct_row["product_id"] is None or not fdct_row["product_path"]:
        return None

    lpth_preset_dir = cp.preset_dir(cf.G_CONFIG.cappi_dir, fdct_row["preset"])

    try:
        lpth_replaced = cp.resolve_cappi_path(fdct_row["product_path"], fdct_row["radar"],
                                              fdct_row["alcance"], fdct_row["timestamp"],
                                              lpth_preset_dir)

    except ValueError:
        return fdct_row["product_path"]

    return str(lpth_replaced)

# ═════════════════════════════════════════════════════════════════════════════════════
# fase 2: cômputo (processos worker)
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def run_workers(flst_tasks: list) -> list:
    """
    executa worker_convert para todas as tarefas.  [convert] workers:
    0 = CPUs − 1, 1 = serial (usado nos testes)

    :returns: um SConvertResult por tarefa, na mesma ordem
    """
    # worker_convert é lido aqui, na chamada: um monkeypatch nos testes vale
    return pool.run_in_pool(worker_convert, flst_tasks, cf.G_CONFIG.convert_workers,
                            _result_of_lost_worker)

# ─────────────────────────────────────────────────────────────────────────────────────
def _result_of_lost_worker(f_task: SConvertTask, f_error: Exception) -> SConvertResult:
    """
    resultado de falha para uma tarefa cujo processo worker morreu (OOM,
    segfault no Py-ART/h5py)
    """
    ls_message = f"worker perdido: {type(f_error).__name__}: {f_error}"

    M_LOG.error("%s: %s", pathlib.Path(f_task.fullpath).name, ls_message)

    return SConvertResult(task=f_task, success=False, message=ls_message)

# ─────────────────────────────────────────────────────────────────────────────────────
def worker_convert(f_task: SConvertTask) -> SConvertResult:
    """
    converte um volume.  Roda no worker: não toca no banco nem em
    cf.G_CONFIG (tudo vem na task), e nunca levanta exceção -- falhas voltam
    como SConvertResult(success=False).

    passos: confere o preset -> grida o .mvol -> grava o .npy -> estatísticas
    """
    try:
        l_preset = _checked_preset(f_task)
        l_radar, lnp_cappi = _grid_volume(pathlib.Path(f_task.fullpath), l_preset.cfg)

        lpth_output = build_filename(pathlib.Path(f_task.cappi_dir), f_task.preset, f_task.radar,
                                     f_task.alcance, f_task.timestamp)
        aio.save_npy(lnp_cappi.astype(np.float32), lpth_output)

        lf_rain, lf_valid = compute_statistics(lnp_cappi)

    except Exception as l_error:
        return SConvertResult(task=f_task, success=False,
                              message=f"{type(l_error).__name__}: {l_error}")

    return SConvertResult(task=f_task, success=True, cappi_path=str(lpth_output),
                          rain_fraction=lf_rain, valid_fraction=lf_valid,
                          latitude=float(l_radar.latitude["data"][0]),
                          longitude=float(l_radar.longitude["data"][0]),
                          altitude=float(l_radar.altitude["data"][0]))

# ─────────────────────────────────────────────────────────────────────────────────────
def _checked_preset(f_task: SConvertTask) -> pc.SCappiPreset:
    """
    preset ativo do alcance, conferido com o que a fila pediu (se o código
    mudou de preset entre a reserva e o cômputo, o volume falha)

    :raises ValueError: preset diferente
    """
    l_preset = pc.preset_for(f_task.alcance)

    if l_preset.id != f_task.preset:
        raise ValueError(f"preset da fila ({f_task.preset}) diferente do ativo ({l_preset.id})")

    return l_preset

# ─────────────────────────────────────────────────────────────────────────────────────
def _grid_volume(fpth_mvol: pathlib.Path, f_cappi_cfg: cc.CappiConfig) -> tuple:
    """
    lê o .mvol e extrai o CAPPI no nível do preset, com os três estados
    (-9999 sem cobertura, -32 sem eco, dBZ)

    a cobertura é gridada junto com a refletividade (mesma chamada, mesmos
    parâmetros), a partir de um campo sintético constante

    :returns: (radar Py-ART, CAPPI 2D)
    :raises FileNotFoundError: .mvol ausente
    """
    if not fpth_mvol.is_file():
        raise FileNotFoundError(f"arquivo não encontrado: {fpth_mvol}")

    l_radar = cc.read_radar(fpth_mvol, f_cappi_cfg.reader, f_cappi_cfg.field_name)

    ls_coverage_field = cc.add_coverage_field(l_radar)
    l_grid = cc.grid_radar(l_radar, f_cappi_cfg, (ls_coverage_field,))

    lnp_cappi = cc.extract_level_coverage(l_grid, f_cappi_cfg.field_name, f_cappi_cfg.z_min_m,
                                          ps.MF_NO_ECHO)

    return l_radar, lnp_cappi

# ─────────────────────────────────────────────────────────────────────────────────────
def compute_statistics(fnp_cappi: np.ndarray) -> tuple[float, float]:
    """
    (rain_fraction, valid_fraction) sem contar o -9999 como válido (ele é
    finito: cp_core.compute_statistics sozinho o contaria).
    valid = fração coberta (o -32 de "sem eco" conta), rain = fração com
    eco > 0 dBZ.

    (o nwcInterpolate tem a sua versão, com np.isclose; as duas dão o mesmo
    resultado nos arrays que cada estágio grava)
    """
    lnp_nodata_as_nan = np.where(fnp_cappi == ps.MF_NODATA, np.nan, fnp_cappi)

    lf_rain, lf_valid = cc.compute_statistics(lnp_nodata_as_nan)

    return float(lf_rain), float(lf_valid)

# ═════════════════════════════════════════════════════════════════════════════════════
# fase 3: gravação
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def persist(flst_results: list) -> tuple[int, int]:
    """
    grava os resultados numa única transação de escrita e, depois do commit,
    apaga os .npy de sintéticos substituídos que não foram sobrescritos

    :returns: (convertidos, falhas)
    """
    li_converted = 0
    li_failed = 0

    # arquivos a apagar só depois do commit
    llst_files_to_unlink = []

    with db.transaction_write() as ldb:
        for l_result in flst_results:
            if l_result.success:
                lb_recorded = _persist_success(ldb, l_result)

                # reserva perdida: o produto e o .npy são da outra execução
                if not lb_recorded:
                    continue

                li_converted += 1

            else:
                _persist_failure(ldb, l_result)
                li_failed += 1

            if _replaced_synthetic_is_orphan(l_result):
                llst_files_to_unlink.append(pathlib.Path(l_result.task.replaced_path))

    # commit feito: agora é seguro apagar
    for lpth_file in llst_files_to_unlink:
        lpth_file.unlink(missing_ok=True)

    return li_converted, li_failed

# ─────────────────────────────────────────────────────────────────────────────────────
def _persist_success(f_db: db.SDatabase, f_result: SConvertResult) -> bool:
    """
    produto DONE + radar_sites + processing_log SUCCESS

    :returns: False se a reserva foi perdida (outra execução retomou o
              volume como preso e já o terminou); nada é gravado nesse caso
    """
    l_task = f_result.task

    lb_finished = f_db.finish_conversion(l_task.radar, l_task.alcance, l_task.timestamp,
                                         l_task.preset, f_result.cappi_path,
                                         f_result.rain_fraction, f_result.valid_fraction)

    if not lb_finished:
        M_LOG.warning("%s: reserva perdida (retomado por outra execução); "
                      "resultado descartado", pathlib.Path(l_task.fullpath).name)
        return False

    f_db.upsert_radar_site(l_task.radar, f_result.latitude, f_result.longitude,
                           f_result.altitude)
    f_db.insert_log(l_task.radar, l_task.alcance, l_task.timestamp, db.CS_STEP_PROCESS,
                    db.CS_LOG_SUCCESS, f"{l_task.preset} {pathlib.Path(f_result.cappi_path).name}")

    return True

# ─────────────────────────────────────────────────────────────────────────────────────
def _persist_failure(f_db: db.SDatabase, f_result: SConvertResult) -> None:
    """
    produto FAILED + processing_log FAILED (a tentativa já foi contada na
    reserva; o volume volta à fila enquanto houver tentativas)
    """
    l_task = f_result.task

    li_attempts = f_db.fail_conversion(l_task.radar, l_task.alcance, l_task.timestamp,
                                       l_task.preset)
    f_db.insert_log(l_task.radar, l_task.alcance, l_task.timestamp, db.CS_STEP_PROCESS,
                    db.CS_LOG_FAILED, f"{l_task.preset}: {f_result.message}")

    M_LOG.error("%s falhou (%d/%d): %s", pathlib.Path(l_task.fullpath).name, li_attempts,
                cf.G_CONFIG.convert_max_retries, f_result.message)

# ─────────────────────────────────────────────────────────────────────────────────────
def _replaced_synthetic_is_orphan(f_result: SConvertResult) -> bool:
    """
    o .npy do sintético substituído deixou de ser usado?  Sim, a menos que o
    observado tenha sido gravado por cima dele (mesmo nome de arquivo)
    """
    ls_replaced_path = f_result.task.replaced_path

    if not ls_replaced_path:
        return False

    if f_result.cappi_path is None:
        return True

    return _comparable_path(ls_replaced_path) != _comparable_path(f_result.cappi_path)

# ─────────────────────────────────────────────────────────────────────────────────────
def _comparable_path(fs_path: str) -> pathlib.Path:
    """
    caminho com o diretório resolvido e o arquivo não (a forma que
    nwc_cappi_path usa), para comparar dois caminhos do mesmo .npy
    """
    lpth = pathlib.Path(fs_path)

    return lpth.absolute().parent.resolve() / lpth.name

# ═════════════════════════════════════════════════════════════════════════════════════
# linha de comando
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def main(flst_argv: list | None = None) -> int:
    """
    ponto de entrada do programa.

    :param flst_argv: argumentos (None = sys.argv)
    :returns: CI_RC_OK (0) = sucesso, CI_RC_PARTIAL (3) = algum volume falhou
              (registrado e retentado na próxima execução), CI_RC_FATAL (2)
              = erro fatal (esquema, banco)
    """
    l_parser = argparse.ArgumentParser(description="conversão .mvol -> CAPPI (cappi_products)")
    l_parser.add_argument("--dry-run", action="store_true",
                          help="só lista a fila (não reserva nem converte)")

    l_args = l_parser.parse_args(flst_argv)

    # o banco pode ter sido criado pelo 21.mvol, sem as tabelas do nowcast
    if not db.ensure_schema():
        return CI_RC_FATAL

    ls_title = "Convert (dry-run)" if l_args.dry_run else "Convert"

    with cli.logged_run(ls_title):
        try:
            li_converted, li_failed = convert_all(l_args.dry_run)

        except Exception:
            M_LOG.exception("erro fatal durante a conversão.")
            return CI_RC_FATAL

        if not l_args.dry_run:
            M_LOG.info("convertidos: %d, falhas: %d", li_converted, li_failed)

    if li_failed:
        return CI_RC_PARTIAL

    return CI_RC_OK

# ─────────────────────────────────────────────────────────────────────────────────────
# this is the bootstrap process

if "__main__" == __name__:

    # inicialização explícita via cf.init() antes de qualquer uso do módulo.
    cf.G_CONFIG = cf.init()

    sys.exit(main())

# < the end >──────────────────────────────────────────────────────────────────────────
