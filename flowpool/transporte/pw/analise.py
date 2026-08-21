"""As partes PURAS do transporte real: texto entra, decisao sai.

Existe para que a parte do transporte que da para errar em silencio tenha teste. O
resto de `transporte.py` e sequencia de cliques contra o Google e so se verifica
rodando — mas cada uma das decisoes aqui ja custou alguma coisa quando foi tomada
solta no meio de uma corrotina:

* ler saldo de um texto de menu           -> reconciliacao de credito
* tirar o UUID do href do card            -> em qual projeto a geracao acontece
* recusar candidato ambiguo               -> um clipe do usuario foi para a lixeira
* escolher a opcao de download por rotulo -> os rotulos tem QUEBRA DE LINHA
* respeitar `suggested_filename`          -> o banco apontando para arquivo mentiroso
* conferir o texto do chip                -> 4s pedidos que sairam 8s, com credito gasto
* ler o interruptor do Agente             -> ligado, o chip SOME e nada mais casa

Sem rede, sem navegador, sem I/O.
"""

from __future__ import annotations

import re
from pathlib import Path

from flowpool.nucleo import tipos
from flowpool.nucleo.tipos import Modo
from flowpool.transporte.base import GeracaoFalhou
from flowpool.transporte.pw import dom
from flowpool.transporte.pw import seletores as sel


def saldo_do_texto(textos) -> int:
    """Acha "38 Créditos do Google Flow" no meio do menu do avatar e devolve 38.

    Recebe a LISTA de textos curtos que `dom.TEXTOS_CURTOS` devolve, e nao a pagina:
    e assim que a leitura de saldo fica testavel e que a regra 2 continua valendo —
    quem varre o DOM e o `dom.py`, quem interpreta e isto aqui.

    Separador de milhar cai fora antes do `int`: conta paga chega a "1.000 Créditos",
    e `int("1.000")` levanta ValueError longe daqui, no meio da reconciliacao.
    """
    for texto in textos:
        achado = re.search(sel.PADRAO_CREDITOS, texto)
        if achado:
            return int(re.sub(r"[.,]", "", achado.group(1)))
    raise GeracaoFalhou("nao achei o saldo no menu da conta")


def id_do_href(href: str | None) -> str:
    """O UUID do projeto a partir do href do card da lista.

    Devolve "" para href que nao e de projeto — e quem chama pula. Aceitar qualquer
    coisa daria um `id_remoto` que nunca casa com card nenhum, e o sintoma apareceria
    tres passos depois, como "projeto nao esta na lista".
    """
    if not href or sel.PADRAO_PROJETO not in href:
        return ""
    # query e fragmento nao fazem parte do id; um `?ref=...` colado no fim viraria
    # parte do UUID e quebraria o `a[href*="{id}"]` de `abrir_projeto`
    caminho = href.split("?")[0].split("#")[0]
    return caminho.rstrip("/").split("/")[-1]


def exigir_unico(quantos: int, seletor: str, o_que: str) -> None:
    """Zero candidatos e erro; mais de um TAMBEM e erro.

    A metade que costuma faltar e a segunda. Um `.first`/`.last` num menu ambiguo ja
    mandou um video do usuario para a lixeira, porque o ultimo item do menu do card e
    "Mover para a lixeira". Ambiguidade e erro, nao escolha.
    """
    if quantos != 1:
        raise GeracaoFalhou(
            f"{o_que}: esperava 1 candidato para {seletor!r}, achei {quantos}")


def _chaves_do_rotulo(rotulo: str) -> set[str]:
    """As palavras que podem identificar um item de menu: a 1a de cada linha.

    Os rotulos do submenu de download tem QUEBRA DE LINHA — "270p\\nGIF animado" — e
    varios itens deste app trazem a ligadura do icone como primeira linha
    ("download\\nBaixar"). Olhar a primeira palavra de CADA linha cobre os tres
    formatos que ja apareceram (so a ligadura, ligadura + rotulo, rotulo + descricao)
    sem cair no `in` solto, que casaria "2K" dentro de "12K".
    """
    chaves = set()
    for linha in dom.sem_acento(rotulo).splitlines():
        partes = linha.split()
        if partes:
            chaves.add(partes[0])
    return chaves


