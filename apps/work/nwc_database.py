# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_database
camada de acesso ao banco SQLite utilizada pelo pipeline (só módulo, sem main).

como usar: abra uma transação e chame os métodos de SDatabase nela.
    with db.transaction() as ldb:            # leitura (BEGIN)
        llst_rows = ldb.series_rows()
    with db.transaction_write() as ldb:      # escrita (BEGIN IMMEDIATE)
        ldb.insert_log(...)
A transação faz commit no fim do bloco, rollback se o bloco levantar
exceção, e sempre fecha a conexão.  Cada transação abre a SUA conexão:
não guarde o ldb fora do bloco.

o banco é compartilhado com o 21.mvol (e o cron dos dois roda ao mesmo
tempo): mantenha as transações de escrita curtas, e nunca faça trabalho
pesado (ler .npy, gridding) dentro delas.

o DDL de volumes, processing_log e radar_sites é um contrato com o 21.mvol
(apps/dbase/vol_dbase.py, que é quem grava os volumes observados) e deve continuar
idêntico ao de lá; as tabelas cappi_products e sequences são só do nowcast.

volumes é a ingestão (um .mvol recebido); cappi_products é o que o nowcast
fez com ele: um CAPPI por (volume, preset), observado (convertido do .mvol),
interpolado ou previsto.  O nowcast não grava em volumes.

2026.nov  mlabru  SDatabase.__init__ fecha a conexão se o PRAGMA journal_mode
                  falhar ("database is locked" num banco ainda não-WAL): ela
                  vazava (ResourceWarning em test_banco_ocupado_na_reserva)

2026.out  mlabru  refatoração para legibilidade: métodos agrupados por assunto
                  (transação, conversão, produtos, séries, sequências, log);
                  create_schema em passos; _fetch_all_as_dicts e
                  _iso_cutoff no lugar de código repetido; parâmetros de
                  volumes_to_convert montados passo a passo.  DDL intocado
2026.out  mlabru  volta a ser só módulo (apps/work/nwc_database.py, sem main):
                  é a camada de banco importada por estágios, migrações e
                  testes, como o vol_dbase.py do 21.mvol; criar/validar o
                  esquema avulso é o programa apps/nwcSchema.py (os estágios
                  já chamam ensure_schema).  Rodado como script, este arquivo
                  carregaria como __main__, um segundo módulo ao lado do
                  importado

2026.out  mlabru  - cappi_pending: só um produto OBSERVADO DONE/FAILED tira o
                    volume de pendente.  Um interpolado na chave dele também
                    tirava, mas o volumes_to_convert ainda quer substituí-lo:
                    a retenção do 21.mvol podia apagar o .mvol antes da
                    conversão.  Alcance sem preset ativo não fica mais
                    pendente para sempre (CASE NULL), coerente com a fila
                  - DROP + CREATE da view numa transação: o 21.mvol nunca vê
                    o banco sem a view
                  - attempts conta na reserva (claim_conversion), não no fim:
                    um volume que derruba o processo (OOM, segfault no
                    Py-ART) era retomado como PROCESSING preso para sempre,
                    sem nunca chegar a max_retries.  O preso que esgotou as
                    tentativas vira FAILED (fail_stale_conversions)
                  - finish/fail_conversion só mexem num observed PROCESSING
                    (a reserva desta execução) e dizem se mexeram

2026.set  mlabru  - tabela cappi_products (chave radar, alcance, timestamp,
                    preset): séries, interpolados e o estado da conversão
                    saem de volumes.status.  series_rows() lê os produtos
                    DONE do preset ativo de cada alcance (nwc_presets);
                    pending_rows() dá os volumes ainda sem produto final;
                    insert_interpolated() e delete_product() (antes
                    delete_volume) passam a usar cappi_products.
                  - sequences.preset (ALTER TABLE em banco antigo).
                  - view cappi_pending (volumes sem produto final no preset
                    ativo): contrato com a retenção do 21.mvol (volPurge),
                    que não apaga o .mvol de um volume pendente; recriada por
                    create_schema quando os presets ativos mudam.
                    pending_rows() lê dela.
                  - conversão (nwcConvert): volumes_to_convert(),
                    claim_conversion(), finish_conversion(),
                    fail_conversion() e upsert_radar_site() (este volta:
                    radar_sites é gravada por quem converte).

2026.set  mlabru  - ensure_schema(): os estágios garantem o esquema no início.
                    O nowcast.sh só criava o esquema se o catalog.db não
                    existisse, mas no deploy o banco é criado antes pelo
                    vol_dbase do 21.mvol, sem a tabela sequences (que é só
                    do nowcast) -> "no such table: sequences" no fim do
                    nwcSequences.  create_schema() usa o busy_timeout
                    do config (o 21.mvol pode estar escrevendo).

2026.set  mlabru  - scan/process passaram para o 21.mvol: insert_volume(),
                    update_volume(), volume_exists(), volumes_to_process(),
                    get_radar_site() e upsert_radar_site() removidos.
                  - novos: series_rows(), insert_interpolated(),
                    delete_volume() e replace_sequences().
                  - import config -> nwc_config; timeout vem de
                    [database] busy_timeout.
                  - processing_log.created_at em ISO-8601 UTC, como no
                    21.mvol (datetime('now') gravava sem fuso).

