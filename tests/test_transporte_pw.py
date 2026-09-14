"""As partes do transporte real que dao para testar sem rede.

O transporte inteiro toca o Google e so se verifica rodando (`fumaca.py`). Mas cada
decisao PURA dentro dele ja custou alguma coisa quando morava solta no meio de uma
corrotina, e e essa parte que esta aqui.
"""

from pathlib import Path

import pytest

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import (
    CardTrocado,
    Estado,
    GeracaoFalhou,
    LimiteDeUso,
    Pedido,
    SaldoInsuficiente,
    Sessao,
)
from flowpool.transporte.pw import acoes, analise, chip, grade
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.analise import (
    achar_no_submenu,
    agente_ligado,
    apareceu_na_lista,
    arquivos_do_pedido,
    caminho_final,
    confirmacao_fraca,
    custo_do_texto,
    escolher_item_enviado,
    escolher_opcao,
    etapas_ate_o_modo,
    exigir_agente_desligado,
    exigir_proporcao_do_modo,
    exigir_referencia_incluida,
    exigir_referencias_completas,
    exigir_selecao,
    exigir_unico,
    faltando_no_chip,
    id_da_midia,
    id_do_href,
    item_pronto,
    nome_do_item,
    saldo_do_texto,
)
from flowpool.transporte.pw.diario import (
    Diario,
    descartar_traco,
    guardar_traco,
    iniciar_traco,
    proximo_traco,
    traco_ligado,
)
from flowpool.transporte.pw.dom import seguro
from flowpool.transporte.pw.transporte import (
    MAX_REPETICOES,
    TransportePlaywright,
)

# --- saldo: o texto do menu do avatar -----------------------------------


def test_saldo_sai_do_texto_do_menu():
    textos = ["Painel", "38 Créditos do Google Flow", "Sair"]
    assert saldo_do_texto(textos) == 38


def test_saldo_le_o_rotulo_sem_acento_tambem():
    # o rotulo vem do i18n do Google; um deploy que troque "Créditos" por "Creditos"
    # nao pode zerar a reconciliacao de credito do pool inteiro
    assert saldo_do_texto(["12 Creditos do Google Flow"]) == 12


def test_saldo_de_conta_paga_tem_separador_de_milhar():
    # `int("1.000")` levanta ValueError longe daqui, no meio da reconciliacao
    assert saldo_do_texto(["1.000 Créditos do Google Flow"]) == 1000


def test_saldo_zero_e_um_numero_valido_e_nao_ausencia():
    # zero e exatamente o estado que interessa (conta seca); confundir com "nao achei"
    # faria o pool tratar conta vazia como erro de leitura
    assert saldo_do_texto(["0 Créditos do Google Flow"]) == 0


def test_sem_creditos_no_texto_e_erro_e_nao_zero():
    with pytest.raises(GeracaoFalhou, match="saldo"):
        saldo_do_texto(["Painel", "Sair", "Configurações"])


# --- id do projeto: o UUID no href do card ------------------------------


def test_uuid_sai_do_href_do_card():
    href = "https://labs.google/fx/pt/tools/flow/project/6f0c1d2e-1111-2222-3333-444455556666"
    assert id_do_href(href) == "6f0c1d2e-1111-2222-3333-444455556666"


def test_uuid_ignora_barra_no_fim():
    assert id_do_href(sel.PADRAO_PROJETO + "abc-123/") == "abc-123"


def test_uuid_ignora_query_e_fragmento():
    # um `?ref=` colado no fim viraria parte do UUID e o `a[href*="{id}"]` de
    # `abrir_projeto` deixaria de casar com o card
    assert id_do_href(sel.PADRAO_PROJETO + "abc-123?ref=lista#topo") == "abc-123"


def test_href_que_nao_e_de_projeto_nao_vira_id():
    assert id_do_href("/fx/pt/tools/flow") == ""
    assert id_do_href(None) == ""


# --- ambiguidade e erro, nunca escolha ----------------------------------


def test_um_candidato_passa():
    exigir_unico(1, "botao", "modo")


@pytest.mark.parametrize("quantos", [0, 2, 7])
def test_zero_ou_mais_de_um_candidato_e_erro(quantos):
    with pytest.raises(GeracaoFalhou, match="esperava 1 candidato"):
        exigir_unico(quantos, "seletor", "item Baixar")


class _LocatorFalso:
    def __init__(self, quantos):
        self.quantos = quantos
        self.clicado = False

    async def count(self):
        return self.quantos

    async def click(self, timeout=None):
        self.clicado = True


class _EscopoFalso:
    def __init__(self, quantos):
        self.loc = _LocatorFalso(quantos)

    def locator(self, seletor):
        return self.loc


async def test_clicar_unico_clica_quando_ha_so_um():
    escopo = _EscopoFalso(1)
    await TransportePlaywright()._clicar_unico(escopo, "[role=menuitem]", "item")
    assert escopo.loc.clicado is True


async def test_clicar_unico_nao_clica_em_menu_ambiguo():
    # a regra nasceu de um `.last` que mandou um video do usuario para a lixeira,
    # porque o ultimo item do menu do card e "Mover para a lixeira"
    escopo = _EscopoFalso(3)
    with pytest.raises(GeracaoFalhou):
        await TransportePlaywright()._clicar_unico(escopo, "[role=menuitem]", "item")
    assert escopo.loc.clicado is False


async def test_clicar_unico_nao_clica_quando_nao_ha_candidato():
    escopo = _EscopoFalso(0)
    with pytest.raises(GeracaoFalhou):
        await TransportePlaywright()._clicar_unico(escopo, "[role=menuitem]", "item")
    assert escopo.loc.clicado is False


# --- submenu de download: os rotulos tem quebra de linha ----------------

MENU_VIDEO = ["270p\nGIF animado", "720p", "1080p", "4K\nExige plano pago"]
MENU_IMAGEM = ["1K\nTamanho original", "2K\nAumentada", "4K"]


def test_escolhe_a_resolucao_de_video_pela_primeira_linha():
    assert escolher_opcao(MENU_VIDEO, "720p") == 1


def test_rotulo_com_quebra_de_linha_casa_pela_primeira_linha():
    # passar "270p\nGIF animado" inteiro para um seletor CSS estoura o parser
    assert escolher_opcao(MENU_VIDEO, "270p") == 0


def test_escolhe_o_tamanho_original_no_menu_de_imagem():
    assert escolher_opcao(MENU_IMAGEM, "1k") == 0


def test_rotulo_com_ligadura_de_icone_na_frente_ainda_casa():
    # varios itens deste app trazem a ligadura como primeira linha ("download\nBaixar")
    assert escolher_opcao(["download\n1K\nTamanho original", "2K\nAumentada"],
                          "1k") == 0


def test_resolucao_de_video_pedida_num_menu_de_imagem_e_erro():
    # o default seguro: pedir 720p num menu de imagem GRITA, em vez de baixar o item
    # errado em silencio
    with pytest.raises(GeracaoFalhou):
        escolher_opcao(MENU_IMAGEM, "720p")


def test_dois_itens_com_o_mesmo_rotulo_sao_erro():
    with pytest.raises(GeracaoFalhou, match="esperava 1 candidato"):
        escolher_opcao(["720p", "720p\nsegunda copia"], "720p")


def test_1080p_nao_e_confundido_com_1k():
    assert escolher_opcao(MENU_VIDEO, "1080p") == 2


# --- caminho de destino: quem escolhe a extensao e o Flow ---------------


def test_o_destino_ganha_a_extensao_real_do_arquivo_baixado():
    # o orquestrador monta `.mp4`/`.png` a partir do modo; imagem sai JPEG
    real = caminho_final(Path("saida/Panelas/take1.png"),
                         "Red_ceramic_mug_on_table_202608201158.jpeg")
    assert real == Path("saida/Panelas/take1.jpeg")


def test_o_nome_e_a_pasta_continuam_sendo_os_nossos():
    # `suggested_filename` traz o prompt inteiro; nao e disso que a pasta precisa
    real = caminho_final(Path("saida/Panelas/take1.mp4"),
                         "Red_ceramic_mug_on_table_202608201311.mp4")
    assert real.parent == Path("saida/Panelas")
    assert real.stem == "take1"


def test_sugestao_sem_extensao_mantem_a_do_destino():
    assert caminho_final(Path("a/b.mp4"), "arquivo_sem_ponto") == Path("a/b.mp4")


def test_aceita_destino_em_texto():
    assert caminho_final("scratch/fumaca.mp4", "x.jpeg") == Path("scratch/fumaca.jpeg")


# --- configurou, confere ------------------------------------------------


CHIP_VIDEO = "video - 720p - 8s crop_9_16 x1"


def test_chip_que_bate_com_o_pedido_nao_tem_pendencia():
    assert faltando_no_chip(CHIP_VIDEO, "t2v", "9:16", 1, "8s") == []


def test_duracao_que_nao_pegou_e_apontada():
    # o caso real: pedidos 4s, saiu 8s, credito debitado e ninguem percebeu
    assert faltando_no_chip(CHIP_VIDEO, "t2v", "9:16", 1, "4s") == ["4s"]


def test_proporcao_que_nao_pegou_e_apontada():
    chip = "video - 720p - 8s crop_16_9 x1"
    assert faltando_no_chip(chip, "t2v", "9:16", 1, "8s") == ["crop_9_16"]


def test_quantidade_que_nao_pegou_e_apontada():
    assert faltando_no_chip(CHIP_VIDEO, "t2v", "9:16", 4, "8s") == ["x4"]


def test_modo_que_nao_trocou_para_imagem_e_apontado():
    # MEDIDO: imagem NAO se anuncia no chip — o lugar do nome do modo fica com o do
    # modelo ("<emoji> nano banana 2 lite crop_9_16 x1"). Confirma-se por AUSENCIA dos
    # outros modos, senao um chip que continuou em video passaria como imagem. A
    # mensagem precisa dizer o que ele ainda diz, ou quem le o log procura errado.
    faltou = faltando_no_chip(CHIP_VIDEO, "imagem", "9:16", 1, None)
    assert len(faltou) == 1 and "imagem" in faltou[0] and "video" in faltou[0]


def test_chip_de_imagem_legitimo_nao_tem_pendencia():
    chip = "nano banana 2 lite crop_9_16 x1"
    assert faltando_no_chip(chip, "imagem", "9:16", 1, None) == []


def test_imagem_nao_cobra_duracao_do_chip():
    # nao ha grupo de duracao no painel em modo imagem; exigir "8s" ali reprovaria
    # toda geracao de imagem
    chip = "imagem · 1k crop_square x4"
    assert faltando_no_chip(chip, "imagem", "1:1", 4, None) == []


# --- proporcao por modo: recusa antes de gastar -------------------------


def test_video_so_aceita_vertical_e_horizontal():
    with pytest.raises(GeracaoFalhou, match="modo t2v aceita so"):
        exigir_proporcao_do_modo("t2v", "1:1")


def test_imagem_aceita_as_cinco():
    for razao in ("16:9", "4:3", "1:1", "3:4", "9:16"):
        exigir_proporcao_do_modo("imagem", razao)


def test_video_vertical_passa():
    exigir_proporcao_do_modo("t2v", "9:16")


# --- o interruptor do Agente: garantir o modo, nao torcer ---------------
#
# VISTO EM PRODUCAO: "as vezes aquele botaozinho de agente esta clicado. E ai o
# playwright nao consegue identificar as coisas."
#
# MEDIDO 2026-08-21, os dois estados, no perfil conta1:
#   <button aria-pressed="false">Agente</button>   -> modo direto, o chip existe
#   <button aria-pressed="true">Agente</button>    -> agente conversacional, e
#                                                     `CHIP_CONFIG` casa ZERO elementos
# Ligado, `_configurar` morre na primeira linha com um timeout que fala do CHIP e nunca
# do interruptor que o tirou da tela. Por isso a garantia roda antes de tocar no chip.


def test_agente_ligado_precisa_de_clique():
    assert agente_ligado(sel.AGENTE_LIGADO) is True


def test_agente_desligado_nao_precisa_de_clique():
    assert agente_ligado(sel.AGENTE_DESLIGADO) is False


def test_o_marcador_medido_e_aria_pressed_true_e_false():
    # se alguem trocar os valores em seletores.py sem medir, os testes acima passariam
    # comparando a constante consigo mesma. Esta linha ancora no que foi MEDIDO.
    assert (sel.ATRIBUTO_AGENTE, sel.AGENTE_LIGADO, sel.AGENTE_DESLIGADO) == (
        "aria-pressed", "true", "false")


@pytest.mark.parametrize("pressed", [None, "", "mixed", "TRUE", "1"])
def test_marcador_desconhecido_e_erro_e_nao_desligado(pressed):
    # tratar o desconhecido como "desligado" reabriria o defeito: num deploy que troque
    # o atributo, toda geracao voltaria a rodar com o agente possivelmente ligado, e o
    # sintoma seria de novo um timeout falando do chip
    with pytest.raises(GeracaoFalhou, match="nao da para saber se o Agente"):
        agente_ligado(pressed)