def escolher_opcao(rotulos: list[str], chave: str) -> int:
    """Indice do item cujo rotulo corresponde a `chave`. Empate e ERRO.

    Devolve POSICAO, mas nao escolhe POR posicao: a posicao e so como o Playwright
    endereca o item que o TEXTO ja identificou, e a unicidade e exigida antes. E a
    diferenca entre isto e o `.last` que esvaziou um clipe na lixeira.

    Rotulo inteiro nao entra em seletor CSS: "270p\\nGIF animado" estoura o parser.
    Por isso a escolha acontece aqui, sobre o texto ja lido.
    """
    alvo = dom.sem_acento(chave).strip()
    achados = [i for i, r in enumerate(rotulos) if alvo in _chaves_do_rotulo(r)]
    exigir_unico(len(achados), f"rotulo {chave!r}", "opcao de menu")
    return achados[0]


def caminho_final(destino: Path | str, sugerido: str) -> Path:
    """O caminho onde o arquivo baixado realmente vai morar.

    O Flow escolhe a extensao: `.jpeg` para imagem (nao `.png`), `.mp4` para video. O
    orquestrador montou `destino` a partir do modo — salvar ali gravaria no banco um
    `.png` que por dentro e JPEG, e ninguem descobriria ate um consumidor engasgar.
    Nome e pasta continuam sendo os nossos: `suggested_filename` traz o prompt
    inteiro, e nao e disso que a pasta do usuario precisa.
    """
    destino = Path(destino)
    sufixo = Path(sugerido).suffix if sugerido else ""
    return destino.with_suffix(sufixo or destino.suffix)


def exigir_proporcao_do_modo(modo: str, proporcao: str) -> None:
    """Recusa ANTES de tocar no chip, com a reserva de credito ainda estornavel.

    MEDIDO: em video o painel oferece so 9:16 e 16:9. Pedir 1:1 em video nao acha
    botao — e o comportamento antigo era seguir em frente e gerar em 9:16 sem avisar.
    """
    permitidas = sel.PROPORCOES_POR_MODO[modo]
    if proporcao not in permitidas:
        raise GeracaoFalhou(
            f"modo {modo} aceita so {permitidas}, e o job pediu {proporcao}")


def faltando_no_chip(chip: str, modo: str, proporcao: str, quantidade: int,
                     duracao: str | None) -> list[str]:
    """O que o texto do chip NAO confirma. Lista vazia e o unico resultado aceitavel.

    "Configurou, confere" mora aqui. A versao tolerante disto — `except Exception:
    print("sem efeito — seguindo")` — engoliu um clique de duracao que nao pegou e
    entregou 8s onde foram pedidos 4s, com credito debitado e arquivo na pasta. O
    valor de retorno e uma lista, e nao um bool, porque a mensagem de erro precisa
    dizer O QUE nao bateu.

    `chip` chega ja normalizado por `dom.seguro` (sem acento, sem segredo, numa linha).
    O MODELO nao entra: ele nao aparece no texto do chip, e e conferido no painel.
    """
    alvos = [
        sel.LIGADURA_PROPORCAO[proporcao],
        f"x{quantidade}",
    ]
    if duracao:
        alvos.append(duracao)
    faltou = [a for a in alvos if a not in chip]

    aceitos = sel.ROTULO_MODO_NO_CHIP[modo]
    if aceitos:
        # QUALQUER um dos marcadores confirma. Nos submodos de video o mais fraco e o
        # rotulo do pai ("video"), porque nao esta medido se o chip nomeia "Frames" e
        # "Elementos" — ver a nota em `seletores.ROTULO_MODO_NO_CHIP`.
        if not any(r in chip for r in aceitos):
            faltou.insert(0, aceitos[0])
        return faltou
    # Imagem nao se anuncia no chip — o lugar do nome do modo fica com o do modelo. Mas
    # NAO verificar seria abrir o buraco que esta funcao existe para fechar: um chip que
    # continuou em video passaria como imagem. Confirma-se por AUSENCIA: nenhum marcador
    # dos outros modos pode estar la.
    intrusos = [rs[0] for m, rs in sel.ROTULO_MODO_NO_CHIP.items()
                if rs and m != modo and any(r in chip for r in rs)]
    if intrusos:
        return [f"modo {modo} (o chip ainda diz {intrusos[0]!r})"]
    return faltou


