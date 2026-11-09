# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwcSequences
constrói as sequências de treinamento do RainNet: 4 entradas + 1 alvo, em
slots consecutivos da série de cada radar.

fluxo (build_sequences):
  1. monta as Timelines a partir do banco (nwc_series.load_collection);
  2. percorre as janelas de 5 slots consecutivos, todos com CAPPI
     (Timeline.windows) e decide, para cada uma, se é aceita
     (evaluate_windows -> reject_reason):
       - o alvo precisa ser OBSERVADO (um interpolado é estimativa, não dado);
       - todos os CAPPIs precisam existir em disco;
       - cada volume precisa de valid_fraction >= min_valid_ratio × (maior
         valid_fraction observada da série) -- relativo porque um radar de
         250 km no grid de ±250 km só cobre ~π/4 dos pixels;
       - a chuva média da janela precisa ser >= min_rain_fraction;
  3. reescreve a tabela sequences inteira numa transação de escrita curta,
     no fim (o lock do banco compartilhado não fica preso durante as
     verificações em disco).

quality de uma sequência: 0 = todos observados, 1 = tem interpolado,
2 = tem previsto.

--dry-run lista, sem gravar nada, as sequências que o próximo ciclo do cron
(nwcInterpolate -> nwcSequences) vai gerar: projeta os interpolados que o
plano do nwcInterpolate vai criar (project_collection), compara com a
tabela atual (novas, mantidas, removidas) e conta as janelas rejeitadas por
motivo.

    python3 apps/nwcSequences.py --dry-run
    python3 apps/nwcSequences.py --dry-run -s CGU-250 --from 2026-09-29

códigos de retorno: 0 ok, 1 erro fatal.

2026.out  mlabru  refatoração para legibilidade: format_sequences e
                  project_collection divididos; linha de sequences como
                  SSequenceRow (campos com nome em vez de lt_row[6]); filtros
                  do dry-run em nwc_cli; datas por nwc_time (antes
                  ni._from_epoch/ni._parse_utc, nomes privados do
                  nwcInterpolate)

2026.out  mlabru  --dry-run com projeção.  Os slots que o nwcInterpolate vai
                  preencher entram como interpolados, com cobertura estimada
                  = max(vizinhos) (a real é a união: limite inferior) e chuva
                  = mistura linear dos vizinhos por alpha; as estatísticas
                  reais só existem depois do cálculo.  Lacunas adiadas e
                  tarefas com vizinho ausente não são projetadas (o run real
                  também não as gera).  Critério de aceitação concentrado em
                  evaluate_windows(), usado pelo run e pelo dry-run.

2026.set  mlabru  - sequences.preset: o preset ativo da série (as séries só
                    têm produtos desse preset, ver nwc_series)

2026.set  mlabru  - garante o esquema no início (db.ensure_schema): em banco
                    criado pelo 21.mvol a tabela sequences não existia e o
                    estágio quebrava no fim, no DELETE FROM sequences

2026.set  mlabru  - janelas sobre os slots da Timeline em vez de Δt == 600 s
                    exato entre linhas: com o jitter real de ±1..4 s quase
                    toda janela observada era rejeitada (4 de 439 sequências
                    eram 100% observadas).
                  - alvo interpolado/previsto rejeitado.
                  - limiar de valid_fraction relativo à maior valid_fraction
                    observada da série ([sequences] min_valid_ratio): um radar
                    de 250 km no grid de ±250 km cobre ~78,5% dos pixels, e o
                    limiar absoluto de 95% rejeitaria todas as suas janelas.
                  - filtro opcional de chuva ([sequences] min_rain_fraction).
                  - leitura numa transação de leitura e DELETE + INSERT numa
                    transação de escrita curta, no fim: o lock de escrita no
                    banco compartilhado com o 21.mvol não fica preso durante
                    as verificações em disco.
                  - dependência de utl_dates removida.

2026.jun  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import logging
import pathlib
import sys
import typing

# locals
import work.nwc_cappi_path as cp
import work.nwc_cli as cli
import work.nwc_config as cf
import work.nwc_database as db
import work.nwc_presets as ps
import work.nwc_series as ns
import work.nwc_time as nt
import nwcInterpolate as ni

import tline.cappi_record as cr
import tline.slot_status as ss
import tline.tl_collection as tc
import tline.timeline as tl
import dbase.vol_record as vr