def test_clicou_e_continuou_ligado_e_erro():
    with pytest.raises(GeracaoFalhou, match="continuou LIGADO"):
        exigir_agente_desligado(sel.AGENTE_LIGADO)


def test_clicou_e_desligou_passa():
    exigir_agente_desligado(sel.AGENTE_DESLIGADO)


class _BotaoAgenteFalso:
    """Um interruptor de mentira: le e troca `aria-pressed`, e conta os cliques.

    `gruda=True` e o botao que NAO obedece — o clique acontece e o estado continua
    ligado. E o caso que precisa virar erro em vez de seguir para um chip que nao
    existe.
    """

    def __init__(self, pressed, gruda=False):
        self.pressed = pressed
        self.gruda = gruda
        self.cliques = 0

    @property
    def first(self):
        return self

    async def wait_for(self, timeout=None):
        return None

    async def count(self):
        return 1

    async def get_attribute(self, nome):
        assert nome == sel.ATRIBUTO_AGENTE
        return self.pressed

    async def click(self, timeout=None):
        self.cliques += 1
        if not self.gruda:
            self.pressed = sel.AGENTE_DESLIGADO


class _PaginaComAgente:
    def __init__(self, botao):
        self.botao = botao

    def locator(self, seletor):
        assert seletor == sel.BOTAO_AGENTE
        return self.botao

    async def wait_for_timeout(self, ms):
        return None


async def test_agente_ligado_e_clicado_antes_de_configurar():
    botao = _BotaoAgenteFalso(sel.AGENTE_LIGADO)
    desligou = await chip.exigir_agente_desligado(
        _PaginaComAgente(botao))
    assert (desligou, botao.cliques, botao.pressed) == (
        True, 1, sel.AGENTE_DESLIGADO)


async def test_agente_desligado_nao_e_tocado():
    # clicar "por garantia" LIGARIA o agente — interruptor nao tem clique inofensivo
    botao = _BotaoAgenteFalso(sel.AGENTE_DESLIGADO)
    desligou = await chip.exigir_agente_desligado(
        _PaginaComAgente(botao))
    assert (desligou, botao.cliques) == (False, 0)


async def test_clique_que_nao_pegou_derruba_a_geracao():
    botao = _BotaoAgenteFalso(sel.AGENTE_LIGADO, gruda=True)
    with pytest.raises(GeracaoFalhou, match="continuou LIGADO"):
        await chip.exigir_agente_desligado(_PaginaComAgente(botao))


async def test_interruptor_ambiguo_ou_ausente_e_erro():
    # zero e "o interruptor sumiu da tela"; dois e ambiguidade. Nenhum se resolve
    # chutando: o vizinho deste botao na barra do prompt e o de ENVIAR.
    class _Sumido(_BotaoAgenteFalso):
        async def count(self):
            return 0

    botao = _Sumido(sel.AGENTE_DESLIGADO)
    with pytest.raises(GeracaoFalhou, match="esperava 1 candidato"):
        await chip.exigir_agente_desligado(_PaginaComAgente(botao))
    assert botao.cliques == 0


# --- Frames e Elementos sao SUBMODOS de Video ---------------------------
#
# MEDIDO 2026-08-20, ao vivo, num job que falhou tres vezes com
#   "modo: esperava 1 candidato para 'button:has-text(\"chrome_extension\")', achei 0"
# A lista de modos do painel DEPENDE do modo atual:
#   chip em VIDEO  -> image Imagem | videocam Video | crop_free Frames | chrome_extension Elementos
#   chip em IMAGEM -> image Imagem | videocam Video
# Frames e Elementos nao existem enquanto o chip estiver em imagem.


def test_frames_e_elementos_sao_submodos_de_video():
    # a hierarquia e um FATO DA INTERFACE, e por isso mora em seletores.py — nao numa
    # esperteza escondida na sequencia do transporte
    assert sel.MODO_PAI["frames2v"] == "t2v"
    assert sel.MODO_PAI["ingredients2v"] == "t2v"


def test_os_dois_modos_de_topo_nao_tem_pai():
    assert "t2v" not in sel.MODO_PAI
    assert "imagem" not in sel.MODO_PAI


def test_modo_visivel_no_painel_e_um_clique_so():
    # nao forcar dois passos quando um basta: cada clique extra e uma animacao a mais
    # e uma chance a mais de o painel redesenhar no meio
    assert etapas_ate_o_modo("imagem", True) == ["imagem"]
    assert etapas_ate_o_modo("frames2v", True) == ["frames2v"]


def test_submodo_ausente_passa_pelo_video_primeiro():
    # e a correcao do defeito: vindo de imagem, `chrome_extension` nao existe no painel
    assert etapas_ate_o_modo("ingredients2v", False) == ["t2v", "ingredients2v"]
    assert etapas_ate_o_modo("frames2v", False) == ["t2v", "frames2v"]


def test_modo_de_topo_ausente_e_erro_e_nao_caminho_inventado():
    # `imagem` e `videocam` existem nos dois estados do painel; um deles sumir e deploy
    # do Google, e o conserto e em seletores.py — nao um clique adivinhado aqui
    for modo in ("imagem", "t2v"):
        with pytest.raises(GeracaoFalhou, match="painel"):
            etapas_ate_o_modo(modo, False)


# --- o rotulo do submodo no chip: tolerancia declarada ------------------


CHIP_FRAMES = "frames - 720p - 4s crop_9_16 x1"
CHIP_SO_VIDEO = "video - 720p - 4s crop_9_16 x1"
CHIP_IMAGEM = "nano banana 2 lite crop_9_16 x1"


def test_chip_que_nomeia_o_submodo_confirma():
    assert faltando_no_chip(CHIP_FRAMES, "frames2v", "9:16", 1, "4s") == []


def test_chip_que_so_diz_video_ainda_confirma_o_submodo():
    # NAO MEDIDO: nao se sabe se o chip nomeia "Frames"/"Elementos" ou se mostra so
    # "Video" nos submodos. Recusar aqui seria falso negativo — bloquearia o modo
    # inteiro por causa de uma palavra que talvez nunca apareca.
    assert faltando_no_chip(CHIP_SO_VIDEO, "frames2v", "9:16", 1, "4s") == []
    assert faltando_no_chip(CHIP_SO_VIDEO, "ingredients2v", "9:16", 1, "4s") == []


def test_chip_que_continuou_em_imagem_reprova_o_submodo():
    # ESTE e o caso que o defeito produzia: o clique no submodo nao acontecia e a
    # geracao seguia em imagem. Tolerar o rotulo do pai nao pode tolerar isto.
    faltou = faltando_no_chip(CHIP_IMAGEM, "ingredients2v", "9:16", 1, "4s")
    assert "elementos" in faltou


def test_chip_de_video_puro_nao_e_confundido_com_frames():
    # o contrario tambem: t2v so aceita "video", e um chip que diz "frames" reprova
    assert faltando_no_chip(CHIP_FRAMES, "t2v", "9:16", 1, "4s") == ["video"]


def test_confirmacao_pelo_rotulo_do_pai_e_declarada():
    # a tolerancia nao pode ser calada: a corrida imprime a medicao que falta, e a
    # linha vira o valor definitivo de ROTULO_MODO_NO_CHIP
    aviso = confirmacao_fraca(CHIP_SO_VIDEO, "frames2v")
    assert aviso and "frames2v" in aviso and "video" in aviso


def test_confirmacao_pelo_rotulo_proprio_nao_avisa_nada():
    assert confirmacao_fraca(CHIP_FRAMES, "frames2v") is None
    assert confirmacao_fraca(CHIP_VIDEO, "t2v") is None
    assert confirmacao_fraca(CHIP_IMAGEM, "imagem") is None


# --- quais arquivos o pedido anexa -------------------------------------
#
# Estes testes eram sobre `_anexar` inteiro, com uma pagina falsa de um `input` so.
# Deixaram de ser: MEDIDO 2026-08-20, `set_input_files` sozinho nao anexa nada — so
# manda o arquivo para o acervo. `_anexar` agora percorre o seletor de midia (abrir,
# enviar, esperar, incluir), o que e SEQUENCIA e so se verifica rodando. O que continua
# testavel — e o que ja errou duas vezes — e a ESCOLHA dos arquivos, que virou
# `analise.arquivos_do_pedido`.


def _pedido(modo, **extra):
    return Pedido(modo=modo, modelo=Modelo.NANO_BANANA, prompt="p", **extra)


def test_imagem_com_referencia_anexa_em_vez_de_descartar():
    # O BUG antigo: a referencia de imagem era jogada fora sem aviso. O pipeline do
    # usuario COMECA aqui — foto da roupa -> modelo vestida.
    assert arquivos_do_pedido(_pedido(Modo.IMAGEM, referencias=["a.png"])) == \
        [str(Path("a.png"))]


def test_imagem_sem_referencia_continua_sendo_texto_puro():
    # imagem sem referencia e o caso comum e NAO pode virar erro
    assert arquivos_do_pedido(_pedido(Modo.IMAGEM)) == []


def test_imagem_respeita_o_mesmo_teto_de_tres():
    assert len(arquivos_do_pedido(
        _pedido(Modo.IMAGEM, referencias=["a.png", "b.png", "c.png", "d.png"]))) == 3


def test_imagem_nao_anexa_frame_nenhum():
    # frame inicial e final continuam exclusivos de frames2v
    assert arquivos_do_pedido(_pedido(Modo.IMAGEM, frame_inicial="a.png")) == []


def test_t2v_nao_anexa_nada():
    assert arquivos_do_pedido(_pedido(Modo.T2V, referencias=["a.png"])) == []


def test_frames2v_anexa_inicial_e_final_nessa_ordem():
    assert arquivos_do_pedido(
        _pedido(Modo.FRAMES2V, frame_inicial="i.png", frame_final="f.png")) == \
        [str(Path("i.png")), str(Path("f.png"))]


def test_frames2v_com_so_o_inicial_nao_inventa_o_final():
    assert arquivos_do_pedido(_pedido(Modo.FRAMES2V, frame_inicial="i.png")) == \
        [str(Path("i.png"))]


async def test_ingredients2v_sem_referencia_continua_sendo_erro():
    # o modo EXIGE arquivo; a lista vazia so vira erro dentro de `_anexar`, e e la que
    # o teste tem que bater para a regra nao sumir na mudanca de casa
    with pytest.raises(GeracaoFalhou, match="sem arquivo"):
        await TransportePlaywright()._anexar(None, _pedido(Modo.INGREDIENTS2V),
                                            Diario(1))


# --- o item do seletor de midia: nome, pronto e qual e o novo -----------
#
# Toda esta secao existe por causa de UM defeito medido: a referencia subia para o
# acervo do projeto e nunca entrava no comando. O conserto passa pelo seletor de midia,
# e cada decisao dele que da para errar em silencio esta aqui.


def test_o_nome_do_item_e_a_primeira_linha():
    # MEDIDO: cada item rende duas linhas — nome e tipo
    assert nome_do_item("folha-frames.png\nImagem") == "folha-frames.png"


def test_item_gerado_leva_o_inicio_do_comando_como_nome():
    assert nome_do_item("Red ceramic mug on table\nVideo") == "Red ceramic mug on table"


def test_item_sem_texto_nao_estoura():
    assert nome_do_item("") == ""
    assert nome_do_item(None) == ""


def test_item_so_esta_pronto_quando_tem_miniatura():
    # MEDIDO com 2,9 MB: o item entra na lista NA HORA, sem miniatura, e a imagem so
    # chega aos 17,25 s. Incluir antes disso e anexar referencia vazia — sem erro, com
    # credito gasto e uma imagem que nao parece com a referencia.
    assert item_pronto({"miniaturas": 1}) is True
    assert item_pronto({"miniaturas": 0}) is False
    assert item_pronto({}) is False


def _src(id_midia, video=False):
    """O `src` da miniatura, como MEDIDO 2026-08-21.

    Video traz um parametro colado no fim, e e por isso que o id se EXTRAI em vez de
    comparar o src inteiro.
    """
    fim = "&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL" if video else ""
    return f"/fx/api/trpc/media.getMediaUrlRedirect?name={id_midia}{fim}"


def _item(nome, selecionado=False, miniaturas=1, tipo="Imagem", id_midia=None):
    return {"texto": f"{nome}\n{tipo}", "selecionado": selecionado,
            "miniaturas": miniaturas,
            "src": _src(id_midia or f"id-de-{nome}") if miniaturas else ""}


# --- o ID DA MIDIA: o que o nome do arquivo nao identifica --------------


def test_o_id_sai_do_parametro_name_do_src():
    assert id_da_midia(_src("166c3d5d")) == "166c3d5d"


def test_o_id_de_video_ignora_o_parametro_colado_no_fim():
    # MEDIDO: item de video traz `&mediaUrlType=...`. Comparar o src INTEIRO faria um
    # video nunca casar com a propria ficha na barra do prompt.
    assert id_da_midia(_src("c474ed85", video=True)) == "c474ed85"


