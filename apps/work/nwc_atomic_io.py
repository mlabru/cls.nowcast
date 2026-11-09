# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

nwc_atomic_io
escrita atômica em disco: grava num arquivo temporário e só então o renomeia
para o nome final.

por quê: vários programas leem os .npy enquanto outros os escrevem (cron do
21.mvol, conversão, interpolação, sequências).  Um rename dentro do mesmo
sistema de arquivos é atômico: quem lê vê o arquivo antigo inteiro ou o novo
inteiro, nunca um arquivo pela metade.

2026.out  mlabru  refatoração para legibilidade: logger de debug removido;
                  _default_file_mode() explica o umask; save_npy() reúne o
                  save_cappi que estava copiado em nwcConvert e nwcInterpolate
2026.out  mlabru  temporário único (mkstemp no mesmo diretório) e apagado se o
                  writer falhar: com o <nome>.tmp fixo, duas execuções no
                  mesmo destino disputavam o arquivo e o segundo replace()
                  falhava

2026.aug  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import os
import pathlib
import tempfile
import typing

# numPy
import numpy as np

# ─────────────────────────────────────────────────────────────────────────────────────
def atomic_write(fpth_destination: pathlib.Path,
                 f_writer: typing.Callable[[pathlib.Path], None]) -> pathlib.Path:
    """
    escreve fpth_destination de forma atômica.

    passo a passo:
      1. cria um temporário único no mesmo diretório
         (<nome>.<aleatório>.tmp), com as permissões de um arquivo comum;
      2. chama f_writer(temporário), que grava o conteúdo;
      3. renomeia o temporário para fpth_destination (Path.replace, atômico).
    Se o writer (ou o rename) falhar, o temporário é apagado e a exceção
    continua subindo.

    :param fpth_destination: caminho final (o diretório já precisa existir)
    :param f_writer: função que recebe o caminho temporário e grava nele
    :returns: fpth_destination
    """
    # mkstemp garante um nome que nenhum outro processo está usando
    li_temporary_fd, ls_temporary = tempfile.mkstemp(dir=fpth_destination.parent,
                                                     prefix=fpth_destination.name + ".",
                                                     suffix=".tmp")
    os.close(li_temporary_fd)
    lpth_temporary = pathlib.Path(ls_temporary)

    try:
        lpth_temporary.chmod(_default_file_mode())
        f_writer(lpth_temporary)
        lpth_temporary.replace(fpth_destination)

    except BaseException:
        lpth_temporary.unlink(missing_ok=True)
        raise

    return fpth_destination

# ─────────────────────────────────────────────────────────────────────────────────────
def save_npy(fnp_array: np.ndarray, fpth_filename: pathlib.Path) -> None:
    """
    grava um array .npy de forma atômica, criando o diretório se preciso.
    O array é gravado como está (o chamador escolhe o dtype).

    :param fnp_array: dados a gravar
    :param fpth_filename: caminho final do .npy
    """
    fpth_filename.parent.mkdir(parents=True, exist_ok=True)

    def write_array(fpth_temporary: pathlib.Path) -> None:
        with fpth_temporary.open("wb") as l_file:
            np.save(l_file, fnp_array)

    atomic_write(fpth_filename, write_array)

# ─────────────────────────────────────────────────────────────────────────────────────
def _default_file_mode() -> int:
    """
    permissões que um arquivo novo teria se fosse criado com open():
    0o666 menos os bits do umask do processo.  (mkstemp cria com 0o600, só
    o dono lê; os outros programas do cron precisam ler os .npy)

    o umask só pode ser lido trocando-o: os.umask(x) define x e devolve o
    anterior, que é imediatamente restaurado.
    """
    li_current_umask = os.umask(0)
    os.umask(li_current_umask)

    return 0o666 & ~li_current_umask

# < the end >──────────────────────────────────────────────────────────────────────────
