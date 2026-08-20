import pytest

from flowpool.nucleo.custos import TETO_DIARIO_FREE, cabe_no_free, custo_de
from flowpool.nucleo.erros import DuracaoSemCusto
from flowpool.nucleo.tipos import DURACAO_PADRAO, DURACOES, Modelo, Tier

# Os quatro modelos que tem duracao. Imagem nao entra: nao ha grupo de duracao no
# painel do Flow em modo imagem.
VIDEO = [Modelo.OMNI_FLASH, Modelo.VEO_LITE, Modelo.VEO_FAST, Modelo.VEO_QUALITY]


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
    ],
)
def test_custo_de_video_por_modelo_e_tier_em_oito_segundos(modelo, tier, esperado):
    # 8s e a duracao de referencia da tabela: e nela que esta o unico ponto MEDIDO
    # (Omni Flash, 12). Estes tres Veo continuam sendo ESTIMATIVA.
    assert custo_de(modelo, tier, "8s") == esperado


@pytest.mark.parametrize("tier", [Tier.FREE, Tier.PRO, Tier.ULTRA])
def test_imagem_custa_zero_em_qualquer_tier(tier):
    assert custo_de(Modelo.NANO_BANANA, tier) == 0


def test_tabela_cobre_todos_os_pares():
    # um modelo novo sem custo cadastrado travaria a fila inteira, nao so um job
    for m in Modelo:
        for t in Tier:
            assert isinstance(custo_de(m, t), int)


def test_tabela_cobre_todas_as_duracoes_dos_modelos_de_video():
    # duracao sem custo cadastrado tem o mesmo efeito de modelo sem custo: o job morre
    # na escolha de conta, nao na geracao
    for m in VIDEO:
        for t in Tier:
            for d in DURACOES:
                assert isinstance(custo_de(m, t, d), int)


def test_quality_nao_cabe_em_conta_gratis():
    # 59 creditos ja no clipe mais curto, contra teto diario de 50 — nunca cabe
    assert cabe_no_free(Modelo.VEO_QUALITY) is False


@pytest.mark.parametrize(
    "modelo", [Modelo.VEO_LITE, Modelo.VEO_FAST, Modelo.NANO_BANANA, Modelo.OMNI_FLASH]
)
def test_o_resto_cabe_em_conta_gratis(modelo):
    assert cabe_no_free(modelo) is True


# --- custo por duracao ---

def test_omni_flash_de_oito_segundos_custa_doze():
    # MEDIDO 2026-08-20, conta FREE, 720p / 8s / 9:16 / x1: saldo 50 -> 38
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE, "8s") == 12
    assert custo_de(Modelo.OMNI_FLASH, Tier.PRO, "8s") == 12
    assert custo_de(Modelo.OMNI_FLASH, Tier.ULTRA, "8s") == 6


def test_omni_flash_de_quatro_segundos_custa_sete():
    # ANUNCIADO pelo agente conversacional do Flow na confirmacao do pedido. NAO foi
    # medido por saldo, ao contrario dos 12 do clipe de 8s.
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE, "4s") == 7
    assert custo_de(Modelo.OMNI_FLASH, Tier.PRO, "4s") == 7


def test_quatro_segundos_custa_menos_que_oito_em_todo_modelo_de_video():
    # e o numero que justifica a mudanca inteira: fixar 8s pagava a mais em todo job
    for m in VIDEO:
        for t in Tier:
            assert custo_de(m, t, "4s") < custo_de(m, t, "8s")


def test_custo_nunca_cai_quando_a_duracao_cresce():
    # nao da para exigir crescimento ESTRITO: nos tiers baratos o arredondamento
    # empata dois degraus vizinhos (Veo Lite ultra: 3, 5, 5, 7)
    for m in VIDEO:
        for t in Tier:
            valores = [custo_de(m, t, d) for d in DURACOES]
            assert valores == sorted(valores)


def test_duracao_omitida_vale_o_padrao_do_dominio():
    # o default nao e o 8s da tabela: e o 4s que todo job nasce pedindo. Um default
    # diferente do padrao do dominio faria a reserva de um job comum sair errada.
    assert DURACAO_PADRAO == "4s"
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE) == 7
    assert custo_de(Modelo.OMNI_FLASH, Tier.FREE) == custo_de(
        Modelo.OMNI_FLASH, Tier.FREE, DURACAO_PADRAO)


def test_imagem_ignora_a_duracao():
    # imagem nao tem duracao. O parametro existe para as chamadas de video; passa-lo
    # numa de imagem nao pode mudar preco nenhum, hoje que e zero nem no dia em que
    # deixar de ser.
    for d in DURACOES:
        assert custo_de(Modelo.NANO_BANANA, Tier.FREE, d) == 0


def test_duracao_fora_da_lista_e_erro_de_configuracao():
    # mesma familia de `ModeloSemCusto`: chutar um preco para "5s" mandaria reservar
    # um numero inventado, e o erro so apareceria como conta seca depois
    with pytest.raises(DuracaoSemCusto):
        custo_de(Modelo.OMNI_FLASH, Tier.FREE, "5s")


# --- teto do free ---

def test_quatro_imagens_ainda_cabem_no_teto_do_free():
    # imagem MEDIDA em zero: 4 x 0 = 0 contra teto de 50 — cabe, mas o calculo tem
    # que ser explicito, porque o dia em que a imagem passar a custar nao avisa
    assert cabe_no_free(Modelo.NANO_BANANA, quantidade=4) is True


def test_lote_que_estoura_o_teto_diario_nao_cabe_no_free():
    # 3 x 20 = 60 contra teto de 50, no clipe de 8s
    assert cabe_no_free(Modelo.VEO_FAST, quantidade=3, duracao="8s") is False


def test_o_mesmo_lote_cabe_no_free_quando_o_clipe_e_de_quatro_segundos():
    # 3 x 12 = 36 contra os mesmos 50. A duracao tem que entrar aqui tambem, senao o
    # teto do free recusaria um lote que o Flow aceitaria.
    assert cabe_no_free(Modelo.VEO_FAST, quantidade=3, duracao="4s") is True
