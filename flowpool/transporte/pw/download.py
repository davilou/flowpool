"""Baixar o resultado: menu do card, resolucao, e os bytes do blob.

Tres decisoes moram aqui, e as tres ja custaram trabalho gerado e pago:

  QUE CARD    o tipo vem do MENU NA TELA, nao de um dicionario em memoria. Card de
              imagem num job de video nao se baixa — se recusa.
  QUE MENU    o submenu de resolucao e achado pelo CONTEUDO. `.last` com dois menus
              abertos e tao fragil quanto indice em lista que reordena, e o vizinho
              errado no menu do card e "Mover para a lixeira".
  QUE BYTES   o arquivo e lido do blob na pagina, e nao do download do Chrome — que
              trava o processo inteiro em blob desde o Chrome 152.

Nada aqui guarda estado. O modo com que o job foi gerado, a auto-cura da sessao e o
card ja achado entram por parametro; quem os tem e o transporte.
"""

from __future__ import annotations

from pathlib import Path

from flowpool.nucleo.tipos import Modo
from flowpool.transporte.base import CardTrocado, GeracaoFalhou
from flowpool.transporte.pw import acoes, analise, captura, dom
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.cura import AutoCura
from flowpool.transporte.pw.diario import Diario

# A resolucao pedida no submenu de download, por modo. So `baixar` le isto.
RESOLUCAO = {
    Modo.IMAGEM: "1k",          # "1k / tamanho original"
    Modo.T2V: "720p",           # NAO MEDIDO na aplicacao nova
    Modo.FRAMES2V: "720p",
    Modo.INGREDIENTS2V: "720p",
}


