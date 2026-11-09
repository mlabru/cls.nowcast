# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_config
configuração do pipeline: lê o config.ini, valida cada valor e entrega um
SConfig imutável em cf.G_CONFIG.  Nenhum outro módulo lê o config.ini.

fluxo (init(), chamado uma vez por programa, antes do argparse):
  1. load_config(): lê o arquivo; cada seção tem a sua função _read_<seção>
     que valida os valores e levanta ValueError citando seção e opção;
  2. check_test_env(): com MET_ENV=test, recusa caminhos fora de MET_TST_ROOT;
  3. create_directories(): cria cappi_dir, log_dir e o diretório do banco;
  4. configure_logging(): arquivo + console, em UTC.

o [paths] pode ser sobreposto pelo ambiente (NWC_CAPPI_DIR, NWC_DBASE_FILE,
NWC_LOG_DIR; vazio = config.ini).  Precedência: CLI > ambiente > config.ini.

para acrescentar uma opção: um campo em SConfig (com default, se a opção for
opcional), a leitura/validação na _read_<seção> correspondente e um teste em
test/test_nwc_config.py.

2026.out  mlabru  refatoração para legibilidade: load_config dividido em
                  _read_<seção>; [convert] ausente tratado sem criar a seção
                  no parser; import configparser sem o apelido "cp" (que nos
                  outros módulos é o nwc_cappi_path); init() cria os
                  diretórios uma vez só, antes do logging
2026.out  mlabru  - [paths] sobreponível pelo ambiente (NWC_DBASE_FILE,
                    NWC_CAPPI_DIR, NWC_LOG_DIR; vazio = config.ini), como os
                    VOL_*/RSM_*/OFL_* dos projetos irmãos: o tst_env só
                    exporta variáveis, sem gerar um config.ini com sed.
                    Precedência: CLI > ambiente > config.ini.
                  - trava MET_ENV=test em init() (check_test_env): os
                    caminhos resolvidos precisam estar dentro de MET_TST_ROOT.
                    Antes só o shell checava, lendo o config.ini com sed, e
                    um programa rodado direto (dry-run, migração) não era
                    checado.

2026.set  mlabru  - seção opcional [convert] (nwcConvert): workers, max_retries,
                    stale_after_s.

2026.set  mlabru  - [interpolate] max_gap e workers validados (inteiros >= 0;
                    max_gap 0 desliga a interpolação, workers 0 = CPUs − 1):
                    valores negativos passavam pelo config e só falhavam
                    depois (na montagem da Timeline / no mp.Pool), com
                    mensagens que não citavam o config.ini.

2026.set  mlabru  - [interpolate] method (flow | linear) e blend (z | dbz):
                    Farnebäck com média em Z como padrão (ver changelog do
                    nwcInterpolate).

2026.set  mlabru  - scan/process passaram para o 21.mvol: seção [mvol] e as
                    opções raw_dirs, failed_dir, time_step, extensões,
                    remove_mvol, calculate_sha1 e cappi_field removidas,
                    junto com verifica_diretorios().
                  - [training] removida de fato: sequences tem colunas fixas
                    input_1..input_4 + target, então o tamanho da janela é
                    constante do esquema, não configuração.
                  - novas seções [timeline], [interpolate] e [sequences].
                  - handlers de log recebem o nível do config.ini: vários
                    módulos (inclusive o vol_record compartilhado com o
                    21.mvol) fazem M_LOG.setLevel(DEBUG), e o nível do root
                    sozinho não filtra registros de loggers filhos.
                  - logs em UTC, como no 21.mvol.

2026.jun  mlabru  - removido efeito colateral no import: G_CONFIG e
                    create_directories() agora são inicializados
                    explicitamente via init().

2026.jun  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import configparser
import dataclasses
import logging
import os
import pathlib
import sys
import time

# < constants >────────────────────────────────────────────────────────────────────────

# arquivo de configuração
CS_CONFIG_FILE = "config.ini"

# seções obrigatórias
CT_REQUIRED_SECTIONS = ("database", "interpolate", "log", "paths", "sequences", "timeline")

# variáveis de ambiente que sobrepõem o [paths] do config.ini (nwc_cfg.sh)
CDCT_PATH_ENV = { "cappi_dir": "NWC_CAPPI_DIR",
                  "database":  "NWC_DBASE_FILE",
                  "log_dir":   "NWC_LOG_DIR" }

# valores aceitos em [interpolate]
CT_INTERP_METHODS = ("flow", "linear")
CT_INTERP_BLENDS = ("z", "dbz")

