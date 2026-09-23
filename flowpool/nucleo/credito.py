"""Contabilidade de credito: reservar o previsto, estornar o que nao foi usado,
reconciliar com o lido.

Descontar so localmente acumula erro (geracao que falha depois de debitar, custo que
o Google muda, imagem que as vezes sai a zero). Em uma semana o app manda job pra
conta seca e a fila trava sem explicacao. Por isso a leitura real sempre vence.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from flowpool.nucleo.custos import TETO_DIARIO_FREE, custo_de
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import Conta, Modelo, Tier

# Gastar mais do que o previsto acima disso significa que o modelo de custo esta
# errado — o numero lido continua valido, mas projetar em cima dele nao.
LIMIAR_DIVERGENCIA = 5

# Idade a partir da qual o saldo baixo de uma conta free deixa de merecer confianca.
# RELATADO 2026-09-23: o horario do reset diario do Flow nao foi medido — por isso a
# regra e "leitura velha", e nao "passou da meia-noite de algum fuso". 3 h e barato:
# reler e abrir o menu do avatar, sem gastar credito, e so acontece quando um job
# nao acha conta.
RELER_FREE_APOS = timedelta(hours=3)


def reservar(conta: Conta, modelo: Modelo, quantidade: int = 1,
             duracao: str | None = None) -> int:
    """Desconta o custo previsto antes de despachar. Devolve o custo reservado.

    Recusa em vez de zerar: chegar aqui sem saldo significa que quem chamou ignorou
    `escolher`, e silenciar isso apagaria credito real que ainda daria outra geracao.

    O custo e o de UMA geracao NAQUELA DURACAO vezes a quantidade pedida. Reservar so
    uma quando o pedido tem quatro deixaria o saldo local acreditando em credito que o
    Flow ja cobrou — e a escolha seguinte mandaria job para uma conta mais magra do que
    o app pensa. Reservar sempre o clipe de 8s faz o contrario: tira do saldo local
    credito que o Flow nao vai cobrar, e a conta sai do pool antes da hora.
    """
    custo = custo_de(modelo, conta.tier, duracao) * quantidade
    if custo > conta.saldo:
        raise SaldoInsuficiente(
            f"conta {conta.email}: saldo {conta.saldo} < custo {custo}"
        )
    conta.saldo -= custo
    return custo


def liberar(conta: Conta, custo: int) -> None:
    """Devolve uma reserva que nao virou geracao.

    Sem isso, tres timeouts de rede numa conta de 50 creditos zeram o saldo local com
    os 50 creditos intactos no Flow — e a conta some do pool para sempre, porque
    ninguem despacha job para conta que parece seca.
    """
    conta.saldo += custo


def reconciliar(conta: Conta, lido: int, agora: datetime) -> int:
    """Adota o saldo lido do Flow. Devolve a diferenca COM SINAL.

    Positivo = sobrou credito (o custo real e menor que a tabela).
    Negativo = faltou credito (o Google cobrou mais que a tabela).
    A distincao importa: e ela que responde se a imagem custa creditos ou sai de graca.
    """
    delta = lido - conta.saldo
    conta.saldo = lido
    conta.saldo_lido_em = agora

    if _parece_reset(conta, lido, delta):
        conta.reset_em = agora
        conta.saldo_confiavel = True
    else:
        # So a direcao pessimista derruba a confianca. Sobrar credito e boa noticia,
        # nao sintoma de contabilidade furada.
        conta.saldo_confiavel = delta >= -LIMIAR_DIVERGENCIA

    return delta


def _parece_reset(conta: Conta, lido: int, delta: int) -> bool:
    """Conta gratuita que volta ao teto diario nao divergiu: ela resetou."""
    return conta.tier == Tier.FREE and delta > 0 and lido >= TETO_DIARIO_FREE


def saldo_pode_ter_resetado(conta: Conta, agora: datetime) -> bool:
    """Conta free abaixo do teto com leitura velha: o Flow pode ja ter devolvido o teto.

    `saldo_confiavel` diz que o numero foi LIDO, nao que ainda vale. Numa free ele
    vence com o reset diario, e sem esta pergunta ninguem o relia: a semeadura pula
    conta confiavel, e job nenhum roda numa conta que parece sem saldo.
    """
    if conta.tier != Tier.FREE or conta.saldo >= TETO_DIARIO_FREE:
        return False
    return conta.saldo_lido_em is None or agora - conta.saldo_lido_em >= RELER_FREE_APOS
