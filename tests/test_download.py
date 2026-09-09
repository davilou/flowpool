"""O caminho do download, com pagina falsa.

Era o maior bloco sem teste do transporte: `_baixar` tem 90 linhas, decide o que baixar
lendo o menu do card, e ja custou quatro videos gerados e pagos (jobs 170/174/189/195,
frames2v) alem de dois por sono fixo (jobs 29 e 30). Nada disso tinha rede.

`captura` entra por duplo: e um colaborador com contrato proprio — intercepta o
`<a download>` e le o blob em pedacos — e tem os testes dela. O que se exercita aqui e a
DECISAO: que resolucao pedir, quando recusar o card, onde gravar o arquivo.
"""

from pathlib import Path

import pytest

from flowpool.nucleo.tipos import Conta, Modo, Tier
from flowpool.transporte.base import GeracaoFalhou, Sessao
from flowpool.transporte.pw import captura
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import Diario
from flowpool.transporte.pw.transporte import TransportePlaywright

MENU_VIDEO = ["fazer o download", "adicionar ao cenario", "publicar no youtube",
              "mover para a lixeira"]
MENU_IMAGEM = ["fazer o download", "animar", "mover para a lixeira"]


class _Itens:
    """Os itens de UM menu."""

    def __init__(self, rotulos, dono):
        self.rotulos = rotulos
        self.dono = dono

    async def all_inner_texts(self):
        return list(self.rotulos)

    async def count(self):
        return len(self.rotulos)

    @property
    def first(self):
        return self

    async def wait_for(self, timeout=None):
        pass

    async def click(self, timeout=None):
        rotulo = self.rotulos[0] if self.rotulos else None
        self.dono.clicados.append(rotulo)
        # o submenu de resolucao nasce DO clique em "Baixar", e nao antes dele
        if rotulo and "download" in rotulo and self.dono.submenu is not None:
            self.dono.menus.append(self.dono.submenu)

    def nth(self, i):
        return _Itens([self.rotulos[i]], self.dono)


class _Menu:
    def __init__(self, rotulos, dono):
        self.rotulos = rotulos
        self.dono = dono

    def locator(self, seletor):
        if seletor == sel.MENU_ITEM:
            return _Itens(self.rotulos, self.dono)
        if seletor == sel.MENU_BAIXAR:
            return _Itens([r for r in self.rotulos if "download" in r], self.dono)
        return _Itens([], self.dono)

    async def wait_for(self, timeout=None):
        pass


class _Menus:
    def __init__(self, pagina):
        self.pagina = pagina

    async def count(self):
        return len(self.pagina.menus_agora())

    def nth(self, i):
        return _Menu(self.pagina.menus_agora()[i], self.pagina)

    @property
    def last(self):
        return _Menu(self.pagina.menus_agora()[-1], self.pagina)


class _Card:
    def __init__(self, indice):
        self.indice = indice

    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 100, "height": 100}


class _Tiles:
    def __init__(self, n):
        self.n = n

    async def count(self):
        return self.n

    def nth(self, i):
        return _Card(i)


class _Mouse:
    def __init__(self):
        self.cliques = []

    async def click(self, x, y, button=None):
        self.cliques.append((x, y, button))


class _Pagina:
    """Uma grade com um tile, e os menus abertos na tela.

    Dois jeitos de dizer que menus existem, porque sao dois defeitos diferentes:

    `submenu`   nasce DO clique em "Baixar" — e a sequencia real do menu do card.
    `por_volta` diz que menus ha em cada volta do relogio, para exercitar o submenu
                que DEMORA a montar, que e o defeito dos jobs 29 e 30.
    """

    def __init__(self, inicial, submenu=None, por_volta=None):
        self.menus = list(inicial)
        self.submenu = submenu
        self.por_volta = por_volta
        self.volta = 0
        self.mouse = _Mouse()
        self.clicados = []
        self.ouvintes = {}
        self.dormiu_ms = 0

    def menus_agora(self):
        if self.por_volta is not None:
            return self.por_volta[min(self.volta, len(self.por_volta) - 1)]
        return self.menus

    def locator(self, seletor):
        if seletor == sel.CARD_GRADE:
            return _Tiles(1)
        if seletor == sel.MENU_CARD:
            return _Menus(self)
        return _Itens([], self)

    async def wait_for_timeout(self, ms):
        self.dormiu_ms += ms
        self.volta += 1

    def on(self, evento, f):
        self.ouvintes.setdefault(evento, []).append(f)

    def remove_listener(self, evento, f):
        self.ouvintes[evento].remove(f)

    def is_closed(self):
        return False