# < constants >────────────────────────────────────────────────────────────────────────

# comprimento da janela de entrada — fixo pelo esquema (input_1..input_4)
CI_INPUT_LENGTH = 4
# comprimento da janela de saída — fixo pelo esquema (target)
CI_FORECAST_LENGTH = 1

# tamanho total da janela deslizante
CI_WINDOW_SIZE = CI_INPUT_LENGTH + CI_FORECAST_LENGTH

# motivos de rejeição (contadores do log)
CS_REJ_TARGET = "alvo não observado"
CS_REJ_FILES = "CAPPI ausente em disco"
CS_REJ_VALID = "cobertura baixa"
CS_REJ_RAIN = "pouca chuva"

# quality de uma sequência (compute_quality)
CI_QUALITY_OBSERVED = 0
CI_QUALITY_INTERPOLATED = 1
CI_QUALITY_PREDICTED = 2

# cabeçalho das listagens de sequências do dry-run
CS_SEQUENCE_HEADER = f"{'SÉRIE':<8s} {'ALVO (UTC)':<19s} {'EPOCH':>10s}  {'ENTRADAS (UTC)':<35s} Q"

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# < SSequenceRow >─────────────────────────────────────────────────────────────────────

class SSequenceRow(typing.NamedTuple):
    """
    uma linha da tabela sequences (a ordem é a de
    SDatabase.replace_sequences/sequences_rows).  É uma tupla: compara e
    gera o mesmo hash que a tupla simples lida do banco.
    """
    radar: str
    alcance: int
    input_1: int
    input_2: int
    input_3: int
    input_4: int
    target: int
    quality: int
    preset: str | None

    # ---------------------------------------------------------------------------------
    @property
    def product_name(self) -> str:
        """
        nome da série, ex.: "CGU-250"
        """
        return f"{self.radar}-{self.alcance}"

    # ---------------------------------------------------------------------------------
    @property
    def inputs(self) -> tuple:
        """
        epochs das quatro entradas
        """
        return self.input_1, self.input_2, self.input_3, self.input_4

# < SProjection >──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class SProjection:
    """
    séries como ficarão depois do próximo nwcInterpolate (dry-run)
    """
    # instante da leitura do banco (UTC)
    read_at: datetime.datetime
    # séries com os interpolados planejados
    collection: tc.TimelineCollection
    # CAPPIs projetados (ainda não existem em disco)
    projected: set
    # lacunas interpoláveis não projetadas: adiadas ou com vizinho ausente
    not_projected: int

    # ---------------------------------------------------------------------------------
    def exists(self, fpth: pathlib.Path) -> bool:
        """
        "o CAPPI existe" na projeção: já está em disco ou vai ser gerado
        """
        return fpth in self.projected or fpth.exists()

# ═════════════════════════════════════════════════════════════════════════════════════
# run real
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def build_sequences() -> tuple[int, collections.Counter]:
    """
    reconstrói completamente a tabela sequences.

    :returns: (sequências criadas, contagem de rejeições por motivo)
    """
    l_collection = ns.load_collection()

    llst_rows = []
    lctr_rejected: collections.Counter = collections.Counter()

    for l_product, l_timeline in l_collection:
        llst_series_rows = sequences_of(l_timeline, lctr_rejected)
        llst_rows.extend(llst_series_rows)

        M_LOG.info("%s: %d slots, %d sequências", l_product, len(l_timeline),
                   len(llst_series_rows))

    # escrita curta, no fim
    with db.transaction_write() as ldb:
        ldb.replace_sequences(llst_rows)

    return len(llst_rows), lctr_rejected

# ═════════════════════════════════════════════════════════════════════════════════════
# critério de aceitação (único lugar: usado pelo run e pelo dry-run)
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def compute_quality(flst_window: list) -> int:
    """
    classifica a janela pela origem dos CAPPIs:

      0 = todos observados
      1 = contém interpolado
      2 = contém previsto (prioridade sobre interpolado)
    """
    li_quality = CI_QUALITY_OBSERVED

    for l_slot in flst_window:
        if l_slot.is_predicted:
            return CI_QUALITY_PREDICTED

        if l_slot.is_interpolated:
            li_quality = CI_QUALITY_INTERPOLATED

    return li_quality

