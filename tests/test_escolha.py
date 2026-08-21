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
    # Fast de 8s custa 20 na free
    escolhida = escolher([conta(1, Tier.FREE, 10), conta(2, Tier.FREE, 40)],
                         Modelo.VEO_FAST, AGORA, duracao="8s")
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
    # Fast de 8s custa 10 na ultra e 20 na pro. Com saldo 15, so a ultra da conta.
    contas = [conta(1, Tier.PRO, 15), conta(2, Tier.ULTRA, 15)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA, duracao="8s").id == 2


def test_desempate_entre_pagas_e_por_saldo():
    contas = [conta(1, Tier.PRO, 300), conta(2, Tier.ULTRA, 900)]
    assert escolher(contas, Modelo.VEO_QUALITY, AGORA).id == 2


def test_desempate_nao_depende_da_ordem_da_lista():
    # a lista vem de um SELECT sem ORDER BY garantido; a escolha nao pode variar
    a, b = conta(1, Tier.FREE, 50), conta(2, Tier.FREE, 50)
    assert escolher([a, b], Modelo.VEO_FAST, AGORA).id == escolher([b, a], Modelo.VEO_FAST, AGORA).id


def test_saldo_exatamente_igual_ao_custo_serve():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.VEO_FAST, AGORA,
                    duracao="8s").id == 1


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
    free = conta(1, Tier.FREE, 30)   # paga tres geracoes de 8s, nao quatro
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([free, pro], Modelo.VEO_LITE, AGORA, duracao="8s").id == 1
    assert escolher([free, pro], Modelo.VEO_LITE, AGORA, quantidade=4,
                    duracao="8s").id == 2


def test_apta_avalia_o_custo_total_do_lote():
    c = conta(1, Tier.FREE, 30)
    assert apta(c, Modelo.VEO_LITE, AGORA, quantidade=3, duracao="8s") is True
    assert apta(c, Modelo.VEO_LITE, AGORA, quantidade=4, duracao="8s") is False


def test_omni_flash_cabe_em_conta_gratis():
    assert escolher([conta(1, Tier.FREE, 20)], Modelo.OMNI_FLASH, AGORA).id == 1


# --- duracao ---

def test_conta_que_paga_um_clipe_de_4s_nao_e_escolhida_para_o_de_8s():
    # E o teste que prova que a duracao atravessou `escolher`, e nao so `reservar`.
    # Omni Flash na free: 4s custa 7 e 8s custa 12. Com saldo 10 a conta paga o curto
    # e nao paga o longo. Se so a reserva soubesse da duracao, a escolha aprovaria
    # esta conta para o job de 8s, o Flow recusaria, o job voltaria pra fila e a conta
    # seria marcada seca — por um problema que era de calculo.
    magra = conta(1, Tier.FREE, 10)
    assert escolher([magra], Modelo.OMNI_FLASH, AGORA, duracao="4s").id == 1
    assert escolher([magra], Modelo.OMNI_FLASH, AGORA, duracao="8s") is None


def test_apta_olha_a_duracao():
    magra = conta(1, Tier.FREE, 10)
    assert apta(magra, Modelo.OMNI_FLASH, AGORA, duracao="4s") is True
    assert apta(magra, Modelo.OMNI_FLASH, AGORA, duracao="8s") is False


def test_duracao_omitida_na_escolha_vale_o_padrao_do_dominio():
    # nao ha default proprio aqui: o de `custo_de` e o mesmo 4s que todo job nasce
    # pedindo, e um segundo default divergente seria a origem de reserva errada
    magra = conta(1, Tier.FREE, 10)
    assert escolher([magra], Modelo.OMNI_FLASH, AGORA).id == 1