async def baixar(pagina, card, id_remoto: str, destino: Path, d: Diario,
                 modo: Modo | None, cura: AutoCura) -> Path:
    """Abre o menu do card, escolhe a resolucao e grava o arquivo. Devolve o caminho.

    `modo` e `cura` entram por parametro porque sao memoria do transporte: o modo com
    que o job foi gerado e a auto-cura da sessao. `card` tambem — quem sabe achar o
    tile e o modulo da grade, e mistura-los aqui amarraria os dois.
    """
    # o <video> nao e clicavel (e miniatura). O alvo e o centro do container.
    caixa = await card.bounding_box()
    if caixa is None:
        raise GeracaoFalhou(f"{id_remoto} sem caixa na tela - nao da para clicar")
    await pagina.mouse.click(caixa["x"] + caixa["width"] / 2,
                             caixa["y"] + caixa["height"] / 2, button="right")
    menu = pagina.locator(sel.MENU_CARD).last
    await menu.wait_for(timeout=15_000)
    await logar_menu(menu)

    # ⚠️ O TIPO VEM DO CARD NA TELA, NAO DE UM DICIONARIO EM MEMORIA.
    #
    # `_modo_do_card` mora na instancia e MORRE A CADA REINICIO do servico. Um job
    # que gerou antes do restart e baixou depois perdia o modo, caia no default
    # `Modo.T2V` e pedia "720p" — num card de IMAGEM, cujo submenu so tem "1k".
    # Sai exatamente "o submenu de download nao trouxe '720p' em 20s", que o
    # usuario viu no painel em 2026-09-09. Tres restarts no mesmo dia tornaram
    # isso comum.
    #
    # O menu do card diz o tipo de graca, e esta MEDIDO: card de video tem
    # "adicionar ao cenario" e "publicar no youtube"; no de imagem o lugar deles e
    # ocupado por "animar". Ler a tela e melhor do que lembrar.
    rotulos = [dom.seguro(t) for t in
               await menu.locator(sel.MENU_ITEM).all_inner_texts()]
    tipo_na_tela = analise.tipo_do_card(rotulos)

    # ⚠️ DIVERGENCIA E CARD ERRADO, E CARD ERRADO NAO SE BAIXA.
    # Foi o que aconteceu nos jobs 170/174/189/195 (frames2v): o menu trazia
    # "animar", isto e, o robo abriu um dos quadros de REFERENCIA achando que era
    # o video. Baixar o que estiver la resolveria o erro na tela e gravaria a foto
    # de referencia no lugar do video, calado — pior que falhar.
    if modo is not None and tipo_na_tela is not None:
        esperado = "imagem" if modo is Modo.IMAGEM else "video"
        if tipo_na_tela != esperado:
            # `CardTrocado`, e nao `GeracaoFalhou` seca: o orquestrador tratava isto
            # como falha de download e repetia ate o teto — 21 aberturas de navegador
            # para chegar na mesma recusa, porque card de imagem nao vira video
            # esperando. Ver o docstring de `CardTrocado`.
            raise CardTrocado(
                f"{id_remoto} e um card de {tipo_na_tela} e o job e {esperado}: "
                f"o card aberto nao e o resultado deste job. Menu: {rotulos[:4]}")

    if tipo_na_tela is not None:
        chave = sel.BAIXAR["1k" if tipo_na_tela == "imagem" else "720p"]
    else:
        chave = sel.BAIXAR[RESOLUCAO[modo if modo is not None else Modo.T2V]]

    await acoes.clicar_unico(menu, sel.MENU_BAIXAR, "item Baixar", cura)
    # os rotulos do submenu tem QUEBRA DE LINHA ("270p\nGIF animado"): passar o
    # rotulo inteiro para um seletor CSS estoura o parser. A escolha acontece sobre
    # o texto ja lido, por `analise.achar_no_submenu`, que recusa empate.
    itens, indice = await esperar_submenu_com(pagina, chave, d)

    # O ARQUIVO E LIDO DO BLOB NA PAGINA, NAO DO DOWNLOAD DO CHROME.
    #
    # Historia curta de um defeito longo. 2026-09-05: o download "perdia" o video —
    # pagina e contexto fechavam e o Playwright apagava o arquivo temporario. As
    # defesas (aba de guarda, `path()` antes de `save_as`, traco desligado)
    # ganhavam a corrida em ~3 de 4 casos. 2026-09-06, Chrome 152: 0 de 22. A sonda
    # com `DEBUG=pw:browser` mostrou o que nenhuma hipotese anterior tinha visto:
    #
    #     download evento: ...mp4 | url: blob:https://flow.google.com/...
    #     pw:browser <process did exit: exitCode=3221225477>   (0xC0000005)
    #
    # O PROCESSO DO CHROME TRAVA no instante em que o download nativo de um blob
    # comeca. Nao ha aba de guarda que segure um navegador morto, e o modo de
    # download do CDP (`allow` para pasta propria) morre igual.
    #
    # O Flow baixa por `<a download href="blob:...">` + `a.click()`. O blob e memoria
    # da pagina: `captura.py` cancela o clique (sem download nativo, sem trava),
    # guarda a URL e o nome, e os bytes sao lidos em pedacos por `evaluate`. Medido:
    # 862.943 bytes de MP4 validos, Chrome vivo, sessao fechada com codigo 0.
    if not await captura.instalado(pagina):
        raise GeracaoFalhou(
            "o interceptador de download nao esta nesta pagina: ela foi aberta "
            "antes de `captura.instalar` — o contexto precisa vir de `abrir_contexto`")
    await captura.zerar(pagina)
    await itens.nth(indice).click(timeout=15_000)
    capturado = await esperar_captura(pagina, d)

    # o Flow escolhe a extensao: jpeg para imagem, mp4 para video. O orquestrador
    # montou `destino` com .png/.mp4 a partir do modo — se salvassemos ali, o banco
    # apontaria para um .png que na verdade e JPEG. Devolver o caminho REAL faz
    # `job.arquivo` ficar verdadeiro.
    real = analise.caminho_final(destino, capturado["nome"] or destino.name)
    tamanho = await captura.gravar_blob(pagina, capturado["url"], real)
    d.passo(f"baixado: {real} ({tamanho} bytes, via {capturado['via']})")
    return real