2026.jun  mlabru  - transaction() usa BEGIN; transaction_write() usa
                    BEGIN IMMEDIATE.  WAL e timeout para acesso concorrente.

2026.jun  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import contextlib
import datetime
import logging
import re
import sqlite3
import typing

# locals
import work.nwc_config as cf
import work.nwc_presets as ps

# < constants >────────────────────────────────────────────────────────────────────────

# status do pipeline
CI_STATUS_NEW          = 0
CI_STATUS_PROCESSING   = 1
CI_STATUS_DONE         = 2
CI_STATUS_FAILED       = 3
CI_STATUS_INTERPOLATED = 4
CI_STATUS_PREDICTED    = 5

# origem de um produto (cappi_products.origin)
CS_ORIGIN_OBSERVED     = "observed"
CS_ORIGIN_INTERPOLATED = "interpolated"
CS_ORIGIN_PREDICTED    = "predicted"

# SQL — criação das tabelas e índices
CS_CREATE_VOLUMES = """
CREATE TABLE IF NOT EXISTS volumes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    radar          TEXT    NOT NULL,
    alcance        INTEGER NOT NULL,
    timestamp      INTEGER NOT NULL,
    fullpath       TEXT    NOT NULL,
    filesize       INTEGER,
    sha1           TEXT,
    has_mvol       INTEGER DEFAULT 1,
    has_cappi      INTEGER DEFAULT 0,
    cappi_path     TEXT,
    interpolated   INTEGER DEFAULT 0,
    predicted      INTEGER DEFAULT 0,
    status         INTEGER DEFAULT 0,
    rain_fraction  REAL,
    valid_fraction REAL,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(radar, alcance, timestamp))"""

# um CAPPI por (volume, preset).  status: CI_STATUS_PROCESSING/DONE/FAILED
# (a conversão de um observado); interpolado/previsto nasce DONE.  attempts:
# conversões tentadas (retry até o limite do conversor).  cappi_path absoluto
# em <cappi_dir>/<preset>/<R>_<A>/<R>_<A>_<ts>.npy
CS_CREATE_PRODUCTS = """
CREATE TABLE IF NOT EXISTS cappi_products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    radar          TEXT    NOT NULL,
    alcance        INTEGER NOT NULL,
    timestamp      INTEGER NOT NULL,
    preset         TEXT    NOT NULL,
    origin         TEXT    NOT NULL,
    status         INTEGER NOT NULL DEFAULT 1,
    attempts       INTEGER NOT NULL DEFAULT 0,
    cappi_path     TEXT,
    rain_fraction  REAL,
    valid_fraction REAL,
    created_at     TEXT,
    updated_at     TEXT,
    UNIQUE(radar, alcance, timestamp, preset))"""

# volumes observados (com .mvol) sem produto observado final (DONE ou FAILED)
# no preset ativo do alcance (um sintético na chave não conta: a conversão
# ainda vai substituí-lo).  É um contrato com o 21.mvol: a retenção (volPurge.py) não
# apaga o .mvol de um volume que está aqui, sem precisar conhecer os presets.
# View não aceita parâmetros: ids e status vão no texto (pending_view_sql)
CS_VIEW_PENDING = "cappi_pending"

# preset no fim: é onde o ALTER TABLE o põe num banco antigo
CS_CREATE_SEQUENCES = """
CREATE TABLE IF NOT EXISTS sequences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    radar      TEXT    NOT NULL,
    alcance    INTEGER NOT NULL,
    input_1    INTEGER NOT NULL,
    input_2    INTEGER NOT NULL,
    input_3    INTEGER NOT NULL,
    input_4    INTEGER NOT NULL,
    target     INTEGER NOT NULL,
    quality    INTEGER DEFAULT 0,
    created_at TEXT,
    preset     TEXT)"""

CS_CREATE_LOG = """
CREATE TABLE IF NOT EXISTS processing_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    radar      TEXT NOT NULL,
    alcance    INTEGER NOT NULL,
    timestamp  INTEGER NOT NULL,
    step       TEXT,
    status     INTEGER,
    message    TEXT,
    created_at TEXT)"""

CS_CREATE_RADAR_SITES = """
CREATE TABLE IF NOT EXISTS radar_sites (
    radar      TEXT PRIMARY KEY,
    latitude   REAL NOT NULL,
    longitude  REAL NOT NULL,
    altitude   REAL NOT NULL,
    updated_at TEXT)"""

CS_CREATE_INDEX_1 = "CREATE INDEX IF NOT EXISTS idx_timestamp        ON volumes(timestamp)"
CS_CREATE_INDEX_2 = "CREATE INDEX IF NOT EXISTS idx_status           ON volumes(status)"
CS_CREATE_INDEX_3 = "CREATE INDEX IF NOT EXISTS idx_radar_timestamp  ON volumes(radar, alcance, timestamp)"
CS_CREATE_INDEX_4 = "CREATE INDEX IF NOT EXISTS idx_sequences_target ON sequences(target)"
CS_CREATE_INDEX_5 = "CREATE INDEX IF NOT EXISTS idx_products_status  ON cappi_products(status)"

