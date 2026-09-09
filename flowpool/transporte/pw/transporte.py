"""Implementacao real do FlowTransport.

Sequencias medidas ao vivo em 2026-08-20. Seletor NENHUM mora aqui: quando o Google
mexer no layout, o conserto e em `seletores.py` e o diagnostico e `conferir.py`.
Decisao pura nenhuma mora aqui tambem: parsing e escolha estao em `analise.py`, que
tem teste. O que sobra neste arquivo e a SEQUENCIA — e sequencia so se verifica
rodando, pelos scripts de fumaca.
"""

from __future__ import annotations

import re
import time
from contextlib import suppress
from pathlib import Path

from flowpool.nucleo import tipos
from flowpool.nucleo.tipos import Conta, Modo
from flowpool.transporte.base import (
    Estado,
    GeracaoFalhou,
    LimiteDeUso,
    Pedido,
    ProjetoRemoto,
    SaldoInsuficiente,
    Sessao,
    SessaoMorta,
)
from flowpool.transporte.pw import acoes, analise, chip, dom, download, grade
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.cura import AutoCura
from flowpool.transporte.pw.diario import (
    Diario,
    descartar_traco,
    guardar_traco,
    iniciar_traco,
    proximo_traco,
)
from flowpool.transporte.pw.sessao import abrir_contexto

