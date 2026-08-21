"""As partes do transporte real que dao para testar sem rede.

O transporte inteiro toca o Google e so se verifica rodando (`fumaca.py`). Mas cada
decisao PURA dentro dele ja custou alguma coisa quando morava solta no meio de uma
corrotina, e e essa parte que esta aqui.
"""

from pathlib import Path

import pytest

from flowpool.nucleo.tipos import Modelo, Modo
from flowpool.transporte.base import GeracaoFalhou, Pedido
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.dom import seguro
from flowpool.transporte.pw.analise import (
    arquivos_do_pedido, caminho_final, confirmacao_fraca, custo_do_texto,
    escolher_item_enviado, escolher_opcao, etapas_ate_o_modo,
    exigir_proporcao_do_modo, exigir_unico, faltando_no_chip, id_do_href,
    item_pronto, nome_do_item, saldo_do_texto,
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
        await TransportePlaywright()._anexar(None, _pedido(Modo.INGREDIENTS2V))


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


def _item(nome, selecionado=False, miniaturas=1, tipo="Imagem"):
    return {"texto": f"{nome}\n{tipo}", "selecionado": selecionado,
            "miniaturas": miniaturas}


def test_acha_o_item_recem_enviado_pelo_nome_do_arquivo():
    itens = [_item("blusa.png", selecionado=True), _item("Red ceramic mug on table")]
    assert escolher_item_enviado(itens, "blusa.png") == 0


def test_item_que_ainda_nao_apareceu_e_espere_mais_e_nao_erro():
    # `None` = "espere"; quem chama fica no laco ate o teto e so entao levanta
    assert escolher_item_enviado([_item("outro.png")], "blusa.png") is None


def test_item_que_apareceu_mas_ainda_sobe_e_espere_mais():
    assert escolher_item_enviado([_item("blusa.png", miniaturas=0)], "blusa.png") is None


def test_dois_arquivos_de_mesmo_nome_desempatam_pelo_selecionado():
    # MEDIDO: enviar o mesmo arquivo duas vezes deixa dois itens de mesmo nome, e o
    # Flow ja deixa o RECEM-ENVIADO selecionado. Recusar a ambiguidade aqui — como
    # `escolher_opcao` faz nos menus — travaria a segunda geracao de qualquer produto.
    itens = [_item("blusa.png"), _item("blusa.png", selecionado=True)]
    assert escolher_item_enviado(itens, "blusa.png") == 1


def test_sem_nenhum_selecionado_vale_o_primeiro_porque_a_lista_e_recentes():
    itens = [_item("blusa.png"), _item("blusa.png")]
    assert escolher_item_enviado(itens, "blusa.png") == 0


def test_o_selecionado_so_ganha_se_estiver_pronto():
    # um item selecionado mas ainda subindo nao pode passar na frente do que ja
    # terminou: incluir o de cima anexaria referencia vazia
    itens = [_item("blusa.png", selecionado=True, miniaturas=0), _item("blusa.png")]
    assert escolher_item_enviado(itens, "blusa.png") == 1


def test_nome_parecido_nao_casa():
    # `blusa.png` nao pode casar com `blusa-2.png`: o `in` solto anexaria o arquivo
    # errado sem nenhum sintoma
    assert escolher_item_enviado([_item("blusa-2.png")], "blusa.png") is None


def test_lista_vazia_e_espere_mais():
    assert escolher_item_enviado([], "blusa.png") is None


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
        # o item so fica PRONTO (com miniatura) depois de o aviso sair da tela
        if self.aviso_aceito:
            self.voltas_com_aviso_aceito += 1
        pronto = self.aviso_aceito and self.voltas_com_aviso_aceito > self.pronto_apos
        return [{"texto": "gato.png", "selecionado": False,
                 "miniaturas": 1 if pronto else 0}]

    async def wait_for_timeout(self, ms):
        self.voltas += 1


async def test_o_aviso_de_direitos_e_pego_quando_aparece_no_meio_do_upload():
    # ANTES: a checagem era uma janela fixa de 2 s ANTES da espera. O aviso chega
    # entre 7 e 17 s (o tempo do upload), entao ela quase nunca o encontrava: o modal
    # ficava na tela, a espera contava ate 180 s e a conta perdia a primeira tentativa
    # inteira. Uma vez em CADA conta do pool — com cinco estreando em paralelo, cinco
    # jobs perdendo a estreia juntos.
    pagina = _PaginaComAviso()
    indice = await TransportePlaywright()._esperar_item_pronto(
        pagina, "gato.png", teto_ms=60_000)

    assert indice == 0
    assert pagina.aviso_aceito is True
    assert sel.BOTAO_CONCORDO in pagina.clicados


async def test_upload_sem_aviso_nao_clica_em_nada():
    # a ausencia e o caso comum: o aviso existe uma vez por conta, e todas as geracoes
    # seguintes nao podem pagar preco nenhum por ele
    pagina = _PaginaComAviso(aparece_na_volta=10_000)
    pagina.aviso_aceito = True          # ja aceito ha muito tempo nesta conta
    indice = await TransportePlaywright()._esperar_item_pronto(
        pagina, "gato.png", teto_ms=60_000)

    assert indice == 0
    assert pagina.clicados == []


# --- o card de falha e reconhecido pela estrutura -----------------------


class _CardFalso:
    def __init__(self, presentes):
        self.presentes = presentes

    def locator(self, seletor):
        return _LocatorContado(self, seletor)

    def quantos(self, seletor):
        return 1 if seletor in self.presentes else 0


async def test_a_falha_antiga_continua_reconhecida():
    assert await TransportePlaywright()._card_falhou(
        _CardFalso({sel.CARD_FALHA})) is True


async def test_a_falha_de_audio_tambem_e_reconhecida():
    # VISTO EM PRODUCAO 2026-08-21: "Falha ao gerar audio. Tente usar outro comando ou
    # envie feedback." nao casava `text="Ops! Algo deu errado"`, e `consultar` devolvia
    # RODANDO ate o timeout de dez minutos. Para o usuario, "travado".
    assert await TransportePlaywright()._card_falhou(
        _CardFalso({sel.CARD_FALHA_TITULO})) is True


async def test_card_normal_nao_e_falha():
    assert await TransportePlaywright()._card_falhou(_CardFalso(set())) is False


def test_o_titulo_da_falha_e_ancorado():
    # sem a ancora, "Falha ao gerar audio" no CORPO do card — ou qualquer "falhou"
    # perdido na tela — viraria falso positivo e mataria geracao boa
    assert sel.CARD_FALHA_TITULO.startswith("text=/^")
    assert sel.CARD_FALHA_TITULO.endswith("$/")


def test_desligar_o_audio_continua_por_medir():
    # o caminho vale mais que o conserto: o usuario descarta o audio de todo video, e
    # desligar audio nas configuracoes faria a classe inteira de falha deixar de
    # existir. Mexer em configuracao da conta dele precisa da autorizacao dele.
    assert sel.CONFIG_SEM_AUDIO == sel.PENDENTE
