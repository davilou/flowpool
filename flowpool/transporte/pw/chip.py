"""O chip: o painel de configuracao da barra do prompt.

Uma geracao so sai certa se o chip confirmar. Este modulo abre o painel, escolhe modo,
modelo, proporcao, resolucao, duracao e quantidade, e CONFERE o resultado contra o que
o painel declara marcado — nao contra o texto do chip, que omite o modelo.

Tres coisas que parecem detalhe e nao sao:

  O CHIP E UM INTERRUPTOR.  Clicar com ele aberto FECHA. Por isso `garantir` pergunta
                            antes de clicar, e e chamada ENTRE cada opcao: no Flow novo
                            o painel se fecha ao escolher.
  AUSENCIA NAO E DEFEITO.   Os grupos de duracao e resolucao pertencem ao MODELO, e
                            somem com alguns. `opcao_se_existir` devolve None nesse
                            caso — se chegasse a `clicar_unico`, a auto-cura tentaria
                            "consertar" a ausencia, e ja mapeou `RESOLUCAO[720p]` para
                            o botao de PROPORCAO fazendo isso.
  NADA DE PASSO TOLERANTE.  Um `except: segue` engoliu um clique de duracao que nao
                            pegou e entregou 8s onde foram pedidos 4s, com credito
                            debitado e ninguem percebendo.

Nao guarda estado: a auto-cura entra por parametro e o custo lido sai no retorno.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from flowpool.nucleo.tipos import Modo
from flowpool.transporte.base import GeracaoFalhou, Pedido
from flowpool.transporte.pw import acoes, analise, dom
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.cura import AutoCura
from flowpool.transporte.pw.diario import Diario

# A resolucao de GERACAO de video, no painel do chip. Grupo novo em 2026-09-05.
#
# O padrao do Flow virou 360p. A aplicacao antiga sempre entregou 720p porque era o
# unico valor que existia — entao NAO tocar aqui rebaixaria em silencio todo video do
# usuario. 720p mantem o que ele ja tinha.
#
# Isto e uma constante e nao um campo do pedido porque `Job` ainda nao tem a coluna.
# Vira escolha por job quando a API expuser o campo — a lista esta em `sel.RESOLUCOES`.
RESOLUCAO_VIDEO = "720p"


@dataclass(frozen=True)
class Config:
    """O que a configuracao deixou pronto: o texto do chip e o custo que ele anunciou.

    O custo vem junto porque so existe com o painel ABERTO, e quem precisa dele — a
    mensagem de saldo curto no envio — acontece com o painel ja fechado.
    """

    chip: str
    custo: int | None


# -------------------------------------------------------------------- chip
def painel_de(pagina):
    return pagina.locator(acoes.medido("PAINEL_CHIP", sel.PAINEL_CHIP)).first


async def aberto(pagina) -> bool:
    """O painel de configuracao esta na tela AGORA.

    Conta os botoes em vez de so procurar o contorno: o overlay do Angular existe
    na pagina mesmo vazio, e `.cdk-overlay-pane` sozinho casaria um pane sem
    conteudo — foi exatamente isso que fez a primeira sonda achar o painel vazio.
    """
    return await pagina.evaluate(
        "() => [...document.querySelectorAll('.cdk-overlay-pane')]"
        ".some(x => x.querySelectorAll('button').length > 3)")


async def garantir(pagina, d: Diario, tentativas: int = 3) -> bool:
    """Deixa o painel aberto, e devolve se conseguiu.

    O CHIP E UM INTERRUPTOR — clicar com ele aberto FECHA. Por isso a primeira
    coisa e perguntar se ja esta aberto, nunca clicar de saida. E por isso esta
    funcao e chamada ENTRE cada opcao: no Flow novo o painel se fecha ao escolher,
    e a opcao seguinte cairia no vazio.
    """
    for volta in range(tentativas):
        if await aberto(pagina):
            return True
        with suppress(Exception):
            await pagina.locator(sel.CHIP_CONFIG).first.click(timeout=10_000)
        gasto = 0
        while gasto < 20_000:
            await pagina.wait_for_timeout(250)
            gasto += 250
            if await aberto(pagina):
                return True
        d.passo(f"o painel de configuracao nao abriu (volta {volta + 1} de "
                f"{tentativas})")
    return False


async def opcao_se_existir(painel, pagina, mapa: dict, chave, o_que: str,
                           d: Diario, cura: AutoCura):
    """Escolhe `chave` no grupo, SE o grupo existir. Devolve o que foi escolhido.

    Devolve `None` quando o grupo inteiro nao esta no painel — e isso e estado
    legitimo, nao seletor quebrado. MEDIDO 2026-09-05: os grupos de DURACAO e
    RESOLUCAO pertencem ao MODELO. Em Frames com Omni Flash os dois aparecem; com
    Veo 3.1 Lite, nenhum dos dois.

    A diferenca entre "ausente" e "quebrado" importa por dois motivos:
    `_clicar_unico` levantaria erro numa configuracao correta, e a AUTO-CURA
    tentaria consertar a ausencia — foi o que aconteceu, e ela mapeou
    `RESOLUCAO[720p]` e `DURACAO[8s]` para o botao de PROPORCAO.
    """
    await garantir(pagina, d)
    # o grupo existe se QUALQUER opcao dele estiver na tela
    for seletor in mapa.values():
        if await painel.locator(seletor).count() > 0:
            break
    else:
        d.passo(f"o modelo escolhido nao oferece {o_que}; o Flow decide")
        return None
    await acoes.clicar_unico(painel, mapa[chave], o_que, cura)
    await garantir(pagina, d)
    return chave


async def marcados(pagina) -> list[str]:
    """O que o painel declara SELECIONADO agora, por `aria-checked`.

    E a novidade da aplicacao nova, e ela fecha uma classe de defeito: antes, a
    unica forma de saber se a configuracao pegou era ler o texto do chip, que
    omite o modelo e resume o resto.
    """
    bruto = await pagina.evaluate(
        "() => [...document.querySelectorAll('.cdk-overlay-pane [aria-checked=true]')]"
        ".map(e => (e.innerText||'').trim().split(String.fromCharCode(10))"
        ".map(s => s.trim()).filter(Boolean).join(' '))")
    return [dom.seguro(t) for t in bruto]


async def quantidade_marcada(pagina, quantidade: int) -> bool:
    """`xN` ja aparece entre os marcados? NA MESMA FORMA que a conferencia usa.

    De proposito o mesmo `" | ".join(...)` do fim de `configurar`: esperar por um
    criterio e conferir por outro daria o pior dos mundos — espera que termina com a
    conferencia ainda reprovando, ou o contrario.
    """
    return f"x{quantidade}" in " | ".join(await marcados(pagina))


async def texto(pagina) -> str:
    bruto = await pagina.locator(sel.CHIP_CONFIG).first.inner_text(timeout=15_000)
    return dom.seguro(bruto.replace("\n", " "))


async def escolher_modo(pagina, painel, modo: str, cura: AutoCura) -> None:
    """Frames e Elementos so existem no painel DEPOIS de ele estar em video.

    MEDIDO 2026-08-20: a lista de modos depende do modo atual — com o chip em
    imagem so ha `image` e `videocam`. Clicar direto em `chrome_extension` vindo de
    imagem e clicar no vazio, e foi assim que um job real falhou tres vezes.

    A espera pelo `videocam` antes de contar nao e zelo: ele existe nos DOIS
    estados do painel, entao serve de ancora — contar o alvo antes de o painel
    renderizar daria "nao esta visivel" e forcaria dois cliques onde um bastava.
    """
    # falhar aqui e esperado: quem da a mensagem boa e o `_clicar_unico`
    with suppress(Exception):
        await painel.locator(sel.BOTAO_MODO["t2v"]).first.wait_for(timeout=15_000)
    visivel = await painel.locator(sel.BOTAO_MODO[modo]).count() > 0
    etapas = analise.etapas_ate_o_modo(modo, visivel)
    for passo in etapas:
        o_que = "modo" if passo == modo else f"modo {passo} (pai de {modo})"
        await acoes.clicar_unico(painel, sel.BOTAO_MODO[passo], o_que, cura)
        # o painel REDESENHA a cada troca de modo: sem esperar, o clique seguinte
        # cai no layout velho
        await pagina.wait_for_timeout(1200)


async def fechado(pagina) -> bool:
    """Fechado = o PAINEL sumiu da tela E nao sobrou veu.

    As duas metades, e nesta ordem, porque cada uma ja falhou sozinha:

    * so o VEU: MEDIDO 2026-08-20 por print — o painel do chip fica aberto SEM veu
      nenhum. Quem so olhava o veu declarava "fechei" sem nem apertar Escape, e o
      painel continuava por cima do campo de prompt. Era este o defeito.
    * so o PAINEL: o dropdown de modelo abre por cima do painel e leva um veu
      junto; o painel pode sumir com o veu ainda de pe, e veu intercepta clique
      mesmo sendo `aria-hidden`.
    """
    painel = pagina.locator(sel.PAINEL_CHIP)
    if await painel.count() > 0 and await painel.first.is_visible():
        return False
    return await pagina.locator(sel.VEU_ABERTO).count() == 0


async def fechar(pagina, tentativas: int = 4) -> None:
    """Aperta Escape ate o painel do chip estar fechado de verdade.

    Um `Escape` so nao basta porque ha DOIS niveis: o dropdown de modelo abre por
    cima do painel, e o primeiro Escape fecha so o de cima.

    Espera-se o ESTADO, nao o tempo. Foi por dormir 1200ms fixos aqui que o defeito
    so aparecia as vezes.

    NAO CHAMAR COM O SELETOR DE MIDIA ABERTO: ele e um `[role=dialog]`, que
    `PAINEL_CHIP` tambem casa — o criterio veria o seletor de midia como "painel
    que nao fechou" e gastaria os Escapes fechando a peca errada.
    """
    for _ in range(tentativas):
        if await fechado(pagina):
            return
        await pagina.keyboard.press("Escape")
        # espera o ESTADO, com teto curto: quem dorme aqui volta a intermitencia
        for _ in range(10):
            await pagina.wait_for_timeout(250)
            if await fechado(pagina):
                return
    raise GeracaoFalhou(
        "o painel do chip nao fechou: ele continua na tela (ou sobrou veu) e "
        "nenhum clique no campo de prompt passaria")


async def exigir_agente_desligado(pagina) -> bool:
    """Garante o MODO DIRETO antes de qualquer outra coisa. Devolve se desligou.

    O botao "Agente" da barra do prompt e um interruptor, e ele troca o modo de
    operacao do Flow. VISTO EM PRODUCAO: as vezes ele amanhece ligado, e ai "o
    Playwright nao consegue identificar as coisas".

    MEDIDO 2026-08-21, os dois estados: com o agente LIGADO o `CHIP_CONFIG` casa
    ZERO elementos — o chip some da barra e no lugar dele entram `tune
    Configuracoes` e `article_spark Instrucoes para o agente`. E por isso que esta
    verificacao roda ANTES de tocar no chip, e nao junto com ele: no estado errado o
    chip nem existe para ser clicado, e o erro que sai fala do chip, nunca do
    interruptor que o tirou da tela.

    Desligar tambem nao e "clicar e seguir". Confirma-se o estado depois, porque um
    clique que nao pegou deixaria a geracao inteira no caminho conversacional — o
    que pergunta antes de gastar, com Aprovar/Rejeitar, e espera uma confirmacao
    que o transporte nao sabe dar.

    Espera o ESTADO, nunca dorme por tempo: quem dorme aqui troca um defeito
    constante por um intermitente.
    """
    loc = pagina.locator(acoes.medido("BOTAO_AGENTE", sel.BOTAO_AGENTE))
    # falhar aqui e esperado: segue para o exigir_unico, que da a mensagem boa
    with suppress(Exception):
        await loc.first.wait_for(timeout=15_000)
    # zero e "o interruptor sumiu da tela"; dois e ambiguidade. Nenhum dos dois se
    # resolve chutando, e a auto-cura nao entra aqui de proposito — o vizinho deste
    # botao e o de ENVIAR, e um chute ali dispara geracao de verdade
    # (seletores.NAO_CURAVEIS diz isso na outra ponta)
    analise.exigir_unico(await loc.count(), sel.BOTAO_AGENTE, "interruptor do Agente")

    botao = loc.first
    if not analise.agente_ligado(await botao.get_attribute(sel.ATRIBUTO_AGENTE)):
        return False

    print("agente LIGADO na barra do prompt: desligando antes de configurar o chip")
    await botao.click(timeout=15_000)
    # o botao REDESENHA ao trocar de estado (medido: o conteudo interno muda). Por
    # isso se le o atributo de novo a cada volta, e nao uma vez so depois de dormir.
    for _ in range(20):
        await pagina.wait_for_timeout(250)
        try:
            if not analise.agente_ligado(
                    await botao.get_attribute(sel.ATRIBUTO_AGENTE)):
                break
        except GeracaoFalhou:
            continue   # marcador ausente no meio do redesenho: o veredito e abaixo
    analise.exigir_agente_desligado(await botao.get_attribute(sel.ATRIBUTO_AGENTE))
    return True


async def custo_anunciado(pagina) -> int | None:
    """O que o painel do chip diz que a geracao vai custar. `None` se nao achou.

    NAO BLOQUEIA A GERACAO. E leitura de instrumento, nao verificacao: enquanto o
    numero nao alimentar a reserva, um deploy que mude a frase nao pode derrubar um
    job que estava correto. O que se perde e a linha no log, e ela avisa sozinha.
    """
    try:
        texto = await pagina.locator(sel.CUSTO_DA_GERACAO).first.inner_text(
            timeout=3000)
        custo = analise.custo_do_texto(dom.seguro(texto))
    except Exception:
        print("custo: o painel nao anunciou o preco (seletores.CUSTO_DA_GERACAO)")
        return None
    print(f"custo anunciado pelo painel: {custo} creditos")
    return custo


async def configurar(pagina, p: Pedido, d: Diario, cura: AutoCura) -> Config:
    """Abre o chip, ajusta tudo e CONFERE. Devolve o texto do chip e o custo lido.

    O CUSTO SAI NO RETORNO. Enquanto isto era metodo ele descansava num campo do
    transporte e era lido la no envio — e o numero so existe com o painel ABERTO,
    enquanto a mensagem de saldo curto acontece com ele ja fechado.

    Nada de passo tolerante. Um `except: segue` engoliu um clique de duracao que
    nao pegou e entregou 8s onde foram pedidos 4s — com credito debitado, arquivo
    na pasta e ninguem percebendo. Se o chip nao confirmar o estado, e erro.
    """
    modo = p.modo.value
    d.passo(f"configurando: {modo} / {p.modelo.value} / {p.proporcao} / "
            f"x{p.quantidade}")
    # recusa ANTES de tocar no chip, com a reserva de credito ainda estornavel
    analise.exigir_proporcao_do_modo(modo, p.proporcao)

    # O MODO DE OPERACAO vem antes do chip, e nao por gosto de ordem: com o agente
    # ligado o chip NAO EXISTE na barra (medido), e a linha seguinte morreria num
    # timeout que fala do chip e nunca da causa.
    await exigir_agente_desligado(pagina)

    if not await garantir(pagina, d):
        raise GeracaoFalhou("o painel de configuracao nao abriu")
    painel = painel_de(pagina)

    # ordem importa: o modo redefine as listas de proporcao, duracao e modelo — e
    # tambem a propria lista de MODOS, o que faz dos submodos duas etapas
    await escolher_modo(pagina, painel, modo, cura)
    await garantir(pagina, d)

    await acoes.clicar_unico(painel, sel.DROPDOWN_MODELO, "dropdown de modelo", cura)
    # A escolha do modelo NAO sai por seletor CSS. Medido: o rotulo tem prefixo que
    # muda por familia (emoji na imagem, ligadura no video), o que mata `:text-is`;
    # e "Nano Banana 2" e prefixo de "Nano Banana 2 Lite", o que torna `:has-text`
    # ambiguo. Le-se os rotulos e escolhe-se em Python, por sufixo — mesmo padrao
    # do submenu de download, e testavel.
    itens = pagina.locator(sel.MENU_ITEM)
    await itens.first.wait_for(timeout=15_000)
    indice = analise.indice_por_sufixo(
        await itens.all_inner_texts(), sel.ROTULO_MODELO[p.modelo.value])
    await itens.nth(indice).click(timeout=15_000)
    await pagina.wait_for_timeout(1000)

    # o modelo NAO aparece no texto do chip: confere-se aqui, com o painel aberto
    rotulo = dom.seguro(await painel.locator(sel.DROPDOWN_MODELO).first.inner_text())
    aceitos = [dom.sem_acento(r) for r in
               analise.rotulos_aceitos(sel.ROTULO_MODELO[p.modelo.value])]
    if not any(r in rotulo for r in aceitos):
        raise GeracaoFalhou(f"modelo nao trocou: dropdown diz {rotulo!r}, "
                            f"esperado um de {aceitos!r}")

    await garantir(pagina, d)
    await acoes.clicar_unico(painel, sel.PROPORCAO[p.proporcao], "proporcao", cura)
    await garantir(pagina, d)

    # RESOLUCAO — grupo que a aplicacao antiga nao tinha, e que NEM SEMPRE EXISTE.
    #
    # MEDIDO 2026-09-05: o painel de video ganhou [360p] [720p], com 360p de padrao.
    # A aplicacao antiga sempre entregou 720p porque era o unico valor. Sem tocar
    # aqui, todo video sairia em 360p sem ninguem pedir — piora silenciosa, o pior
    # tipo.
    #
    # MAS O GRUPO PERTENCE AO MODELO, NAO AO MODO. Medido no mesmo painel, em
    # Frames: com Omni Flash ha duracao e resolucao; trocando para Veo 3.1 Lite,
    # OS DOIS GRUPOS SOMEM. Exigir o botao ali quebrava a geracao — e pior, a
    # auto-cura tentava "consertar" a ausencia e mapeou `RESOLUCAO[720p]` para o
    # botao de PROPORCAO. Ausencia legitima nao e seletor quebrado.
    resolucao = None
    if p.modo is not Modo.IMAGEM:
        resolucao = await opcao_se_existir(
            painel, pagina, sel.RESOLUCAO, RESOLUCAO_VIDEO, "resolucao", d, cura)
    # `None` nos modos SEM grupo de duracao no painel, onde procurar o botao
    # derrubaria a geracao inteira. Eram so imagem; FRAMES2V entrou em 2026-08-21.
    #
    # MEDIDO por print do painel aberto em Frames: ele tem `Imagem|Video`,
    # `Frames|Elementos`, proporcao, dropdown de modelo e quantidade — e mais nada.
    # O chip confirma, escrevendo "Video . 720p . x1" onde os outros modos escrevem
    # "Video . 720p . 4s . 9:16 x1". Sem isto, `_clicar_unico` procurava "4s" num
    # painel que nao o tem, a auto-cura era acionada, e o first/last frame do
    # usuario nunca passava da configuracao.
    #
    # Nos demais modos de video vale a duracao DO PEDIDO — ate a Task 15 era uma
    # constante de 8s aqui, e todo video saia 70% mais caro do que o de 4s.
    #
    # 2026-09-05: NA APLICACAO NOVA, FRAMES TEM DURACAO. Os quatro valores estao no
    # painel em todos os modos de video, `frames` inclusive — medido. A conclusao
    # de 21/08 valia para a aplicacao antiga e morreu com ela; `tipos.SEM_DURACAO`
    # continua valendo so para IMAGEM.
    duracao = None if p.modo is Modo.IMAGEM else p.duracao
    if duracao:
        duracao = await opcao_se_existir(
            painel, pagina, sel.DURACAO, duracao, "duracao", d, cura)
    await garantir(pagina, d)
    await acoes.clicar_unico(painel, sel.QUANTIDADE[p.quantidade], "quantidade", cura)
    await garantir(pagina, d)

    # ⚠️ ESPERAR O `aria-checked` ASSENTAR, PORQUE A QUANTIDADE E O ULTIMO GRUPO.
    #
    # Todos os grupos acima tem um clique DEPOIS deles, e e esse clique que lhes da
    # tempo de repintar antes da conferencia. A quantidade nao tem nenhum: clicava-se
    # e lia-se `marcados()` na linha de baixo, ainda com o valor do job ANTERIOR.
    #
    # MEDIDO 2026-09-12 por `scratch/sonda_x4_painel.py`: os quatro botoes estao
    # habilitados e visiveis, e o clique PEGA — reabrindo o painel, `x4` estava
    # marcado na sonda que acabara de "falhar". Quem errava era a leitura.
    # Jobs 862, 955, 1007 e 1014 pediram x4 e leram x1; 869 e 1010 pediram x1 e leram
    # x4 — sempre o valor do job anterior. Nenhum se perdeu (o retry do orquestrador
    # acerta), e cada um custou um ciclo de navegador inteiro.
    #
    # Nao e um `except: segue` com outro nome: esgotado o prazo, a conferencia abaixo
    # reprova exatamente como antes. Esperar so tira da conta o tempo de repintura.
    if not await acoes.esperar(
            pagina, lambda: quantidade_marcada(pagina, p.quantidade), d,
            f"o painel marcar x{p.quantidade}", teto_ms=10_000, cada=250):
        d.passo(f"x{p.quantidade} nao apareceu marcado em 10s; a conferencia decide")

    # A CONFERENCIA MUDOU DE FONTE, e melhorou.
    #
    # A aplicacao antiga nao marcava a opcao escolhida, e a unica forma de saber se
    # a configuracao pegou era ler o texto do chip — que nomeia umas coisas e omite
    # outras (o modelo nunca aparecia la). A nova marca TUDO com `aria-checked`,
    # entao da para perguntar diretamente o que esta selecionado.
    selecionados = await marcados(pagina)
    d.passo(f"marcado no painel: {selecionados}")

    # O CUSTO ESTA NA TELA. Le-se aqui porque o painel ja esta aberto e a leitura
    # nao custa clique nenhum — ao contrario do saldo, que paga dois. Hoje isto e
    # log; ligar na reserva do orquestrador e passo proprio (ver seletores.py).
    # guardado porque a mensagem de saldo curto, la em `_gerar`, acontece com o
    # painel JA FECHADO — e o numero so existe enquanto ele esta aberto
    custo = await custo_anunciado(pagina)

    await fechar(pagina)

    # A CONFERENCIA E CONTRA O QUE ESTA MARCADO, nao contra o texto do chip.
    #
    # Cada item pedido tem que aparecer entre os `aria-checked=true`. O chip
    # continua sendo lido, mas como LOG: ele resume bem para o humano e omite
    # coisas para a maquina (o modelo nunca aparece la).
    # so se exige o que o painel OFERECEU. `_opcao_se_existir` devolve None quando
    # o grupo nao existe naquele modelo, e cobrar um botao ausente reprovaria uma
    # configuracao que esta correta.
    exigidos = [sel.MODO_ICONE[modo], sel.LIGADURA_PROPORCAO[p.proporcao],
                f"x{p.quantidade}"]
    if duracao:
        exigidos.append(duracao)
    if resolucao:
        exigidos.append(resolucao)
    juntos = " | ".join(selecionados)
    faltando = [e for e in exigidos if e not in juntos]
    if faltando:
        raise GeracaoFalhou(
            f"o painel nao confirma a configuracao: marcado {selecionados}, "
            f"faltou {faltando}")

    do_chip = await texto(pagina)
    d.passo(f"configuracao confirmada; chip diz: {do_chip}")
    return Config(chip=do_chip, custo=custo)