def confirmacao_fraca(chip: str, modo: str) -> str | None:
    """Aviso quando o chip so confirmou o modo pelo rotulo do PAI.

    Existe para a tolerancia dos submodos nao ser calada. Quando `frames2v` passa
    porque o chip diz "video" — e nao "frames" —, a corrida imprime esta linha, e ela e
    justamente a medicao que falta: se ela nunca aparecer, o chip nomeia os submodos e
    a tolerancia pode sair de `seletores.py`; se aparecer sempre, o chip nao os nomeia e
    a tolerancia e a unica verificacao possivel.
    """
    aceitos = sel.ROTULO_MODO_NO_CHIP[modo]
    if len(aceitos) < 2 or aceitos[0] in chip:
        return None
    fraco = next((r for r in aceitos[1:] if r in chip), None)
    if not fraco:
        return None
    return (f"modo {modo}: o chip nao diz {aceitos[0]!r}, so {fraco!r} — a troca vale, "
            f"mas o rotulo do submodo continua POR MEDIR (seletores.ROTULO_MODO_NO_CHIP)")


def etapas_ate_o_modo(modo: str, visivel: bool) -> list[str]:
    """Em quais botoes de modo clicar, na ordem, para chegar em `modo`.

    MEDIDO 2026-08-20: a lista de modos do painel depende do modo atual. Com o chip em
    imagem so existem `image` e `videocam`; `crop_free` e `chrome_extension` aparecem
    depois que o painel esta em video. Clicar direto no submodo vindo de imagem e
    clicar no vazio — foi o que fez um job real falhar tres vezes com
    "esperava 1 candidato para 'button:has-text(\"chrome_extension\")', achei 0".

    `visivel` e o que o transporte contou no painel ABERTO. Quando o alvo ja esta la,
    um clique basta e nao se forca dois: cada clique extra e mais uma animacao e mais
    uma chance de o painel redesenhar no meio.
    """
    if visivel:
        return [modo]
    pai = sel.MODO_PAI.get(modo)
    if not pai:
        raise GeracaoFalhou(
            f"modo {modo} nao esta no painel e nao e submodo de nenhum outro — se o "
            f"botao sumiu de vez, o conserto e em seletores.MODO_ICONE")
    return [pai, modo]


def agente_ligado(pressed: str | None) -> bool:
    """O interruptor "Agente" da barra do prompt esta ligado? True = precisa clicar.

    MEDIDO 2026-08-21, os dois estados: o botao carrega `aria-pressed`, e ele vale
    "true" ligado e "false" desligado. O atributo EXISTE nos dois — nao e ausencia
    contra presenca, e e isso que torna o terceiro caso abaixo diagnostico em vez de
    normal.

    Ligado, o Flow troca de modo de operacao: o prompt vai para um agente
    conversacional que pergunta antes de gastar, e o CHIP SOME DA BARRA (medido:
    `CHIP_CONFIG` casa zero). O transporte inteiro depende do chip, entao ligado nao e
    "um pouco diferente", e sim outro aplicativo.

    MARCADOR DESCONHECIDO E ERRO, NAO "DESLIGADO". Tratar `None` como desligado seria
    reabrir exatamente o defeito que esta funcao fecha: num deploy que troque o
    atributo, toda geracao voltaria a rodar com o agente possivelmente ligado, e o
    sintoma seria de novo um timeout falando do chip. Falhar aqui nomeia a causa e
    acontece antes de qualquer credito sair.
    """
    if pressed == sel.AGENTE_LIGADO:
        return True
    if pressed == sel.AGENTE_DESLIGADO:
        return False
    raise GeracaoFalhou(
        f"nao da para saber se o Agente esta ligado: {sel.ATRIBUTO_AGENTE} vale "
        f"{pressed!r}, e o medido e {sel.AGENTE_LIGADO!r} ou {sel.AGENTE_DESLIGADO!r}. "
        f"Rode `python -m flowpool.transporte.pw.conferir <perfil> <uuid>` e reveja "
        f"seletores.BOTAO_AGENTE")