# ─────────────────────────────────────────────────────────────────────────────────────
def reject_reason(flst_window: list, ff_min_valid: float,
                  ffn_exists: typing.Callable = pathlib.Path.exists) -> str | None:
    """
    critérios de aceitação de uma janela (já contígua e toda preenchida).

    :param ff_min_valid: valid_fraction mínima de cada volume (absoluta, já
                         multiplicada pela maior valid_fraction da série)
    :param ffn_exists: Path -> bool, "o CAPPI existe" (a projeção do dry-run
                       considera também os interpolados ainda não gerados)
    :returns: None se aceita, senão o motivo (CS_REJ_*)
    """
    if not flst_window[-1].is_observed:
        return CS_REJ_TARGET

    for l_slot in flst_window:
        if not ffn_exists(l_slot.cappi_path):
            return CS_REJ_FILES

    # cobertura de cada volume (None conta como 0: sem estatística não há
    # como garantir o volume)
    for l_slot in flst_window:
        if (l_slot.record.valid_fraction or 0.0) < ff_min_valid:
            return CS_REJ_VALID

    llst_rain = [l_slot.record.rain_fraction or 0.0 for l_slot in flst_window]
    lf_mean_rain = sum(llst_rain) / len(llst_rain)

    if lf_mean_rain < cf.G_CONFIG.min_rain_fraction:
        return CS_REJ_RAIN

    return None

# ─────────────────────────────────────────────────────────────────────────────────────
def evaluate_windows(f_timeline: tl.Timeline,
                     ffn_exists: typing.Callable = pathlib.Path.exists) -> typing.Iterator[tuple]:
    """
    todas as janelas candidatas de uma série, com o veredito.  Único lugar
    do critério de aceitação (run e dry-run).

    :returns: iterador de (janela, motivo da rejeição ou None se aceita)
    """
    lf_min_valid = cf.G_CONFIG.min_valid_ratio * _reference_valid_fraction(f_timeline)

    for llst_window in f_timeline.windows(CI_WINDOW_SIZE):
        yield llst_window, reject_reason(llst_window, lf_min_valid, ffn_exists)

# ─────────────────────────────────────────────────────────────────────────────────────
def _reference_valid_fraction(f_timeline: tl.Timeline) -> float:
    """
    referência de cobertura da série: a maior valid_fraction entre os slots
    observados (0 se não há nenhum)
    """
    lf_reference = 0.0

    for l_slot in f_timeline:
        if l_slot.is_observed:
            lf_reference = max(lf_reference, l_slot.record.valid_fraction or 0.0)

    return lf_reference

# ─────────────────────────────────────────────────────────────────────────────────────
def row_of(f_timeline: tl.Timeline, flst_window: list) -> SSequenceRow:
    """
    janela aceita -> linha de sequences (radar, alcance, 4 entradas, alvo,
    quality, preset)
    """
    llst_epochs = [l_slot.epoch for l_slot in flst_window]

    return SSequenceRow(f_timeline.product.station,
                        f_timeline.product.range_km,
                        *llst_epochs,
                        compute_quality(flst_window),
                        ps.active_id(f_timeline.product.range_km))

# ─────────────────────────────────────────────────────────────────────────────────────
def sequences_of(f_timeline: tl.Timeline, fctr_rejected: collections.Counter) -> list:
    """
    janelas aceitas de uma série, como linhas prontas para
    SDatabase.replace_sequences(); as rejeições são contadas em fctr_rejected
    """
    llst_rows = []

    for llst_window, ls_reason in evaluate_windows(f_timeline):
        if ls_reason is not None:
            fctr_rejected[ls_reason] += 1
            continue

        llst_rows.append(row_of(f_timeline, llst_window))

    return llst_rows

