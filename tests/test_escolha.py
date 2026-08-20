from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier


def conta(id, tier, saldo, saude=SaudeSessao.VIVA, ativa=True):
    return Conta(id=id, email=f"c{id}@x.com", tier=tier, perfil=f"p/{id}",
                 saldo=saldo, saude=saude, ativa=ativa)


def test_sem_contas_devolve_none():
    assert escolher([], Modelo.VEO_FAST) is None


def test_gasta_o_gratis_antes_do_pago():
    # credito de free morre em 24h; o de pago acumula. Aritmetica, nao preferencia.
    free = conta(1, Tier.FREE, 50)
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([pro, free], Modelo.VEO_FAST).id == 1


def test_entre_free_pega_a_de_maior_saldo():
    # espalha o uso e evita sobras inuteis de 5 creditos pelo pool
    escolhida = escolher([conta(1, Tier.FREE, 20), conta(2, Tier.FREE, 50)], Modelo.VEO_LITE)
    assert escolhida.id == 2


def test_quality_ignora_todas_as_free_e_vai_pra_paga():
    contas = [conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50), conta(3, Tier.ULTRA, 9000)]
    assert escolher(contas, Modelo.VEO_QUALITY).id == 3


def test_quality_sem_conta_paga_devolve_none():
    assert escolher([conta(1, Tier.FREE, 50)], Modelo.VEO_QUALITY) is None


def test_pula_conta_com_saldo_insuficiente():
    # Fast custa 20 na free
    escolhida = escolher([conta(1, Tier.FREE, 10), conta(2, Tier.FREE, 40)], Modelo.VEO_FAST)
    assert escolhida.id == 2


def test_pula_conta_com_sessao_expirada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.EXPIRADA), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_pula_conta_bloqueada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.BLOQUEADA), conta(2, Tier.PRO, 900)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_pula_conta_desativada():
    contas = [conta(1, Tier.FREE, 50, ativa=False), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_pool_todo_seco_devolve_none():
    assert escolher([conta(1, Tier.FREE, 5), conta(2, Tier.FREE, 0)], Modelo.VEO_FAST) is None


def test_ultra_paga_menos_e_por_isso_consegue_onde_pro_nao_conseguiria():
    # Fast custa 10 na ultra e 20 na pro. Com saldo 15, so a ultra da conta.
    contas = [conta(1, Tier.PRO, 15), conta(2, Tier.ULTRA, 15)]
    assert escolher(contas, Modelo.VEO_FAST).id == 2


def test_desempate_entre_pagas_e_por_saldo():
    contas = [conta(1, Tier.PRO, 300), conta(2, Tier.ULTRA, 900)]
    assert escolher(contas, Modelo.VEO_QUALITY).id == 2


def test_desempate_nao_depende_da_ordem_da_lista():
    # a lista vem de um SELECT sem ORDER BY garantido; a escolha nao pode variar
    a, b = conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50)
    assert escolher([a, b], Modelo.VEO_FAST).id == escolher([b, a], Modelo.VEO_FAST).id


def test_saldo_exatamente_igual_ao_custo_serve():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.VEO_FAST).id == 1
