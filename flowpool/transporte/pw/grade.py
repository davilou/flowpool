"""O item da grade: achar, ler o estado, esperar aparecer.

A grade e a lista de resultados do projeto no Flow, e cada tile e um card. Este modulo
faz tres coisas com ela — achar o card de um job, dizer em que estado ele esta (falhou?
foi limite de uso?) e esperar um card novo nascer.

O QUE ELE NAO FAZ E DECIDIR. Nenhuma funcao aqui levanta `SaldoInsuficiente` ou
`LimiteDeUso`, e nenhuma marca conta como seca: elas respondem o que a tela mostra, e
quem traduz isso em consequencia e o `consultar` do transporte. A separacao importa
porque o mesmo sinal significa coisas diferentes em momentos diferentes — o aviso de
credito zerado durante a espera do card e um prazo mais curto, e nao uma recusa.

A MEMORIA DE IDENTIDADES ENTRA POR PARAMETRO (`lembradas`). E o transporte que guarda
qual `src` de miniatura corresponde a qual job, porque isso vive enquanto a sessao
viver; `achar` so consulta e, quando a identidade envelhece, esquece. Deixar o dicionario
la e a funcao aqui e o que permite testar a busca com tres tiles falsos e um dict.
"""

from __future__ import annotations

from dataclasses import dataclass

from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import acoes
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.diario import Diario


@dataclass(frozen=True)
class CardNovo:
    """O que a espera pelo card novo descobriu.

    `identidade` e None mesmo quando `apareceu` e True: o card acabou de nascer e ainda
    nao tem miniatura, entao nao ha `src` estavel para guardar. Guardar o placeholder
    quebrou o job 435 — ver `esperar_novo`.
    """

    apareceu: bool
    identidade: str | None


# A grade nao expoe id: os tiles so tem os marcadores de build do Angular
# (`_ngcontent-*`, `_nghost-*`), que sao iguais em todos e mudam a cada deploy.
# O que identifica um card e o `src` da miniatura — uma URL de conteudo, unica
# por resultado. MEDIDO 2026-09-09 por `scratch/sonda_grade.py`.
#
# Card recem-criado pode ainda nao ter miniatura; ai entra a POSICAO como
# desempate, e por isso a lista e ordenada e nao um conjunto.
_IDENTIDADES = r"""
(seletor) => [...document.querySelectorAll(seletor)].map(e => {
  const m = e.querySelector('img,video');
  const src = m ? (m.getAttribute('src') || m.getAttribute('poster') || '') : '';
  return src || ('sem-midia:' + (e.innerText || '').trim().split('\n')[0]).slice(0, 60);
})
"""

# O prefixo que o JS acima usa para o tile que ainda nao tem miniatura. Fica aqui, ao
# lado de quem o produz, porque `leitura_da_grade` e a unica outra parte que precisa
# saber o que ele significa: "apareceu um card, mas ele ainda nao tem endereco".
PLACEHOLDER = "sem-midia:"


def leitura_da_grade(base: list[str], agora: list[str], contagem: int,
                     antes: int) -> tuple[bool, str | None]:
    """(apareceu, identidade), a partir do retrato ANTES e do DEPOIS. Decisao pura.

    ⚠️ A IDENTIDADE VEM ANTES DA CONTAGEM, E A ORDEM E A DECISAO. Enquanto a contagem
    era consultada primeiro, ela devolvia na hora em toda grade abaixo do teto de 24
    tiles — isto e, em quase todo projeto — e a identidade saia None SEMPRE. A protecao
    montada depois dos jobs 170/174/189/195 (baixar a referencia achando que era o
    video) nunca chegou a valer: `_identidade_do_card` ficava vazio e `_card` caia na
    posicao, que e exatamente o comportamento que ela existia para substituir.

    A contagem continua sendo sinal VALIDO, so nao pode ser o primeiro: ela e a unica
    resposta quando nao ha retrato ANTES, e a grade cravada em 24 a torna cega.

    Identidade so quando ha `src` DE VERDADE. O placeholder e igual para todo card
    recem-nascido e some quando a miniatura carrega; guardar isso quebrou o job 435.
    """
    conhecidas = set(base)
    novas = [i for i in agora if i not in conhecidas]
    reais = [i for i in novas if not i.startswith(PLACEHOLDER)]
    if reais:
        return True, reais[0]
    # o primeiro tile trocando de identidade tambem e card novo: acontece quando ele
    # entra ainda sem miniatura e nenhuma identidade inedita aparece
    trocou = bool(agora) and bool(base) and agora[0] != base[0]
    return bool(novas) or trocou or contagem > antes, None


