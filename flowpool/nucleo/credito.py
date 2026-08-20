"""Contabilidade de credito: reservar o previsto, reconciliar com o lido.

Descontar so localmente acumula erro (geracao que falha depois de debitar, custo que
o Google muda, imagem que as vezes sai a zero). Em uma semana o app manda job pra
conta seca e a fila trava sem explicacao. Por isso a leitura real sempre vence.
"""

from __future__ import annotations

from datetime import datetime

from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.tipos import Conta, Modelo

# Acima disso, o saldo deixa de ser confiavel ate a proxima leitura.
LIMIAR_DIVERGENCIA = 5


def reservar(conta: Conta, modelo: Modelo) -> int:
    """Desconta o custo previsto antes de despachar. Devolve o custo reservado."""
    custo = custo_de(modelo, conta.tier)
    conta.saldo = max(0, conta.saldo - custo)
    return custo


def reconciliar(conta: Conta, lido: int, agora: datetime) -> int:
    """Adota o saldo lido do Flow. Devolve a divergencia absoluta."""
    divergencia = abs(lido - conta.saldo)
    conta.saldo = lido
    conta.saldo_lido_em = agora
    conta.saldo_confiavel = divergencia <= LIMIAR_DIVERGENCIA
    return divergencia
