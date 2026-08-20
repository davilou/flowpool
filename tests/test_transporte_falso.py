import pytest

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import Pedido, SaldoInsuficiente, SessaoMorta, Estado
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
