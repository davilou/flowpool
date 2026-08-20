from datetime import datetime, timedelta, timezone

from flowpool.nucleo.escolha import apta, escolher
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# relogio fixo: `escolher` e pura e recebe o agora por parametro
AGORA = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def conta(id, tier, saldo, saude=SaudeSessao.VIVA, ativa=True, seca_ate=None):
    return Conta(id=id, email=f"c{id}@x.com", tier=tier, perfil=f"p/{id}",
                 saldo=saldo, saude=saude, ativa=ativa, seca_ate=seca_ate)


def test_sem_contas_devolve_none():
    assert escolher([], Modelo.VEO_FAST, AGORA) is None


def test_gasta_o_gratis_antes_do_pago():
    # credito de free morre em 24h; o de pago acumula. Aritmetica, nao preferencia.
    free = conta(1, Tier.FREE, 50)
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([pro, free], Modelo.VEO_FAST, AGORA).id == 1


def test_entre_free_pega_a_de_maior_saldo():
    # espalha o uso e evita sobras inuteis de 5 creditos pelo pool
    escolhida = escolher([conta(1, Tier.FREE, 20), conta(2, Tier.FREE, 50)], Modelo.VEO_LITE, AGORA)
    assert escolhida.id == 2


def test_quality_ignora_todas_as_free_e_vai_pra_paga():
    contas = [conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50), conta(3, Tier.ULTRA, 9000)]
    assert escolher(contas, Modelo.VEO_QUALITY, AGORA).id == 3


def test_quality_sem_conta_paga_devolve_none():
    assert escolher([conta(1, Tier.FREE, 50)], Modelo.VEO_QUALITY, AGORA) is None


def test_pula_conta_com_saldo_insuficiente():
    # Fast custa 20 na free
    escolhida = escolher([conta(1, Tier.FREE, 10), conta(2, Tier.FREE, 40)], Modelo.VEO_FAST, AGORA)
    assert escolhida.id == 2


def test_pula_conta_com_sessao_expirada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.EXPIRADA), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_pula_conta_bloqueada():
    contas = [conta(1, Tier.FREE, 50, saude=SaudeSessao.BLOQUEADA), conta(2, Tier.PRO, 900)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_pula_conta_desativada():
    contas = [conta(1, Tier.FREE, 50, ativa=False), conta(2, Tier.FREE, 30)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_pool_todo_seco_devolve_none():
    assert escolher([conta(1, Tier.FREE, 5), conta(2, Tier.FREE, 0)], Modelo.VEO_FAST, AGORA) is None


def test_ultra_paga_menos_e_por_isso_consegue_onde_pro_nao_conseguiria():
    # Fast custa 10 na ultra e 20 na pro. Com saldo 15, so a ultra da conta.
    contas = [conta(1, Tier.PRO, 15), conta(2, Tier.ULTRA, 15)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 2


def test_desempate_entre_pagas_e_por_saldo():
    contas = [conta(1, Tier.PRO, 300), conta(2, Tier.ULTRA, 900)]
    assert escolher(contas, Modelo.VEO_QUALITY, AGORA).id == 2


def test_desempate_nao_depende_da_ordem_da_lista():
    # a lista vem de um SELECT sem ORDER BY garantido; a escolha nao pode variar
    a, b = conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50)
    assert escolher([a, b], Modelo.VEO_FAST, AGORA).id == escolher([b, a], Modelo.VEO_FAST, AGORA).id


def test_saldo_exatamente_igual_ao_custo_serve():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.VEO_FAST, AGORA).id == 1


def test_pula_conta_seca_ate_o_prazo():
    # o Flow mostrou saldo e recusou gerar; insistir antes do prazo e laco quente
    seca = conta(1, Tier.FREE, 50, seca_ate=AGORA + timedelta(hours=1))
    assert escolher([seca, conta(2, Tier.FREE, 30)], Modelo.VEO_FAST, AGORA).id == 2


def test_conta_seca_volta_ao_pool_depois_do_prazo():
    seca = conta(1, Tier.FREE, 50, seca_ate=AGORA - timedelta(minutes=1))
    assert escolher([seca], Modelo.VEO_FAST, AGORA).id == 1


def test_conta_que_paga_uma_geracao_nao_e_escolhida_para_um_lote_de_quatro():
    # Se so a reserva multiplicasse, o app escolheria esta conta, tentaria gerar quatro,
    # o Flow recusaria, o job voltaria pra fila e a conta seria marcada seca — por um
    # problema que era de calculo, nao de credito.
    # O veiculo deixou de ser imagem porque a medicao de 2026-08-20 poe imagem em ZERO:
    # com custo zero nao existe conta que nao comporte o lote. `escolher` nao conhece
    # modo, entao qualquer modelo com custo prende a mesma mecanica.
    magra = conta(1, Tier.FREE, 10)   # paga exatamente uma geracao
    assert escolher([magra], Modelo.VEO_LITE, AGORA) is not None
    assert escolher([magra], Modelo.VEO_LITE, AGORA, quantidade=4) is None


def test_lote_de_imagem_cabe_em_qualquer_conta_viva():
    # a consequencia de dominio da medicao: imagem custa zero, entao a escolha de conta
    # deixa de discriminar por saldo em job de imagem. Vale prender — se um dia a
    # imagem voltar a custar, este teste cai e a conta magra volta a ser recusada.
    magra = conta(1, Tier.FREE, 0)
    assert escolher([magra], Modelo.NANO_BANANA, AGORA, quantidade=4) is not None


def test_lote_grande_pula_a_free_e_cai_na_paga():
    # com quantidade 1 a free vence pela regra do credito que expira antes;
    # com quantidade 4 ela nao comporta o total e sai da escolha
    free = conta(1, Tier.FREE, 30)   # paga tres geracoes, nao quatro
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([free, pro], Modelo.VEO_LITE, AGORA).id == 1
    assert escolher([free, pro], Modelo.VEO_LITE, AGORA, quantidade=4).id == 2


def test_apta_avalia_o_custo_total_do_lote():
    c = conta(1, Tier.FREE, 30)
    assert apta(c, Modelo.VEO_LITE, AGORA, quantidade=3) is True
    assert apta(c, Modelo.VEO_LITE, AGORA, quantidade=4) is False


def test_omni_flash_cabe_em_conta_gratis():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.OMNI_FLASH, AGORA).id == 1
