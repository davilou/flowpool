"""O bloco de midia: anexar referencia, limpar rascunho, preencher slots.

Sao 606 linhas, e 15 dos 18 metodos nao tinham teste nenhum. E o miolo da geracao, e as
armadilhas dele nao sao de estado — sao de TEMPO E ORDEM. Cada teste aqui guarda uma
que ja aconteceu de verdade:

  o socorro virava a causa   `_abrir_seletor_midia` apertava Escape ASSUMINDO que o
                             painel nao tinha aberto. Quando ele tinha aberto e so
                             demorado a casar, o Escape fechava o painel recem-aberto e
                             o clique seguinte alternava de novo. Relatado pelo usuario:
                             "as vezes ele fecha ali o mais onde voce coloca as fotos".
  referencia herdada         uma tentativa que morre no meio deixa a ficha na barra, e
                             o Flow SOMA a proxima em vez de trocar (1 -> 2 -> 3).
  o retrato mal tirado       o acervo carrega miniatura preguicosamente; comparar
                             contra um retrato que ainda estava sendo tirado "achou" 6
                             arquivos novos tendo enviado 2.
  a selecao que divergia     "Incluir no comando" age sobre o que esta SELECIONADO, e
                             nao sobre o item que o codigo julga ter escolhido.
  a posicao sem conferencia  os dois slots de Frames sao escolhidos por posicao, e isso
                             so e aceitavel porque cada um e conferido depois.

NAO ESTA COBERTO AQUI, e de proposito: `_anexar`, `_incluir_do_acervo` e
`_enviar_no_seletor_aberto`. Os tres orquestram o ciclo inteiro do painel (abrir,
enviar, esperar upload, marcar, incluir, fechar), e um duplo honesto para eles teria
que modelar a animacao do overlay. Duplo desonesto e pior que nenhum: passaria verde
sem guardar nada.
"""

import pytest

from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import dom
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import Diario
from flowpool.transporte.pw.transporte import TransportePlaywright


def _src(id_midia):
    return f"/fx/api/trpc/media.getMediaUrlRedirect?name={id_midia}"


def _item(nome, selecionado=False, miniaturas=1, id_midia=None):
    return {"texto": f"{nome}\nImagem", "selecionado": selecionado,
            "miniaturas": miniaturas,
            "src": _src(id_midia or f"id-de-{nome}") if miniaturas else ""}


class _Loc:
    def __init__(self, pagina, seletor):
        self.pagina = pagina
        self.seletor = seletor

    @property
    def first(self):
        return self

    def nth(self, i):
        return _Slot(self.pagina, self.seletor, i)

    def locator(self, seletor):
        return _Loc(self.pagina, seletor)

    async def count(self):
        return self.pagina.quantos(self.seletor)

    async def is_visible(self):
        return self.pagina.visivel.get(self.seletor, True)

    async def get_attribute(self, nome):
        return self.pagina.atributos.get((self.seletor, nome))

    async def click(self, timeout=None):
        self.pagina.clicar(self.seletor)


class _Slot(_Loc):
    """Um slot da barra de Frames: pode ou nao ter imagem dentro."""

    def __init__(self, pagina, seletor, i):
        super().__init__(pagina, seletor)
        self.i = i

    def locator(self, seletor):
        if seletor == "img, video":
            return _Contador(1 if self.i in self.pagina.slots_cheios else 0)
        return _Loc(self.pagina, seletor)

    async def click(self, timeout=None):
        self.pagina.clicar(f"{self.seletor}[{self.i}]")
        if self.i in self.pagina.enche_ao_clicar:
            self.pagina.slots_cheios.add(self.i)


class _Contador:
    def __init__(self, n):
        self.n = n

    async def count(self):
        return self.n


class _Teclado:
    def __init__(self, pagina):
        self.pagina = pagina

    async def press(self, tecla):
        self.pagina.escapes.append(tecla)


