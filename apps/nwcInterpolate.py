# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwcInterpolate
preenche lacunas curtas das séries de CAPPI por interpolação no tempo.

o que é interpolado: lacunas de até [interpolate] max_gap slots com um
volume OBSERVADO de cada lado (tline.timeline.Timeline.gaps).  O resultado
é gravado como produto "interpolated" do preset ativo da série, em
<cappi_dir>/<preset>/<R>_<A>/<R>_<A>_<timestamp>.npy (fill −9999).
O nwcSequences nunca usa um interpolado como alvo.

como (interpolate_fields), com alpha = posição do slot entre os vizinhos
(0 = anterior, 1 = posterior), calculada pelos horários reais:
- method = flow (padrão): optical flow de Farnebäck do anterior para o
  posterior; o anterior é deslocado por alpha·flow, o posterior por
  −(1 − alpha)·flow, e só então os dois são misturados;
- method = linear: mistura direta, sem deslocamento (cross-fade);
- a mistura tem peso (1 − alpha, alpha), em Z linear (blend = z, padrão)
  ou em dBZ (blend = dbz).

fluxo (interpolate_all), em três fases, como no nwcConvert:
  1) plano serial (plan): lê o banco numa transação de leitura, monta as
     Timelines e decide, lacuna a lacuna, se interpola ou por que não
     (longa, sem_ancora, adiada, ocupada) -> SPlan;
  2) cômputo paralelo (run_workers -> worker_gap, via nwc_pool): os
     workers não tocam no SQLite nem em cf.G_CONFIG, e gravam o .npy com o
     sufixo .stage (staging_path), nunca com o nome final;
  3) gravação serial (persist): numa transação de escrita, INSERT do
     produto e só então rename do .stage para o nome final; apaga os
     sintéticos substituídos depois do commit.

--dry-run roda só o plano (o mesmo código do run real) e lista, sem gravar
nada, o que seria feito: resumo por série, arquivos a gerar, interpolados a
apagar e todas as lacunas, com o destino (interpolar, longa, sem_ancora,
adiada, ocupada); --timeline acrescenta a tabela compacta de cada série.  As
sequências que resultam daí: nwcSequences.py --dry-run.

    python3 apps/nwcInterpolate.py --dry-run
    python3 apps/nwcInterpolate.py --dry-run -s CGU-250 --timeline --from 2026-08-01

códigos de retorno: 0 ok, 1 erro fatal ou algum slot falhou.

2026.out  mlabru  refatoração para legibilidade: format_plan, persist, plan e
                  worker_gap divididos em funções de um passo só; filtros do
                  dry-run em nwc_cli.SListingFilter; pool em nwc_pool;
                  build_filename/save_cappi/datas vindos de nwc_cappi_path,
                  nwc_atomic_io e nwc_time; CF_NODATA = nwc_presets.MF_NODATA

2026.out  mlabru  lacuna "ocupada": slot no horário de um produto não DONE do
                  preset ativo (db.occupied_rows) é pulado.  O observado cuja
                  conversão falhou (FAILED) não entra na série nem está
                  pendente; com cadência regular o slot faltante cai
                  exatamente no horário dele e o INSERT dava IntegrityError a
                  cada ciclo (BVA_400 1790477193, rc 1 em todo cron).  O
                  IntegrityError que sobra (observado convertido depois do
                  plano) vira aviso e não conta como falha no rc
