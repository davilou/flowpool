"""O painel de configuracao — o chip — com pagina falsa.

`_configurar` tem 134 linhas, e nenhuma delas tinha teste. E onde moram as armadilhas
mais caras do transporte, e todas ja aconteceram de verdade:

  * um `except: segue` engoliu um clique de duracao que nao pegou e entregou 8s onde
    foram pedidos 4s — com credito debitado e ninguem percebendo;
  * o grupo de duracao/resolucao NAO EXISTE em alguns modelos, e exigir o botao ali
    fazia a auto-cura "consertar" a ausencia mapeando `RESOLUCAO[720p]` para o botao
    de PROPORCAO;
  * o chip e um INTERRUPTOR: clicar com ele aberto fecha.

`_exigir_agente_desligado` ja tem rede propria em `test_transporte_pw.py`
(`_PaginaComAgente`), e por isso nao se repete aqui.
"""

import pytest

from flowpool.nucleo.tipos import Modelo, Modo
from flowpool.transporte.base import GeracaoFalhou, Pedido
from flowpool.transporte.pw import chip
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import Diario
from flowpool.transporte.pw.transporte import TransportePlaywright


class _Loc:
    def __init__(self, pagina, seletor):
        self.pagina = pagina
        self.seletor = seletor

    # --- o que o transporte pergunta a um localizador
    async def count(self):
        return self.pagina.presentes.get(self.seletor, 0)

    @property
    def first(self):
        return self

    def nth(self, i):
        return _Item(self.pagina, self.seletor, i)

    def locator(self, seletor):
        return _Loc(self.pagina, seletor)

    async def wait_for(self, timeout=None):
        if not self.pagina.presentes.get(self.seletor):
            raise TimeoutError(self.seletor)

    async def click(self, timeout=None):
        self.pagina.clicados.append(self.seletor)
        if self.seletor == sel.CHIP_CONFIG:
            self.pagina.abrir()

    async def is_visible(self):
        return bool(self.pagina.presentes.get(self.seletor))

    async def inner_text(self, timeout=None):
        return self.pagina.textos[self.seletor]

    async def all_inner_texts(self):
        return list(self.pagina.itens_do_menu)

    async def get_attribute(self, nome):
        return self.pagina.atributos.get((self.seletor, nome))


class _Item(_Loc):
    def __init__(self, pagina, seletor, i):
        super().__init__(pagina, seletor)
        self.i = i

    async def click(self, timeout=None):
        self.pagina.clicados.append(f"{self.seletor}[{self.i}]")


class _Teclado:
    def __init__(self, pagina):
        self.pagina = pagina

    async def press(self, tecla):
        assert tecla == "Escape"
        self.pagina.escapes += 1
        if self.pagina.escapes >= self.pagina.escapes_para_fechar:
            self.pagina.fechar()


class _Pagina:
    """O painel do chip: o que esta na tela, o que esta marcado, o que o chip diz."""

    def __init__(self, *, presentes=None, marcados=(), modelo_no_dropdown="Nano Banana 2",
                 chip="imagem crop_9_16 x1", custo="vai usar 0 creditos",
                 escapes_para_fechar=1, itens_do_menu=None):
        # tudo o que o painel oferece; um grupo AUSENTE simplesmente nao entra aqui
        self.presentes = dict(presentes or {})
        self.marcados = list(marcados)
        self.itens_do_menu = list(itens_do_menu or ["Nano Banana 2", "Nano Banana 2 Lite"])
        self.textos = {sel.DROPDOWN_MODELO: modelo_no_dropdown,
                       sel.CHIP_CONFIG: chip}
        if custo is not None:
            self.textos[sel.CUSTO_DA_GERACAO] = custo
        self.atributos = {(sel.BOTAO_AGENTE, sel.ATRIBUTO_AGENTE): sel.AGENTE_DESLIGADO}
        self.presentes.setdefault(sel.BOTAO_AGENTE, 1)
        self.presentes.setdefault(sel.CHIP_CONFIG, 1)
        self.presentes.setdefault(sel.MENU_ITEM, len(self.itens_do_menu))
        self.aberto = True
        self.escapes = 0
        self.escapes_para_fechar = escapes_para_fechar
        self.clicados = []
        self.consultados = []
        self.dormiu_ms = 0
        self.abrir()

    def abrir(self):
        self.aberto = True
        self.presentes[sel.PAINEL_CHIP] = 1

    def fechar(self):
        self.aberto = False
        self.presentes[sel.PAINEL_CHIP] = 0
        self.presentes[sel.VEU_ABERTO] = 0

    def locator(self, seletor):
        self.consultados.append(seletor)
        return _Loc(self, seletor)

    async def evaluate(self, js, arg=None):
        if "length > 3" in js:
            return self.aberto
        if "aria-checked=true" in js:
            return list(self.marcados)
        raise AssertionError(f"evaluate inesperado: {js[:60]}")

    async def wait_for_timeout(self, ms):
        self.dormiu_ms += ms

    @property
    def keyboard(self):
        return _Teclado(self)