# ═════════════════════════════════════════════════════════════════════════════════════
# dry-run: projeção do próximo ciclo
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def project_collection() -> SProjection:
    """
    séries depois do próximo nwcInterpolate: os slots de cada tarefa do plano
    viram interpolados com estatísticas estimadas (as reais só existem depois
    do cálculo, ver _projected_record).

    tarefa com vizinho ausente ou vazio não é projetada: o worker falharia e
    o slot continuaria faltando.  Lacunas adiadas também não (o volume ainda
    não foi convertido).
    """
    l_plan = ni.plan()

    lpth_cappi_dir = pathlib.Path(cf.G_CONFIG.cappi_dir)

    llst_records = []
    lset_projected = set()
    li_not_projected = sum(1 for _, _, ls_reason in l_plan.skipped
                           if ni.CS_GAP_DEFERRED == ls_reason)

    ldct_tasks_by_series = _group_tasks_by_series(l_plan.tasks)

    for l_product, l_timeline in l_plan.collection:
        llst_filled = [l_slot.record for l_slot in l_timeline if l_slot.is_filled]
        llst_records.extend(llst_filled)

        ldct_record_by_path = {str(l_record.cappi_path): l_record for l_record in llst_filled}
        ltpl_series = (l_product.station, l_product.range_km)

        for l_task in ldct_tasks_by_series.get(ltpl_series, []):
            l_previous = ldct_record_by_path[l_task.previous_path]
            l_next = ldct_record_by_path[l_task.next_path]

            if not (cp.is_usable_cappi_file(l_previous.cappi_path) and
                    cp.is_usable_cappi_file(l_next.cappi_path)):
                li_not_projected += 1
                continue

            for li_timestamp, lf_alpha in l_task.slots:
                lpth_output = ni.build_filename(lpth_cappi_dir, l_task.preset, l_task.radar,
                                                l_task.alcance, li_timestamp)

                llst_records.append(_projected_record(l_product, lpth_output, li_timestamp,
                                                      lf_alpha, l_previous, l_next))
                lset_projected.add(lpth_output)

    return SProjection(read_at=l_plan.read_at,
                       collection=ns.collection_of(llst_records),
                       projected=lset_projected,
                       not_projected=li_not_projected)

# ─────────────────────────────────────────────────────────────────────────────────────
def _group_tasks_by_series(flst_tasks: list) -> dict:
    """
    tarefas do plano agrupadas por (radar, alcance), na ordem original
    """
    ldct_tasks_by_series: dict = {}

    for l_task in flst_tasks:
        ldct_tasks_by_series.setdefault((l_task.radar, l_task.alcance), []).append(l_task)

    return ldct_tasks_by_series

# ─────────────────────────────────────────────────────────────────────────────────────
def _projected_record(f_product, fpth_output: pathlib.Path, fi_timestamp: int,
                      ff_alpha: float, f_previous: cr.CappiRecord,
                      f_next: cr.CappiRecord) -> cr.CappiRecord:
    """
    CappiRecord interpolado que o próximo nwcInterpolate vai gerar, com
    estatísticas estimadas:

    - valid_fraction = max(vizinhos): a cobertura do interpolado é a união
      da dos vizinhos, então é um limite inferior (estimativa conservadora)
    - rain_fraction = (1 − alpha)·anterior + alpha·posterior
    """
    lf_previous_rain = f_previous.rain_fraction or 0.0
    lf_next_rain = f_next.rain_fraction or 0.0

    lf_valid_estimate = max(f_previous.valid_fraction or 0.0, f_next.valid_fraction or 0.0)
    lf_rain_estimate = (1.0 - ff_alpha) * lf_previous_rain + ff_alpha * lf_next_rain

    l_volume = vr.VolumeRecord(product=f_product,
                               timestamp=nt.epoch_to_utc(fi_timestamp),
                               filename=fpth_output)

    return cr.CappiRecord(volume=l_volume,
                          cappi_path=fpth_output,
                          origin=ss.SlotStatus.INTERPOLATED,
                          valid_fraction=lf_valid_estimate,
                          rain_fraction=lf_rain_estimate)