# conversão do nível de log
CDCT_LOG_LEVELS = { "CRITICAL": logging.CRITICAL,
                    "DEBUG":    logging.DEBUG,
                    "ERROR":    logging.ERROR,
                    "INFO":     logging.INFO,
                    "WARNING":  logging.WARNING }

# < SConfig >──────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SConfig:
    """
    configuração imutável do pipeline.
    todos os campos são somente-leitura após a construção.
    """
    # diretórios
    cappi_dir: pathlib.Path
    log_dir: pathlib.Path

    # banco: arquivo SQLite e quanto esperar (ms) por um lock de outro processo
    database: pathlib.Path
    busy_timeout_ms: int

    # logging: nível (logging.INFO, ...) e arquivo (dentro de log_dir)
    log_level: int
    log_file: pathlib.Path

    # timeline (segundos)
    interval_s: int
    tolerance_s: int

    # interpolação: maior lacuna interpolada (slots) e processos do pool
    max_gap: int
    workers: int

    # sequências: fração mínima de cobertura (relativa) e de chuva
    min_valid_ratio: float
    min_rain_fraction: float

    # interpolação: método e domínio da média (defaults = produção)
    method: str = "flow"
    blend: str = "z"

    # conversão .mvol -> CAPPI (seção opcional [convert]): processos do pool
    # (0 = CPUs − 1, 1 = serial), tentativas antes do FAILED definitivo e
    # idade (s) de um PROCESSING considerado preso
    convert_workers: int = 0
    convert_max_retries: int = 3
    convert_stale_after_s: int = 3600

# -------------------------------------------------------------------------------------
def check_test_env(f_cfg: SConfig) -> None:
    """
    trava do ambiente de teste (21.mvol/tst_env): com MET_ENV=test, cappi_dir,
    database e log_dir (resolvidos a partir do cwd) precisam estar dentro de
    MET_TST_ROOT -- mesma regra do nwc_cfg.sh.  Lança ValueError antes de
    qualquer escrita; sem MET_ENV=test não faz nada
    """
    if "test" != os.environ.get("MET_ENV"):
        return

    ls_root = os.environ.get("MET_TST_ROOT", "")

    if not ls_root:
        raise ValueError("MET_ENV=test exige MET_TST_ROOT")

    lpth_root = pathlib.Path(os.path.realpath(ls_root))

    for lpth_checked in (f_cfg.cappi_dir, f_cfg.database, f_cfg.log_dir):
        lpth_real = pathlib.Path(os.path.realpath(lpth_checked))

        # estritamente dentro da raiz (o padrão "${root}"/* do shell)
        if lpth_real == lpth_root or not lpth_real.is_relative_to(lpth_root):
            raise ValueError(f"MET_ENV=test: '{lpth_checked}' fora de {ls_root}")

# -------------------------------------------------------------------------------------
def configure_logging(f_cfg: SConfig) -> None:
    """
    configura o logging (arquivo + console) a partir do SConfig.
    deve ser chamada antes de qualquer uso de logging nos demais módulos.
    """
    # formatter em UTC (todo instante do pipeline é UTC)
    l_formatter = logging.Formatter("%(asctime)sZ [%(levelname)-7s] %(message)s",
                                    "%Y-%m-%d %H:%M:%S")
    l_formatter.converter = time.gmtime

    llst_handlers = [logging.FileHandler(f_cfg.log_file, encoding="utf-8"),
                     logging.StreamHandler(sys.stdout)]

    for l_handler in llst_handlers:
        # nível no handler, não só no root (ver changelog de 2026.set)
        l_handler.setLevel(f_cfg.log_level)
        l_handler.setFormatter(l_formatter)

    logging.basicConfig(level=f_cfg.log_level, handlers=llst_handlers, force=True)

# -------------------------------------------------------------------------------------
def create_directories(f_cfg: SConfig) -> None:
    """
    cria os diretórios necessários para o pipeline caso não existam.
    """
    f_cfg.cappi_dir.mkdir(parents=True, exist_ok=True)
    f_cfg.log_dir.mkdir(parents=True, exist_ok=True)

    # diretório pai do banco de dados
    f_cfg.database.parent.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------------------------