def _pedido(**extra):
    base = {"modo": Modo.IMAGEM, "modelo": Modelo.NANO_BANANA_2, "prompt": "x",
            "proporcao": "9:16", "quantidade": 1}
    base.update(extra)
    return Pedido(**base)


def _presentes_de_imagem():
    return {sel.BOTAO_MODO["imagem"]: 1, sel.BOTAO_MODO["t2v"]: 1,
            sel.DROPDOWN_MODELO: 1, sel.PROPORCAO["9:16"]: 1, sel.QUANTIDADE[1]: 1}


def _marcados_de_imagem():
    return ["image imagem", "crop_9_16 9:16", "x1"]


# ------------------------------------------------------- o caminho que da certo

async def test_configuracao_confirmada_devolve_o_texto_do_chip():
    pagina = _Pagina(presentes=_presentes_de_imagem(), marcados=_marcados_de_imagem())
    t = TransportePlaywright()

    chip = await t._configurar(pagina, _pedido(), Diario(1))

    assert chip == "imagem crop_9_16 x1"
    assert t._ultimo_custo == 0


# --------------------------------------------------------- o que tem que recusar

async def test_painel_que_nao_confirma_a_configuracao_e_erro():
    """Um `except: segue` engoliu um clique de duracao que nao pegou e entregou 8s
    onde foram pedidos 4s — credito debitado, arquivo na pasta, ninguem percebendo."""
    pagina = _Pagina(presentes=_presentes_de_imagem(),
                     marcados=["image imagem", "x1"])   # falta a proporcao
    with pytest.raises(GeracaoFalhou, match="nao confirma a configuracao"):
        await TransportePlaywright()._configurar(pagina, _pedido(), Diario(1))


async def test_modelo_que_nao_trocou_e_erro():
    """O modelo NAO aparece no texto do chip: se nao se conferir aqui, com o painel
    aberto, a geracao sai no modelo errado sem nenhum sinal."""
    pagina = _Pagina(presentes=_presentes_de_imagem(), marcados=_marcados_de_imagem(),
                     modelo_no_dropdown="Omni 1.1 Flash")
    with pytest.raises(GeracaoFalhou, match="modelo nao trocou"):
        await TransportePlaywright()._configurar(pagina, _pedido(), Diario(1))


# ------------------------------------------- ausencia legitima nao e defeito