async def sem_credito(pagina) -> bool:
    """O aviso de conta zerada — que ainda nao foi visto ao vivo.

    Enquanto o seletor estiver PENDENTE, a resposta e "nao sei", e "nao sei" tem
    que ser False: levantar `SaldoInsuficiente` por causa de um seletor invalido
    marcaria a conta como seca e a tiraria do pool sem nenhuma evidencia.
    Descoberto o rotulo real (Task 14), so `seletores.py` muda.
    """
    if sel.AVISO_SEM_CREDITO in ("", sel.PENDENTE):
        return False
    return await pagina.locator(sel.AVISO_SEM_CREDITO).count() > 0


async def identidades_da_grade(pagina) -> list[str]:
    try:
        return await pagina.evaluate(_IDENTIDADES, sel.CARD_GRADE)
    except Exception:
        # nunca derrubar a geracao por causa da leitura de identidade: sem ela o
        # `_esperar_card_novo` volta a depender da contagem, que ainda funciona
        # abaixo do teto.
        return []


async def esperar_novo(pagina, antes: int, d: Diario,
                       teto_ms: int = 120_000,
                       graca_do_aviso_ms: int = 20_000,
                       ids_antes: list[str] | None = None) -> CardNovo:
    """Espera o card novo aparecer na grade. Devolve SE apareceu, e com que identidade.

    A identidade sai no RETORNO. Enquanto isto era metodo, ela era escrita num campo do
    transporte (`_identidade_nova`) e lida por `_gerar` na linha seguinte — canal
    lateral que so funcionava porque as duas coisas aconteciam juntas.

    QUEM DECIDE E `leitura_da_grade`, e o motivo esta la: a contagem era consultada
    antes da identidade e devolvia na hora, deixando `identidade` em None em toda grade
    abaixo de 24 tiles. O alcance continua limitado — card recem-nascido nao tem `src`,
    e um `src` capturado no nascimento envelhece quando a midia entra —, e por isso a
    conferencia de TIPO em `consultar` e em `baixar` e que e a trava.

    Devolve `bool` em vez de levantar porque quem chama precisa decidir o que a
    ausencia do card significa — recusa por saldo ou falha de envio —, e a
    mensagem certa depende disso.

    O AVISO DE CREDITO NAO DECIDE, ELE ENCURTA O PRAZO. Ver o comentario em
    `_gerar`: o aviso significa "o saldo agora e zero", nao "recusei este pedido",
    e a geracao que gasta o ULTIMO credito produz os dois ao mesmo tempo.

    Mas ele e informacao util: com o aviso na tela e nenhum card, esperar os 120 s
    inteiros e desperdicio. Entao ele passa a valer um prazo curto — tempo de
    sobra para um card que ja estivesse a caminho, e nada perto do teto cheio.
    """
    gasto = 0
    prazo_do_aviso: int | None = None
    base = list(ids_antes) if ids_antes else []
    while gasto < teto_ms:
        # A LEITURA E UMA SO, e a ordem dos sinais mora em `leitura_da_grade`:
        # identidade primeiro, contagem como resposta de quem nao tem retrato ANTES.
        agora = await identidades_da_grade(pagina) if base else []
        contagem = await pagina.locator(sel.CARD_GRADE).count()
        apareceu, identidade = leitura_da_grade(base, agora, contagem, antes)
        if identidade is not None:
            d.passo(f"card novo por identidade (a grade segue com "
                    f"{len(agora)} tiles): {identidade[-28:]}")
            return CardNovo(True, identidade)
        if apareceu:
            # nasceu sem miniatura: nao ha `src` estavel para guardar, e inventar um
            # faria `_card` casar com o tile errado depois. Quem protege daqui em
            # diante e a conferencia de TIPO em `consultar` e em `baixar`.
            d.passo("card novo, ainda sem miniatura: seguindo pela posicao")
            return CardNovo(True, None)
        if prazo_do_aviso is None and await sem_credito(pagina):
            prazo_do_aviso = gasto + graca_do_aviso_ms
            d.passo(f"o aviso de credito zerado apareceu; dou "
                    f"{graca_do_aviso_ms // 1000}s ao card antes de concluir recusa")
        if prazo_do_aviso is not None and gasto >= prazo_do_aviso:
            return CardNovo(False, None)
        await pagina.wait_for_timeout(2000)
        gasto += 2000
        d.esperando("o card novo aparecer na grade")
    return CardNovo(False, None)


