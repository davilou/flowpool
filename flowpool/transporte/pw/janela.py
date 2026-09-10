"""Onde a janela do Chrome de trabalho fica: fora da tela, ou na sua frente.

Unica parte do sistema que mexe na GEOMETRIA da janela. Nao conhece job, conta nem
banco — recebe a pagina e decide posicao.

⚠️ POR QUE FORA DA TELA, E NAO MINIMIZADA. MEDIDO 2026-09-10, os tres jeitos contra a
mesma pagina sonda (`visibilityState`, IntersectionObserver, clique sintetizado,
`requestAnimationFrame` e `setInterval` por 2 s):

    janela          visibilidade  IntersectionObserver  clique   rAF
    normal          visible       disparou              ok       295 quadros
    fora da tela    visible       disparou              ok       299 quadros
    MINIMIZADA      visible       disparou              ok        11 quadros

Minimizar surpreende para o bem — `visibilityState` nem muda, porque o Playwright sobe
o Chrome com `--disable-renderer-backgrounding` e `--disable-backgrounding-occluded-windows`.
Mas a COMPOSICAO DE QUADROS para, e e ai que esta base se machuca: a mecanica do Flow
que mais deu trabalho e animada (o overlay do CDK, o popover do slot de frame). O modo
de falha seria thumbnail que nao aparece e espera que estoura — sem erro nenhum.

Fora da tela empata com a janela normal em TODOS os sinais. CONFERIDO que nao e
ilusao: com `--window-position=-32000,-32000` o Win32 informa a janela em
`(-32000, -32000)` numa tela de 1920x1080, o Chrome nao a traz de volta, e
`pagina.screenshot` continua saindo (15 KB naquele teste).

Headless resolveria tambem e esta FORA DE COGITACAO: e o sinal de automacao mais
gritante que existe, e acabamos de tirar o `navigator.webdriver` justamente por isso —
ver `sessao.argumentos_playwright`.
"""

from __future__ import annotations

import os

# ⚠️ NAO E "bem longe", E LONGE O BASTANTE COM MARGEM. O Chrome aceita coordenada
# negativa e nao reposiciona a janela para dentro da area de trabalho, mas um valor
# proximo da borda (-2000, digamos) apareceria de volta num monitor a esquerda do
# principal. -32000 fica fora de qualquer arranjo de monitor plausivel e ainda cabe no
# `LONG` que o Win32 usa.
POSICAO_OCULTA = (-32000, -32000)

# A janela oculta precisa de tamanho EXPLICITO: `--start-maximized` e a posicao se
# anulam, e sem nenhum dos dois a janela nasceria no tamanho padrao pequeno do Chrome.
# O Flow e uma interface larga, e medir a grade num viewport estreito foi fonte de
# seletor que casava zero. 1920x1080 e o que a janela maximizada desta maquina da.
TAMANHO_OCULTO = (1920, 1080)

# Onde a janela reaparece quando alguem pede para ve-la. Nao e `maximized` de proposito:
# a janela que invade a tela inteira e exatamente a reclamacao que fez tudo isto
# existir. Canto superior esquerdo, grande o bastante para ler a grade do Flow.
POSICAO_VISIVEL = (40, 40)
TAMANHO_VISIVEL = (1500, 960)

def oculta_por_ambiente() -> bool:
    """Se o Chrome de trabalho deve nascer fora da tela.

    `FLOWPOOL_JANELA=oculta` liga. O DEFAULT E VISIVEL, e isso nao e timidez: com a
    janela fora da tela o log de passo passa a ser o unico sinal de que ha trabalho
    acontecendo, e foi "a tela parada me fez achar que travou" que fez aquele log
    existir (ver `diario.py`). Quem esconde a janela tem que saber que escondeu.
    """
    return (os.environ.get("FLOWPOOL_JANELA") or "").strip().lower() == "oculta"


def argumentos(oculta: bool) -> list[str]:
    """As flags de geometria do Chrome de trabalho. Funcao pura, para ter teste.

    Os dois modos sao MUTUAMENTE EXCLUSIVOS no Chrome: `--start-maximized` vence
    `--window-position`, e passar os dois deixaria a janela no meio da tela de quem
    pediu para nao ve-la — um bug que so aparece na tela do usuario, nunca num teste
    que olhe a lista de flags sem olhar a ordem.
    """
    if not oculta:
        return ["--start-maximized"]
    x, y = POSICAO_OCULTA
    largura, altura = TAMANHO_OCULTO
    return [f"--window-position={x},{y}", f"--window-size={largura},{altura}"]


async def mostrar(pagina) -> None:
    """Traz para a tela a janela daquela pagina. Levanta se o CDP recusar.

    Via `Browser.getWindowForTarget` + `Browser.setWindowBounds`, que e o protocolo e
    nao Win32: funciona sem enumerar janelas do sistema, sem depender do titulo (o Flow
    troca o titulo da aba) e sem distinguir qual dos Chromes abertos e este — o alvo do
    CDP E esta pagina.

    ⚠️ MEXER NA JANELA PODE APAGAR A FALHA QUE VOCE QUER VER. Relatado pelo usuario em
    2026-09-10: "algumas falhas somem quando reabre a janela". Por isso NADA aqui e
    automatico — nao existe chamada a `mostrar` num tratador de erro. Quem mostra e uma
    pessoa, pelo botao do painel, sabendo que o ato tem efeito; o diagnostico de falha
    continua sendo o print (`acoes.print_de_falha`), que e tirado sem tocar na janela.
    """
    cdp = await pagina.context.new_cdp_session(pagina)
    alvo = await cdp.send("Browser.getWindowForTarget")
    x, y = POSICAO_VISIVEL
    largura, altura = TAMANHO_VISIVEL
    await cdp.send("Browser.setWindowBounds", {
        "windowId": alvo["windowId"],
        "bounds": {"left": x, "top": y, "width": largura, "height": altura,
                   "windowState": "normal"},
    })