# Chave de `sel.BAIXAR` por modo — a resolucao pedida no menu de DOWNLOAD.
# Medido 2026-09-05 num resultado de imagem: 1k / 2k / 4k. O de video falta medir.
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
        # desde quando cada card esta sem porcentagem e sem midia. O relogio entra
        # pela porta para o teste avancar o tempo sem dormir.
        self._relogio = time.monotonic
        self._parado: dict[tuple[int, str], float] = {}
        # o custo que o painel anunciou na ultima configuracao
        self._ultimo_custo: int | None = None
        # `baixar` recebe so o id_remoto, e o menu de resolucao e diferente entre
        # imagem e video. Guardar o modo no momento de gerar e mais barato do que
        # adivinhar pelo DOM — e adivinhar errado baixaria a resolucao errada calado.
        self._modo_do_card: dict[tuple[int, str], Modo] = {}
        # ⚠️ A POSICAO NAO IDENTIFICA O CARD, e isso ja custou video pago.
        #
        # MEDIDO 2026-09-09 nos jobs 170/174/189/195 (frames2v): o download abriu um
        # card de IMAGEM. Prova no proprio erro — o submenu trouxe
        # `['1k / tamanho original']`, que este arquivo documenta como submenu de
        # imagem (video traz `270p / 720p / 1080p / 4k`), e o menu do card trazia
        # `motion_blur / animar`, que so existe em card de imagem. O codigo pediu
        # "720p", nunca achou, tentou 21 vezes e desistiu com o video gerado e pago.
        #
        # A causa e `card-0` ser POSICIONAL: o frames2v sobe DOIS quadros de
        # referencia, upload vira tile na grade, e o tile de cima deixa de ser a
        # geracao. Por isso o card passou a ser lembrado pela IDENTIDADE (o `src` da
        # miniatura), com a posicao so como ultimo recurso.
        self._identidade_do_card: dict[tuple[int, str], str] = {}
        # preenchido por `_esperar_card_novo` e consumido logo depois em `_gerar`
        self._identidade_nova: str | None = None
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

    async def viva(self, s: Sessao) -> bool:
        """Se a sessao guardada ainda tem pagina — o Chrome pode ter morrido sozinho."""
        try:
            _, ctx, pagina = s.handle
            return not pagina.is_closed() and bool(ctx.pages)
        except Exception:
            return False

    async def renovar(self, s: Sessao) -> None:
        """Sessao reusada para um job novo: ABA NOVA, aba velha fechada, relogio zerado.

        A aba nova e o que faz o reuso se comportar como um Chrome novo. MEDIDO
        2026-09-06 no primeiro reuso: o Flow guarda o modo escolhido no
        `sessionStorage` DA ABA, entao a aba do job anterior ja abria em modo imagem
        e `_escolher_modo("imagem")` nao achava o botao — o painel nao oferece o modo
        que ja esta ativo. Num Chrome novo o `sessionStorage` nasce vazio e o defeito
        nunca aparecia. Aba nova = `sessionStorage` vazio, nenhum menu aberto, nenhum
        veu do job anterior; e custa ~1 s, contra 10-15 s de abrir e validar um Chrome.

        A nova abre ANTES de a velha fechar: num contexto persistente, fechar a ultima
        aba derruba o navegador inteiro.

        O relogio do log de passo recomeca porque o total que o usuario le e "ha
        quanto tempo ESTE job esta rodando", nao ha quanto tempo o navegador existe.
        """
        pw, ctx, velha = s.handle
        nova = await ctx.new_page()
        with suppress(Exception):
            await velha.close()
        s.handle = (pw, ctx, nova)
        self._diarios[s.conta.id] = Diario(s.conta.id)

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
        await acoes.sem_veu(pagina)
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
            linhas = [x for x in dom.sem_segredo(par["texto"]).splitlines()
                      if x.strip() and x.strip() not in ("edit", "delete")]
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
        await acoes.sem_veu(pagina)
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
        await acoes.sem_veu(pagina)
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

        if not await acoes.esperar(pagina, renomeou, self._diario(s.conta.id),
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

    async def _configurar(self, pagina, p: Pedido, d: Diario) -> str:
        """`chip.configurar` com a cura desta sessao, guardando o custo anunciado.

        O custo so existe com o painel aberto, e quem o usa — a mensagem de saldo curto
        no envio — acontece com ele ja fechado. Por isso ele descansa aqui.
        """
        cfg = await chip.configurar(pagina, p, d, self.cura)
        self._ultimo_custo = cfg.custo
        return cfg.chip







    async def _clicar_unico(self, escopo, seletor: str, o_que: str) -> None:
        """`acoes.clicar_unico` com a cura DESTE transporte.

        Fica aqui, e nao no ponto de chamada, porque a cura e estado do transporte e os
        catorze lugares que clicam nao tem por que saber disso.
        """
        await acoes.clicar_unico(escopo, seletor, o_que, self.cura)


    async def print_de_falha(self, pagina, motivo: str) -> Path | None:
        """`acoes.print_de_falha`, exposta no objeto.

        Continua sendo metodo porque e por aqui que a suite troca o print por um duplo
        na instancia, e porque `gerar`, `consultar` e `baixar` a passam adiante como
        retorno de chamada.
        """
        return await acoes.print_de_falha(pagina, motivo)


    async def _exigir_botao_de_enviar(self, pagina, email: str) -> None:
        """Recusa gerar quando o botao de enviar nao esta na barra.

        MEDIDO 2026-09-05, sem gastar nada: numa conta com ~36 creditos, configurar um
        modelo que o painel anuncia por 100 faz o BOTAO DE ENVIAR DESAPARECER, e um
        `info` toma o lugar dele. `BOTAO_ENVIAR` passa a casar ZERO.

        A aplicacao antiga so avisava DEPOIS do clique, e foi por ler aquele aviso
        antes do card que um job voltou para a fila com o video ja gerado e pago. Aqui
        nao ha essa corrida: sem botao, nada foi enviado, e a reserva continua
        estornavel.

        `SaldoInsuficiente` e o erro certo — ele faz o orquestrador trocar de conta em
        vez de gastar as tres tentativas do job na mesma conta seca.
        """
        if await pagina.locator(sel.BOTAO_ENVIAR).count() > 0:
            return
        tem_info = await pagina.locator(sel.BOTAO_INFO_BARRA).count() > 0
        raise SaldoInsuficiente(
            f"{email}: o botao de enviar nao esta na barra"
            + (" (no lugar dele ha um `info`, que e como o Flow mostra saldo "
               "insuficiente)" if tem_info else "")
            + (f" - o painel anunciou {self._ultimo_custo} creditos"
               if self._ultimo_custo is not None else ""))

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
            caminho = await self.print_de_falha(
                pagina, f"gerar {p.modo.value} {type(e).__name__}")
            d = self._diario(s.conta.id)
            d.passo(f"FALHOU em gerar: {type(e).__name__}: {e}")
            self._anotar_falha(s, caminho, d)
            await self._traco_da_falha(s)
            raise

    @staticmethod
    def _anotar_falha(s: Sessao, caminho: Path | None, d: Diario) -> None:
        """Deixa na sessao o material do diagnostico automatico: print e log.

        O orquestrador nao ve pagina nem diario; ve a sessao. E o print e nomeado
        pelo MOTIVO (sobrescrito a cada falha igual), entao o caminho precisa ser
        guardado agora, antes de a proxima falha do mesmo tipo trocar a imagem.
        """
        s.falha = {"print": str(caminho) if caminho else None,
                   "passos": list(d.linhas[-40:])}

    async def _gerar(self, s: Sessao, p: Pedido, pagina) -> str:
        # o que o modo nao aceita e recusado ANTES de qualquer coisa: nenhum clique
        # dado, nenhum credito gasto, e a reserva do orquestrador ainda estornavel.
        # Antes tambem da checagem de CARD_GRADE, para a mensagem falar do pedido
        # errado e nao de um seletor por medir.
        analise.exigir_proporcao_do_modo(p.modo.value, p.proporcao)
        # a contagem da grade vem antes de tudo: sem CARD_GRADE medido nao ha como
        # saber que o Flow aceitou o pedido, e e melhor descobrir isso aqui do que
        # depois de o credito sair
        seletor_grade = acoes.medido("CARD_GRADE", sel.CARD_GRADE)
        antes = await pagina.locator(seletor_grade).count()

        d = self._diario(s.conta.id)

        # EM FRAMES, O ENVIO VEM ANTES DA CONFIGURACAO — e a ordem nao e capricho.
        #
        # MEDIDO 2026-09-05: o popover que o slot `inicio`/`fim` abre SO ESCOLHE do
        # acervo; o `flow-media-upload` dele esta vazio e invisivel. E no modo Frames o
        # `+` some da barra (ela vira `flow-ingredient-bar`). Ou seja, depois de trocar
        # para Frames nao existe mais nenhuma porta de envio.
        #
        # Entao os arquivos sobem enquanto o `+` ainda esta la, e depois os slots os
        # escolhem por ID no acervo.
        frames: list[dict] = []
        if p.modo is Modo.FRAMES2V:
            arquivos = analise.arquivos_do_pedido(p)
            if not arquivos:
                raise GeracaoFalhou("frames2v sem frame inicial")
            # SAIR DE FRAMES ANTES DE ENVIAR. O chip guarda o modo entre execucoes:
            # numa REPETICAO do job o projeto abre ja em Frames, o `+` nao existe, e o
            # envio morria com "waiting for locator(flow-add-menu button)" — na
            # primeira tentativa funcionava, na segunda nao, que e o pior tipo de
            # intermitencia porque parece aleatorio.
            #
            # `ingredients2v` e o vizinho natural: mesmo tipo (video) e tem o `+`.
            await chip.exigir_agente_desligado(pagina)
            if await chip.garantir(pagina, d):
                painel_envio = chip.painel_de(pagina)
                await chip.escolher_modo(pagina, painel_envio,
                                         Modo.INGREDIENTS2V.value, self.cura)
                await chip.fechar(pagina)
            d.passo(f"enviando {len(arquivos)} frame(s) antes de trocar para Frames")
            frames = await self._enviar_arquivos(pagina, arquivos, d)
            await chip.fechar(pagina)

        await self._configurar(pagina, p, d)
        if p.modo is Modo.FRAMES2V:
            await self._preencher_slots(pagina, frames, d)
        else:
            await self._anexar(pagina, p, d)

        # ARMADILHA: `input[aria-label="Texto editavel"]` e o NOME DO PROJETO. Escrever
        # ali renomeia o projeto em vez de gerar, e o sintoma e "nada acontece" — que e
        # o pior tipo de bug aqui. O campo certo e o unico contenteditable visivel.
        campo = pagina.locator(sel.CAMPO_PROMPT).first
        await campo.click(timeout=15_000)
        await campo.fill("")
        await self._escrever_prompt(pagina, p.prompt)

        # SALDO CURTO SE RECONHECE AQUI, ANTES DO CLIQUE — e isso e melhor do que era.
        #
        # MEDIDO 2026-09-05, sem gastar nada: numa conta com ~36 creditos, configurar
        # um modelo que o painel anuncia por 100 faz o BOTAO DE ENVIAR DESAPARECER, e
        # um `info` toma o lugar dele. `BOTAO_ENVIAR` passa a casar ZERO.
        #
        # A aplicacao antiga so avisava DEPOIS do clique, e foi por ler aquele aviso
        # antes do card que um job voltou para a fila com o video ja gerado e pago.
        # Aqui nao ha essa corrida: sem botao, nada foi enviado, e a reserva continua
        # estornavel.
        #
        # `SaldoInsuficiente` e o erro certo: ele faz o orquestrador trocar de conta em
        # vez de gastar as tres tentativas do job na mesma conta seca.
        await self._exigir_botao_de_enviar(pagina, s.conta.email)

        # A CONTAGEM DE CARDS E FEITA AQUI, DEPOIS DOS UPLOADS. Frame e referencia
        # enviados VIRAM TILES na grade ("Todas as midias" mostra os envios) — MEDIDO
        # por print em 2026-09-06: pares e trios de imagens identicas, que o usuario
        # leu como "gerou duas imagens". Contar antes dos uploads fazia
        # `_esperar_card_novo` voltar no instante do clique, por um tile que nao era a
        # geracao. O `antes` do inicio de `_gerar` continua valendo so como registro.
        antes = await pagina.locator(seletor_grade).count()
        # ⚠️ A CONTAGEM SOZINHA NAO SERVE DE SINAL: A GRADE TEM TETO DE 24 TILES.
        # MEDIDO 2026-09-09. A grade nao e virtualizada (rolar nao traz mais nada),
        # ela simplesmente para de renderizar em 24. Passado o teto, `count()` fica
        # cravado em 24 e nunca pode ficar MAIOR que `antes` — entao a comparacao
        # `count() > antes` vira sempre False e todo job vira "o Flow nao criou card
        # novo", com a imagem gerada e o credito gasto.
        #
        # Custou 161 falsos negativos: zero delas ate o job ~200, 49 de 50 na faixa
        # 300-349, e ZERO sucessos na faixa 350-399. Dois jobs em contas DIFERENTES
        # (341/conta 3 e 418/conta 1) registraram `antes=24`, o que descarta
        # coincidencia de conta. E o teto chega rapido porque tile nao e so geracao:
        # referencia enviada tambem vira tile (ver o comentario acima).
        #
        # Por isso a identidade passou a ser o `src` da miniatura, que e unico por
        # card. A contagem continua no log — ela AINDA e um sinal valido quando
        # cresce, so nao pode ser o unico.
        ids_antes = await grade.identidades_da_grade(pagina)
        d.passo(f"enviando o comando ({len(p.prompt)} caracteres; {antes} cards na grade"
                f"{' — NO TETO DE 24, contagem nao serve de sinal' if antes >= 24 else ''})")
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
        if not await self._esperar_card_novo(pagina, antes, d, ids_antes=ids_antes):
            if await grade.sem_credito(pagina):
                raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")
            raise GeracaoFalhou("o Flow nao criou card novo depois do envio")

        if await grade.sem_credito(pagina):
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
        if self._identidade_nova:
            self._identidade_do_card[(s.conta.id, id_remoto)] = self._identidade_nova
        else:
            # sem identidade nao se guarda nada: `_card` volta a usar a posicao, que
            # e o comportamento antigo. Registrar False aqui seria pior que omitir.
            self._identidade_do_card.pop((s.conta.id, id_remoto), None)
        return id_remoto

    async def _esperar_card_novo(self, pagina, antes: int, d: Diario,
                                 teto_ms: int = 120_000,
                                 graca_do_aviso_ms: int = 20_000,
                                 ids_antes: list[str] | None = None) -> bool:
        """`grade.esperar_novo`, guardando a identidade que ela achou.

        A identidade sai no retorno da funcao e descansa aqui ate `_gerar` decidir se
        vale guarda-la pela sessao inteira — quem tem vida longa e o transporte, nao a
        espera.
        """
        novo = await grade.esperar_novo(pagina, antes, d, teto_ms=teto_ms,
                                        graca_do_aviso_ms=graca_do_aviso_ms,
                                        ids_antes=ids_antes)
        self._identidade_nova = novo.identidade
        return novo.apareceu

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

        # FRAMES nao passa por aqui: os arquivos dele sobem ANTES de trocar de modo, e
        # os slots sao preenchidos por `_preencher_slots`. Ver `_gerar`.
        if p.modo is Modo.FRAMES2V:
            raise GeracaoFalhou(
                "frames2v nao usa `_anexar` — o envio acontece antes de configurar")

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

    async def _preencher_slots(self, pagina, frames: list[dict], d: Diario) -> None:
        """Poe cada frame ja enviado no seu slot da barra de Frames.

        `frames` vem de `_enviar_arquivos`, na ordem [inicial, final] — o final e
        opcional.

        A ORDEM DOS SLOTS E POSICIONAL, e so e aceitavel por tres razoes juntas: a
        barra tem exatamente dois, o do meio se identifica pela ligadura `swap_horiz`
        (que nao e traduzida, e por isso fica de fora do seletor), e cada slot e
        CONFERIDO depois de preenchido. Sem a conferencia isto seria a mesma aposta que
        ja mandou video do usuario para a lixeira.
        """
        slots = pagina.locator(sel.SLOT_FRAME)
        quantos = await slots.count()
        if quantos != 2:
            raise GeracaoFalhou(
                f"a barra de frames deveria ter 2 slots (inicio e fim), tem {quantos}")

        for n, item in enumerate(frames[:2]):
            qual = "inicio" if n == 0 else "fim"
            d.passo(f"frame {qual}: {item['nome']}")
            quanto = d.cronometro()
            await acoes.sem_veu(pagina)
            await slots.nth(n).click(timeout=15_000)

            # o popover do slot NAO tem abas — o sinal e o elemento nomeado
            if not await acoes.esperar(
                    pagina, lambda: acoes.tem(pagina, sel.POPOVER_SLOT), d,
                    f"o popover do slot {qual}", teto_ms=30_000):
                raise GeracaoFalhou(f"o slot {qual} nao abriu o seletor de midia")

            alvo = pagina.locator(sel.ITEM_MIDIA_POR_ID.format(id=item["id"]))
            analise.exigir_unico(await alvo.count(), f"item {item['id']}",
                                 f"frame {qual} no acervo")
            await alvo.first.click(timeout=15_000)
            await pagina.wait_for_timeout(1200)
            incluir = pagina.locator(sel.BOTAO_INCLUIR_NO_COMANDO)
            if await incluir.count() == 1:
                await incluir.first.click(timeout=15_000)

            if not await acoes.esperar(
                    pagina, lambda n=n: self._slot_cheio(pagina, n), d,
                    f"o slot {qual} mostrar a imagem", teto_ms=30_000):
                raise GeracaoFalhou(f"{item['nome']} nao entrou no slot {qual}")
            d.passo(f"frame {qual} posto em {quanto():.1f}s")

    async def _slot_cheio(self, pagina, indice: int) -> bool:
        """Se aquele slot ja mostra uma imagem. E a conferencia que valida a posicao.

        POR LOCATOR, NAO POR `evaluate`. `SLOT_FRAME` usa `:not(:has-text(...))`, que e
        pseudo-classe DO PLAYWRIGHT e nao CSS: passar isso para `querySelectorAll`
        estoura com "is not a valid selector" — e o erro fala de sintaxe quando o
        seletor estava certo, so no lugar errado.
        """
        slots = pagina.locator(sel.SLOT_FRAME)
        if await slots.count() <= indice:
            return False
        return await slots.nth(indice).locator("img, video").count() > 0

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
        return await self._enviar_no_seletor_aberto(pagina, arquivos, d)

    async def _enviar_no_seletor_aberto(self, pagina, arquivos: list[str],
                                        d: Diario) -> list[dict]:
        """A parte do envio que supoe o seletor de midia do `+` JA ABERTO.

        Existe separada de `_enviar_arquivos` para o caso em que o painel ja esta
        aberto e reabri-lo custaria os ~12 s de `_abrir_seletor_midia`.

        E O PAINEL DO `+`, E SO ELE. O popover do slot de frame NAO ENVIA ARQUIVO:
        `flow-media-upload` existe la mas fica vazio e invisivel em janela maximizada,
        que e a condicao em que o transporte roda (MEDIDO 2026-09-05, ver
        `seletores.POPOVER_SLOT`). E por isso que `_gerar` sai de Frames para
        `ingredients2v` ANTES de enviar os frames — enquanto o `+` ainda existe.
        """
        antes = await self._acervo_assentado(pagina, d)
        ids_antes = {i for i in (analise.id_da_midia(x.get("src")) for x in antes) if i}
        nomes = {Path(a).name for a in arquivos}

        # Aqui havia um desvio para `sel.BOTAO_UPLOAD_FRAME` quando "enviar midia" nao
        # estava na tela — um atributo que NUNCA EXISTIU em `seletores.py`. Vinha da
        # primeira medicao do popover do slot, feita em janela pequena (onde aparece o
        # `flow-mobile-add-menu`, esse sim com upload proprio) e corrigida no mesmo dia
        # pela medicao em janela maximizada. O ramo nunca foi avaliado, porque
        # `_abrir_seletor_midia` levanta se o `+` nao abrir — mas o dia em que fosse
        # daria `AttributeError`, e nao a mensagem que diz o que fazer.
        if await pagina.locator(sel.BOTAO_ENVIAR_MIDIA).count() == 0:
            raise GeracaoFalhou(
                "o painel aberto nao tem 'enviar midia': provavelmente e o popover do "
                "slot de frame, que so escolhe do acervo. O envio precisa acontecer "
                "pelo `+`, antes de trocar para o modo Frames.")
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

        if not await acoes.esperar(pagina, subiram, d, "as miniaturas do envio",
                                   teto_ms=240_000, cada=1000):
            raise GeracaoFalhou(
                f"so {len(novos)} de {len(arquivos)} arquivo(s) ficaram prontos "
                "no acervo")
        if len({i["id"] for i in novos}) != len(novos):
            raise GeracaoFalhou(f"os itens enviados repetem id: {[i['id'] for i in novos]}")
        return novos[:len(arquivos)]

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

        if not await acoes.esperar(pagina, entrou, d,
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
            # sem `+` na tela (modo FRAMES) nao ha interruptor para esperar
            if await pagina.locator(sel.BOTAO_ANEXAR).count() == 0:
                return True
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
        # O `+` NAO EXISTE EM TODO MODO. MEDIDO 2026-09-05: em FRAMES a barra vira
        # `flow-ingredient-bar` com os slots `inicio`/`fim`, e `flow-add-menu` fica sem
        # botao clicavel. Ler `aria-expanded` dele ali estoura com
        #     TimeoutError: waiting for locator("flow-add-menu button")
        # — e o erro fala do `+` quando o fato e que aquele modo nao tem `+`.
        #
        # Sem o `+`, sobram as outras duas condicoes, que ja bastam: o overlay visivel
        # E as abas montadas. O `aria-expanded` continua valendo onde existe, porque e
        # ele que exclui o painel que esta SAINDO de cena pela animacao.
        gasto = 0
        while gasto < teto_ms:
            tem_mais = await pagina.locator(sel.BOTAO_ANEXAR).count() > 0
            declarado = True
            if tem_mais:
                declarado = await pagina.locator(sel.BOTAO_ANEXAR).first.get_attribute(
                    sel.ATRIBUTO_ANEXAR) == sel.ANEXAR_ABERTO
            if (declarado
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
        await self._abrir_seletor_midia(pagina, d)

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
    async def _card(self, pagina, id_remoto: str, conta_id: int | None = None):
        """`grade.achar` com a memoria de identidades DESTE transporte."""
        return await grade.achar(pagina, id_remoto, conta_id, self._identidade_do_card)

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        """Envolvido em print-em-falha e traco-em-falha, como `gerar` e `baixar`.

        ⚠️ ESTE ENVELOPE FALTAVA, e a falta apareceu do pior jeito. RELATADO pelo
        usuario 2026-09-09: um job falhou em `consultar` e o diagnostico local
        respondeu "nao consigo afirmar a causa, nao ha print da tela" — e sugeriu
        mexer nos seletores do Flow, que estavam certos.

        `consultar` e o metodo que roda a CADA volta do polling; era o unico dos tres
        que falhava as cegas, justamente o com mais chance de encontrar uma mudanca
        de interface.

        ⚠️ Nao confundir com a auto-cura: ela so entra quando um SELETOR devolve zero
        candidatos. Erro de logica nossa depois de uma leitura bem-sucedida nao tem
        seletor para curar — e curar ali mascararia o defeito em vez de mostra-lo.
        """
        try:
            return await self._consultar(s, id_remoto)
        except (SaldoInsuficiente, LimiteDeUso):
            # nao sao falha de tela: sao decisoes de roteamento de conta, e a tela
            # nesse instante nao diz nada que o print ajude a entender
            raise
        except Exception as e:
            d = self._diario(s.conta.id)
            d.passo(f"FALHOU em consultar {id_remoto}: {type(e).__name__}: {e}")
            caminho = None
            try:
                if not s.handle[2].is_closed():
                    caminho = await self.print_de_falha(
                        s.handle[2], f"consultar {type(e).__name__}")
            except Exception:
                pass
            self._anotar_falha(s, caminho, d)
            await self._traco_da_falha(s)
            raise

    async def _consultar(self, s: Sessao, id_remoto: str) -> Estado:
        """Le o estado pela GRADE, nunca pela conversa.

        O agente so responde quando perguntado; a grade atualiza sozinha. E nao se
        contam elementos <video> soltos: o Flow usa <video> em outros lugares da tela,
        e o contador declararia "pronto" antes de existir card.
        """
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto, s.conta.id)

        if await grade.falhou(card):
            # ⚠️ LIMITE DE USO NAO SE RESOLVE REPETINDO — se resolve TROCANDO DE CONTA.
            #
            # RELATADO pelo usuario 2026-09-09, com print: o card dizia "Voce chegou ao
            # limite de uso. Tente de novo mais tarde." e o painel do FlowPool mostrava
            # o job como GERANDO. O robo ficava martelando a mesma conta, "gastando
            # horas para gerar uma imagem".
            #
            # A conta ainda MOSTRA saldo — o limite e de uso, nao de credito —, entao
            # nada nesta camada percebia. E `redo` na mesma conta e garantido falhar de
            # novo: e o mesmo limite.
            #
            # `SaldoInsuficiente` e o erro certo e ja existia pronto: o orquestrador
            # estorna o credito reservado (o card confirma "Nao houve cobranca"), marca
            # a conta com prazo em `seca_ate`, NAO gasta tentativa do job e devolve para
            # a fila — outra conta pega na hora.
            if await grade.por_limite_de_uso(card):
                self._diario(s.conta.id).passo(
                    f"{id_remoto}: o Flow recusou por LIMITE DE USO, nao por falta de "
                    f"credito. Repetir aqui nao adianta — trocando de conta.")
                raise LimiteDeUso(
                    f"conta {s.conta.email} atingiu o limite de uso do Flow "
                    f"(sem cobranca por esta geracao; o saldo continua valendo)")

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
                # ⚠️ O `redo` refaz a geracao e a miniatura ganha um `src` NOVO. A
                # identidade guardada em `_gerar` fica velha nesse instante, e mante-la
                # faria `_card` procurar para sempre um tile que nao existe mais —
                # travando o polling do proprio job que se esta tentando salvar.
                # Sem identidade, `_card` volta a usar a posicao, que aqui e o certo:
                # o card refeito ocupa o mesmo lugar.
                self._identidade_do_card.pop((s.conta.id, id_remoto), None)
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
                if not await grade.esperar_sair_da_falha(
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

        # A DECISAO E PURA (`analise.estado_do_card`) e a ORDEM dos sinais e a decisao:
        # porcentagem antes de midia. MEDIDO por print em 2026-09-06 (job 189): um
        # video a 8% ja tem uma <img> borrada no card; olhar a midia primeiro dava
        # PRONTO e 22 tentativas de baixar um video que ainda nao existia.
        texto = dom.seguro(await card.inner_text(timeout=10_000))
        tem_porcentagem = re.search(sel.PADRAO_PORCENTAGEM, texto) is not None
        tem_midia = await card.locator(sel.CARD_MIDIA).count() > 0
        chave = (s.conta.id, id_remoto)
        if tem_porcentagem or tem_midia:
            self._parado.pop(chave, None)
        else:
            self._parado.setdefault(chave, self._relogio())
        parado_ha = (self._relogio() - self._parado[chave]) if chave in self._parado else None
        veredito = analise.estado_do_card(False, tem_porcentagem, tem_midia, parado_ha)
        if veredito == "rodando":
            return Estado.RODANDO
        if veredito == "pronto":
            return Estado.PRONTO

        # SEM MIDIA E SEM PORCENTAGEM POR `SEM_PROGRESSO_S` = PAROU DE PROGREDIR.
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
        # A PACIENCIA E O QUE TORNA ISSO SEGURO, e ela e POR TEMPO. Era por numero de
        # leituras (15), e quando o polling caiu de 5 s para 2 s o prazo caiu de 75 s
        # para 30 s sem ninguem decidir isso: toda imagem lenta virava "falhou" aos
        # 36 s e a retomada a achava pronta 4 s depois (jobs 145-158, 2026-09-06).
        self._parado.pop(chave, None)
        self._diario(s.conta.id).passo(
            f"{id_remoto}: {parado_ha:.0f}s sem midia e sem porcentagem — o card "
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
            caminho = None
            try:
                if not s.handle[2].is_closed():
                    caminho = await self.print_de_falha(
                        s.handle[2], f"baixar {type(e).__name__}")
            except Exception:
                pass
            self._anotar_falha(s, caminho, d)
            await self._traco_da_falha(s)
            raise

    async def _baixar(self, s: Sessao, id_remoto: str, destino: Path,
                      d: Diario) -> Path:
        """`download.baixar` com o que o transporte lembra desta sessao.

        O card sai daqui achado, e nao de dentro do download: quem sabe achar tile na
        grade e o modulo da grade, e o modo do job so existe nesta instancia.
        """
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto, s.conta.id)
        return await download.baixar(
            pagina, card, id_remoto, destino, d,
            modo=self._modo_do_card.get((s.conta.id, id_remoto)), cura=self.cura)
