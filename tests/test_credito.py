from datetime import datetime, timezone

import pytest

from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, liberar, reconciliar, reservar
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import Conta, Modelo, Tier


def conta(saldo, tier=Tier.FREE):
    return Conta(id=1, email="a@b.com", tier=tier, perfil="p", saldo=saldo,
                 saldo_confiavel=True)


AGORA = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


# --- reservar ---

def test_reservar_desconta_o_custo_previsto():
    c = conta(50)
    assert reservar(c, Modelo.VEO_FAST) == 20
    assert c.saldo == 30


def test_reservar_recusa_quando_o_custo_excede_o_saldo():
    # zerar o saldo apagaria credito real que ainda daria outra geracao mais barata
    c = conta(5)
    with pytest.raises(SaldoInsuficiente):
        reservar(c, Modelo.VEO_FAST)


def test_reservar_recusado_nao_altera_o_saldo():
    c = conta(5)
    with pytest.raises(SaldoInsuficiente):
        reservar(c, Modelo.VEO_FAST)
    assert c.saldo == 5


def test_reservar_aceita_saldo_exatamente_igual_ao_custo():
    c = conta(20)
    assert reservar(c, Modelo.VEO_FAST) == 20
    assert c.saldo == 0


# --- liberar ---

def test_liberar_devolve_o_credito():
    c = conta(30)
    liberar(c, 20)
    assert c.saldo == 50


def test_reservar_e_liberar_voltam_ao_saldo_original():
    # tres timeouts de rede nao podem sumir com a conta do pool
    c = conta(50)
    custo = reservar(c, Modelo.VEO_FAST)
    liberar(c, custo)
    assert c.saldo == 50


# --- reconciliar ---

def test_reconciliar_com_leitura_igual_mantem_confianca():
    c = conta(30)
    delta = reconciliar(c, lido=30, agora=AGORA)
    assert delta == 0
    assert c.saldo == 30
    assert c.saldo_confiavel is True
    assert c.saldo_lido_em == AGORA


def test_reconciliar_sempre_adota_o_valor_lido():
    # a tela do Flow e a verdade; o previsto e so estimativa
    c = conta(30)
    reconciliar(c, lido=42, agora=AGORA)
    assert c.saldo == 42


def test_delta_positivo_quando_sobrou_credito():
    # o custo real saiu menor que a tabela — e isso que responde se imagem sai de graca
    c = conta(30)
    assert reconciliar(c, lido=35, agora=AGORA) == 5


def test_delta_negativo_quando_faltou_credito():
    # o Google cobrou mais que a tabela
    c = conta(30)
    assert reconciliar(c, lido=28, agora=AGORA) == -2


def test_divergencia_pequena_para_baixo_nao_derruba_confianca():
    c = conta(30)
    delta = reconciliar(c, lido=30 - LIMIAR_DIVERGENCIA, agora=AGORA)
    assert delta == -LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is True


def test_divergencia_grande_para_baixo_derruba_confianca():
    c = conta(100)
    delta = reconciliar(c, lido=100 - LIMIAR_DIVERGENCIA - 1, agora=AGORA)
    assert delta < -LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is False


def test_divergencia_grande_para_cima_em_conta_paga_nao_derruba_confianca():
    # sobrar credito e boa noticia, nao sintoma de contabilidade furada
    c = conta(100, tier=Tier.PRO)
    delta = reconciliar(c, lido=100 + LIMIAR_DIVERGENCIA + 10, agora=AGORA)
    assert delta > LIMIAR_DIVERGENCIA
    assert c.saldo_confiavel is True


def test_reset_da_conta_gratis_nao_e_divergencia():
    # 10 -> 50 e o reset diario, nao contabilidade furada
    c = conta(10, tier=Tier.FREE)
    delta = reconciliar(c, lido=50, agora=AGORA)
    assert delta == 40
    assert c.saldo == 50
    assert c.reset_em == AGORA
    assert c.saldo_confiavel is True


def test_conta_paga_que_sobe_ate_50_nao_e_reset():
    c = conta(10, tier=Tier.PRO)
    reconciliar(c, lido=50, agora=AGORA)
    assert c.reset_em is None
    assert c.saldo_confiavel is True


# --- quantidade ---

def test_reservar_multiplica_pela_quantidade():
    # quatro imagens custam quatro vezes uma; reservar so uma deixaria o Flow cobrar
    # o que o saldo local nao previu
    c = conta(50)
    assert reservar(c, Modelo.NANO_BANANA, quantidade=4) == 8
    assert c.saldo == 42


def test_reservar_recusa_lote_que_o_saldo_nao_paga_inteiro():
    # a conta paga UMA geracao, nao o lote. Reservar so uma faria o app despachar um
    # pedido que o Flow recusa, e a conta seria marcada seca por erro de calculo.
    c = conta(30)
    with pytest.raises(SaldoInsuficiente):
        reservar(c, Modelo.VEO_FAST, quantidade=2)
    assert c.saldo == 30


def test_reservar_sem_quantidade_continua_reservando_uma():
    c = conta(50)
    assert reservar(c, Modelo.VEO_FAST) == 20