def _sessao(pagina):
    return Sessao(conta=Conta(id=1, email="a@b.c", tier=Tier.FREE, perfil="p"),
                  handle=(None, None, pagina))


@pytest.fixture
def captura_falsa(monkeypatch):
    """`captura` vira duplo: o contrato dela e testado no lugar dela."""
    estado = {"gravou": [], "zerou": 0, "instalado": True,
              "capturas": [{"url": "blob:x", "nome": "saida.jpeg", "via": "a.click()"}]}

    async def instalado(pagina):
        return estado["instalado"]

    async def zerar(pagina):
        estado["zerou"] += 1

    async def capturados(pagina):
        return list(estado["capturas"])

    async def gravar_blob(pagina, url, destino, pedaco=None):
        estado["gravou"].append(Path(destino))
        return 4242

    monkeypatch.setattr(captura, "instalado", instalado)
    monkeypatch.setattr(captura, "zerar", zerar)
    monkeypatch.setattr(captura, "capturados", capturados)
    monkeypatch.setattr(captura, "gravar_blob", gravar_blob)
    return estado


# ------------------------------------------------- _baixar: o que se recusa

async def test_card_de_imagem_num_job_de_video_e_recusado_sem_baixar(captura_falsa):
    """JOBS 170/174/189/195: o frames2v sobe dois quadros de referencia, eles viram
    tiles, e o robo abriu um deles achando que era o video. Baixar o que estivesse la
    gravaria a foto de referencia no lugar do video, calado — pior que falhar."""
    pagina = _Pagina([MENU_IMAGEM])
    t = TransportePlaywright()
    t._modo_do_card[(1, "card-0")] = Modo.T2V

    with pytest.raises(GeracaoFalhou, match="card de imagem e o job e video"):
        await t._baixar(_sessao(pagina), "card-0", Path("scratch/x.mp4"), Diario(1))

    assert captura_falsa["gravou"] == [], "nao pode ter gravado nada"


async def test_interceptador_ausente_diz_o_que_fazer(captura_falsa):
    captura_falsa["instalado"] = False
    pagina = _Pagina([MENU_IMAGEM], submenu=["1k", "2k"])
    t = TransportePlaywright()
    t._modo_do_card[(1, "card-0")] = Modo.IMAGEM

    with pytest.raises(GeracaoFalhou, match="interceptador de download"):
        await t._baixar(_sessao(pagina), "card-0", Path("scratch/x.png"), Diario(1))


# --------------------------------------------- _baixar: o que se le da tela

async def test_o_tipo_vem_da_tela_quando_a_memoria_do_modo_se_perdeu(
        captura_falsa, tmp_path):
    """`_modo_do_card` morre a cada reinicio do servico. Sem ler a tela, o job que
    gerou antes e baixou depois caia no default de video e pedia 720p num submenu de
    imagem, que so tem 1k — a mensagem que o usuario viu em 2026-09-09."""
    pagina = _Pagina([MENU_IMAGEM], submenu=["1k", "2k"])
    t = TransportePlaywright()          # sem `_modo_do_card`: simula o restart

    real = await t._baixar(_sessao(pagina), "card-0", tmp_path / "x.png", Diario(1))

    assert "1k" in pagina.clicados, f"pediu a resolucao errada: {pagina.clicados}"
    assert real.suffix == ".jpeg"


