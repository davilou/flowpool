"""Escolha de conta. Funcao pura: sem I/O, sem relogio proprio, sem banco.

O `agora` entra por parametro. Chamar `datetime.now()` aqui dentro tiraria a pureza
da unica peca que precisa ser testavel sem navegador e sem congelar o relogio.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from flowpool.nucleo.custos import cabe_no_free, custo_de
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# Free primeiro: seu credito expira em 24h, o das pagas acumula ate o fim do mes.
_ORDEM_TIER = {Tier.FREE: 1, Tier.PRO: 0, Tier.ULTRA: 0}


def apta(c: Conta, modelo: Modelo, agora: datetime) -> bool:
    # Comparacao por valor, nao por identidade: `Tier` e `SaudeSessao` herdam de
    # `str, Enum`, e um valor cru vindo do SQLite quebraria `is` em silencio.
    if not c.ativa or c.saude != SaudeSessao.VIVA:
        return False
    # Conta seca: o Flow ja recusou gerar apesar do saldo aparente. Insistir antes do
    # prazo devolve o job a fila na hora e o motor vira laco quente.
    if c.seca_ate is not None and c.seca_ate > agora:
        return False
    if c.tier == Tier.FREE and not cabe_no_free(modelo):
        return False
    return custo_de(modelo, c.tier) <= c.saldo


def escolher(contas: Sequence[Conta], modelo: Modelo, agora: datetime) -> Conta | None:
    """Melhor conta para rodar `modelo` em `agora`, ou None se nenhuma serve."""
    aptas = [c for c in contas if apta(c, modelo, agora)]
    if not aptas:
        return None
    return max(aptas, key=lambda c: (_ORDEM_TIER[c.tier], c.saldo, -c.id))