def exigir_agente_desligado(pressed: str | None) -> None:
    """O veredito DEPOIS do clique: continuou ligado e ERRO, nao aviso.

    Seguir em frente com o agente ligado nao da erro na hora — da um timeout tres
    passos depois, falando do chip que o proprio agente tirou da tela. E o job teria
    gastado a tentativa para descobrir isso.
    """
    if agente_ligado(pressed):
        raise GeracaoFalhou(
            "cliquei no interruptor do Agente e ele continuou LIGADO "
            f"({sel.ATRIBUTO_AGENTE}={pressed!r}). Com o agente ligado o chip nao "
            f"existe na barra, e configurar a geracao e impossivel")


def custo_do_texto(texto: str) -> int:
    """"A geração vai usar 12 créditos" -> 12.

    MEDIDO 2026-08-20: o painel do chip anuncia o custo EXATO antes de gerar. Isso
    derruba a conclusao anterior de que so o agente conversacional sabia o preco.

    Aceita acentuado e sem acento pelo mesmo motivo de `saldo_do_texto`: o texto passa
    por `dom.seguro` antes de ser impresso, e um caminho que le o valor ja normalizado
    nao pode deixar de casar.

    Zero e resposta valida — e justamente o caso da imagem, que e gratuita. Confundir
    zero com "nao achei" faria a leitura parecer quebrada exatamente onde ela esta
    certa, e e por isso que a ausencia levanta em vez de devolver 0.
    """
    achado = re.search(sel.PADRAO_CUSTO, texto)
    if not achado:
        raise GeracaoFalhou(f"nao achei o custo anunciado em {texto!r}")
    return int(re.sub(r"[.,]", "", achado.group(1)))


def nome_do_item(texto: str) -> str:
    """O nome do item da lista de midia: a PRIMEIRA linha do rotulo.

    MEDIDO: cada `[role=option]` do seletor de midia rende duas linhas — o nome na
    primeira e o tipo na segunda ("folha-frames.png" / "Imagem"). Item enviado leva o
    nome do arquivo; item gerado leva o inicio do proprio comando.
    """
    linhas = (texto or "").strip().splitlines()
    return linhas[0].strip() if linhas else ""


def item_pronto(item: dict) -> bool:
    """O upload terminou quando o item GANHA MINIATURA.

    MEDIDO 2026-08-20 com um arquivo de 2,9 MB, amostrado de 250 em 250 ms: o item
    entra na lista imediatamente, sem miniatura, e so aos 17,25 s a imagem aparece.
    Os 3 s fixos da versao anterior cobriam 500 KB e nao cobrem isso — e estourar o
    prazo aqui nao dava erro, dava referencia vazia com credito gasto.

    A porcentagem que corre na tela nao serve: ela mora no card do ACERVO, fora do
    seletor, e some ANTES de a miniatura chegar.
    """
    return item.get("miniaturas", 0) > 0


