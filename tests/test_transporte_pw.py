"""As partes do transporte real que dao para testar sem rede.

O transporte inteiro toca o Google e so se verifica rodando (`fumaca.py`). Mas cada
decisao PURA dentro dele ja custou alguma coisa quando morava solta no meio de uma
corrotina, e e essa parte que esta aqui.
"""

from pathlib import Path

import pytest

from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.analise import (
    caminho_final, escolher_opcao, exigir_proporcao_do_modo, exigir_unico,
    faltando_no_chip, id_do_href, saldo_do_texto,
)
from flowpool.transporte.pw.transporte import TransportePlaywright

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
    assert escolher_opcao(MENU_VIDEO, sel.BAIXAR["720p"]) == 1


def test_rotulo_com_quebra_de_linha_casa_pela_primeira_linha():
    # passar "270p\nGIF animado" inteiro para um seletor CSS estoura o parser
    assert escolher_opcao(MENU_VIDEO, sel.BAIXAR["270p"]) == 0


def test_escolhe_o_tamanho_original_no_menu_de_imagem():
    assert escolher_opcao(MENU_IMAGEM, sel.BAIXAR["1k"]) == 0


def test_rotulo_com_ligadura_de_icone_na_frente_ainda_casa():
    # varios itens deste app trazem a ligadura como primeira linha ("download\nBaixar")
    assert escolher_opcao(["download\n1K\nTamanho original", "2K\nAumentada"],
                          sel.BAIXAR["1k"]) == 0


def test_resolucao_de_video_pedida_num_menu_de_imagem_e_erro():
    # o default seguro: pedir 720p num menu de imagem GRITA, em vez de baixar o item
    # errado em silencio
    with pytest.raises(GeracaoFalhou):
        escolher_opcao(MENU_IMAGEM, sel.BAIXAR["720p"])


def test_dois_itens_com_o_mesmo_rotulo_sao_erro():
    with pytest.raises(GeracaoFalhou, match="esperava 1 candidato"):
        escolher_opcao(["720p", "720p\nsegunda copia"], sel.BAIXAR["720p"])


def test_1080p_nao_e_confundido_com_1k():
    assert escolher_opcao(MENU_VIDEO, sel.BAIXAR["1080p"]) == 2


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


def test_chip_que_bate_com_o_pedido_nao_tem_pendencia():
    chip = "video · 720p · 8s crop_9_16 x1"
    assert faltando_no_chip(chip, "t2v", "9:16", 1, "8s") == []


def test_duracao_que_nao_pegou_e_apontada():
    # o caso real: pedidos 4s, saiu 8s, credito debitado e ninguem percebeu
    chip = "video · 720p · 8s crop_9_16 x1"
    assert faltando_no_chip(chip, "t2v", "9:16", 1, "4s") == ["4s"]


def test_proporcao_que_nao_pegou_e_apontada():
    chip = "video · 720p · 8s crop_16_9 x1"
    assert faltando_no_chip(chip, "t2v", "9:16", 1, "8s") == ["crop_9_16"]


def test_quantidade_que_nao_pegou_e_apontada():
    chip = "video · 720p · 8s crop_9_16 x1"
    assert faltando_no_chip(chip, "t2v", "9:16", 4, "8s") == ["x4"]


def test_modo_que_nao_trocou_e_apontado():
    chip = "video · 720p · 8s crop_9_16 x1"
    assert faltando_no_chip(chip, "imagem", "9:16", 1, None) == ["imagem"]


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