# ═════════════════════════════════════════════════════════════════════════════════════
# dry-run: listagem
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def format_sequences(f_projection: SProjection, flst_current: list,
                     f_filter: cli.SListingFilter | None = None) -> str:
    """
    texto do dry-run: cabeçalho, resumo por série, sequências a gerar e
    sequências a remover.

    :param flst_current: tabela sequences atual (SDatabase.sequences_rows)
    :param f_filter: séries e intervalo (pelo alvo) a listar; None = tudo
    """
    if f_filter is None:
        f_filter = cli.SListingFilter()

    lset_current = {SSequenceRow._make(ltpl_row) for ltpl_row in flst_current}

    llst_lines = ["DRY-RUN nwcSequences — nada será gravado",
                  f"banco lido em {f_projection.read_at:%Y-%m-%d %H:%M:%S}Z; é uma foto: o "
                  "21.mvol e a conversão rodam em paralelo e o run real relê o banco",
                  f"projeção do próximo nwcInterpolate: {len(f_projection.projected)} slots "
                  f"interpolados (cobertura = max dos vizinhos, chuva = mistura por alpha); "
                  f"{f_projection.not_projected} lacunas não projetadas (adiadas ou com "
                  "vizinho ausente)",
                  f"min_valid_ratio={cf.G_CONFIG.min_valid_ratio} "
                  f"min_rain_fraction={cf.G_CONFIG.min_rain_fraction}",
                  "", "== resumo por série"]

    # (linha, projetada) das janelas aceitas, de todas as séries listadas
    llst_accepted = []

    for l_product, l_timeline in f_projection.collection:
        if not f_filter.wants_product(str(l_product)):
            continue

        ls_summary, llst_series_accepted = _summarize_series(l_product, l_timeline,
                                                             f_projection, lset_current)
        llst_lines.append(ls_summary)
        llst_accepted.extend(llst_series_accepted)

    lset_new_rows = {l_row for l_row, _ in llst_accepted}

    llst_lines += _section_to_generate(llst_accepted, lset_current, f_filter)
    llst_lines += _section_to_remove(lset_current - lset_new_rows, f_filter)

    return "\n".join(llst_lines)

# ─────────────────────────────────────────────────────────────────────────────────────
def _summarize_series(f_product, f_timeline: tl.Timeline, f_projection: SProjection,
                      fset_current: set) -> tuple[str, list]:
    """
    avalia as janelas de uma série e monta a sua linha de resumo

    :returns: (linha do resumo, [(SSequenceRow, projetada), ...] das aceitas)
    """
    lctr_rejected: collections.Counter = collections.Counter()
    li_candidates = 0
    li_projected = 0
    llst_accepted = []
    lset_rows = set()

    for llst_window, ls_reason in evaluate_windows(f_timeline, f_projection.exists):
        li_candidates += 1

        if ls_reason is not None:
            lctr_rejected[ls_reason] += 1
            continue

        lb_projected = any(l_slot.cappi_path in f_projection.projected for l_slot in llst_window)

        if lb_projected:
            li_projected += 1

        l_row = row_of(f_timeline, llst_window)
        lset_rows.add(l_row)
        llst_accepted.append((l_row, lb_projected))

    ltpl_series = (f_product.station, f_product.range_km)
    lset_current_series = {l_row for l_row in fset_current
                           if (l_row.radar, l_row.alcance) == ltpl_series}

    ls_rejections = ", ".join(f"{ls_reason} {li_count}"
                              for ls_reason, li_count in lctr_rejected.most_common())

    if not ls_rejections:
        ls_rejections = "0"

    ls_summary = (f"{str(f_product):<8s} janelas {li_candidates:>6d} | "
                  f"aceitas {len(lset_rows):>5d} (projetadas {li_projected}) | "
                  f"novas {len(lset_rows - lset_current_series)} "
                  f"mantidas {len(lset_rows & lset_current_series)} "
                  f"removidas {len(lset_current_series - lset_rows)} | "
                  f"rejeitadas: {ls_rejections}")

    return ls_summary, llst_accepted

# ─────────────────────────────────────────────────────────────────────────────────────
def _section_to_generate(flst_accepted: list, fset_current: set,
                         f_filter: cli.SListingFilter) -> list[str]:
    """
    seção "sequências a gerar", com as marcas [nova] (não está na tabela) e
    [projetada] (usa um interpolado que ainda não existe)
    """
    llst_lines = ["", "== sequências a gerar", CS_SEQUENCE_HEADER]
    li_count = 0

    for l_row, lb_projected in flst_accepted:
        if not f_filter.wants_epoch(l_row.target):
            continue

        ls_marks = ""

        if l_row not in fset_current:
            ls_marks += " [nova]"

        if lb_projected:
            ls_marks += " [projetada]"

        llst_lines.append(_format_sequence_row(l_row) + ls_marks)
        li_count += 1

    llst_lines.append(f"total: {li_count}")

    return llst_lines