def load_config(fs_fname: str = CS_CONFIG_FILE) -> SConfig:
    """
    lê o config.ini e devolve um SConfig validado (o [paths] pode ser
    sobreposto pelo ambiente, ver CDCT_PATH_ENV).

    :param fs_fname: caminho do config.ini (relativo ao diretório corrente)
    :raises FileNotFoundError: arquivo não existe
    :raises ValueError: seção obrigatória faltando ou valor inválido (a
                        mensagem cita a seção e a opção)
    """
    l_parser = _read_config_file(fs_fname)

    ldct_paths = _read_paths(l_parser)
    ldct_log = _read_log(l_parser, ldct_paths["log_dir"])

    return SConfig(**ldct_paths,
                   **ldct_log,
                   busy_timeout_ms=l_parser.getint("database", "busy_timeout", fallback=10000),
                   **_read_timeline(l_parser),
                   **_read_interpolate(l_parser),
                   **_read_sequences(l_parser),
                   **_read_convert(l_parser))

# -------------------------------------------------------------------------------------
def _read_config_file(fs_fname: str) -> configparser.ConfigParser:
    """
    abre o config.ini e confere se as seções obrigatórias existem

    :raises FileNotFoundError: arquivo não existe
    :raises ValueError: seção obrigatória faltando
    """
    # interpolation=None: "%" em valores não é sintaxe de interpolação
    l_parser = configparser.ConfigParser(interpolation=None)

    if not l_parser.read(fs_fname, encoding="utf-8"):
        raise FileNotFoundError(f"arquivo de configuração '{fs_fname}' não encontrado.")

    lset_missing = set(CT_REQUIRED_SECTIONS) - set(l_parser.sections())

    if lset_missing:
        raise ValueError(f"seções faltando no config.ini: {', '.join(sorted(lset_missing))}")

    return l_parser

# -------------------------------------------------------------------------------------
def _read_paths(f_parser: configparser.ConfigParser) -> dict:
    """
    [paths]: cappi_dir, database e log_dir (ambiente > config.ini)
    """
    return {"cappi_dir": _get_path(f_parser, "cappi_dir"),
            "database": _get_path(f_parser, "database"),
            "log_dir": _get_path(f_parser, "log_dir")}

# -------------------------------------------------------------------------------------
def _read_log(f_parser: configparser.ConfigParser, fpth_log_dir: pathlib.Path) -> dict:
    """
    [log]: level (nome do nível) e file (nome do arquivo dentro de log_dir)

    :raises ValueError: nível desconhecido
    """
    ls_level = f_parser.get("log", "level").upper()

    if ls_level not in CDCT_LOG_LEVELS:
        raise ValueError(f"nível de log inválido: {ls_level}")

    return {"log_level": CDCT_LOG_LEVELS[ls_level],
            "log_file": fpth_log_dir / f_parser.get("log", "file")}