async def tipo_do_tile(card) -> str | None:
    """"imagem" | "video" | None, pelo ELEMENTO do tile. Sem abrir menu nenhum.

    MEDIDO 2026-09-06 e anotado no proprio `seletores.py`: o tipo do card tem elemento
    proprio (`flow-image-tile` / `flow-video-tile`), e isso e melhor que a aplicacao
    antiga, onde o tipo precisava ser lembrado por fora. A medicao estava la desde
    entao e NAO ERA USADA EM LUGAR NENHUM.

    O que ela permite e o que faltava: conferir o tipo DURANTE O POLLING. A outra
    leitura de tipo (`analise.tipo_do_card`) le o menu, e menu exige botao direito —
    so da para usa-la na hora de baixar, que e depois de `consultar` ja ter dito
    PRONTO. Foi por esse buraco que passaram os jobs 1114-1116 e 1120-1128
    (2026-09-13): `consultar` deu PRONTO para a imagem de referencia enviada, porque
    tudo que ele exigia era um `<img>` dentro do tile.

    None e "nao sei", e tem que ser: card de falha e card ainda nascendo nao casam
    nenhum dos dois, e chutar ali derrubaria geracao boa.
    """
    if await acoes.quantos(card, sel.CARD_VIDEO) > 0:
        return "video"
    if await acoes.quantos(card, sel.CARD_IMAGEM) > 0:
        return "imagem"
    return None


# --------------------------------------------------------------- consultar
async def achar(pagina, id_remoto: str, conta_id: int | None = None,
                lembradas: dict[tuple[int, str], str] | None = None):
    """O item da grade daquele job. IDENTIDADE primeiro, posicao so no fim.

    `card-0` e posicional, e posicao mente assim que qualquer tile entra na
    frente. No frames2v isso e garantido: os dois quadros de referencia sobem
    ANTES da geracao e viram tiles. Foi assim que os jobs 170/174/189/195
    baixaram um card de imagem achando que era o video — 21 tentativas cada,
    video gerado e pago, nenhum arquivo.

    ⚠️ A IDENTIDADE E UM ATALHO, NAO UMA TRAVA. Quando ela nao aparece na grade,
    cai-se para a posicao — que e o comportamento antigo, e funciona na maioria
    dos casos. A primeira versao LEVANTAVA erro aqui, e derrubou o job 435 com a
    imagem pronta: a identidade guardada tinha virado a miniatura de verdade, e o
    valor velho, claro, nao estava mais la.
    Quem protege contra baixar o card errado e a conferencia de TIPO em `_baixar`
    (menu de imagem x menu de video), que olha o card na hora — nao esta funcao.
    """
    indice = int(id_remoto.split("-")[1])
    cards = pagina.locator(acoes.medido("CARD_GRADE", sel.CARD_GRADE))
    lembradas = {} if lembradas is None else lembradas
    alvo = lembradas.get((conta_id, id_remoto)) if conta_id else None
    if alvo:
        atuais = await identidades_da_grade(pagina)
        if alvo in atuais:
            return cards.nth(atuais.index(alvo))
        # nao achou: o card pode ter ganhado miniatura nova. Segue pela posicao e
        # esquece a identidade velha, que so produziria este mesmo desvio de novo.
        lembradas.pop((conta_id, id_remoto), None)
    if await cards.count() <= indice:
        raise GeracaoFalhou(f"{id_remoto} sumiu da grade")
    return cards.nth(indice)


# Os motivos MEDIDOS de recusa sem cobranca. Sao dois textos e uma decisao so, porque
# a acao que eles pedem e a mesma: trocar de conta. Motivo novo entra aqui.
_MOTIVOS_DE_RECUSA = (sel.CARD_LIMITE_DE_USO, sel.CARD_ATIVIDADE_INCOMUM)


async def motivo_da_recusa(card) -> str | None:
    """Qual motivo de recusa esta no card, para a mensagem dizer a verdade.

    Devolve o seletor que casou, ou None. Existe porque "atingiu o limite de uso" e
    "atividade incomum" pedem a mesma acao mas sao problemas DIFERENTES do ponto de
    vista de quem opera: um passa com o reset do dia, o outro passa afrouxando o
    paralelismo. Reportar os dois com o mesmo texto mandaria olhar o lugar errado.
    """
    for seletor in _MOTIVOS_DE_RECUSA:
        if await acoes.quantos(card, seletor) > 0:
            return seletor
    return None


