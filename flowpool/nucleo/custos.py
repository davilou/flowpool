"""Custo em creditos por (modelo, tier, duracao), e o teto diario da conta gratuita.

Os numeros vem do suporte do Flow, de medicao publica e de duas medicoes proprias
(ver o spec). Nada aqui e preco final: e uma ESTIMATIVA para reservar saldo antes de
despachar — quem manda e a leitura real, reconciliada em `nucleo/credito.py`.

Video tem tres dimensoes (modelo, tier, duracao); imagem tem duas. Duracao nao entra
em imagem porque nao existe grupo de duracao no painel do Flow em modo imagem.
"""

from __future__ import annotations

from flowpool.nucleo.erros import DuracaoSemCusto, ModeloSemCusto
from flowpool.nucleo.tipos import DURACAO_PADRAO, Modelo, Tier

TETO_DIARIO_FREE = 50

# Duracao de referencia da tabela de video: e nela que esta o unico numero MEDIDO por
# saldo. Todas as outras saem daqui reescaladas por `_ESCALA_DURACAO`.
_DURACAO_BASE = "8s"

# Custo de UM clipe de 8s, por (modelo, tier).
_CUSTO_VIDEO: dict[tuple[Modelo, Tier], int] = {
    # MEDIDO 2026-08-20, conta FREE, 720p / 8s / 9:16 / x1: saldo 50 -> 38.
    (Modelo.OMNI_FLASH, Tier.FREE): 12,
    (Modelo.OMNI_FLASH, Tier.PRO): 12,
    (Modelo.OMNI_FLASH, Tier.ULTRA): 6,   # EXTRAPOLADO: Ultra paga metade em Lite/Fast
    # ESTIMATIVA — nenhum Veo foi medido ainda, em duracao nenhuma.
    (Modelo.VEO_LITE, Tier.FREE): 10,
    (Modelo.VEO_LITE, Tier.PRO): 10,
    (Modelo.VEO_LITE, Tier.ULTRA): 5,
    (Modelo.VEO_FAST, Tier.FREE): 20,
    (Modelo.VEO_FAST, Tier.PRO): 20,
    (Modelo.VEO_FAST, Tier.ULTRA): 10,
    (Modelo.VEO_QUALITY, Tier.FREE): 100,
    (Modelo.VEO_QUALITY, Tier.PRO): 100,
    (Modelo.VEO_QUALITY, Tier.ULTRA): 100,
}

# Imagem. Duracao nao entra: o painel nao tem o grupo, e o transporte nao o toca.
# MEDIDO 2026-08-20: imagem custa ZERO. Saldo 50 antes e 50 depois, e o Flow nem pede
# confirmacao. Desempata a divergencia que o spec carregava (1-2 vs zero).
_CUSTO_IMAGEM: dict[tuple[Modelo, Tier], int] = {
    (Modelo.NANO_BANANA, Tier.FREE): 0,
    (Modelo.NANO_BANANA, Tier.PRO): 0,
    (Modelo.NANO_BANANA, Tier.ULTRA): 0,
}

# Escala do custo por duracao, na moeda do Omni Flash free/pro — que e onde estao os
# unicos dois pontos conhecidos:
#
#   8s = 12   MEDIDO 2026-08-20 por saldo (50 -> 38), 720p / 9:16 / x1.
#   4s =  7   ANUNCIADO pelo agente conversacional do Flow na confirmacao do pedido.
#             NAO foi medido por saldo — e um numero que o Flow disse, nao um que o
#             app viu sair da conta.
#
# 6s e 10s sao EXTRAPOLADOS: a reta que passa pelos dois pontos e 2 + 1,25 por segundo,
# o que da 9,5 e 14,5, arredondados para cima em 10 e 15. O 15 tem um apoio a mais —
# e o outro numero que o agente ja anunciou para esta familia de pedido.
#
# Os tres Veo herdam a MESMA forma a partir do seu proprio 8s, e ali a extrapolacao e
# dupla: nenhum Veo foi medido em duracao nenhuma.
#
# Arredondamento para CIMA, sempre. Reserva curta demais aprova uma conta que nao paga
# o job: o Flow recusa, o job volta pra fila e a conta e marcada seca por erro de
# calculo. Reserva folgada so custa um pouco de saldo ate a reconciliacao — que e
# quem, no fim, corrige tudo isto.
_ESCALA_DURACAO = {"4s": 7, "6s": 10, "8s": 12, "10s": 15}


def _escalar(custo_base: int, duracao: str) -> int:
    """Custo de `duracao` a partir do custo de 8s. Arredonda para cima."""
    try:
        fator = _ESCALA_DURACAO[duracao]
    except KeyError:
        raise DuracaoSemCusto(
            f"sem custo cadastrado para duracao {duracao!r}; "
            f"use uma de {list(_ESCALA_DURACAO)}") from None
    base = _ESCALA_DURACAO[_DURACAO_BASE]
    return -(-custo_base * fator // base)   # divisao inteira com teto


def custo_de(modelo: Modelo, tier: Tier, duracao: str | None = None) -> int:
    """Custo estimado em creditos de UMA geracao.

    `duracao` so tem efeito nos modelos de video. `None` vale `DURACAO_PADRAO`, que e
    o mesmo padrao com que todo `Job` nasce — nao o 8s da tabela. Um default divergente
    do padrao do dominio faria a reserva do job comum sair errada em silencio.

    O default existe tambem para as chamadas de IMAGEM, que nao tem duracao nenhuma
    para informar.
    """
    if (modelo, tier) in _CUSTO_IMAGEM:
        return _CUSTO_IMAGEM[(modelo, tier)]
    try:
        base = _CUSTO_VIDEO[(modelo, tier)]
    except KeyError:
        raise ModeloSemCusto(f"sem custo cadastrado para {modelo.value} em {tier.value}") from None
    return _escalar(base, duracao or DURACAO_PADRAO)


def cabe_no_free(modelo: Modelo, quantidade: int = 1,
                 duracao: str | None = None) -> bool:
    """Se uma conta gratuita consegue, no melhor dia possivel, rodar este pedido.

    Quantidade e duracao entram no calculo porque as duas mexem no custo total. Um
    lote que estoura o teto diario nunca vai caber em conta gratuita, e descobrir isso
    so na hora de gerar custaria uma geracao recusada. A duracao entra pelo mesmo
    motivo, na direcao oposta: sem ela, o teto recusaria um lote de clipes curtos que
    o Flow aceitaria de bom grado.
    """
    return custo_de(modelo, Tier.FREE, duracao) * quantidade <= TETO_DIARIO_FREE
