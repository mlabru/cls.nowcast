# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

timeline
série temporal de um produto (estação + alcance, ex.: CGU-250): coloca cada
CAPPI num "slot" de tempo, acha as lacunas e lista janelas de slots
consecutivos.

como a série é montada (Timeline.from_records -> _build):
  1. os CAPPIs são ordenados por horário (observado antes de sintético no
     mesmo horário);
  2. cada CAPPI é posicionado em relação ao ANTERIOR: n = Δt / interval,
     arredondado (associação encadeada; a deriva do relógio do radar não
     acumula);
       n == 0  -> mesmo slot do anterior: duplicata (_resolve_duplicate)
       resíduo |Δt − n·interval| > tolerance -> slot INVALID (quebra)
       senão   -> n − 1 slots MISSING entre os dois, e o slot do CAPPI;
  3. cada sequência de MISSING é uma lacuna (gaps()), com o motivo:
     interpolável, longa ou sem âncora.

este pacote (tline) não conhece banco nem arquivos de configuração: quem o
usa (work/nwc_series) entrega os CappiRecord e os parâmetros.

2026.out  mlabru  refatoração para legibilidade: _build -> _place_record com
                  retornos antecipados; _iter_missing_runs() é o único laço
                  que percorre lacunas (antes repetido em _calculate_gaps e
                  gaps); _gap_reason(); table() dividida em funções de linha;
                  Timeline.count removido (era o mesmo que len())
2026.set  mlabru  associação encadeada no lugar da grade fixa T0 + n·interval.
                  Medido no catalog.db: BVA avança 610–622 s por volume, SRO
                  594 s, STI 586–614 s e CGU-250 mudou de fase (:x0:23 ->
                  :x0:02).  Com a grade fixa e ±60 s todo volume dessas
                  séries virava órfão em ~1 h (e era descartado em silêncio).
                  Agora cada volume é posicionado em relação ao anterior
                  (n = round(Δt / interval)), a deriva não acumula e as
                  lacunas recebem horários estimados entre os vizinhos
                  reais.  A associação passou de O(N²) (_nearest_index sobre
                  todos os slots, por volume) para O(N).
                - fonte passou a ser o banco (CappiRecord), não nomes de
                  .mvol: parse_mvol_filename(), from_files() e add()
                  (incremental, para daemon) removidos
                - duplicatas no mesmo slot resolvidas por origem (observado
                  vence interpolado/previsto, que vai para `superseded`) e
                  contadas em vez de descartadas em silêncio
                - interpolatable_gaps() e windows() adicionados
                - gaps(): toda lacuna com o motivo (interpolável, longa, sem
                  âncora), para o dry-run do nc_interpolate mostrar também o
                  que NÃO será interpolado; interpolatable_gaps() passa a ser
                  derivado dela (uma definição só do critério); table()
                  compacta (sequências de MISSING numa linha: há lacunas de
                  6000+ slots) volta, com filtro por intervalo

2026.aug  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import dataclasses
import datetime
import logging
import math
import typing

# locals
import tline.cappi_record as cr
import tline.slot_status as ss
import tline.tl_consts as ct
import tline.tl_slot as ts
import dbase.vol_product_key as pk

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# < constants >────────────────────────────────────────────────────────────────────────

# motivo de uma lacuna (SGap.reason)
CS_GAP_INTERPOLATABLE = "interpolavel"
CS_GAP_LONG = "longa"
CS_GAP_NO_ANCHOR = "sem_ancora"

# formato de instante nas tabelas
CS_TABLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# < SGap >─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SGap:
    """
    lacuna: slots MISSING consecutivos e os slots vizinhos.

    reason:
    - interpolavel: 1..max_interpolated_gap slots, com OBSERVED dos dois lados
    - longa: mais de max_interpolated_gap slots
    - sem_ancora: um vizinho é interpolado/previsto (ou a série acaba ali)
    """
    # slot vizinho anterior (None na borda da série)
    previous: ts.TimeSlot | None
    # slot vizinho posterior (None na borda da série)
    next: ts.TimeSlot | None
    # slots faltantes, em ordem
    missing: tuple
    # motivo (CS_GAP_*)
    reason: str = CS_GAP_INTERPOLATABLE

    # ---------------------------------------------------------------------------------
    @property
    def is_interpolatable(self) -> bool:
        """
        a lacuna vai ser preenchida pelo nwcInterpolate?
        """
        return CS_GAP_INTERPOLATABLE == self.reason

    # ---------------------------------------------------------------------------------
    def alpha(self, f_slot: ts.TimeSlot) -> float:
        """
        posição relativa de um slot faltante entre previous (0) e next (1),
        pelos horários reais dos vizinhos.  Ex.: vizinhos às 10:00 e 10:20,
        slot às 10:10 -> 0.5.
        """
        lf_total_s = (self.next.timestamp - self.previous.timestamp).total_seconds()
        lf_elapsed_s = (f_slot.timestamp - self.previous.timestamp).total_seconds()

        return lf_elapsed_s / lf_total_s

