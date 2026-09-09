"""A grade vista de fora do transporte.

`sem_credito`, `identidades_da_grade` e `esperar_sair_da_falha` nao tinham teste
enquanto eram metodos: exercitar qualquer uma exigia um transporte inteiro. Sao tres
peças de DEGRADACAO — o que cada uma responde quando nao sabe —, e e por isso que
merecem rede: um "sim" errado aqui tira conta boa do pool ou gasta repeticao a toa.
"""

import pytest

from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import grade
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import Diario


class _Loc:
    def __init__(self, n=0, filhos=None):
        self.n = n
        self.filhos = filhos or {}

    def locator(self, seletor):
        return _Loc(self.filhos.get(seletor, 0))

    async def count(self):
        return self.n

    def nth(self, i):
        return _Tile(i, self.filhos)


class _Tile:
    def __init__(self, indice, filhos):
        self.indice = indice
        self.filhos = filhos

    def locator(self, seletor):
        marcas = self.filhos.get(self.indice, {})
        return _Loc(marcas.get(seletor, 0))


class _Grade:
    """Uma grade de `n` tiles; `marcas[i]` diz que seletores casam no tile `i`."""

    def __init__(self, n, marcas=None, identidades=None, explode=False):
        self.n = n
        self.marcas = marcas or {}
        self.identidades = identidades
        self.explode = explode
        self.dormiu_ms = 0

    def locator(self, seletor):
        return _Loc(self.n, self.marcas)

    async def evaluate(self, script, arg=None):
        if self.explode:
            raise RuntimeError("a pagina navegou no meio da leitura")
        return self.identidades

    async def wait_for_timeout(self, ms):
        self.dormiu_ms += ms


def _diario():
    return Diario(conta_id=1)


# ------------------------------------------------------------- sem_credito

async def test_sem_credito_responde_nao_sei_como_falso(monkeypatch):
    """Com o seletor PENDENTE a resposta tem que ser False.

    True marcaria a conta como seca e a tiraria do pool por causa de um seletor que
    ninguem mediu — evidencia nenhuma, custo alto.
    """
    monkeypatch.setattr(sel, "AVISO_SEM_CREDITO", sel.PENDENTE)
    assert await grade.sem_credito(_Grade(0)) is False


async def test_sem_credito_le_a_tela_quando_o_seletor_existe(monkeypatch):
    monkeypatch.setattr(sel, "AVISO_SEM_CREDITO", "text=acabaram")
    assert await grade.sem_credito(_Grade(1)) is True


# --------------------------------------------------- identidades_da_grade

async def test_identidades_devolve_vazio_em_vez_de_derrubar_a_geracao():
    """Sem identidade, `esperar_novo` volta a depender da contagem — que ainda serve.
    Levantar aqui mataria uma geracao por causa de uma leitura auxiliar."""
    assert await grade.identidades_da_grade(_Grade(3, explode=True)) == []


async def test_identidades_devolve_o_que_a_pagina_deu():
    assert await grade.identidades_da_grade(
        _Grade(2, identidades=["src-a", "src-b"])) == ["src-a", "src-b"]


# ------------------------------------------------------------------ achar

async def test_achar_usa_a_identidade_guardada_e_ignora_a_posicao():
    pagina = _Grade(3, identidades=["upload-a", "upload-b", "o-video"])
    card = await grade.achar(pagina, "card-0", 7, {(7, "card-0"): "o-video"})
    assert card.indice == 2


async def test_achar_cai_na_posicao_e_esquece_a_identidade_que_sumiu():
    """A identidade some por motivo banal: o card ganhou a miniatura de verdade e o
    valor velho era o placeholder. Derrubar o job aqui mataria geracao pronta."""
    lembradas = {(7, "card-0"): "identidade-velha"}
    pagina = _Grade(3, identidades=["a", "b", "c"])
    card = await grade.achar(pagina, "card-0", 7, lembradas)
    assert card.indice == 0
    assert lembradas == {}, "a identidade velha so produziria este desvio de novo"


async def test_achar_avisa_quando_o_card_sumiu_da_grade():
    with pytest.raises(GeracaoFalhou, match="sumiu da grade"):
        await grade.achar(_Grade(0), "card-2")


# ------------------------------------------------- esperar_sair_da_falha

async def test_sair_da_falha_e_verdadeiro_quando_o_card_deixa_de_ser_falha():
    pagina = _Grade(1, marcas={0: {}})
    assert await grade.esperar_sair_da_falha(pagina, "card-0", _diario()) is True


async def test_card_que_sumiu_da_grade_conta_como_sair_da_falha():
    """Sumir no meio da troca E sair da falha: o Flow trocou o tile pelo novo."""
    assert await grade.esperar_sair_da_falha(_Grade(0), "card-0", _diario()) is True


async def test_sair_da_falha_devolve_falso_no_teto_sem_gastar_repeticao():
    """`False` aqui e "ainda nao", nao erro: quem chama segue o polling. Levantar
    transformaria uma falha em quatro."""
    pagina = _Grade(1, marcas={0: {sel.CARD_FALHA: 1}})
    saiu = await grade.esperar_sair_da_falha(pagina, "card-0", _diario(), teto_ms=2_000)
    assert saiu is False
    assert pagina.dormiu_ms == 2_000