2026.out  mlabru  - o worker grava em <nome>.npy.stage e o persist só o promove
                    ao nome final depois do INSERT, dentro da transação.
                    Interpolado e observado têm o mesmo nome; antes o worker
                    sobrescrevia o .npy de um observado convertido depois do
                    plano no mesmo horário, e o IntegrityError apagava o
                    arquivo (produto DONE sem arquivo, ou com dado
                    interpolado).  Agora um IntegrityError só apaga o staging
                  - ProcessPoolExecutor no lugar de mp.Pool.map: um worker
                    morto (OOM, segfault no cv2) travava o map para sempre,
                    com o flock preso e todo cron seguinte saindo com 0 ("já
                    em execução"); agora as lacunas dele falham
                    (BrokenProcessPool).  cv2.setNumThreads(1) nos workers
                    (CPUs − 1 processos já ocupam a máquina)

2026.out  mlabru  dry-run: a seção "lacunas" lista todas as lacunas (início, fim,
                  slots, destino), inclusive as que vão ser interpoladas —
                  antes só as puladas, e as interpoladas apareciam apenas
                  como arquivos a gerar

2026.set  mlabru  cappi_products: interpolados gravados como produtos do preset
                  ativo (em <cappi_dir>/<preset>/), substituídos apagados de
                  lá; lacunas adiadas enquanto houver volume observado sem
                  produto final no preset (pending_rows), e não mais pelo
                  status NEW/PROCESSING de volumes
2026.set  mlabru  garante o esquema no início (db.ensure_schema), também no
                  dry-run: o banco pode ter sido criado pelo 21.mvol, sem as
                  tabelas que são só do nowcast

2026.set  mlabru  --dry-run: plan() devolve um SPlan com as lacunas NÃO
                  interpoladas e o motivo (longa, sem âncora, adiada); o run
                  real passa a logar o mesmo resumo por série — antes só
                  aparecia quantas lacunas foram interpoladas (no CGU-250,
                  95 lacunas de 3+ slots eram puladas em silêncio)

2026.set  mlabru  Farnebäck + média em Z como padrão.  Medido no CGU-400,
                  deixando um volume observado de fora (nwcInterpolBench.py):
                  60 janelas por lacuna (versão final deste módulo):
                    lacuna 1 (20 min)  MAE    CSI20  CSI35  área35  Δp99.5
                    linear dBZ         5.84   0.572  0.191   0.37   −2.85
                    linear Z           6.29   0.562  0.211   0.78   +0.04
                    flow dBZ           4.41   0.688  0.256   0.51   −1.98
                    flow Z             4.65   0.682  0.271   0.80   −0.26
                    lacuna 2 (30 min)
                    linear dBZ         6.46   0.532  0.144   0.29   −3.41
                    flow Z             6.41   0.552  0.213   0.72   −0.92
                  Com o linear, Z só troca o núcleo apagado (dBZ) por imagem
                  dupla com intensidade cheia; com o flow os dois campos já
                  chegam alinhados e Z preserva picos e núcleos (distribuição
                  de intensidades igual à dos observados, que é o que importa
                  para treino) a +0,2 dB de MAE.  Com vizinhos a 30 min a
                  vantagem do flow quase some — daí max_gap pequeno.
                  Do núcleo antigo (apps/.bkp/nc_interpolate.py), corrigido:
                - −9999 entrava no cv2.remap bilinear e se misturava com os
                  vizinhos na borda da cobertura (494 pixels entre −9920 e
                  −100 num CAPPI com máscara de 250 km); agora vira NaN antes
                - forward/backward eram misturados 50/50 para qualquer alpha;
                  agora o peso é (1 − alpha, alpha)
                - saída NaN -> fill −9999 (contrato do .npy)
                - remap com borda NaN apagava a última linha/coluna de todo
                  CAPPI (NaN·0 no bilinear); agora BORDER_REPLICATE
                - cobertura do resultado = união da dos vizinhos (o flow não
                  inventa eco fora do alcance do radar); pixel sem dado após
                  o warp dentro da cobertura cai para a mistura direta
                - normalização da imagem do flow min-max por quadro -> escala
                  fixa 0–60 dBZ (sem diferença medida, 4.48 x 4.47, mas os dois
                  quadros ficam na mesma escala e a imagem constante não zera)

2026.set  mlabru  initial version (Linux/Python), substitui o interpolate.py
                  (Farnebäck, lacunas de até 12 slots, grade fixa de 600 s)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import argparse
import dataclasses
import datetime
import logging
import pathlib
import sqlite3
import sys
import typing

# numPy
import numpy as np

# openCV
import cv2

# locals
import work.nwc_atomic_io as aio
import work.nwc_cappi_path as cp
import work.nwc_cli as cli
import work.nwc_config as cf
import work.nwc_database as db
import work.nwc_pool as pool
import work.nwc_presets as ps
import work.nwc_series as ns
import work.nwc_time as nt

import tline.slot_status as ss
import tline.tl_collection as tc
import tline.timeline as tl

# < constants >────────────────────────────────────────────────────────────────────────

# valor do pixel sem dado.  É finito: np.isfinite sozinho não o reconhece
# como inválido (ver valid_mask)
CF_NODATA = ps.MF_NODATA

# escala da imagem uint8 usada só para estimar o flow (dBZ -> 0..255): fixa e
# igual para os dois quadros; abaixo de 0 dBZ é céu limpo/ruído
CT_FLOW_RANGE_DBZ = (0.0, 60.0)

# motivo de uma lacuna adiada (os demais vêm de tline.timeline.CS_GAP_*):
# há um volume observado dentro dela ainda sem produto final
CS_GAP_DEFERRED = "adiada"
# motivo de uma lacuna com slot no horário de um produto não DONE (ex: o
# observado cuja conversão falhou): o INSERT daria IntegrityError
CS_GAP_OCCUPIED = "ocupada"
# destino, no dry-run, de uma lacuna que vai ser interpolada
CS_GAP_TO_INTERPOLATE = "interpolar"

# todos os motivos de "não interpolar", na ordem do resumo por série
CT_SKIP_REASONS = (tl.CS_GAP_LONG, tl.CS_GAP_NO_ANCHOR, CS_GAP_DEFERRED, CS_GAP_OCCUPIED)

# resultado da gravação de um slot (_persist_slot)
CS_SLOT_INTERPOLATED = "interpolado"
CS_SLOT_FAILED = "falhou"
CS_SLOT_ALREADY_EXISTS = "já existe"

# parâmetros do Farnebäck — os do interpolador antigo (pirâmide de 3 níveis,
# janela 25×25), com os quais a tabela do changelog foi medida
CDCT_FARNEBACK = { "pyr_scale": 0.5,
                   "levels": 3,
                   "winsize": 25,
                   "iterations": 5,
                   "poly_n": 7,
                   "poly_sigma": 1.5,
                   "flags": 0 }

# formato de instante nas listagens do dry-run
CS_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# caminho do .npy de um produto (nome mantido: testes e nwcSequences usam
# ni.build_filename)
build_filename = cp.build_product_filename

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# < SGapTask >─────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SGapTask:
    """
    uma lacuna a interpolar.  Pickle-safe (só tipos simples): é o que vai
    para o processo worker.
    """
    radar: str
    alcance: int
    # id do preset dos vizinhos e dos interpolados
    preset: str
    # CAPPIs observados vizinhos
    previous_path: str
    next_path: str
    # (timestamp epoch, alpha) de cada slot faltante
    slots: tuple
    # raiz dos CAPPIs
    cappi_dir: str
    # [interpolate] method / blend (o worker não lê o config)
    method: str = "flow"
    blend: str = "z"

    # ---------------------------------------------------------------------------------
    @property
    def product_name(self) -> str:
        """
        nome da série, ex.: "CGU-250"
        """
        return f"{self.radar}-{self.alcance}"

# < SSlotResult >──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SSlotResult:
    """
    resultado de um slot interpolado, devolvido pelo worker ao processo
    principal.  Pickle-safe.  Com success=False só a chave e message têm
    valor.
    """
    radar: str
    alcance: int
    preset: str
    timestamp: int
    success: bool
    # nome FINAL do .npy (o worker gravou em staging_path(cappi_path))
    cappi_path: str | None = None
    rain_fraction: float | None = None
    valid_fraction: float | None = None
    message: str = ""

# < SPlan >────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class SPlan:
    """
    resultado de plan(): o que o run vai fazer e o que vai deixar de fazer
    """
    # instante da leitura do banco (UTC)
    read_at: datetime.datetime
    # séries
    collection: tc.TimelineCollection
    # lacunas a interpolar (SGapTask)
    tasks: list
    # sintéticos que perderam o slot para um observado (serão apagados)
    superseded: list
    # (ProductKey, SGap, motivo) das lacunas que não serão interpoladas
    skipped: list

    # ---------------------------------------------------------------------------------
    def summary_lines(self) -> list[str]:
        """
        uma linha por série: slots, origens, lacunas por destino, substituídos
        """
        llst_lines = []

        for l_product, l_timeline in self.collection:
            llst_tasks = self._tasks_of(l_product)
            ldct_skips = self._count_skips_by_reason(l_product)
            li_superseded = sum(1 for l_record in self.superseded
                                if l_record.product == l_product)
            li_task_slots = sum(len(l_task.slots) for l_task in llst_tasks)

            llst_lines.append(
                f"{str(l_product):<8s} slots {len(l_timeline):>6d} | "
                f"obs {l_timeline.count_of(ss.SlotStatus.OBSERVED):>5d} "
                f"int {l_timeline.count_of(ss.SlotStatus.INTERPOLATED):>4d} "
                f"falt {l_timeline.count_of(ss.SlotStatus.MISSING):>6d} | "
                f"lacunas: interpolar {len(llst_tasks):>3d} ({li_task_slots} slots), "
                f"longas {ldct_skips[tl.CS_GAP_LONG]}, "
                f"sem âncora {ldct_skips[tl.CS_GAP_NO_ANCHOR]}, "
                f"adiadas {ldct_skips[CS_GAP_DEFERRED]}, "
                f"ocupadas {ldct_skips[CS_GAP_OCCUPIED]} | substituir {li_superseded}")

        return llst_lines

    # ---------------------------------------------------------------------------------
    def _tasks_of(self, f_product) -> list:
        """
        tarefas de uma série
        """
        return [l_task for l_task in self.tasks
                if (l_task.radar, l_task.alcance) == (f_product.station, f_product.range_km)]

    # ---------------------------------------------------------------------------------
    def _count_skips_by_reason(self, f_product) -> dict:
        """
        lacunas puladas de uma série, contadas por motivo (todos os motivos
        aparecem, com 0 se não houver)
        """
        ldct_counts = {ls_reason: 0 for ls_reason in CT_SKIP_REASONS}

        for l_skipped_product, _, ls_reason in self.skipped:
            if l_skipped_product == f_product:
                ldct_counts[ls_reason] += 1

        return ldct_counts

# < SGapListingRow >───────────────────────────────────────────────────────────────────

class SGapListingRow(typing.NamedTuple):
    """
    uma linha da seção "lacunas" do dry-run.  É uma tupla: ordenar uma
    lista delas ordena por série, depois início, fim, ...
    """
    product: str
    first_epoch: int
    last_epoch: int
    slot_count: int
    destination: str

# ═════════════════════════════════════════════════════════════════════════════════════
# orquestração
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def interpolate_all() -> tuple[int, int, int]:
    """
    executa as três fases.

    :returns: (interpolados, falhas, sintéticos substituídos por observados)
    """
    # 1) plano (serial, só leitura)
    l_plan = plan()

    for ls_line in l_plan.summary_lines():
        M_LOG.info(ls_line)

    # 2) cômputo (paralelo, sem banco)
    llst_results = run_workers(l_plan.tasks)

    # 3) gravação (serial)
    try:
        return persist(llst_results, l_plan.superseded)

    finally:
        # staging que sobrou (transação abortada): o persist já promoveu ou
        # apagou os outros
        for l_result in llst_results:
            if l_result.success:
                staging_path(pathlib.Path(l_result.cappi_path)).unlink(missing_ok=True)

# ═════════════════════════════════════════════════════════════════════════════════════
# fase 1: plano
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def plan() -> SPlan:
    """
    lê o banco e decide, lacuna a lacuna, o que interpolar.

    uma lacuna interpolável ainda pode ser pulada:
    - adiada: contém um volume observado ainda sem produto final no preset
      ativo (db.pending_rows).  A conversão roda em paralelo e esse volume
      vai virar produto em breve; interpolar agora só geraria um sintético
      para ser substituído na próxima execução;
    - ocupada: um slot cai no horário de um produto não DONE
      (db.occupied_rows, ex.: observado com conversão FAILED); o INSERT
      falharia a cada ciclo.

    :returns: SPlan (tasks, substituídos e lacunas puladas com o motivo)
    """
    ldt_read_at = datetime.datetime.now(datetime.timezone.utc)

    llst_series_rows, ldct_pending, ldct_occupied = _read_plan_inputs()

    l_collection = ns.build_collection(llst_series_rows)

    llst_tasks = []
    llst_superseded = []
    llst_skipped = []

    for l_product, l_timeline in l_collection:
        llst_superseded.extend(l_timeline.superseded)

        ltpl_series = (l_product.station, l_product.range_km)
        llst_pending_epochs = ldct_pending.get(ltpl_series, [])
        lset_occupied_epochs = ldct_occupied.get(ltpl_series, set())

        for l_gap in l_timeline.gaps():
            ls_skip_reason = _skip_reason(l_gap, llst_pending_epochs, lset_occupied_epochs)

            if ls_skip_reason is not None:
                llst_skipped.append((l_product, l_gap, ls_skip_reason))
                continue

            llst_tasks.append(_make_gap_task(l_product, l_gap))

    return SPlan(read_at=ldt_read_at, collection=l_collection, tasks=llst_tasks,
                 superseded=llst_superseded, skipped=llst_skipped)

# ─────────────────────────────────────────────────────────────────────────────────────
def _read_plan_inputs() -> tuple[list, dict, dict]:
    """
    tudo o que o plano lê do banco, numa única transação de leitura

    :returns: (linhas das séries,
               {(radar, alcance): [epochs pendentes]},
               {(radar, alcance): {epochs ocupados}})
    """
    ldct_pending: dict = {}
    ldct_occupied: dict = {}

    with db.transaction() as ldb:
        llst_series_rows = ldb.series_rows()

        for ldct_row in ldb.pending_rows():
            ltpl_series = (ldct_row["radar"], ldct_row["alcance"])
            ldct_pending.setdefault(ltpl_series, []).append(ldct_row["timestamp"])

        for ldct_row in ldb.occupied_rows():
            ltpl_series = (ldct_row["radar"], ldct_row["alcance"])
            ldct_occupied.setdefault(ltpl_series, set()).add(ldct_row["timestamp"])

    return llst_series_rows, ldct_pending, ldct_occupied

# ─────────────────────────────────────────────────────────────────────────────────────
def _skip_reason(f_gap: tl.SGap, flst_pending_epochs: list,
                 fset_occupied_epochs: set) -> str | None:
    """
    por que a lacuna NÃO será interpolada

    :returns: motivo (longa, sem_ancora, adiada, ocupada) ou None se ela
              deve ser interpolada
    """
    if not f_gap.is_interpolatable:
        return f_gap.reason

    li_previous_epoch = f_gap.previous.epoch
    li_next_epoch = f_gap.next.epoch

    for li_pending_epoch in flst_pending_epochs:
        if li_previous_epoch < li_pending_epoch < li_next_epoch:
            return CS_GAP_DEFERRED

    for l_slot in f_gap.missing:
        if l_slot.epoch in fset_occupied_epochs:
            return CS_GAP_OCCUPIED

    return None

# ─────────────────────────────────────────────────────────────────────────────────────
def _make_gap_task(f_product, f_gap: tl.SGap) -> SGapTask:
    """
    lacuna interpolável -> SGapTask (o que vai para o worker), com o
    (epoch, alpha) de cada slot faltante e o método do config
    """
    ltpl_slots = tuple((l_slot.epoch, f_gap.alpha(l_slot)) for l_slot in f_gap.missing)

    return SGapTask(radar=f_product.station,
                    alcance=f_product.range_km,
                    preset=ps.active_id(f_product.range_km),
                    previous_path=str(f_gap.previous.cappi_path),
                    next_path=str(f_gap.next.cappi_path),
                    slots=ltpl_slots,
                    cappi_dir=str(cf.G_CONFIG.cappi_dir),
                    method=cf.G_CONFIG.method,
                    blend=cf.G_CONFIG.blend)

# ═════════════════════════════════════════════════════════════════════════════════════
# fase 2: cômputo (processos worker)
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def run_workers(flst_tasks: list) -> list:
    """
    executa worker_gap para todas as lacunas.  [interpolate] workers:
    0 = CPUs − 1, 1 = serial (usado nos testes)

    :returns: lista única de SSlotResult (todos os slots de todas as lacunas)
    """
    # worker_gap é lido aqui, na chamada: um monkeypatch nos testes vale
    llst_results_per_gap = pool.run_in_pool(worker_gap, flst_tasks, cf.G_CONFIG.workers,
                                            _results_of_lost_worker, init_worker)

    llst_slot_results = []

    for llst_gap_results in llst_results_per_gap:
        llst_slot_results.extend(llst_gap_results)

    return llst_slot_results

# ─────────────────────────────────────────────────────────────────────────────────────
def init_worker() -> None:
    """
    initializer dos processos worker: um thread de OpenCV por processo (o
    pool já usa CPUs − 1 processos)
    """
    cv2.setNumThreads(1)

# ─────────────────────────────────────────────────────────────────────────────────────
def _results_of_lost_worker(f_task: SGapTask, f_error: Exception) -> list:
    """
    resultado de falha para uma lacuna cujo processo worker morreu (OOM,
    segfault no cv2)
    """
    ls_message = f"worker perdido: {type(f_error).__name__}: {f_error}"

    M_LOG.error("%s_%d: %s", f_task.radar, f_task.alcance, ls_message)

    return failed_slots(f_task, ls_message)

# ─────────────────────────────────────────────────────────────────────────────────────
def failed_slots(f_task: SGapTask, fs_message: str) -> list:
    """
    todos os slots de uma lacuna como falha, com a mesma mensagem
    """
    return [SSlotResult(f_task.radar, f_task.alcance, f_task.preset, li_timestamp, False,
                        message=fs_message)
            for li_timestamp, _ in f_task.slots]

# ─────────────────────────────────────────────────────────────────────────────────────
def worker_gap(f_task: SGapTask) -> list:
    """
    interpola todos os slots de uma lacuna.  Roda no worker: não toca no
    banco nem em cf.G_CONFIG (tudo vem na task), e nunca levanta exceção —
    falhas voltam como SSlotResult(success=False).

    :returns: um SSlotResult por slot de f_task.slots
    """
    try:
        lnp_previous, lnp_next, lnp_flow = _load_neighbors(f_task)

    except Exception as l_error:
        # sem os vizinhos, todos os slots da lacuna falham juntos
        return failed_slots(f_task, f"leitura dos vizinhos/flow: {l_error}")

    llst_results = []

    for li_timestamp, lf_alpha in f_task.slots:
        llst_results.append(_interpolate_slot(f_task, lnp_previous, lnp_next, lnp_flow,
                                              li_timestamp, lf_alpha))

    return llst_results

# ─────────────────────────────────────────────────────────────────────────────────────
def _load_neighbors(f_task: SGapTask) -> tuple:
    """
    lê os dois CAPPIs vizinhos e, com method = flow, calcula o flow uma vez
    por lacuna (ele é reaproveitado em todos os slots)

    :returns: (anterior, posterior, flow ou None)
    """
    lnp_previous = load_cappi(pathlib.Path(f_task.previous_path))
    lnp_next = load_cappi(pathlib.Path(f_task.next_path))

    lnp_flow = None

    if "flow" == f_task.method:
        lnp_flow = compute_flow(to_nan(lnp_previous), to_nan(lnp_next))

    return lnp_previous, lnp_next, lnp_flow

# ─────────────────────────────────────────────────────────────────────────────────────
def _interpolate_slot(f_task: SGapTask, fnp_previous: np.ndarray, fnp_next: np.ndarray,
                      fnp_flow: np.ndarray | None, fi_timestamp: int,
                      ff_alpha: float) -> SSlotResult:
    """
    interpola um slot, grava o .npy no staging e calcula as estatísticas.
    Nunca levanta exceção.
    """
    try:
        lnp_cappi = interpolate_fields(fnp_previous, fnp_next, ff_alpha,
                                       f_task.method, f_task.blend, fnp_flow)

        lpth_output = build_filename(pathlib.Path(f_task.cappi_dir), f_task.preset,
                                     f_task.radar, f_task.alcance, fi_timestamp)

        # staging: o persist promove ao nome final depois do INSERT
        aio.save_npy(lnp_cappi, staging_path(lpth_output))

        lf_rain, lf_valid = compute_statistics(lnp_cappi)

    except Exception as l_error:
        return SSlotResult(f_task.radar, f_task.alcance, f_task.preset, fi_timestamp, False,
                           message=str(l_error))

    return SSlotResult(f_task.radar, f_task.alcance, f_task.preset, fi_timestamp, True,
                       cappi_path=str(lpth_output),
                       rain_fraction=lf_rain,
                       valid_fraction=lf_valid)

# ─────────────────────────────────────────────────────────────────────────────────────
def staging_path(fpth_filename: pathlib.Path) -> pathlib.Path:
    """
    onde o worker grava o .npy (<nome>.npy.stage): o nome final é o mesmo de
    um observado no mesmo horário, e só o persist, depois do INSERT, sabe se
    o arquivo final é do interpolado
    """
    return fpth_filename.with_name(fpth_filename.name + ".stage")

# ─────────────────────────────────────────────────────────────────────────────────────
def load_cappi(fpth_filename: pathlib.Path) -> np.ndarray:
    """
    carrega um CAPPI .npy

    :raises ValueError: o array não é 2D
    """
    lnp_data = np.load(fpth_filename, allow_pickle=False)

    if 2 != lnp_data.ndim:
        raise ValueError(f"CAPPI não é 2D: {fpth_filename} {lnp_data.shape}")

    return lnp_data

# ─────────────────────────────────────────────────────────────────────────────────────
def compute_statistics(fnp_cappi: np.ndarray) -> tuple[float, float]:
    """
    (rain_fraction, valid_fraction), sem contar o fill como válido — mesma
    definição do nwcConvert: valid = fração com dado (valid_mask), rain =
    fração com dado e > 0 dBZ
    """
    lnp_valid = valid_mask(fnp_cappi)
    lnp_rain = lnp_valid & (fnp_cappi > 0.0)

    return float(lnp_rain.mean()), float(lnp_valid.mean())

# ═════════════════════════════════════════════════════════════════════════════════════
# núcleo numérico da interpolação
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def interpolate_fields(fnp_previous: np.ndarray, fnp_next: np.ndarray, ff_alpha: float,
                       fs_method: str = "flow", fs_blend: str = "z",
                       fnp_flow: np.ndarray | None = None) -> np.ndarray:
    """
    campo no instante alpha ∈ (0, 1) entre previous (0) e next (1).

    method = "flow": previous deslocado por alpha·flow, next por
    −(1 − alpha)·flow, e os dois misturados com peso (1 − alpha, alpha).
    method = "linear": mistura direta (flow nulo).

    :param fnp_flow: flow já calculado (worker_gap o reaproveita para todos os
                     slots de uma lacuna); None = calcula aqui
    :returns: float32 com fill −9999 (contrato do .npy)
    :raises ValueError: dimensões diferentes, alpha fora de (0, 1) ou
                        method/blend desconhecidos
    """
    if fnp_previous.shape != fnp_next.shape:
        raise ValueError(f"CAPPIs com dimensões diferentes: {fnp_previous.shape} x {fnp_next.shape}")

    if not 0.0 < ff_alpha < 1.0:
        raise ValueError(f"alpha fora de (0, 1): {ff_alpha}")

    if fs_method not in ("flow", "linear"):
        raise ValueError(f"method inválido: {fs_method!r}")

    lnp_previous_nan = to_nan(fnp_previous)
    lnp_next_nan = to_nan(fnp_next)

    # mistura direta: é o resultado do linear e o fallback do flow
    lnp_output = blend_fields(lnp_previous_nan, lnp_next_nan, ff_alpha, fs_blend)

    if "flow" == fs_method:
        if fnp_flow is None:
            fnp_flow = compute_flow(lnp_previous_nan, lnp_next_nan)

        _apply_motion(lnp_output, lnp_previous_nan, lnp_next_nan, fnp_flow, ff_alpha, fs_blend)

    # NaN -> fill
    lnp_output[~np.isfinite(lnp_output)] = CF_NODATA

    return lnp_output

# ─────────────────────────────────────────────────────────────────────────────────────
def _apply_motion(fnp_output: np.ndarray, fnp_previous_nan: np.ndarray,
                  fnp_next_nan: np.ndarray, fnp_flow: np.ndarray, ff_alpha: float,
                  fs_blend: str) -> None:
    """
    troca, em fnp_output (a mistura direta), os pixels pela mistura dos
    vizinhos deslocados pelo flow.  Altera fnp_output no lugar.

    só entram pixels com dado nas duas versões: a cobertura do resultado
    continua sendo a união da cobertura dos vizinhos (fora dela o flow
    advectaria eco para onde o radar não vê), e um pixel que o warp deixou
    sem dado (NaN vizinho no remap bilinear) fica com a mistura direta
    """
    lnp_previous_moved = warp(fnp_previous_nan, fnp_flow, ff_alpha)
    lnp_next_moved = warp(fnp_next_nan, -fnp_flow, 1.0 - ff_alpha)

    lnp_moved_blend = blend_fields(lnp_previous_moved, lnp_next_moved, ff_alpha, fs_blend)

    lnp_take_moved = np.isfinite(fnp_output) & np.isfinite(lnp_moved_blend)
    fnp_output[lnp_take_moved] = lnp_moved_blend[lnp_take_moved]

# ─────────────────────────────────────────────────────────────────────────────────────
def interpolate_linear(fnp_previous: np.ndarray, fnp_next: np.ndarray, ff_alpha: float,
                       fs_blend: str = "dbz") -> np.ndarray:
    """
    mistura direta, sem deslocamento: interpolate_fields com method="linear"
    """
    return interpolate_fields(fnp_previous, fnp_next, ff_alpha, "linear", fs_blend)

# ─────────────────────────────────────────────────────────────────────────────────────
def blend_fields(fnp_a: np.ndarray, fnp_b: np.ndarray, ff_alpha: float,
                 fs_blend: str) -> np.ndarray:
    """
    (1 − alpha)·a + alpha·b, pixel a pixel, em Z linear ou em dBZ.

    entradas com NaN nos pixels sem dado (ver to_nan).  Política:
    - válido nos dois: média ponderada
    - válido em um só: copia o valor válido (borda da cobertura, ou pixel
      que o warp trouxe de fora da imagem)
    - inválido nos dois: NaN

    :param fs_blend: "z" (média de 10^(dBZ/10), volta para dBZ) ou "dbz"
    :returns: float32 com NaN nos pixels sem dado
    :raises ValueError: blend desconhecido
    """
    if fs_blend not in ("z", "dbz"):
        raise ValueError(f"blend inválido: {fs_blend!r}")

    lnp_valid_a = np.isfinite(fnp_a)
    lnp_valid_b = np.isfinite(fnp_b)

    lnp_output = np.full(fnp_a.shape, np.nan, dtype=np.float32)

    lnp_both = lnp_valid_a & lnp_valid_b

    if "z" == fs_blend:
        # Z = 10^(dBZ/10) cabe em float64 com folga; o piso de −30 dBZ vira 1e-3
        lnp_z_a = np.power(10.0, fnp_a[lnp_both] / 10.0, dtype=np.float64)
        lnp_z_b = np.power(10.0, fnp_b[lnp_both] / 10.0, dtype=np.float64)

        lnp_output[lnp_both] = 10.0 * np.log10((1.0 - ff_alpha) * lnp_z_a + ff_alpha * lnp_z_b)

    else:
        lnp_output[lnp_both] = (1.0 - ff_alpha) * fnp_a[lnp_both] + ff_alpha * fnp_b[lnp_both]

    lnp_only_a = lnp_valid_a & ~lnp_valid_b
    lnp_only_b = ~lnp_valid_a & lnp_valid_b

    lnp_output[lnp_only_a] = fnp_a[lnp_only_a]
    lnp_output[lnp_only_b] = fnp_b[lnp_only_b]

    return lnp_output

# ─────────────────────────────────────────────────────────────────────────────────────
def compute_flow(fnp_previous: np.ndarray, fnp_next: np.ndarray) -> np.ndarray:
    """
    optical flow de Farnebäck do anterior para o posterior

    :param fnp_previous, fnp_next: campos em dBZ com NaN nos pixels sem dado
    :returns: (H, W, 2) deslocamento (dx, dy) em pixels, do anterior ao posterior
    """
    return cv2.calcOpticalFlowFarneback(prepare_flow_image(fnp_previous),
                                        prepare_flow_image(fnp_next),
                                        None, **CDCT_FARNEBACK)

# ─────────────────────────────────────────────────────────────────────────────────────
def prepare_flow_image(fnp_field: np.ndarray) -> np.ndarray:
    """
    campo em dBZ (NaN = sem dado) -> imagem uint8 para o Farnebäck, na escala
    fixa CT_FLOW_RANGE_DBZ; sem dado conta como céu limpo (0)
    """
    lf_low_dbz, lf_high_dbz = CT_FLOW_RANGE_DBZ

    lnp_field = np.where(np.isfinite(fnp_field), fnp_field, lf_low_dbz)
    lnp_scaled = np.clip((lnp_field - lf_low_dbz) / (lf_high_dbz - lf_low_dbz), 0.0, 1.0)

    return (255.0 * lnp_scaled).astype(np.uint8)

# ─────────────────────────────────────────────────────────────────────────────────────
def to_nan(fnp_cappi: np.ndarray) -> np.ndarray:
    """
    cópia float32 com NaN nos pixels sem dado (fill −9999 ou não finitos).
    Tem de vir antes do warp: o remap bilinear misturaria o −9999 com os
    vizinhos, e NaN ao menos se propaga como "sem dado".
    """
    lnp_output = np.array(fnp_cappi, dtype=np.float32)
    lnp_output[~valid_mask(lnp_output)] = np.nan

    return lnp_output

# ─────────────────────────────────────────────────────────────────────────────────────
def valid_mask(fnp_cappi: np.ndarray) -> np.ndarray:
    """
    pixels com dado: finitos e diferentes do fill −9999
    """
    return np.isfinite(fnp_cappi) & ~np.isclose(fnp_cappi, CF_NODATA)

# ─────────────────────────────────────────────────────────────────────────────────────
def warp(fnp_field: np.ndarray, fnp_flow: np.ndarray, ff_factor: float) -> np.ndarray:
    """
    advecção: desloca o campo por ff_factor·flow (amostragem reversa, com o
    flow avaliado no pixel de destino — aproximação de primeira ordem, boa
    para os 5–15 px por intervalo observados).

    BORDER_REPLICATE e não BORDER_CONSTANT com NaN (como no interpolador
    antigo): o remap bilinear lê o vizinho de fora da imagem com peso 0 na
    última linha/coluna, e NaN·0 = NaN — a borda direita/inferior de todo
    CAPPI saía sem dado, mesmo com flow nulo.

    :param fnp_field: dBZ com NaN nos pixels sem dado (ver to_nan)
    """
    li_height, li_width = fnp_field.shape

    lnp_x, lnp_y = np.meshgrid(np.arange(li_width, dtype=np.float32),
                               np.arange(li_height, dtype=np.float32))

    return cv2.remap(fnp_field,
                     lnp_x - ff_factor * fnp_flow[..., 0],
                     lnp_y - ff_factor * fnp_flow[..., 1],
                     interpolation=cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)

# ═════════════════════════════════════════════════════════════════════════════════════
# fase 3: gravação
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def persist(flst_results: list, flst_superseded: list) -> tuple[int, int, int]:
    """
    grava os resultados no banco numa única transação de escrita, e só
    depois do commit apaga os .npy que deixaram de ser referenciados

    :returns: (interpolados, falhas, sintéticos substituídos)
    """
    li_interpolated = 0
    li_failed = 0

    # arquivos a apagar só depois do commit
    llst_files_to_unlink = []

    with db.transaction_write() as ldb:
        li_superseded = _delete_superseded(ldb, flst_superseded, llst_files_to_unlink)

        for l_result in flst_results:
            ls_outcome = _persist_slot(ldb, l_result, llst_files_to_unlink)

            if CS_SLOT_INTERPOLATED == ls_outcome:
                li_interpolated += 1

            elif CS_SLOT_FAILED == ls_outcome:
                li_failed += 1

            # CS_SLOT_ALREADY_EXISTS é só um aviso: não conta como falha

    # commit feito: agora é seguro apagar
    for lpth_file in llst_files_to_unlink:
        pathlib.Path(lpth_file).unlink(missing_ok=True)

    return li_interpolated, li_failed, li_superseded

# ─────────────────────────────────────────────────────────────────────────────────────
def _delete_superseded(f_db: db.SDatabase, flst_superseded: list,
                       flst_files_to_unlink: list) -> int:
    """
    apaga as linhas dos interpolados que perderam o slot para um observado
    atrasado; os arquivos entram em flst_files_to_unlink (apagados após o
    commit)

    :returns: quantos foram apagados
    """
    li_deleted = 0

    for l_record in flst_superseded:
        li_alcance = l_record.product.range_km

        lb_deleted = f_db.delete_product(l_record.product.station, li_alcance, l_record.epoch,
                                         ps.active_id(li_alcance))

        if lb_deleted:
            flst_files_to_unlink.append(l_record.cappi_path)
            li_deleted += 1

    return li_deleted

# ─────────────────────────────────────────────────────────────────────────────────────
def _persist_slot(f_db: db.SDatabase, f_result: SSlotResult, flst_files_to_unlink: list) -> str:
    """
    grava um slot: INSERT do produto, rename staging -> nome final e linha
    no processing_log.

    :returns: CS_SLOT_INTERPOLATED, CS_SLOT_FAILED ou CS_SLOT_ALREADY_EXISTS
    """
    if not f_result.success:
        _log_slot_failure(f_db, f_result, f_result.message)
        return CS_SLOT_FAILED

    lpth_final = pathlib.Path(f_result.cappi_path)
    lpth_staging = staging_path(lpth_final)

    try:
        f_db.insert_interpolated(f_result.radar, f_result.alcance, f_result.timestamp,
                                 f_result.preset, f_result.cappi_path,
                                 f_result.rain_fraction, f_result.valid_fraction)

    # chave (radar, alcance, timestamp, preset) já existe: um observado
    # convertido depois do plano tem exatamente esse horário, e o arquivo
    # final é dele -- só o staging sai.  Corrida rara e inofensiva (o plano
    # já pula as chaves ocupadas): aviso, não falha -- não muda o rc
    except sqlite3.IntegrityError as l_error:
        ls_message = f"produto já existe: {l_error}"
        flst_files_to_unlink.append(lpth_staging)

        M_LOG.warning("%s_%d %d: %s; interpolado descartado", f_result.radar,
                      f_result.alcance, f_result.timestamp, ls_message)

        f_db.insert_log(f_result.radar, f_result.alcance, f_result.timestamp,
                        db.CS_STEP_INTERPOLATE, db.CS_LOG_FAILED, ls_message)
        return CS_SLOT_ALREADY_EXISTS

    # a linha é do interpolado: o staging vira o arquivo final.  Dentro da
    # transação: se o rename falhar, a linha sai
    try:
        lpth_staging.replace(lpth_final)

    except OSError as l_error:
        f_db.delete_product(f_result.radar, f_result.alcance, f_result.timestamp,
                            f_result.preset)
        flst_files_to_unlink.append(lpth_staging)

        _log_slot_failure(f_db, f_result, f"staging -> {lpth_final.name}: {l_error}")
        return CS_SLOT_FAILED

    f_db.insert_log(f_result.radar, f_result.alcance, f_result.timestamp,
                    db.CS_STEP_INTERPOLATE, db.CS_LOG_SUCCESS, lpth_final.name)

    return CS_SLOT_INTERPOLATED

# ─────────────────────────────────────────────────────────────────────────────────────
def _log_slot_failure(f_db: db.SDatabase, f_result: SSlotResult, fs_message: str) -> None:
    """
    falha de um slot: no log do programa e em processing_log
    """
    M_LOG.error("%s_%d %d: %s", f_result.radar, f_result.alcance, f_result.timestamp,
                fs_message)

    f_db.insert_log(f_result.radar, f_result.alcance, f_result.timestamp,
                    db.CS_STEP_INTERPOLATE, db.CS_LOG_FAILED, fs_message)

# ═════════════════════════════════════════════════════════════════════════════════════
# dry-run
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def format_plan(f_plan: SPlan, f_filter: cli.SListingFilter | None = None,
                fb_timeline: bool = False) -> str:
    """
    texto do dry-run: cabeçalho, resumo por série e as seções de arquivos a
    gerar, interpolados a apagar, lacunas e (opcional) timelines

    :param f_filter: séries e intervalo a listar; None = tudo
    :param fb_timeline: acrescenta Timeline.table() compacta de cada série
    """
    if f_filter is None:
        f_filter = cli.SListingFilter()

    llst_lines = ["DRY-RUN nwcInterpolate — nada será gravado",
                  f"banco lido em {f_plan.read_at:{CS_TIME_FORMAT}}Z; é uma foto: o 21.mvol "
                  "roda em paralelo e o run real refaz o plano",
                  f"method={cf.G_CONFIG.method} blend={cf.G_CONFIG.blend} "
                  f"max_gap={cf.G_CONFIG.max_gap} tolerance={cf.G_CONFIG.tolerance_s}s "
                  f"cappi_dir={cf.G_CONFIG.cappi_dir}",
                  "", "== resumo por série"]

    for ls_line in f_plan.summary_lines():
        ls_product = ls_line.split()[0]

        if f_filter.wants_product(ls_product):
            llst_lines.append(ls_line)

    llst_lines += _section_files_to_create(f_plan, f_filter)
    llst_lines += _section_superseded(f_plan, f_filter)
    llst_lines += _section_gaps(f_plan, f_filter)

    if fb_timeline:
        llst_lines += _section_timelines(f_plan, f_filter)

    return "\n".join(llst_lines)

# ─────────────────────────────────────────────────────────────────────────────────────
def _section_files_to_create(f_plan: SPlan, f_filter: cli.SListingFilter) -> list[str]:
    """
    seção "arquivos a gerar": um .npy por slot de cada tarefa, com as marcas
    [vizinho ausente] (o worker vai falhar) e [sobrescreve] (já existe)
    """
    llst_lines = ["", "== arquivos a gerar",
                  f"{'SÉRIE':<8s} {'INSTANTE (UTC)':<19s} {'EPOCH':>10s} {'ALPHA':>5s}  "
                  "ANTERIOR -> POSTERIOR  =>  SAÍDA"]
    li_file_count = 0

    for l_task in f_plan.tasks:
        if not f_filter.wants_product(l_task.product_name):
            continue

        ls_neighbor_mark = ""

        if not (cp.is_usable_cappi_file(l_task.previous_path) and
                cp.is_usable_cappi_file(l_task.next_path)):
            ls_neighbor_mark = " [vizinho ausente]"

        ls_previous_name = pathlib.Path(l_task.previous_path).name
        ls_next_name = pathlib.Path(l_task.next_path).name

        for li_timestamp, lf_alpha in l_task.slots:
            if not f_filter.wants_epoch(li_timestamp):
                continue

            lpth_output = build_filename(pathlib.Path(l_task.cappi_dir), l_task.preset,
                                         l_task.radar, l_task.alcance, li_timestamp)

            ls_overwrite_mark = " [sobrescreve]" if lpth_output.exists() else ""

            llst_lines.append(f"{l_task.product_name:<8s} {_format_epoch(li_timestamp)} "
                              f"{li_timestamp:>10d} {lf_alpha:5.3f}  "
                              f"{ls_previous_name} -> {ls_next_name}  =>  "
                              f"{lpth_output}{ls_neighbor_mark}{ls_overwrite_mark}")
            li_file_count += 1

    llst_lines.append(f"total: {li_file_count}")

    return llst_lines

# ─────────────────────────────────────────────────────────────────────────────────────
def _section_superseded(f_plan: SPlan, f_filter: cli.SListingFilter) -> list[str]:
    """
    seção "interpolados a apagar": sintéticos cujo slot recebeu um observado
    """
    llst_lines = ["", "== interpolados a apagar (o slot recebeu um volume observado)"]
    li_deleted_count = 0

    for l_record in f_plan.superseded:
        ls_product = str(l_record.product)

        if not (f_filter.wants_product(ls_product) and
                f_filter.wants_time(l_record.timestamp_utc)):
            continue

        llst_lines.append(f"{ls_product:<8s} {_format_epoch(l_record.epoch)} "
                          f"{l_record.epoch:>10d}  {l_record.cappi_path}")
        li_deleted_count += 1

    llst_lines.append(f"total: {li_deleted_count}")

    return llst_lines

# ─────────────────────────────────────────────────────────────────────────────────────
def _section_gaps(f_plan: SPlan, f_filter: cli.SListingFilter) -> list[str]:
    """
    seção "lacunas": TODAS as lacunas (as que vão ser interpoladas e as
    puladas), com o destino, e o total por destino.  Entra a lacuna que
    toca o intervalo do filtro.
    """
    llst_lines = ["", "== lacunas",
                  f"{'SÉRIE':<8s} {'INÍCIO (UTC)':<19s} {'FIM (UTC)':<19s} {'SLOTS':>6s}  DESTINO"]
    ldct_count_by_destination: dict = {}

    for l_row in sorted(_gap_listing_rows(f_plan)):
        if not f_filter.wants_product(l_row.product):
            continue

        if not f_filter.overlaps(l_row.first_epoch, l_row.last_epoch):
            continue

        llst_lines.append(f"{l_row.product:<8s} {_format_epoch(l_row.first_epoch)} "
                          f"{_format_epoch(l_row.last_epoch)} {l_row.slot_count:>6d}  "
                          f"{l_row.destination}")

        ldct_count_by_destination[l_row.destination] = \
            ldct_count_by_destination.get(l_row.destination, 0) + 1

    ls_total_line = f"total: {sum(ldct_count_by_destination.values())}"

    for ls_destination, li_count in sorted(ldct_count_by_destination.items()):
        ls_total_line += f", {ls_destination} {li_count}"

    llst_lines.append(ls_total_line)

    return llst_lines

# ─────────────────────────────────────────────────────────────────────────────────────
def _gap_listing_rows(f_plan: SPlan) -> list[SGapListingRow]:
    """
    uma SGapListingRow por lacuna do plano: as tarefas (destino
    "interpolar") e as puladas (destino = motivo)
    """
    llst_rows = []

    for l_task in f_plan.tasks:
        llst_rows.append(SGapListingRow(product=l_task.product_name,
                                        first_epoch=l_task.slots[0][0],
                                        last_epoch=l_task.slots[-1][0],
                                        slot_count=len(l_task.slots),
                                        destination=CS_GAP_TO_INTERPOLATE))

    for l_product, l_gap, ls_reason in f_plan.skipped:
        llst_rows.append(SGapListingRow(product=str(l_product),
                                        first_epoch=l_gap.missing[0].epoch,
                                        last_epoch=l_gap.missing[-1].epoch,
                                        slot_count=len(l_gap.missing),
                                        destination=ls_reason))

    return llst_rows

# ─────────────────────────────────────────────────────────────────────────────────────
def _section_timelines(f_plan: SPlan, f_filter: cli.SListingFilter) -> list[str]:
    """
    seção "timeline <série>": a tabela compacta de cada série listada
    """
    llst_lines = []

    for l_product, l_timeline in f_plan.collection:
        if not f_filter.wants_product(str(l_product)):
            continue

        llst_lines += ["", f"== timeline {l_product}",
                       l_timeline.table(fb_compact=True, fdt_from=f_filter.dt_from,
                                        fdt_to=f_filter.dt_to)]

    return llst_lines

# ─────────────────────────────────────────────────────────────────────────────────────
def _format_epoch(fi_epoch: int) -> str:
    """
    epoch -> "AAAA-MM-DD HH:MM:SS" (UTC)
    """
    return f"{nt.epoch_to_utc(fi_epoch):{CS_TIME_FORMAT}}"

# ═════════════════════════════════════════════════════════════════════════════════════
# linha de comando
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def main(flst_argv: list | None = None) -> int:
    """
    ponto de entrada do programa.

    :param flst_argv: argumentos (None = sys.argv)
    :returns: 0 = sucesso, 1 = erro fatal ou algum slot falhou
    """
    l_parser = _build_argument_parser()
    l_args = l_parser.parse_args(flst_argv)

    # filtros só fazem sentido na listagem: num run real seriam ignorados em
    # silêncio (o run sempre processa todas as séries)
    cli.reject_filters_without_dry_run(l_parser, l_args,
                                       "--timeline, -s, --from e --to exigem --dry-run",
                                       [l_args.timeline])

    # o banco pode ter sido criado pelo 21.mvol, sem as tabelas do nowcast
    if not db.ensure_schema():
        return 1

    if l_args.dry_run:
        return _run_dry_run(l_args)

    return _run_interpolation()

# ─────────────────────────────────────────────────────────────────────────────────────
def _build_argument_parser() -> argparse.ArgumentParser:
    """
    --dry-run, --timeline e os filtros de listagem (-s, --from, --to)
    """
    l_parser = argparse.ArgumentParser(description="interpolação temporal das lacunas curtas")
    l_parser.add_argument("--dry-run", action="store_true",
                          help="só lista o que seria feito (não grava nada)")
    l_parser.add_argument("--timeline", action="store_true",
                          help="com --dry-run: tabela compacta de cada série")

    cli.add_listing_filter_arguments(l_parser,
                                     "com --dry-run: início do intervalo listado (UTC)",
                                     "com --dry-run: fim do intervalo listado (UTC)")
    return l_parser

# ─────────────────────────────────────────────────────────────────────────────────────
def _run_dry_run(f_args: argparse.Namespace) -> int:
    """
    imprime o plano, sem gravar nada

    :returns: 0 ok, 1 erro
    """
    try:
        ls_text = format_plan(plan(), cli.SListingFilter.from_arguments(f_args),
                              f_args.timeline)

    except Exception:
        M_LOG.exception("erro no dry-run.")
        return 1

    print(ls_text)

    return 0

# ─────────────────────────────────────────────────────────────────────────────────────
def _run_interpolation() -> int:
    """
    o run real (as três fases), delimitado no log

    :returns: 0 ok, 1 erro fatal ou algum slot falhou
    """
    ls_title = f"Interpolate (method={cf.G_CONFIG.method}, blend={cf.G_CONFIG.blend})"

    with cli.logged_run(ls_title):
        try:
            li_interpolated, li_failed, li_superseded = interpolate_all()

        except Exception:
            M_LOG.exception("erro fatal durante a interpolação.")
            return 1

        M_LOG.info("interpolados: %d, falhas: %d, substituídos: %d",
                   li_interpolated, li_failed, li_superseded)

    if li_failed:
        return 1

    return 0

# ─────────────────────────────────────────────────────────────────────────────────────
# this is the bootstrap process

if "__main__" == __name__:

    # inicialização explícita via cf.init() antes de qualquer uso do módulo.
    cf.G_CONFIG = cf.init()

    sys.exit(main())

# < the end >──────────────────────────────────────────────────────────────────────────