# < Timeline >─────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Timeline:
    """
    série temporal de um único produto radar (station + range), por exemplo
    BEL-250 ou BEL-400.  Crie com Timeline.from_records().
    """
    # product
    product: pk.ProductKey
    # intervalo nominal
    interval: datetime.timedelta = ct.CI_DEFAULT_INTERVAL
    # desvio máximo de cadência (ver tl_consts)
    tolerance: datetime.timedelta = ct.CI_DEFAULT_TOLERANCE
    # lacuna máxima interpolável
    max_interpolated_gap: int = ct.CI_DEFAULT_MAX_INTERPOLATED_GAP
    # slots, em ordem cronológica
    slots: list = dataclasses.field(default_factory=list)
    # records que perderam a disputa por um slot para outro da mesma origem
    duplicates: list = dataclasses.field(default_factory=list)
    # interpolados/previstos cujo slot recebeu um volume observado depois
    superseded: list = dataclasses.field(default_factory=list)

    # ---------------------------------------------------------------------------------
    def __post_init__(self) -> None:
        """
        valida os parâmetros (chamado pelo dataclass logo após o __init__)

        :raises ValueError: interval <= 0, tolerance fora de [0, interval/2]
                            ou max_interpolated_gap < 0
        """
        if self.interval <= datetime.timedelta(0):
            raise ValueError("interval deve ser positivo")

        if self.tolerance < datetime.timedelta(0):
            raise ValueError("tolerance não pode ser negativa")

        # acima de interval/2 dois volumes consecutivos poderiam cair no
        # mesmo slot e no vizinho ao mesmo tempo
        if self.tolerance > self.interval / 2:
            raise ValueError("tolerance não pode exceder interval/2")

        if self.max_interpolated_gap < 0:
            raise ValueError("max_interpolated_gap inválido")

    # ---------------------------------------------------------------------------------
    def __iter__(self) -> typing.Iterator[ts.TimeSlot]:
        """
        percorre os slots em ordem cronológica
        """
        return iter(self.slots)

    def __len__(self) -> int:
        """
        número de slots (de todos os status)
        """
        return len(self.slots)

    def __getitem__(self, fi_index: int) -> ts.TimeSlot:
        """
        slot pela posição
        """
        return self.slots[fi_index]

    # ---------------------------------------------------------------------------------
    @classmethod
    def from_records(cls, f_records: typing.Iterable[cr.CappiRecord], *,
                     f_interval: datetime.timedelta = ct.CI_DEFAULT_INTERVAL,
                     f_tolerance: datetime.timedelta = ct.CI_DEFAULT_TOLERANCE,
                     fi_max_interpolated_gap: int = ct.CI_DEFAULT_MAX_INTERPOLATED_GAP) -> Timeline:
        """
        constrói a Timeline de um produto a partir dos seus CAPPIs.

        records com o mesmo horário são ordenados com o observado primeiro,
        para que a disputa por slot seja determinística.

        :param f_records: CAPPIs de UM produto, em qualquer ordem
        :param f_interval: cadência nominal do radar
        :param f_tolerance: desvio aceito em relação a um múltiplo do intervalo
        :param fi_max_interpolated_gap: maior lacuna (em slots) interpolável
        :raises ValueError: lista vazia ou com produtos diferentes
        """
        llst_records = list(f_records)

        if not llst_records:
            raise ValueError("não existem registros")

        l_product = llst_records[0].product

        for l_record in llst_records:
            if l_record.product != l_product:
                raise ValueError("Timeline não pode conter produtos diferentes: "
                                 f"{l_product} != {l_record.product}")

        llst_records.sort(key=_observed_first_sort_key)

        l_timeline = cls(product=l_product,
                         interval=f_interval,
                         tolerance=f_tolerance,
                         max_interpolated_gap=fi_max_interpolated_gap)

        l_timeline._build(llst_records)

        return l_timeline

    # ---------------------------------------------------------------------------------
    def _build(self, flst_sorted_records: typing.Sequence[cr.CappiRecord]) -> None:
        """
        monta self.slots a partir dos records já ordenados (ver o docstring
        do módulo) e marca as posições dentro de cada lacuna
        """
        self.slots = []

        for l_record in flst_sorted_records:
            self._place_record(l_record)

        self._mark_gap_positions()

    # ---------------------------------------------------------------------------------
    def _place_record(self, f_record: cr.CappiRecord) -> None:
        """
        associação encadeada de um record: posicionado em relação ao último
        slot preenchido, n = round(Δt / interval).

        - primeiro record: vira o primeiro slot;
        - n == 0: mesmo slot do anterior (duplicata, ver _resolve_duplicate);
        - resíduo |Δt − n·interval| > tolerance: cadência fora do nominal;
          um slot INVALID (quebra) entra antes do record;
        - senão: n − 1 slots MISSING entre os dois, depois o record.
        """
        if not self.slots:
            self.slots.append(_filled_slot(f_record))
            return

        # âncora: o último slot preenchido é sempre o último da lista
        li_previous_epoch = self.slots[-1].epoch

        li_delta_s = f_record.epoch - li_previous_epoch
        li_steps = _round_half_up(li_delta_s / self.interval.total_seconds())

        if 0 == li_steps:
            self._resolve_duplicate(self.slots[-1], f_record)
            return

        lf_residual_s = abs(li_delta_s - li_steps * self.interval.total_seconds())

        if lf_residual_s > self.tolerance.total_seconds():
            self._append_cadence_break(li_previous_epoch, f_record)

        else:
            # lacuna (vazia quando li_steps == 1)
            self._append_missing_slots(li_previous_epoch, f_record.epoch, li_steps)

        self.slots.append(_filled_slot(f_record))

    # ---------------------------------------------------------------------------------
    def _append_cadence_break(self, fi_previous_epoch: int, f_record: cr.CappiRecord) -> None:
        """
        acrescenta um slot INVALID no meio do caminho entre o slot anterior e
        o record: a série é "quebrada" ali (nunca interpolada, interrompe
        janelas)
        """
        li_delta_s = f_record.epoch - fi_previous_epoch

        M_LOG.warning("%s: cadência irregular entre %s e %s (Δt=%d s), série quebrada",
                      self.product, _from_epoch(fi_previous_epoch).isoformat(),
                      f_record.timestamp_utc.isoformat(), li_delta_s)

        self.slots.append(ts.TimeSlot(timestamp=_from_epoch(fi_previous_epoch + li_delta_s // 2),
                                      status=ss.SlotStatus.INVALID))

    # ---------------------------------------------------------------------------------
    def _append_missing_slots(self, fi_previous_epoch: int, fi_next_epoch: int,
                              fi_steps: int) -> None:
        """
        acrescenta (fi_steps − 1) slots MISSING igualmente espaçados entre
        dois volumes reais.  Os horários ficam em segundos inteiros porque
        viram o timestamp do produto se forem interpolados.
        """
        for li_step in range(1, fi_steps):
            li_offset_s = _round_half_up(li_step * (fi_next_epoch - fi_previous_epoch) / fi_steps)

            self.slots.append(ts.TimeSlot(timestamp=_from_epoch(fi_previous_epoch + li_offset_s)))

    # ---------------------------------------------------------------------------------
    def _mark_gap_positions(self) -> None:
        """
        preenche gap_length e gap_index de cada slot faltante.

        exemplo: O O M M O  ->  os dois M ficam com gap_length = 2 e
                                gap_index = 1 e 2
        """
        for li_start, li_end in self._iter_missing_runs():
            li_gap_length = li_end - li_start

            for li_position, l_slot in enumerate(self.slots[li_start:li_end], start=1):
                l_slot.gap_length = li_gap_length
                l_slot.gap_index = li_position

    # ---------------------------------------------------------------------------------
    def _iter_missing_runs(self) -> typing.Iterator[tuple[int, int]]:
        """
        cada sequência de slots MISSING consecutivos, como (início, fim) no
        estilo de fatia: self.slots[início:fim] são os slots da lacuna
        """
        li_index = 0
        li_slot_count = len(self.slots)

        while li_index < li_slot_count:
            if not self.slots[li_index].is_missing:
                li_index += 1
                continue

            li_start = li_index

            while li_index < li_slot_count and self.slots[li_index].is_missing:
                li_index += 1

            yield li_start, li_index

    # ---------------------------------------------------------------------------------
    def _resolve_duplicate(self, f_slot: ts.TimeSlot, f_record: cr.CappiRecord) -> None:
        """
        dois records no mesmo slot.

        - observado chega e o slot tem sintético: o observado assume o slot e
          o sintético vai para `superseded` (é o volume que chegou atrasado
          depois que a lacuna já tinha sido interpolada; quem chamou decide
          apagá-lo);
        - sintético chega e o slot tem observado: o sintético vai para
          `superseded`;
        - mesma origem: fica o primeiro, o outro vai para `duplicates`.
        """
        l_kept_record = f_slot.record

        lb_new_is_observed = f_record.origin == ss.SlotStatus.OBSERVED
        lb_kept_is_observed = l_kept_record.origin == ss.SlotStatus.OBSERVED

        if lb_new_is_observed and not lb_kept_is_observed:
            self.superseded.append(l_kept_record)

            f_slot.record = f_record
            f_slot.status = f_record.origin
            f_slot.timestamp = f_record.timestamp_utc
            return

        if lb_kept_is_observed and not lb_new_is_observed:
            self.superseded.append(f_record)
            return

        M_LOG.warning("%s: dois volumes no mesmo slot, mantido %s, ignorado %s",
                      self.product, l_kept_record.timestamp_utc.isoformat(),
                      f_record.timestamp_utc.isoformat())

        self.duplicates.append(f_record)

    # ---------------------------------------------------------------------------------
    def gaps(self) -> list[SGap]:
        """
        todas as lacunas (sequências de MISSING), com o motivo.

        critério de interpolável: 1..max_interpolated_gap slots MISSING com um
        slot OBSERVED de cada lado.  Interpolado ou previsto nunca serve de
        âncora (interpolar a partir de um sintético acumula erro), e INVALID
        nunca é lacuna (é quebra).  Se a lacuna é longa e também sem âncora,
        vale "longa".
        """
        llst_gaps = []

        for li_start, li_end in self._iter_missing_runs():
            l_previous = self.slots[li_start - 1] if li_start > 0 else None
            l_next = self.slots[li_end] if li_end < len(self.slots) else None

            ls_reason = self._gap_reason(l_previous, l_next, li_end - li_start)

            llst_gaps.append(SGap(previous=l_previous,
                                  next=l_next,
                                  missing=tuple(self.slots[li_start:li_end]),
                                  reason=ls_reason))

        return llst_gaps

    # ---------------------------------------------------------------------------------
    def _gap_reason(self, f_previous: ts.TimeSlot | None, f_next: ts.TimeSlot | None,
                    fi_length: int) -> str:
        """
        motivo de uma lacuna (CS_GAP_*), na ordem de prioridade: longa, sem
        âncora, interpolável
        """
        if fi_length > self.max_interpolated_gap:
            return CS_GAP_LONG

        if f_previous is None or not f_previous.is_observed:
            return CS_GAP_NO_ANCHOR

        if f_next is None or not f_next.is_observed:
            return CS_GAP_NO_ANCHOR

        return CS_GAP_INTERPOLATABLE

    # ---------------------------------------------------------------------------------
    def interpolatable_gaps(self) -> list[SGap]:
        """
        lacunas que serão interpoladas (ver gaps() para o critério)
        """
        return [l_gap for l_gap in self.gaps() if l_gap.is_interpolatable]

    # ---------------------------------------------------------------------------------
    def summary(self) -> str:
        """
        resumo textual da Timeline (contagens por status, início e fim)
        """
        llst_lines = [f"Product      : {self.product}",
                      f"Interval     : {self.interval}",
                      f"Tolerance    : {self.tolerance}",
                      f"Slots        : {len(self)}",
                      f"Observed     : {self.count_of(ss.SlotStatus.OBSERVED)}",
                      f"Interpolated : {self.count_of(ss.SlotStatus.INTERPOLATED)}",
                      f"Predicted    : {self.count_of(ss.SlotStatus.PREDICTED)}",
                      f"Missing      : {self.count_of(ss.SlotStatus.MISSING)}",
                      f"Invalid      : {self.count_of(ss.SlotStatus.INVALID)}",
                      f"Duplicates   : {len(self.duplicates)}",
                      f"Superseded   : {len(self.superseded)}",
                      f"Completeness : {self.completeness:.1%}"]

        if self.slots:
            llst_lines.append(f"Start        : {self.slots[0].timestamp.isoformat()}")
            llst_lines.append(f"End          : {self.slots[-1].timestamp.isoformat()}")

        return "\n".join(llst_lines)

    # ---------------------------------------------------------------------------------
    def table(self, fb_compact: bool = True, fdt_from: datetime.datetime | None = None,
              fdt_to: datetime.datetime | None = None) -> str:
        """
        tabela da série, um slot por linha.

        :param fb_compact: cada sequência de MISSING numa linha só, com o
                           tamanho e o motivo (há lacunas de milhares de slots)
        :param fdt_from, fdt_to: só linhas que tocam o intervalo (UTC, inclusivo)
        """
        ldct_reason_by_slot = self._missing_reason_by_slot()

        llst_lines = [f"{'INSTANTE (UTC)':<20s} {'STATUS':<13s} {'EPOCH':>10s}  ARQUIVO / LACUNA",
                      "-" * 100]

        li_index = 0

        while li_index < len(self.slots):
            l_slot = self.slots[li_index]

            # lacuna inteira numa linha só
            if fb_compact and l_slot.is_missing:
                l_last_slot = self.slots[li_index + l_slot.gap_length - 1]

                if _touches_interval(l_slot.timestamp, l_last_slot.timestamp, fdt_from, fdt_to):
                    llst_lines.append(_format_gap_row(l_slot, l_last_slot,
                                                      ldct_reason_by_slot[id(l_slot)]))

                li_index += l_slot.gap_length
                continue

            if _touches_interval(l_slot.timestamp, l_slot.timestamp, fdt_from, fdt_to):
                llst_lines.append(_format_slot_row(l_slot, ldct_reason_by_slot))

            li_index += 1

        return "\n".join(llst_lines)

    # ---------------------------------------------------------------------------------
    def _missing_reason_by_slot(self) -> dict:
        """
        motivo da lacuna de cada slot faltante, indexado por id(slot) (o
        TimeSlot é mutável, então não pode ser chave de dict por valor)
        """
        ldct_reason_by_slot = {}

        for l_gap in self.gaps():
            for l_slot in l_gap.missing:
                ldct_reason_by_slot[id(l_slot)] = l_gap.reason

        return ldct_reason_by_slot

    # ---------------------------------------------------------------------------------
    def validate(self) -> None:
        """
        verifica as invariantes da Timeline (usado nos testes)

        :raises ValueError: slot sem fuso, fora de ordem, ou com status
                            incoerente com o record
        """
        for li_slot_index, l_slot in enumerate(self.slots):
            if l_slot.timestamp.tzinfo is None:
                raise ValueError("slot sem timezone")

            ls_when = l_slot.timestamp.isoformat()

            if li_slot_index > 0 and l_slot.timestamp <= self.slots[li_slot_index - 1].timestamp:
                raise ValueError(f"slots fora de ordem em {ls_when}")

            if l_slot.is_filled != (l_slot.status in ss.CT_FILLED_STATUSES):
                raise ValueError(f"status {l_slot.status} incoerente com record em {ls_when}")

            if l_slot.is_filled and l_slot.record.origin != l_slot.status:
                raise ValueError(f"status {l_slot.status} difere da origem do record "
                                 f"em {ls_when}")

    # ---------------------------------------------------------------------------------
    def windows(self, fi_size: int) -> typing.Iterator[list]:
        """
        janelas de fi_size slots consecutivos, todos preenchidos.  Um slot
        MISSING ou INVALID interrompe a janela.  Janelas se sobrepõem: com
        5 slots preenchidos seguidos e fi_size 4 saem 2 janelas.

        :raises ValueError: fi_size < 1
        """
        if fi_size < 1:
            raise ValueError("fi_size deve ser >= 1")

        # quantos slots preenchidos seguidos terminam no slot atual
        li_filled_run = 0

        for li_slot_index, l_slot in enumerate(self.slots):
            if l_slot.is_filled:
                li_filled_run += 1

            else:
                li_filled_run = 0

            if li_filled_run >= fi_size:
                li_first = li_slot_index - fi_size + 1
                yield self.slots[li_first : li_slot_index + 1]

    # ---------------------------------------------------------------------------------
    def count_of(self, f_status: ss.SlotStatus) -> int:
        """
        número de slots com o status dado
        """
        return sum(l_slot.status == f_status for l_slot in self.slots)

    # ---------------------------------------------------------------------------------
    @property
    def completeness(self) -> float:
        """
        fração de slots observados (0 para Timeline vazia)
        """
        if not self.slots:
            return 0.0

        return self.count_of(ss.SlotStatus.OBSERVED) / len(self)

# < helpers >──────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────────────
def _filled_slot(f_record: cr.CappiRecord) -> ts.TimeSlot:
    """
    slot preenchido por um record (status = origem do record)
    """
    return ts.TimeSlot(timestamp=f_record.timestamp_utc,
                       record=f_record,
                       status=f_record.origin)

# ─────────────────────────────────────────────────────────────────────────────────────
def _observed_first_sort_key(f_record: cr.CappiRecord) -> tuple[int, bool]:
    """
    chave de ordenação: por horário e, no mesmo horário, observado antes de
    sintético (False < True)
    """
    return f_record.epoch, f_record.origin != ss.SlotStatus.OBSERVED

# ─────────────────────────────────────────────────────────────────────────────────────
def _touches_interval(fdt_start: datetime.datetime, fdt_end: datetime.datetime,
                      fdt_from: datetime.datetime | None,
                      fdt_to: datetime.datetime | None) -> bool:
    """
    o período [início, fim] toca o intervalo [from, to]? (None = sem limite)
    """
    if fdt_from is not None and fdt_end < fdt_from:
        return False

    if fdt_to is not None and fdt_start > fdt_to:
        return False

    return True

# ─────────────────────────────────────────────────────────────────────────────────────
def _format_gap_row(f_first_slot: ts.TimeSlot, f_last_slot: ts.TimeSlot, fs_reason: str) -> str:
    """
    linha da tabela compacta para uma lacuna inteira:
    "<início> missing <epoch>  ×<slots> [<motivo>] até <fim>"
    """
    ls_until = ""

    if f_first_slot.gap_length > 1:
        ls_until = f"até {f_last_slot.timestamp:{CS_TABLE_TIME_FORMAT}}"

    return (f"{f_first_slot.timestamp:{CS_TABLE_TIME_FORMAT}} "
            f"{'missing':<13s} {f_first_slot.epoch:>10d}  "
            f"×{f_first_slot.gap_length} [{fs_reason}] {ls_until}")

# ─────────────────────────────────────────────────────────────────────────────────────
def _format_slot_row(f_slot: ts.TimeSlot, fdct_reason_by_slot: dict) -> str:
    """
    linha da tabela para um slot: arquivo (preenchido), motivo (faltante) ou
    "quebra de cadência" (INVALID)
    """
    if f_slot.is_filled:
        ls_info = f_slot.cappi_path.name

    elif f_slot.is_missing:
        ls_info = f"[{fdct_reason_by_slot[id(f_slot)]}]"

    else:
        ls_info = "quebra de cadência"

    return (f"{f_slot.timestamp:{CS_TABLE_TIME_FORMAT}} "
            f"{f_slot.status.value:<13s} {f_slot.epoch:>10d}  {ls_info}")

# ─────────────────────────────────────────────────────────────────────────────────────
def _from_epoch(fi_epoch: int) -> datetime.datetime:
    """
    epoch UTC (s) -> datetime aware em UTC.  (cópia de work/nwc_time.epoch_to_utc:
    tline não importa nada de work/)
    """
    return datetime.datetime.fromtimestamp(fi_epoch, tz=datetime.timezone.utc)

# ─────────────────────────────────────────────────────────────────────────────────────
def _round_half_up(ff_value: float) -> int:
    """
    arredondamento "meio para cima": o round() do Python arredonda 0.5 para o
    par (round(0.5) == 0, round(2.5) == 2), o que tornaria a associação
    dependente da paridade do número de slots
    """
    return math.floor(ff_value + 0.5)

# < the end >──────────────────────────────────────────────────────────────────────────
