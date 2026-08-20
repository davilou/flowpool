import pytest

from flowpool.nucleo.custos import TETO_DIARIO_FREE, cabe_no_free, custo_de
from flowpool.nucleo.tipos import Modelo, Tier


def test_teto_diario_do_free():
    assert TETO_DIARIO_FREE == 50


@pytest.mark.parametrize(
    "modelo,tier,esperado",
    [
        (Modelo.VEO_LITE, Tier.FREE, 10),
        (Modelo.VEO_LITE, Tier.PRO, 10),
        (Modelo.VEO_LITE, Tier.ULTRA, 5),
        (Modelo.VEO_FAST, Tier.FREE, 20),
        (Modelo.VEO_FAST, Tier.PRO, 20),
        (Modelo.VEO_FAST, Tier.ULTRA, 10),
        (Modelo.VEO_QUALITY, Tier.FREE, 100),
        (Modelo.VEO_QUALITY, Tier.PRO, 100),
        (Modelo.VEO_QUALITY, Tier.ULTRA, 100),
        (Modelo.NANO_BANANA, Tier.FREE, 2),
        (Modelo.NANO_BANANA, Tier.ULTRA, 2),
    ],
)
def test_custo_por_modelo_e_tier(modelo, tier, esperado):
    assert custo_de(modelo, tier) == esperado


def test_tabela_cobre_todos_os_pares():
    # um modelo novo sem custo cadastrado travaria a fila inteira, nao so um job
    for m in Modelo:
        for t in Tier:
            assert isinstance(custo_de(m, t), int)


def test_quality_nao_cabe_em_conta_gratis():
    # 100 creditos contra teto diario de 50 — nunca cabe
    assert cabe_no_free(Modelo.VEO_QUALITY) is False


@pytest.mark.parametrize(
    "modelo", [Modelo.VEO_LITE, Modelo.VEO_FAST, Modelo.NANO_BANANA, Modelo.OMNI_FLASH]
)
def test_o_resto_cabe_em_conta_gratis(modelo):
    assert cabe_no_free(modelo) is True


@pytest.mark.parametrize("tier,esperado", [(Tier.FREE, 20), (Tier.PRO, 20), (Tier.ULTRA, 10)])
def test_omni_flash_tem_custo_nos_tres_tiers(tier, esperado):
    # estimativa na faixa do Veo Fast; o numero real sai da reconciliacao
    assert custo_de(Modelo.OMNI_FLASH, tier) == esperado


def test_quatro_imagens_ainda_cabem_no_teto_do_free():
    # 4 x 2 = 8 contra teto de 50 — cabe, mas o calculo tem que ser explicito
    assert cabe_no_free(Modelo.NANO_BANANA, quantidade=4) is True


def test_lote_que_estoura_o_teto_diario_nao_cabe_no_free():
    # 3 x 20 = 60 contra teto de 50
    assert cabe_no_free(Modelo.VEO_FAST, quantidade=3) is False