def test_src_sem_id_nao_vira_id_vazio_util():
    # id vazio nao pode ser tratado como identidade: dois itens sem miniatura
    # "casariam" entre si, que e a classe de erro que o id existe para fechar
    assert id_da_midia("") == ""
    assert id_da_midia(None) == ""
    assert id_da_midia("/fx/api/trpc/media.getMediaUrlRedirect") == ""


# --- O DEFEITO DE 21/08: o homonimo velho do acervo ---------------------
#
# O acervo do projeto PERSISTE ENTRE JOBS, e os arquivos vem de `_coladas/` com nome
# por hash — entao reusar a mesma personagem entre geracoes e o caso COMUM, nao o raro.
# Quando o arquivo ja foi enviado antes, a lista tem um item de MESMO NOME que JA TEM
# MINIATURA, e a busca por nome o aceitava como "meu upload terminou" no primeiro
# instante, com o arquivo real ainda em voo.
#
# EVIDENCIA NO BANCO (conta 6, mesmo projeto): `8ced8effebecb2f9.jpg` entrou no acervo
# no job 23 e, nos jobs 26 e 30, era a SEGUNDA referencia — e e onde travava. O job 27
# saiu "pronto" com as fotos TROCADAS, e o 29 falhou nas tres tentativas. A propria
# repeticao fabricava a condicao: a tentativa 0 subia A e morria na B, a tentativa 1
# subia A de novo, e ja havia um homonimo pronto para a espera devolver na hora.


def test_homonimo_velho_do_acervo_nao_passa_por_upload_terminado():
    # ESTE E O TESTE QUE REPRODUZ O DEFEITO. Antes do conserto ele devolvia o item
    # VELHO no primeiro instante — e dai saia a foto errada, sem erro, com credito
    # gasto. O retrato de antes e o que separa os dois homonimos.
    velho = _item("blusa.png", id_midia="ja-estava-no-acervo")
    novo = _item("blusa.png", miniaturas=0, id_midia="acabou-de-subir")
    assert escolher_item_enviado([novo, velho], "blusa.png",
                                 {"ja-estava-no-acervo"}) is None


def test_o_homonimo_novo_e_escolhido_quando_fica_pronto():
    velho = _item("blusa.png", id_midia="ja-estava-no-acervo")
    novo = _item("blusa.png", selecionado=True, id_midia="acabou-de-subir")
    assert escolher_item_enviado([novo, velho], "blusa.png",
                                 {"ja-estava-no-acervo"}) == "acabou-de-subir"


def test_segunda_tentativa_do_mesmo_job_inclui_o_certo():
    # a tentativa 0 subiu `blusa.png` e morreu depois; a tentativa 1 sobe de novo. Sao
    # DOIS itens de mesmo nome, os dois com miniatura — e so o retrato distingue.
    da_tentativa_0 = _item("blusa.png", id_midia="upload-da-tentativa-0")
    da_tentativa_1 = _item("blusa.png", selecionado=True, id_midia="upload-agora")
    assert escolher_item_enviado([da_tentativa_1, da_tentativa_0], "blusa.png",
                                 {"upload-da-tentativa-0"}) == "upload-agora"


def test_acha_o_item_recem_enviado_pelo_nome_do_arquivo():
    itens = [_item("blusa.png", selecionado=True), _item("Red ceramic mug on table")]
    assert escolher_item_enviado(itens, "blusa.png", set()) == "id-de-blusa.png"


def test_item_que_ainda_nao_apareceu_e_espere_mais_e_nao_erro():
    # `None` = "espere"; quem chama fica no laco ate o teto e so entao levanta
    assert escolher_item_enviado([_item("outro.png")], "blusa.png", set()) is None


def test_item_que_apareceu_mas_ainda_sobe_e_espere_mais():
    assert escolher_item_enviado(
        [_item("blusa.png", miniaturas=0)], "blusa.png", set()) is None


def test_dois_uploads_na_mesma_espera_desempatam_pelo_selecionado():
    # MEDIDO: o Flow deixa o RECEM-ENVIADO selecionado. Recusar a ambiguidade aqui —
    # como `escolher_opcao` faz nos menus — travaria a geracao; e ela quase nao
    # acontece, porque se sobe um arquivo por vez.
    itens = [_item("blusa.png", id_midia="a"),
             _item("blusa.png", selecionado=True, id_midia="b")]
    assert escolher_item_enviado(itens, "blusa.png", set()) == "b"


def test_sem_nenhum_selecionado_vale_o_primeiro_porque_a_lista_e_recentes():
    itens = [_item("blusa.png", id_midia="a"), _item("blusa.png", id_midia="b")]
    assert escolher_item_enviado(itens, "blusa.png", set()) == "a"


def test_o_selecionado_so_ganha_se_estiver_pronto():
    # um item selecionado mas ainda subindo nao pode passar na frente do que ja
    # terminou: incluir o de cima anexaria referencia vazia
    itens = [_item("blusa.png", selecionado=True, miniaturas=0, id_midia="a"),
             _item("blusa.png", id_midia="b")]
    assert escolher_item_enviado(itens, "blusa.png", set()) == "b"


def test_nome_parecido_nao_casa():
    # `blusa.png` nao pode casar com `blusa-2.png`: o `in` solto anexaria o arquivo
    # errado sem nenhum sintoma
    assert escolher_item_enviado([_item("blusa-2.png")], "blusa.png", set()) is None


def test_lista_vazia_e_espere_mais():
    assert escolher_item_enviado([], "blusa.png", set()) is None


def test_apareceu_na_lista_ignora_o_homonimo_velho():
    # senao a mensagem de erro diria "apareceu e nao ficou pronto" sobre um item que
    # nem e o meu, e mandaria investigar upload lento onde o envio nem pegou
    velho = _item("blusa.png", id_midia="ja-estava-no-acervo")
    assert apareceu_na_lista([velho], "blusa.png", {"ja-estava-no-acervo"}) is False
    novo = _item("blusa.png", miniaturas=0, id_midia="novo")
    assert apareceu_na_lista([velho, novo], "blusa.png",
                             {"ja-estava-no-acervo"}) is True


# --- conferir QUAL entrou, e nao so quantas -----------------------------


def test_a_selecao_precisa_ser_exatamente_a_minha():
    # "Incluir no comando" age sobre o que esta MARCADO, e nao sobre o item que o
    # codigo julga ter escolhido. Enquanto ninguem conferia, os dois divergiam sem
    # sintoma e o arquivo errado entrava na geracao.
    exigir_selecao(["meu"], "meu", "blusa.png")
    with pytest.raises(GeracaoFalhou, match="selecao errada"):
        exigir_selecao(["outro"], "meu", "blusa.png")
    with pytest.raises(GeracaoFalhou, match="selecao errada"):
        exigir_selecao([], "meu", "blusa.png")
    with pytest.raises(GeracaoFalhou, match="selecao errada"):
        exigir_selecao(["meu", "outro"], "meu", "blusa.png")


def test_a_barreira_aprova_a_ficha_certa():
    exigir_referencia_incluida(["a"], ["a", "b"], "b", "blusa.png")


def test_a_barreira_reprova_ficha_que_nao_chegou():
    # erro no ponto certo, com o nome do arquivo que sumiu — e nao um "pedi 2 e vejo
    # 1" no fim, que nao diz qual
    with pytest.raises(GeracaoFalhou, match="nenhuma ficha nova"):
        exigir_referencia_incluida(["a"], ["a"], "b", "blusa.png")


def test_a_barreira_reprova_ficha_de_outro_arquivo():
    with pytest.raises(GeracaoFalhou, match="nao e a do arquivo enviado"):
        exigir_referencia_incluida(["a"], ["a", "z"], "b", "blusa.png")


def test_duas_fichas_iguais_reprovam():
    # O DEFEITO, EM UMA LINHA. A conferencia por contagem via duas e aprovava: a
    # geracao saia com a mesma foto no lugar de duas, sem erro, com credito gasto, e
    # o usuario so descobriu conferindo a conta.
    with pytest.raises(GeracaoFalhou, match="anexo REPETIDO"):
        exigir_referencias_completas(["b", "b"], ["a", "b"])


def test_duas_referencias_distintas_passam():
    exigir_referencias_completas(["a", "b"], ["a", "b"])
    exigir_referencias_completas(["b", "a"], ["a", "b"])   # a ordem nao importa


def test_referencia_faltando_reprova_por_quantidade():
    with pytest.raises(GeracaoFalhou, match="pedi 2 referencia"):
        exigir_referencias_completas(["a"], ["a", "b"])


def test_ficha_de_arquivo_que_ninguem_enviou_reprova():
    with pytest.raises(GeracaoFalhou, match="anexo TROCADO"):
        exigir_referencias_completas(["a", "z"], ["a", "b"])


# --- o custo que o painel anuncia --------------------------------------
#
# MEDIDO 2026-08-20: o painel do chip diz "A geração vai usar N créditos" antes de
# gerar. Derruba a conclusao anterior de que so o agente conversacional sabia o preco.


def test_custo_sai_da_frase_do_painel():
    assert custo_do_texto("A geração vai usar 12 créditos") == 12


def test_custo_le_a_frase_ja_normalizada_sem_acento():
    # o texto passa por `dom.seguro` antes de ser lido; o caminho que le o valor
    # normalizado nao pode deixar de casar
    assert custo_do_texto("a geracao vai usar 7 creditos") == 7


def test_custo_zero_e_valido_e_e_justamente_o_da_imagem():
    assert custo_do_texto("A geração vai usar 0 créditos") == 0


def test_custo_de_conta_paga_tem_separador_de_milhar():
    assert custo_do_texto("A geração vai usar 1.200 créditos") == 1200


def test_frase_ausente_e_erro_e_nao_zero():
    # zero e o preco da imagem; confundi-lo com "nao achei" faria a leitura parecer
    # quebrada exatamente onde ela esta certa
    with pytest.raises(GeracaoFalhou, match="custo"):
        custo_do_texto("Nano Banana 2 Lite")


def test_o_singular_tambem_casa():
    assert custo_do_texto("A geração vai usar 1 crédito") == 1


# --- o que se imprime tem que caber no console -------------------------
#
# `dom.seguro` e o unico caminho de saida para texto vindo do Flow, e ele so cumpria
# metade do contrato: tirava acento e deixava EMOJI. O rotulo do modelo de imagem tem
# um — e imprimir emoji num console cp1252 nao troca a letra por '?', levanta
# UnicodeEncodeError e derruba a ferramenta. Foi assim que o `conferir.py` morreu no
# meio do roteiro de modos, com todos os seletores corretos.


def test_emoji_do_rotulo_de_modelo_nao_derruba_a_impressao():
    chip = seguro("\U0001f34c Nano Banana 2 Lite crop_9_16 x1")
    chip.encode("cp1252")            # e este encode que estourava
    assert "nano banana 2 lite" in chip


def test_o_que_importa_no_chip_sobrevive_ao_corte():
    # a verificacao do chip le o texto ja normalizado; tirar o emoji nao pode levar
    # junto a ligadura da proporcao nem a quantidade
    chip = seguro("\U0001f34c Nano Banana 2 Lite crop_9_16 x1")
    assert faltando_no_chip(chip, "imagem", "9:16", 1, None) == []


def test_acento_e_segredo_continuam_saindo():
    assert seguro("Vídeo") == "video"
    # `seguro` tambem passa por `sem_acento`, que baixa a caixa: a mascara sai
    # minuscula, e e assim que ela aparece no log
    assert "<mascarado>" in seguro("token ya29.AbCdEf123")


# --- geracoes simultaneas: a chave dos cards e (conta, card) ------------
#
# `id_remoto` e POSICIONAL: `card-0` quer dizer "o primeiro item da grade DAQUELA
# conta". Com duas geracoes ao mesmo tempo em contas diferentes, as duas produzem
# `card-0`, e uma tabela indexada so pelo card faria um job baixar a resolucao
# escolhida pelo outro.


def test_dois_cards_de_mesmo_nome_em_contas_diferentes_nao_se_misturam():
    t = TransportePlaywright()
    t._modo_do_card[(1, "card-0")] = Modo.IMAGEM
    t._modo_do_card[(2, "card-0")] = Modo.T2V
    t._repeticoes[(1, "card-0")] = 2
    t._repeticoes[(2, "card-0")] = 0

    assert t._modo_do_card[(1, "card-0")] is Modo.IMAGEM
    assert t._modo_do_card[(2, "card-0")] is Modo.T2V
    assert t._repeticoes[(2, "card-0")] == 0


# --- o aviso de direitos aparece NO MEIO do upload ----------------------


class _LocatorContado:
    def __init__(self, pagina, seletor):
        self.pagina = pagina
        self.seletor = seletor

    async def count(self):
        return self.pagina.quantos(self.seletor)

    async def click(self, timeout=None):
        self.pagina.clicados.append(self.seletor)
        if self.seletor == sel.BOTAO_CONCORDO:
            self.pagina.aviso_aceito = True


