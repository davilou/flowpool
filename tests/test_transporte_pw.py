"""As partes do transporte real que dao para testar sem rede.

O transporte inteiro toca o Google e so se verifica rodando (`fumaca.py`). Mas cada
decisao PURA dentro dele ja custou alguma coisa quando morava solta no meio de uma
corrotina, e e essa parte que esta aqui.
"""

from pathlib import Path

import pytest

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
from flowpool.transporte.base import Estado, GeracaoFalhou, Pedido, Sessao
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import (
    Diario, descartar_traco, guardar_traco, iniciar_traco, proximo_traco,
    traco_ligado,
)
from flowpool.transporte.pw.dom import seguro
from flowpool.transporte.pw.analise import (
    agente_ligado, arquivos_do_pedido, caminho_final, confirmacao_fraca,
    achar_no_submenu, apareceu_na_lista, custo_do_texto, escolher_item_enviado,
    escolher_opcao,
    etapas_ate_o_modo, exigir_agente_desligado, exigir_proporcao_do_modo,
    exigir_referencia_incluida, exigir_referencias_completas, exigir_selecao,
    exigir_unico, faltando_no_chip, id_da_midia, id_do_href, item_pronto,
    nome_do_item, saldo_do_texto,
)
from flowpool.transporte.pw.transporte import (
    MAX_REPETICOES, TransportePlaywright,
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
    desligou = await TransportePlaywright()._exigir_agente_desligado(
        _PaginaComAgente(botao))
    assert (desligou, botao.cliques, botao.pressed) == (
        True, 1, sel.AGENTE_DESLIGADO)


async def test_agente_desligado_nao_e_tocado():
    # clicar "por garantia" LIGARIA o agente — interruptor nao tem clique inofensivo
    botao = _BotaoAgenteFalso(sel.AGENTE_DESLIGADO)
    desligou = await TransportePlaywright()._exigir_agente_desligado(
        _PaginaComAgente(botao))
    assert (desligou, botao.cliques) == (False, 0)


async def test_clique_que_nao_pegou_derruba_a_geracao():
    botao = _BotaoAgenteFalso(sel.AGENTE_LIGADO, gruda=True)
    with pytest.raises(GeracaoFalhou, match="continuou LIGADO"):
        await TransportePlaywright()._exigir_agente_desligado(_PaginaComAgente(botao))


async def test_interruptor_ambiguo_ou_ausente_e_erro():
    # zero e "o interruptor sumiu da tela"; dois e ambiguidade. Nenhum se resolve
    # chutando: o vizinho deste botao na barra do prompt e o de ENVIAR.
    class _Sumido(_BotaoAgenteFalso):
        async def count(self):
            return 0

    botao = _Sumido(sel.AGENTE_DESLIGADO)
    with pytest.raises(GeracaoFalhou, match="esperava 1 candidato"):
        await TransportePlaywright()._exigir_agente_desligado(_PaginaComAgente(botao))
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
    t = TransportePlaywright()
    assert await t._card_falhou(_CardFalso({sel.CARD_FALHA})) is True
    assert await t._card_falhou(_CardFalso({sel.CARD_FALHA_TITULO})) is True
    assert await t._card_falhou(_CardFalso(set())) is False


async def test_card_normal_nao_e_falha():
    assert await TransportePlaywright()._card_falhou(_CardFalso(set())) is False


async def test_seletor_pendente_nao_vira_falha_nem_vai_para_o_playwright():
    """PENDENTE vale "...", e o Playwright le isso como XPath.

    MEDIDO 2026-09-05 numa geracao real: `consultar` estourou com
    `SyntaxError: The string '...' is not a valid XPath expression` — um erro que fala
    de sintaxe quando o fato e que ninguem mediu o card de falha na aplicacao nova.

    Sem medicao, a resposta certa e FALSE: "nao sei dizer que falhou". O custo e um
    card realmente falho parecer RODANDO ate o teto do polling; o custo do contrario
    seria dar por falha uma geracao que esta so demorando.
    """
    class _Explode:
        def locator(self, seletor):
            raise AssertionError(
                f"seletor pendente chegou ao Playwright: {seletor!r}")

    assert sel.CARD_FALHA == sel.PENDENTE          # enquanto nao for medido
    assert await TransportePlaywright()._card_falhou(_Explode()) is False


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

    def __init__(self, expandido, dialogos, visivel, opcoes, abas):
        self.estado = {
            sel.BOTAO_ANEXAR: expandido,
            sel.SELETOR_MIDIA: (dialogos, visivel),
            sel.ITEM_MIDIA: opcoes,
            sel.ABAS_SELETOR_MIDIA: abas,
        }
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
        return self.painel.estado[sel.BOTAO_ANEXAR]


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
    """Um card da grade, com ou sem midia e com ou sem porcentagem."""

    def __init__(self, midia=False, texto=""):
        self.midia = midia
        self.texto = texto

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    def quantos(self, seletor):
        return 1 if (self.midia and seletor == sel.CARD_MIDIA) else 0

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


async def test_card_sem_progresso_so_falha_depois_de_insistir():
    """Paciencia antes de desistir: card recem-criado passa instantes sem sinal.

    Declarar falha na primeira leitura mataria geracao boa, que e pior do que esperar.
    """
    from flowpool.transporte.pw.transporte import VOLTAS_SEM_PROGRESSO
    t = TransportePlaywright()
    parado = _CardDaGrade(midia=False, texto="um gato")
    for _ in range(VOLTAS_SEM_PROGRESSO - 1):
        assert await _consultar(t, parado) is Estado.RODANDO
    assert await _consultar(t, parado) is Estado.FALHOU


async def test_progresso_no_meio_zera_a_paciencia():
    """Uma porcentagem que aparece depois de leituras vazias RECOMECA a contagem.

    Sem isso, um card que demora a comecar acumularia leituras vazias e seria dado por
    falho ja gerando — falha inventada com credito gasto.
    """
    from flowpool.transporte.pw.transporte import VOLTAS_SEM_PROGRESSO
    t = TransportePlaywright()
    vazio = _CardDaGrade(midia=False, texto="um gato")
    andando = _CardDaGrade(midia=False, texto="image 12% um gato")
    for _ in range(VOLTAS_SEM_PROGRESSO - 1):
        await _consultar(t, vazio)
    assert await _consultar(t, andando) is Estado.RODANDO
    # a contagem zerou: agora precisa das voltas todas de novo
    for _ in range(VOLTAS_SEM_PROGRESSO - 1):
        assert await _consultar(t, vazio) is Estado.RODANDO
    assert await _consultar(t, vazio) is Estado.FALHOU


# --- saldo curto se reconhece ANTES do clique --------------------------------


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
    from flowpool.transporte.base import SaldoInsuficiente
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
