"""Integracao do nucleo: escolher + reservar + liberar sobre o pool real do Davi.

Nao ha navegador nem banco aqui. E o cenario que expoe qualquer regressao na
contabilidade — reserva que zera saldo, estorno que nao volta, free que sobra.
"""

from datetime import datetime, timezone

from flowpool.nucleo.credito import liberar, reservar
from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, Modelo, Tier

AGORA = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def conta(id, tier, saldo):
    return Conta(id=id, email=f"c{id}@x.com", tier=tier, perfil=f"p/{id}", saldo=saldo)


def pool():
    return [
        conta(1, Tier.FREE, 50),
        conta(2, Tier.FREE, 50),
        conta(3, Tier.FREE, 50),
        conta(4, Tier.ULTRA, 9000),
    ]


def test_dez_jobs_fast_drenam_as_free_antes_de_tocar_a_ultra():
    contas = pool()
    ultra = contas[3]
    frees = contas[:3]
    despachos = []

    for _ in range(10):
        escolhida = escolher(contas, Modelo.VEO_FAST, AGORA, duracao="8s")
        assert escolhida is not None, "o pool tem Ultra com 9000; nunca deveria secar"
        # so se reserva na conta que `escolher` devolveu — mesmo objeto, nao uma copia
        assert any(escolhida is c for c in contas)
        custo = reservar(escolhida, Modelo.VEO_FAST, duracao="8s")
        assert custo == custo_de(Modelo.VEO_FAST, escolhida.tier, "8s")
        despachos.append(escolhida.id)

    # Fast de 8s custa 20 na free: 50 -> 30 -> 10. Cada free rende 2 jobs, as tres
    # rendem 6. A duracao vai explicita porque o padrao do dominio e 4s, e a 4s (12
    # creditos) cada free renderia 4 jobs — outro teste, nao este.
    assert despachos[:6].count(1) == 2
    assert despachos[:6].count(2) == 2
    assert despachos[:6].count(3) == 2
    assert ultra.id not in despachos[:6]
    # so depois de nenhuma free caber e que a Ultra entra
    assert despachos[6:] == [ultra.id] * 4

    for f in frees:
        assert f.saldo < custo_de(Modelo.VEO_FAST, Tier.FREE, "8s")

    assert all(c.saldo >= 0 for c in contas)
    assert ultra.saldo == 9000 - 4 * custo_de(Modelo.VEO_FAST, Tier.ULTRA, "8s")


def test_nenhuma_conta_e_reservada_sem_ter_sido_escolhida():
    contas = pool()
    tocadas = set()

    for _ in range(10):
        escolhida = escolher(contas, Modelo.VEO_FAST, AGORA, duracao="8s")
        reservar(escolhida, Modelo.VEO_FAST, duracao="8s")
        tocadas.add(escolhida.id)

    intactas = [c for c in contas if c.id not in tocadas]
    assert all(c.saldo == 50 or c.saldo == 9000 for c in intactas)


def test_falha_de_geracao_devolve_a_reserva_intacta():
    contas = pool()
    escolhida = escolher(contas, Modelo.VEO_FAST, AGORA, duracao="8s")
    antes = escolhida.saldo

    custo = reservar(escolhida, Modelo.VEO_FAST, duracao="8s")
    # a geracao morre aqui: timeout de rede, sessao caida, o que for
    liberar(escolhida, custo)

    assert escolhida.saldo == antes
