"""Custo em creditos por (modelo, tier), e o teto diario da conta gratuita.

Os numeros vem do suporte do Flow e de medicao publica (ver o spec). O custo da
imagem e o mais incerto: ha fonte dizendo que sai de graca. Por isso o valor aqui e
so uma ESTIMATIVA para reservar saldo antes de despachar — quem manda e a leitura
real, reconciliada em `nucleo/credito.py`.
"""

from __future__ import annotations

from flowpool.nucleo.erros import ModeloSemCusto
from flowpool.nucleo.tipos import Modelo, Tier

TETO_DIARIO_FREE = 50

_CUSTO: dict[tuple[Modelo, Tier], int] = {
    # MEDIDO 2026-08-20, conta FREE, 720p / 8s / 9:16 / x1: saldo 50 -> 38.
    # ATENCAO: o custo do video depende da CONFIGURACAO, nao so do modelo — pelo agente
    # conversacional, a mesma familia de pedido anunciou 7 e 15 creditos em ajustes
    # diferentes. Esta tabela e por modelo porque e so uma RESERVA anterior ao
    # despacho; quem manda e a reconciliacao (`nucleo/credito.py`).
    (Modelo.OMNI_FLASH, Tier.FREE): 12,
    (Modelo.OMNI_FLASH, Tier.PRO): 12,
    (Modelo.OMNI_FLASH, Tier.ULTRA): 6,   # EXTRAPOLADO: Ultra paga metade em Lite/Fast
    # ESTIMATIVA — nenhum Veo foi medido ainda.
    (Modelo.VEO_LITE, Tier.FREE): 10,
    (Modelo.VEO_LITE, Tier.PRO): 10,
    (Modelo.VEO_LITE, Tier.ULTRA): 5,
    (Modelo.VEO_FAST, Tier.FREE): 20,
    (Modelo.VEO_FAST, Tier.PRO): 20,
    (Modelo.VEO_FAST, Tier.ULTRA): 10,
    (Modelo.VEO_QUALITY, Tier.FREE): 100,
    (Modelo.VEO_QUALITY, Tier.PRO): 100,
    (Modelo.VEO_QUALITY, Tier.ULTRA): 100,
    # MEDIDO 2026-08-20: imagem custa ZERO. Saldo 50 antes e 50 depois, e o Flow nem
    # pede confirmacao. Desempata a divergencia que o spec carregava (1-2 vs zero).
    (Modelo.NANO_BANANA, Tier.FREE): 0,
    (Modelo.NANO_BANANA, Tier.PRO): 0,
    (Modelo.NANO_BANANA, Tier.ULTRA): 0,
}


def custo_de(modelo: Modelo, tier: Tier) -> int:
    """Custo estimado em creditos de uma geracao."""
    try:
        return _CUSTO[(modelo, tier)]
    except KeyError:
        raise ModeloSemCusto(f"sem custo cadastrado para {modelo.value} em {tier.value}") from None


def cabe_no_free(modelo: Modelo, quantidade: int = 1) -> bool:
    """Se uma conta gratuita consegue, no melhor dia possivel, rodar este pedido.

    A quantidade entra no calculo porque ela multiplica o custo. Quatro imagens ainda
    cabem em 50 creditos, mas isso precisa ser conta feita, nao sorte: um lote que
    estoura o teto diario nunca vai caber em conta gratuita, e descobrir isso so na
    hora de gerar custaria uma geracao recusada.
    """
    return custo_de(modelo, Tier.FREE) * quantidade <= TETO_DIARIO_FREE