async def test_grupo_que_o_modelo_nao_oferece_nao_vira_exigencia():
    """MEDIDO 2026-09-05: duracao e resolucao pertencem ao MODELO. Em Frames com Veo
    3.1 Lite os dois grupos SOMEM. Cobrar o botao ali reprovaria uma configuracao
    correta — e a auto-cura tentava consertar a ausencia."""
    presentes = {sel.BOTAO_MODO["t2v"]: 1, sel.BOTAO_MODO["ingredients2v"]: 1,
                 sel.DROPDOWN_MODELO: 1, sel.PROPORCAO["9:16"]: 1, sel.QUANTIDADE[1]: 1}
    pagina = _Pagina(presentes=presentes,
                     marcados=["chrome_extension elementos", "crop_9_16 9:16", "x1"],
                     modelo_no_dropdown="Omni 1.1 Flash",
                     itens_do_menu=["Omni 1.1 Flash"], chip="video 9:16 x1")

    chip = await TransportePlaywright()._configurar(
        pagina, _pedido(modo=Modo.INGREDIENTS2V, modelo=Modelo.OMNI_FLASH,
                        duracao="4s"), Diario(1))

    assert chip == "video 9:16 x1"


async def test_grupo_ausente_nao_chega_a_pedir_clique():
    """A auto-cura so entra por `clicar_unico`. Se o grupo ausente chegasse la, ela
    tentaria "consertar" — foi assim que `RESOLUCAO[720p]` virou o botao de PROPORCAO."""
    presentes = {sel.BOTAO_MODO["t2v"]: 1, sel.BOTAO_MODO["ingredients2v"]: 1,
                 sel.DROPDOWN_MODELO: 1, sel.PROPORCAO["9:16"]: 1, sel.QUANTIDADE[1]: 1}
    pagina = _Pagina(presentes=presentes,
                     marcados=["chrome_extension elementos", "crop_9_16 9:16", "x1"],
                     modelo_no_dropdown="Omni 1.1 Flash",
                     itens_do_menu=["Omni 1.1 Flash"], chip="video 9:16 x1")

    await TransportePlaywright()._configurar(
        pagina, _pedido(modo=Modo.INGREDIENTS2V, modelo=Modelo.OMNI_FLASH,
                        duracao="4s"), Diario(1))

    assert sel.DURACAO["4s"] not in pagina.clicados
    assert sel.RESOLUCAO["720p"] not in pagina.clicados


async def test_imagem_nunca_procura_grupo_de_duracao():
    """`SEM_DURACAO` vale so para IMAGEM: o painel de imagem nao tem o grupo."""
    pagina = _Pagina(presentes=_presentes_de_imagem(), marcados=_marcados_de_imagem())
    await TransportePlaywright()._configurar(pagina, _pedido(duracao="4s"), Diario(1))
    assert not [s for s in pagina.consultados if s in sel.DURACAO.values()]


# ------------------------------------------------ o chip e um interruptor

async def test_painel_ja_aberto_nao_leva_clique():
    """Clicar no chip com ele aberto FECHA. Por isso se pergunta antes de clicar."""
    pagina = _Pagina(presentes=_presentes_de_imagem())
    assert await chip.garantir(pagina, Diario(1)) is True
    assert sel.CHIP_CONFIG not in pagina.clicados


async def test_painel_fechado_e_aberto_pelo_chip():
    pagina = _Pagina(presentes=_presentes_de_imagem())
    pagina.fechar()
    assert await chip.garantir(pagina, Diario(1)) is True
    assert sel.CHIP_CONFIG in pagina.clicados


# --------------------------------------------------------- fechar de verdade

async def test_fechar_insiste_porque_ha_dois_niveis_de_overlay():
    """O dropdown de modelo abre por cima do painel: o primeiro Escape fecha so o de
    cima."""
    pagina = _Pagina(presentes=_presentes_de_imagem(), escapes_para_fechar=2)
    await chip.fechar(pagina)
    assert pagina.escapes == 2


async def test_painel_que_nao_fecha_e_erro_e_nao_segue_calado():
    pagina = _Pagina(presentes=_presentes_de_imagem(), escapes_para_fechar=99)
    with pytest.raises(GeracaoFalhou, match="nao fechou"):
        await chip.fechar(pagina)


