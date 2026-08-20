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

Sem rede, sem navegador, sem I/O.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    rotulo_modo = sel.ROTULO_MODO_NO_CHIP[modo]
    if rotulo_modo:
        alvos.insert(0, rotulo_modo)
    else:
        # Imagem nao se anuncia no chip — o lugar do nome do modo fica com o do modelo.
        # Mas NAO verificar seria abrir o buraco que esta funcao existe para fechar: um
        # chip que continuou em video passaria como imagem. Confirma-se por AUSENCIA:
        # nenhum marcador dos outros modos pode estar la.
        intrusos = [r for m, r in sel.ROTULO_MODO_NO_CHIP.items()
                    if r and m != modo and r in chip]
        if intrusos:
            return [f"modo {modo} (o chip ainda diz {intrusos[0]!r})"]
    if duracao:
        alvos.append(duracao)
    return [a for a in alvos if a not in chip]


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