class _PaginaComAviso:
    """A tela do seletor de midia durante um upload que trava atras do aviso.

    Reproduz o que foi medido em producao: o modal so aparece quando o Flow COMECA a
    receber o arquivo (aqui, na volta 6 do laco — uns 3 s), e enquanto ele estiver na
    tela o item nunca fica pronto.
    """

    def __init__(self, aparece_na_volta=6, pronto_apos=2):
        self.voltas = 0
        self.aparece_na_volta = aparece_na_volta
        self.pronto_apos = pronto_apos
        self.aviso_aceito = False
        self.voltas_com_aviso_aceito = 0
        self.clicados = []

    @property
    def aviso_na_tela(self):
        return self.voltas >= self.aparece_na_volta and not self.aviso_aceito

    def quantos(self, seletor):
        if seletor in (sel.AVISO_DIREITOS, sel.VEU_ABERTO):
            return 1 if self.aviso_na_tela else 0
        if seletor == sel.BOTAO_CONCORDO:
            return 1 if self.aviso_na_tela else 0
        return 0

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    async def evaluate(self, script, seletor):
        # o item so fica PRONTO (com miniatura) depois de o aviso sair da tela — e a
        # miniatura e onde mora o id, entao sem ela o item nao tem id nenhum. E o que
        # foi MEDIDO: o item entra na lista vazio e ganha a imagem no fim do upload.
        if self.aviso_aceito:
            self.voltas_com_aviso_aceito += 1
        pronto = self.aviso_aceito and self.voltas_com_aviso_aceito > self.pronto_apos
        return [{"texto": "gato.png", "selecionado": False,
                 "miniaturas": 1 if pronto else 0,
                 "src": _src("id-do-gato") if pronto else ""}]

    async def wait_for_timeout(self, ms):
        self.voltas += 1


async def test_o_aviso_de_direitos_e_pego_quando_aparece_no_meio_do_upload():
    # ANTES: a checagem era uma janela fixa de 2 s ANTES da espera. O aviso chega
    # entre 7 e 17 s (o tempo do upload), entao ela quase nunca o encontrava: o modal
    # ficava na tela, a espera contava ate 180 s e a conta perdia a primeira tentativa
    # inteira. Uma vez em CADA conta do pool — com cinco estreando em paralelo, cinco
    # jobs perdendo a estreia juntos.
    pagina = _PaginaComAviso()
    id_midia = await TransportePlaywright()._esperar_item_pronto(
        pagina, "gato.png", set(), Diario(1), teto_ms=60_000)

    assert id_midia == "id-do-gato"
    assert pagina.aviso_aceito is True
    assert sel.BOTAO_CONCORDO in pagina.clicados


async def test_upload_sem_aviso_nao_clica_em_nada():
    # a ausencia e o caso comum: o aviso existe uma vez por conta, e todas as geracoes
    # seguintes nao podem pagar preco nenhum por ele
    pagina = _PaginaComAviso(aparece_na_volta=10_000)
    pagina.aviso_aceito = True          # ja aceito ha muito tempo nesta conta
    id_midia = await TransportePlaywright()._esperar_item_pronto(
        pagina, "gato.png", set(), Diario(1), teto_ms=60_000)

    assert id_midia == "id-do-gato"
    assert pagina.clicados == []


# --- o card de falha e reconhecido pela estrutura -----------------------


class _CardFalso:
    def __init__(self, presentes):
        self.presentes = presentes

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    def quantos(self, seletor):
        return 1 if seletor in self.presentes else 0


async def test_as_duas_frases_de_falha_sao_reconhecidas(monkeypatch):
    """Duas frases, nao uma — e a licao sobrevive a troca de aplicacao.

    VISTO EM PRODUCAO 2026-08-21: "Falha ao gerar audio. Tente usar outro comando ou
    envie feedback." nao casava `text="Ops! Algo deu errado"`, e `consultar` devolvia
    RODANDO ate o timeout de dez minutos. Para o usuario, "travado".

    Os VALORES daqueles seletores morreram com a aplicacao antiga e ainda nao foram
    remedidos na nova (ficaram PENDENTE). O que este teste guarda e a LOGICA: com dois
    seletores validos, QUALQUER um deles reconhece a falha.
    """
    monkeypatch.setattr(sel, "CARD_FALHA", 'text="Ops! Algo deu errado"')
    monkeypatch.setattr(sel, "CARD_FALHA_TITULO", r"text=/^\s*Falha\s*$/")
    assert await grade.falhou(_CardFalso({sel.CARD_FALHA})) is True
    assert await grade.falhou(_CardFalso({sel.CARD_FALHA_TITULO})) is True
    assert await grade.falhou(_CardFalso(set())) is False


async def test_card_normal_nao_e_falha():
    assert await grade.falhou(_CardFalso(set())) is False


async def test_seletor_pendente_nao_vira_falha_nem_vai_para_o_playwright():
    """PENDENTE vale "...", e o Playwright le isso como XPath.

    MEDIDO 2026-09-05 numa geracao real: `consultar` estourou com
    `SyntaxError: The string '...' is not a valid XPath expression` — um erro que fala
    de sintaxe quando o fato e que ninguem mediu o card de falha na aplicacao nova.

    ⚠️ `CARD_FALHA` e `CARD_FALHA_TITULO` foram MEDIDOS em 2026-09-09 e nao sao mais
    pendentes. A regra guardada aqui e a que sobreviveu a medicao, e e mais geral:
    seletor pendente NUNCA vai para o Playwright; seletor medido vai.
    """
    class _So_o_medido:
        def __init__(self):
            self.vistos = []

        def locator(self, seletor):
            if seletor == sel.PENDENTE:
                raise AssertionError(
                    f"seletor pendente chegou ao Playwright: {seletor!r}")
            self.vistos.append(seletor)
            return _ContaZero()

    class _ContaZero:
        async def count(self):
            return 0

    card = _So_o_medido()
    assert await grade.falhou(card) is False
    # os dois medidos foram consultados de verdade — sem isto o teste passaria
    # mesmo se alguem devolvesse os seletores para PENDENTE
    assert sel.CARD_FALHA in card.vistos
    assert sel.CARD_FALHA_TITULO in card.vistos


class _CardParcial:
    """Um card de falha em que so algumas marcas casam."""

    def __init__(self, casam):
        self.casam = casam

    def locator(self, seletor):
        return _ContaFixa(1 if seletor in self.casam else 0)


class _ContaFixa:
    def __init__(self, n):
        self.n = n

    async def count(self):
        return self.n


async def test_recusa_sem_cobranca_exige_a_marca_do_motivo_E_a_da_nao_cobranca():
    """Uma marca so nao basta: "limite" aparece noutros avisos do Flow, e concluir
    recusa por engano tiraria uma conta boa do pool por ESPERA_RESET inteiro."""
    # so a frase do motivo, sem "nao houve cobranca": nao decide
    assert await grade.recusa_sem_cobranca(_CardParcial({sel.CARD_LIMITE_DE_USO})) is False
    # so "nao houve cobranca": tambem nao
    assert await grade.recusa_sem_cobranca(_CardParcial({sel.CARD_SEM_COBRANCA})) is False
    # as duas: e recusa
    assert await grade.recusa_sem_cobranca(
        _CardParcial({sel.CARD_LIMITE_DE_USO, sel.CARD_SEM_COBRANCA})) is True


async def test_atividade_incomum_tambem_e_recusa_sem_cobranca():
    """⚠️ MEDIDO por print 2026-09-09, e custou os jobs 583-588 inteiros.

    O card dizia:

        Falha
        Notamos uma atividade incomum. Aguarde alguns instantes antes de tentar de
        novo ou acesse a Central de Ajuda para mais informacoes.
        Nao houve cobranca por esta geracao.

    E o bloqueio antiabuso do Google — as tres contas dispararam no mesmo segundo,
    todas sem proxy. A acao certa e a MESMA do limite de uso: nao houve cobranca,
    repetir nesta conta nao adianta, troca-se de conta. Como a frase nao casava, o
    codigo caia no ramo do repetir e morria no seletor PENDENTE.
    """
    assert await grade.recusa_sem_cobranca(
        _CardParcial({sel.CARD_ATIVIDADE_INCOMUM, sel.CARD_SEM_COBRANCA})) is True
    # sem a marca de nao-cobranca continua sendo falha comum, que se repete pelo card
    assert await grade.recusa_sem_cobranca(
        _CardParcial({sel.CARD_ATIVIDADE_INCOMUM})) is False


def test_desligar_o_audio_continua_por_medir():
    # o caminho vale mais que o conserto: o usuario descarta o audio de todo video, e
    # desligar audio nas configuracoes faria a classe inteira de falha deixar de
    # existir. Mexer em configuracao da conta dele precisa da autorizacao dele.
    assert sel.CONFIG_SEM_AUDIO == sel.PENDENTE


def test_o_teto_de_repeticoes_tem_margem_sobre_o_caso_medido():
    # MEDIDO 2026-08-21: um caso real (falha ao gerar audio) precisou de 2 repeticoes.
    # Com teto 2 o app batia exatamente no limite e uma falha um pouco mais teimosa
    # seria descartada sem motivo. Repetir pelo card NAO cobra; desistir cedo custa o
    # job inteiro — nova tentativa, novo navegador, chip reconfigurado, referencias
    # re-anexadas. A assimetria manda no numero.
    assert MAX_REPETICOES > 2


# --- o traco e o log de passo: instrumento que nao pode quebrar nada ----
#
# Existem por um defeito aberto: geracao com DUAS referencias falha nas primeiras
# tentativas e passa numa posterior, com
#   TargetClosedError: Page.wait_for_timeout: Target page, context or browser
#   has been closed
# Tres hipoteses ja foram deduzidas do erro final e as tres estavam erradas. O que
# falta e ver a tela no instante exato.
#
# O que se testa AQUI nao e o conteudo do traco — isso so se ve abrindo o zip. E a
# unica propriedade que o instrumento nao pode perder: NAO DERRUBAR O JOB QUE ELE
# DIAGNOSTICA, e nao encher o disco em job que deu certo.


class _TracingQuebrado:
    """A gravacao falha de todo jeito. E o que acontece com o navegador ja morto."""

    async def start(self, **kw):
        raise RuntimeError("browser has been closed")

    async def stop(self, path=None):
        raise RuntimeError("browser has been closed")


class _TracingFalso:
    def __init__(self):
        self.iniciado = False
        self.paradas: list[str | None] = []

    async def start(self, **kw):
        self.iniciado = True

    async def stop(self, path=None):
        self.paradas.append(path)


class _CtxFalso:
    def __init__(self, tracing=None):
        self.tracing = tracing if tracing is not None else _TracingFalso()
        self.fechado = False

    async def close(self):
        self.fechado = True


class _PwFalso:
    def __init__(self):
        self.parado = False

    async def stop(self):
        self.parado = True


async def test_traco_que_nao_inicia_nao_derruba_o_job():
    # a regra que este modulo existe para cumprir: diagnostico que quebra a coisa que
    # ele diagnostica e pior do que nenhum
    assert await iniciar_traco(_CtxFalso(_TracingQuebrado())) is False


async def test_traco_que_nao_grava_nao_derruba_o_job(tmp_path):
    # e o caso QUE MAIS INTERESSA: com o navegador morto nao ha de onde coletar o que
    # foi gravado. O traco se perde, o job segue, e o log de passo — que e stdout —
    # continua sendo a prova do que aconteceu
    assert await guardar_traco(_CtxFalso(_TracingQuebrado()),
                               tmp_path / "x.zip") is None


async def test_descartar_traco_engole_a_propria_falha():
    await descartar_traco(_CtxFalso(_TracingQuebrado()))   # nao levanta


async def test_desligavel_por_ambiente(monkeypatch):
    # a gravacao tem custo por acao; quem estiver medindo tempo precisa poder tirar o
    # instrumento do caminho sem editar codigo
    monkeypatch.setenv("FLOWPOOL_TRACO", "0")
    ctx = _CtxFalso()
    assert traco_ligado() is False
    assert await iniciar_traco(ctx) is False
    assert ctx.tracing.iniciado is False


async def test_desligado_por_padrao(monkeypatch):
    """O padrao se INVERTEU em 2026-09-05, e a razao e medida.

    Bisseccao do download de video no Flow novo:

        traco  aba de guarda   resultado
        on     sim             FALHA
        off    nao             FALHA
        off    sim             FUNCIONA

    Com traco ligado, TODO video gerado se perde no download — gerado, pago e sem
    arquivo. O traco existe para diagnosticar; ele nao pode ser a causa.
    """
    monkeypatch.delenv("FLOWPOOL_TRACO", raising=False)
    assert traco_ligado() is False


async def test_traco_liga_por_variavel_de_ambiente(monkeypatch):
    # continua disponivel para investigacao, com o custo conhecido
    monkeypatch.setenv("FLOWPOOL_TRACO", "1")
    assert traco_ligado() is True


def test_o_nome_do_traco_nao_sobrescreve_o_anterior(tmp_path):
    # sem relogio de proposito: `datetime.now()` dentro do transporte tornaria o teste
    # dependente da hora, e o que importa e so nao perder o traco de ontem
    primeiro = proximo_traco(6, tmp_path)
    assert primeiro.name == "conta6-0001.zip"
    primeiro.write_bytes(b"x")
    assert proximo_traco(6, tmp_path).name == "conta6-0002.zip"