class _Pagina:
    """A barra do prompt e o seletor de midia, num estado declarado.

    `itens_por_volta` deixa a lista do acervo MUDAR ao longo do tempo, que e a unica
    forma de exercitar o retrato do acervo e a espera da selecao.
    """

    def __init__(self, *, presentes=None, atributos=None, visivel=None,
                 itens=(), itens_por_volta=None, refs=(),
                 slots_cheios=(), enche_ao_clicar=(), some_ao_tirar=True):
        self.presentes = dict(presentes or {})
        self.atributos = dict(atributos or {})
        self.visivel = dict(visivel or {})
        self.itens = list(itens)
        self.itens_por_volta = itens_por_volta
        self.refs = list(refs)
        self.slots_cheios = set(slots_cheios)
        self.enche_ao_clicar = set(enche_ao_clicar)
        self.some_ao_tirar = some_ao_tirar
        self.volta = 0
        self.clicados = []
        self.escapes = []
        self.dormiu_ms = 0

    # --- o que o transporte pergunta
    def quantos(self, seletor):
        if seletor == sel.ITEM_MIDIA:
            return len(self._itens_agora())
        return self.presentes.get(seletor, 0)

    def _itens_agora(self):
        if self.itens_por_volta is None:
            return self.itens
        return self.itens_por_volta[min(self.volta, len(self.itens_por_volta) - 1)]

    def locator(self, seletor):
        return _Loc(self, seletor)

    async def evaluate(self, js, arg=None):
        if js is dom.ITENS_DE_MIDIA:
            return [dict(i) for i in self._itens_agora()]
        if js is dom.SRC_DAS_REFERENCIAS:
            return list(self.refs)
        raise AssertionError("evaluate inesperado")

    async def wait_for_timeout(self, ms):
        self.dormiu_ms += ms
        self.volta += 1

    @property
    def keyboard(self):
        return _Teclado(self)

    # --- efeitos de clique
    def clicar(self, seletor):
        self.clicados.append(seletor)
        if seletor == sel.BOTAO_TIRAR_REFERENCIA and self.some_ao_tirar:
            self.presentes[sel.REFERENCIA_ANEXADA] -= 1
            if self.refs:
                self.refs.pop()


def _aberto(**extra):
    """O seletor de midia montado: `+` declarando aberto, dialogo visivel, abas."""
    base = {sel.BOTAO_ANEXAR: 1, sel.SELETOR_MIDIA: 1, sel.ABAS_SELETOR_MIDIA: 7}
    base.update(extra)
    return base


# ============================================================================
# `_abrir_seletor_midia` — a armadilha em que o socorro virava a causa
# ============================================================================

async def test_seletor_ja_aberto_nao_leva_clique_nem_escape():
    """MEDIDO 2026-09-05: perguntar primeiro poupa ~12 s por geracao. A referencia 1
    levava 13,4 s e a 2 levava 1,6 s — a diferenca era esta pergunta."""
    pagina = _Pagina(presentes=_aberto(),
                     atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO})

    await TransportePlaywright()._abrir_seletor_midia(pagina, Diario(1))

    assert pagina.clicados == []
    assert pagina.escapes == []
    # E O CUSTO QUE PROVA O ATALHO. Sem ele o codigo chega no mesmo lugar, so que
    # girando: eram ~12 s por geracao, e por isso a pergunta vem antes de tudo.
    assert pagina.dormiu_ms == 0


async def test_com_o_mais_declarando_ABERTO_nao_se_clica_nem_se_aperta_escape():
    """A ARMADILHA. Com o `+` em `open` — o que acontece enquanto a animacao de
    fechamento corre — um clique FECHA em vez de abrir, e o Escape fecha o painel que
    acabou de abrir. Nenhum dos dois pode acontecer neste estado."""
    pagina = _Pagina(
        presentes=_aberto(**{sel.SELETOR_MIDIA: 1, sel.ABAS_SELETOR_MIDIA: 0}),
        visivel={sel.SELETOR_MIDIA: False},
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO})

    with pytest.raises(GeracaoFalhou):
        await TransportePlaywright()._abrir_seletor_midia(pagina, Diario(1), tentativas=2)

    assert sel.BOTAO_ANEXAR not in pagina.clicados, "clicar aqui FECHARIA o painel"
    assert pagina.escapes == [], "Escape aqui fecharia o painel recem-aberto"


async def test_com_o_mais_declarando_fechado_clica_e_pode_apertar_escape():
    """O contraponto do teste acima: fechado de verdade, insistir e o certo."""
    pagina = _Pagina(
        presentes={sel.BOTAO_ANEXAR: 1, sel.SELETOR_MIDIA: 0, sel.ABAS_SELETOR_MIDIA: 0},
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_FECHADO})

    with pytest.raises(GeracaoFalhou, match="nao abriu depois de"):
        await TransportePlaywright()._abrir_seletor_midia(pagina, Diario(1), tentativas=2)

    assert pagina.clicados.count(sel.BOTAO_ANEXAR) == 2
    assert pagina.escapes == ["Escape"], "um Escape entre as duas voltas"


# ============================================================================
# `_esperar_anexar_fechado` — a barreira entre uma referencia e a proxima
# ============================================================================

