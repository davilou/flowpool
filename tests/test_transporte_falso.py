import pytest

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import (
    ErroTransporte, Estado, Pedido, ProjetoRemoto, SaldoInsuficiente, SessaoMorta,
)
from flowpool.transporte.falso import TransporteFalso


def conta(saldo=50):
    return Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="p", saldo=saldo)


def pedido():
    return Pedido(modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="uma gata",
                  referencias=[], frame_inicial=None, frame_final=None)


async def test_gerar_devolve_id_remoto_e_fica_pronto():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    assert id_remoto
    assert await t.consultar(s, id_remoto) is Estado.PRONTO


async def test_gerar_debita_o_saldo_simulado():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    await t.gerar(s, pedido())
    assert await t.saldo(s) == 30


async def test_saldo_insuficiente_levanta():
    t = TransporteFalso(saldos={1: 5})
    s = await t.abrir_sessao(conta(saldo=5))
    with pytest.raises(SaldoInsuficiente):
        await t.gerar(s, pedido())


async def test_sessao_morta_levanta_ao_abrir():
    t = TransporteFalso(saldos={1: 50}, mortas={1})
    with pytest.raises(SessaoMorta):
        await t.abrir_sessao(conta())


async def test_baixar_escreve_o_arquivo(tmp_path):
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    destino = tmp_path / "c1.mp4"
    saida = await t.baixar(s, id_remoto, destino)
    assert saida == destino
    assert destino.read_bytes().startswith(b"FALSO")


async def test_falhar_em_conta_especifica():
    t = TransporteFalso(saldos={1: 50}, falha_em={1})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    assert await t.consultar(s, id_remoto) is Estado.FALHOU


async def test_passos_rodando_atrasa_o_pronto():
    # geracao real nao fica pronta na primeira consulta; o motor precisa fazer polling
    t = TransporteFalso(saldos={1: 50}, passos_rodando=2)
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    assert await t.consultar(s, id_remoto) is Estado.RODANDO
    assert await t.consultar(s, id_remoto) is Estado.RODANDO
    assert await t.consultar(s, id_remoto) is Estado.PRONTO


async def test_nunca_termina_fica_rodando_para_sempre():
    t = TransporteFalso(saldos={1: 50}, nunca_termina={1})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.gerar(s, pedido())
    for _ in range(5):
        assert await t.consultar(s, id_remoto) is Estado.RODANDO


async def test_morre_ao_gerar_levanta_sessao_morta_sem_cobrar():
    # a sessao do Google cai no meio de `gerar`, nao so em `abrir_sessao`
    t = TransporteFalso(saldos={1: 50}, morre_ao_gerar={1})
    s = await t.abrir_sessao(conta())
    with pytest.raises(SessaoMorta):
        await t.gerar(s, pedido())
    assert await t.saldo(s) == 50
    assert t.geracoes == {}


async def test_conta_nova_nao_tem_projeto_nenhum():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    assert await t.listar_projetos(s) == []


async def test_criar_projeto_devolve_id_e_conta_as_chamadas():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    assert await t.criar_projeto(s, "Panelas Monaco") == "proj-1"
    assert await t.criar_projeto(s, "Video Roupa") == "proj-2"
    assert t.criacoes_projeto == 2
    assert await t.listar_projetos(s) == [
        ProjetoRemoto(id_remoto="proj-1", nome="Panelas Monaco"),
        ProjetoRemoto(id_remoto="proj-2", nome="Video Roupa"),
    ]


async def test_projeto_de_uma_conta_nao_aparece_na_outra():
    # e a restricao que define o desenho: projeto pertence a UMA conta
    t = TransporteFalso(saldos={1: 50, 2: 50})
    g1 = await t.abrir_sessao(conta())
    g2 = await t.abrir_sessao(Conta(id=2, email="g2@b.com", tier=Tier.FREE, perfil="p"))
    await t.criar_projeto(g1, "Moda")
    assert await t.listar_projetos(g2) == []


async def test_abrir_projeto_registra_o_aberto():
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    id_remoto = await t.criar_projeto(s, "Moda")
    await t.abrir_projeto(s, id_remoto)
    assert t.abertos[1] == id_remoto


async def test_abrir_projeto_de_outra_conta_levanta():
    t = TransporteFalso(saldos={1: 50, 2: 50})
    g1 = await t.abrir_sessao(conta())
    g2 = await t.abrir_sessao(Conta(id=2, email="g2@b.com", tier=Tier.FREE, perfil="p"))
    alheio = await t.criar_projeto(g1, "Moda")
    with pytest.raises(ErroTransporte):
        await t.abrir_projeto(g2, alheio)


async def test_lote_de_quatro_debita_quatro_vezes():
    # o Flow cobra por geracao; o falso precisa cobrar igual, senao o motor passa no
    # teste com uma contabilidade que o real desmente.
    # Era um lote de IMAGEM ate a medicao de 2026-08-20 poe-la em custo ZERO: com zero,
    # quatro geracoes e uma debitam o mesmo e o teste passaria sem mecanica nenhuma.
    t = TransporteFalso(saldos={1: 50})
    s = await t.abrir_sessao(conta())
    await t.gerar(s, Pedido(modo=Modo.T2V, modelo=Modelo.VEO_LITE, prompt="p",
                            quantidade=4))
    assert t.saldos[1] == 10


async def test_lote_que_nao_cabe_no_saldo_e_recusado():
    t = TransporteFalso(saldos={1: 30})
    s = await t.abrir_sessao(conta(30))
    with pytest.raises(SaldoInsuficiente):
        await t.gerar(s, Pedido(modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                                quantidade=2))


async def test_pedido_carrega_proporcao_e_quantidade():
    # sao configuracoes do painel do Flow: sem atravessar a fronteira, o transporte
    # real geraria sempre no default da UI e o pedido do usuario seria ignorado
    p = Pedido(modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p")
    assert p.proporcao == "9:16"
    assert p.quantidade == 1