def test_os_tracos_de_contas_diferentes_nao_disputam_numero(tmp_path):
    # ate cinco navegadores trabalham ao mesmo tempo, um por conta
    (tmp_path / "conta6-0001.zip").write_bytes(b"x")
    assert proximo_traco(7, tmp_path).name == "conta7-0001.zip"


async def test_job_bem_sucedido_nao_deixa_arquivo_no_disco(tmp_path, monkeypatch):
    # alguns MB por traco, dezenas de jobs por dia: guardar tudo enche o disco em uma
    # semana. Em sucesso, `stop()` vai SEM `path`, que e o descarte do Playwright.
    monkeypatch.chdir(tmp_path)
    t = TransportePlaywright()
    ctx, pw = _CtxFalso(), _PwFalso()
    s = Sessao(conta=Conta(id=6, email="a@b.com", tier=Tier.FREE, perfil="p"),
               handle=(pw, ctx, None))
    t._tracando.add(6)

    await t.fechar(s)

    assert ctx.tracing.paradas == [None]        # parou, sem caminho = descartou
    assert list(tmp_path.rglob("*.zip")) == []
    assert (ctx.fechado, pw.parado) == (True, True)


async def test_falha_guarda_o_traco_uma_vez_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = TransportePlaywright()
    ctx = _CtxFalso()
    s = Sessao(conta=Conta(id=6, email="a@b.com", tier=Tier.FREE, perfil="p"),
               handle=(_PwFalso(), ctx, None))
    t._tracando.add(6)

    await t._traco_da_falha(s)
    # a segunda chamada nao pode parar de novo: `consultar` pode devolver FALHOU e
    # `baixar` falhar em seguida na mesma sessao, e um `stop` duplo levanta
    await t._traco_da_falha(s)

    assert len(ctx.tracing.paradas) == 1
    assert ctx.tracing.paradas[0].endswith("conta6-0001.zip")


async def test_o_traco_ja_guardado_nao_e_descartado_no_fechar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = TransportePlaywright()
    ctx, pw = _CtxFalso(), _PwFalso()
    s = Sessao(conta=Conta(id=6, email="a@b.com", tier=Tier.FREE, perfil="p"),
               handle=(pw, ctx, None))
    t._tracando.add(6)

    await t._traco_da_falha(s)
    await t.fechar(s)

    assert len(ctx.tracing.paradas) == 1        # so a gravacao da falha


# --- o log de passo -----------------------------------------------------


class _RelogioDeMentira:
    """Um relogio que so anda quando o teste manda. Nao dorme, nao olha a maquina."""

    def __init__(self):
        self.agora = 0.0

    def __call__(self):
        return self.agora


def test_espera_curta_nao_imprime_nada(capsys):
    # quase toda espera termina rapido; uma linha "ainda esperando" no segundo zero
    # seria ruido em todo job
    relogio = _RelogioDeMentira()
    d = Diario(6, relogio=relogio)
    for segundo in (1.0, 5.0, 14.0):
        relogio.agora = segundo
        d.esperando("a miniatura de blusa.png")
    assert capsys.readouterr().out == ""


def test_espera_longa_avisa_uma_vez_a_cada_janela(capsys):
    # `_esperar_item_pronto` gira a cada 500 ms: sem limite, um upload de 3 minutos
    # escreveria 360 linhas iguais. E com silencio total o usuario fecha a janela
    # achando que travou — foi o que aconteceu em 21/08.
    relogio = _RelogioDeMentira()
    d = Diario(6, relogio=relogio)
    for decimo in range(1, 451):        # 45 s, amostrados como o laco real amostra
        relogio.agora = decimo * 0.1
        d.esperando("a miniatura de blusa.png")
    saida = capsys.readouterr().out
    assert saida.count("ainda esperando") == 2      # aos 15s e aos 30s, nao 450 vezes
    assert "a miniatura de blusa.png" in saida


def test_a_janela_conta_do_inicio_DAQUELA_espera(capsys):
    # a espera da referencia 2 comeca depois da 1 ter terminado. Se a janela contasse
    # do inicio da SESSAO, ela avisaria "ainda esperando" na primeira volta — dizendo
    # "ainda" sobre uma espera que acabou de comecar.
    relogio = _RelogioDeMentira()
    d = Diario(6, relogio=relogio)
    relogio.agora = 40.0
    d.esperando("a miniatura de blusa2.png")
    assert capsys.readouterr().out == ""
    relogio.agora = 46.0
    d.esperando("a miniatura de blusa2.png")
    assert capsys.readouterr().out == ""
    relogio.agora = 56.0
    d.esperando("a miniatura de blusa2.png")
    assert "ainda esperando a miniatura de blusa2.png" in capsys.readouterr().out


def test_o_passo_carrega_a_conta_e_o_tempo(capsys):
    # ate cinco geracoes escrevem na mesma saida; sem a conta no prefixo o log de
    # cinco jobs vira um so, ilegivel
    relogio = _RelogioDeMentira()
    d = Diario(6, relogio=relogio)
    relogio.agora = 2.4
    d.passo("configurando: imagem / nano-banana / 9:16 / x1")
    assert capsys.readouterr().out == (
        "[   2s] conta 6: configurando: imagem / nano-banana / 9:16 / x1\n")


# --- o submenu de download: esperar o ESTADO, nao dormir 1,5 s ----------
#
# CUSTOU DOIS VIDEOS GERADOS E PAGOS (jobs 29 e 30, conta 6, tres tentativas cada):
#   GeracaoFalhou: opcao de menu: esperava 1 candidato para "rotulo '720p'", achei 0
# Os dois com `id_remoto` gravado e `arquivo = None` — gerados, cobrados, sem download.
#
# A causa: `baixar` dormia 1,5 s e depois pegava `MENU_CARD.last`. Quando o submenu de
# resolucao demorava — e ele demora justamente quando a pagina acabou de renderizar um
# video novo — o `.last` ainda era o menu DE CIMA, que nao tem "720p".

MENU_PAI = ["motion_blur\nAnimar", "download\nBaixar", "share\nCompartilhar",
            "delete\nMover para a lixeira"]


def test_submenu_que_ainda_nao_veio_e_espere_mais_e_nao_achei_zero():
    # ESTE E O TESTE QUE REPRODUZ O DEFEITO. So o menu PAI esta na tela; o submenu de
    # resolucao ainda nao montou. "Achei 0" mandava o job morrer; o certo e esperar.
    assert achar_no_submenu([MENU_PAI], "720p") is None


def test_acha_a_resolucao_no_submenu_certo_mesmo_com_dois_menus_abertos():
    # identifica pelo CONTEUDO, e nao por ser o ultimo: `.last` com dois menus abertos
    # e tao fragil quanto indice em lista que reordena
    menus = [MENU_PAI, MENU_VIDEO]
    assert achar_no_submenu(menus, "720p") == (1, 1)


def test_a_ordem_dos_menus_na_tela_nao_decide():
    menus = [MENU_VIDEO, MENU_PAI]
    assert achar_no_submenu(menus, "1080p") == (0, 2)


def test_menu_de_imagem_nao_tem_720p_e_isso_e_espere_mais():
    # e quem estoura o teto e o transporte, com um erro que LISTA os rotulos achados
    assert achar_no_submenu([MENU_IMAGEM], "720p") is None


def test_a_mesma_resolucao_em_dois_menus_e_erro_e_nao_escolha():
    # a regra dura: quando o texto casa com mais de um candidato, ERRO. No menu do card
    # o vizinho errado e "Mover para a lixeira".
    with pytest.raises(GeracaoFalhou, match="esperava 1 candidato"):
        achar_no_submenu([MENU_VIDEO, MENU_VIDEO], "720p")


def test_sem_menu_nenhum_e_espere_mais():
    assert achar_no_submenu([], "720p") is None


# --- o painel montou? A REGRESSAO do acervo vazio -----------------------
#
# REGRESSAO EM PRODUCAO (21/08): `_esperar_seletor_midia` exigia `[role=option]` > 0
# como prova de que o painel montou. Num projeto NOVO o acervo esta VAZIO — zero itens,
# LEGITIMAMENTE — entao o criterio nunca se satisfazia: o painel abria, o codigo dizia
# "nao abriu", apertava Escape, repetia tres vezes e levantava
#     GeracaoFalhou: o seletor de midia nao abriu depois de 3 cliques no anexar
# Projeto novo NUNCA anexava a primeira referencia. Como o usuario trabalha com um
# projeto por produto, todo produto novo nascia travado — bloqueio total, nao
# intermitente. Projetos #8 e #9 e o job #38 morreram assim.
#
# O engano foi conceitual: "a lista tem itens" foi tomado como prova de que o painel
# montou. Acervo vazio nao e painel quebrado, e o codigo nunca ve esse estado de novo
# depois do primeiro upload — por isso a verificacao ao vivo, feita num projeto com
# acervo cheio, passou batido.
#
# MEDIDO 2026-08-21 no projeto vazio: as ABAS existem nos dois acervos (7 abertas, 0
# fechado), e o `input[type=file]` NAO serve — nem esta dentro do dialogo, nem
# distingue aberto de fechado.


class _PainelFalso:
    """Uma tela com o seletor de midia em algum estado. So conta e le atributo."""

    def __init__(self, expandido, dialogos, visivel, opcoes, abas, tem_mais=1):
        # `tem_mais` = quantos `+` existem na tela. MEDIDO: em modo FRAMES a barra vira
        # `flow-ingredient-bar` e o `+` some — ler `aria-expanded` dele ali estoura.
        self.estado = {
            sel.BOTAO_ANEXAR: tem_mais,
            sel.SELETOR_MIDIA: (dialogos, visivel),
            sel.ITEM_MIDIA: opcoes,
            sel.ABAS_SELETOR_MIDIA: abas,
        }
        self.expandido = expandido
        self.voltas = 0

    def locator(self, seletor):
        return _LocPainel(self, seletor)

    async def wait_for_timeout(self, ms):
        self.voltas += 1


class _LocPainel:
    def __init__(self, painel, seletor):
        self.painel = painel
        self.seletor = seletor

    @property
    def first(self):
        return self

    async def count(self):
        valor = self.painel.estado[self.seletor]
        return valor[0] if isinstance(valor, tuple) else valor

    async def is_visible(self):
        return self.painel.estado[sel.SELETOR_MIDIA][1]

    async def get_attribute(self, nome):
        assert nome == sel.ATRIBUTO_ANEXAR
        return self.painel.expandido


async def test_painel_aberto_com_acervo_VAZIO_esta_pronto():
    # ESTE E O TESTE QUE REPRODUZ A REGRESSAO. Projeto novo: nenhuma midia no acervo,
    # e o painel montado e perfeitamente utilizavel — e onde o upload vai entrar.
    painel = _PainelFalso(expandido=sel.ANEXAR_ABERTO, dialogos=1, visivel=True,
                          opcoes=0, abas=7)
    assert await TransportePlaywright()._esperar_seletor_midia(
        painel, teto_ms=2000) is True


async def test_painel_aberto_com_acervo_cheio_continua_pronto():
    painel = _PainelFalso(expandido=sel.ANEXAR_ABERTO, dialogos=1, visivel=True,
                          opcoes=13, abas=7)
    assert await TransportePlaywright()._esperar_seletor_midia(
        painel, teto_ms=2000) is True


async def test_painel_saindo_de_cena_NAO_esta_pronto():
    # o conserto original nao pode regredir: o painel que sai pela animacao continua
    # VISIVEL, e foi ele que fez uma espera rodar 180 s com a lista em zero. O `+` ja
    # declara fechado nesse instante, e e isso que o exclui.
    painel = _PainelFalso(expandido=sel.ANEXAR_FECHADO, dialogos=1, visivel=True,
                          opcoes=13, abas=7)
    assert await TransportePlaywright()._esperar_seletor_midia(
        painel, teto_ms=750) is False


async def test_dialogo_declarado_aberto_mas_ainda_sem_conteudo_nao_esta_pronto():
    # o `aria-expanded` troca antes de o painel montar; sem a terceira condicao a
    # sequencia seguiria contra um dialogo vazio
    painel = _PainelFalso(expandido=sel.ANEXAR_ABERTO, dialogos=1, visivel=True,
                          opcoes=0, abas=0)
    assert await TransportePlaywright()._esperar_seletor_midia(
        painel, teto_ms=750) is False


async def test_sem_dialogo_nenhum_nao_esta_pronto():
    painel = _PainelFalso(expandido=sel.ANEXAR_FECHADO, dialogos=0, visivel=False,
                          opcoes=0, abas=0)
    assert await TransportePlaywright()._esperar_seletor_midia(
        painel, teto_ms=750) is False


# --- o aviso de credito zerado nao decide; o card decide -------------------