async def recusa_sem_cobranca(card) -> bool:
    """Se aquele card de falha e RECUSA DA CONTA, e nao falha da geracao.

    Distingue duas coisas que o codigo tratava igual e pedem acoes OPOSTAS:
    falha de geracao pede `redo` na mesma conta; recusa pede trocar de conta,
    porque repetir bate no mesmo bloqueio.

    Exige DUAS marcas — a frase do motivo e a de "nao houve cobranca". Uma so
    seria frágil: "limite" aparece em outros avisos do Flow, e concluir recusa
    por engano tiraria uma conta boa do pool por `ESPERA_RESET` inteiro.

    ⚠️ CHAMAVA-SE `por_limite_de_uso`, E O NOME ESTREITO ERA O DEFEITO. Ele descrevia
    o unico motivo medido ate entao, e quando o Google recusou por OUTRO motivo — o
    antiabuso, "Notamos uma atividade incomum" — nada aqui casou. A recusa virou falha
    comum, o codigo foi tentar clicar em `redo` e morreu no seletor pendente. Jobs
    583-588, 2026-09-09: seis geracoes perdidas, nenhuma cobrada, e o painel dizendo
    que o resultado existia. O que decide e "o Flow recusou E nao cobrou"; o motivo e
    detalhe da mensagem.
    """
    if not await motivo_da_recusa(card):
        return False
    return await acoes.quantos(card, sel.CARD_SEM_COBRANCA) > 0


async def falhou(card) -> bool:
    """Se aquele item da grade e um card de FALHA.

    Duas frases, nao uma. `CARD_FALHA` e a mensagem antiga, casada por texto exato;
    `CARD_FALHA_TITULO` casa o titulo "Falha" sozinho, que e o que aparece nas
    outras. VISTO EM PRODUCAO 2026-08-21: "Falha ao gerar audio. Tente usar outro
    comando ou envie feedback." nao casava a frase antiga, `consultar` nao
    reconhecia a falha, nao clicava no repetir, e devolvia RODANDO ate o timeout de
    dez minutos — para o usuario, "travado".

    Reconhecer pela ESTRUTURA e nao pela frase e o que impede a proxima redacao do
    Google de reabrir o mesmo buraco.
    """
    # SELETOR PENDENTE NAO VAI PARA O PLAYWRIGHT. `sel.PENDENTE` vale "...", que
    # o Playwright interpreta como XPath e recusa com "not a valid XPath
    # expression" — um erro que fala de sintaxe quando o problema e que ninguem
    # mediu o card de falha na aplicacao nova ainda.
    #
    # Sem medicao, a resposta e FALSE: "nao sei dizer que falhou". O custo disso e
    # um card realmente falho parecer RODANDO ate o teto do polling — que era o
    # comportamento antes de alguem medir a frase, e e melhor que dar por falha uma
    # geracao que esta so demorando.
    for seletor in (sel.CARD_FALHA, sel.CARD_FALHA_TITULO):
        if (seletor and seletor != sel.PENDENTE
                and await card.locator(seletor).count() > 0):
            return True
    return False


async def esperar_sair_da_falha(pagina, id_remoto: str, d: Diario,
                                teto_ms: int = 45_000) -> bool:
    """Depois do `redo`, espera o card DEIXAR de ser card de falha.

    Devolve se saiu. `False` nao e erro: e "ainda nao", e quem chama segue o
    polling sem gastar outra repeticao — que era exatamente o gasto que
    transformava uma falha em quatro.

    O teto e generoso porque estourar aqui nao custa nada (o polling continua) e
    encurtar custa a repeticao inteira. O card sai da falha quando o Flow assume a
    nova geracao; isso demora bem mais que os 3 s que estavam aqui.
    """
    gasto = 0
    while gasto < teto_ms:
        await pagina.wait_for_timeout(500)
        gasto += 500
        try:
            card = await achar(pagina, id_remoto)
        except GeracaoFalhou:
            # o card sumiu da grade no meio da troca: isso E sair da falha
            return True
        if not await falhou(card):
            return True
        d.esperando(f"{id_remoto} sair do estado de falha depois do repetir")
    return False