# ─────────────────────────────────────────────────────────────────────────────────────
def _section_to_remove(fset_removed: set, f_filter: cli.SListingFilter) -> list[str]:
    """
    seção "sequências a remover": estão na tabela e não serão geradas
    """
    llst_lines = ["", "== sequências a remover (estão na tabela e não serão geradas)",
                  CS_SEQUENCE_HEADER]
    li_count = 0

    for l_row in sorted(fset_removed):
        if not (f_filter.wants_product(l_row.product_name) and
                f_filter.wants_epoch(l_row.target)):
            continue

        llst_lines.append(_format_sequence_row(l_row))
        li_count += 1

    llst_lines.append(f"total: {li_count}")

    return llst_lines

# ─────────────────────────────────────────────────────────────────────────────────────
def _format_sequence_row(f_row: SSequenceRow) -> str:
    """
    "<série> <alvo> <epoch>  <hh:mm:ss das 4 entradas> <quality>"
    """
    ls_inputs = " ".join(f"{nt.epoch_to_utc(li_epoch):%H:%M:%S}" for li_epoch in f_row.inputs)

    return (f"{f_row.radar}-{f_row.alcance:<4d} "
            f"{nt.epoch_to_utc(f_row.target):%Y-%m-%d %H:%M:%S} {f_row.target:>10d}  "
            f"{ls_inputs:<35s} {f_row.quality}")

# ═════════════════════════════════════════════════════════════════════════════════════
# linha de comando
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def main(flst_argv: list | None = None) -> int:
    """
    ponto de entrada do programa.

    :param flst_argv: argumentos (None = sys.argv)
    :returns: 0 = sucesso, 1 = erro fatal
    """
    l_parser = _build_argument_parser()
    l_args = l_parser.parse_args(flst_argv)

    # filtros só fazem sentido na listagem (o run reconstrói a tabela inteira)
    cli.reject_filters_without_dry_run(l_parser, l_args, "-s, --from e --to exigem --dry-run")

    # sequences é só do nowcast: o banco pode ter sido criado pelo 21.mvol
    if not db.ensure_schema():
        return 1

    if l_args.dry_run:
        return _run_dry_run(l_args)

    return _run_build()

# ─────────────────────────────────────────────────────────────────────────────────────
def _build_argument_parser() -> argparse.ArgumentParser:
    """
    --dry-run e os filtros de listagem (-s, --from, --to)
    """
    l_parser = argparse.ArgumentParser(description="construção das sequências de treinamento")
    l_parser.add_argument("--dry-run", action="store_true",
                          help="só lista as sequências que o próximo ciclo vai gerar "
                               "(não grava nada)")

    cli.add_listing_filter_arguments(l_parser, "com --dry-run: alvo a partir de (UTC)",
                                     "com --dry-run: alvo até (UTC)")
    return l_parser

# ─────────────────────────────────────────────────────────────────────────────────────
def _run_dry_run(f_args: argparse.Namespace) -> int:
    """
    imprime a projeção do próximo ciclo, sem gravar nada

    :returns: 0 ok, 1 erro
    """
    try:
        l_projection = project_collection()

        with db.transaction() as ldb:
            llst_current = ldb.sequences_rows()

        ls_text = format_sequences(l_projection, llst_current,
                                   cli.SListingFilter.from_arguments(f_args))

    except Exception:
        M_LOG.exception("erro no dry-run.")
        return 1

    print(ls_text)

    return 0

# ─────────────────────────────────────────────────────────────────────────────────────
def _run_build() -> int:
    """
    o run real, delimitado no log

    :returns: 0 ok, 1 erro fatal
    """
    with cli.logged_run("Build Sequences"):
        try:
            li_created, lctr_rejected = build_sequences()

        except Exception:
            M_LOG.exception("erro fatal durante a construção das sequências.")
            return 1

        M_LOG.info("sequências criadas: %d", li_created)

        for ls_reason, li_count in lctr_rejected.most_common():
            M_LOG.info("rejeitadas (%s): %d", ls_reason, li_count)

    return 0

# ─────────────────────────────────────────────────────────────────────────────────────
# this is the bootstrap process

if "__main__" == __name__:

    # inicialização explícita via cf.init() antes de qualquer uso do módulo.
    cf.G_CONFIG = cf.init()

    sys.exit(main())

# < the end >──────────────────────────────────────────────────────────────────────────