async def test_sem_o_mais_na_tela_nao_ha_interruptor_para_esperar():
    """Em modo FRAMES a barra vira `flow-ingredient-bar` e o `+` some: ler o atributo
    dele ali estouraria."""
    assert await TransportePlaywright()._esperar_anexar_fechado(
        _Pagina(presentes={sel.BOTAO_ANEXAR: 0})) is True


async def test_o_mais_que_nunca_declara_fechado_devolve_falso_no_teto():
    pagina = _Pagina(presentes={sel.BOTAO_ANEXAR: 1},
                     atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO})
    assert await TransportePlaywright()._esperar_anexar_fechado(
        pagina, teto_ms=1_000) is False
    assert pagina.dormiu_ms == 1_000


# ============================================================================
# `_limpar_referencias` — referencia herdada e geracao errada
# ============================================================================

async def test_rascunho_limpo_nao_leva_clique():
    pagina = _Pagina(presentes={sel.REFERENCIA_ANEXADA: 0})
    await TransportePlaywright()._limpar_referencias(pagina)
    assert pagina.clicados == []


async def test_cada_ficha_sai_pelo_cancel_dela():
    """O Flow SOMA a proxima referencia em vez de trocar: sem limpar, a segunda
    tentativa do mesmo job geraria com o dobro das referencias."""
    pagina = _Pagina(presentes={sel.REFERENCIA_ANEXADA: 3, sel.BOTAO_TIRAR_REFERENCIA: 1})
    await TransportePlaywright()._limpar_referencias(pagina)
    assert pagina.clicados == [sel.BOTAO_TIRAR_REFERENCIA] * 3
    assert pagina.quantos(sel.REFERENCIA_ANEXADA) == 0


async def test_o_close_da_barra_nunca_e_usado():
    """O efeito dele nao foi medido: "fechar" tanto pode limpar so as referencias
    quanto apagar o prompt inteiro, e limpar o rascunho errado destroi o comando."""
    pagina = _Pagina(presentes={sel.REFERENCIA_ANEXADA: 2, sel.BOTAO_TIRAR_REFERENCIA: 1})
    await TransportePlaywright()._limpar_referencias(pagina)
    assert all(c == sel.BOTAO_TIRAR_REFERENCIA for c in pagina.clicados)


async def test_ficha_sem_botao_de_tirar_e_erro_nomeando_quantas_sobraram():
    pagina = _Pagina(presentes={sel.REFERENCIA_ANEXADA: 2, sel.BOTAO_TIRAR_REFERENCIA: 0})
    with pytest.raises(GeracaoFalhou, match="nenhum botao de tirar"):
        await TransportePlaywright()._limpar_referencias(pagina)


async def test_ficha_que_nao_sai_vira_erro_em_vez_de_laco_infinito():
    pagina = _Pagina(presentes={sel.REFERENCIA_ANEXADA: 1, sel.BOTAO_TIRAR_REFERENCIA: 1},
                     some_ao_tirar=False)
    with pytest.raises(GeracaoFalhou, match="nao sairam da barra"):
        await TransportePlaywright()._limpar_referencias(pagina)


# ============================================================================
# `_acervo_assentado` — o retrato so vale depois que a lista para de mudar
# ============================================================================

async def test_o_retrato_espera_a_lista_parar_de_mudar():
    crescendo = [[_item("a")], [_item("a"), _item("b")]]
    parado = [_item("a"), _item("b")]
    pagina = _Pagina(itens_por_volta=[*crescendo, parado, parado, parado, parado])

    retrato = await TransportePlaywright()._acervo_assentado(pagina, Diario(1))

    assert [i["texto"] for i in retrato] == ["a\nImagem", "b\nImagem"]


async def test_item_sem_miniatura_impede_o_retrato():
    """MEDIDO na propria sonda: dos 15 itens, so 8 tinham `src` no primeiro instante.
    Item sem id nao entra no retrato — quando a miniatura chega ele PARECE
    recem-enviado, e a sonda "achou" 6 arquivos novos tendo enviado 2."""
    meio_carregado = [_item("a"), _item("b", miniaturas=0)]
    pronto = [_item("a"), _item("b")]
    pagina = _Pagina(itens_por_volta=[meio_carregado] * 6 + [pronto] * 5)

    retrato = await TransportePlaywright()._acervo_assentado(pagina, Diario(1))

    assert all(i["src"] for i in retrato), "nenhum item pode entrar sem src"


# ============================================================================
# `_marcar_item` — "Incluir no comando" age sobre o que esta SELECIONADO
# ============================================================================