async def test_a_extensao_real_vence_a_que_o_orquestrador_pediu(captura_falsa, tmp_path):
    """O Flow escolhe a extensao. Salvar em `.png` gravaria no banco um caminho que
    por dentro e JPEG, e ninguem descobriria ate um consumidor engasgar."""
    pagina = _Pagina([MENU_IMAGEM], submenu=["1k"])
    t = TransportePlaywright()
    t._modo_do_card[(1, "card-0")] = Modo.IMAGEM

    real = await t._baixar(_sessao(pagina), "card-0", tmp_path / "saida.png", Diario(1))

    assert real == tmp_path / "saida.jpeg"
    assert captura_falsa["gravou"] == [tmp_path / "saida.jpeg"]
    assert captura_falsa["zerou"] == 1, "captura anterior nao pode contar como esta"


# ----------------------------------------------------- _esperar_submenu_com

async def test_submenu_e_achado_pelo_conteudo_e_nao_por_ser_o_ultimo():
    """JOBS 29 e 30: a versao antiga dormia 1,5 s e pegava `MENU_CARD.last`. Com o
    submenu demorando, o `.last` ainda era o menu de cima, que nao tem 720p."""
    pagina = _Pagina([], por_volta=[[["720p", "1080p"], MENU_VIDEO]])  # alvo e o PRIMEIRO
    itens, indice = await TransportePlaywright()._esperar_submenu_com(
        pagina, "720p", Diario(1))
    assert (await itens.all_inner_texts())[indice] == "720p"


async def test_submenu_que_demora_e_esperado_em_vez_de_dar_erro():
    pagina = _Pagina([], por_volta=[[MENU_VIDEO], [MENU_VIDEO],
                                    [MENU_VIDEO, ["720p", "1080p"]]])
    itens, indice = await TransportePlaywright()._esperar_submenu_com(
        pagina, "720p", Diario(1))
    assert (await itens.all_inner_texts())[indice] == "720p"
    assert pagina.dormiu_ms > 0, "chegou tarde: tem que ter esperado"


async def test_submenu_que_nunca_vem_lista_o_que_havia_na_tela():
    """Sem isso a falha chegava como "achei 0" sem dizer o que existia."""
    pagina = _Pagina([MENU_VIDEO])
    with pytest.raises(GeracaoFalhou, match="adicionar ao cenario"):
        await TransportePlaywright()._esperar_submenu_com(
            pagina, "720p", Diario(1), teto_ms=1_000)


# --------------------------------------------------------- _esperar_captura

async def test_download_nativo_vira_erro_que_nomeia_o_arquivo(captura_falsa):
    """O Chrome 152 TRAVA o processo ao iniciar download nativo de blob. Se o Flow
    voltar a esse caminho, o erro tem que dizer isso — nao um travamento mudo."""
    captura_falsa["capturas"] = []
    pagina = _Pagina([MENU_VIDEO])
    original = pagina.wait_for_timeout

    async def dispara_o_nativo(ms):
        for f in pagina.ouvintes["download"]:
            f(type("Dl", (), {"suggested_filename": "video.mp4"})())
        await original(ms)

    pagina.wait_for_timeout = dispara_o_nativo
    with pytest.raises(GeracaoFalhou, match="download NATIVO"):
        await TransportePlaywright()._esperar_captura(pagina, Diario(1), teto_ms=2_000)


async def test_esperar_captura_nao_deixa_ouvinte_para_tras(captura_falsa):
    """Uma sessao baixa varias vezes: ouvinte a mais por chamada e vazamento com cara
    de bug intermitente."""
    pagina = _Pagina([MENU_VIDEO])
    await TransportePlaywright()._esperar_captura(pagina, Diario(1))
    assert pagina.ouvintes["download"] == []


async def test_captura_que_nunca_chega_falha_dizendo_quanto_esperou(captura_falsa):
    captura_falsa["capturas"] = []
    pagina = _Pagina([MENU_VIDEO])
    with pytest.raises(GeracaoFalhou, match="nenhum blob foi capturado em 2s"):
        await TransportePlaywright()._esperar_captura(pagina, Diario(1), teto_ms=2_000)
