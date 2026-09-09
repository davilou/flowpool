"""As acoes basicas sobre a pagina, testadas sem transporte nenhum.

Antes de `acoes.py` existir, `esperar`, `tem` e `sem_veu` eram metodos estaticos de
`TransportePlaywright` e nao tinham teste: para exercitar uma espera era preciso montar
um transporte inteiro. Sao as tres primeiras a ganhar rede depois da mudanca de lugar —
e sao elas que giram em TODA geracao.
"""

import pytest

from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import acoes
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import Diario


class _Loc:
    def __init__(self, quantos):
        self.quantos = quantos
        self.clicado = False
        self.esperou = False

    @property
    def first(self):
        return self

    async def wait_for(self, timeout=None):
        self.esperou = True

    async def count(self):
        return self.quantos() if callable(self.quantos) else self.quantos

    async def click(self, timeout=None):
        self.clicado = True


class _Pagina:
    """Pagina falsa: um localizador por seletor, e um relogio que so conta."""

    def __init__(self, **por_seletor):
        self.locs = {s: _Loc(q) for s, q in por_seletor.items()}
        self.dormiu_ms = 0
        self.escapes = 0

    def locator(self, seletor):
        return self.locs.setdefault(seletor, _Loc(0))

    async def wait_for_timeout(self, ms):
        self.dormiu_ms += ms

    @property
    def keyboard(self):
        return self

    async def press(self, tecla):
        assert tecla == "Escape"
        self.escapes += 1


def _diario():
    return Diario(conta_id=1)


# ---------------------------------------------------------------- pagina_de

def test_pagina_de_devolve_a_pagina_por_tras_do_localizador():
    class _ComPage:
        page = "a pagina"
    assert acoes.pagina_de(_ComPage()) == "a pagina"


def test_pagina_de_devolve_a_propria_pagina_quando_ja_e_uma():
    pagina = _Pagina()
    assert acoes.pagina_de(pagina) is pagina


# ---------------------------------------------------------------------- tem

async def test_tem_e_falso_sem_candidato():
    assert await acoes.tem(_Pagina(**{"#x": 0}), "#x") is False


async def test_tem_e_verdadeiro_com_qualquer_candidato():
    assert await acoes.tem(_Pagina(**{"#x": 3}), "#x") is True


# ------------------------------------------------------------------ esperar

async def test_esperar_devolve_verdadeiro_sem_dormir_quando_ja_vale():
    pagina = _Pagina()

    async def ja():
        return True

    assert await acoes.esperar(pagina, ja, _diario(), "o que for") is True
    assert pagina.dormiu_ms == 0


async def test_esperar_devolve_falso_no_teto_em_vez_de_levantar():
    """A ausencia significa coisa diferente em cada ponto: quem chama da a mensagem."""
    pagina = _Pagina()

    async def nunca():
        return False

    assert await acoes.esperar(pagina, nunca, _diario(), "o que for",
                               teto_ms=2_000, cada=500) is False
    assert pagina.dormiu_ms == 2_000


async def test_esperar_para_assim_que_a_condicao_vale():
    pagina = _Pagina()
    voltas = 0

    async def na_terceira():
        nonlocal voltas
        voltas += 1
        return voltas >= 3

    assert await acoes.esperar(pagina, na_terceira, _diario(), "x",
                               teto_ms=10_000, cada=500) is True
    assert pagina.dormiu_ms == 1_000    # dormiu duas vezes, nao ate o teto


# ------------------------------------------------------------------ sem_veu

async def test_sem_veu_nao_aperta_nada_quando_nao_ha_veu():
    pagina = _Pagina(**{sel.VEU_ABERTO: 0})
    assert await acoes.sem_veu(pagina) is True
    assert pagina.escapes == 0


async def test_sem_veu_aperta_escape_ate_o_veu_sair():
    restantes = [1, 1, 0]
    pagina = _Pagina(**{sel.VEU_ABERTO: lambda: restantes.pop(0) if restantes else 0})
    assert await acoes.sem_veu(pagina) is True
    assert pagina.escapes == 2


async def test_sem_veu_desiste_e_avisa_em_vez_de_girar_para_sempre():
    """O veu que nao sai e problema de verdade — girar sem fim so esconderia."""
    pagina = _Pagina(**{sel.VEU_ABERTO: 1})
    assert await acoes.sem_veu(pagina, tentativas=3) is False
    assert pagina.escapes == 3


# ------------------------------------------------------------- clicar_unico

class _CuraQueNaoCura:
    async def curar(self, pagina, escopo, seletor, o_que, print_da_tela=None):
        return None