async def test_item_ja_selecionado_sozinho_nao_leva_clique():
    """MEDIDO: clicar num item JA selecionado o DESmarca, e sem selecao o botao
    "Incluir no comando" some da tela.

    O comportamento tem DUAS guardas no codigo — a saida exata e o `break` do "marcado
    junto com outro" —, e tirar so uma nao muda o resultado. Este teste guarda o
    comportamento, entao so acusa quando as duas saem.
    """
    pagina = _Pagina(itens=[_item("foto", selecionado=True, id_midia="alvo")])
    await TransportePlaywright()._marcar_item(pagina, "alvo", "foto.png")
    assert pagina.clicados == []


async def test_item_solto_e_clicado_ate_ficar_selecionado():
    solto = [_item("foto", selecionado=False, id_midia="alvo")]
    marcado = [_item("foto", selecionado=True, id_midia="alvo")]
    pagina = _Pagina(itens_por_volta=[solto, marcado, marcado],
                     presentes={sel.ITEM_MIDIA_POR_ID.format(id="alvo"): 1})

    await TransportePlaywright()._marcar_item(pagina, "alvo", "foto.png")

    assert pagina.clicados == [sel.ITEM_MIDIA_POR_ID.format(id="alvo")]


async def test_selecao_que_nao_fica_exclusiva_e_erro():
    """Marcado junto com outro nao se desempata no escuro: a selecao devia ser
    exclusiva, e incluir com dois marcados poe a midia errada no comando."""
    dois = [_item("foto", selecionado=True, id_midia="alvo"),
            _item("outra", selecionado=True, id_midia="intruso")]
    pagina = _Pagina(itens=dois,
                     presentes={sel.ITEM_MIDIA_POR_ID.format(id="alvo"): 1})

    with pytest.raises(GeracaoFalhou, match="selecao errada"):
        await TransportePlaywright()._marcar_item(pagina, "alvo", "foto.png")


# ============================================================================
# `_esperar_ficha_nova` — a barreira antes da proxima referencia
# ============================================================================

async def test_a_ficha_que_chega_atrasada_ainda_conta():
    """A ficha nao aparece no mesmo quadro em que o seletor fecha: conferir na hora
    daria "nao entrou" para uma referencia que entrou 300 ms depois."""
    pagina = _Pagina(refs=[])

    async def chega_depois(ms):
        pagina.dormiu_ms += ms
        pagina.refs.append(_src("nova"))

    pagina.wait_for_timeout = chega_depois
    await TransportePlaywright()._esperar_ficha_nova(pagina, antes=0)
    assert len(pagina.refs) == 1


async def test_ficha_que_nunca_chega_nao_trava_para_sempre():
    pagina = _Pagina(refs=[])
    await TransportePlaywright()._esperar_ficha_nova(pagina, antes=0, teto_ms=1_000)
    assert pagina.dormiu_ms == 1_000


# ============================================================================
# frames: a posicao so vale porque cada slot e CONFERIDO depois
# ============================================================================

async def test_barra_de_frames_sem_dois_slots_e_erro():
    """A escolha por posicao so e aceitavel com a barra tendo exatamente dois."""
    pagina = _Pagina(presentes={sel.SLOT_FRAME: 3})
    with pytest.raises(GeracaoFalhou, match="deveria ter 2 slots"):
        await TransportePlaywright()._preencher_slots(
            pagina, [{"id": "a", "nome": "a.png"}], Diario(1))


async def test_slot_vazio_e_slot_cheio_se_distinguem_pela_imagem_dentro():
    pagina = _Pagina(presentes={sel.SLOT_FRAME: 2}, slots_cheios={0})
    t = TransportePlaywright()
    assert await t._slot_cheio(pagina, 0) is True
    assert await t._slot_cheio(pagina, 1) is False


async def test_slot_que_nao_mostra_a_imagem_derruba_nomeando_o_arquivo():
    """Sem esta conferencia, a escolha por posicao seria a mesma aposta que ja mandou
    video do usuario para a lixeira."""
    pagina = _Pagina(
        presentes={sel.SLOT_FRAME: 2, sel.POPOVER_SLOT: 1,
                   sel.ITEM_MIDIA_POR_ID.format(id="id-a"): 1,
                   sel.BOTAO_INCLUIR_NO_COMANDO: 1, sel.VEU_ABERTO: 0})

    with pytest.raises(GeracaoFalhou,
                       match=r"a-inicial\.png nao entrou no slot inicio"):
        await TransportePlaywright()._preencher_slots(
            pagina, [{"id": "id-a", "nome": "a-inicial.png"}], Diario(1))
