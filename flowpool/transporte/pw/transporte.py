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
from flowpool.transporte.pw import seletores as sel
from flowpool.transporte.pw.sessao import abrir_contexto, interpretar_sessao

# Chave de `sel.BAIXAR` por modo. 4K fica de fora dos dois: exige plano pago.
RESOLUCAO = {
    Modo.IMAGEM: "1k",          # "1K Tamanho original"
    Modo.T2V: "720p",
    Modo.FRAMES2V: "720p",
    Modo.INGREDIENTS2V: "720p",
}
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
        # `baixar` recebe so o id_remoto, e o menu de resolucao e diferente entre
        # imagem e video. Guardar o modo no momento de gerar e mais barato do que
        # adivinhar pelo DOM — e adivinhar errado baixaria a resolucao errada calado.
        self._modo_do_card: dict[tuple[int, str], Modo] = {}

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

    async def _configurar(self, pagina, p: Pedido) -> str:
        """Abre o chip, ajusta tudo e CONFERE. Devolve o texto final do chip.

        Nada de passo tolerante. Um `except: segue` engoliu um clique de duracao que
        nao pegou e entregou 8s onde foram pedidos 4s — com credito debitado, arquivo
        na pasta e ninguem percebendo. Se o chip nao confirmar o estado, e erro.
        """
        modo = p.modo.value
        # recusa ANTES de tocar no chip, com a reserva de credito ainda estornavel
        analise.exigir_proporcao_do_modo(modo, p.proporcao)

        # O MODO DE OPERACAO vem antes do chip, e nao por gosto de ordem: com o agente
        # ligado o chip NAO EXISTE na barra (medido), e a linha seguinte morreria num
        # timeout que fala do chip e nunca da causa.
        await self._exigir_agente_desligado(pagina)

        await pagina.locator(sel.CHIP_CONFIG).first.click(timeout=15_000)
        painel = self._painel(pagina)
        await painel.wait_for(timeout=15_000)

        # ordem importa: o modo redefine as listas de proporcao, duracao e modelo — e
        # tambem a propria lista de MODOS, o que faz dos submodos duas etapas
        await self._escolher_modo(pagina, painel, modo)

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
        # `None` em imagem: o painel nao tem grupo de duracao naquele modo, e procurar
        # o botao ali derrubaria toda geracao de imagem. Nos tres modos de video vale a
        # duracao DO PEDIDO — ate a Task 15 era uma constante de 8s aqui, e todo video
        # saia 70% mais caro do que o de 4s sem ninguem ver.
        duracao = None if p.modo is Modo.IMAGEM else p.duracao
        if duracao:
            await self._clicar_unico(painel, sel.DURACAO[duracao], "duracao")
        await self._clicar_unico(painel, sel.QUANTIDADE[p.quantidade], "quantidade")

        # O CUSTO ESTA NA TELA. Le-se aqui porque o painel ja esta aberto e a leitura
        # nao custa clique nenhum — ao contrario do saldo, que paga dois. Hoje isto e
        # log; ligar na reserva do orquestrador e passo proprio (ver seletores.py).
        await self._custo_anunciado(pagina)

        await self._fechar_painel(pagina)

        chip = await self._texto_do_chip(pagina)
        faltando = analise.faltando_no_chip(chip, modo, p.proporcao, p.quantidade, duracao)
        if faltando:
            raise GeracaoFalhou(
                f"chip nao confirma a configuracao: diz {chip!r}, faltou {faltando}")
        # a tolerancia do rotulo de submodo nao pode ser calada: esta linha e a medicao
        # que falta sobre o que o chip escreve em `frames2v` e `ingredients2v`
        aviso = analise.confirmacao_fraca(chip, modo)
        if aviso:
            print("chip:", aviso)
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
        """Envolvido em print-em-falha: interface so se diagnostica vendo a tela."""
        _, _, pagina = s.handle
        try:
            return await self._gerar(s, p, pagina)
        except Exception as e:
            await self.print_de_falha(pagina, f"gerar {p.modo.value} {type(e).__name__}")
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

        await self._configurar(pagina, p)
        await self._anexar(pagina, p)

        # ARMADILHA: `input[aria-label="Texto editavel"]` e o NOME DO PROJETO. Escrever
        # ali renomeia o projeto em vez de gerar, e o sintoma e "nada acontece" — que e
        # o pior tipo de bug aqui. O campo certo e o unico contenteditable visivel.
        campo = pagina.locator(sel.CAMPO_PROMPT).first
        await campo.click(timeout=15_000)
        await campo.fill("")
        await self._escrever_prompt(pagina, p.prompt)

        # dois botoes contem a palavra "Criar": anexar e enviar. So a ligadura separa.
        await pagina.locator(sel.BOTAO_ENVIAR).first.click(timeout=15_000)

        if await self._sem_credito(pagina):
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")

        # o card novo aparecendo e a UNICA confirmacao de que o Flow aceitou o pedido
        await self._esperar_card_novo(pagina, antes)
        indice = 0 if sel.CARDS_NOVOS_PRIMEIRO else antes
        id_remoto = f"card-{indice}"
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

    async def _esperar_card_novo(self, pagina, antes: int, teto_ms: int = 120_000) -> None:
        gasto = 0
        while gasto < teto_ms:
            if await pagina.locator(sel.CARD_GRADE).count() > antes:
                return
            await pagina.wait_for_timeout(2000)
            gasto += 2000
        raise GeracaoFalhou("o Flow nao criou card novo depois do envio")

    async def _anexar(self, pagina, p: Pedido) -> None:
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
        for arquivo in arquivos:
            await self._incluir_referencia(pagina, arquivo)

        # CONFERE, nao confia. Este e o passo que faltava: sem ele, uma referencia que
        # nao entrou some em silencio e a geracao sai por texto puro — que foi
        # exatamente o defeito. Divergencia e ERRO, com a reserva ainda estornavel.
        incluidas = await pagina.locator(sel.REFERENCIA_ANEXADA).count()
        if incluidas != len(arquivos):
            raise GeracaoFalhou(
                f"anexo nao confirma: pedi {len(arquivos)} referencia(s) e a barra do "
                f"prompt mostra {incluidas}")

    async def _limpar_referencias(self, pagina) -> None:
        """Zera o rascunho antes de anexar. Referencia herdada e geracao errada.

        Uma tentativa que morreu no meio deixa a miniatura na barra do prompt, e o
        Flow SOMA a proxima em vez de trocar (medido: 1 -> 2 -> 3). Sem limpar, a
        segunda tentativa do mesmo job geraria com o dobro das referencias.
        """
        if await pagina.locator(sel.REFERENCIA_ANEXADA).count() == 0:
            return
        await self._clicar_unico(pagina, sel.BOTAO_APAGAR_COMANDO, "apagar comando")
        for _ in range(20):
            await pagina.wait_for_timeout(250)
            if await pagina.locator(sel.REFERENCIA_ANEXADA).count() == 0:
                return
        raise GeracaoFalhou(
            "as referencias de uma tentativa anterior nao sairam da barra do prompt")

    async def _abrir_seletor_midia(self, pagina, tentativas: int = 3):
        """Abre o seletor de midia pelo `+`, tentando de novo se ele nao vier.

        Um clique que nao abre nada acontece: o botao muda de posicao quando ja ha
        referencia anexada (a caixa do prompt cresce), e um clique perdido nao levanta
        erro — so nao abre. Insistir e barato; desistir custa a geracao inteira e as
        tres tentativas do job.

        Nao esconde problema de verdade: se as tres voltas falharem, o erro sobe com o
        print salvo. O que ele evita e o job morrer por um clique que caiu no vazio.
        """
        for volta in range(tentativas):
            await pagina.locator(sel.BOTAO_ANEXAR).first.click(timeout=15_000)
            seletor = pagina.locator(sel.SELETOR_MIDIA).first
            try:
                await seletor.wait_for(timeout=8_000)
                return seletor
            except Exception:
                if volta + 1 < tentativas:
                    print(f"o seletor de midia nao abriu (volta {volta + 1}); repetindo")
                    await pagina.keyboard.press("Escape")
                    await pagina.wait_for_timeout(1200)
        raise GeracaoFalhou(
            f"o seletor de midia nao abriu depois de {tentativas} cliques no anexar")

    async def _incluir_referencia(self, pagina, arquivo: str) -> None:
        """Uma volta completa do seletor de midia: abrir, enviar, esperar, incluir."""
        seletor = await self._abrir_seletor_midia(pagina)

        # o `input[type=file]` vive DENTRO do seletor; nao se clica em "Enviar midia"
        await pagina.locator(sel.UPLOAD).first.set_input_files([arquivo])
        # o aviso de direitos e vigiado DENTRO da espera, nao antes dela — ver
        # `_esperar_item_pronto`
        indice = await self._esperar_item_pronto(pagina, Path(arquivo).name)
        itens = pagina.locator(sel.ITEM_MIDIA)
        # CONFERIR antes de clicar. MEDIDO: clicar num item JA selecionado o DESmarca,
        # e sem selecao o botao "Incluir no comando" some da tela — o clique cego
        # derrubaria a geracao com "esperava 1 candidato, achei 0".
        if await itens.nth(indice).get_attribute("aria-selected") != "true":
            await itens.nth(indice).click(timeout=15_000)
        await self._clicar_unico(pagina, sel.BOTAO_INCLUIR_NO_COMANDO,
                                 "Incluir no comando")
        # o seletor fecha sozinho ao incluir; espera-se o ESTADO, nunca um sono
        await seletor.wait_for(state="hidden", timeout=15_000)

    async def _esperar_item_pronto(self, pagina, nome: str, teto_ms: int = 180_000) -> int:
        """Espera o upload TERMINAR e devolve o indice do item na lista.

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
            itens = await pagina.evaluate(dom.ITENS_DE_MIDIA, sel.ITEM_MIDIA)
            indice = analise.escolher_item_enviado(itens, nome)
            if indice is not None:
                return indice
            visto = visto or any(
                analise.nome_do_item(i.get("texto", "")) == nome for i in itens)
            await pagina.wait_for_timeout(500)
            gasto += 500
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
        return (await card.locator(sel.CARD_FALHA).count() > 0
                or await card.locator(sel.CARD_FALHA_TITULO).count() > 0)

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
        chave = sel.BAIXAR[
            RESOLUCAO[self._modo_do_card.get((s.conta.id, id_remoto), Modo.T2V)]]
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