def test_lote_grande_pula_a_free_quando_o_clipe_e_longo_e_cabe_quando_e_curto():
    # a duracao muda a conta escolhida tanto quanto a quantidade, e pelo mesmo motivo:
    # as duas multiplicam o custo total do pedido
    free = conta(1, Tier.FREE, 30)
    pro = conta(2, Tier.PRO, 1000)
    assert escolher([free, pro], Modelo.VEO_LITE, AGORA, quantidade=4,
                    duracao="8s").id == 2
    assert escolher([free, pro], Modelo.VEO_LITE, AGORA, quantidade=4,
                    duracao="4s").id == 1


# ---------------------------------------------------------------- simultaneidade
#
# A invariante do pool com varias janelas: UM job por conta, UM navegador por conta.
# Nao e preferencia — dois Chromes com o mesmo `--user-data-dir` nao coexistem: o
# segundo entrega a URL ao primeiro e morre. A conta que ja esta gerando precisa,
# portanto, sair da escolha enquanto durar a geracao.


def test_conta_ocupada_nao_e_escolhida():
    ocupada = conta(1, Tier.FREE, 50)
    livre = conta(2, Tier.FREE, 30)
    assert escolher([ocupada, livre], Modelo.VEO_FAST, AGORA,
                    ocupadas=frozenset({1})).id == 2


def test_todas_ocupadas_devolve_none():
    # nao e "pool seco": e "pool cheio". Quem chama trata os dois do mesmo jeito —
    # devolve o job a fila — mas o motivo no evento e outro.
    assert escolher([conta(1, Tier.FREE, 50)], Modelo.VEO_FAST, AGORA,
                    ocupadas=frozenset({1})) is None


def test_ocupadas_vazio_e_o_padrao_e_nao_muda_nada():
    contas = [conta(1, Tier.FREE, 50)]
    assert escolher(contas, Modelo.VEO_FAST, AGORA).id == 1
    assert escolher(contas, Modelo.VEO_FAST, AGORA, ocupadas=frozenset()).id == 1


def test_conta_ocupada_tambem_sai_da_escolha_em_imagem():
    # imagem nao olha saldo, mas olha janela: o limite dela e o perfil do Chrome
    ocupada = conta(1, Tier.FREE, 0)
    assert escolher([ocupada], Modelo.NANO_BANANA, AGORA,
                    ocupadas=frozenset({1})) is None


# ------------------------------------------------------- imagem nao olha credito


def test_imagem_escolhe_conta_com_saldo_zero():
    # MEDIDO tres vezes: imagem custa ZERO. Exigir saldo dela e recusar uma geracao
    # que o Flow faria de graca.
    assert escolher([conta(1, Tier.FREE, 0)], Modelo.NANO_BANANA, AGORA).id == 1


def test_imagem_escolhe_conta_seca():
    # "seca" quer dizer "sem credito". Imagem nao precisa de credito, entao o prazo
    # de seca nao diz nada sobre ela.
    seca = conta(1, Tier.FREE, 0, seca_ate=AGORA + timedelta(hours=1))
    assert escolher([seca], Modelo.NANO_BANANA, AGORA).id == 1
    assert apta(seca, Modelo.NANO_BANANA, AGORA) is True


def test_video_continua_recusando_conta_seca_e_conta_sem_saldo():
    # o afrouxamento vale so onde o custo e zero; o corte que importa segue de pe
    seca = conta(1, Tier.FREE, 50, seca_ate=AGORA + timedelta(hours=1))
    pobre = conta(2, Tier.FREE, 3)
    assert escolher([seca, pobre], Modelo.VEO_FAST, AGORA) is None


def test_imagem_nao_escolhe_conta_expirada_nem_desativada():
    # o que imagem dispensa e CREDITO. Sessao caida e conta desligada continuam fora:
    # sem login nao ha o que clicar, de graca ou pago.
    morta = conta(1, Tier.FREE, 0, saude=SaudeSessao.EXPIRADA)
    desligada = conta(2, Tier.FREE, 0, ativa=False)
    bloqueada = conta(3, Tier.FREE, 0, saude=SaudeSessao.BLOQUEADA)
    assert escolher([morta, desligada, bloqueada], Modelo.NANO_BANANA, AGORA) is None