class _PaginaDoEnvio:
    """A grade logo depois de enviar o comando.

    `aviso_na_volta` = quando o banner "Seus creditos acabaram" entra na tela.
    `card_na_volta`  = quando o card novo aparece (None = nunca aparece).
    """

    def __init__(self, aviso_na_volta=None, card_na_volta=None, antes=3):
        self.aviso_na_volta = aviso_na_volta
        self.card_na_volta = card_na_volta
        self.antes = antes
        self.voltas = 0
        self.clicados = []

    def quantos(self, seletor):
        if seletor == sel.CARD_GRADE:
            chegou = (self.card_na_volta is not None
                      and self.voltas >= self.card_na_volta)
            return self.antes + 1 if chegou else self.antes
        if seletor == sel.AVISO_SEM_CREDITO:
            return 1 if (self.aviso_na_volta is not None
                         and self.voltas >= self.aviso_na_volta) else 0
        return 0

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    async def wait_for_timeout(self, ms):
        self.voltas += 1


async def test_ultimo_credito_gera_o_video_e_o_aviso_nao_pode_recusar():
    # RELATADO pelo usuario: "o video e gerado com o ultimo credito da conta e o robo
    # nao esta pegando que o video foi gerado, esta deixando o trabalho como pendente
    # falando que a conta esta sem saldo. Porem o video e gerado."
    #
    # O aviso aparece JUNTO com o aceite, porque ele diz "o saldo agora e zero" e nao
    # "recusei este pedido". A ordem antiga lia o aviso antes do card e concluia recusa
    # — o job voltava para a fila com o video gerado e pago.
    pagina = _PaginaDoEnvio(aviso_na_volta=0, card_na_volta=2)

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.antes, Diario(1))

    assert apareceu is True


async def test_sem_credito_de_verdade_nao_espera_o_teto_inteiro(monkeypatch):
    # com o aviso na tela e NENHUM card, insistir os 120 s seria desperdicio: o aviso
    # nao decide, mas encurta o prazo
    #
    # O VALOR do aviso morreu com a aplicacao antiga e ainda nao foi remedido; o que
    # este teste guarda e a REGRA, e por isso ele injeta um seletor valido. Com
    # `AVISO_SEM_CREDITO` em PENDENTE, `_sem_credito` responde "nao sei" e a graca
    # nunca comeca — que e o comportamento certo para um sinal nao medido, e nao o que
    # se quer exercitar aqui.
    monkeypatch.setattr(sel, "AVISO_SEM_CREDITO",
                        "text=/Seus cr[ee]ditos do Google Flow acabaram/")
    pagina = _PaginaDoEnvio(aviso_na_volta=0, card_na_volta=None)

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.antes, Diario(1), teto_ms=120_000, graca_do_aviso_ms=10_000)

    assert apareceu is False
    # a graca sao 10 s a 2 s por volta: bem antes das 60 voltas do teto cheio
    assert pagina.voltas <= 8


async def test_sem_aviso_o_card_continua_tendo_o_teto_cheio():
    # o caso comum: conta com saldo, card que demora. Encurtar aqui inventaria falha
    pagina = _PaginaDoEnvio(aviso_na_volta=None, card_na_volta=20)

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.antes, Diario(1))

    assert apareceu is True


async def test_nem_card_nem_aviso_devolve_falso_no_teto():
    # sem card e sem aviso a causa nao e saldo, e quem chama tem que poder dizer isso
    pagina = _PaginaDoEnvio(aviso_na_volta=None, card_na_volta=None)

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.antes, Diario(1), teto_ms=6_000)

    assert apareceu is False


# --- card que parou de progredir: falha SEM depender de frase -----------------


class _CardDaGrade:
    """Um card da grade, com ou sem midia, com ou sem porcentagem, de um TIPO."""

    def __init__(self, midia=False, texto="", tipo=None, falha=False):
        self.midia = midia
        self.texto = texto
        # "imagem" | "video" | None. None e o card que nao casa nenhum dos dois
        # elementos de tipo — falha, ou tile ainda nascendo.
        self.tipo = tipo
        self.falha = falha

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    def quantos(self, seletor):
        if seletor == sel.CARD_MIDIA:
            return 1 if self.midia else 0
        if seletor == sel.CARD_VIDEO:
            return 1 if self.tipo == "video" else 0
        if seletor == sel.CARD_IMAGEM:
            return 1 if self.tipo == "imagem" else 0
        if seletor in (sel.CARD_FALHA, sel.CARD_FALHA_TITULO):
            return 1 if self.falha else 0
        return 0

    async def inner_text(self, timeout=None):
        return self.texto


class _PaginaDaGrade:
    def __init__(self, card):
        self.card = card
        self.clicados = []

    def locator(self, seletor):
        return _ListaDeCards(self.card)


class _ListaDeCards:
    def __init__(self, card):
        self.card = card

    async def count(self):
        return 1

    def nth(self, i):
        return self.card


class _SessaoFalsa:
    def __init__(self, card):
        self.conta = type("C", (), {"id": 1, "email": "a@b.com"})()
        self.handle = (None, None, _PaginaDaGrade(card))


async def _consultar(transporte, card):
    return await transporte.consultar(_SessaoFalsa(card), "card-0")


async def test_card_com_porcentagem_esta_rodando():
    # MEDIDO 2026-09-05 numa geracao real: o card em andamento traz a porcentagem no
    # proprio texto — ['image', '20%', <prompt>] — e ela some quando a midia entra
    t = TransportePlaywright()
    assert await _consultar(t, _CardDaGrade(texto="image 20% um gato")) is Estado.RODANDO
    assert await _consultar(t, _CardDaGrade(texto="image 86% um gato")) is Estado.RODANDO


async def test_card_com_midia_esta_pronto():
    t = TransportePlaywright()
    assert await _consultar(t, _CardDaGrade(midia=True, texto="um gato")) is Estado.PRONTO


async def test_card_sem_progresso_so_falha_depois_do_prazo_em_segundos():
    """Paciencia antes de desistir, POR TEMPO: card recem-criado passa instantes sem
    sinal, e o prazo tem que ser o mesmo em qualquer ritmo de leitura.

    Era por numero de leituras (15). Quando o polling caiu de 5 s para 2 s, o prazo
    caiu de 75 s para 30 s sem ninguem decidir, e toda imagem lenta virou "falhou"
    aos 36 s (jobs 145-158, 2026-09-06).
    """
    t = TransportePlaywright()
    agora = [1000.0]
    t._relogio = lambda: agora[0]
    parado = _CardDaGrade(midia=False, texto="um gato")
    assert await _consultar(t, parado) is Estado.RODANDO          # marca o inicio
    agora[0] += analise.SEM_PROGRESSO_S - 1
    assert await _consultar(t, parado) is Estado.RODANDO
    agora[0] += 1
    assert await _consultar(t, parado) is Estado.FALHOU


async def test_progresso_no_meio_zera_a_paciencia():
    """Uma porcentagem que aparece depois de leituras vazias RECOMECA o relogio.

    Sem isso, um card que demora a comecar acumularia tempo vazio e seria dado por
    falho ja gerando — falha inventada com credito gasto.
    """
    t = TransportePlaywright()
    agora = [1000.0]
    t._relogio = lambda: agora[0]
    vazio = _CardDaGrade(midia=False, texto="um gato")
    andando = _CardDaGrade(midia=False, texto="image 12% um gato")
    await _consultar(t, vazio)
    agora[0] += analise.SEM_PROGRESSO_S - 1
    assert await _consultar(t, andando) is Estado.RODANDO
    # o relogio zerou: precisa do prazo inteiro de novo
    assert await _consultar(t, vazio) is Estado.RODANDO
    agora[0] += analise.SEM_PROGRESSO_S - 1
    assert await _consultar(t, vazio) is Estado.RODANDO
    agora[0] += 1
    assert await _consultar(t, vazio) is Estado.FALHOU


# --- consultar NAO diz PRONTO para um card que nao e deste job -----------------


def _com_modo(modo, card):
    """Um transporte que ja sabe o modo daquele card, como depois de `gerar`."""
    t = TransportePlaywright()
    t._modo_do_card[(1, "card-0")] = modo
    return t, card


async def test_consultar_recusa_o_tile_de_imagem_num_job_de_video():
    """⚠️ O DEFEITO QUE ESTE TESTE TRANCA — jobs 1114-1116 e 1120-1128, 2026-09-13.

    Depois de a geracao sumir da grade, `card-0` (que e POSICAO) passou a apontar para
    a imagem de referencia enviada. `consultar` exigia apenas um `<img>` dentro do
    tile, achou, disse PRONTO, e so o menu do card — la no `baixar`, depois de abrir
    navegador — descobriu que era imagem. O tipo esta no ELEMENTO do tile e da para
    ler durante o polling.
    """
    t, card = _com_modo(Modo.INGREDIENTS2V,
                        _CardDaGrade(midia=True, texto="um navio", tipo="imagem"))
    with pytest.raises(CardTrocado, match="nao e o resultado deste job"):
        await _consultar(t, card)


async def test_consultar_recusa_o_tile_de_video_num_job_de_imagem():
    t, card = _com_modo(Modo.IMAGEM,
                        _CardDaGrade(midia=True, texto="um gato", tipo="video"))
    with pytest.raises(CardTrocado):
        await _consultar(t, card)


async def test_consultar_aceita_o_tile_do_tipo_certo():
    t, card = _com_modo(Modo.INGREDIENTS2V,
                        _CardDaGrade(midia=True, texto="um navio", tipo="video"))
    assert await _consultar(t, card) is Estado.PRONTO


async def test_consultar_nao_recusa_quando_o_tile_nao_diz_o_tipo():
    """"Nao sei" nao pode virar CardTrocado: seria matar geracao boa por um elemento
    que o Google renomeou."""
    t, card = _com_modo(Modo.INGREDIENTS2V,
                        _CardDaGrade(midia=True, texto="um navio", tipo=None))
    assert await _consultar(t, card) is Estado.PRONTO


async def test_consultar_sem_modo_lembrado_nao_recusa_nada():
    """Depois de um restart do servico `_modo_do_card` esta vazio. Sem o que comparar,
    a conferencia se cala — quem decide entao e o menu, no `baixar`."""
    t = TransportePlaywright()
    card = _CardDaGrade(midia=True, texto="um navio", tipo="imagem")
    assert await _consultar(t, card) is Estado.PRONTO


async def test_video_ainda_gerando_nao_e_recusado_pelo_tipo():
    """A conferencia mora no ponto em que se diria PRONTO, e nao na entrada.

    Card em andamento pode nao ter elemento de tipo nenhum, ou ter uma miniatura
    borrada; recusar ali transformaria "esta gerando" em "card trocado" e mataria o
    job com o video a caminho.
    """
    t, card = _com_modo(Modo.INGREDIENTS2V,
                        _CardDaGrade(midia=True, texto="8% um navio", tipo="imagem"))
    assert await _consultar(t, card) is Estado.RODANDO


async def test_o_relogio_do_card_anterior_nao_condena_o_proximo():
    """⚠️ MEDIDO NO JOB 1133, conta 1, 2026-09-13 — as duas linhas no MESMO segundo:

        [  24s] conta 1: o Flow aceitou: card-0
        [  24s] conta 1: card-0: 113s sem midia e sem porcentagem — o card parou de
                progredir.

    `_parado` e indexado por `(conta, id_remoto)`, e `id_remoto` e a string `card-0`
    tambem na geracao SEGUINTE. O ramo `grade.falhou` devolvia FALHOU sem soltar a
    chave, entao o cronometro da tentativa anterior continuava correndo e matava um
    card com zero segundo de vida — sem nem dar chance de ele sair da fila.
    """
    t = TransportePlaywright()
    agora = [1000.0]
    t._relogio = lambda: agora[0]
    vazio = _CardDaGrade(midia=False, texto="um navio")

    assert await _consultar(t, vazio) is Estado.RODANDO       # marca o inicio
    agora[0] += analise.SEM_PROGRESSO_S
    assert await _consultar(t, vazio) is Estado.FALHOU         # desiste desta geracao

    # o Flow aceita uma geracao NOVA, que reusa a mesma posicao `card-0`
    t.esquecer_card(1, "card-0")
    assert await _consultar(t, vazio) is Estado.RODANDO, "o card novo nasce com prazo"
    agora[0] += analise.SEM_PROGRESSO_S - 1
    assert await _consultar(t, vazio) is Estado.RODANDO, "prazo inteiro, nao o resto"


async def test_card_de_falha_tambem_solta_o_cronometro():
    """O outro caminho de FALHOU — o card de falha na tela — soltava tudo menos o
    cronometro, e era essa assimetria que vazava para a geracao seguinte."""
    t = TransportePlaywright()
    agora = [1000.0]
    t._relogio = lambda: agora[0]
    vazio = _CardDaGrade(midia=False, texto="um navio")
    await _consultar(t, vazio)                                 # marca o inicio
    agora[0] += 50

    falho = _CardDaGrade(midia=False, texto="Falha", falha=True)
    assert await _consultar(t, falho) is Estado.FALHOU
    assert (1, "card-0") not in t._parado


async def test_video_a_8_por_cento_com_miniatura_borrada_esta_rodando():
    # MEDIDO por print em 2026-09-06 (job 189): o card de video em andamento tem
    # <img> borrada E "8%". Olhar a midia antes da porcentagem dava PRONTO.
    t = TransportePlaywright()
    assert await _consultar(
        t, _CardDaGrade(midia=True, texto="8% The scene moves")) is Estado.RODANDO


