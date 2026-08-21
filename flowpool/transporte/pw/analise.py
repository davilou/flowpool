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


def id_da_midia(src: str | None) -> str:
    """O id da midia dentro do `src` da miniatura. `""` quando nao ha.

    MEDIDO 2026-08-21:
        /fx/api/trpc/media.getMediaUrlRedirect?name=166c3d5d-e7d7-45ad-8453-e9a9053dfd6a
    e, em item de VIDEO, com um parametro colado no fim:
        ...?name=c474ed85-...&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL

    E por isso que se EXTRAI o `name` em vez de comparar o `src` inteiro: o src cru de
    um video nunca casaria com o src da ficha do mesmo video.

    `""` para o que nao tem id — item ainda sem miniatura, por exemplo. Quem chama
    trata isso como "ainda nao da para identificar", nunca como um id valido; ids
    vazios que se comparassem iguais fariam dois itens diferentes parecerem o mesmo,
    que e exatamente a classe de erro que este id existe para fechar.
    """
    achado = re.search(sel.PADRAO_ID_MIDIA, src or "")
    return achado.group(1) if achado else ""


def ids_dos_itens(itens: list[dict]) -> set[str]:
    """Os ids de uma lista de itens, sem os vazios. E o "retrato antes do envio"."""
    return {i for i in (id_da_midia(x.get("src")) for x in itens) if i}


def ids_selecionados(itens: list[dict]) -> list[str]:
    """Os ids dos itens marcados agora. A selecao do seletor de midia e EXCLUSIVA,
    entao o normal e zero ou um — mais que isso e a interface ter mudado."""
    return [i for i in (id_da_midia(x.get("src")) for x in itens if x.get("selecionado")) if i]


def escolher_item_enviado(itens: list[dict], nome: str,
                          ids_antes: set[str] | None = None) -> str | None:
    """O ID do item recem-enviado. `None` = ainda nao esta pronto, espere mais.

    DEVOLVE ID, NAO POSICAO. Foi um indice calculado numa varredura e usado num clique
    depois que deixou o transporte clicando no item errado: a lista se REORDENA (o
    recem-enviado sobe ao topo) e ENCOLHE (o incluido sai dela), entao a posicao de
    ontem e outro item hoje.

    `ids_antes` E O RETRATO DA LISTA ANTES DO ENVIO, e e a peca que conserta o defeito
    de 21/08. Sem ele esta funcao escolhia PELO NOME, e o nome nao identifica nada:

      O acervo do projeto PERSISTE ENTRE JOBS. Quando o mesmo arquivo ja foi enviado
      num job anterior — e ele quase sempre foi, porque os arquivos vem de `_coladas/`
      com nome por hash e a personagem se reaproveita entre gerações — existe na lista
      um HOMONIMO VELHO que JA TEM MINIATURA. Ele passava em `item_pronto` no primeiro
      instante, e a espera pelo upload terminava sem ter esperado nada.

      Dai vinha a cadeia inteira que se via em producao: "Incluir no comando" clicado
      com o upload ainda em voo, a lista se reordenando no meio, a interface
      engasgando, o botao `+` mudando de lugar, e a segunda referencia virando a foto
      errada — ou nenhuma. Como a personagem reaproveitada e quase sempre a SEGUNDA
      referencia, travava sempre na 2, e "as vezes" era so o acaso de o arquivo ja
      estar no acervo ou nao.

      Pior: a propria REPETICAO fabricava a condicao. A tentativa 0 subia o arquivo A
      e morria na B; a tentativa 1 subia A de novo, e agora havia um homonimo pronto
      para a espera devolver na hora. Foi assim que um job saiu "pronto" com as fotos
      trocadas — resultado errado, em silencio, com credito gasto.

    Com o retrato, so podem ser o recem-enviado os itens que APARECERAM depois dele.

    `None` continua sendo "espere mais", nao "falhou": quem chama fica no laco ate o
    teto e so entao levanta, com uma mensagem que sabe dizer se o item nem apareceu ou
    se apareceu e nao ficou pronto.

    Ambiguidade AQUI nao vira erro, ao contrario de `escolher_opcao`. Dois uploads do
    mesmo nome DENTRO desta mesma espera nao acontecem — sobe-se um arquivo por vez —,
    mas se acontecerem o desempate e o medido: o Flow deixa o recem-enviado
    SELECIONADO, e sem nenhum selecionado vale o primeiro, porque a lista sai por
    "Recentes" e o novo entra no topo.
    """
    ids_antes = set() if ids_antes is None else ids_antes
    novos = [x for x in itens
             if (id := id_da_midia(x.get("src"))) and id not in ids_antes]
    candidatos = [x for x in novos if nome_do_item(x.get("texto", "")) == nome]
    if not candidatos:
        return None
    # `item_pronto` so vale sobre o item que EU criei. Aplicado a um homonimo velho ele
    # nao prova nada sobre o meu upload — era essa a confusao.
    prontos = [x for x in candidatos if item_pronto(x)]
    if not prontos:
        return None
    marcados = [x for x in prontos if x.get("selecionado")]
    return id_da_midia((marcados[0] if marcados else prontos[0]).get("src"))


