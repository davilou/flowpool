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

`_anexar` e `_incluir_do_acervo` ENTRARAM depois, e a chave foi separar o que da para
modelar do que nao da: a mecanica do envio (`_enviar_arquivos`) abre o seletor NATIVO
de arquivo do sistema e entra por duplo, como o `captura` entrou nos testes de
download. O que se exercita e a DECISAO — limpar antes, uma volta por referencia,
conferir QUAIS entraram.

CONTINUA FORA, e de proposito: `_enviar_no_seletor_aberto`. Ele depende de
`expect_file_chooser` e da chegada assincrona das miniaturas no acervo; um duplo
honesto para isso teria que modelar a animacao do overlay, e duplo desonesto e pior que
nenhum — passaria verde sem guardar nada.
"""

import pytest

from flowpool.nucleo.tipos import Modelo, Modo
from flowpool.transporte.base import GeracaoFalhou, Pedido
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
        # CLICAR NO VAZIO ESTOURA, como no Playwright de verdade. Sem isto o duplo
        # aprova codigo que clica em elemento ausente — e foi exatamente o que
        # deixou `test_o_botao_incluir_e_OPCIONAL` passar sem dente.
        if self.pagina.quantos(self.seletor) == 0:
            raise TimeoutError(f"nada casa {self.seletor}")
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
                 slots_cheios=(), enche_ao_clicar=(), some_ao_tirar=True,
                 entra_ao_clicar=(), proximas_fichas=()):
        self.presentes = dict(presentes or {})
        self.atributos = dict(atributos or {})
        self.visivel = dict(visivel or {})
        self.itens = list(itens)
        self.itens_por_volta = itens_por_volta
        self.refs = list(refs)
        self.slots_cheios = set(slots_cheios)
        self.enche_ao_clicar = set(enche_ao_clicar)
        self.some_ao_tirar = some_ao_tirar
        # que seletores, ao serem clicados, poem uma ficha na barra do prompt — e qual
        # ficha. Com a lista vazia, nada entra: e assim que se exercita "a ficha nunca
        # chegou", que e o erro que nomeia o arquivo.
        self.entra_ao_clicar = set(entra_ao_clicar)
        self.proximas_fichas = list(proximas_fichas)
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
        if seletor in self.entra_ao_clicar and self.proximas_fichas:
            self.refs.append(_src(self.proximas_fichas.pop(0)))


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


# ============================================================================
# `_incluir_do_acervo` — uma volta do seletor por referencia
# ============================================================================

def _com_seletor_aberto(**extra):
    """O seletor de midia montado, para `_abrir_seletor_midia` sair pelo atalho."""
    base = _aberto(**extra)
    return base


async def test_incluir_devolve_o_id_que_entrou():
    """Devolve o ID, e nao None, porque quem chama precisa conferir QUAL entrou —
    e a diferenca entre aprovar duas fichas iguais e reprovar."""
    item = {"id": "midia-a", "nome": "a.png"}
    pagina = _Pagina(
        presentes=_com_seletor_aberto(**{sel.ITEM_MIDIA_POR_ID.format(id="midia-a"): 1,
                                         sel.BOTAO_INCLUIR_NO_COMANDO: 1}),
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO},
        entra_ao_clicar={sel.BOTAO_INCLUIR_NO_COMANDO}, proximas_fichas=["midia-a"])

    assert await TransportePlaywright()._incluir_do_acervo(
        pagina, item, Diario(1)) == "midia-a"


async def test_o_botao_incluir_e_OPCIONAL():
    """Uma sonda viu o menu fechar sozinho num caso isolado: se o botao nao esta na
    tela, o clique no item ja resolveu, e insistir seria clicar no vazio."""
    item = {"id": "midia-a", "nome": "a.png"}
    alvo = sel.ITEM_MIDIA_POR_ID.format(id="midia-a")
    pagina = _Pagina(
        presentes=_com_seletor_aberto(**{alvo: 1, sel.BOTAO_INCLUIR_NO_COMANDO: 0}),
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO},
        entra_ao_clicar={alvo}, proximas_fichas=["midia-a"])

    assert await TransportePlaywright()._incluir_do_acervo(
        pagina, item, Diario(1)) == "midia-a"


async def test_item_que_nao_esta_no_acervo_e_erro():
    item = {"id": "sumiu", "nome": "a.png"}
    pagina = _Pagina(
        presentes=_com_seletor_aberto(**{sel.ITEM_MIDIA_POR_ID.format(id="sumiu"): 0}),
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO})

    with pytest.raises(GeracaoFalhou, match="item sumiu"):
        await TransportePlaywright()._incluir_do_acervo(pagina, item, Diario(1))


async def test_item_AMBIGUO_no_acervo_e_erro_e_nao_desempate():
    """Dois candidatos e ambiguidade. Escolher um no escuro poe a midia errada no
    comando — e a geracao sai plausivel, com o arquivo errado."""
    item = {"id": "midia-a", "nome": "a.png"}
    pagina = _Pagina(
        presentes=_com_seletor_aberto(**{sel.ITEM_MIDIA_POR_ID.format(id="midia-a"): 2}),
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO})

    with pytest.raises(GeracaoFalhou, match="achei 2"):
        await TransportePlaywright()._incluir_do_acervo(pagina, item, Diario(1))


async def test_ficha_que_nao_chega_na_barra_nomeia_o_arquivo():
    """Sem o nome, a falha nao diz QUAL das referencias ficou pelo caminho."""
    item = {"id": "midia-a", "nome": "a-roupa.png"}
    pagina = _Pagina(
        presentes=_com_seletor_aberto(**{sel.ITEM_MIDIA_POR_ID.format(id="midia-a"): 1,
                                         sel.BOTAO_INCLUIR_NO_COMANDO: 1}),
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO})
        # `entra_ao_clicar` vazio: o clique acontece e a ficha nunca aparece

    with pytest.raises(GeracaoFalhou, match=r"a-roupa\.png nao entrou na barra"):
        await TransportePlaywright()._incluir_do_acervo(pagina, item, Diario(1))


# ============================================================================
# `_anexar` — a orquestracao: limpar, enviar, incluir uma a uma, CONFERIR quais
# ============================================================================

def _pedido(modo, referencias=(), frame_inicial=None, frame_final=None):
    return Pedido(modo=modo, modelo=Modelo.NANO_BANANA_2, prompt="x",
                  referencias=list(referencias),
                  frame_inicial=frame_inicial, frame_final=frame_final)


def _envio_falso(monkeypatch, novos):
    """`_enviar_arquivos` entra por duplo: a mecanica dele e o seletor NATIVO de
    arquivo do sistema, que nenhuma pagina falsa modela com honestidade. O que se
    exercita aqui e a decisao — limpar antes, uma volta por referencia, conferir
    QUAIS entraram."""
    chamadas = []

    async def falso(self, pagina, arquivos, d):
        chamadas.append(list(arquivos))
        return list(novos)

    monkeypatch.setattr(TransportePlaywright, "_enviar_arquivos", falso)
    return chamadas


def _pagina_para_anexar(ids, fichas=None):
    alvos = {sel.ITEM_MIDIA_POR_ID.format(id=i): 1 for i in ids}
    return _Pagina(
        presentes=_aberto(**{**alvos, sel.BOTAO_INCLUIR_NO_COMANDO: 1,
                             sel.REFERENCIA_ANEXADA: 0}),
        atributos={(sel.BOTAO_ANEXAR, sel.ATRIBUTO_ANEXAR): sel.ANEXAR_ABERTO},
        entra_ao_clicar={sel.BOTAO_INCLUIR_NO_COMANDO},
        proximas_fichas=list(ids if fichas is None else fichas))


async def test_modo_que_EXIGE_arquivo_sem_arquivo_e_erro_nomeando_o_modo():
    """`ingredients2v` sem referencia nao tem o que gerar."""
    with pytest.raises(GeracaoFalhou, match="ingredients2v sem arquivo"):
        await TransportePlaywright()._anexar(
            _Pagina(), _pedido(Modo.INGREDIENTS2V), Diario(1))


async def test_imagem_sem_referencia_volta_sem_tocar_em_nada():
    """Imagem sem referencia e o caso COMUM: geracao por texto puro. Confundir com o
    modo que exige transformaria toda geracao de imagem em erro."""
    pagina = _Pagina()
    await TransportePlaywright()._anexar(pagina, _pedido(Modo.IMAGEM), Diario(1))
    assert pagina.clicados == []


async def test_frames2v_nao_passa_por_aqui():
    """Os arquivos do frames sobem ANTES de trocar de modo, e os slots sao preenchidos
    por `_preencher_slots`. Deixar passar aqui geraria envio em duplicidade."""
    with pytest.raises(GeracaoFalhou, match="frames2v nao usa"):
        await TransportePlaywright()._anexar(
            _Pagina(), _pedido(Modo.FRAMES2V, frame_inicial="a.png"), Diario(1))


async def test_limpa_o_rascunho_ANTES_de_anexar(monkeypatch):
    """Referencia herdada de uma tentativa que morreu no meio: o Flow SOMA a proxima
    em vez de trocar, e a segunda tentativa geraria com o dobro das referencias."""
    _envio_falso(monkeypatch, [{"id": "midia-a", "nome": "a.png"}])
    pagina = _pagina_para_anexar(["midia-a"])
    pagina.presentes[sel.REFERENCIA_ANEXADA] = 2
    pagina.presentes[sel.BOTAO_TIRAR_REFERENCIA] = 1
    pagina.refs = [_src("velha-1"), _src("velha-2")]

    await TransportePlaywright()._anexar(
        pagina, _pedido(Modo.IMAGEM, ["a.png"]), Diario(1))

    assert pagina.clicados[0] == sel.BOTAO_TIRAR_REFERENCIA, "limpar vem primeiro"
    assert pagina.clicados.count(sel.BOTAO_TIRAR_REFERENCIA) == 2


async def test_uma_volta_do_seletor_por_referencia(monkeypatch):
    """MEDIDO que a selecao do seletor e EXCLUSIVA: nao ha como marcar tres e incluir
    de uma vez."""
    _envio_falso(monkeypatch, [{"id": "midia-a", "nome": "a.png"},
                               {"id": "midia-b", "nome": "b.png"}])
    pagina = _pagina_para_anexar(["midia-a", "midia-b"])

    await TransportePlaywright()._anexar(
        pagina, _pedido(Modo.IMAGEM, ["a.png", "b.png"]), Diario(1))

    assert pagina.clicados.count(sel.BOTAO_INCLUIR_NO_COMANDO) == 2


async def test_o_envio_vira_UMA_tacada_so(monkeypatch):
    """MEDIDO 2026-09-05: o input criado pelo "enviar midia" tem `multiple`, e dois
    arquivos subiram juntos em 11,2 s. Uma volta por arquivo era da aplicacao antiga."""
    chamadas = _envio_falso(monkeypatch, [{"id": "midia-a", "nome": "a.png"},
                                          {"id": "midia-b", "nome": "b.png"}])
    pagina = _pagina_para_anexar(["midia-a", "midia-b"])

    await TransportePlaywright()._anexar(
        pagina, _pedido(Modo.IMAGEM, ["a.png", "b.png"]), Diario(1))

    assert chamadas == [["a.png", "b.png"]], "os dois num envio so"


async def test_DUAS_FICHAS_IGUAIS_reprovam(monkeypatch):
    """O defeito de 21/08: a versao por CONTAGEM aprovava duas fichas iguais, e um job
    real saiu com a mesma foto duas vezes — sem erro, credito gasto, resultado
    plausivel. O usuario so descobriu conferindo a conta."""
    _envio_falso(monkeypatch, [{"id": "midia-a", "nome": "a.png"},
                               {"id": "midia-b", "nome": "b.png"}])
    # o acervo entrega a MESMA ficha nas duas voltas
    pagina = _pagina_para_anexar(["midia-a", "midia-b"], fichas=["midia-a", "midia-a"])

    with pytest.raises(GeracaoFalhou, match="anexo REPETIDO"):
        await TransportePlaywright()._anexar(
            pagina, _pedido(Modo.IMAGEM, ["a.png", "b.png"]), Diario(1))


async def test_ficha_TROCADA_reprova(monkeypatch):
    """Entrou midia que nao e de nenhum dos arquivos enviados: a geracao sairia com a
    foto errada, e sem sintoma nenhum."""
    _envio_falso(monkeypatch, [{"id": "midia-a", "nome": "a.png"}])
    pagina = _pagina_para_anexar(["midia-a"], fichas=["intrusa"])

    with pytest.raises(GeracaoFalhou, match="anexo TROCADO"):
        await TransportePlaywright()._anexar(
            pagina, _pedido(Modo.IMAGEM, ["a.png"]), Diario(1))
