# -*- coding: utf-8 -*-
"""
Centro Logístico de Simulação Meteorológica
Copyright (c) 1995-2026, Instituto de Controle do Espaço Aéreo

tl_consts
valores default dos parâmetros da Timeline.  Em produção quem manda é o
config.ini ([timeline] e [interpolate] max_gap), lido por work/nwc_series.

2026.set  mlabru  tolerância default 60 s -> interval/2 (300 s): com a
                  associação encadeada (ver timeline.py) a tolerância mede o
                  desvio em relação ao volume anterior, não a uma grade fixa
                  em T0, e ±60 s rejeitava cadências reais de 586–622 s
                  (BVA, SRO, STI).

2026.aug  mlabru  initial version (Linux/Python)
"""
# < imports >──────────────────────────────────────────────────────────────────────────

# python library
from __future__ import annotations

import datetime

# < constants >───────────────────────────────────────────────────────────────────────

# intervalo nominal entre volumes de uma série
CI_DEFAULT_INTERVAL = datetime.timedelta(minutes=10)

# desvio máximo aceito entre o Δt real de dois volumes consecutivos e o
# múltiplo de interval mais próximo.  Com interval/2 todo volume encontra um
# slot (o arredondamento nunca deixa resíduo maior que isso); valores
# menores tornam a série mais estrita e marcam como INVALID (quebra) os
# pontos onde a cadência foge do nominal.
CI_DEFAULT_TOLERANCE = CI_DEFAULT_INTERVAL / 2

# número máximo de slots faltantes consecutivos que podem ser interpolados.
# Com vizinhos a 30 min ou mais a interpolação (mesmo com optical flow) perde
# quase toda a vantagem sobre um cross-fade, e o cross-fade produz imagens
# duplas (medição no changelog do nwcInterpolate).
CI_DEFAULT_MAX_INTERPOLATED_GAP = 2

# < the end >──────────────────────────────────────────────────────────────────────────
