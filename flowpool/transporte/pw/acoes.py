"""As acoes basicas sobre a pagina: clicar, esperar, olhar, provar.

Sao as pecas que TODO caminho do transporte usa — o clique que recusa ambiguidade, a
espera que da sinal de vida, a presenca de um seletor, o veu do overlay e o print da
falha. Estao aqui, e nao dentro de `TransportePlaywright`, porque nenhuma delas depende
do estado da sessao: o que precisam entra por parametro.

E o par de `analise.py`. La ficam as decisoes puras — texto entra, veredito sai, sem
tocar em nada. Aqui ficam as acoes minimas sobre a pagina: tocam, mas nao sabem em que
ponto do trabalho estao. Quem sabe disso e o transporte.

`clicar_unico` recebe a `cura` por parametro em vez de guardar uma: e o transporte que
tem uma, e faze-la entrar pela porta e o que permite testar a acao com uma pagina falsa
e uma cura falsa, sem construir um transporte inteiro.
"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path

from flowpool.transporte.pw import analise, dom
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.cura import AutoCura
from flowpool.transporte.pw.diario import Diario


def pagina_de(escopo):
    """A pagina por tras de um escopo de clique.

    `clicar_unico` recebe ora a pagina, ora um localizador (o painel do chip, o menu
    do card, um item da grade) — e a cura precisa da PAGINA para tirar print e varrer
    o DOM, enquanto a validacao continua acontecendo no escopo estreito. Localizador
    expoe `.page`; a pagina nao expoe, e e ela mesma.
    """
    return getattr(escopo, "page", escopo)


async def clicar_unico(escopo, seletor: str, o_que: str, cura: AutoCura) -> None:
    """Clica so quando ha EXATAMENTE um candidato.

    E aqui que a regra "nunca por posicao" vira codigo. Um `.first`/`.last` num
    menu ambiguo ja mandou um video do usuario para a lixeira, porque o ultimo item
    do menu do card e "Mover para a lixeira". Ambiguidade e erro, nao escolha.
    """
    loc = escopo.locator(seletor)
    # ESPERAR antes de contar. `count()` responde na hora, e no Flow quase tudo
    # aparece depois de uma animacao: menu que abre, painel que troca de modo, lista
    # que carrega. Contar primeiro da zero e vira "achei 0 candidatos" — mensagem
    # que manda procurar um seletor errado quando o seletor estava certo e so
    # chegou cedo. Este mesmo engano custou tres execucoes: saldo, lista de
    # projetos e menu de modelo.
    # falhar aqui e esperado: segue para o exigir_unico, que da a mensagem boa
    with suppress(Exception):
        await loc.first.wait_for(timeout=15_000)
    quantos = await loc.count()

    # AUTO-CURA, e SO com zero candidatos. Dois candidatos e AMBIGUIDADE, e
    # ambiguidade nao se cura: foi um desempate automatico que mandou um video do
    # usuario para a lixeira. Zero e outra coisa — e o Google ter mexido no botao —,
    # e ai perguntar tem sentido. `cura` recusa sozinha tudo o que for destrutivo,
    # tudo o que nao tiver descricao e tudo o que passar do teto da execucao; ela
    # nunca levanta, entao o pior caso continua sendo o erro de sempre.
    if quantos == 0:
        novo = await cura.curar(
            pagina_de(escopo), escopo, seletor, o_que, print_de_falha)
        if novo:
            loc = escopo.locator(novo)
            quantos = await loc.count()

    analise.exigir_unico(quantos, seletor, o_que)
    await loc.click(timeout=15_000)


async def esperar(pagina, condicao, d: Diario, o_que: str,
                  teto_ms: int = 60_000, cada: int = 500) -> bool:
    """Gira ate a condicao valer, dando sinal de vida. Devolve se valeu.

    Devolve `bool` em vez de levantar porque a ausencia significa coisas
    diferentes em cada ponto, e a mensagem certa e a de quem chama.
    """
    gasto = 0
    while gasto < teto_ms:
        if await condicao():
            return True
        await pagina.wait_for_timeout(cada)
        gasto += cada
        d.esperando(o_que)
    return False


async def tem(pagina, seletor: str) -> bool:
    return await pagina.locator(seletor).count() > 0


async def sem_veu(pagina, tentativas: int = 16) -> bool:
    """Fecha o que estiver aberto ate o veu do overlay sair da tela.

    MEDIDO 2026-09-05: com um menu aberto, o Playwright recusa o clique seguinte
    com `<div class="cdk-overlay-backdrop ..."> intercepts pointer events`. O veu
    bloqueia TUDO, e o erro nomeia o elemento coberto e nunca o que o cobre — foi
    assim que o aviso de direitos travou a geracao na aplicacao antiga, e custou
    horas antes de alguem olhar um print.
    """
    for _ in range(tentativas):
        if await pagina.locator(sel.VEU_ABERTO).count() == 0:
            return True
        await pagina.keyboard.press("Escape")
        await pagina.wait_for_timeout(400)
    return False


async def print_de_falha(pagina, motivo: str) -> Path | None:
    """Salva um print quando alguma coisa da errado. Devolve o caminho.

    Existe porque diagnosticar interface pelo DOM e adivinhar. Duas tentativas
    foram perdidas deduzindo que um `<div data-state=open aria-hidden>` era o painel
    do chip; era um modal de aviso do Google, e UM PRINT teria mostrado isso em dois
    segundos. Quem diagnostica sem ver a tela conserta o elemento errado.

    Nunca levanta: um print que falha nao pode virar o erro que se estava tentando
    explicar.
    """
    try:
        destino = Path("scratch/falhas")
        destino.mkdir(parents=True, exist_ok=True)
        # sem timestamp: o Playwright nao expoe relogio e importar `time` aqui so
        # para nomear arquivo nao paga. O motivo ja distingue os casos, e o ultimo
        # print de cada motivo e o que interessa.
        limpo = re.sub(r"[^a-z0-9]+", "-", dom.sem_acento(motivo))[:50].strip("-")
        caminho = destino / f"{limpo or 'falha'}.png"
        await pagina.screenshot(path=str(caminho))
        print(f"print da falha: {caminho}")
        return caminho
    except Exception:
        return None