# -------------------------------------------------------------------------------------
def _read_timeline(f_parser: configparser.ConfigParser) -> dict:
    """
    [timeline]: interval_s (> 0) e tolerance_s (0..interval_s/2; default
    interval_s/2, ver tline/tl_consts.py)

    :raises ValueError: valores fora dos limites
    """
    li_interval_s = f_parser.getint("timeline", "interval_s")
    li_tolerance_s = f_parser.getint("timeline", "tolerance_s", fallback=li_interval_s // 2)

    if li_interval_s <= 0:
        raise ValueError(f"[timeline] interval_s deve ser > 0: {li_interval_s}")

    if li_tolerance_s < 0 or li_tolerance_s > li_interval_s / 2:
        raise ValueError(f"[timeline] tolerance_s deve estar entre 0 e interval_s/2 "
                         f"({li_interval_s / 2}): {li_tolerance_s}")

    return {"interval_s": li_interval_s, "tolerance_s": li_tolerance_s}

# -------------------------------------------------------------------------------------
def _read_interpolate(f_parser: configparser.ConfigParser) -> dict:
    """
    [interpolate]: max_gap, workers, method e blend

    :raises ValueError: inteiro negativo/inválido, method ou blend desconhecidos
    """
    li_max_gap = _get_non_negative_int(f_parser, "interpolate", "max_gap",
                                       "0 desliga a interpolação")
    li_workers = _get_non_negative_int(f_parser, "interpolate", "workers",
                                       "0 = número de CPUs − 1, 1 = serial", fi_fallback=0)

    ls_method = f_parser.get("interpolate", "method", fallback="flow").lower()
    ls_blend = f_parser.get("interpolate", "blend", fallback="z").lower()

    if ls_method not in CT_INTERP_METHODS:
        raise ValueError(f"[interpolate] method deve ser {'|'.join(CT_INTERP_METHODS)}: "
                         f"{ls_method!r}")

    if ls_blend not in CT_INTERP_BLENDS:
        raise ValueError(f"[interpolate] blend deve ser {'|'.join(CT_INTERP_BLENDS)}: "
                         f"{ls_blend!r}")

    return {"max_gap": li_max_gap, "workers": li_workers,
            "method": ls_method, "blend": ls_blend}

# -------------------------------------------------------------------------------------
def _read_sequences(f_parser: configparser.ConfigParser) -> dict:
    """
    [sequences]: min_valid_ratio e min_rain_fraction (default 0), ambos em [0, 1]

    :raises ValueError: valor fora de [0, 1]
    """
    lf_min_valid_ratio = f_parser.getfloat("sequences", "min_valid_ratio")
    lf_min_rain_fraction = f_parser.getfloat("sequences", "min_rain_fraction", fallback=0.0)

    for ls_option, lf_value in (("min_valid_ratio", lf_min_valid_ratio),
                                ("min_rain_fraction", lf_min_rain_fraction)):
        if not 0.0 <= lf_value <= 1.0:
            raise ValueError(f"[sequences] {ls_option} deve estar em [0, 1]: {lf_value}")

    return {"min_valid_ratio": lf_min_valid_ratio, "min_rain_fraction": lf_min_rain_fraction}

# -------------------------------------------------------------------------------------
def _read_convert(f_parser: configparser.ConfigParser) -> dict:
    """
    [convert] (seção opcional; sem ela, valem os defaults): workers,
    max_retries e stale_after_s

    :raises ValueError: inteiro negativo/inválido
    """
    return {"convert_workers": _get_non_negative_int(f_parser, "convert", "workers",
                                                     "0 = número de CPUs − 1, 1 = serial",
                                                     fi_fallback=0),
            "convert_max_retries": _get_non_negative_int(f_parser, "convert", "max_retries",
                                                         "0 = nunca tenta", fi_fallback=3),
            "convert_stale_after_s": _get_non_negative_int(f_parser, "convert", "stale_after_s",
                                                           "0 = todo PROCESSING é retomado",
                                                           fi_fallback=3600)}

# -------------------------------------------------------------------------------------
def _get_path(f_parser: configparser.ConfigParser, fs_option: str) -> pathlib.Path:
    """
    caminho do [paths]: a variável de ambiente de CDCT_PATH_ENV, se não
    vazia, senão o config.ini
    """
    ls_from_environment = os.environ.get(CDCT_PATH_ENV[fs_option], "").strip()

    if ls_from_environment:
        return pathlib.Path(ls_from_environment)

    return pathlib.Path(f_parser.get("paths", fs_option))

# -------------------------------------------------------------------------------------
def _get_non_negative_int(f_parser: configparser.ConfigParser, fs_section: str,
                          fs_option: str, fs_meaning: str, fi_fallback: int | None = None) -> int:
    """
    lê um inteiro >= 0 do config.ini, com mensagem que cita seção e opção

    :param fs_meaning: o que o 0 significa, para a mensagem de erro
    :param fi_fallback: valor quando a opção (ou a seção inteira) falta;
                        None = opção obrigatória
    :raises ValueError: não é inteiro, ou é negativo
    """
    # has_option() devolve False também quando a seção não existe
    if fi_fallback is not None and not f_parser.has_option(fs_section, fs_option):
        return fi_fallback

    ls_raw = f_parser.get(fs_section, fs_option)

    try:
        li_value = int(ls_raw)

    except ValueError:
        raise ValueError(f"[{fs_section}] {fs_option} deve ser inteiro: {ls_raw!r}") from None

    if li_value < 0:
        raise ValueError(f"[{fs_section}] {fs_option} deve ser >= 0 ({fs_meaning}): {li_value}")

    return li_value

# -------------------------------------------------------------------------------------
def init(fs_fname: str = CS_CONFIG_FILE) -> SConfig:
    """
    ponto de entrada único para inicialização do pipeline:
    - lê o config.ini (e as sobreposições do ambiente)
    - aplica a trava MET_ENV=test, antes de qualquer escrita
    - configura o logging
    - cria os diretórios necessários
    - retorna o SConfig populado

    uso típico no ponto de entrada da aplicação:
        import work.nwc_config as cf
        cf.G_CONFIG = cf.init()
    """
    l_config = load_config(fs_fname)

    # trava do ambiente de teste: antes de criar qualquer diretório
    check_test_env(l_config)

    # log_dir precisa existir antes do FileHandler do configure_logging
    create_directories(l_config)
    configure_logging(l_config)

    return l_config

# < global >───────────────────────────────────────────────────────────────────────────

# G_CONFIG começa como None e é populado explicitamente via init().  Nenhum I/O
# ocorre no momento do import deste módulo.
G_CONFIG: SConfig | None = None

# < the end >──────────────────────────────────────────────────────────────────────────
