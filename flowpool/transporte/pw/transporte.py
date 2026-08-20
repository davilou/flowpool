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

from flowpool.nucleo.tipos import Conta, Modo
from flowpool.transporte.base import (
    Estado, GeracaoFalhou, Pedido, ProjetoRemoto, SaldoInsuficiente, Sessao, SessaoMorta,
)
from flowpool.transporte.pw import analise, dom
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.sessao import abrir_contexto, interpretar_sessao

# Fixa ate `Pedido` ganhar o campo — ver "Pendencias declaradas" na Task 11 do plano.
DURACAO_PADRAO = "8s"
# Chave de `sel.BAIXAR` por modo. 4K fica de fora dos dois: exige plano pago.
RESOLUCAO = {
    Modo.IMAGEM: "1k",          # "1K Tamanho original"
    Modo.T2V: "720p",
    Modo.FRAMES2V: "720p",
    Modo.INGREDIENTS2V: "720p",
}
# Quantas vezes repetir um card falho pelo proprio botao `redo`, que NAO cobra de novo.
MAX_REPETICOES = 2


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


class TransportePlaywright:
    def __init__(self, timeout_ms: int = 600_000) -> None:
        self.timeout_ms = timeout_ms
        self._repeticoes: dict[str, int] = {}
        # `baixar` recebe so o id_remoto, e o menu de resolucao e diferente entre
        # imagem e video. Guardar o modo no momento de gerar e mais barato do que
        # adivinhar pelo DOM — e adivinhar errado baixaria a resolucao errada calado.
        self._modo_do_card: dict[str, Modo] = {}

    # ------------------------------------------------------------------ sessao
    async def abrir_sessao(self, conta: Conta) -> Sessao:
        # o proxy e DA CONTA, nao do processo: e assim que cada conta sai por um IP
        # proprio e o Google nao consegue correlacionar as vinte entre si. Vazio e o
        # normal hoje, e `abrir_contexto` trata None como "sem proxy".
        pw, ctx = await abrir_contexto(Path(conta.perfil), conta.proxy)
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        try:
            # a prova de sessao viva e a API, nao a tela: deslogado o Flow devolve {}
            # com HTTP 200, e a tela leva segundos para trocar para o botao de login
            resposta = await pagina.request.get(sel.URL_BASE + sel.URL_SESSAO)
            interpretar_sessao(await resposta.json())
        except SessaoMorta:
            await ctx.close()
            await pw.stop()
            raise SessaoMorta(f"sessao de {conta.email} caiu")
        return Sessao(conta=conta, handle=(pw, ctx, pagina))

    async def fechar(self, s: Sessao) -> None:
        pw, ctx, _ = s.handle
        await ctx.close()
        await pw.stop()

    # ------------------------------------------------------------------- saldo
    async def saldo(self, s: Sessao) -> int:
        """O saldo NAO fica na tela do projeto: mora no menu do avatar.

        Custa dois cliques (abrir e fechar). Por isso o orquestrador le uma vez por
        job, na reconciliacao, e nao a cada volta do polling.
        """
        _, _, pagina = s.handle
        await pagina.locator(sel.AVATAR_CONTA).first.click(timeout=15_000)
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
        await pagina.locator(sel.CARD_PROJETO).first.wait_for(timeout=30_000)
        # MEDIDO: o proprio <a> do card NAO tem texto — `inner_text()` volta vazio e
        # qualquer `.splitlines()[0]` estoura. O nome mora num ancestral. Sobe-se ate o
        # ultimo ancestral que ainda contem UM SO link de projeto: acima disso vem o
        # container da grade, cujo texto junta todos os projetos num so.
        pares = await pagina.evaluate(
            """(seletor) => [...document.querySelectorAll(seletor)].map(a => {
                 let no = a, card = a;
                 for (let i = 0; i < 6 && no.parentElement; i++) {
                   no = no.parentElement;
                   if (no.querySelectorAll(seletor).length === 1) card = no; else break;
                 }
                 return {href: a.getAttribute('href'),
                         texto: (card.innerText || '').trim()};
               })""",
            sel.CARD_PROJETO,
        )
        achados: list[ProjetoRemoto] = []
        for par in pares:
            id_remoto = analise.id_do_href(par["href"])
            if not id_remoto:
                continue
            # `sem_segredo` e nao `seguro`: o nome e comparado com `job.produto`, entao
            # tirar o acento aqui faria "Panelas Monaco" deixar de casar consigo mesmo e
            # a adocao criaria um projeto novo a cada geracao.
            linhas = dom.sem_segredo(par["texto"]).splitlines()
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
        await pagina.locator(sel.BOTAO_NOVO_PROJETO).first.click(timeout=30_000)
        await pagina.wait_for_url(re.compile(re.escape(sel.PADRAO_PROJETO)), timeout=30_000)
        id_remoto = analise.id_do_href(pagina.url)
        if not id_remoto:
            raise GeracaoFalhou(f"projeto {nome!r} nao abriu depois de criado")

        await pagina.locator(sel.BOTAO_MAIS_OPCOES).first.click(timeout=15_000)
        await pagina.locator(sel.MENU_RENOMEAR).first.click(timeout=15_000)
        campo = pagina.locator(sel.CAMPO_NOME_PROJETO).first
        await campo.wait_for(timeout=15_000)
        await campo.fill(nome)
        await pagina.locator(sel.BOTAO_CONCLUIR_NOME).first.click(timeout=15_000)
        await pagina.wait_for_timeout(1500)
        return id_remoto

    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None:
        """NUNCA por goto. Deep link derruba a app com "Application error".

        O Next espera chegar por navegacao interna. Vai-se a lista e CLICA-SE no card.
        """
        _, _, pagina = s.handle
        if sel.PADRAO_PROJETO + id_remoto in pagina.url:
            return
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        # A lista e preguicosa (IntersectionObserver). Contar antes de ela carregar da
        # zero e produz a mensagem errada: "nao e da conta" quando na verdade a pagina
        # ainda estava vazia. Espera-se o card especifico; se ele nunca aparecer, ai sim
        # a conclusao vale.
        card = pagina.locator(f'a[href*="{id_remoto}"]')
        try:
            await card.first.wait_for(timeout=30_000)
        except Exception:
            pass
        if await card.count() == 0:
            # projeto de outra conta simplesmente nao aparece na lista. Seguir daqui
            # geraria no projeto errado — ou em nenhum.
            raise GeracaoFalhou(
                f"projeto {id_remoto} nao esta na lista de {s.conta.email}")
        await card.first.click()
        await pagina.locator(sel.CAMPO_PROMPT).first.wait_for(timeout=30_000)

    # -------------------------------------------------------------------- chip
    def _painel(self, pagina):
        return pagina.locator(_medido("PAINEL_CHIP", sel.PAINEL_CHIP)).first

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
        analise.exigir_unico(await loc.count(), seletor, o_que)
        await loc.click(timeout=15_000)

    async def _configurar(self, pagina, p: Pedido) -> str:
        """Abre o chip, ajusta tudo e CONFERE. Devolve o texto final do chip.

        Nada de passo tolerante. Um `except: segue` engoliu um clique de duracao que
        nao pegou e entregou 8s onde foram pedidos 4s — com credito debitado, arquivo
        na pasta e ninguem percebendo. Se o chip nao confirmar o estado, e erro.
        """
        modo = p.modo.value
        # recusa ANTES de tocar no chip, com a reserva de credito ainda estornavel
        analise.exigir_proporcao_do_modo(modo, p.proporcao)

        await pagina.locator(sel.CHIP_CONFIG).first.click(timeout=15_000)
        painel = self._painel(pagina)
        await painel.wait_for(timeout=15_000)

        # ordem importa: o modo redefine as listas de proporcao, duracao e modelo
        await self._clicar_unico(painel, sel.BOTAO_MODO[modo], "modo")
        await pagina.wait_for_timeout(1200)

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

        await self._clicar_unico(painel, sel.PROPORCAO[p.proporcao], "proporcao")
        duracao = None if p.modo is Modo.IMAGEM else DURACAO_PADRAO
        if duracao:
            await self._clicar_unico(painel, sel.DURACAO[duracao], "duracao")
        await self._clicar_unico(painel, sel.QUANTIDADE[p.quantidade], "quantidade")

        await pagina.keyboard.press("Escape")
        await pagina.wait_for_timeout(1200)

        chip = await self._texto_do_chip(pagina)
        faltando = analise.faltando_no_chip(chip, modo, p.proporcao, p.quantidade, duracao)
        if faltando:
            raise GeracaoFalhou(
                f"chip nao confirma a configuracao: diz {chip!r}, faltou {faltando}")
        return chip

    # ------------------------------------------------------------------- gerar
    async def gerar(self, s: Sessao, p: Pedido) -> str:
        _, _, pagina = s.handle
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

        await self._configurar(pagina, p)
        await self._anexar(pagina, p)

        # ARMADILHA: `input[aria-label="Texto editavel"]` e o NOME DO PROJETO. Escrever
        # ali renomeia o projeto em vez de gerar, e o sintoma e "nada acontece" — que e
        # o pior tipo de bug aqui. O campo certo e o unico contenteditable visivel.
        campo = pagina.locator(sel.CAMPO_PROMPT).first
        await campo.click(timeout=15_000)
        await campo.fill("")
        await campo.type(p.prompt, delay=8)

        # dois botoes contem a palavra "Criar": anexar e enviar. So a ligadura separa.
        await pagina.locator(sel.BOTAO_ENVIAR).first.click(timeout=15_000)

        if await self._sem_credito(pagina):
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")

        # o card novo aparecendo e a UNICA confirmacao de que o Flow aceitou o pedido
        await self._esperar_card_novo(pagina, antes)
        indice = 0 if sel.CARDS_NOVOS_PRIMEIRO else antes
        id_remoto = f"card-{indice}"
        self._repeticoes[id_remoto] = 0
        self._modo_do_card[id_remoto] = p.modo
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

    async def _esperar_card_novo(self, pagina, antes: int, teto_ms: int = 120_000) -> None:
        gasto = 0
        while gasto < teto_ms:
            if await pagina.locator(sel.CARD_GRADE).count() > antes:
                return
            await pagina.wait_for_timeout(2000)
            gasto += 2000
        raise GeracaoFalhou("o Flow nao criou card novo depois do envio")

    async def _anexar(self, pagina, p: Pedido) -> None:
        """Um unico `input[type=file]` (multiple, accept=image/*) serve a tudo.

        Quem decide o que cada arquivo significa e o MODO ja escolhido no chip, nao o
        seletor: em `crop_free` os arquivos viram frame inicial e final, em
        `chrome_extension` viram referencias. Por isso `_configurar` roda ANTES daqui.
        """
        if p.modo is Modo.INGREDIENTS2V:
            arquivos = [str(Path(r)) for r in p.referencias[:3]]
        elif p.modo is Modo.FRAMES2V:
            arquivos = [str(Path(x)) for x in (p.frame_inicial, p.frame_final) if x]
        else:
            return
        if not arquivos:
            raise GeracaoFalhou(f"modo {p.modo.value} sem arquivo para anexar")
        await pagina.locator(sel.UPLOAD).first.set_input_files(arquivos)
        await pagina.wait_for_timeout(3000)

    # --------------------------------------------------------------- consultar
    async def _card(self, pagina, id_remoto: str):
        indice = int(id_remoto.split("-")[1])
        cards = pagina.locator(_medido("CARD_GRADE", sel.CARD_GRADE))
        if await cards.count() <= indice:
            raise GeracaoFalhou(f"{id_remoto} sumiu da grade")
        return cards.nth(indice)

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        """Le o estado pela GRADE, nunca pela conversa.

        O agente so responde quando perguntado; a grade atualiza sozinha. E nao se
        contam elementos <video> soltos: o Flow usa <video> em outros lugares da tela,
        e o contador declararia "pronto" antes de existir card.
        """
        _, _, pagina = s.handle
        card = await self._card(pagina, id_remoto)

        if await card.locator(sel.CARD_FALHA).count() > 0:
            vezes = self._repeticoes.get(id_remoto, 0)
            if vezes < MAX_REPETICOES:
                # `redo` repete SEM cobrar de novo. Repetir aqui, dentro de `consultar`,
                # e o que faz o orquestrador nem tomar conhecimento: `_esperar` ve
                # RODANDO e continua o polling, sem gastar tentativa do job nem
                # reabrir navegador. O usuario decidiu que nao quer notificacao — entao
                # a repeticao tem que ser invisivel e barata.
                self._repeticoes[id_remoto] = vezes + 1
                await self._clicar_unico(card, sel.BOTAO_REPETIR_CARD,
                                         f"repetir {id_remoto}")
                await pagina.wait_for_timeout(3000)
                return Estado.RODANDO
            # Sob o banner de alta demanda a falha e do Google, nao da conta. Levantar
            # SaldoInsuficiente ou SessaoMorta aqui marcaria a conta seca ou expirada e
            # um pico do Google derrubaria o pool inteiro. GeracaoFalhou nao marca nada.
            if await pagina.locator(sel.AVISO_ALTA_DEMANDA).count() > 0:
                raise GeracaoFalhou(
                    "Flow em alta demanda: falha do Google, a conta esta boa")
            return Estado.FALHOU

        if await card.locator(sel.CARD_MIDIA).count() > 0:
            return Estado.PRONTO
        return Estado.RODANDO

    # ------------------------------------------------------------------ baixar
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
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
        await pagina.wait_for_timeout(1500)

        # assumir VIDEO quando o modo nao e conhecido e o default seguro: pedir "720p"
        # num menu de imagem nao acha rotulo e `escolher_opcao` grita, enquanto pedir
        # "Tamanho original" num menu de video baixaria em silencio o errado.
        chave = sel.BAIXAR[RESOLUCAO[self._modo_do_card.get(id_remoto, Modo.T2V)]]
        submenu = pagina.locator(sel.MENU_CARD).last
        itens = submenu.locator(sel.MENU_ITEM)
        # os rotulos do submenu tem QUEBRA DE LINHA ("270p\nGIF animado"): passar o
        # rotulo inteiro para um seletor CSS estoura o parser. A escolha acontece sobre
        # o texto ja lido, por `analise.escolher_opcao`, que recusa empate.
        indice = analise.escolher_opcao(await itens.all_inner_texts(), chave)
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
        return real

    async def _logar_menu(self, pagina, menu) -> None:
        """Imprime os candidatos ANTES de clicar. Barato, e ja teria evitado um
        clipe do usuario indo para a lixeira."""
        itens = [dom.seguro(t) for t in
                 await menu.locator(sel.MENU_ITEM).all_inner_texts()]
        print("menu do card:", [i.splitlines()[0] for i in itens if i])