async def test_clicar_unico_clica_com_um_candidato():
    pagina = _Pagina(**{"#b": 1})
    await acoes.clicar_unico(pagina, "#b", "o botao", _CuraQueNaoCura())
    assert pagina.locs["#b"].clicado is True


async def test_clicar_unico_recusa_ambiguidade_sem_pedir_cura():
    """Dois candidatos e ambiguidade, e ambiguidade nao se cura: foi um desempate
    automatico que mandou um video do usuario para a lixeira."""
    class _CuraQueAcusa(_CuraQueNaoCura):
        chamada = False

        async def curar(self, *a, **k):
            _CuraQueAcusa.chamada = True
            return None

    pagina = _Pagina(**{"#b": 2})
    with pytest.raises(GeracaoFalhou):
        await acoes.clicar_unico(pagina, "#b", "o botao", _CuraQueAcusa())
    assert pagina.locs["#b"].clicado is False
    assert _CuraQueAcusa.chamada is False


async def test_clicar_unico_clica_no_seletor_que_a_cura_devolveu():
    class _CuraQueAcha(_CuraQueNaoCura):
        async def curar(self, pagina, escopo, seletor, o_que, print_da_tela=None):
            return "#curado"

    pagina = _Pagina(**{"#b": 0, "#curado": 1})
    await acoes.clicar_unico(pagina, "#b", "o botao", _CuraQueAcha())
    assert pagina.locs["#curado"].clicado is True
    assert pagina.locs["#b"].clicado is False


# ------------------------------------------------- banner de cookies do labs.google


class _Banner:
    """A barra de cookies, que existe no DOM esteja ela na tela ou nao.

    MEDIDO 2026-09-09 por sonda: na conta 2 ela vinha com `inert=true`,
    `aria-hidden=true` e ALTURA ZERO — presente e inerte. Na conta 6, visivel, cobrindo
    o rodape bem em cima da barra do prompt.
    """

    def __init__(self, existe=True, visivel=True):
        self.existe = existe
        self.visivel = visivel
        self.clicado = False

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self.existe else 0

    async def is_visible(self):
        return self.visivel

    async def click(self, timeout=None):
        self.clicado = True


class _PaginaComBanner:
    def __init__(self, barra, recusar):
        self.barra, self.recusar = barra, recusar
        self.pedidos = []

    def locator(self, seletor):
        self.pedidos.append(seletor)
        if seletor == sel.BANNER_COOKIES:
            return self.barra
        if seletor == sel.BANNER_COOKIES_RECUSAR:
            return self.recusar
        raise AssertionError(f"seletor inesperado: {seletor!r}")


async def test_banner_visivel_e_recusado_com_agora_nao():
    """⚠️ "Agora nao", NUNCA "Concordo". A escolha e do Davi (2026-09-09) e e a que
    preserva privacidade — recusa os cookies nao essenciais.

    O banner cobre o rodape, que e onde vivem o chip e o botao de enviar. E o suspeito
    de "o painel de configuracao nao abriu" na conta 6.
    """
    pagina = _PaginaComBanner(_Banner(), _Banner())

    assert await acoes.dispensar_banner_cookies(pagina, _diario()) is True
    assert pagina.recusar.clicado is True
    # o seletor de recusa e o unico alvo: "Concordo" e vizinho na mesma barra
    assert sel.BANNER_COOKIES_RECUSAR in pagina.pedidos


async def test_banner_inerte_nao_se_clica():
    """PRESENTE NAO E VISIVEL. Clicar num botao `inert` de altura zero falha — e falha
    por um motivo que nao e o de verdade, mandando quem le procurar o erro errado."""
    pagina = _PaginaComBanner(_Banner(visivel=False), _Banner(visivel=False))

    assert await acoes.dispensar_banner_cookies(pagina, _diario()) is False
    assert pagina.recusar.clicado is False


async def test_sem_banner_nao_faz_nada():
    pagina = _PaginaComBanner(_Banner(existe=False), _Banner(existe=False))

    assert await acoes.dispensar_banner_cookies(pagina, _diario()) is False
    assert pagina.recusar.clicado is False


async def test_banner_que_nao_some_nao_derruba_a_geracao():
    """O banner e um estorvo, nao um requisito: se o clique nao o dispensar, a geracao
    segue e tenta assim mesmo. Levantar aqui trocaria um problema possivel por uma
    falha certa."""
    barra = _Banner()

    class _RecusaQueFalha(_Banner):
        async def click(self, timeout=None):
            raise RuntimeError("o botao sumiu no meio do clique")

    pagina = _PaginaComBanner(barra, _RecusaQueFalha())

    assert await acoes.dispensar_banner_cookies(pagina, _diario()) is False