# --- saldo curto se reconhece ANTES do clique --------------------------------


# --- seletor PENDENTE nunca chega ao Playwright -------------------------------


class _EscopoQueRecusaPendente:
    """Um escopo que estoura com `PENDENTE` do jeito que o Playwright estoura.

    MEDIDO em producao 2026-09-09, nos jobs 583-588: `'...'` chega ao Playwright, que
    o le como XPath e recusa com `SyntaxError: The string '...' is not a valid XPath
    expression`. O erro fala de sintaxe quando o problema e que ninguem mediu o
    seletor — e derrubou `consultar` no meio do polling das seis geracoes.
    """

    def __init__(self, marcas=(), texto="Falha"):
        self.marcas = set(marcas)
        self.texto = texto
        self.vistos = []

    def locator(self, seletor):
        if not seletor or seletor == sel.PENDENTE:
            raise SyntaxError(
                f"The string {seletor!r} is not a valid XPath expression")
        self.vistos.append(seletor)
        return _LocatorContado(self, seletor)

    def quantos(self, seletor):
        return 1 if seletor in self.marcas else 0

    async def inner_text(self, timeout=None):
        return self.texto


async def test_quantos_responde_zero_para_seletor_pendente():
    """PENDENTE vale "nao sei", e "nao sei" se conta como ZERO candidatos.

    O contrato ja era esse em `grade.falhou` e `grade.sem_credito`, cada uma com a
    guarda escrita a mao. Onde a guarda faltou, o seletor foi para o Playwright.
    """
    escopo = _EscopoQueRecusaPendente(marcas={sel.CARD_MIDIA})

    assert await acoes.quantos(escopo, sel.PENDENTE) == 0
    assert await acoes.quantos(escopo, "") == 0
    # e o seletor medido continua sendo perguntado de verdade
    assert await acoes.quantos(escopo, sel.CARD_MIDIA) == 1
    assert escopo.vistos == [sel.CARD_MIDIA]


class _PaginaQueRecusaPendente(_EscopoQueRecusaPendente):
    """A pagina do projeto: `locator` devolve a lista de cards, menos nos avisos."""

    def __init__(self, card, marcas=()):
        super().__init__(marcas=marcas)
        self.card = card

    def locator(self, seletor):
        if not seletor or seletor == sel.PENDENTE:
            raise SyntaxError(
                f"The string {seletor!r} is not a valid XPath expression")
        self.vistos.append(seletor)
        if seletor in self.marcas:
            return _LocatorContado(self, seletor)
        return _ListaDeCards(self.card)


async def test_card_de_falha_nao_manda_o_botao_repetir_pendente_ao_playwright():
    """O card falhou, `BOTAO_REPETIR_CARD` esta PENDENTE: devolve FALHOU, nao estoura.

    ⚠️ ESTE E O DEFEITO DOS JOBS 583-588 (2026-09-09). O Flow recusou as seis geracoes
    com "Notamos uma atividade incomum / Nao houve cobranca"; `por_limite_de_uso` nao
    reconheceu a frase, o codigo caiu no ramo do repetir e perguntou ao Playwright por
    `'...'`. `consultar` morreu com um erro de XPath, o job voltou a fila com
    `id_remoto` preenchido — e as tentativas seguintes NUNCA regeraram, so ficaram
    tentando baixar o card-0, que a essa altura era uma imagem de referencia.
    """
    card = _EscopoQueRecusaPendente(marcas={sel.CARD_FALHA_TITULO})
    pagina = _PaginaQueRecusaPendente(card)
    sessao = Sessao(conta=Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil=Path(".")),
                    handle=(None, None, pagina))

    assert await TransportePlaywright().consultar(sessao, "card-0") is Estado.FALHOU


async def test_consultar_troca_de_conta_no_card_de_atividade_incomum():
    """O caminho inteiro: card de atividade incomum -> `LimiteDeUso`.

    E `LimiteDeUso` porque e ele que o orquestrador ja trata do jeito certo — estorna,
    poe a conta de molho, SOLTA `id_remoto` e `conta_id` e devolve o job a fila sem
    gastar tentativa. Foi exatamente o que faltou nos jobs 583-588: sem soltar o
    `id_remoto`, as tentativas seguintes vieram como retomada e nunca regeraram.
    """
    card = _EscopoQueRecusaPendente(
        marcas={sel.CARD_FALHA_TITULO, sel.CARD_ATIVIDADE_INCOMUM, sel.CARD_SEM_COBRANCA})
    pagina = _PaginaQueRecusaPendente(card)
    sessao = Sessao(conta=Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil=Path(".")),
                    handle=(None, None, pagina))

    with pytest.raises(LimiteDeUso, match="atividade incomum"):
        await TransportePlaywright().consultar(sessao, "card-0")


class _BarraSemEnviar:
    """A barra do prompt quando o custo passa do saldo.

    MEDIDO 2026-09-05 numa conta com ~36 creditos e o modelo de 100: o botao de
    enviar SOME e um `info` toma o lugar dele.
    """

    def __init__(self, tem_info=True):
        self.tem_info = tem_info

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    def quantos(self, seletor):
        if seletor == sel.BOTAO_ENVIAR:
            return 0
        if seletor == sel.BOTAO_INFO_BARRA:
            return 1 if self.tem_info else 0
        return 0


async def test_sem_botao_de_enviar_e_saldo_insuficiente():
    """Sem botao, NADA foi enviado — e essa e a diferenca que importa.

    A aplicacao antiga so avisava depois do clique, e foi por ler aquele aviso antes
    do card que um job voltou para a fila com o video ja gerado e pago. Reconhecer
    antes do clique deixa a reserva estornavel.
    """
    t = TransportePlaywright()
    t._ultimo_custo = 100
    with pytest.raises(SaldoInsuficiente) as erro:
        await t._exigir_botao_de_enviar(_BarraSemEnviar(), "a@b.com")
    assert "100" in str(erro.value)
    assert "info" in str(erro.value)


async def test_com_botao_de_enviar_segue_em_frente():
    class _Ok:
        def locator(self, seletor):
            return _LocatorContado(self, seletor)

        def quantos(self, seletor):
            return 1 if seletor == sel.BOTAO_ENVIAR else 0

    await TransportePlaywright()._exigir_botao_de_enviar(_Ok(), "a@b.com")


async def test_sem_o_mais_na_tela_valem_as_outras_condicoes(monkeypatch):
    """Em modo FRAMES o `+` nao existe, e ler `aria-expanded` dele estoura.

    MEDIDO 2026-09-05: a barra vira `flow-ingredient-bar` com os slots inicio/fim, e
    `flow-add-menu` fica sem botao clicavel. `frames2v` morria com
        TimeoutError: waiting for locator("flow-add-menu button")
    — erro que fala do `+` quando o fato e que aquele modo nao tem `+`.

    Sem ele, sobram o overlay visivel e as abas montadas, que ja bastam.
    """
    pronto = _PainelFalso(expandido=None, dialogos=1, visivel=True, opcoes=0, abas=7,
                          tem_mais=0)
    assert await TransportePlaywright()._esperar_seletor_midia(pronto, 1500) is True

    # e o painel AUSENTE continua reprovando, com ou sem `+`
    vazio = _PainelFalso(expandido=None, dialogos=0, visivel=False, opcoes=0, abas=0,
                         tem_mais=0)
    assert await TransportePlaywright()._esperar_seletor_midia(vazio, 1000) is False


# ---- o estado do card e uma decisao pura, e a ordem dos sinais e a decisao ----

def test_card_com_porcentagem_esta_rodando_mesmo_com_miniatura():
    # MEDIDO 2026-09-06 (job 189): video a 8% com <img> borrada no card. Olhar a midia
    # antes da porcentagem dava "pronto" e 22 downloads de nada.
    assert analise.estado_do_card(False, True, True, None) == "rodando"


def test_card_com_midia_e_sem_porcentagem_esta_pronto():
    assert analise.estado_do_card(False, False, True, 0.0) == "pronto"


def test_card_de_falha_e_falha_antes_de_qualquer_outro_sinal():
    assert analise.estado_do_card(True, True, True, None) == "falhou"


def test_card_sem_sinal_espera_por_tempo_e_nao_por_leituras():
    assert analise.estado_do_card(False, False, False, None) == "rodando"
    assert analise.estado_do_card(False, False, False, 35.0) == "rodando"   # 15 leituras de 2 s
    assert analise.estado_do_card(False, False, False, analise.SEM_PROGRESSO_S - 1) == "rodando"
    assert analise.estado_do_card(False, False, False, analise.SEM_PROGRESSO_S) == "falhou"


def test_card_na_fila_nao_morre_por_mais_tempo_que_passe():
    """⚠️ MEDIDO no log de passo, 2026-09-13 — o texto do card que foi dado por morto:

        card-0: 91s sem midia e sem porcentagem — o card parou de progredir.
        Texto: 'movie
na fila
the large container ship glides steadily...'

    `na fila` e o Flow dizendo que a geracao existe e ainda NAO COMECOU. Card
    enfileirado nao tem porcentagem nem midia — os dois unicos sinais que esta funcao
    conhecia —, entao a paciencia acabava enquanto o video esperava a vez. O usuario
    abria a conta, via o video sendo gerado, e o painel dizia que tinha falhado.
    """
    assert analise.estado_do_card(False, False, False, 10_000.0, na_fila=True) == "na_fila"


def test_a_fila_e_veredito_proprio_e_nao_apelido_de_rodando():
    """Os dois esperam igual, e e por isso que davam na mesma no comeco. Mas so um
    deles diz "o Google ainda nem comecou", e sem essa distincao a demora do Flow
    chegava ao painel como demora do FlowPool."""
    assert analise.estado_do_card(False, True, False, None, na_fila=True) == "rodando"
    assert analise.estado_do_card(False, False, False, 1.0) == "rodando"
    assert analise.estado_do_card(False, False, False, 1.0, na_fila=True) == "na_fila"


def test_a_fila_nao_apaga_uma_falha_nem_uma_midia_pronta():
    """A ordem dos sinais nao muda: falha decide primeiro, e midia continua sendo
    PRONTO. `na_fila` so responde por quem nao tem nenhum dos outros."""
    assert analise.estado_do_card(True, False, False, None, na_fila=True) == "falhou"
    assert analise.estado_do_card(False, False, True, 0.0, na_fila=True) == "pronto"


# --- teto de 24 tiles: a contagem satura e deixa de ser sinal -----------------


class _PaginaNoTeto:
    """A grade CRAVADA no teto de 24 tiles, com o card novo entrando na frente.

    Reproduz o defeito medido em 2026-09-09: a grade do Flow para de renderizar em
    24, entao `count()` nunca fica maior que `antes` e a comparacao antiga concluia
    "o Flow nao criou card novo" com a imagem gerada e o credito gasto. Foram 161
    falhas assim — zero ate o job ~200 e 49 de 50 na faixa 300-349.
    """

    TETO = 24

    def __init__(self, card_na_volta=2, com_midia=True):
        self.card_na_volta = card_na_volta
        self.com_midia = com_midia
        self.voltas = 0
        # identidades estaveis: o `src` da miniatura de cada tile
        self.base = [f"src-antigo-{i}" for i in range(self.TETO)]

    def _chegou(self):
        return self.voltas >= self.card_na_volta

    def quantos(self, seletor):
        # ⚠️ O PONTO DO TESTE: a contagem NAO cresce, nem depois do card entrar.
        if seletor == sel.CARD_GRADE:
            return self.TETO
        return 0

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    async def evaluate(self, script, seletor=None):
        if not self._chegou():
            return list(self.base)
        novo = "src-do-card-novo" if self.com_midia else "sem-midia:"
        # card novo entra na frente e o mais velho sai da renderizacao
        return [novo, *self.base[:-1]]

    async def wait_for_timeout(self, ms):
        self.voltas += 1


async def test_grade_no_teto_de_24_ainda_detecta_o_card_novo():
    # sem isto o job vira falha com a imagem gerada — o defeito de 2026-09-09
    pagina = _PaginaNoTeto(card_na_volta=2)

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.TETO, Diario(1), ids_antes=list(pagina.base))

    assert apareceu is True


async def test_no_teto_card_novo_sem_miniatura_e_pego_pela_posicao():
    # card recem-criado pode entrar antes de ter miniatura: nenhuma identidade
    # inedita aparece, mas o PRIMEIRO tile deixa de ser o que era
    pagina = _PaginaNoTeto(card_na_volta=2, com_midia=False)

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.TETO, Diario(1), ids_antes=list(pagina.base))

    assert apareceu is True


async def test_no_teto_sem_card_nenhum_continua_devolvendo_falso():
    # a correcao nao pode inventar sucesso: grade parada de verdade segue False
    pagina = _PaginaNoTeto(card_na_volta=None)
    pagina.card_na_volta = 10**9

    apareceu = await TransportePlaywright()._esperar_card_novo(
        pagina, pagina.TETO, Diario(1), teto_ms=6_000, ids_antes=list(pagina.base))

    assert apareceu is False


# --- o card e achado por IDENTIDADE, nao por posicao --------------------------


