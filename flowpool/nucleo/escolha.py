"""Escolha de conta. Funcao pura: sem I/O, sem relogio, sem banco."""

from __future__ import annotations

from collections.abc import Sequence

from flowpool.nucleo.custos import cabe_no_free, custo_de
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# Free primeiro: seu credito expira em 24h, o das pagas acumula ate o fim do mes.
_ORDEM_TIER = {Tier.FREE: 1, Tier.PRO: 0, Tier.ULTRA: 0}


def apta(c: Conta, modelo: Modelo) -> bool:
    if not c.ativa or c.saude is not SaudeSessao.VIVA:
        return False
    if c.tier is Tier.FREE and not cabe_no_free(modelo):
        return False
    return custo_de(modelo, c.tier) <= c.saldo


def escolher(contas: Sequence[Conta], modelo: Modelo) -> Conta | None:
    """Melhor conta para rodar `modelo` agora, ou None se nenhuma serve."""
    aptas = [c for c in contas if apta(c, modelo)]
    if not aptas:
        return None
    return max(aptas, key=lambda c: (_ORDEM_TIER[c.tier], c.saldo, -c.id))
