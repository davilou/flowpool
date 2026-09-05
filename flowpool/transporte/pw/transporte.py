"""Implementacao real do FlowTransport.

Sequencias medidas ao vivo em 2026-08-20. Seletor NENHUM mora aqui: quando o Google
mexer no layout, o conserto e em `seletores.py` e o diagnostico e `conferir.py`.
Decisao pura nenhuma mora aqui tambem: parsing e escolha estao em `analise.py`, que
tem teste. O que sobra neste arquivo e a SEQUENCIA — e sequencia so se verifica
rodando, pelos scripts de fumaca.
"""

from __future__ import annotations

import re
from pathlib import Path

from flowpool.nucleo import tipos
from flowpool.nucleo.tipos import Conta, Modo
from flowpool.transporte.base import (
    Estado, GeracaoFalhou, Pedido, ProjetoRemoto, SaldoInsuficiente, Sessao, SessaoMorta,
)
from flowpool.transporte.pw import analise, dom
from flowpool.transporte.pw.cura import AutoCura
from flowpool.transporte.pw.diario import (
    Diario, descartar_traco, guardar_traco, iniciar_traco, proximo_traco,
)
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.sessao import abrir_contexto, interpretar_sessao

# Chave de `sel.BAIXAR` por modo — a resolucao pedida no menu de DOWNLOAD.
# Medido 2026-09-05 num resultado de imagem: 1k / 2k / 4k. O de video falta medir.
RESOLUCAO = {
    Modo.IMAGEM: "1k",          # "1k / tamanho original"
    Modo.T2V: "720p",           # NAO MEDIDO na aplicacao nova
    Modo.FRAMES2V: "720p",
    Modo.INGREDIENTS2V: "720p",
}

# A resolucao de GERACAO de video, no painel do chip. Grupo novo em 2026-09-05.
#
# O padrao do Flow virou 360p. A aplicacao antiga sempre entregou 720p porque era o
# unico valor que existia — entao NAO tocar aqui rebaixaria em silencio todo video do
# usuario. 720p mantem o que ele ja tinha.
#
# Isto e uma constante e nao um campo do pedido porque `Job` ainda nao tem a coluna.
# Vira escolha por job quando a API expuser o campo — a lista esta em `sel.RESOLUCOES`.
RESOLUCAO_VIDEO = "720p"
# Quantas vezes repetir um card falho pelo proprio botao `redo`, que NAO cobra de novo.
#
# MEDIDO 2026-08-21: um caso real (falha ao gerar audio no Omni Flash) precisou de 2
# repeticoes — saiu na terceira. O teto de 2 batia EXATAMENTE nesse caso e nao tinha
# margem nenhuma: uma falha que precisasse de tres faria o app desistir de algo que
# teria funcionado.
#
# A conta e assimetrica, e e ela que manda no numero:
#   * repetir pelo card custa SO TEMPO. O proprio Flow escreve "Nao houve cobranca por
#     esta geracao" na mensagem de falha.
#   * desistir cedo custa o job inteiro: o orquestrador gasta uma tentativa, reabre
#     navegador, reconfigura o chip, re-anexa as referencias e regenera — muito mais
#     caro do que clicar de novo num botao gratuito.
#   * e esta classe de falha e transitoria por natureza, o tipo de coisa que some
#     sozinha na volta seguinte.
#
# O teto continua existindo porque falha que persiste quatro vezes nao e transitoria:
# e problema de verdade, e ai o job tem que falhar de vez e aparecer para a pessoa.
MAX_REPETICOES = 4

# Quantas leituras seguidas SEM midia e SEM porcentagem antes de dar um card por
# parado. Com o polling do orquestrador a cada ~4 s, 15 voltas sao ~1 minuto.
#
# O numero e assimetrico de proposito. Desistir cedo mata geracao boa — um card
# recem-criado passa alguns instantes sem sinal nenhum, e o proprio Flow leva uns
# segundos para comecar a contar a porcentagem. Desistir tarde custa so tempo de
# polling, e o teto de dez minutos do orquestrador continua atras como rede.
VOLTAS_SEM_PROGRESSO = 15


def _medido(nome: str, seletor: str) -> str:
    """Recusa usar um seletor que ainda nao foi medido ao vivo.

    `sel.PENDENTE` vale "..." — mandar isso para o Playwright estoura com "unknown
    engine ..." no meio de `gerar`, e a mensagem manda procurar no lugar errado. Aqui
    o erro nomeia o seletor e o que fazer, e acontece ANTES de qualquer clique que
    gaste credito.
    """
    if not seletor or seletor == sel.PENDENTE:
        raise GeracaoFalhou(
            f"seletor {nome} ainda nao foi medido ao vivo (esta como {sel.PENDENTE!r}). "
            f"Rode `python -m flowpool.transporte.pw.conferir <perfil> <uuid>`, anote o "
            f"valor real em seletores.py e repita.")
    return seletor


def _pagina_de(escopo):
    """A pagina por tras de um escopo de clique.

    `_clicar_unico` recebe ora a pagina, ora um localizador (o painel do chip, o menu
    do card, um item da grade) — e a cura precisa da PAGINA para tirar print e varrer
    o DOM, enquanto a validacao continua acontecendo no escopo estreito. Localizador
    expoe `.page`; a pagina nao expoe, e e ela mesma.
    """
    return getattr(escopo, "page", escopo)