class _TileDaGrade:
    """O tile devolvido por `nth`; guarda o indice para o teste conferir."""

    def __init__(self, indice):
        self.indice = indice


class _ListaDeTiles:
    def __init__(self, quantos):
        self._quantos = quantos

    async def count(self):
        return self._quantos

    def nth(self, i):
        return _TileDaGrade(i)


class _GradeComTiles:
    """Uma grade com identidades conhecidas, para exercitar `_card`."""

    def __init__(self, ids):
        self.ids = list(ids)

    def quantos(self, seletor):
        return len(self.ids)

    def locator(self, seletor):
        return _ListaDeTiles(self.quantos(seletor))

    async def evaluate(self, script, seletor=None):
        return list(self.ids)


async def test_card_e_achado_pela_identidade_e_nao_pela_posicao():
    # MEDIDO nos jobs 170/174/189/195: o frames2v sobe dois quadros de referencia,
    # upload vira tile, e o `card-0` deixou de ser o video — o download abriu um card
    # de imagem e pediu "720p" num submenu que so tinha "1k".
    t = TransportePlaywright()
    t._identidade_do_card[(7, "card-0")] = "src-do-video"
    # o video escorregou para a posicao 2: dois uploads entraram na frente
    pagina = _GradeComTiles(["src-upload-a", "src-upload-b", "src-do-video"])

    card = await t._card(pagina, "card-0", 7)

    assert card.indice == 2


async def test_sem_identidade_guardada_o_card_continua_posicional():
    # o caminho antigo tem que seguir valendo: modo sem upload, grade pequena
    t = TransportePlaywright()
    pagina = _GradeComTiles(["a", "b", "c"])

    card = await t._card(pagina, "card-0", 7)

    assert card.indice == 0


async def test_identidade_que_sumiu_cai_na_posicao_e_nao_derruba_o_job():
    """⚠️ REGRESSAO MEDIDA (job 435, 2026-09-09): a versao anterior LEVANTAVA aqui.

    A identidade guardada some por um motivo banal e comum — o card ganhou a
    miniatura de verdade e o valor velho era o placeholder. Derrubar o job nesse
    caso mata uma geracao que esta pronta. A identidade e um atalho; quem protege
    contra baixar o card errado e a conferencia de TIPO em `_baixar`.
    """
    t = TransportePlaywright()
    t._identidade_do_card[(7, "card-0")] = "src-que-sumiu"
    pagina = _GradeComTiles(["outro-a", "outro-b"])

    card = await t._card(pagina, "card-0", 7)

    assert card.indice == 0
    # e esquece a identidade velha, que so produziria o mesmo desvio na proxima volta
    assert (7, "card-0") not in t._identidade_do_card


async def test_placeholder_nunca_vira_identidade_guardada():
    """`sem-midia:` e igual para TODO card novo e some quando a miniatura chega.

    Guardar isso foi o que quebrou o job 435: serve de sinal de que um card
    apareceu, nao de identidade para reencontra-lo depois.
    """
    pagina = _PaginaNoTeto(card_na_volta=2, com_midia=False)
    t = TransportePlaywright()

    apareceu = await t._esperar_card_novo(
        pagina, pagina.TETO, Diario(1), ids_antes=list(pagina.base))

    assert apareceu is True
    # E ESTA e a linha que o nome do teste promete: o `sem-midia:` serviu de sinal e
    # nao ficou guardado. Sem ela, o teste passava com o defeito do job 435 inteiro.
    assert t._identidade_nova is None


async def test_identidade_de_verdade_chega_inteira_em_quem_espera():
    """O par do teste acima: com miniatura, o `src` do card novo TEM que voltar.

    E por este valor que `_gerar` reencontra o card depois, quando a grade cravada no
    teto faz a posicao mentir.
    """
    pagina = _PaginaNoTeto(card_na_volta=2, com_midia=True)
    t = TransportePlaywright()

    apareceu = await t._esperar_card_novo(
        pagina, pagina.TETO, Diario(1), ids_antes=list(pagina.base))

    assert apareceu is True
    assert t._identidade_nova == "src-do-card-novo"


async def test_leitura_de_identidade_vazia_volta_para_a_posicao():
    # grade vazia na LEITURA e falha de leitura, nao card sumido: se o codigo
    # tratasse os dois igual, uma falha de `evaluate` derrubaria o job inteiro
    t = TransportePlaywright()
    t._identidade_do_card[(7, "card-0")] = "src-do-video"
    pagina = _GradeComTiles([])
    pagina.quantos = lambda seletor: 3 if seletor == sel.CARD_GRADE else 0

    card = await t._card(pagina, "card-0", 7)

    assert card.indice == 0


# --- o tipo do card vem da TELA, nao da memoria do processo ------------------


def test_tipo_do_card_le_o_menu():
    # MEDIDO em seletores.py: video tem "adicionar ao cenario"/"publicar no youtube";
    # no de imagem o lugar deles e ocupado por "animar"
    assert analise.tipo_do_card(
        ["favorite / favorito", "motion_blur / animar",
         "download / fazer o download"]) == "imagem"
    assert analise.tipo_do_card(
        ["play_movies / adicionar ao cenario",
         "video_library / publicar no youtube"]) == "video"


def test_tipo_do_card_sem_marca_responde_nao_sei():
    # "nao sei" tem que ser dizivel: um menu que mudou de redacao viraria palpite de
    # tipo, e tipo errado baixa o arquivo errado EM SILENCIO
    assert analise.tipo_do_card(["favorite / favorito",
                                 "download / fazer o download"]) is None


def test_tipo_do_card_ambiguo_tambem_e_nao_sei():
    # as duas marcas juntas nao deveriam existir; se existirem, chutar e pior
    assert analise.tipo_do_card(["motion_blur / animar",
                                 "play_movies / adicionar ao cenario"]) is None


# --- consultar fotografa a tela quando falha ---------------------------------


class _PaginaViva:
    def is_closed(self):
        return False


class _ContaFake:
    id = 1
    email = "quem@exemplo.com"


class _SessaoFake:
    """O minimo que o envelope de `consultar` toca: id da conta e a pagina."""

    conta = _ContaFake()
    handle = (None, None, _PaginaViva())
    falha = None


def _sessao_real() -> Sessao:
    """`Sessao` de verdade com pagina falsa dentro.

    `_SessaoFake` acima serve ao que veio antes, mas cada chamada com ela rende um erro
    de tipo no pyright do repositorio inteiro — e a catraca do CLAUDE.md diz que aquele
    numero desce, nunca sobe. Montar o envelope real custa uma linha e nao custa nada ao
    teste: o que se exercita e o print, nao a conta.
    """
    conta = Conta(id=1, email="quem@exemplo.com", tier=Tier.FREE, perfil="p/1")
    return Sessao(conta=conta, handle=(None, None, _PaginaViva()))


async def _sem_traco(s):
    return None



async def test_consultar_tira_print_quando_falha():
    """⚠️ RELATADO 2026-09-09: um job falhou em `consultar` e o diagnostico local
    respondeu "nao ha print da tela" — e chutou que os seletores do Flow tinham
    mudado, quando estavam certos. `gerar` e `baixar` ja fotografavam; `consultar`,
    que roda a cada volta do polling, era o unico cego."""
    t = TransportePlaywright()
    tirou = []

    async def _print(pagina, motivo):
        tirou.append(motivo)
        return None

    async def _explode(s, id_remoto):
        raise GeracaoFalhou("qualquer falha de leitura da grade")

    t.print_de_falha = _print
    t._consultar = _explode
    t._traco_da_falha = _sem_traco
    t._anotar_falha = lambda *a, **k: None

    with pytest.raises(GeracaoFalhou):
        await t.consultar(_SessaoFake(), "card-0")

    assert tirou and "consultar" in tirou[0]


@pytest.mark.parametrize(
    ("rotulo", "privado", "chamar"),
    [
        ("saldo", "_saldo", lambda t, s: t.saldo(s)),
        ("listar projetos", "_listar_projetos", lambda t, s: t.listar_projetos(s)),
        ("criar projeto", "_criar_projeto", lambda t, s: t.criar_projeto(s, "P")),
        ("abrir projeto", "_abrir_projeto", lambda t, s: t.abrir_projeto(s, "uuid-1")),
    ],
)
async def test_os_passos_de_navegacao_tiram_print_quando_falham(rotulo, privado, chamar):
    """⚠️ O BURACO QUE A JANELA OCULTA TRANSFORMOU EM BLOQUEIO.

    Auditoria de 2026-09-10: dos publicos, so `gerar`, `consultar` e `baixar`
    fotografavam. O job 619 morreu em `abrir_projeto` ("o projeto nao abriu") sem
    nenhuma tela guardada — e com `FLOWPOOL_JANELA=oculta` nao ha nem a janela para
    olhar depois.
    """
    transporte = TransportePlaywright()
    tirou: list[str] = []

    async def _print(pagina, motivo):
        tirou.append(motivo)
        return None

    async def _explode(*a, **k):
        raise GeracaoFalhou("o Flow nao respondeu")

    transporte.print_de_falha = _print
    setattr(transporte, privado, _explode)
    transporte._anotar_falha = lambda *a, **k: None

    with pytest.raises(GeracaoFalhou):
        await chamar(transporte, _sessao_real())

    assert tirou, f"{rotulo} falhou sem print"
    assert rotulo in tirou[0], f"o print de {rotulo} foi batizado de {tirou[0]!r}"


async def test_navegacao_tambem_nao_fotografa_troca_de_conta():
    """A guarda que `consultar` ja tinha, valendo para os passos novos.

    Print a cada troca de conta seria disco sujo num lote grande, e ninguem ligaria
    o disco cheio a esta linha.
    """
    t = TransportePlaywright()
    tirou = []

    async def _print(pagina, motivo):
        tirou.append(motivo)
        return None

    async def _limite(*a, **k):
        raise LimiteDeUso("conta no limite", motivo="limite_de_uso")

    t.print_de_falha = _print
    t._saldo = _limite
    t._anotar_falha = lambda *a, **k: None

    with pytest.raises(LimiteDeUso):
        await t.saldo(_sessao_real())

    assert tirou == []


async def test_consultar_nao_fotografa_troca_de_conta():
    """Limite de uso e saldo nao sao falha de tela: sao roteamento de conta. Print
    ali seria lixo em disco a cada troca, e ha muitas."""
    t = TransportePlaywright()
    tirou = []

    async def _print(pagina, motivo):
        tirou.append(motivo)
        return None

    async def _limite(s, id_remoto):
        raise LimiteDeUso("conta no limite")

    t.print_de_falha = _print
    t._consultar = _limite
    t._traco_da_falha = _sem_traco

    with pytest.raises(LimiteDeUso):
        await t.consultar(_SessaoFake(), "card-0")

    assert tirou == []


# --- navegador aberto que ninguem fecha: a janela orfa ------------------------


class _CtxDaAbertura:
    def __init__(self, pagina):
        self.pages = [pagina]
        self.fechado = False

    async def close(self):
        self.fechado = True

    async def new_page(self):
        return self.pages[0]


class _PwDaAbertura:
    def __init__(self):
        self.parado = False

    async def stop(self):
        self.parado = True


class _PaginaQueNaoNavega:
    url = "about:blank"

    def __init__(self, erro):
        self.erro = erro

    async def goto(self, *a, **kw):
        raise self.erro

    def is_closed(self):
        return False


def _conta_pw():
    return Conta(id=1, email="a@b.com", tier=Tier.FREE, perfil="p/1")


async def test_navegador_fecha_quando_a_navegacao_falha(monkeypatch, tmp_path):
    """⚠️ A JANELA ORFA, E ELA TRAVA A CONTA INTEIRA — MEDIDO no banco, 2026-09-10.

    `abrir_contexto` sobe o Chrome; so DEPOIS vem o `goto`. O `try` daqui comecava
    abaixo dele, entao um `Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED` (proxy fora
    do ar) levantava com o navegador DE PE e sem ninguem segurando a referencia: ele
    nao esta em `_guardadas`, entao `_fechar_ociosas` nunca o ve, e `encerrar_todas`
    tampouco. A janela fica na tela para sempre.

    E o estrago nao para na janela. Ela segura o `--user-data-dir`, e todo job
    seguinte daquela conta morre em `launch_persistent_context: Target page, context
    or browser has been closed` — que foi exatamente a cascata das contas 4, 6 e 7 em
    2026-09-10 18:26-18:28.

    `validar_perfil`, a irma desta funcao, sempre teve o `finally` certo.
    """
    ctx = _CtxDaAbertura(_PaginaQueNaoNavega(RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED")))
    pw = _PwDaAbertura()

    async def _abrir(perfil, proxy=None):
        return pw, ctx

    monkeypatch.setattr(
        "flowpool.transporte.pw.transporte.abrir_contexto", _abrir)

    with pytest.raises(RuntimeError, match="TUNNEL"):
        await TransportePlaywright().abrir_sessao(_conta_pw())

    assert ctx.fechado, "o Chrome ficaria aberto segurando o perfil da conta"
    assert pw.parado, "e o driver do Playwright junto"