# processing_log (texto, por contrato com o 21.mvol).  A conversão usa o mesmo
# step do volProcess do 21.mvol: o histórico de um volume é contínuo
CS_STEP_INTERPOLATE = "interpolate"
CS_STEP_PROCESS = "process_volume"
CS_LOG_SUCCESS = "SUCCESS"
CS_LOG_FAILED  = "FAILED"

# status de volumes que tinham CAPPI antes de cappi_products (só as migrações
# nwc_purge_interp, nwc_move_cappi e nwc_migrate_products usam)
CT_CAPPI_STATUS = (CI_STATUS_DONE, CI_STATUS_INTERPOLATED, CI_STATUS_PREDICTED)

# < logging >──────────────────────────────────────────────────────────────────────────

M_LOG = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════════════
# SQL gerado a partir dos presets ativos
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def active_preset_sql(fs_alcance_column: str) -> tuple[str, tuple]:
    """
    expressão SQL com o id do preset ativo do alcance em fs_alcance_column
    (NULL para alcance sem preset), e seus parâmetros (para os "?")

    ex.: ("CASE p.alcance WHEN ? THEN ? WHEN ? THEN ? END",
          (250, "nwc250v2", 400, "nwc400v2"))
    """
    llst_params = []

    for li_alcance, ls_preset_id in sorted(ps.MDCT_ACTIVE.items()):
        llst_params.append(li_alcance)
        llst_params.append(ls_preset_id)

    ls_when_clauses = " ".join(["WHEN ? THEN ?"] * len(ps.MDCT_ACTIVE))

    return f"CASE {fs_alcance_column} {ls_when_clauses} END", tuple(llst_params)

# ─────────────────────────────────────────────────────────────────────────────────────
def pending_view_sql() -> str:
    """
    CREATE VIEW de cappi_pending com os presets ativos de nwc_presets.

    uma view não aceita parâmetros ("?"), então os ids vão no texto como
    literais -- por isso cada id é conferido antes (só [a-z0-9]).  É a mesma
    regra de active_preset_sql, escrita de outro jeito.

    :raises ValueError: id de preset com caractere impróprio
    """
    for ls_preset_id in ps.MDCT_ACTIVE.values():
        if not re.fullmatch(r"[a-z0-9]+", ls_preset_id):
            raise ValueError(f"id de preset impróprio para SQL literal: {ls_preset_id!r}")

    ls_active_preset_case = " ".join(f"WHEN {li_alcance} THEN '{ls_preset_id}'"
                                     for li_alcance, ls_preset_id in sorted(ps.MDCT_ACTIVE.items()))

    return f"""CREATE VIEW {CS_VIEW_PENDING} AS
SELECT v.radar, v.alcance, v.timestamp, v.fullpath
FROM   volumes v
WHERE  v.has_mvol = 1
AND    v.status NOT IN ({CI_STATUS_INTERPOLATED}, {CI_STATUS_PREDICTED})
AND    CASE v.alcance {ls_active_preset_case} END IS NOT NULL
AND    NOT EXISTS (SELECT 1
                   FROM   cappi_products p
                   WHERE  p.radar     = v.radar
                   AND    p.alcance   = v.alcance
                   AND    p.timestamp = v.timestamp
                   AND    p.preset    = CASE v.alcance {ls_active_preset_case} END
                   AND    p.origin    = '{CS_ORIGIN_OBSERVED}'
                   AND    p.status   IN ({CI_STATUS_DONE}, {CI_STATUS_FAILED}))"""