async def esperar_captura(pagina, d: Diario, teto_ms: int = 60_000,
                          cada: int = 250) -> dict:
    """Espera o interceptador registrar o `<a download>` do clique.

    Espera de ESTADO, nao de tempo: o Flow busca o arquivo antes de montar o
    blob, e isso leva de 1 s a alguns segundos conforme o tamanho.

    Se em vez da captura chegar um download NATIVO, o Flow mudou o caminho (deixou
    de usar `<a download>` de blob) e o interceptador precisa ser medido de novo —
    o erro diz isso, em vez de deixar o Chrome travar em silencio.
    """
    nativo: list = []

    def anotar(dl):
        nativo.append(dl.suggested_filename)

    # registrado e REMOVIDO: uma sessao baixa varias vezes, e cada chamada
    # deixando um ouvinte a mais e vazamento com cara de bug intermitente
    pagina.on("download", anotar)
    try:
        gasto = 0
        while gasto < teto_ms:
            if nativo:
                raise GeracaoFalhou(
                    f"o Flow disparou um download NATIVO ({nativo[0]}) em vez do "
                    "<a download> de blob que o interceptador cobre. O caminho de "
                    "download mudou: medir de novo antes de insistir")
            lista = await captura.capturados(pagina)
            if lista:
                return lista[-1]
            await pagina.wait_for_timeout(cada)
            gasto += cada
            d.esperando("o blob do download")
        raise GeracaoFalhou(
            f"cliquei em baixar e nenhum blob foi capturado em {teto_ms // 1000}s")
    finally:
        pagina.remove_listener("download", anotar)


async def esperar_submenu_com(pagina, chave: str, d: Diario,
                              teto_ms: int = 20_000):
    """Espera o submenu que CONTEM `chave`. Devolve (itens, indice).

    SUBSTITUI UM SONO FIXO, e ele custou dois videos gerados e pagos (jobs 29 e
    30). A versao anterior dormia 1,5 s e pegava `MENU_CARD.last`; quando o submenu
    de resolucao demorava — e ele demora justamente quando a pagina acabou de
    renderizar um video novo — o `.last` ainda era o menu DE CIMA, que nao tem
    "720p". As tres tentativas se esgotavam e o video ficava no Flow, cobrado, sem
    ninguem baixar. Era a unica espera por TEMPO que restava no transporte.

    Identifica o submenu pelo CONTEUDO, e nao por ser o ultimo: com dois menus
    abertos, "o ultimo" e tao fragil quanto indice em lista que reordena — e o
    vizinho errado no menu do card e "Mover para a lixeira".

    O ERRO LISTA O QUE HAVIA NA TELA. Sem isso a falha chegou como "achei 0" sem
    dizer o que existia, e meia hora se perdeu nisso.
    """
    gasto = 0
    vistos: list[list[str]] = []
    while gasto < teto_ms:
        menus = pagina.locator(sel.MENU_CARD)
        vistos = [await menus.nth(i).locator(sel.MENU_ITEM).all_inner_texts()
                  for i in range(await menus.count())]
        achado = analise.achar_no_submenu(vistos, chave)
        if achado is not None:
            qual, indice = achado
            return menus.nth(qual).locator(sel.MENU_ITEM), indice
        await pagina.wait_for_timeout(250)
        gasto += 250
        d.esperando(f"o submenu de download com {chave!r}")
    raise GeracaoFalhou(
        f"o submenu de download nao trouxe {chave!r} em {teto_ms // 1000}s. "
        f"O que havia na tela: "
        f"{[[dom.seguro(r).replace(chr(10), ' / ') for r in m] for m in vistos]}")


async def logar_menu(menu) -> None:
    """Imprime os candidatos ANTES de clicar. Barato, e ja teria evitado um
    clipe do usuario indo para a lixeira."""
    itens = [dom.seguro(t) for t in
             await menu.locator(sel.MENU_ITEM).all_inner_texts()]
    print("menu do card:", [i.splitlines()[0] for i in itens if i])