async def test_veu_que_sobra_conta_como_painel_aberto():
    """MEDIDO 2026-08-20: o painel do chip fica aberto SEM veu, e o veu pode sobrar
    sem painel. Cada metade ja falhou sozinha; por isso as duas."""
    pagina = _Pagina(presentes=_presentes_de_imagem())
    pagina.fechar()
    pagina.presentes[sel.VEU_ABERTO] = 1
    assert await chip.fechado(pagina) is False


# ------------------------------------------------------- leitura de instrumento

async def test_custo_que_o_painel_nao_anuncia_nao_derruba_a_geracao():
    """E leitura de instrumento, nao verificacao: enquanto o numero nao alimentar a
    reserva, um deploy que mude a frase nao pode derrubar um job correto."""
    pagina = _Pagina(presentes=_presentes_de_imagem(), marcados=_marcados_de_imagem(),
                     custo=None)
    t = TransportePlaywright()

    chip = await t._configurar(pagina, _pedido(), Diario(1))

    assert chip == "imagem crop_9_16 x1"
    assert t._ultimo_custo is None


# ------------------------------------------- a corrida entre o clique e a leitura

class _PaginaLentaParaMarcar(_Pagina):
    """O painel que reflete o clique TARDE — o Angular ainda nao repintou.

    Ate `leituras_ate_marcar`, `marcados()` devolve o estado ANTIGO; dali em diante,
    o novo. E o duplo mais honesto da corrida: o clique pegou, quem atrasa e a tela.
    """

    def __init__(self, *, leituras_ate_marcar: int, depois: list[str], **kw):
        super().__init__(**kw)
        self.leituras = 0
        self.leituras_ate_marcar = leituras_ate_marcar
        self.depois = list(depois)

    async def evaluate(self, js, arg=None):
        if "aria-checked=true" in js:
            self.leituras += 1
            if self.leituras >= self.leituras_ate_marcar:
                return list(self.depois)
        return await super().evaluate(js, arg)


def _presentes_x4():
    return {**_presentes_de_imagem(), sel.QUANTIDADE[4]: 1}


async def test_quantidade_que_demora_a_marcar_e_esperada_e_nao_reprovada():
    """MEDIDO 2026-09-12 por `scratch/sonda_x4_painel.py`: o clique em quantidade PEGA.

    Quem errava era a leitura. Quantidade e o ULTIMO grupo configurado — o unico sem
    um clique seguinte que lhe de tempo de assentar —, e `marcados()` era lido na
    linha de baixo, ainda com o valor do job ANTERIOR. Os jobs 862, 955, 1007 e 1014
    pediram x4 e leram x1; o 869 e o 1010 pediram x1 e leram x4. Nenhum se perdeu,
    porque o retry do orquestrador acerta na volta seguinte — cada um custou um ciclo
    de navegador inteiro para descobrir que a configuracao ja estava certa.
    """
    pagina = _PaginaLentaParaMarcar(
        presentes=_presentes_x4(),
        marcados=["image imagem", "crop_9_16 9:16", "x1"],   # o resto do job anterior
        depois=["image imagem", "crop_9_16 9:16", "x4"],
        leituras_ate_marcar=3,
        chip="imagem crop_9_16 x4")

    chip_lido = await TransportePlaywright()._configurar(
        pagina, _pedido(quantidade=4), Diario(1))

    assert chip_lido == "imagem crop_9_16 x4"
    assert pagina.leituras >= 3, "nao adiantou esperar se leu uma vez so"


async def test_quantidade_que_nunca_marca_continua_sendo_erro():
    """A espera nao pode virar um `except: segue` com outro nome.

    Se o botao de fato nao pegou, a geracao sai no numero errado — e em video cada
    saida extra custa cheio. Esgotado o prazo, a conferencia reprova como sempre.
    """
    pagina = _Pagina(presentes=_presentes_x4(),
                     marcados=["image imagem", "crop_9_16 9:16", "x1"])

    with pytest.raises(GeracaoFalhou, match="nao confirma a configuracao"):
        await TransportePlaywright()._configurar(
            pagina, _pedido(quantidade=4), Diario(1))