def escolher_item_enviado(itens: list[dict], nome: str) -> int | None:
    """Indice do item que e o arquivo recem-enviado. `None` = ainda nao esta pronto.

    `None` e "espere mais", nao "falhou": quem chama fica no laco ate o teto e so
    entao levanta, com uma mensagem que sabe dizer se o item nem apareceu ou se
    apareceu e nao ficou pronto.

    Ambiguidade AQUI nao pode ser erro, e essa e a diferenca em relacao a
    `escolher_opcao`. Enviar o MESMO arquivo duas vezes deixa dois itens de mesmo nome
    na lista — foi medido, com dois `blusa.png` —, e recusar isso travaria a segunda
    geracao de qualquer produto. O desempate e medido, nao arbitrado:

      1. o item que o Flow ja deixou SELECIONADO e o que acabou de subir (ele
         auto-seleciona o recem-enviado);
      2. sem nenhum selecionado, vale o primeiro — a lista sai ordenada por
         "Recentes", e o novo entra no topo.
    """
    candidatos = [i for i, x in enumerate(itens) if nome_do_item(x.get("texto", "")) == nome]
    if not candidatos:
        return None
    prontos = [i for i in candidatos if item_pronto(itens[i])]
    if not prontos:
        return None
    selecionados = [i for i in prontos if itens[i].get("selecionado")]
    return selecionados[0] if selecionados else prontos[0]


def arquivos_do_pedido(p) -> list[str]:
    """Quais arquivos este pedido anexa, na ordem, ja com o teto do modo aplicado.

    Vive aqui, e nao dentro da corrotina que clica, porque e a decisao que ja errou
    duas vezes: uma descartando a referencia da IMAGEM sem avisar (o pipeline do
    usuario COMECA por ela — foto da roupa -> modelo vestida), outra deixando frame
    inicial e final vazarem para modos que nao os aceitam.

    Quem decide o que cada arquivo SIGNIFICA e o modo ja escolhido no chip, nao o
    seletor: em `crop_free` viram frame inicial e final; em `chrome_extension` e em
    imagem viram referencias. Por isso `_configurar` roda antes de `_anexar`.
    """
    if p.modo is Modo.FRAMES2V:
        return [str(Path(x)) for x in (p.frame_inicial, p.frame_final) if x]
    if not tipos.aceita_referencia(p.modo):
        return []
    return [str(Path(r)) for r in p.referencias[:tipos.max_referencias(p.modo)]]


def indice_por_sufixo(rotulos, alvo: str) -> int:
    """Acha o item cujo rotulo TERMINA em `alvo`. Recusa empate e ausencia.

    Existe porque nenhum seletor CSS do Playwright da conta do menu de modelos, e
    MEDIDO em 2026-08-20 e o que prova cada tentativa:

      textos crus: '\U0001f34c Nano Banana Pro', '\U0001f34c Nano Banana 2', '\U0001f34c Nano Banana 2 Lite'
        :text-is("\U0001f34c Nano Banana 2 Lite")  -> 0   (o emoji atrapalha)
        :text-matches("Nano Banana 2 Lite$")       -> 0   (nao ancora como se espera)
        :has-text("Nano Banana 2 Lite")            -> 1   (casa, mas...)
        :has-text("Nano Banana 2")                 -> 2   (...pega o Lite junto)

    O prefixo muda por familia — emoji na imagem, ligadura `volume_up` mais quebra de
    linha no video —, entao correspondencia exata e inutil; e "Nano Banana 2" e prefixo
    de "Nano Banana 2 Lite", entao substring e ambigua. Ancorar o FIM resolve as duas
    coisas, e em Python o ancoramento funciona.

    Mesmo padrao que `escolher_opcao` usa no submenu de download: ler os rotulos e
    escolher aqui, onde da para testar, em vez de espremer a regra num seletor.
    """
    achados = [
        i for i, bruto in enumerate(rotulos)
        if (bruto or "").strip().splitlines()[-1].strip().endswith(alvo)
    ]
    if len(achados) != 1:
        raise GeracaoFalhou(
            f"modelo {alvo!r}: esperava 1 rotulo terminando nele, achei "
            f"{len(achados)} em {list(rotulos)!r}")
    return achados[0]