def apareceu_na_lista(itens: list[dict], nome: str,
                      ids_antes: set[str] | None = None) -> bool:
    """Se um item NOVO com aquele nome ja esta na lista, pronto ou nao.

    Separa as duas mensagens de erro que interessam quando a espera estoura: "o item
    nem apareceu" (o envio nao pegou) e "apareceu e nao ficou pronto" (upload lento).
    Olha so os NOVOS pelo mesmo motivo de `escolher_item_enviado`: um homonimo velho
    no acervo faria o erro dizer "apareceu" sobre um item que nao e o meu.

    ITEM SEM ID CONTA COMO NOVO, e essa e a diferenca em relacao a
    `escolher_item_enviado`. MEDIDO 2026-08-21: o item entra na lista SEM miniatura —
    e o id mora no `src` da miniatura, entao enquanto o upload corre o item nao tem
    id nenhum. Exigir id aqui faria a mensagem de erro dizer "nao apareceu" em todo
    upload lento, mandando investigar envio que nao pegou onde o arquivo estava
    subindo. E um item sem id nao pode ser um dos velhos: os velhos foram retratados
    justamente pelos ids que tinham.

    `escolher_item_enviado` continua exigindo id, e deve: ali o valor de retorno E o
    id, e sem miniatura o item nao esta pronto de qualquer forma.
    """
    ids_antes = set() if ids_antes is None else ids_antes
    return any(nome_do_item(x.get("texto", "")) == nome
               and id_da_midia(x.get("src")) not in ids_antes
               for x in itens)


def exigir_selecao(marcados: list[str], esperado: str, nome: str) -> None:
    """Antes de "Incluir no comando": o item marcado tem que ser EXATAMENTE o meu.

    "Incluir no comando" age sobre o que estiver SELECIONADO, e nao sobre o item que o
    codigo julga ter escolhido. Enquanto ninguem conferia isso, os dois podiam divergir
    e a divergencia entrava na geracao sem sintoma nenhum.
    """
    if marcados != [esperado]:
        raise GeracaoFalhou(
            f"{nome}: ia incluir com a selecao errada — marcado {marcados}, "
            f"esperado [{esperado!r}]. 'Incluir no comando' age sobre o que esta "
            f"marcado, entao isso anexaria o arquivo errado sem erro nenhum")


def exigir_referencia_incluida(antes: list[str], depois: list[str], esperado: str,
                               nome: str) -> None:
    """A BARREIRA entre uma referencia e a proxima: entrou UMA, e foi a minha.

    Conferida a cada referencia, e nao so no fim. Tres coisas de uma vez:
      * fecha a corrida — a proxima so comeca depois desta ter chegado na barra;
      * da tempo de o botao `+` reassentar, porque a barra do prompt CRESCE a cada
        ficha e o `+` muda de lugar (ver `_abrir_seletor_midia`);
      * transforma "sumiu em silencio" em erro no ponto certo, com o nome do arquivo
        que sumiu, em vez de um "pedi 2 e vejo 1" no fim que nao diz qual.
    """
    novos = list(depois)
    for id_ in antes:
        if id_ in novos:
            novos.remove(id_)
    if novos == [esperado]:
        return
    if not novos:
        raise GeracaoFalhou(
            f"{nome}: cliquei em 'Incluir no comando' e nenhuma ficha nova apareceu "
            f"na barra do prompt (continuam {len(depois)})")
    raise GeracaoFalhou(
        f"{nome}: a ficha que entrou na barra nao e a do arquivo enviado — entrou "
        f"{novos}, esperado [{esperado!r}]")


def exigir_referencias_completas(ids: list[str], esperados: list[str]) -> None:
    """A conferencia final: QUAIS entraram, nao quantas.

    A versao por contagem aprovava DUAS FICHAS IGUAIS — foi assim que um job real saiu
    com a mesma foto duas vezes, sem erro, com o credito gasto e um resultado que
    parecia plausivel. O usuario so descobriu conferindo a conta.

    As tres condicoes sao independentes e cada uma pega um defeito diferente:
      * quantidade — alguma referencia nao entrou
      * repetida   — a MESMA midia entrou duas vezes (o defeito de 21/08)
      * trocada    — entrou midia que nao e de nenhum dos arquivos enviados
    """
    if len(ids) != len(esperados):
        raise GeracaoFalhou(
            f"anexo nao confirma: pedi {len(esperados)} referencia(s) e a barra do "
            f"prompt mostra {len(ids)}")
    if len(set(ids)) != len(ids):
        raise GeracaoFalhou(
            f"anexo REPETIDO: a barra do prompt mostra a mesma midia mais de uma vez "
            f"({ids}). A geracao sairia com o mesmo arquivo no lugar de dois")
    if set(ids) != set(esperados):
        raise GeracaoFalhou(
            f"anexo TROCADO: a barra do prompt mostra {sorted(ids)}, e os arquivos "
            f"enviados foram {sorted(esperados)}")


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
