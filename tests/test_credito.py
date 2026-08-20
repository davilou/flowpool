from datetime import datetime, timezone

from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, reconciliar, reservar
from flowpool.nucleo.tipos import Conta, Modelo, Tier


def conta(saldo, tier=Tier.FREE):
    return Conta(id=1, email="a@b.com", tier=tier, perfil="p", saldo=saldo,
                 saldo_confiavel=True)


AGORA = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def test_reservar_desconta_o_custo_previsto():
    c = conta(50)
    reservar(c, Modelo.VEO_FAST)
    assert c.saldo == 30


def test_reservar_nunca_deixa_saldo_negativo():
    c = conta(5)
    reservar(c, Modelo.VEO_FAST)
    assert c.saldo == 0


def test_reconciliar_com_leitura_igual_mantem_confianca():
    c = conta(30)
    div = reconciliar(c, lido=30, agora=AGORA)
    assert div == 0
    assert c.saldo == 30
    assert c.saldo_confiavel is True
    assert c.saldo_lido_em == AGORA


def test_reconciliar_sempre_adota_o_valor_lido():
    # a tela do Flow e a verdade; o previsto e so estimativa
    c = conta(30)
    reconciliar(c, lido=42, agora=AGORA)
    assert c.saldo == 42


def test_divergencia_pequena_nao_derruba_confianca():
    c = conta(30)
    div = reconciliar(c, lido=32, agora=AGORA)
    assert div == 2
    assert c.saldo_confiavel is True


def test_divergencia_grande_derruba_confianca():
    c = conta(30)
    div = reconciliar(c, lido=30 + LIMIAR_DIVERGENCIA + 1, agora=AGORA)
    assert div > LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is False


def test_divergencia_e_em_modulo():
    c = conta(100)
    div = reconciliar(c, lido=100 - LIMIAR_DIVERGENCIA - 1, agora=AGORA)
    assert div > LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is False