# ─────────────────────────────────────────────────────────────────────────────────────
def utc_now_iso() -> str:
    """
    instante atual em ISO-8601 UTC (formato de created_at/updated_at)
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

# ─────────────────────────────────────────────────────────────────────────────────────
def _iso_cutoff(fi_seconds_ago: int) -> str:
    """
    instante de fi_seconds_ago segundos atrás, em ISO-8601 UTC.  Mesmo
    formato de updated_at, então "updated_at < corte" pode ser comparado
    como texto no SQL
    """
    ldt_cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(seconds=fi_seconds_ago))

    return ldt_cutoff.isoformat()

# < SDatabase >────────────────────────────────────────────────────────────────────────

class SDatabase:
    """
    uma conexão com o banco SQLite do pipeline.  Não crie diretamente: use
    os context managers transaction() ou transaction_write(), que fazem
    commit/rollback e fecham a conexão.

    os métodos que leem devolvem list[dict] (ou tuplas): os dados continuam
    válidos depois que a conexão fecha (sqlite3.Row não continua).
    """
    # ---------------------------------------------------------------------------------
    def __init__(self):
        """
        abre a conexão com o SQLite e configura opções de desempenho e
        integridade.

        - timeout: quanto esperar por um lock de outro processo (o 21.mvol)
          antes de levantar "database is locked";
        - check_same_thread=False: a conexão pode ser usada por outra thread
          (nunca por dois processos);
        - WAL: leitores não bloqueiam o escritor, e vice-versa.
        """
        self.conn = sqlite3.connect(cf.G_CONFIG.database,
                                    timeout=cf.G_CONFIG.busy_timeout_ms / 1000.0,
                                    check_same_thread=False)

        # rows como sqlite3.Row (acesso por nome de coluna)
        self.conn.row_factory = sqlite3.Row

        # o PRAGMA journal_mode num banco ainda não-WAL precisa de lock e
        # pode levantar "database is locked": fecha a conexão, senão ela
        # vaza (quem chama nunca recebe o objeto para fechar)
        try:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")

            self.cursor = self.conn.cursor()

        except Exception:
            self.conn.close()
            raise

    # ═════════════════════════════════════════════════════════════════════════════════
    # transação e SQL avulso
    # ═════════════════════════════════════════════════════════════════════════════════

    # ---------------------------------------------------------------------------------
    def begin(self) -> None:
        """
        inicia uma transação de leitura (BEGIN DEFERRED: o lock só é pedido
        quando necessário)
        """
        self.cursor.execute("BEGIN")

    # ---------------------------------------------------------------------------------
    def begin_write(self) -> None:
        """
        inicia uma transação de escrita (BEGIN IMMEDIATE): o lock de escrita
        é pedido já aqui, e não no primeiro UPDATE -- um conflito aparece no
        início do bloco, antes de qualquer trabalho
        """
        self.cursor.execute("BEGIN IMMEDIATE")

    # ---------------------------------------------------------------------------------
    def commit(self) -> None:
        """
        confirma as alterações da transação corrente
        """
        self.conn.commit()

    # ---------------------------------------------------------------------------------
    def rollback(self) -> None:
        """
        desfaz todas as alterações da transação corrente
        """
        self.conn.rollback()

    # ---------------------------------------------------------------------------------
    def close(self) -> None:
        """
        fecha a conexão com o banco
        """
        self.conn.close()

    # ---------------------------------------------------------------------------------
    def execute(self, fs_sql: str, ft_params: tuple = ()):
        """
        executa um SQL avulso com parâmetros posicionais ("?").  Usado só
        pelas migrações; os estágios usam os métodos com nome.

        :returns: o cursor (para fetchall/fetchone/rowcount)
        """
        return self.cursor.execute(fs_sql, ft_params)

    # ---------------------------------------------------------------------------------
    def _fetch_all_as_dicts(self) -> list[dict]:
        """
        todas as linhas do último SELECT, como list[dict]
        """
        return [dict(l_row) for l_row in self.cursor.fetchall()]

    # ═════════════════════════════════════════════════════════════════════════════════
    # conversão .mvol -> CAPPI (nwcConvert)
    # ═════════════════════════════════════════════════════════════════════════════════

    # ---------------------------------------------------------------------------------
    def volumes_to_convert(self, fi_max_retries: int, fi_stale_after_s: int) -> list[dict]:
        """
        a fila de conversão, ordenada por timestamp: volumes observados (com
        .mvol, alcance com preset ativo) cujo produto no preset ativo

        - não existe, ou
        - é um sintético (o observado o substitui), ou
        - é FAILED com menos de fi_max_retries tentativas, ou
        - é PROCESSING há mais de fi_stale_after_s segundos (processo morto
          no meio do pool) com tentativas sobrando

        :returns: list[dict] (radar, alcance, timestamp, fullpath, preset,
                  product_id, product_path); product_id None = sem produto
        """
        ls_case, lt_preset_params = active_preset_sql("v.alcance")
        ls_cutoff = _iso_cutoff(fi_stale_after_s)

        # um parâmetro (ou grupo) por "?" do SQL, na ordem em que aparecem
        llst_params = []
        llst_params += lt_preset_params                                # AS preset
        llst_params += lt_preset_params                                # JOIN p.preset
        llst_params += [CI_STATUS_INTERPOLATED, CI_STATUS_PREDICTED]   # v.status NOT IN
        llst_params += lt_preset_params                                # IS NOT NULL
        llst_params += [CS_ORIGIN_OBSERVED]                            # p.origin !=
        llst_params += [CI_STATUS_FAILED, fi_max_retries]              # FAILED com sobra
        llst_params += [CI_STATUS_PROCESSING, fi_max_retries, ls_cutoff]   # PROCESSING preso

        self.cursor.execute(f"""SELECT v.radar, v.alcance, v.timestamp, v.fullpath,
                                       {ls_case} AS preset,
                                       p.id AS product_id, p.cappi_path AS product_path
                                FROM   volumes v
                                LEFT JOIN cappi_products p ON  p.radar     = v.radar
                                                           AND p.alcance   = v.alcance
                                                           AND p.timestamp = v.timestamp
                                                           AND p.preset    = {ls_case}
                                WHERE  v.has_mvol = 1
                                AND    v.status NOT IN (?, ?)
                                AND    {ls_case} IS NOT NULL
                                AND    (p.id IS NULL
                                        OR p.origin != ?
                                        OR (p.status = ? AND p.attempts < ?)
                                        OR (p.status = ? AND p.attempts < ?
                                                         AND (p.updated_at IS NULL
                                                              OR p.updated_at < ?)))
                                ORDER BY v.timestamp""",
                            llst_params)

        return self._fetch_all_as_dicts()

    # ---------------------------------------------------------------------------------
    def claim_conversion(self, fdct_row: dict) -> None:
        """
        reserva a conversão de um volume (linha de volumes_to_convert): o
        produto do preset passa a observed PROCESSING.  Um sintético com a
        mesma chave vira esse produto (o observado tem prioridade); o
        caminho dele fica em fdct_row["product_path"] para quem converte.

        a tentativa conta aqui (attempts + 1), não no fim: se o processo
        morrer na conversão, o PROCESSING preso já carrega a tentativa
        """
        if fdct_row["product_id"] is None:
            self.insert_product(fdct_row["radar"], fdct_row["alcance"], fdct_row["timestamp"],
                                fdct_row["preset"], CS_ORIGIN_OBSERVED, CI_STATUS_PROCESSING,
                                None, None, None, fi_attempts=1)
            return

        # observado que volta (FAILED/preso): +1 tentativa, mantém o caminho;
        # sintético que vira observado: tentativas recomeçam em 1, sem caminho
        self.cursor.execute("""UPDATE cappi_products
                               SET    origin     = ?,
                                      status     = ?,
                                      attempts   = CASE WHEN origin = ? THEN attempts + 1 ELSE 1 END,
                                      cappi_path = CASE WHEN origin = ? THEN cappi_path ELSE NULL END,
                                      updated_at = ?
                               WHERE  id         = ?""",
                            (CS_ORIGIN_OBSERVED, CI_STATUS_PROCESSING, CS_ORIGIN_OBSERVED,
                             CS_ORIGIN_OBSERVED, utc_now_iso(), fdct_row["product_id"]))

    # ---------------------------------------------------------------------------------
    def finish_conversion(self, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                          fs_preset: str, fs_cappi_path: str, ff_rain_fraction: float,
                          ff_valid_fraction: float) -> bool:
        """
        registra uma conversão bem-sucedida: DONE, caminho e estatísticas (a
        tentativa já contou na reserva).  Só altera um observed PROCESSING.

        :returns: False se a reserva não é mais desta execução (outra a
                  retomou como presa e já terminou)
        """
        self.cursor.execute("""UPDATE cappi_products
                               SET    status         = ?,
                                      cappi_path     = ?,
                                      rain_fraction  = ?,
                                      valid_fraction = ?,
                                      updated_at     = ?
                               WHERE  radar          = ?
                               AND    alcance        = ?
                               AND    timestamp      = ?
                               AND    preset         = ?
                               AND    origin         = ?
                               AND    status         = ?""",
                            (CI_STATUS_DONE, fs_cappi_path, ff_rain_fraction, ff_valid_fraction,
                             utc_now_iso(), fs_radar, fi_alcance, fi_timestamp, fs_preset,
                             CS_ORIGIN_OBSERVED, CI_STATUS_PROCESSING))

        return 1 == self.cursor.rowcount

    # ---------------------------------------------------------------------------------
    def fail_conversion(self, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                        fs_preset: str) -> int:
        """
        registra uma conversão que falhou: FAILED (a tentativa já contou na
        reserva).  Só altera um observed PROCESSING: se outra execução
        retomou e terminou o volume, o resultado dela fica.

        :returns: tentativas feitas até agora (incluindo esta); 0 se o
                  produto não existe
        """
        self.cursor.execute("""UPDATE cappi_products
                               SET    status     = ?,
                                      updated_at = ?
                               WHERE  radar      = ?
                               AND    alcance    = ?
                               AND    timestamp  = ?
                               AND    preset     = ?
                               AND    origin     = ?
                               AND    status     = ?""",
                            (CI_STATUS_FAILED, utc_now_iso(), fs_radar, fi_alcance,
                             fi_timestamp, fs_preset, CS_ORIGIN_OBSERVED, CI_STATUS_PROCESSING))

        self.cursor.execute("""SELECT attempts FROM cappi_products
                               WHERE  radar = ? AND alcance = ? AND timestamp = ? AND preset = ?""",
                            (fs_radar, fi_alcance, fi_timestamp, fs_preset))

        l_row = self.cursor.fetchone()

        if l_row is None:
            return 0

        return int(l_row[0])

    # ---------------------------------------------------------------------------------
    def fail_stale_conversions(self, fi_max_retries: int, fi_stale_after_s: int) -> list[dict]:
        """
        PROCESSING preso há mais de fi_stale_after_s segundos que já esgotou
        as tentativas -> FAILED.  É o volume que derruba o processo a cada
        tentativa: sem isso ficaria PROCESSING (pendente) para sempre.

        :returns: list[dict] (radar, alcance, timestamp, preset, attempts)
                  dos que viraram FAILED
        """
        # o mesmo WHERE no SELECT (para devolver quem muda) e no UPDATE
        ls_where = """WHERE  origin   = ?
                      AND    status   = ?
                      AND    attempts >= ?
                      AND    (updated_at IS NULL OR updated_at < ?)"""
        lt_where_params = (CS_ORIGIN_OBSERVED, CI_STATUS_PROCESSING, fi_max_retries,
                           _iso_cutoff(fi_stale_after_s))

        self.cursor.execute(f"SELECT radar, alcance, timestamp, preset, attempts "
                            f"FROM cappi_products {ls_where}", lt_where_params)
        llst_failed_rows = self._fetch_all_as_dicts()

        self.cursor.execute(f"UPDATE cappi_products SET status = ?, updated_at = ? {ls_where}",
                            (CI_STATUS_FAILED, utc_now_iso(), *lt_where_params))

        return llst_failed_rows

    # ---------------------------------------------------------------------------------
    def upsert_radar_site(self, fs_radar: str, ff_latitude: float, ff_longitude: float,
                          ff_altitude: float) -> None:
        """
        insere ou atualiza a posição de uma estação (idempotente), como o
        vol_dbase do 21.mvol
        """
        self.cursor.execute("""INSERT INTO radar_sites (radar, latitude, longitude,
                                                        altitude, updated_at)
                               VALUES (?, ?, ?, ?, ?)
                               ON CONFLICT(radar) DO UPDATE SET
                                   latitude   = excluded.latitude,
                                   longitude  = excluded.longitude,
                                   altitude   = excluded.altitude,
                                   updated_at = excluded.updated_at""",
                            (fs_radar, ff_latitude, ff_longitude, ff_altitude, utc_now_iso()))

    # ═════════════════════════════════════════════════════════════════════════════════
    # produtos (cappi_products)
    # ═════════════════════════════════════════════════════════════════════════════════

    # ---------------------------------------------------------------------------------
    def insert_product(self, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                       fs_preset: str, fs_origin: str, fi_status: int,
                       fs_cappi_path: str | None, ff_rain_fraction: float | None,
                       ff_valid_fraction: float | None,
                       fs_created_at: str | None = None, fi_attempts: int = 0) -> None:
        """
        insere um produto em cappi_products

        :param fs_origin: CS_ORIGIN_*
        :param fi_status: CI_STATUS_PROCESSING/DONE/FAILED
        :param fs_created_at: created_at (ISO-8601 UTC); None = agora.  A
                              migração preserva o do volume de origem.
        :param fi_attempts: tentativas de conversão já feitas
        :raises sqlite3.IntegrityError: já existe produto para a chave
        """
        ls_now = utc_now_iso()

        self.cursor.execute("""INSERT INTO cappi_products (radar, alcance, timestamp, preset,
                                                           origin, status, attempts, cappi_path,
                                                           rain_fraction, valid_fraction,
                                                           created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (fs_radar, fi_alcance, fi_timestamp, fs_preset, fs_origin,
                             fi_status, fi_attempts, fs_cappi_path, ff_rain_fraction,
                             ff_valid_fraction, fs_created_at or ls_now, ls_now))

    # ---------------------------------------------------------------------------------
    def insert_interpolated(self, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                            fs_preset: str, fs_cappi_path: str,
                            ff_rain_fraction: float, ff_valid_fraction: float) -> None:
        """
        insere um produto interpolado (DONE).  Não há volume em volumes: o
        interpolado não veio de um .mvol.

        :raises sqlite3.IntegrityError: já existe produto para a chave
        """
        self.insert_product(fs_radar, fi_alcance, fi_timestamp, fs_preset,
                            CS_ORIGIN_INTERPOLATED, CI_STATUS_DONE, fs_cappi_path,
                            ff_rain_fraction, ff_valid_fraction)

    # ---------------------------------------------------------------------------------
    def delete_product(self, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                       fs_preset: str) -> int:
        """
        apaga um produto sintético (interpolado/previsto).  Nunca apaga um
        observado (o WHERE só aceita as origens sintéticas).

        :returns: número de linhas apagadas (0 ou 1)
        """
        self.cursor.execute("""DELETE FROM cappi_products
                               WHERE  radar     = ?
                               AND    alcance   = ?
                               AND    timestamp = ?
                               AND    preset    = ?
                               AND    origin   IN (?, ?)""",
                            (fs_radar, fi_alcance, fi_timestamp, fs_preset,
                             CS_ORIGIN_INTERPOLATED, CS_ORIGIN_PREDICTED))

        return self.cursor.rowcount

    # ═════════════════════════════════════════════════════════════════════════════════
    # séries e interpolação (nwc_series, nwcInterpolate)
    # ═════════════════════════════════════════════════════════════════════════════════

    # ---------------------------------------------------------------------------------
    def series_rows(self) -> list[dict]:
        """
        produtos DONE do preset ativo de cada alcance (observados,
        interpolados e previstos), ordenados por (radar, alcance, timestamp).
        Produtos de outros presets (legado, versões antigas) ficam de fora.

        fullpath é o .mvol de origem (volumes.fullpath) ou, para um sintético,
        o próprio CAPPI.

        :returns: list[dict] (radar, alcance, timestamp, preset, origin,
                  fullpath, cappi_path, valid_fraction, rain_fraction)
        """
        ls_case, lt_preset_params = active_preset_sql("p.alcance")

        self.cursor.execute(f"""SELECT p.radar, p.alcance, p.timestamp, p.preset, p.origin,
                                       COALESCE(v.fullpath, p.cappi_path) AS fullpath,
                                       p.cappi_path, p.valid_fraction, p.rain_fraction
                                FROM   cappi_products p
                                LEFT JOIN volumes v ON  v.radar     = p.radar
                                                    AND v.alcance   = p.alcance
                                                    AND v.timestamp = p.timestamp
                                WHERE  p.status = ?
                                AND    p.cappi_path IS NOT NULL
                                AND    p.preset = {ls_case}
                                ORDER BY p.radar, p.alcance, p.timestamp""",
                            (CI_STATUS_DONE, *lt_preset_params))

        return self._fetch_all_as_dicts()

    # ---------------------------------------------------------------------------------
    def pending_rows(self) -> list[dict]:
        """
        volumes observados (com .mvol) que ainda não têm produto final (DONE
        ou FAILED) no preset ativo do seu alcance (view cappi_pending): a
        conversão ainda vai acontecer (ou está acontecendo), e uma lacuna que
        os contém deve esperar em vez de ser interpolada.

        :returns: list[dict] (radar, alcance, timestamp)
        """
        self.cursor.execute(f"SELECT radar, alcance, timestamp FROM {CS_VIEW_PENDING}")

        return self._fetch_all_as_dicts()

    # ---------------------------------------------------------------------------------
    def occupied_rows(self) -> list[dict]:
        """
        chaves de produtos do preset ativo que não estão DONE (FAILED ou
        PROCESSING, de qualquer origem).  Não entram na série (series_rows só
        traz DONE), mas ocupam a chave: um interpolado no mesmo horário daria
        IntegrityError.  O caso típico é o observado cuja conversão falhou:
        não está pendente (cappi_pending exclui FAILED) e, com cadência
        regular, o slot faltante cai exatamente no horário dele.

        :returns: list[dict] (radar, alcance, timestamp)
        """
        ls_case, lt_preset_params = active_preset_sql("p.alcance")

        self.cursor.execute(f"""SELECT p.radar, p.alcance, p.timestamp
                                FROM   cappi_products p
                                WHERE  p.status <> ?
                                AND    p.preset = {ls_case}""",
                            (CI_STATUS_DONE, *lt_preset_params))

        return self._fetch_all_as_dicts()

    # ---------------------------------------------------------------------------------
    def get_volume(self, fs_radar: str, fi_alcance: int, fi_timestamp: int) -> dict | None:
        """
        registro completo de um volume pela chave (radar, alcance, timestamp),
        ou None se não existir (usado nos testes)
        """
        self.cursor.execute("""SELECT *
                               FROM   volumes
                               WHERE  radar     = ?
                               AND    alcance   = ?
                               AND    timestamp = ?""", (fs_radar, fi_alcance, fi_timestamp))

        l_row = self.cursor.fetchone()

        if l_row is None:
            return None

        return dict(l_row)

    # ═════════════════════════════════════════════════════════════════════════════════
    # sequências (nwcSequences)
    # ═════════════════════════════════════════════════════════════════════════════════

    # ---------------------------------------------------------------------------------
    def replace_sequences(self, flst_rows: list) -> None:
        """
        reconstrói a tabela sequences inteira (DELETE + INSERT, na transação
        de quem chama).

        :param flst_rows: tuplas (radar, alcance, input_1, input_2, input_3,
                          input_4, target, quality, preset)
        """
        self.cursor.execute("DELETE FROM sequences")

        self.cursor.executemany("""INSERT INTO sequences (radar, alcance, input_1, input_2,
                                                          input_3, input_4, target, quality,
                                                          preset, created_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                                flst_rows)

    # ---------------------------------------------------------------------------------
    def sequences_rows(self) -> list[tuple]:
        """
        conteúdo atual da tabela sequences, no formato de replace_sequences

        :returns: tuplas (radar, alcance, input_1, input_2, input_3, input_4,
                  target, quality, preset)
        """
        self.cursor.execute("""SELECT radar, alcance, input_1, input_2, input_3, input_4,
                                      target, quality, preset
                               FROM   sequences
                               ORDER BY radar, alcance, target""")

        return [tuple(l_row) for l_row in self.cursor.fetchall()]

    # ═════════════════════════════════════════════════════════════════════════════════
    # processing_log
    # ═════════════════════════════════════════════════════════════════════════════════

    # ---------------------------------------------------------------------------------
    def insert_log(self, fs_radar: str, fi_alcance: int, fi_timestamp: int,
                   fs_step: str, fs_status: str, fs_message: str) -> None:
        """
        insere uma entrada em processing_log

        :param fs_step: CS_STEP_PROCESS ou CS_STEP_INTERPOLATE
        :param fs_status: CS_LOG_SUCCESS ou CS_LOG_FAILED
        """
        self.cursor.execute("""INSERT INTO processing_log (radar, alcance, timestamp, step,
                                                           status, message, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (fs_radar, fi_alcance, fi_timestamp, fs_step, fs_status,
                             fs_message, utc_now_iso()))

# ═════════════════════════════════════════════════════════════════════════════════════
# transações
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _transaction(fb_write: bool) -> typing.Iterator[SDatabase]:
    """
    abre uma conexão, inicia a transação, faz commit no sucesso e rollback
    (propagando a exceção) no erro, e sempre fecha a conexão
    """
    ldb = SDatabase()

    try:
        if fb_write:
            ldb.begin_write()

        else:
            ldb.begin()

        yield ldb

        ldb.commit()

    except Exception:
        # desfaz qualquer alteração parcial e deixa a exceção subir
        ldb.rollback()
        raise

    finally:
        ldb.close()

# ─────────────────────────────────────────────────────────────────────────────────────
def transaction():
    """
    transação de leitura (BEGIN DEFERRED)

    uso:
        with db.transaction() as ldb:
            llst_rows = ldb.series_rows()
    """
    return _transaction(False)

# ─────────────────────────────────────────────────────────────────────────────────────
def transaction_write():
    """
    transação de escrita (BEGIN IMMEDIATE).  Use sempre que o bloco
    modificar dados, e mantenha o bloco curto (o banco é compartilhado)

    uso:
        with db.transaction_write() as ldb:
            ldb.insert_log(...)
    """
    return _transaction(True)

# ═════════════════════════════════════════════════════════════════════════════════════
# esquema
# ═════════════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────────────
def create_schema() -> None:
    """
    cria (ou valida) o esquema.  Idempotente (CREATE ... IF NOT EXISTS).

    num banco criado pelo 21.mvol só cria o que é do nowcast (cappi_products,
    sequences e seus índices): o DDL das tabelas compartilhadas é idêntico ao
    do vol_dbase, e CREATE ... IF NOT EXISTS sobre tabela existente não
    escreve.  Um sequences antigo, sem a coluna preset, ganha a coluna, e a
    view cappi_pending é recriada se os presets ativos mudaram.
    """
    cf.G_CONFIG.database.parent.mkdir(parents=True, exist_ok=True)

    # DDL fora de transaction(): não precisa de rollback
    l_connection = sqlite3.connect(cf.G_CONFIG.database,
                                   timeout=cf.G_CONFIG.busy_timeout_ms / 1000.0)

    try:
        _create_tables_and_indexes(l_connection)
        _add_sequences_preset_column(l_connection)
        _refresh_pending_view(l_connection)

        l_connection.commit()

    finally:
        l_connection.close()

# ─────────────────────────────────────────────────────────────────────────────────────
def _create_tables_and_indexes(f_connection: sqlite3.Connection) -> None:
    """
    CREATE TABLE/INDEX IF NOT EXISTS de todas as tabelas e índices
    """
    for ls_sql in (CS_CREATE_VOLUMES, CS_CREATE_PRODUCTS, CS_CREATE_SEQUENCES,
                   CS_CREATE_LOG, CS_CREATE_RADAR_SITES, CS_CREATE_INDEX_1,
                   CS_CREATE_INDEX_2, CS_CREATE_INDEX_3, CS_CREATE_INDEX_4,
                   CS_CREATE_INDEX_5):
        f_connection.execute(ls_sql.strip())

# ─────────────────────────────────────────────────────────────────────────────────────
def _add_sequences_preset_column(f_connection: sqlite3.Connection) -> None:
    """
    sequences de antes de cappi_products não tem a coluna preset: acrescenta
    """
    # PRAGMA table_info: uma linha por coluna; o nome é o campo 1
    lset_columns = set()

    for l_row in f_connection.execute("PRAGMA table_info(sequences)"):
        lset_columns.add(l_row[1])

    if "preset" not in lset_columns:
        f_connection.execute("ALTER TABLE sequences ADD COLUMN preset TEXT")

# ─────────────────────────────────────────────────────────────────────────────────────
def _refresh_pending_view(f_connection: sqlite3.Connection) -> None:
    """
    recria a view cappi_pending só se os presets ativos mudaram (no caso
    comum não escreve nada)
    """
    ls_view_sql = pending_view_sql()

    l_row = f_connection.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?",
                                 (CS_VIEW_PENDING,)).fetchone()

    if l_row is not None and l_row[0] == ls_view_sql:
        return

    # DDL não abre transação sozinho no sqlite3: sem o BEGIN, o 21.mvol
    # (purge_candidates) poderia ver o banco sem a view entre o DROP e o CREATE
    if not f_connection.in_transaction:
        f_connection.execute("BEGIN IMMEDIATE")

    f_connection.execute(f"DROP VIEW IF EXISTS {CS_VIEW_PENDING}")
    f_connection.execute(ls_view_sql)

# ─────────────────────────────────────────────────────────────────────────────────────
def ensure_schema() -> bool:
    """
    create_schema() para o início de cada estágio: loga e devolve False em
    caso de erro, para o estágio falhar logo, e não depois de processar tudo

    :returns: True se o esquema está completo
    """
    try:
        create_schema()

    except Exception:
        M_LOG.exception("não foi possível criar/validar o esquema em %s", cf.G_CONFIG.database)
        return False

    return True

# < the end >──────────────────────────────────────────────────────────────────────────