class TransportePlaywright:
    def __init__(self, timeout_ms: int = 600_000, cura: AutoCura | None = None) -> None:
        self.timeout_ms = timeout_ms
        # A auto-cura vive UMA por transporte, e nao uma por clique, porque o teto de
        # curas e por execucao: e o contador dela que separa "o Google mexeu num botao"
        # de "o Google refez a tela", e um contador que renasce a cada clique nao
        # separaria nada. Ela se desliga sozinha sem modelo respondendo.
        self.cura = cura if cura is not None else AutoCura()
        # As duas tabelas abaixo sao por (CONTA, card), nunca so por card.
        #
        # `id_remoto` e POSICIONAL — `card-0` e "o primeiro item da grade daquela
        # conta". Com geracoes simultaneas em contas diferentes, duas delas produzem
        # `card-0` ao mesmo tempo, e uma chave so por card faria um job baixar a
        # resolucao escolhida pelo outro (video em "Tamanho original", imagem em
        # "720p") e contar as repeticoes do vizinho. O par com o id da conta separa,
        # e as tres chamadas que precisam disso — `gerar`, `consultar`, `baixar` —
        # recebem a sessao, entao o id da conta esta sempre a mao.
        self._repeticoes: dict[tuple[int, str], int] = {}
        # leituras seguidas sem sinal de progresso, por card
        self._parado: dict[tuple[int, str], int] = {}
        # `baixar` recebe so o id_remoto, e o menu de resolucao e diferente entre
        # imagem e video. Guardar o modo no momento de gerar e mais barato do que
        # adivinhar pelo DOM — e adivinhar errado baixaria a resolucao errada calado.
        self._modo_do_card: dict[tuple[int, str], Modo] = {}
        # O log de passo e o traco sao POR CONTA pelo mesmo motivo das duas tabelas
        # acima: ate cinco geracoes correm ao mesmo tempo, uma por conta, e um diario
        # por transporte misturaria os relogios de cinco jobs num so.
        self._diarios: dict[int, Diario] = {}
        # quem esta com gravacao ligada. Sai do conjunto quando o traco e guardado (na
        # falha) ou descartado (no fim), e e o que impede tentar parar duas vezes.
        self._tracando: set[int] = set()

    def _diario(self, conta_id: int) -> Diario:
        """O diario daquela conta. NUNCA None.

        `setdefault` em vez de exigir sessao aberta porque os scripts de fumaca e os
        testes chamam metodos internos direto, sem passar por `abrir_sessao`. Um
        `None` a ser checado em cada ponto de log espalharia `if` por toda a sequencia
        para proteger uma linha de print.
        """
        return self._diarios.setdefault(conta_id, Diario(conta_id))

    async def _traco_da_falha(self, s: Sessao) -> None:
        """Guarda o traco AGORA, no momento da falha, e para de gravar.

        No momento da falha, e nao em `fechar`, porque a falha que motivou tudo isto e
        um navegador que morre: em `fechar` o contexto ja pode nao existir para coletar
        o que gravou. Aqui ele ainda tem chance.

        Nunca levanta — chamada de dentro de um `except`, e um erro daqui substituiria
        a excecao de verdade por uma sobre gravacao de arquivo.
        """
        if s.conta.id not in self._tracando:
            return
        self._tracando.discard(s.conta.id)
        try:
            await guardar_traco(s.handle[1], proximo_traco(s.conta.id))
        except Exception as e:
            print(f"traco: {type(e).__name__} ao guardar - seguindo")

    # ------------------------------------------------------------------ sessao
    async def abrir_sessao(self, conta: Conta) -> Sessao:
        # o proxy e DA CONTA, nao do processo: e assim que cada conta sai por um IP
        # proprio e o Google nao consegue correlacionar as vinte entre si. Vazio e o
        # normal hoje, e `abrir_contexto` trata None como "sem proxy".
        pw, ctx = await abrir_contexto(Path(conta.perfil), conta.proxy)
        # o relogio do log de passo comeca aqui: e o instante em que a conta passa a
        # ocupar um navegador, e e desse total que o usuario reclama quando "travou"
        self._diarios[conta.id] = d = Diario(conta.id)
        # a gravacao comeca ANTES da primeira acao. Um traco que so pega o fim nao
        # responde a pergunta que se faz dele, que e o que a tela mostrava ANTES
        if await iniciar_traco(ctx):
            self._tracando.add(conta.id)
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        try:
            # A PROVA DE SESSAO VIVA VOLTOU A SER A TELA. A aplicacao antiga tinha
            # `/fx/api/auth/session`, e o comentario aqui dizia, com razao para
            # aquele DOM, que a API era melhor que a tela — deslogado o Flow devolvia
            # `{}` com HTTP 200 e a tela demorava a trocar.
            #
            # MEDIDO 2026-09-05: a rota nao existe na aplicacao nova; a autenticacao e
            # cookie do Google. Enquanto `URL_SESSAO` ficou PENDENTE ("..."), esta
            # linha pedia `https://flow.google.com...` — host invalido — e devolvia
            # `getaddrinfo ENOTFOUND` em TODA conta, inclusive as logadas.
            #
            # O sinal e o AVATAR, e nao a lista de projetos: conta sem projeto nenhum
            # tem lista vazia e continua viva. Ver `sessao.validar_perfil`.
            await pagina.locator(sel.AVATAR_CONTA).first.wait_for(timeout=45_000)
        except Exception:
            motivo = (
                f"{conta.email} caiu no portao de idade do Flow: a conta Google "
                "precisa de verificacao de idade. Revalidar NAO resolve"
                if sel.URL_PORTAO_IDADE in pagina.url else
                f"nao achei o avatar em {pagina.url} - {conta.email} parece deslogada")
            # sessao caida NAO rende traco: nao e defeito de sequencia, e um login que
            # expirou, e o diagnostico e uma linha no evento. Guardar um zip de alguns
            # MB por conta expirada encheria o disco pelo motivo mais banal possivel.
            self._tracando.discard(conta.id)
            self._diarios.pop(conta.id, None)
            await descartar_traco(ctx)
            await ctx.close()
            await pw.stop()
            raise SessaoMorta(motivo) from None
        d.passo("sessao aberta")
        return Sessao(conta=conta, handle=(pw, ctx, pagina))

    async def fechar(self, s: Sessao) -> None:
        pw, ctx, _ = s.handle
        # traco que chega inteiro ate aqui e de job que NAO falhou — na falha ele ja
        # foi guardado, no instante em que ela aconteceu. Descartar e a regra 2 do
        # `diario.py`: alguns MB por job, dezenas de jobs por dia, uma semana de disco.
        if s.conta.id in self._tracando:
            self._tracando.discard(s.conta.id)
            await descartar_traco(ctx)
        self._diarios.pop(s.conta.id, None)
        await ctx.close()
        await pw.stop()

    # ------------------------------------------------------------------- saldo
    async def saldo(self, s: Sessao) -> int:
        """O saldo NAO fica na tela do projeto: mora no menu do avatar.

        Custa dois cliques (abrir e fechar). Por isso o orquestrador le uma vez por
        job, na reconciliacao, e nao a cada volta do polling.
        """
        _, _, pagina = s.handle
        # O ALVO DO CLIQUE E O `[role=button]`, NAO A IMAGEM. MEDIDO 2026-09-05: o
        # avatar da aplicacao nova e um `div[role=button][aria-label="detalhes da
        # conta"]` com a `img` dentro. Clicar na imagem tambem funciona por
        # propagacao, mas mirar no botao e o que sobrevive a ele trocar de icone.
        #
        # E preciso o VEU FORA antes: com qualquer overlay aberto, o Playwright recusa
        # o clique dizendo "intercepts pointer events" e fala do elemento coberto,
        # nunca do que o cobre.
        await self._sem_veu(pagina)
        await pagina.locator(sel.BOTAO_AVATAR).first.click(timeout=15_000)
        try:
            # ESPERA PELO CONTEUDO, nao por tempo. A versao anterior dormia 1200ms
            # fixos e falhava de forma intermitente — passava numa execucao e quebrava
            # na seguinte, com o menu ainda sem renderizar. Espera fixa em interface
            # sempre vira isso: funciona ate a vez em que a maquina esta mais lenta.
            erro: Exception | None = None
            for _ in range(10):
                await pagina.wait_for_timeout(600)
                # a varredura passa por `dom` (regra 2: o __NEXT_DATA__ carrega o
                # access_token em texto puro); quem interpreta e `analise`, que tem teste
                try:
                    return analise.saldo_do_texto(
                        await pagina.evaluate(dom.TEXTOS_CURTOS, 60))
                except GeracaoFalhou as e:
                    erro = e
            raise erro if erro else GeracaoFalhou("saldo nao apareceu no menu")
        finally:
            # sem fechar, o menu fica por cima do chip e do prompt na proxima geracao
            await pagina.keyboard.press("Escape")

    # ---------------------------------------------------------------- projetos
    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]:
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.locator(sel.CARTAO_PROJETO).first.wait_for(timeout=30_000)
        # SIMPLIFICOU NA APLICACAO NOVA. A antiga exigia subir ancestrais porque o <a>
        # do card nao tinha texto; aqui `flow-project-card` traz o nome e o link
        # juntos, e o texto vem como ["<nome>", "edit", "delete"].
        #
        # MEDIDO 2026-09-05:
        #   texto = ['tiktok shop', 'edit', 'delete']
        #   href  = /project/9d985620-ea36-4136-9562-735fdcaf24dd
        pares = await pagina.evaluate(
            """(seletor) => [...document.querySelectorAll(seletor)].map(card => {
                 const a = card.querySelector('a[href*="/project/"]');
                 return {href: a ? a.getAttribute('href') : '',
                         texto: (card.innerText || '').trim()};
               })""",
            sel.CARTAO_PROJETO,
        )
        achados: list[ProjetoRemoto] = []
        for par in pares:
            id_remoto = analise.id_do_href(par["href"])
            if not id_remoto:
                continue
            # `sem_segredo` e nao `seguro`: o nome e comparado com `job.produto`, entao
            # tirar o acento aqui faria "Panelas Monaco" deixar de casar consigo mesmo e
            # a adocao criaria um projeto novo a cada geracao.
            # o texto do cartao vem como "<nome>\nedit\ndelete": o nome e a primeira
            # linha, e as duas ligaduras do fim sao botoes, nao parte do nome
            linhas = [l for l in dom.sem_segredo(par["texto"]).splitlines()
                      if l.strip() and l.strip() not in ("edit", "delete")]
            if not linhas:
                # card sem nome legivel: ignorar e melhor que adotar o projeto errado
                continue
            achados.append(ProjetoRemoto(id_remoto=id_remoto, nome=linhas[0].strip()))
        return achados

    async def criar_projeto(self, s: Sessao, nome: str) -> str:
        """SAO DUAS ETAPAS. "Novo projeto" cria e ENTRA direto, sem pedir nome.

        Renomear e depois, por "Mais opcoes" -> "Renomear". Quem tratar isso como uma
        etapa so acaba com uma conta cheia de "Projeto sem titulo", e a adocao por nome
        (Task 7) para de funcionar — a proxima geracao do mesmo produto cria outro.
        """
        _, _, pagina = s.handle
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await self._sem_veu(pagina)
        await self._clicar_unico(pagina, sel.BOTAO_NOVO_PROJETO, "novo projeto")
        await pagina.wait_for_url(re.compile(re.escape(sel.PADRAO_PROJETO)), timeout=30_000)
        id_remoto = analise.id_do_href(pagina.url)
        if not id_remoto:
            raise GeracaoFalhou(f"projeto {nome!r} nao abriu depois de criado")

        # A SEGUNDA ETAPA — renomear. MEDIDO 2026-09-05: o `more_vert` do CABECALHO do
        # projeto abre "renomear", "ver lixeira" e "excluir". Os dois ultimos sao
        # destrutivos e estao em NAO_CURAVEIS; aqui so se usa o primeiro, escolhido
        # por TEXTO, porque escolher por posicao num menu que tem "excluir" e o erro
        # que ja apagou trabalho do usuario uma vez.
        await self._sem_veu(pagina)
        await self._clicar_unico(pagina, sel.BOTAO_MAIS_OPCOES, "mais opcoes do projeto")
        await self._clicar_unico(pagina, sel.MENU_RENOMEAR, "renomear")

        campo = pagina.locator(sel.CAMPO_NOME_PROJETO).first
        await campo.wait_for(timeout=15_000)
        await campo.fill(nome)
        # NAO HA BOTAO "Concluir" na aplicacao nova — o campo fecha com Enter. Chutar
        # um botao aqui deixaria o nome escrito e nao confirmado, e a adocao por nome
        # (que compara `job.produto` com o titulo) passaria a criar um projeto novo a
        # cada geracao do mesmo produto.
        await campo.press("Enter")

        # CONFERE que o nome pegou, em vez de confiar no Enter. Sem isto, um projeto
        # que ficasse "sem titulo" so apareceria como duplicacao silenciosa dias
        # depois, com a conta cheia de projetos iguais.
        async def renomeou() -> bool:
            try:
                atual = await pagina.locator(sel.CAMPO_NOME_PROJETO).first.input_value(
                    timeout=3000)
            except Exception:
                return False
            return dom.sem_acento(nome) in dom.sem_acento(atual)

        if not await self._esperar(pagina, renomeou, self._diario(s.conta.id),
                                   f"o titulo virar {nome!r}", teto_ms=20_000):
            raise GeracaoFalhou(
                f"projeto {id_remoto} criado, mas o titulo nao virou {nome!r}")
        return id_remoto

    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None:
        """Por URL DIRETA — e isto e uma inversao em relacao a aplicacao antiga.

        A versao Next quebrava em deep link com "Application error", e por isso o
        codigo antigo ia a lista e CLICAVA no card. MEDIDO 2026-09-05 na aplicacao
        Angular: `https://flow.google.com/project/<uuid>` abre o projeto direto, em
        todas as sondas. E o UUID de agosto continua valendo — os projetos antigos
        abrem no endereco novo.

        Ir direto e melhor que clicar na lista por um motivo pratico: a lista e
        preguicosa e paginada, e um projeto antigo pode simplesmente nao estar
        carregado nela.

        O PORTAO DE IDADE e checado aqui porque o sintoma sem isso e enganoso: a conta
        bloqueada nao mostra projeto nenhum, e a mensagem viraria "o projeto nao e
        desta conta" — mandando investigar o lugar errado.
        """
        _, _, pagina = s.handle
        alvo = f"{sel.URL_BASE}{sel.PADRAO_PROJETO}{id_remoto}"
        if id_remoto in pagina.url:
            return
        await pagina.goto(alvo, wait_until="domcontentloaded")
        try:
            await pagina.locator(sel.CAMPO_PROMPT).first.wait_for(timeout=45_000)
        except Exception:
            if sel.URL_PORTAO_IDADE in pagina.url:
                raise SessaoMorta(
                    f"{s.conta.email} caiu no portao de idade do Flow "
                    f"({sel.URL_PORTAO_IDADE}): a conta precisa de verificacao de "
                    "idade no Google") from None
            raise GeracaoFalhou(
                f"projeto {id_remoto} nao abriu em {s.conta.email} "
                f"(url final: {pagina.url})") from None

    # -------------------------------------------------------------------- chip
    def _painel(self, pagina):
        return pagina.locator(_medido("PAINEL_CHIP", sel.PAINEL_CHIP)).first

    @staticmethod
    async def _sem_veu(pagina, tentativas: int = 16) -> bool:
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

    @staticmethod
    async def _painel_aberto(pagina) -> bool:
        """O painel de configuracao esta na tela AGORA.

        Conta os botoes em vez de so procurar o contorno: o overlay do Angular existe
        na pagina mesmo vazio, e `.cdk-overlay-pane` sozinho casaria um pane sem
        conteudo — foi exatamente isso que fez a primeira sonda achar o painel vazio.
        """
        return await pagina.evaluate(
            "() => [...document.querySelectorAll('.cdk-overlay-pane')]"
            ".some(x => x.querySelectorAll('button').length > 3)")

    async def _garantir_painel(self, pagina, d: Diario, tentativas: int = 3) -> bool:
        """Deixa o painel aberto, e devolve se conseguiu.

        O CHIP E UM INTERRUPTOR — clicar com ele aberto FECHA. Por isso a primeira
        coisa e perguntar se ja esta aberto, nunca clicar de saida. E por isso esta
        funcao e chamada ENTRE cada opcao: no Flow novo o painel se fecha ao escolher,
        e a opcao seguinte cairia no vazio.
        """
        for volta in range(tentativas):
            if await self._painel_aberto(pagina):
                return True
            try:
                await pagina.locator(sel.CHIP_CONFIG).first.click(timeout=10_000)
            except Exception:
                pass
            gasto = 0
            while gasto < 20_000:
                await pagina.wait_for_timeout(250)
                gasto += 250
                if await self._painel_aberto(pagina):
                    return True
            d.passo(f"o painel de configuracao nao abriu (volta {volta + 1} de "
                    f"{tentativas})")
        return False

    @staticmethod
    async def _marcados_no_painel(pagina) -> list[str]:
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

    async def _texto_do_chip(self, pagina) -> str:
        bruto = await pagina.locator(sel.CHIP_CONFIG).first.inner_text(timeout=15_000)
        return dom.seguro(bruto.replace("\n", " "))

    async def _clicar_unico(self, escopo, seletor: str, o_que: str) -> None:
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
        try:
            await loc.first.wait_for(timeout=15_000)
        except Exception:
            pass   # segue para o exigir_unico, que da a mensagem boa
        quantos = await loc.count()

        # AUTO-CURA, e SO com zero candidatos. Dois candidatos e AMBIGUIDADE, e
        # ambiguidade nao se cura: foi um desempate automatico que mandou um video do
        # usuario para a lixeira. Zero e outra coisa — e o Google ter mexido no botao —,
        # e ai perguntar tem sentido. `cura` recusa sozinha tudo o que for destrutivo,
        # tudo o que nao tiver descricao e tudo o que passar do teto da execucao; ela
        # nunca levanta, entao o pior caso continua sendo o erro de sempre.
        if quantos == 0:
            novo = await self.cura.curar(
                _pagina_de(escopo), escopo, seletor, o_que, self.print_de_falha)
            if novo:
                loc = escopo.locator(novo)
                quantos = await loc.count()

        analise.exigir_unico(quantos, seletor, o_que)
        await loc.click(timeout=15_000)

    async def _escolher_modo(self, pagina, painel, modo: str) -> None:
        """Frames e Elementos so existem no painel DEPOIS de ele estar em video.

        MEDIDO 2026-08-20: a lista de modos depende do modo atual — com o chip em
        imagem so ha `image` e `videocam`. Clicar direto em `chrome_extension` vindo de
        imagem e clicar no vazio, e foi assim que um job real falhou tres vezes.

        A espera pelo `videocam` antes de contar nao e zelo: ele existe nos DOIS
        estados do painel, entao serve de ancora — contar o alvo antes de o painel
        renderizar daria "nao esta visivel" e forcaria dois cliques onde um bastava.
        """
        try:
            await painel.locator(sel.BOTAO_MODO["t2v"]).first.wait_for(timeout=15_000)
        except Exception:
            pass   # segue: quem da a mensagem boa e o `_clicar_unico`
        visivel = await painel.locator(sel.BOTAO_MODO[modo]).count() > 0
        etapas = analise.etapas_ate_o_modo(modo, visivel)
        for passo in etapas:
            o_que = "modo" if passo == modo else f"modo {passo} (pai de {modo})"
            await self._clicar_unico(painel, sel.BOTAO_MODO[passo], o_que)
            # o painel REDESENHA a cada troca de modo: sem esperar, o clique seguinte
            # cai no layout velho
            await pagina.wait_for_timeout(1200)

    async def print_de_falha(self, pagina, motivo: str) -> Path | None:
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

    async def _painel_fechado(self, pagina) -> bool:
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

    async def _escrever_prompt(self, pagina, prompt: str) -> None:
        """Escreve o prompt SEM que a quebra de linha vire envio.

        `type()` simula tecla por tecla, e num `\\n` ele pressiona Enter — que naquele
        campo ENVIA. Um prompt de quatro paragrafos virava quatro geracoes, cada uma
        com um pedaco do texto, sem erro nenhum: cada envio parcial gera normalmente,
        so que a partir de um prompt que o usuario nunca escreveu.

        `insert_text` insere o texto direto, disparando so o evento `input` — nao ha
        keydown de Enter para o campo interpretar. O texto inteiro entra de uma vez.

        A quebra de linha em si e preservada pelo proprio `insert_text` quando o campo
        aceita; quando nao aceita, o pior caso e um paragrafo colado no outro — infinitamente
        melhor do que quatro geracoes truncadas.
        """
        await pagina.keyboard.insert_text(prompt)
        await pagina.wait_for_timeout(500)

        # CONFERE. O campo e contenteditable e nem todo caminho de insercao pega; se o
        # texto nao entrou inteiro, gerar seria pagar credito por um prompt errado — que
        # foi exatamente o que o defeito original fazia em silencio.
        escrito = (await pagina.locator(sel.CAMPO_PROMPT).first.inner_text()).strip()
        faltou = len(prompt.strip()) - len(escrito)
        if faltou > 5:   # folga para normalizacao de espaco e quebra de linha
            raise GeracaoFalhou(
                f"o prompt nao entrou inteiro no campo: pedi {len(prompt.strip())} "
                f"caracteres, o campo ficou com {len(escrito)}")

    async def _fechar_painel(self, pagina, tentativas: int = 4) -> None:
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
            if await self._painel_fechado(pagina):
                return
            await pagina.keyboard.press("Escape")
            # espera o ESTADO, com teto curto: quem dorme aqui volta a intermitencia
            for _ in range(10):
                await pagina.wait_for_timeout(250)
                if await self._painel_fechado(pagina):
                    return
        raise GeracaoFalhou(
            "o painel do chip nao fechou: ele continua na tela (ou sobrou veu) e "
            "nenhum clique no campo de prompt passaria")

    async def _exigir_agente_desligado(self, pagina) -> bool:
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
        loc = pagina.locator(_medido("BOTAO_AGENTE", sel.BOTAO_AGENTE))
        try:
            await loc.first.wait_for(timeout=15_000)
        except Exception:
            pass   # segue para o exigir_unico, que da a mensagem boa
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

    async def _configurar(self, pagina, p: Pedido, d: Diario) -> str:
        """Abre o chip, ajusta tudo e CONFERE. Devolve o texto final do chip.

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
        await self._exigir_agente_desligado(pagina)

        if not await self._garantir_painel(pagina, d):
            raise GeracaoFalhou("o painel de configuracao nao abriu")
        painel = self._painel(pagina)

        # ordem importa: o modo redefine as listas de proporcao, duracao e modelo — e
        # tambem a propria lista de MODOS, o que faz dos submodos duas etapas
        await self._escolher_modo(pagina, painel, modo)
        await self._garantir_painel(pagina, d)

        await self._clicar_unico(painel, sel.DROPDOWN_MODELO, "dropdown de modelo")
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
        esperado = dom.sem_acento(sel.ROTULO_MODELO[p.modelo.value])
        if esperado not in rotulo:
            raise GeracaoFalhou(f"modelo nao trocou: dropdown diz {rotulo!r}, "
                                f"esperado {esperado!r}")

        await self._garantir_painel(pagina, d)
        await self._clicar_unico(painel, sel.PROPORCAO[p.proporcao], "proporcao")
        await self._garantir_painel(pagina, d)

        # RESOLUCAO — grupo que a aplicacao antiga nao tinha.
        #
        # MEDIDO 2026-09-05: o painel de video ganhou [360p] [720p], e o padrao do Flow
        # e 360p. A aplicacao antiga sempre entregou 720p porque era o unico valor que
        # existia. Sem tocar aqui, TODO video passaria a sair em 360p sem ninguem pedir
        # — uma piora silenciosa de qualidade, que e o pior tipo.
        #
        # Imagem nao tem o grupo.
        if p.modo is not Modo.IMAGEM:
            await self._clicar_unico(
                painel, sel.RESOLUCAO[RESOLUCAO_VIDEO], "resolucao")
            await self._garantir_painel(pagina, d)
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
            await self._garantir_painel(pagina, d)
            await self._clicar_unico(painel, sel.DURACAO[duracao], "duracao")
        await self._garantir_painel(pagina, d)
        await self._clicar_unico(painel, sel.QUANTIDADE[p.quantidade], "quantidade")
        await self._garantir_painel(pagina, d)

        # A CONFERENCIA MUDOU DE FONTE, e melhorou.
        #
        # A aplicacao antiga nao marcava a opcao escolhida, e a unica forma de saber se
        # a configuracao pegou era ler o texto do chip — que nomeia umas coisas e omite
        # outras (o modelo nunca aparecia la). A nova marca TUDO com `aria-checked`,
        # entao da para perguntar diretamente o que esta selecionado.
        marcados = await self._marcados_no_painel(pagina)
        d.passo(f"marcado no painel: {marcados}")

        # O CUSTO ESTA NA TELA. Le-se aqui porque o painel ja esta aberto e a leitura
        # nao custa clique nenhum — ao contrario do saldo, que paga dois. Hoje isto e
        # log; ligar na reserva do orquestrador e passo proprio (ver seletores.py).
        await self._custo_anunciado(pagina)

        await self._fechar_painel(pagina)

        # A CONFERENCIA E CONTRA O QUE ESTA MARCADO, nao contra o texto do chip.
        #
        # Cada item pedido tem que aparecer entre os `aria-checked=true`. O chip
        # continua sendo lido, mas como LOG: ele resume bem para o humano e omite
        # coisas para a maquina (o modelo nunca aparece la).
        exigidos = [sel.MODO_ICONE[modo], sel.LIGADURA_PROPORCAO[p.proporcao],
                    f"x{p.quantidade}"]
        if duracao:
            exigidos.append(duracao)
        if p.modo is not Modo.IMAGEM:
            exigidos.append(RESOLUCAO_VIDEO)
        juntos = " | ".join(marcados)
        faltando = [e for e in exigidos if e not in juntos]
        if faltando:
            raise GeracaoFalhou(
                f"o painel nao confirma a configuracao: marcado {marcados}, "
                f"faltou {faltando}")

        chip = await self._texto_do_chip(pagina)
        d.passo(f"configuracao confirmada; chip diz: {chip}")
        return chip

    async def _custo_anunciado(self, pagina) -> int | None:
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

    # ------------------------------------------------------------------- gerar
    async def gerar(self, s: Sessao, p: Pedido) -> str:
        """Envolvido em print-em-falha: interface so se diagnostica vendo a tela.

        E em traco-em-falha, pelo mesmo motivo levado a serio: o print mostra UM
        instante — o do fim, que e o menos informativo —, e o defeito das duas
        referencias precisa do instante ANTERIOR ao erro. O traco guarda todos.
        """
        _, _, pagina = s.handle
        try:
            return await self._gerar(s, p, pagina)
        except Exception as e:
            await self.print_de_falha(pagina, f"gerar {p.modo.value} {type(e).__name__}")
            self._diario(s.conta.id).passo(f"FALHOU em gerar: {type(e).__name__}: {e}")
            await self._traco_da_falha(s)
            raise

    async def _gerar(self, s: Sessao, p: Pedido, pagina) -> str:
        # o que o modo nao aceita e recusado ANTES de qualquer coisa: nenhum clique
        # dado, nenhum credito gasto, e a reserva do orquestrador ainda estornavel.
        # Antes tambem da checagem de CARD_GRADE, para a mensagem falar do pedido
        # errado e nao de um seletor por medir.
        analise.exigir_proporcao_do_modo(p.modo.value, p.proporcao)
        # a contagem da grade vem antes de tudo: sem CARD_GRADE medido nao ha como
        # saber que o Flow aceitou o pedido, e e melhor descobrir isso aqui do que
        # depois de o credito sair
        grade = _medido("CARD_GRADE", sel.CARD_GRADE)
        antes = await pagina.locator(grade).count()

        d = self._diario(s.conta.id)
        await self._configurar(pagina, p, d)
        await self._anexar(pagina, p, d)

        # ARMADILHA: `input[aria-label="Texto editavel"]` e o NOME DO PROJETO. Escrever
        # ali renomeia o projeto em vez de gerar, e o sintoma e "nada acontece" — que e
        # o pior tipo de bug aqui. O campo certo e o unico contenteditable visivel.
        campo = pagina.locator(sel.CAMPO_PROMPT).first
        await campo.click(timeout=15_000)
        await campo.fill("")
        await self._escrever_prompt(pagina, p.prompt)

        # dois botoes contem a palavra "Criar": anexar e enviar. So a ligadura separa.
        d.passo(f"enviando o comando ({len(p.prompt)} caracteres)")
        await pagina.locator(sel.BOTAO_ENVIAR).first.click(timeout=15_000)

        # O CARD DECIDE, O AVISO NAO. Esta ordem estava invertida, e custou videos
        # gerados e pagos com o job voltando para a fila como pendente.
        #
        # RELATADO pelo usuario: "o video e gerado com o ultimo credito da conta e o
        # robo nao esta pegando que o video foi gerado, esta deixando o trabalho como
        # pendente falando que a conta esta sem saldo. Porem o video e gerado."
        #
        # A causa e que o aviso "Seus creditos do Google Flow acabaram" nao quer dizer
        # "recusei este pedido" — quer dizer "o saldo agora e zero". Quando a geracao
        # consome o ULTIMO credito, o Flow faz as DUAS coisas: aceita o pedido E mostra
        # o aviso. Ler o aviso antes do card lia a consequencia e concluia sobre a
        # causa.
        #
        # O sinal que separa recusa de aceite e o CARD NOVO, e so ele. Por isso o aviso
        # deixou de decidir e passou a ENCURTAR O PRAZO: com ele na tela, esperar os
        # 120 s inteiros por um card que nao vem seria desperdicio; sem ele, o card
        # continua sendo a unica confirmacao.
        if not await self._esperar_card_novo(pagina, antes, d):
            if await self._sem_credito(pagina):
                raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")
            raise GeracaoFalhou("o Flow nao criou card novo depois do envio")

        if await self._sem_credito(pagina):
            # aceitou E zerou: o job segue normalmente, e quem tira a conta do pool e a
            # reconciliacao de saldo no fim. Registrar importa porque este e o caso que
            # antes virava "pendente sem motivo", e o log e onde a pessoa vai olhar.
            d.passo("o Flow aceitou o pedido E avisou que o credito acabou: "
                    "era o ULTIMO credito desta conta")
        indice = 0 if sel.CARDS_NOVOS_PRIMEIRO else antes
        id_remoto = f"card-{indice}"
        d.passo(f"o Flow aceitou: {id_remoto}")
        self._repeticoes[(s.conta.id, id_remoto)] = 0
        self._modo_do_card[(s.conta.id, id_remoto)] = p.modo
        return id_remoto

    async def _sem_credito(self, pagina) -> bool:
        """O aviso de conta zerada — que ainda nao foi visto ao vivo.

        Enquanto o seletor estiver PENDENTE, a resposta e "nao sei", e "nao sei" tem
        que ser False: levantar `SaldoInsuficiente` por causa de um seletor invalido
        marcaria a conta como seca e a tiraria do pool sem nenhuma evidencia.
        Descoberto o rotulo real (Task 14), so `seletores.py` muda.
        """
        if sel.AVISO_SEM_CREDITO in ("", sel.PENDENTE):
            return False
        return await pagina.locator(sel.AVISO_SEM_CREDITO).count() > 0

    async def _esperar_card_novo(self, pagina, antes: int, d: Diario,
                                 teto_ms: int = 120_000,
                                 graca_do_aviso_ms: int = 20_000) -> bool:
        """Espera o card novo aparecer na grade. Devolve SE apareceu.

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
        while gasto < teto_ms:
            if await pagina.locator(sel.CARD_GRADE).count() > antes:
                return True
            if prazo_do_aviso is None and await self._sem_credito(pagina):
                prazo_do_aviso = gasto + graca_do_aviso_ms
                d.passo(f"o aviso de credito zerado apareceu; dou "
                        f"{graca_do_aviso_ms // 1000}s ao card antes de concluir recusa")
            if prazo_do_aviso is not None and gasto >= prazo_do_aviso:
                return False
            await pagina.wait_for_timeout(2000)
            gasto += 2000
            d.esperando("o card novo aparecer na grade")
        return False

    async def _anexar(self, pagina, p: Pedido, d: Diario) -> None:
        """Faz cada arquivo virar REFERENCIA DA GERACAO, pelo seletor de midia.

        O DEFEITO QUE ISTO CONSERTA: a versao anterior fazia `set_input_files` no
        `input[type=file]` escondido e parava por ai. MEDIDO 2026-08-20 por print —
        isso envia o arquivo para o ACERVO do projeto (ele aparece em "Uploads" na
        barra lateral) e NADA MAIS. A imagem nunca entrava no comando, e o Flow gerava
        como se nao houvesse anexo: sem erro, sem aviso, com o credito gasto e uma
        imagem que nao parece com a referencia.

        O caminho certo e o que uma pessoa faz na tela: abrir o seletor pelo `+`,
        enviar, ESPERAR o upload terminar, confirmar que o item esta marcado e clicar
        em "Incluir no comando". Uma volta por referencia — MEDIDO que a selecao do
        seletor e exclusiva, entao nao ha como marcar tres e incluir de uma vez.

        `_configurar` roda ANTES: quem decide o que cada arquivo significa e o modo ja
        escolhido no chip, nao o seletor de arquivo.
        """
        arquivos = analise.arquivos_do_pedido(p)
        if not arquivos:
            if not tipos.exige_arquivo(p.modo):
                return
            raise GeracaoFalhou(f"modo {p.modo.value} sem arquivo para anexar")

        await self._limpar_referencias(pagina)

        # O ENVIO VIRA UMA TACADA SO. Na aplicacao antiga havia uma volta completa do
        # seletor por arquivo, porque so dava para enviar um. MEDIDO 2026-09-05: o
        # input criado pelo "enviar midia" tem `multiple`, e dois arquivos subiram
        # juntos em 11,2 s.
        #
        # Isso encurta o caminho onde moravam tres defeitos da aplicacao antiga — o
        # homonimo velho passando por recem-enviado, o `+` fechando quando devia abrir,
        # e o indice decorado numa lista que reordena. Nenhum deles some por magica: o
        # que some e a repeticao do trecho onde eles aconteciam.
        d.passo(f"enviando {len(arquivos)} arquivo(s) de uma vez")
        quanto = d.cronometro()
        novos = await self._enviar_arquivos(pagina, arquivos, d)
        d.passo(f"{len(novos)} arquivo(s) prontos no acervo em {quanto():.1f}s")

        esperados: list[str] = []
        for n, item in enumerate(novos, 1):
            # o numero DENTRO do total e o que fecha o caso das duas referencias: com
            # ele, "parou na 2 de 2" e "nem chegou na 2" deixam de ser a mesma linha
            d.passo(f"incluindo referencia {n} de {len(novos)}: {item['nome']}")
            quanto = d.cronometro()
            esperados.append(await self._incluir_do_acervo(pagina, item, d))
            d.passo(f"referencia {n} incluida em {quanto():.1f}s")

        # CONFERE QUAIS, nao quantas. A versao por contagem aprovava DUAS FICHAS
        # IGUAIS — foi assim que um job real saiu com a mesma foto duas vezes, sem
        # erro, com credito gasto e um resultado plausivel que o usuario so pegou
        # conferindo a conta. Cada ficha carrega o id da midia (MEDIDO 21/08), entao
        # da para comparar identidade em vez de numero.
        analise.exigir_referencias_completas(await self._ids_anexados(pagina), esperados)

    async def _limpar_referencias(self, pagina) -> None:
        """Zera o rascunho antes de anexar. Referencia herdada e geracao errada.

        Uma tentativa que morreu no meio deixa a ficha na barra do prompt, e o Flow
        SOMA a proxima em vez de trocar (medido na aplicacao antiga: 1 -> 2 -> 3). Sem
        limpar, a segunda tentativa do mesmo job geraria com o dobro das referencias.

        TIRA UMA A UMA, pelo `cancel` de cada ficha. Existe tambem um botao `close` na
        barra, e ele NAO e usado: o efeito dele nao foi medido, e "fechar" tanto pode
        limpar so as referencias quanto apagar o prompt inteiro. Limpar o rascunho
        errado destroi o comando do usuario — e o `cancel` da ficha tem efeito
        conhecido e verificavel, que e a ficha sumir.
        """
        for _ in range(12):
            quantas = await pagina.locator(sel.REFERENCIA_ANEXADA).count()
            if quantas == 0:
                return
            botoes = pagina.locator(sel.BOTAO_TIRAR_REFERENCIA)
            if await botoes.count() == 0:
                raise GeracaoFalhou(
                    f"ha {quantas} referencia(s) na barra e nenhum botao de tirar")
            await botoes.first.click(timeout=15_000)
            # espera a ficha SUMIR, nao um tempo fixo: sem isso a volta seguinte conta
            # a mesma ficha e clica de novo, e o laco vira uma corrida com a animacao
            for _ in range(24):
                await pagina.wait_for_timeout(250)
                if await pagina.locator(sel.REFERENCIA_ANEXADA).count() < quantas:
                    break
        raise GeracaoFalhou(
            "as referencias de uma tentativa anterior nao sairam da barra do prompt")

    async def _enviar_arquivos(self, pagina, arquivos: list[str],
                               d: Diario) -> list[dict]:
        """Envia TODOS os arquivos de uma vez e devolve os itens novos do acervo.

        Cada item devolvido tem `id` (do `src` da miniatura) e `nome` (o do arquivo).
        SAO AS DUAS CONDICOES, e nenhuma sozinha basta:

          * so o id: o `src` troca de host ao longo da vida do item (medido —
            `flow-content.google` vira `lh3.googleusercontent.com`), entao um item
            VELHO que troque de host durante a operacao pareceria novo
          * so o nome: nome nao identifica nada. O acervo tem homonimos de sobra —
            medido, tres itens chamados `02a56f959156564b.png` —, e foi exatamente
            isso que fez duas referencias virarem a mesma foto na aplicacao antiga
        """
        await self._abrir_seletor_midia(pagina, d)

        antes = await self._acervo_assentado(pagina, d)
        ids_antes = {i for i in (analise.id_da_midia(x.get("src")) for x in antes) if i}
        nomes = {Path(a).name for a in arquivos}

        async with pagina.expect_file_chooser(timeout=30_000) as espera:
            await self._clicar_unico(pagina, sel.BOTAO_ENVIAR_MIDIA, "enviar midia")
        seletor = await espera.value
        await seletor.set_files(arquivos)

        novos: list[dict] = []

        async def subiram() -> bool:
            nonlocal novos
            achados = []
            for x in await self._itens_da_lista(pagina):
                ident = analise.id_da_midia(x.get("src"))
                nome = analise.nome_do_item(x.get("texto", ""))
                # `miniaturas` e a chave que `dom.ITENS_DE_MIDIA` devolve. As sondas
                # de scratch usavam `midias`, e copiar dali fez o filtro nunca dar
                # verdadeiro: "so 0 de 2 arquivos ficaram prontos" depois de 250 s
                # esperando dois arquivos que ja estavam la.
                if (ident and ident not in ids_antes and nome in nomes
                        and x.get("miniaturas")):
                    achados.append({"id": ident, "nome": nome, "src": x.get("src", "")})
            novos = achados
            return len(novos) >= len(arquivos)

        if not await self._esperar(pagina, subiram, d, "as miniaturas do envio",
                                   teto_ms=240_000, cada=1000):
            raise GeracaoFalhou(
                f"so {len(novos)} de {len(arquivos)} arquivo(s) ficaram prontos "
                "no acervo")
        if len({i["id"] for i in novos}) != len(novos):
            raise GeracaoFalhou(f"os itens enviados repetem id: {[i['id'] for i in novos]}")
        return novos[:len(arquivos)]

    @staticmethod
    async def _esperar(pagina, condicao, d: Diario, o_que: str,
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

    async def _acervo_assentado(self, pagina, d: Diario) -> list[dict]:
        """O retrato do acervo, SO depois de a lista parar de mudar.

        MEDIDO na propria sonda, que na primeira versao "achou" 6 arquivos novos tendo
        enviado 2: dos 15 itens do acervo, so 8 tinham `src` no primeiro instante — os
        outros carregam a miniatura preguicosamente. Item sem id nao entra no retrato,
        entao quando a miniatura chega ele PARECE recem-enviado.

        E a regra do retrato vista de outro angulo: comparar contra um retrato que
        ainda estava sendo tirado nao compara nada.
        """
        antes: list[dict] = []
        estavel = 0
        for _ in range(80):
            agora = await self._itens_da_lista(pagina)
            if agora == antes and not [i for i in agora if not i.get("src")]:
                estavel += 1
                if estavel >= 3:
                    break
            else:
                estavel = 0
            antes = agora
            await pagina.wait_for_timeout(500)
        d.passo(f"acervo assentado: {len(antes)} itens")
        return antes

    async def _incluir_do_acervo(self, pagina, item: dict, d: Diario) -> str:
        """Poe UM item do acervo no comando. Devolve o id incluido.

        SAO DOIS PASSOS, medidos por print: o clique no item o SELECIONA e abre uma
        pre-visualizacao com "Incluir no comando" ao lado; e esse botao que inclui.
        (Uma sonda viu o menu fechar sozinho num caso isolado, entao o botao e tratado
        como opcional — se ele nao estiver na tela, o clique ja resolveu, e insistir
        seria clicar no vazio.)
        """
        await self._abrir_seletor_midia(pagina, d)

        alvo = pagina.locator(sel.ITEM_MIDIA_POR_ID.format(id=item["id"]))
        analise.exigir_unico(await alvo.count(), f"item {item['id']}", "item do acervo")

        antes = len(await self._ids_anexados(pagina))
        await alvo.first.click(timeout=15_000)
        await pagina.wait_for_timeout(1200)
        incluir = pagina.locator(sel.BOTAO_INCLUIR_NO_COMANDO)
        if await incluir.count() == 1:
            await incluir.first.click(timeout=15_000)

        async def entrou() -> bool:
            return len(await self._ids_anexados(pagina)) > antes

        if not await self._esperar(pagina, entrou, d,
                                   f"a ficha de {item['nome']}", teto_ms=30_000):
            raise GeracaoFalhou(
                f"{item['nome']} nao entrou na barra do prompt depois de incluido")
        return item["id"]

    async def _abrir_seletor_midia(self, pagina, d: Diario, tentativas: int = 3):
        """Abre o seletor de midia pelo `+`, tentando de novo se ele nao vier.

        Um clique que nao abre nada acontece: o botao muda de posicao quando ja ha
        referencia anexada (a caixa do prompt cresce), e um clique perdido nao levanta
        erro — so nao abre. Insistir e barato; desistir custa a geracao inteira e as
        tres tentativas do job.

        O RETRY ERA UMA ARMADILHA, e o usuario viu isso na tela: "as vezes ele fecha
        ali o mais onde voce coloca as fotos. Parece que ele fecha isso e nao consegue
        mais enviar a segunda foto." A versao anterior apertava Escape ASSUMINDO que o
        painel nao tinha aberto. Mas quando ele ABRIU e so demorou a casar — que e
        exatamente o que acontece com a interface engasgada por um upload correndo por
        baixo — o Escape FECHAVA o painel recem-aberto, e o clique seguinte pegava a
        animacao e alternava de novo. O socorro virava a causa.

        A correcao e nao chutar: so se aperta Escape quando NAO HA nada aberto para
        fechar. Painel visivel que ainda nao casou o seletor e "esperar mais".

        A espera tambem subiu de 8 para 20 s pelo mesmo motivo: 8 s e curto para uma
        interface sob upload. Isso e paliativo — a causa de verdade era a espera que
        terminava cedo demais (`_esperar_item_pronto`), e ela ja foi consertada. Os
        dois consertos sao necessarios: um tira a causa, o outro tira a armadilha.
        """
        # JA ABERTO E CASO COMUM, e perguntar primeiro custa ~12 s por geracao.
        #
        # MEDIDO 2026-09-05 no log de passo: a referencia 1 levava 13,4 s e a 2 levava
        # 1,6 s. A diferenca nao era a rede — era esta funcao esperando o `+` DECLARAR
        # fechado logo depois do upload, quando o painel estava aberto o tempo todo. O
        # `aria-expanded` dizia `true` com razao, `_esperar_anexar_fechado` girava ate
        # o teto de 10 s, e so entao o codigo percebia que ja podia usar o painel.
        if await self._esperar_seletor_midia(pagina, teto_ms=1500):
            return pagina.locator(sel.SELETOR_MIDIA).first

        for volta in range(tentativas):
            # SO CLICA COM O BOTAO DECLARANDO FECHADO. Com ele em `open` — o que
            # acontece enquanto a animacao de fechamento da referencia anterior corre —
            # um clique FECHA em vez de abrir, e era essa a armadilha.
            if await self._esperar_anexar_fechado(pagina):
                await pagina.locator(sel.BOTAO_ANEXAR).first.click(timeout=15_000)
            else:
                d.passo("o `+` ainda declara ABERTO; nao clico (o clique fecharia) - "
                        "esperando o painel se firmar")
            if await self._esperar_seletor_midia(pagina):
                return pagina.locator(sel.SELETOR_MIDIA).first
            if volta + 1 >= tentativas:
                break
            # Escape SO com o botao declarando fechado e nada na tela: senao seria o
            # proprio codigo fechando o painel que acabou de abrir
            estado = await pagina.locator(sel.BOTAO_ANEXAR).first.get_attribute(
                sel.ATRIBUTO_ANEXAR)
            d.passo(f"o seletor de midia nao ficou pronto (volta {volta + 1} de "
                    f"{tentativas}); o `+` diz {estado!r}")
            if estado == sel.ANEXAR_FECHADO:
                await pagina.keyboard.press("Escape")
                await pagina.wait_for_timeout(1200)
        raise GeracaoFalhou(
            f"o seletor de midia nao abriu depois de {tentativas} cliques no anexar")

    async def _esperar_anexar_fechado(self, pagina, teto_ms: int = 10_000) -> bool:
        """Espera o `+` DECLARAR fechado. Devolve se chegou la.

        E a barreira que faltava entre uma referencia e a proxima: o painel se fecha
        com animacao, e enquanto ela corre o botao continua dizendo `open`. Clicar
        nessa janela fecha em vez de abrir.
        """
        gasto = 0
        while gasto < teto_ms:
            if await pagina.locator(sel.BOTAO_ANEXAR).first.get_attribute(
                    sel.ATRIBUTO_ANEXAR) == sel.ANEXAR_FECHADO:
                return True
            await pagina.wait_for_timeout(250)
            gasto += 250
        return False

    async def _esperar_seletor_midia(self, pagina, teto_ms: int = 20_000) -> bool:
        """Aberto = o `+` DECLARA aberto, o dialogo esta visivel E a lista tem itens.

        As tres partes, e cada uma cobre um engano medido:

        * so `wait_for(visible)` no dialogo — o que uma versao anterior fazia — casa o
          painel que esta SAINDO de cena pela animacao. Ele volta "abriu" e um instante
          depois a tela nao tem lista nenhuma. Medido: 180 s de espera com
          `[role=option]` em zero. Quem exclui esse caso e o `aria-expanded`, que ja
          diz "fechado" enquanto a animacao corre.
        * so o `aria-expanded` do botao — ele declara ANTES de o painel montar.
        * as ABAS provam que o painel montou o proprio conteudo.

        A TERCEIRA CONDICAO JA FOI `[role=option] > 0`, E ISSO FOI UMA REGRESSAO EM
        PRODUCAO. "A lista tem itens" parece prova de que o painel montou e nao e: num
        projeto NOVO o acervo esta VAZIO, legitimamente, e o criterio nunca se
        satisfazia — projeto novo jamais anexava a primeira referencia, e como o
        usuario tem um projeto por produto, todo produto novo nascia travado. O estado
        so aparece uma vez por projeto e some no primeiro upload, e foi por isso que a
        verificacao ao vivo, feita num projeto de acervo cheio, nao o viu.

        O `input[type=file]` seria o candidato natural — e o proximo passo da
        sequencia —, mas MEDIDO ele nao serve: nao e descendente do `[role=dialog]`
        (zero dentro dele com o painel aberto) e conta 1 na pagina inteira tanto com o
        painel aberto quanto fechado. Sinal que vale 1 sempre nao distingue estado.

        As abas valem porque existem com acervo cheio E vazio (7 em ambos) e somem com
        o painel fechado (0).

        O TETO DE 20 s NAO E FOLGA OCIOSA, E JA FOI TESTADO CONTRA. Quando o clique
        pega, o painel monta com as 7 abas em ~100 ms — o que faz os 20 s parecerem
        desperdicio, porque a segunda referencia quase sempre perde o primeiro clique
        (a barra do prompt cresce com a ficha da primeira e o `+` muda de lugar) e
        cada repeticao custa o teto inteiro de relogio.

        BAIXAR PARA 8 s QUEBROU AO VIVO: a segunda referencia voltou a morrer com "o
        item nao apareceu no seletor de midia em 180s". Os ~100 ms sao o caminho
        RAPIDO, e generalizar deles para o caminho lento e o mesmo engano que criou a
        regressao do acervo vazio — medir uma condicao e concluir sobre outra. Nos 20 s
        mora o caminho lento, que e o que essa espera existe para cobrir. Se um dia
        alguem quiser esse tempo de volta, o caminho e medir o LENTO, nao encurtar o
        teto e torcer.
        """
        gasto = 0
        while gasto < teto_ms:
            if (await pagina.locator(sel.BOTAO_ANEXAR).first.get_attribute(
                    sel.ATRIBUTO_ANEXAR) == sel.ANEXAR_ABERTO
                    and await pagina.locator(sel.SELETOR_MIDIA).count() > 0
                    and await pagina.locator(sel.SELETOR_MIDIA).first.is_visible()
                    and await pagina.locator(sel.ABAS_SELETOR_MIDIA).count() > 0):
                return True
            await pagina.wait_for_timeout(250)
            gasto += 250
        return False

    async def _ids_anexados(self, pagina) -> list[str]:
        """Os ids das midias que ja estao na barra do prompt como referencia.

        MEDIDO 2026-08-21: a ficha da barra carrega o mesmo `?name=<id>` do item da
        lista. E o que permite conferir QUAL entrou, e nao so quantas — a diferenca
        entre pegar duas fichas iguais e aprova-las.
        """
        srcs = await pagina.evaluate(dom.SRC_DAS_REFERENCIAS, sel.REFERENCIA_ANEXADA)
        return [analise.id_da_midia(s) for s in srcs]

    async def _itens_da_lista(self, pagina) -> list[dict]:
        return await pagina.evaluate(dom.ITENS_DE_MIDIA, sel.ITEM_MIDIA)

    async def _incluir_referencia(self, pagina, arquivo: str, d: Diario) -> str:
        """Uma volta completa do seletor de midia. Devolve o ID da midia incluida.

        Devolve o ID, e nao None, porque quem chama precisa conferir QUAL entrou. Ver
        `analise.exigir_referencias_completas`.
        """
        nome = Path(arquivo).name
        fichas_antes = await self._ids_anexados(pagina)
        seletor = await self._abrir_seletor_midia(pagina, d)

        # O RETRATO ANTES DO ENVIO. Sem ele, um homonimo VELHO do acervo — que ja tem
        # miniatura desde um job anterior — passava por "meu upload terminou" no
        # primeiro instante, e todo o resto acontecia com o arquivo ainda em voo. Ver
        # `analise.escolher_item_enviado`.
        ids_antes = analise.ids_dos_itens(await self._itens_da_lista(pagina))

        # o `input[type=file]` vive DENTRO do seletor; nao se clica em "Enviar midia"
        await pagina.locator(sel.UPLOAD).first.set_input_files([arquivo])
        # o aviso de direitos e vigiado DENTRO da espera, nao antes dela — ver
        # `_esperar_item_pronto`
        id_midia = await self._esperar_item_pronto(pagina, nome, ids_antes, d)

        await self._marcar_item(pagina, id_midia, nome)
        await self._clicar_unico(pagina, sel.BOTAO_INCLUIR_NO_COMANDO,
                                 "Incluir no comando")
        # O seletor fecha sozinho ao incluir; espera-se o ESTADO, nunca um sono. E o
        # estado que vale e o do PROPRIO BOTAO `+`, nao a visibilidade do dialogo: o
        # painel sai de cena com animacao, e enquanto ela corre ele continua visivel
        # e o botao continua dizendo `open`. Quem seguia pela visibilidade passava
        # para a referencia seguinte no meio da animacao, e o clique no `+` daquela
        # fechava o painel em vez de abrir.
        if not await self._esperar_anexar_fechado(pagina):
            raise GeracaoFalhou(
                f"{nome}: o seletor de midia nao terminou de fechar depois de incluir "
                f"({sel.ATRIBUTO_ANEXAR} do `+` continua {sel.ANEXAR_ABERTO!r})")

        # A BARREIRA. So se passa para a proxima referencia depois que ESTA chegou na
        # barra — e depois de conferir que a ficha nova e a do arquivo que subiu.
        await self._esperar_ficha_nova(pagina, len(fichas_antes))
        analise.exigir_referencia_incluida(
            fichas_antes, await self._ids_anexados(pagina), id_midia, nome)
        return id_midia

    async def _marcar_item(self, pagina, id_midia: str, nome: str) -> None:
        """Deixa EXATAMENTE `id_midia` selecionado no seletor de midia.

        Duas coisas que a versao anterior errava, e as duas custaram caro:

        * ela clicava por POSICAO (`itens.nth(indice)`), com um indice calculado numa
          varredura anterior. A lista se reordena e encolhe, entao a posicao vira outro
          item. Aqui o alvo carrega o ID no proprio seletor e e resolvido no instante do
          clique, por `_clicar_unico` — zero candidatos e erro, dois tambem.
        * ela conferia `aria-selected` do item que JULGAVA ter escolhido e seguia. Mas
          "Incluir no comando" age sobre o que estiver SELECIONADO na lista, e os dois
          podiam divergir sem sintoma. Agora se confere a SELECAO, que e o que manda.

        MEDIDO: clicar num item JA selecionado o DESmarca, e sem selecao o botao
        "Incluir no comando" some da tela. Por isso so se clica quando preciso.
        """
        alvo = sel.ITEM_MIDIA_POR_ID.format(id=id_midia)
        for _ in range(20):
            marcados = analise.ids_selecionados(await self._itens_da_lista(pagina))
            if marcados == [id_midia]:
                return
            if id_midia in marcados:
                # marcado junto com outro: a selecao devia ser exclusiva. Nao se
                # desempata no escuro — quem decide e o exigir logo abaixo.
                break
            await self._clicar_unico(pagina, alvo, f"item {nome} no seletor de midia")
            # espera o ESTADO, nunca um sono: o item redesenha ao ser marcado
            for _ in range(12):
                await pagina.wait_for_timeout(250)
                if analise.ids_selecionados(
                        await self._itens_da_lista(pagina)) == [id_midia]:
                    return
        analise.exigir_selecao(
            analise.ids_selecionados(await self._itens_da_lista(pagina)), id_midia, nome)

    async def _esperar_ficha_nova(self, pagina, antes: int,
                                  teto_ms: int = 20_000) -> None:
        """Espera a barra do prompt ir de `antes` para `antes + 1` fichas.

        Espera o ESTADO. A ficha nao aparece no mesmo quadro em que o seletor fecha, e
        conferir na hora daria "nao entrou" para uma referencia que entrou 300 ms
        depois. Estourar aqui e erro de verdade, e o erro vem de quem chama, que sabe
        o nome do arquivo.
        """
        gasto = 0
        while gasto < teto_ms:
            if len(await self._ids_anexados(pagina)) > antes:
                return
            await pagina.wait_for_timeout(250)
            gasto += 250

    async def _esperar_item_pronto(self, pagina, nome: str, ids_antes: set[str],
                                   d: Diario, teto_ms: int = 180_000) -> str:
        """Espera o upload TERMINAR e devolve o ID DA MIDIA recem-enviada.

        DEVOLVE ID, NAO POSICAO — e `ids_antes` (o retrato da lista antes do envio) e
        o que faz esta espera esperar de verdade. O defeito de 21/08 morava aqui: com
        a busca feita PELO NOME, um homonimo velho do acervo — que ja tem miniatura
        desde um job anterior — satisfazia "pronto" no primeiro instante, e a funcao
        voltava sem ter esperado nada, com o upload real ainda em voo. Ver a cadeia
        inteira em `analise.escolher_item_enviado`.

        Espera pelo ITEM PRONTO, nao por tempo. MEDIDO 2026-08-20: 2,9 MB levaram
        17,25 s, e o item entra na lista no primeiro segundo — sem miniatura — com uma
        porcentagem correndo. Os 3 s fixos da versao anterior incluiriam um item vazio.

        O teto e generoso de proposito: estourar aqui e um upload lento, que se resolve
        esperando; incluir cedo e uma referencia vazia, que so aparece na imagem final.

        O AVISO DE DIREITOS e vigiado a cada volta, e nao numa janela fixa antes desta
        espera. MEDIDO em producao (job #19, conta estreando): o aviso so aparece
        quando o Flow COMECA A RECEBER o arquivo, e o upload leva de 7 a 17 segundos.
        Uma espera de 2 s antes do laco quase nunca o encontrava; o modal ficava na
        tela segurando o upload, esta funcao contava ate 180 s por uma miniatura que
        nao vinha, e a conta perdia uma tentativa inteira e ~180 s na estreia. Uma vez
        em CADA conta do pool — com cinco contas estreando em paralelo, cinco jobs
        perdendo a primeira tentativa juntos.

        Vigiar custa uma contagem por volta, e a contagem para assim que o aviso for
        dispensado: a ausencia e o caso comum em todas as geracoes seguintes.
        """
        gasto = 0
        visto = False
        vigiar_aviso = True
        while gasto < teto_ms:
            if vigiar_aviso and await self._dispensar_aviso_de_direitos(pagina):
                vigiar_aviso = False
            itens = await self._itens_da_lista(pagina)
            id_midia = analise.escolher_item_enviado(itens, nome, ids_antes)
            if id_midia:
                return id_midia
            visto = visto or analise.apareceu_na_lista(itens, nome, ids_antes)
            await pagina.wait_for_timeout(500)
            gasto += 500
            # A LINHA QUE FALTAVA. Meia hora ja foi gasta investigando um job que
            # estava apenas subindo um arquivo grande — e foi o silencio que fez o
            # usuario fechar a janela achando que travou. Diz tambem se o item ja
            # APARECEU, que e o que separa "upload lento" de "o arquivo nem chegou".
            d.esperando(f"a miniatura de {nome}"
                        f"{'' if visto else ' (o item nem apareceu na lista)'}")
        raise GeracaoFalhou(
            f"{nome}: o item {'nao ficou pronto' if visto else 'nao apareceu'} no "
            f"seletor de midia em {teto_ms // 1000}s")

    async def _dispensar_aviso_de_direitos(self, pagina) -> bool:
        """Dispensa o modal "Aviso" da primeira vez que a conta envia arquivo, SE ele
        estiver na tela agora. Devolve se dispensou.

        Autorizado pelo Davi em 2026-08-20, e a autorizacao e sobre o comportamento
        permanente, nao sobre um clique: o aviso aparece uma vez em CADA conta do pool.
        Ele declara que quem envia tem os direitos sobre as imagens — e quem pode
        afirmar isso e o dono delas, por isso a decisao foi dele. `BOTAO_CONCORDO`
        continua entre os NAO CURAVEIS pelo mesmo motivo: aceitar termo em nome do
        usuario nao e conserto de interface.

        Olhada instantanea, sem espera NENHUMA — nem os 2 s de antes. Quem espera e
        quem chama, volta a volta, durante todo o upload: e la que o aviso aparece, em
        momento imprevisivel entre o segundo 0 e o 17. Uma espera bloqueante aqui
        dentro faria toda geracao seguinte pagar o preco de um pop-up que existiu uma
        vez so.
        """
        if await pagina.locator(sel.AVISO_DIREITOS).count() == 0:
            return False
        await self._clicar_unico(pagina, sel.BOTAO_CONCORDO, "aviso de direitos")
        # Espera o AVISO sumir, e nao "o painel fechar". A diferenca importa: isto roda
        # com o SELETOR DE MIDIA aberto, e `_fechar_painel` casaria o seletor de midia
        # (que tambem e `[role=dialog]`) e ficaria apertando Escape para fechar
        # justamente a peca que a sequencia precisa que continue de pe.
        for _ in range(20):
            if await pagina.locator(sel.AVISO_DIREITOS).count() == 0 \
                    and await pagina.locator(sel.VEU_ABERTO).count() == 0:
                break
            await pagina.wait_for_timeout(250)
        print("aviso de direitos dispensado (primeira vez desta conta)")
        return True

    # --------------------------------------------------------------- consultar
    async def _card(self, pagina, id_remoto: str):
        indice = int(id_remoto.split("-")[1])
        cards = pagina.locator(_medido("CARD_GRADE", sel.CARD_GRADE))
        if await cards.count() <= indice:
            raise GeracaoFalhou(f"{id_remoto} sumiu da grade")
        return cards.nth(indice)

    @staticmethod
    async def _card_falhou(card) -> bool:
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
            if seletor and seletor != sel.PENDENTE:
                if await card.locator(seletor).count() > 0:
                    return True
        return False

    async def _esperar_sair_da_falha(self, pagina, id_remoto: str, d: Diario,
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
                card = await self._card(pagina, id_remoto)
            except GeracaoFalhou:
                # o card sumiu da grade no meio da troca: isso E sair da falha
                return True
            if not await self._card_falhou(card):
                return True
            d.esperando(f"{id_remoto} sair do estado de falha depois do repetir")
        return False

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        """Le o estado pela GRADE, nunca pela conversa.

        O agente so responde quando perguntado; a grade atualiza sozinha. E nao se
        contam elementos <video> soltos: o Flow usa <video> em outros lugares da tela,
        e o contador declararia "pronto" antes de existir card.
        """
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto)

        if await self._card_falhou(card):
            vezes = self._repeticoes.get((s.conta.id, id_remoto), 0)
            # CONFERIR o botao antes de mirar nele. O card de falha tem tres botoes em
            # fila e um deles APAGA; com zero ou com dois candidatos o certo e desistir
            # e devolver FALHOU — o job volta pra fila e alguem olha — em vez de clicar
            # no vizinho e jogar fora o trabalho do usuario.
            repetir = card.locator(sel.BOTAO_REPETIR_CARD)
            if vezes < MAX_REPETICOES and await repetir.count() == 1:
                # `redo` repete SEM cobrar de novo — a propria mensagem do card diz
                # "Nao houve cobranca por esta geracao". Repetir aqui, dentro de
                # `consultar`, e o que faz o orquestrador nem tomar conhecimento:
                # `_esperar` ve RODANDO e continua o polling, sem gastar tentativa do
                # job nem reabrir navegador. O usuario decidiu que nao quer notificacao
                # — entao a repeticao tem que ser invisivel e barata.
                self._repeticoes[(s.conta.id, id_remoto)] = vezes + 1
                self._diario(s.conta.id).passo(
                    f"{id_remoto} falhou; repetindo pelo card "
                    f"(gratuito, {vezes + 1} de {MAX_REPETICOES})")
                await self._clicar_unico(card, sel.BOTAO_REPETIR_CARD,
                                         f"repetir {id_remoto}")
                # ESPERA O ESTADO, NAO UM SONO. O `wait_for_timeout(3000)` daqui
                # custou tres jobs marcados como falhos com o video GERADO na conta.
                #
                # MEDIDO nos tracos conta1-0007 e conta8-0008, identicos: 4 cliques em
                # `redo`, 4 sonos de 3 s, 5 checagens de falha. O card NAO sai do
                # estado de falha em 3 s — entao a volta seguinte do polling relia o
                # MESMO card de falha, contava como falha nova, gastava mais uma
                # repeticao e clicava em `redo` de novo. As quatro repeticoes se
                # queimavam em ~12 segundos, e `consultar` devolvia FALHOU.
                #
                # E os `redo` FUNCIONAVAM: o ultimo quadro do traco mostra um card
                # gerando a 21%. O Flow refazia o video e o codigo ja tinha desistido
                # de olhar — o usuario via o video pronto na conta e o painel dizendo
                # que falhou.
                if not await self._esperar_sair_da_falha(
                        pagina, id_remoto, self._diario(s.conta.id)):
                    # nao saiu: nao adianta clicar de novo, e clicar de novo e
                    # justamente o que fabricava o defeito. Devolve RODANDO e deixa a
                    # proxima volta do polling reavaliar com a tela mais adiantada.
                    self._diario(s.conta.id).passo(
                        f"{id_remoto}: o card nao saiu da falha depois do repetir; "
                        "seguindo o polling sem gastar outra repeticao")
                return Estado.RODANDO
            # Sob o banner de alta demanda a falha e do Google, nao da conta. Levantar
            # SaldoInsuficiente ou SessaoMorta aqui marcaria a conta seca ou expirada e
            # um pico do Google derrubaria o pool inteiro. GeracaoFalhou nao marca nada.
            if await pagina.locator(sel.AVISO_ALTA_DEMANDA).count() > 0:
                raise GeracaoFalhou(
                    "Flow em alta demanda: falha do Google, a conta esta boa")
            # o Flow desistiu do card. Isto NAO levanta — quem transforma em falha do
            # job e o orquestrador —, entao o traco tem que ser guardado aqui ou ele
            # seria descartado em `fechar` como se o job tivesse dado certo.
            self._diario(s.conta.id).passo(f"{id_remoto}: o Flow devolveu FALHOU")
            await self._traco_da_falha(s)
            return Estado.FALHOU

        if await card.locator(sel.CARD_MIDIA).count() > 0:
            self._parado.pop((s.conta.id, id_remoto), None)
            return Estado.PRONTO

        # SEM MIDIA E SEM PORCENTAGEM = PAROU DE PROGREDIR.
        #
        # O texto do card de falha da aplicacao NOVA ainda nao foi medido: varri os
        # oito projetos das cinco contas vivas e nao ha um unico card falho guardado —
        # o Flow nao os mantem. Sem esse texto, `_card_falhou` responde "nao sei", e
        # uma geracao que falhasse ficaria parecendo RODANDO ate o teto de dez minutos.
        # Na aplicacao antiga isso custava o job inteiro e o usuario descrevia como
        # "travado".
        #
        # O que DA para medir e o progresso: MEDIDO 2026-09-05 numa geracao real, o
        # card em andamento traz a PORCENTAGEM no proprio texto —
        # `['image', '20%', <prompt>]`, depois `'86%'` — e some quando a midia entra.
        # Entao a ausencia das DUAS coisas, sustentada, e sinal estrutural de que aquele
        # card nao esta indo a lugar nenhum. Nao depende de frase nenhuma, e por isso
        # sobrevive a proxima traducao do Google.
        #
        # A PACIENCIA E O QUE TORNA ISSO SEGURO. Um card recem-criado passa alguns
        # instantes sem midia e sem porcentagem — declarar falha ali mataria geracao
        # boa, que e pior do que esperar. So depois de `VOLTAS_SEM_PROGRESSO` leituras
        # seguidas sem sinal nenhum e que se desiste.
        texto = dom.seguro(await card.inner_text(timeout=10_000))
        if re.search(sel.PADRAO_PORCENTAGEM, texto):
            self._parado.pop((s.conta.id, id_remoto), None)
            return Estado.RODANDO

        chave = (s.conta.id, id_remoto)
        self._parado[chave] = vezes = self._parado.get(chave, 0) + 1
        if vezes < VOLTAS_SEM_PROGRESSO:
            return Estado.RODANDO
        self._parado.pop(chave, None)
        self._diario(s.conta.id).passo(
            f"{id_remoto}: {vezes} leituras sem midia e sem porcentagem — o card "
            f"parou de progredir. Texto: {texto[:120]!r}")
        await self._traco_da_falha(s)
        return Estado.FALHOU

    # ------------------------------------------------------------------ baixar
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        """Envolvido em traco-em-falha pelo mesmo motivo de `gerar`.

        O menu do card e a parte mais perigosa da sequencia — o vizinho de "Baixar" e
        "Mover para a lixeira" —, e quando ela erra a pergunta e sempre a mesma: o que
        estava na tela naquele instante.
        """
        d = self._diario(s.conta.id)
        d.passo(f"baixando {id_remoto} para {destino.name}")
        try:
            return await self._baixar(s, id_remoto, destino, d)
        except Exception as e:
            d.passo(f"FALHOU em baixar: {type(e).__name__}: {e}")
            await self._traco_da_falha(s)
            raise

    async def _baixar(self, s: Sessao, id_remoto: str, destino: Path,
                      d: Diario) -> Path:
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto)

        # o <video> nao e clicavel (e miniatura). O alvo e o centro do container.
        caixa = await card.bounding_box()
        if caixa is None:
            raise GeracaoFalhou(f"{id_remoto} sem caixa na tela - nao da para clicar")
        await pagina.mouse.click(caixa["x"] + caixa["width"] / 2,
                                 caixa["y"] + caixa["height"] / 2, button="right")
        menu = pagina.locator(sel.MENU_CARD).last
        await menu.wait_for(timeout=15_000)
        await self._logar_menu(pagina, menu)

        await self._clicar_unico(menu, sel.MENU_BAIXAR, "item Baixar")

        # assumir VIDEO quando o modo nao e conhecido e o default seguro: pedir "720p"
        # num menu de imagem nao acha rotulo e a busca grita, enquanto pedir "Tamanho
        # original" num menu de video baixaria em silencio o errado.
        chave = sel.BAIXAR[
            RESOLUCAO[self._modo_do_card.get((s.conta.id, id_remoto), Modo.T2V)]]
        # os rotulos do submenu tem QUEBRA DE LINHA ("270p\nGIF animado"): passar o
        # rotulo inteiro para um seletor CSS estoura o parser. A escolha acontece sobre
        # o texto ja lido, por `analise.achar_no_submenu`, que recusa empate.
        itens, indice = await self._esperar_submenu_com(pagina, chave, d)
        async with pagina.expect_download(timeout=self.timeout_ms) as espera:
            await itens.nth(indice).click(timeout=15_000)
        download = await espera.value

        # o Flow escolhe a extensao: jpeg para imagem, mp4 para video. O orquestrador
        # montou `destino` com .png/.mp4 a partir do modo — se salvassemos ali, o banco
        # apontaria para um .png que na verdade e JPEG. Devolver o caminho REAL faz
        # `job.arquivo` ficar verdadeiro sem tocar na Task 7.
        real = analise.caminho_final(destino, download.suggested_filename)
        real.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(real)
        d.passo(f"baixado: {real}")
        return real

    async def _esperar_submenu_com(self, pagina, chave: str, d: Diario,
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

    async def _logar_menu(self, pagina, menu) -> None:
        """Imprime os candidatos ANTES de clicar. Barato, e ja teria evitado um
        clipe do usuario indo para a lixeira."""
        itens = [dom.seguro(t) for t in
                 await menu.locator(sel.MENU_ITEM).all_inner_texts()]
        print("menu do card:", [i.splitlines()[0] for i in itens if i])
