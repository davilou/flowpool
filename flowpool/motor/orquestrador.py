"""Casa job com conta e conduz o ciclo de vida. Nao conhece Playwright."""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flowpool.dados.repo import Repo
from flowpool.motor import diagnostico
from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, liberar, reconciliar, reservar
from flowpool.nucleo.escolha import apta, escolher
from flowpool.nucleo.proxy import analisar, mascarar
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modo, SaudeSessao
from flowpool.transporte.base import (
    CardTrocado,
    Estado,
    FlowTransport,
    GeracaoFalhou,
    LimiteDeUso,
    Pedido,
    SaldoInsuficiente,
    Sessao,
    SessaoMorta,
)

MAX_TENTATIVAS = 3
ESPERA_RESET = timedelta(hours=1)
TIMEOUT_GERACAO = timedelta(minutes=10)
# De quanto em quanto o card e consultado durante a geracao. Era 5 s: o "pronto"
# chegava ate 5 s atrasado em TODO job, por uma leitura de DOM que custa
# milissegundos. 2 s corta a media de 2,5 s para 1 s por job (2026-09-06).
INTERVALO_POLLING = 2.0

# Quanto um job espera quando a conta que ele precisa esta OCUPADA — nao seca, nao
# expirada: ocupada. Sao coisas de escala diferente. Conta seca espera o reset diario
# (`ESPERA_RESET`, uma hora); conta ocupada espera o vizinho terminar, o que leva
# minutos. Adiar por uma hora um job que so esbarrou numa janela ocupada deixaria o
# pool ocioso com a fila cheia.
ESPERA_JANELA = timedelta(seconds=30)

# ⚠️ QUANTO O MOTOR ESPERA ENTRE UM DESPACHO E O SEGUINTE, e por que existe.
#
# `rodar` reivindica sem parar enquanto `passo` despachar — de proposito: e o que enche
# as janelas em vez de andar uma por vez. So que com tres contas livres e tres jobs na
# fila, as tres geracoes saem no MESMO SEGUNDO. MEDIDO 2026-09-09: os jobs 583, 584 e
# 585 foram despachados as 16:24:41, em tres contas Google diferentes, todas pelo mesmo
# IP (`proxy` vazio nas nove contas). O Flow recusou as tres com "Notamos uma atividade
# incomum. Aguarde alguns instantes... Nao houve cobranca por esta geracao" — o
# antiabuso do Google, reagindo ao que parece um robo porque E um robo.
#
# ⚠️ CORRECAO 2026-09-10: A CONCORRENCIA NAO ERA A CAUSA — era so o que estava na tela
# naquele dia. O job 598 foi recusado pelo MESMO texto em cinco contas (2, 3, 4, 5, 8)
# UMA DE CADA VEZ, 30 a 40 s entre elas, cada uma pelo seu proprio proxy: nem
# simultaneidade, nem IP comum. O que separava o robo do humano era `navigator.webdriver`
# (ver `sessao.argumentos_playwright`). ESTE ESPACO CONTINUA VALENDO — sair em rajada e
# ruim de todo jeito e ele custa zero numa fila de video —, mas nao e a explicacao do
# bloqueio, e tratar como se fosse manda a proxima investigacao para o lugar errado.
#
# O espaco NAO SERIALIZA NADA: as geracoes continuam correndo em paralelo, so nao
# COMECAM juntas. E ele desconta o tempo ja gasto, entao numa fila de videos de dois
# minutos ele custa zero — o proximo despacho ja chega atrasado por conta propria. Quem
# paga sao as rajadas de imagem, que e exatamente onde o bloqueio aparecia.
#
# O JITTER EXISTE PARA NAO RE-SINCRONIZAR. Espaco fixo desalinha a primeira rajada e
# realinha a seguinte: quatro jobs do mesmo tamanho terminam com a mesma distancia entre
# si e voltam a bater juntos. Uma fracao sorteada por cima quebra o compasso.
ESPACO_ENTRE_DESPACHOS = 4.0
JITTER_DO_DESPACHO = 0.5

# Quanto tempo o navegador de uma conta fica aberto, OCIOSO, esperando o proximo job.
#
# Abrir o Chrome, navegar ate o Flow, validar pelo avatar e fechar tudo custava 10 a
# 15 s por job — mais da metade do envelope de um job de imagem de 50 s (medido
# 2026-09-06), e o preco inteiro de cada retomada. Guardar a sessao entre jobs da
# mesma conta paga isso uma vez por rajada, nao uma vez por job.
#
# Tres minutos, e nao "para sempre": cada Chrome ocioso e ~500 MB, e a sessao do
# Google e um cookie que envelhece — o transporte confere `viva` antes de reusar, e
# uma sessao que morreu no meio e descartada e reaberta.
OCIOSIDADE_SESSAO = timedelta(minutes=3)

# Teto de geracoes ao mesmo tempo. Cada uma segura uma janela de Chrome e uma conta.
#
# 5 e o padrao decidido pelo usuario sobre a maquina dele: 62 GB de RAM, ~30 GB livres
# com o ComfyUI residente, e cada Chrome custa entre 400 MB e 1 GB. Sobra folga.
#
# Este numero raramente e o limite de verdade. O teto REAL e o menor entre tres coisas,
# e a que costuma mandar e a primeira:
#   1. quantas CONTAS estao livres — uma geracao por conta, sempre;
#   2. este teto;
#   3. a memoria, compartilhada com o ComfyUI quando ele estiver gerando junto.
# Se a maquina engasgar, baixar este numero e a primeira coisa a tentar, e a variavel
# de ambiente existe para isso.
MAX_SIMULTANEOS = 5

# Quantas vezes repetir SO O DOWNLOAD antes de desistir do job.
#
# MEDIDO 2026-09-05: o download de video derruba o Chrome de forma intermitente — 1
# sucesso em 3 no mesmo arquivo. Como o video ja existe e ja foi pago quando se chega
# aqui, repetir custa tempo e um navegador, e desistir custa o job inteiro.
#
# A TAXA FOI MEDIDA, e o numero sai dela. Quatro downloads seguidos do MESMO arquivo:
# falha, falha, falha, OK — ~25% de sucesso por tentativa.
#
#     teto  chance de pelo menos um passar (1 - 0.75^n)
#      8            90%
#     20            99,7%
#
# Com teto 8 um job em cada dez morria com o arquivo pronto do outro lado — foi o que
# aconteceu com o job 93. Cada tentativa custa ~10 s e uma abertura de navegador, entao
# 20 sao ~3 minutos no pior caso, contra perder um video ja pago.
#
# O teto continua existindo porque sem ele isto vira laco quente: um download que nunca
# funciona abriria e fecharia navegador para sempre.
MAX_DOWNLOADS = 20


def teto_do_ambiente(padrao: int = MAX_SIMULTANEOS) -> int:
    """Le `FLOWPOOL_MAX_SIMULTANEOS`, caindo no padrao quando o valor nao serve.

    Valor invalido nao derruba o servico: um erro de digitacao numa variavel de
    ambiente nao pode impedir o app de subir, e um teto errado se percebe olhando o
    painel. Minimo 1 — zero pararia a fila em silencio.
    """
    bruto = os.environ.get("FLOWPOOL_MAX_SIMULTANEOS", "").strip()
    if not bruto:
        return padrao
    try:
        return max(1, int(bruto))
    except ValueError:
        return padrao


def proxy_de_fuga_do_ambiente() -> str | None:
    """Le `FLOWPOOL_PROXY_FUGA`: por onde a conta recusada por antiabuso sai.

    Vazia e o normal: sem fuga, a recusa descansa a conta uma hora, como sempre.
    Ao contrario de `teto_do_ambiente`, URL invalida DERRUBA a subida
    (`ProxyInvalido`): um teto errado se percebe no painel, um proxy errado so se
    perceberia na primeira recusa — horas depois, com a fila parada e o Chrome
    abrindo pelo IP errado sem dizer nada.

    MEDIDO 2026-09-16: `socks5://127.0.0.1:40000` e o Cloudflare WARP em modo proxy
    (`warp-cli mode proxy`), gratuito; o IP de saida trocou a cada reconexao.
    """
    bruto = os.environ.get("FLOWPOOL_PROXY_FUGA", "").strip()
    if not bruto:
        return None
    analisar(bruto)   # levanta ProxyInvalido; o valor traduzido nao interessa aqui
    return bruto


def _extensao(modo: Modo) -> str:
    return ".png" if modo is Modo.IMAGEM else ".mp4"


class Orquestrador:
    """Reivindica jobs e os despacha em paralelo, um por conta.

    A INVARIANTE que organiza tudo aqui: um job por conta, um navegador por conta.
    Nao e preferencia — dois Chromes com o mesmo `--user-data-dir` nao coexistem: o
    segundo entrega a URL ao primeiro e morre, e o Playwright ve `TargetClosedError`.

    Ela vale de graca uma segunda coisa: como dois trabalhadores nunca tocam a mesma
    conta, nunca ha duas escritas concorrentes na mesma linha de `conta`. `salvar_conta`
    grava a linha inteira a partir de uma copia carregada no comeco do passo, e sem a
    exclusividade isso seria uma escrita perdida a cada cruzamento.

    O rastro de contas ocupadas mora AQUI, em memoria, e nao no banco: e estado de
    processo, nao dado do dominio. Um restart o limpa naturalmente, que e exatamente o
    certo — depois de um restart nao ha navegador nenhum aberto.
    """

    def __init__(self, repo: Repo, transporte: FlowTransport, raiz: Path | str,
                 max_simultaneos: int = MAX_SIMULTANEOS,
                 timeout_geracao: timedelta = TIMEOUT_GERACAO,
                 intervalo_polling: float = INTERVALO_POLLING,
                 espaco_entre_despachos: float = ESPACO_ENTRE_DESPACHOS,
                 diagnosticador: diagnostico.Diagnosticador | None = None,
                 proxy_fuga: str | None = None) -> None:
        self.repo = repo
        self.transporte = transporte
        self.raiz = Path(raiz)
        # Por onde sai a conta recusada por ANTIABUSO (ver `_ao_bater_limite`). None =
        # sem fuga, a conta descansa. `_em_fuga` e memoria de processo de proposito:
        # reiniciou, a conta volta ao IP de casa e, se for recusada, entra em fuga de
        # novo ao custo de uma recusa sem cobranca. Coluna no banco seria mais um
        # estado para o painel explicar, por um dado que se recupera sozinho.
        self.proxy_fuga = proxy_fuga
        self._em_fuga: set[int] = set()
        # Quem explica um job que falhou de vez (`motor/diagnostico.py`). `None` = sem
        # diagnostico, que e o padrao DE PROPOSITO: quem liga e o `__main__`, com o
        # modelo local; um teste que falha um job nao pode sair batendo em HTTP.
        self.diagnosticador = diagnosticador
        # tarefas de fundo que nao ocupam janela (diagnosticos em curso)
        self._fundo: set[asyncio.Task] = set()
        self.max_simultaneos = max(1, int(max_simultaneos))
        # parametrizados para o teste nao ter que esperar dez minutos de verdade
        self.timeout_geracao = timeout_geracao
        self.intervalo_polling = intervalo_polling
        # o espaco entre despachos, e o relogio/sono/sorteio que ele usa. Entram como
        # atributo — e nao `time`/`random`/`asyncio` chamados direto no meio do metodo —
        # porque e o que permite testar a conta do espaco sem esperar por ela.
        self.espaco_entre_despachos = max(0.0, float(espaco_entre_despachos))
        self._ultimo_despacho: float | None = None
        self._relogio = time.monotonic
        self._pausa = asyncio.sleep
        self._sorteio = random.random
        self._rodando = False
        # ids de conta com navegador aberto agora — por geracao ou por leitura de saldo
        self._ocupadas: set[int] = set()
        # downloads que falharam por job — nao gastam tentativa, mas tem teto
        self._downloads_falhos: dict[int, int] = {}
        self._tarefas: set[asyncio.Task] = set()
        # O despertador do laco. `rodar` dorme sobre ele em vez de sobre um relogio:
        # conta liberada ou saldo lido acordam o motor na hora, em vez de dali a
        # `intervalo` segundos — e junto com `antecipar_reavaliacao` e o que faz um job
        # adiado por "janela cheia" sair da fila no instante em que a janela vaga, e
        # nao 30 s depois (medido: 11,6 eventos `sem_conta_apta` por job pronto).
        self._acordar = asyncio.Event()
        # sessoes abertas e OCIOSAS, por conta: (sessao, desde quando esta parada).
        # Uma conta esta aqui OU em `_ocupadas`, nunca nos dois.
        self._guardadas: dict[int, tuple[Sessao, datetime]] = {}
        # TODA sessao com navegador aberto agora — a ociosa e a que esta no meio de um
        # job. `_guardadas` nao serve para isso: ela e a fila de espera, e some no
        # instante em que a conta volta a trabalhar. Quem quer "a janela desta conta"
        # precisa achar a sessao nos DOIS estados, e o caso interessante e justamente o
        # segundo: ver a tela enquanto a geracao acontece.
        self._vivas: dict[int, Sessao] = {}

    # ------------------------------------------------------------- o que o painel ve

    @property
    def gerando(self) -> int:
        """Quantas geracoes estao em curso."""
        return len(self._tarefas)

    @property
    def contas_ocupadas(self) -> int:
        """Quantas contas tem navegador aberto agora.

        Pode passar de `gerando` por um: a leitura de saldo tambem abre navegador e
        tambem toma a conta, sem ser uma geracao.
        """
        return len(self._ocupadas)

    def ocupada(self, conta_id: int) -> bool:
        return conta_id in self._ocupadas

    @property
    def sessoes_ociosas(self) -> int:
        """Quantos navegadores estao abertos sem job — guardados para o proximo."""
        return len(self._guardadas)

    # ----------------------------------------------------- sessoes entre jobs

    async def _obter_sessao(self, conta: Conta) -> Sessao:
        """A sessao guardada da conta, se ainda serve; senao uma nova.

        Conferir `viva` antes de reusar e o que separa "reuso" de "reuso de um Chrome
        que morreu": o navegador pode ter caido sozinho, e a pagina fechada so seria
        descoberta no primeiro clique, como um erro de sequencia que nao e.
        """
        # A conta em FUGA sai pelo proxy de fuga, e nao pelo seu. E uma copia: o
        # `proxy` da conta no banco continua sendo o dela, e e ele que volta quando o
        # processo reinicia. O transporte nao sabe que fuga existe — recebe uma conta
        # com proxy, como qualquer outra.
        if conta.id in self._em_fuga and self.proxy_fuga:
            conta = replace(conta, proxy=self.proxy_fuga)
        guardada = self._guardadas.pop(conta.id, None)
        if guardada is not None:
            sessao, _ = guardada
            # ⚠️ O `pop` JA ACONTECEU, e por isso o `except` nao e opcional. Daqui em
            # diante a sessao nao esta mais na fila de ociosas; se `renovar` levantar,
            # ninguem mais tem a referencia dela — nem `_fechar_ociosas`, nem
            # `encerrar_todas` — e o Chrome fica de pe segurando o perfil da conta,
            # que e a janela orfa vista em 2026-09-10 (ver `abrir_sessao`).
            #
            # Falhar ao renovar e a mesma descoberta que `viva` False, um passo
            # depois: o navegador guardado nao serve. Fecha e abre outro.
            #
            # E a sessao so serve se saiu pelo IP CERTO. A recusa por antiabuso
            # acontece com o Chrome de casa aberto e saudavel, e `_encerrar_ciclo` o
            # guarda; reusa-lo na fuga sairia pelo mesmo IP recem-recusado, sem erro.
            try:
                if sessao.conta.proxy == conta.proxy and await self.transporte.viva(sessao):
                    await self.transporte.renovar(sessao)
                    return sessao
            except Exception as e:
                self.repo.registrar_evento(
                    None, conta.id, "reuso_falhou",
                    json.dumps({"erro": f"{type(e).__name__}: {e}"}))
            await self._fechar_sessao(sessao, conta.id)
        # `_vivas` e escrita AQUI e apagada em `_fechar_sessao`, os dois pontos unicos
        # por onde navegador abre e fecha. Registrar em cada chamador seria esquecer um.
        nova = await self.transporte.abrir_sessao(conta)
        self._vivas[conta.id] = nova
        return nova

    def _guardar_sessao(self, conta: Conta, sessao: Sessao) -> None:
        self._guardadas[conta.id] = (sessao, datetime.now(timezone.utc))

    async def _fechar_sessao(self, sessao: Sessao, conta_id: int,
                             job_id: int | None = None) -> None:
        """Fecha sem deixar excecao escapar: um browser ja morto levanta ao fechar, e
        isso escapando de um `finally` matava a task de `rodar()` em silencio."""
        self._vivas.pop(conta_id, None)
        try:
            await self.transporte.fechar(sessao)
        except Exception as e:
            self.repo.registrar_evento(job_id, conta_id, "fechar_falhou",
                                       json.dumps({"erro": str(e)}))

    async def encerrar_sessao(self, conta_id: int) -> bool:
        """Fecha o navegador ocioso daquela conta, se houver. Devolve se fechou.

        E o que as rotas de "Abrir" e "Revalidar" chamam antes de subir um Chrome
        proprio no mesmo perfil: dois Chromes no mesmo `--user-data-dir` nao
        coexistem, e o segundo entregaria a URL ao Chrome do Playwright.
        """
        guardada = self._guardadas.pop(conta_id, None)
        if guardada is None:
            return False
        await self._fechar_sessao(guardada[0], conta_id)
        return True

    async def mostrar_janela(self, conta_id: int) -> bool:
        """Traz para a tela a janela daquela conta. Devolve se havia janela.

        Existe por causa de `FLOWPOOL_JANELA=oculta`: com o Chrome nascendo fora da
        tela, ver o que esta acontecendo numa conta deixou de ser automatico.

        ⚠️ NAO E CHAMADO POR NENHUM TRATADOR DE FALHA, e isso e requisito e nao
        esquecimento. RELATADO pelo usuario 2026-09-10: "algumas falhas somem quando
        reabre a janela" — mexer na janela muda o estado da pagina, entao uma janela que
        pulasse sozinha na falha poderia apagar a prova. O diagnostico automatico e o
        print (`acoes.print_de_falha`), que nao toca na janela; mostrar e ato de pessoa.

        `False` significa "esta conta nao tem navegador aberto agora", que e diferente
        de erro: a sessao pode ter sido fechada por ociosidade, ou a conta pode nunca
        ter trabalhado. Quem chama transforma isso na mensagem certa; falha do CDP
        propaga, porque janela que nao apareceu com o painel dizendo "ok" e pior que
        erro — o usuario iria procurar na tela o que continua em -32000.
        """
        sessao = self._vivas.get(conta_id)
        if sessao is None:
            return False
        await self.transporte.mostrar_janela(sessao)
        self.repo.registrar_evento(None, conta_id, "janela_mostrada", "{}")
        return True

    async def encerrar_todas(self) -> None:
        """Fecha TODA janela que o motor abriu. Nao sobra Chrome depois desta chamada.

        ⚠️ `_guardadas` NAO BASTA, e a diferenca aparece exatamente no desligamento com
        trabalho em curso: a sessao de um job nao esta na fila de ociosas — ela sai de
        `_guardadas` no instante em que a conta volta a trabalhar. Parar o servico ali
        deixava aquele Chrome de pe, sem dono, segurando o `--user-data-dir`, e o boot
        seguinte esbarrava em perfil ocupado numa conta sem defeito nenhum.

        `_vivas` e o registro de toda janela aberta, e por isso e ele quem fecha a
        conta. Idempotente: `_fechar_sessao` tira de `_vivas` e engole erro de browser
        ja morto, entao chamar duas vezes nao custa nada.
        """
        for conta_id in list(self._guardadas):
            await self.encerrar_sessao(conta_id)
        for conta_id, sessao in list(self._vivas.items()):
            await self._fechar_sessao(sessao, conta_id)

    async def _fechar_ociosas(self) -> None:
        limite = datetime.now(timezone.utc) - OCIOSIDADE_SESSAO
        for conta_id, (_, desde) in list(self._guardadas.items()):
            if desde <= limite:
                await self.encerrar_sessao(conta_id)

    # ------------------------------------------------------------------- recuperacao

    def recuperar(self) -> int:
        """Resolve os jobs que um crash deixou presos em `rodando`. Roda no boot.

        `reivindicar` so olha para `pendente`, entao um job travado em `rodando` nunca
        seria retomado nem falharia — e o credito que ele reservou nunca voltaria ao
        saldo. Devolve quantos jobs foram tocados.

        Duas situacoes, e a diferenca e o `id_remoto`:
        - vazio: o Flow nunca aceitou o pedido, entao a reserva volta e o job recomeca
          do zero, sem gastar tentativa (nao foi ele que falhou, foi a maquina).
        - preenchido: existe geracao la fora, ja paga. Estornar inflaria o saldo, e
          perder o `id_remoto` faria pagar de novo — os dois sao preservados.
        """
        presos = self.repo.jobs(EstadoJob.RODANDO)
        for job in presos:
            conta_id = job.conta_id
            conta = self.repo.conta(conta_id) if conta_id is not None else None
            if job.id_remoto is None:
                if conta is not None and job.custo_estimado is not None:
                    liberar(conta, job.custo_estimado)
                    self.repo.salvar_conta(conta)
                job.custo_estimado = None
                job.conta_id = None
                job.estado = EstadoJob.PENDENTE
                self.repo.salvar_job(job)
                self.repo.registrar_evento(job.id, conta_id, "recuperado_sem_gerar")
            else:
                job.estado = EstadoJob.PENDENTE
                self.repo.salvar_job(job)
                self.repo.registrar_evento(
                    job.id, conta_id, "recuperado_com_geracao",
                    json.dumps({"id_remoto": job.id_remoto}))
        return len(presos)

    # ------------------------------------------------------------------------ o passo

    async def passo(self) -> bool:
        """Reivindica um job, reserva a conta dele e SOLTA a geracao em segundo plano.

        Devolve False quando nao havia o que despachar — sem vaga, sem conta livre ou
        sem job. Quem chama usa isso para saber se vale tentar de novo na hora ou
        dormir um pouco.

        NAO espera a geracao terminar: era isso que fazia a fila andar uma por vez.
        Para esperar (testes, parada limpa) existe `drenar`.

        A ORDEM aqui e o que torna o paralelismo seguro. Reservar a conta acontece
        ANTES de `create_task`, sem nenhum `await` no meio: se a reserva ficasse dentro
        da tarefa, dois passos seguidos reivindicariam dois jobs e escolheriam a MESMA
        conta, porque a primeira tarefa ainda nao teria comecado a rodar. Dois Chromes
        no mesmo perfil, e o segundo morre.

        E reivindicar continua vindo depois do teto, pelo mesmo motivo de sempre: job
        puxado e devolvido a fila e job que outro worker pega no meio do caminho.
        """
        if len(self._tarefas) >= self.max_simultaneos:
            return False
        if not self._ha_conta_livre():
            return False
        agora = datetime.now(timezone.utc)
        job = self.repo.reivindicar(agora)
        if job is None:
            return False

        conta = await self._destinar(job, agora)
        if conta is None:
            # `_destinar` ja devolveu o job a fila com o evento certo. True porque
            # algo ANDOU: o laco pode tentar o proximo job em vez de dormir.
            return True

        tarefa = asyncio.create_task(self._conduzir(job, conta))
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)
        return True

    def _ha_conta_livre(self) -> bool:
        """Se existe conta viva que nao esta com navegador aberto.

        Serve para nao reivindicar job que nao teria onde rodar. Sem esta pergunta, com
        as janelas cheias, cada volta do laco puxaria mais um job so para empurra-lo
        trinta segundos a frente — a fila inteira remexida por nada.

        Pool sem NENHUMA conta viva devolve True de proposito: ali o job PRECISA ser
        reivindicado, porque e a recusa dele que vira o evento `sem_conta_apta` que o
        painel usa para explicar por que nada anda.
        """
        vivas = [c for c in self.repo.contas()
                 if c.ativa and c.saude is SaudeSessao.VIVA]
        if not vivas:
            return True
        return any(c.id not in self._ocupadas for c in vivas)

    async def _destinar(self, job: Job, agora: datetime) -> Conta | None:
        """Reserva a conta em que o job vai rodar, ou o devolve a fila e retorna None.

        A unica parte async e a semeadura de saldo, que abre navegador. O caminho
        comum nao tem `await` nenhum, e e isso que deixa a reserva atomica.
        """
        if job.id_remoto is not None and job.conta_id in self._ocupadas:
            # Existe geracao PAGA em curso, e so a conta que a criou consegue retoma-la.
            # Trocar de conta aqui abandonaria o credito ja gasto e pagaria de novo: um
            # Quality de 11 minutos custa 200 creditos e nao entrega nada. Esperar a
            # janela vagar custa minutos; a troca custa dinheiro.
            job.estado = EstadoJob.PENDENTE
            job.reavaliar_em = agora + ESPERA_JANELA
            self.repo.salvar_job(job)
            self.repo.registrar_evento(job.id, job.conta_id, "retomada_espera_a_conta",
                                       json.dumps({"id_remoto": job.id_remoto}))
            return None

        try:
            conta = self._reservar_conta(job, agora)
            if conta is None and await self._semear_uma_conta(agora):
                # A leitura pode ter revelado credito que a escolha nao enxergava.
                # `agora` continua o mesmo de proposito: o relogio do passo nao anda no
                # meio dele, e a semeadura demora segundos, nao horas.
                conta = self._reservar_conta(job, agora)
        except Exception as e:
            # `reivindicar` ja marcou o job `rodando`. Deixar a excecao subir daqui o
            # deixaria travado nesse estado ate o proximo boot — e `ModeloSemCusto`,
            # que e erro de configuracao, e exatamente o caso que chega aqui. Gasta
            # tentativa: reprocessar para sempre um modelo sem custo cadastrado nao
            # conserta o cadastro.
            erro = f"{type(e).__name__}: {e}"
            self.repo.registrar_evento(job.id, None, "escolha_falhou",
                                       json.dumps({"erro": erro}))
            self._devolver(job, erro, gasta_tentativa=True)
            return None

        if conta is not None:
            return conta

        # "nenhuma conta serve" e "as que servem estao ocupadas" sao coisas diferentes,
        # e a espera de cada uma tambem: a primeira aguarda o reset diario, a segunda
        # aguarda a janela do vizinho fechar.
        contas = self.repo.contas()
        cheio = escolher(contas, job.modelo, agora, job.quantidade,
                         job.duracao) is not None
        job.estado = EstadoJob.PENDENTE
        # ⚠️ "NINGUEM SERVE" ESPERA A PRIMEIRA CONTA VOLTAR, NAO UMA HORA FIXA.
        # MEDIDO 2026-09-16: o job 1654 foi adiado para 22:11 com as contas 7, 8, 9 e
        # 2 saindo do descanso as 21:45 — 25 min de fila parada por leva.
        # `_quando_reavaliar` ja calculava o instante certo para o caminho da recusa;
        # o despacho e que ficou com a hora fixa da primeira versao.
        job.reavaliar_em = (agora + ESPERA_JANELA if cheio
                            else self._quando_reavaliar(job, agora))
        self.repo.salvar_job(job)
        self.repo.registrar_evento(
            job.id, None, "sem_conta_livre" if cheio else "sem_conta_apta")
        return None

    def _reservar_conta(self, job: Job, agora: datetime) -> Conta | None:
        """Escolhe a conta e JA a marca ocupada. Sem `await` no meio, de proposito.

        Escolher e marcar precisam ser um passo so. Com um ponto de suspensao entre os
        dois, dois trabalhadores leem o mesmo conjunto de contas livres, escolhem a
        mesma e o segundo Chrome morre no `--user-data-dir` do primeiro.
        """
        escolhida = self._conta_para(job, agora)
        if escolhida is not None:
            self._ocupadas.add(escolhida.id)
        return escolhida

    def _quando_reavaliar(self, job: Job, agora: datetime) -> datetime | None:
        """Quando este job merece uma nova chance. `None` = agora mesmo.

        ⚠️ ADIAR O JOB E O ULTIMO RECURSO, NAO O PRIMEIRO. A versao anterior punha
        `job.reavaliar_em = conta.seca_ate` sempre que UMA conta recusava — uma hora
        parado mesmo com outras contas livres. RELATADO pelo Davi 2026-09-09:
        "se uma conta nao tem saldo, o certo e pular para outra conta, porque outra
        pode ter saldo, pode ter resetado". Os saldos do pool naquele dia eram
        50/3/50/50/50/2/2/3/2 — nada de ciclo comum entre elas.

        Mas soltar sem prazo quando NENHUMA serve traz o laco quente de volta: o job
        voltaria na hora, escolheria nada e giraria. Entao o prazo, quando existe, e
        o instante em que a PRIMEIRA conta volta a servir — nao uma hora fixa.
        """
        contas = self.repo.contas()
        if escolher(contas, job.modelo, agora, job.quantidade, job.duracao,
                    ocupadas=self._ocupadas) is not None:
            return None      # alguem pode pegar agora

        # ninguem serve: espera a primeira porta que abre. `reset_em` entra porque a
        # conta pode estar so sem saldo, sem nenhum descanso marcado.
        marcos = [t for c in contas
                  for t in (c.limite_ate, c.seca_ate, c.reset_em)
                  if t is not None and t > agora]
        return min(marcos) if marcos else agora + ESPERA_RESET

    def _conta_para(self, job: Job, agora: datetime) -> Conta | None:
        """Conta em que este job deve rodar.

        Job que volta a fila com `id_remoto` tem geracao paga em curso no Flow, e ela
        so pode ser retomada de dentro da conta que a criou — por isso aquela conta
        tem preferencia. Se ela nao servir mais, a escolha normal vale e o job gera do
        zero. (O caso "ela serve mas esta ocupada" nem chega aqui: `_destinar` faz o
        job esperar, porque trocar de conta ali jogaria credito fora.)
        """
        contas = self.repo.contas()
        if job.id_remoto is not None and job.conta_id is not None:
            anterior = next((c for c in contas if c.id == job.conta_id), None)
            if anterior is not None and anterior.id not in self._ocupadas \
                    and apta(anterior, job.modelo, agora, job.quantidade, job.duracao):
                return anterior
        return escolher(contas, job.modelo, agora, job.quantidade, job.duracao,
                        ocupadas=self._ocupadas)

    async def _conduzir(self, job: Job, conta: Conta) -> None:
        """Leva o job ate o fim e SEMPRE devolve a conta ao pool.

        O `finally` e a peca que impede o pool de encolher: conta que nao e liberada
        depois de uma falha some da escolha para sempre, e o motor vai parando sem
        ninguem entender por que.

        O `except` largo existe porque isto roda como tarefa de fundo. `_executar` ja
        trata o que sabe tratar; o que escapar dele morreria dentro da task, sem
        ninguem observando — mesmo motivo do `try` de `rodar()`.
        """
        try:
            await self._executar(job, conta)
        except Exception as e:
            self.repo.registrar_evento(
                job.id, conta.id, "conduzir_falhou",
                json.dumps({"erro": f"{type(e).__name__}: {e}"}))
        finally:
            self._liberar(conta.id)

    def _liberar(self, conta_id: int) -> None:
        """Devolve a conta ao pool E acorda quem esperava por uma.

        Os jobs adiados por "janela cheia" (`ESPERA_JANELA`, 30 s) esperavam
        exatamente isto: uma conta vagar. Zerar o `reavaliar_em` deles e acordar o
        laco faz o proximo despacho acontecer agora. Os adiados por falta de credito
        (`ESPERA_RESET`, 1 h) ficam como estao — uma conta livre sem saldo nao muda
        nada para eles; quem os antecipa e a leitura de saldo.
        """
        self._ocupadas.discard(conta_id)
        self.repo.antecipar_reavaliacao(datetime.now(timezone.utc) + ESPERA_JANELA)
        self._acordar.set()

    async def _dormir(self, intervalo: float) -> None:
        """Dorme ate `intervalo` segundos — ou ate alguem acordar o motor."""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._acordar.wait(), timeout=intervalo)
        self._acordar.clear()

    def marcar_despacho(self) -> None:
        """Anota que uma geracao acabou de sair. Ver `ESPACO_ENTRE_DESPACHOS`."""
        self._ultimo_despacho = self._relogio()

    async def _espacar_despacho(self) -> None:
        """Espera o que FALTA para o proximo despacho. Nao dorme o espaco inteiro.

        O tempo que a geracao anterior ja levou conta: numa fila de videos de dois
        minutos isto custa zero, porque o proximo despacho ja chega atrasado sozinho.
        Quem paga sao as rajadas de imagem — que e exatamente onde o Google bloqueou.

        DORME AQUI, E NAO DENTRO DA TAREFA: as geracoes ja despachadas continuam
        correndo enquanto o laco espera. Espacar e sobre o INICIO, nao sobre a duracao.

        E NAO SEGURA JOB NENHUM enquanto espera — `rodar` chama isto DEPOIS de o
        despacho ter saido, nunca entre reivindicar e despachar. Um job parado em
        `rodando` durante a espera apareceria como travado no painel por nada.
        """
        if not self.espaco_entre_despachos or self._ultimo_despacho is None:
            return
        alvo = self.espaco_entre_despachos * (
            1 + JITTER_DO_DESPACHO * self._sorteio())
        falta = alvo - (self._relogio() - self._ultimo_despacho)
        if falta > 0:
            await self._pausa(falta)

    async def drenar(self) -> None:
        """Espera as geracoes ja despachadas terminarem. Nao reivindica nada.

        Em laco porque uma tarefa pode terminar enquanto outra ainda roda e o conjunto
        muda embaixo do `gather`.
        """
        while self._tarefas:
            await asyncio.gather(*tuple(self._tarefas), return_exceptions=True)
        # os diagnosticos de fundo tambem: um teste que falha um job quer ver o
        # evento, e a parada limpa nao pode deixar tarefa orfa
        while self._fundo:
            await asyncio.gather(*tuple(self._fundo), return_exceptions=True)

    # -------------------------------------------------------------- saldo e semeadura

    async def _semear_uma_conta(self, agora: datetime) -> bool:
        """Le o saldo de UMA conta que ainda nao tem numero em que confiar.

        Existe por causa de um impasse fechado: `Conta.saldo` nasce 0, `apta` exige
        `custo <= saldo`, e o saldo so era escrito na reconciliacao — que roda depois
        de um job. O saldo so atualizava rodando job, e o job so rodava se houvesse
        saldo. Nada quebrava e nada aparecia: o job ficava pendente para sempre.

        Ler o saldo no cadastro resolve o caso comum, mas nao todos: banco recriado,
        conta inserida por fora, reset diario que ninguem observou — cada um reintroduz
        o impasse numa variante. Por isso o motor tambem se cura sozinho.

        UMA conta por passo, nao todas: abrir navegador e caro e o motor roda em laco.
        Isto nao vira laco quente porque a semeadura so acontece com um job JA
        reivindicado, e o job que nao acha conta sai da fila por `ESPERA_RESET`.

        Devolve True quando alguma leitura entrou no banco — e so entao vale reescolher.
        """
        alvo = self._proxima_a_semear(self.repo.contas(), agora, self._ocupadas)
        if alvo is None:
            return False
        return await self.semear_saldo(alvo)

    @staticmethod
    def _proxima_a_semear(contas: list[Conta], agora: datetime,
                          ocupadas: frozenset[int] | set[int] = frozenset()
                          ) -> Conta | None:
        """Conta cujo saldo mais precisa ser lido, ou None se nenhuma precisa.

        Fica de fora quem ja tem saldo confiavel: sem esse corte, um pool
        legitimamente seco viraria um laco de abrir e fechar navegador a cada volta.
        Fica de fora tambem a conta seca com prazo em aberto — o Flow ja recusou gerar
        nela apesar do saldo, e reler o numero nao muda isso ate o reset. E fica de
        fora a conta OCUPADA: ler saldo abre navegador, e naquele perfil ja ha um.
        """
        candidatas = [
            c for c in contas
            if c.ativa and c.saude == SaudeSessao.VIVA and not c.saldo_confiavel
            and c.id not in ocupadas
            and (c.seca_ate is None or c.seca_ate <= agora)
        ]
        if not candidatas:
            return None
        # nunca lida primeiro, depois a de leitura mais antiga; o id so desempata
        return min(candidatas, key=lambda c: (
            c.saldo_lido_em is not None,
            c.saldo_lido_em.timestamp() if c.saldo_lido_em else 0.0,
            c.id,
        ))

    async def semear_saldo(self, conta: Conta) -> bool:
        """Abre sessao SO para ler o saldo e reconciliar. Devolve True se leu.

        Nao gasta credito: ler saldo e abrir o menu do avatar. Mas ABRE NAVEGADOR, e e
        por isso que toma a conta como qualquer geracao: numa conta que ja esta gerando
        seria o segundo Chrome no mesmo `--user-data-dir`, e o segundo morre. As rotas
        do painel chamam esta funcao direto — a trava precisa estar aqui dentro, e nao
        so no caminho do motor.

        Registra evento sempre, nos tres desfechos. Uma abertura de navegador sem job
        associado precisa ter explicacao no banco, senao quem for auditar depois vai
        procurar a geracao que a pagou e nao vai achar.

        Falha nao propaga: quem chama esta no meio de um passo do motor ou de uma rota
        de cadastro, e nos dois lugares travar seria pior do que ficar sem o numero.
        """
        if conta.id in self._ocupadas:
            self.repo.registrar_evento(
                None, conta.id, "saldo_nao_semeado",
                json.dumps({"erro": "conta ocupada por uma geracao em curso"}))
            return False
        self._ocupadas.add(conta.id)
        sessao = None
        leu = False
        try:
            sessao = await self._obter_sessao(conta)
            lido = await self.transporte.saldo(sessao)
            leu = True
            antes = conta.saldo
            reconciliar(conta, lido, datetime.now(timezone.utc))
            self.repo.salvar_conta(conta)
            self.repo.registrar_evento(None, conta.id, "saldo_semeado",
                                       json.dumps({"antes": antes, "lido": lido}))
            # o que se sabe do credito mudou: todo job adiado por "sem conta apta"
            # merece nova avaliacao agora, e nao daqui a uma hora
            self.repo.antecipar_reavaliacao(None)
            self._acordar.set()
            return True
        except SessaoMorta as e:
            # campo a campo, como no resto do motor: `salvar_conta` grava a linha
            # inteira a partir de uma copia velha e ressuscitaria a sessao
            conta.saude = SaudeSessao.EXPIRADA
            self.repo.marcar_sessao(conta.id, SaudeSessao.EXPIRADA)
            self.repo.registrar_evento(None, conta.id, "saldo_nao_semeado",
                                       json.dumps({"erro": str(e), "sessao": "morta"}))
            return False
        except Exception as e:
            self.repo.registrar_evento(
                None, conta.id, "saldo_nao_semeado",
                json.dumps({"erro": f"{type(e).__name__}: {e}"}))
            return False
        finally:
            if sessao is not None:
                if leu:
                    self._guardar_sessao(conta, sessao)
                else:
                    await self._fechar_sessao(sessao, conta.id)
            self._liberar(conta.id)

    # --------------------------------------------------------------------- a execucao

    async def _executar(self, job: Job, conta: Conta) -> None:
        """O ciclo de uma geracao, do lado de dentro de uma conta ja reservada.

        Nada aqui e compartilhado entre trabalhadores: `job` e `conta` sao objetos
        proprios deste passo, e como a conta e exclusiva, `salvar_conta` nunca disputa
        a mesma linha com ninguem. O que E compartilhado — `self.repo` e o transporte —
        e serializado: a conexao do sqlite tem trava, e o transporte guarda o estado
        por (conta, card).
        """
        # retomada: a geracao ja existe e e da mesma conta. Gerar de novo pagaria duas
        # vezes e deixaria a primeira orfa, terminando no Flow sem ninguem para baixar.
        retomando = job.id_remoto is not None and job.conta_id == conta.id
        job.estado = EstadoJob.RODANDO
        job.conta_id = conta.id
        self.repo.salvar_job(job)
        self.repo.registrar_evento(
            job.id, conta.id, "despachado",
            json.dumps({"modelo": job.modelo.value, "retomada": retomando}))

        sessao = None
        # o que ESTA tentativa colocou em reserva. Vive numa local, e nao em
        # `job.custo_estimado`, porque `_estornar` zera aquele campo no caminho da
        # falha — inclusive quando NAO devolve nada — e quem precisa do numero
        # (`_soltar_geracao_fantasma`) so roda depois, no `finally`. Zero numa
        # retomada, que nao reserva nada: la nao ha o que comparar.
        reservado = 0
        # o `CardTrocado` do ciclo, se houver. A DECISAO sobre ele nao cabe aqui: ela
        # precisa saber se a geracao foi COBRADA, e esse numero so existe depois da
        # reconciliacao, no `finally`. Ver `_fechar_card_trocado`.
        card_trocado: Exception | None = None
        try:
            sessao = await self._obter_sessao(conta)
            await self._resolver_projeto(sessao, job, conta)
            if not retomando:
                # a conta mudou: a geracao anterior nao serve mais, comeca do zero
                job.id_remoto = None
                # guardar o reservado no job e o que permite estornar depois —
                # inclusive depois de um restart, por isso vai para o banco na hora
                # o total do lote, nao o de uma geracao: e este numero que o estorno
                # devolve depois
                job.custo_estimado = reservar(conta, job.modelo, job.quantidade,
                                              job.duracao)
                reservado = job.custo_estimado
                self.repo.salvar_conta(conta)
                self.repo.salvar_job(job)

                job.id_remoto = await self.transporte.gerar(sessao, self._pedido(job))
                self.repo.salvar_job(job)

            estado = await self._esperar(sessao, job, job.id_remoto)
            if estado is not Estado.PRONTO:
                raise GeracaoFalhou(f"Flow devolveu {estado.value}")

            destino = self.raiz / job.destino / f"{job.nome}{_extensao(job.modo)}"
            try:
                arquivo = await self.transporte.baixar(sessao, job.id_remoto, destino)
            except CardTrocado:
                # NAO e falha de download, e por isso nao entra no laco dele. Sobe
                # para o tratador do ciclo, que tem a politica — uma so, agora que a
                # divergencia de tipo pode vir tambem do polling.
                raise
            except Exception as e:
                self._apos_download_falho(job, conta, e)
                return

            self._downloads_falhos.pop(job.id, None)
            job.arquivo = str(arquivo)
            job.estado = EstadoJob.PRONTO
            job.erro = None
            self.repo.salvar_job(job)
            self.repo.registrar_evento(job.id, conta.id, "pronto")

        except CardTrocado as e:
            card_trocado = e
            self._anotar_card_trocado(job, conta, e)
            # ⚠️ ANTES SO EXISTIA EM VOLTA DO `baixar`, e por isso o mesmo erro vindo
            # do polling caia no `except Exception` la embaixo: virava falha comum,
            # gastava tentativa do job e guardava `id_remoto` — de modo que a
            # tentativa seguinte retomava o ponteiro morto e chegava na mesma recusa.
            # Jobs 1114-1116 e 1120-1128 (2026-09-13).

        except SessaoMorta as e:
            self._ao_perder_sessao(job, conta, sessao, e)

        except LimiteDeUso as e:
            self._ao_bater_limite(job, conta, sessao, e)

        except SaldoInsuficiente as e:
            self._ao_secar(job, conta, sessao, e)

        except Exception as e:  # falha de rede, timeout, geracao errada
            self._ao_falhar(job, conta, sessao, e)

        finally:
            if sessao is not None:
                await self._encerrar_ciclo(job, conta, sessao, reservado,
                                           card_trocado)

    def _anotar_card_trocado(self, job: Job, conta: Conta, e: Exception) -> None:
        """Poe o card trocado na linha do tempo. NAO decide o destino do job.

        Vale para o CICLO INTEIRO, e nao so para o download. A divergencia de tipo
        passou a ser vista tambem no polling (`consultar` le `flow-image-tile` /
        `flow-video-tile`), e enquanto o tratador so envolvia o `baixar` o mesmo erro
        vindo dali caia no `except Exception` generico: gastava tentativa do job e
        guardava `id_remoto`, de modo que a tentativa seguinte retomava o ponteiro
        morto — jobs 1114-1116 e 1120-1128, 2026-09-13.

        O que fazer com o job depende de a geracao ter sido COBRADA, e esse numero so
        existe depois da reconciliacao. Por isso a decisao mora em
        `_fechar_card_trocado`, chamada do `finally`.
        """
        self.repo.registrar_evento(
            job.id, conta.id, "card_trocado",
            json.dumps({"id_remoto": job.id_remoto, "erro": str(e)}))

    def _fechar_card_trocado(self, job: Job, conta: Conta, sessao, e: Exception,
                             delta: int | None, reservado: int) -> None:
        """Card trocado: gera de novo se nada foi cobrado, senao chama uma pessoa.

        ⚠️ ISTO NAO E FALHA DE DOWNLOAD, E NAO ENTRA NO LACO DELE. Aquele laco existe
        para a falha que passa sozinha (o Chrome travando no blob); um card do tipo
        errado nao vira o certo com o tempo — a identificacao e que furou. MEDIDO nos
        jobs 583-588 (2026-09-09): 21 aberturas de navegador cada, todas parando na
        mesma recusa. Nenhum dos dois caminhos daqui gasta tentativa do job.

        SAO PRECISAS AS DUAS EVIDENCIAS PARA GERAR DE NOVO, e cada uma sozinha ja
        enganou este codigo:

        * so o CARD nao basta — a geracao pode existir e estar apenas mal
          identificada, e foi essa duvida que fez a politica original desistir sempre;
        * so o SALDO nao basta, e essa foi minha regressao de 2026-09-13. Eu soltava o
          ponteiro quando o reservado voltava inteiro, e o log de passo mostrou por que
          esta errado: o card dizia `movie / na fila / <prompt>`. O Flow ENFILEIRA a
          geracao e cobra quando ela COMECA, nao quando aceita. No job 1133 o ponteiro
          foi solto, o job gerou de novo na mesma conta e a conta 1 caiu de 50 para 38
          logo depois — pagou duas vezes.

        Saldo ilegivel (`delta` None) conta como cobrado: "nao sei" desiste. O erro
        para este lado custa tempo de uma pessoa; para o outro, credito.
        """
        erro = f"{type(e).__name__}: {e}"
        falha = sessao.falha if sessao else None
        if delta is not None and reservado > 0 and delta >= reservado:
            self.repo.registrar_evento(
                job.id, conta.id, "geracao_fantasma",
                json.dumps({"id_remoto": job.id_remoto, "delta": delta,
                            "reservado": reservado}))
            # `_devolver` solta a conta junto quando `id_remoto` e None, e e o certo:
            # se nao existe geracao presa aquela conta, nada prende o job a ela — e no
            # caso medido era justamente a conta que estava recusando.
            job.id_remoto = None
            self._devolver(job, erro, gasta_tentativa=False, falha=falha)
            return
        # cobrado, ou sem saber: existe geracao paga em algum lugar do Flow. Desiste
        # sem mexer em `id_remoto` — ele e a pista para achar a orfa — e a mensagem
        # diz o que olhar.
        self._devolver(job, erro, gasta_tentativa=False, falha=falha, desistir=True)

    def _apos_download_falho(self, job: Job, conta: Conta, e: Exception) -> None:
        """O download nao trouxe os bytes. Conta, respeita o teto, devolve o job."""
        # BAIXAR DE NOVO NAO GASTA TENTATIVA — o resultado JA existe e JA foi
        # pago. Gastar tentativa aqui jogaria fora o job inteiro por uma falha
        # DEPOIS da cobranca; repetir so o download custa tempo e um navegador.
        #
        # A causa raiz da intermitencia de 2026-09-05 foi encontrada em
        # 2026-09-06: o processo do Chrome TRAVAVA no download nativo de blob
        # (ver `transporte/pw/captura.py`). O transporte deixou de usar o
        # download do Chrome, e este caminho passou a ser a excecao — fica pelo
        # mesmo motivo de sempre: uma falha depois da cobranca nao pode custar
        # o job.
        vezes = self._downloads_falhos.get(job.id, 0) + 1
        self._downloads_falhos[job.id] = vezes
        self.repo.registrar_evento(
            job.id, conta.id, "download_falhou",
            json.dumps({"erro": f"{type(e).__name__}: {e}", "vezes": vezes}))
        # TETO. Sem ele isto vira laco quente: um download que nunca funciona
        # abriria e fecharia navegador para sempre, sem gastar tentativa e sem
        # nunca desistir. Chegando aqui, a falha e de verdade e o job para —
        # com o video ainda la, e a mensagem dizendo isso.
        if vezes >= MAX_DOWNLOADS:
            # NAO zera o contador. Zerando, a tentativa seguinte do job
            # recomecaria a contagem e o teto viraria MAX_DOWNLOADS vezes o
            # numero de tentativas — 24 aberturas de navegador para um
            # download que nunca vai funcionar.
            # ⚠️ A MENSAGEM DIZ O QUE SE SABE, E NAO MAIS DO QUE ISSO.
            #
            # Ela afirmava, categorica, "o resultado EXISTE e ja foi pago". Nos
            # jobs 583-588 (2026-09-09) isso era FALSO: o Flow tinha recusado a
            # geracao sem cobrar, e o robo estava tentando baixar uma imagem de
            # referencia que a posicao `card-0` entregou no lugar do video. O
            # usuario leu a frase, foi procurar o video na conta e nao achou —
            # meia tarde perdida por causa de uma certeza que ninguem verificou.
            #
            # O que se sabe de verdade aqui: o card passou na conferencia de
            # TIPO (divergencia sai por `CardTrocado`, acima) e o download nao
            # trouxe os bytes. Isso torna provavel que o resultado esteja la —
            # provavel o bastante para NAO gerar de novo por reflexo, e nao o
            # bastante para mandar alguem procurar sem avisar que e preciso
            # conferir.
            raise GeracaoFalhou(
                f"o download falhou {vezes} vezes seguidas e o job para aqui. "
                f"O card {job.id_remoto} da conta {conta.email} passou na "
                f"conferencia de tipo, entao o resultado provavelmente esta la "
                f"e ja foi pago: CONFIRA na conta antes de gerar de novo, para "
                f"nao pagar duas vezes. "
                f"Ultimo erro: {type(e).__name__}: {e}"
            ) from e
        job.reavaliar_em = None
        self._devolver(job, f"{type(e).__name__}: {e}", gasta_tentativa=False)
        return

    def _ao_perder_sessao(self, job: Job, conta: Conta, sessao, e: Exception) -> None:
        """A sessao caiu no meio. Estorna, marca a conta expirada, devolve o job."""
        # A sessao nao morre so em `abrir_sessao`: no transporte real ela cai no
        # meio de `gerar`/`consultar`/`baixar`. Como este e o primeiro `except`,
        # sem estornar aqui o credito reservado sumiria do saldo local para
        # sempre. `_estornar` e no-op depois que o Flow aceitou, entao nao infla.
        if self._estornar(job, conta):
            self.repo.salvar_conta(conta)
        conta.saude = SaudeSessao.EXPIRADA
        # escrita de campo especifico: `salvar_conta` grava a linha inteira a
        # partir de uma copia velha, e outro worker ressuscitaria a sessao
        self.repo.marcar_sessao(conta.id, SaudeSessao.EXPIRADA)
        self._evento_de_falha(job, conta, "sessao_morta")
        self._devolver(job, str(e), gasta_tentativa=False,
                       falha=sessao.falha if sessao else None)

    def _ao_bater_limite(self, job: Job, conta: Conta, sessao, e: LimiteDeUso) -> None:
        """O Flow recusou por limite de uso, sem cobrar. A CONTA descansa, o job nao."""
        # ⚠️ LIMITE DE USO NAO E FALTA DE CREDITO, e a diferenca aparece na tela.
        # RELATADO pelo usuario 2026-09-09: geracao de IMAGEM custa zero, entao a
        # conta bate no limite COM credito sobrando. Reportar isso como saldo
        # faria o painel acusar problema de credito numa conta cheia, e mandaria
        # ele investigar a coisa errada.
        #
        # A ACAO e a mesma do saldo — prazo de descanso, job de volta a fila sem
        # gastar tentativa, outra conta pega. O que muda e o nome e o evento.
        # E `saldo_confiavel` continua TRUE: nada foi cobrado, o numero local
        # segue valendo, e derruba-lo forcaria uma reconciliacao inutil.
        #
        # ⚠️ `sem_cobranca=True`: o card exigiu a frase "Nao houve cobranca" para
        # chegar aqui, entao a regra "aceitou, logo cobrou" nao vale — ver
        # `_estornar`. O `salvar_conta` logo abaixo grava o saldo devolvido junto
        # com o `limite_ate`.
        self._estornar(job, conta, sem_cobranca=True)
        em_fuga = self._entrar_em_fuga(job, conta, e)
        if em_fuga:
            # A conta NAO descansa: continua apta, continua a de maior saldo, e o
            # proximo despacho a escolhe de novo — agora saindo pelo proxy de fuga
            # (`_obter_sessao`). O resto do tratamento e igual: estorno feito acima,
            # job solto abaixo.
            self.repo.salvar_conta(conta)
        else:
            # ⚠️ `limite_ate`, NAO `seca_ate`. `seca_ate` e sobre credito e libera de
            # proposito o que e de graca — com ele, `apta` seguia devolvendo True para
            # imagem e o motor reescolhia a conta que acabara de recusar.
            conta.limite_ate = datetime.now(timezone.utc) + ESPERA_RESET
            self.repo.salvar_conta(conta)

        # ⚠️ QUEM DESCANSA E A CONTA, NAO O JOB. A primeira versao disto copiou o
        # `job.reavaliar_em = conta.seca_ate` do saldo e ESTACIONOU O JOB POR UMA
        # HORA com outras contas livres do lado — visto em 2026-09-09 no job 439,
        # parado enquanto as contas 3 e 5 estavam com saldo 50 e sem descanso.
        # `reivindicar` so pega job com `reavaliar_em` nulo ou vencido, e a escolha
        # de conta ja pula quem tem `seca_ate` no futuro: marcar a conta basta.
        #
        # Em FUGA o job volta SEM prazo: a conta que acabou de recusar e a que vai
        # tentar de novo, pelo outro IP. `_quando_reavaliar` nao serve aqui — ela
        # ainda ve esta conta em `_ocupadas` (o ciclo nao terminou) e, sem `limite_ate`
        # para apontar, cairia na hora fixa.
        job.reavaliar_em = (None if em_fuga else
                            self._quando_reavaliar(job, datetime.now(timezone.utc)))

        # ⚠️ E SOLTA A CONTA E O CARD. `_devolver` preserva `conta_id` sempre que
        # `id_remoto` esta preenchido, porque isso normalmente significa "existe
        # geracao PAGA em curso, retome aquela em vez de pagar de novo". Aqui e o
        # oposto: o card e o de FALHA, e ele mesmo diz "nao houve cobranca". Manter
        # os dois prendia o job justamente na conta que acabou de recusar.
        job.id_remoto = None
        job.conta_id = None

        # O MOTIVO VAI PARA O EVENTO, e nao so para a mensagem do job. `job.erro` e
        # sobrescrito pela tentativa seguinte — quando o job 619 acabou em "o projeto
        # nao abriu", a recusa que o precedeu ja tinha sumido do banco. O evento e o
        # unico registro que fica, e sem esta chave o historico nao separa cota de
        # antiabuso: duas causas que se resolvem em lugares opostos.
        self._evento_de_falha(job, conta, "conta_no_limite", {"motivo": e.motivo})
        self._devolver(job, str(e), gasta_tentativa=False,
                       falha=sessao.falha if sessao else None)

    def _entrar_em_fuga(self, job: Job, conta: Conta, e: LimiteDeUso) -> bool:
        """Se esta recusa manda a conta sair por outro IP em vez de descansar.

        So o ANTIABUSO: cota nao passa trocando de IP, e a conta ja em fuga que e
        recusada de novo mostrou que o IP nao era o problema — descansa. Sem proxy de
        fuga configurado, nada muda.

        MEDIDO 2026-09-16 20:45 UTC: contas 7, 8, 9 e 2 recusadas em sequencia, uma
        a cada 5 s, todas pelo IP de casa; o Davi destravou uma na mao trocando de IP
        e gerou. O motor faz o mesmo, e este metodo e a medicao limpa daquilo: robo,
        mesma cadencia, so o IP muda.
        """
        if (not self.proxy_fuga or e.motivo != LimiteDeUso.MOTIVO_ANTIABUSO
                or conta.id in self._em_fuga):
            return False
        self._em_fuga.add(conta.id)
        self.repo.registrar_evento(job.id, conta.id, "conta_em_fuga",
                                   json.dumps({"proxy": mascarar(self.proxy_fuga)}))
        return True

    def _ao_secar(self, job: Job, conta: Conta, sessao, e: Exception) -> None:
        """Nao ha credito para este pedido. A conta seca com prazo, o job volta."""
        # Vem de duas fontes: o guarda local em `reservar` (a conta tem credito
        # real, so nao o bastante para este modelo) ou o proprio Flow. Estornar e
        # deixar a reconciliacao do `finally` adotar o numero verdadeiro — zerar o
        # saldo na mao apagaria credito que existe e tiraria a conta do pool.
        #
        # Marcar a conta seca COM PRAZO e o que impede o laco quente: se o Flow
        # mostra saldo mas recusa gerar (cota diaria, throttle), sem prazo o job
        # volta a fila na mesma hora, para a mesma conta, abrindo e fechando um
        # Chrome por iteracao, sem gastar tentativa e sem nunca desistir.
        self._estornar(job, conta)
        agora = datetime.now(timezone.utc)
        conta.seca_ate = agora + ESPERA_RESET
        conta.saldo_confiavel = False
        self.repo.salvar_conta(conta)
        # ⚠️ NAO e mais `= conta.seca_ate`. Ver `_quando_reavaliar`: adiar o job
        # por uma hora com outras contas livres foi defeito medido em 2026-09-09.
        # A conta continua descansando; o JOB so espera se ninguem puder pega-lo.
        job.reavaliar_em = self._quando_reavaliar(job, agora)
        self._evento_de_falha(job, conta, "conta_seca")
        self._devolver(job, str(e), gasta_tentativa=False,
                       falha=sessao.falha if sessao else None)

    def _ao_falhar(self, job: Job, conta: Conta, sessao, e: Exception) -> None:
        """Falha de rede, timeout, geracao errada. A unica que GASTA tentativa."""
        # `_estornar` decide sozinho se devolve: so devolve enquanto o Flow ainda
        # nao aceitou o pedido. Falha de download ou geracao recusada acontece
        # depois da cobranca, e ali estornar inflaria o saldo.
        self._estornar(job, conta)
        conta.falhas += 1
        self.repo.salvar_conta(conta)
        self._evento_de_falha(job, conta, "falhou", {"erro": str(e)})
        self._devolver(job, f"{type(e).__name__}: {e}", gasta_tentativa=True,
                       falha=sessao.falha if sessao else None)

    async def _encerrar_ciclo(self, job: Job, conta: Conta, sessao,
                              reservado: int = 0,
                              card_trocado: Exception | None = None) -> None:
        """Reconcilia o saldo, fecha o card trocado e guarda ou fecha a sessao.

        O card trocado se decide AQUI porque e aqui que o saldo e lido: a escolha
        entre "gera de novo" e "chama uma pessoa" depende de a geracao ter sido
        cobrada, e `delta` e a unica evidencia disso. Ver `_fechar_card_trocado`.
        """
        saudavel = False
        delta: int | None = None
        try:
            lido = await self.transporte.saldo(sessao)
            delta = reconciliar(conta, lido, datetime.now(timezone.utc))
            conta.falhas = 0 if job.estado is EstadoJob.PRONTO else conta.falhas
            self.repo.salvar_conta(conta)
            if abs(delta) >= LIMIAR_DIVERGENCIA:
                # a medicao que responde se imagem custa credito ou sai de
                # graca. Calcular o delta com sinal e joga-lo fora apagava o
                # unico dado real que o projeto tem sobre custo.
                self.repo.registrar_evento(
                    job.id, conta.id, "divergencia",
                    json.dumps({"delta": delta, "lido": lido}))
            saudavel = True
        except Exception as e:
            # o `except: pass` daqui engolia tambem falha de `salvar_conta` —
            # o silencio que transformava conta seca em laco infinito
            self.repo.registrar_evento(job.id, conta.id, "reconciliacao_falhou",
                                       json.dumps({"erro": str(e)}))
        if card_trocado is not None:
            self._fechar_card_trocado(job, conta, sessao, card_trocado,
                                      delta, reservado)
        # A leitura de saldo e a PROVA de que a sessao ainda responde: ela
        # abre e fecha o menu do avatar. Sessao que passou fica guardada para
        # o proximo job da conta; sessao que falhou ali e fechada — o proximo
        # job abre uma nova em vez de herdar um navegador suspeito.
        if saudavel:
            self._guardar_sessao(conta, sessao)
        else:
            await self._fechar_sessao(sessao, conta.id, job.id)

    async def _resolver_projeto(self, sessao, job: Job, conta: Conta) -> None:
        """Deixa a sessao dentro do projeto certo antes de gerar.

        Toda geracao do Flow acontece dentro de um projeto, e o projeto tem id na URL.
        A politica e um projeto por produto EM CADA CONTA — projeto pertence a uma
        conta so, entao nao existe "o projeto do Vestido Vermelho", existe um em cada
        conta que gerou algo dele.

        Ordem: banco local, depois lista remota (adocao), depois criacao. O passo do
        meio existe porque sem ele apagar o `flowpool.db` ou criar um projeto na mao
        pelo site faria o app duplicar tudo, e projeto duplicado no Flow e bagunca que
        ninguem limpa depois.

        Job sem `produto` (criado direto pelo repo) nao tem projeto a resolver: criar
        um chamado "" seria pior do que nao criar nada.
        """
        if not job.produto:
            return

        conhecido = self.repo.projeto_de(conta.id, job.produto)
        if conhecido is not None:
            await self.transporte.abrir_projeto(sessao, conhecido.id_remoto)
            return

        remotos = await self.transporte.listar_projetos(sessao)
        achado = next((p for p in remotos if p.nome == job.produto), None)
        if achado is not None:
            id_remoto, nome, tipo = achado.id_remoto, achado.nome, "projeto_adotado"
        else:
            id_remoto = await self.transporte.criar_projeto(sessao, job.produto)
            nome, tipo = job.produto, "projeto_criado"

        self.repo.registrar_projeto(conta.id, job.produto, id_remoto, nome)
        self.repo.registrar_evento(
            job.id, conta.id, tipo,
            json.dumps({"produto": job.produto, "id_remoto": id_remoto}))
        await self.transporte.abrir_projeto(sessao, id_remoto)

    def _evento_de_falha(self, job: Job, conta: Conta, tipo: str,
                         extra: dict | None = None) -> None:
        """Registra a falha ANTES de `_devolver`, com o que a auditoria precisa.

        Depois de `_devolver` o job ja mudou de estado e pode ter perdido o vinculo
        com a conta. O `id_remoto` no evento e o que permite achar geracao orfa no
        Flow, e a tentativa e o que mostra quantas vezes aquele job ja custou credito.
        """
        payload = {"id_remoto": job.id_remoto, "tentativa": job.tentativas}
        if extra:
            payload.update(extra)
        self.repo.registrar_evento(job.id, conta.id, tipo, json.dumps(payload))

    def _estornar(self, job: Job, conta: Conta, sem_cobranca: bool = False) -> bool:
        """Devolve ao saldo a reserva de um job que nao chegou a virar geracao.

        So estorna enquanto `id_remoto is None` — ou seja, enquanto o Flow ainda nao
        aceitou o pedido. Depois disso o credito provavelmente JA foi cobrado, e
        devolver inflaria o saldo local. Nao da para contar com a reconciliacao do
        `finally` para consertar: a falha que trouxe o codigo ate aqui costuma ser de
        rede, e a leitura de saldo falha junto. O saldo inflado sobreviveria, a conta
        seria escolhida de novo, o Flow recusaria, e o ciclo se repetiria.

        ⚠️ `sem_cobranca` E A UNICA COISA QUE DERRUBA ESSA REGRA, e ela so vem de UMA
        evidencia: o proprio card na tela dizendo "Nao houve cobranca por esta geracao"
        (`grade.recusa_sem_cobranca` exige essa frase para existir). Ali o "provavelmente
        cobrou" e falso com prova, e nao estornar deixa o saldo local ABAIXO do real.
        MEDIDO nos jobs 583-588 (2026-09-09): a reconciliacao do `finally` adotava o
        numero verdadeiro e a divergencia saia como `delta=+15` — mas ela le o saldo com
        a MESMA sessao que acabou de dar problema, e quando essa leitura falha a conta
        fica parecendo mais pobre do que e, e some das escolhas de modelo caro por uma
        geracao que nunca foi cobrada.

        A alternativa era limpar `id_remoto` antes de chamar isto, e seria pior: fazer a
        condicao virar verdadeira a mao esconde o motivo, e quem ler depois nao sabe se
        o campo foi solto para estornar ou para o job voltar a fila.

        Idempotente: zera `custo_estimado` para nao estornar duas vezes. Quem chama
        salva o job logo em seguida (`_devolver`), entao o zero chega ao banco.

        Devolve True quando credito voltou de fato ao saldo em memoria — quem chamou
        usa isso para saber se ainda precisa persistir a conta.
        """
        if job.custo_estimado is None:
            return False
        devolveu = sem_cobranca or job.id_remoto is None
        if devolveu:
            liberar(conta, job.custo_estimado)
        job.custo_estimado = None
        return devolveu

    def _devolver(self, job: Job, erro: str, gasta_tentativa: bool,
                  falha: dict | None = None, desistir: bool = False) -> None:
        """Devolve o job a fila.

        Com `id_remoto` preenchido, `id_remoto` e `conta_id` sao PRESERVADOS: existe
        geracao paga em curso no Flow, e a proxima tentativa retoma aquela em vez de
        gerar (e pagar) de novo deixando a primeira orfa. Um Quality de 11 minutos
        custa 200 creditos e nao entrega nada; na terceira tentativa, 300.

        `falha` e o material que o transporte deixou na sessao (print e log de
        passos). Quando o job falha DE VEZ, ele vai para o diagnostico automatico.
        """
        job.erro = erro
        if gasta_tentativa:
            job.tentativas += 1
        # `desistir` e para a falha que NAO melhora sozinha — hoje, o card trocado.
        # Gastar as tentativas restantes ali seria abrir navegador duas vezes mais para
        # chegar exatamente na mesma recusa, e adiar em minutos a noticia que a pessoa
        # precisa ter agora.
        if desistir or job.tentativas >= MAX_TENTATIVAS:
            job.estado = EstadoJob.FALHOU
        else:
            job.estado = EstadoJob.PENDENTE
            if job.id_remoto is None:
                job.conta_id = None
        self.repo.salvar_job(job)
        if job.estado is EstadoJob.FALHOU:
            self._agendar_diagnostico(job.id, falha)

    def _agendar_diagnostico(self, job_id: int, falha: dict | None) -> None:
        """Diagnostico em segundo plano: nunca segura a fila nem a chamada HTTP.

        O modelo local pode levar minutos so para carregar. Quem quer o resultado
        olha o evento `diagnostico` no detalhe do job quando ele aparecer.
        """
        if self.diagnosticador is None:
            return
        try:
            laco = asyncio.get_running_loop()
        except RuntimeError:
            return
        tarefa = laco.create_task(diagnostico.diagnosticar_job(
            self.repo, job_id, self.diagnosticador, falha))
        self._fundo.add(tarefa)
        tarefa.add_done_callback(self._fundo.discard)

    async def diagnosticar(self, job_id: int) -> str | None:
        """Diagnostico sob demanda (rota da API): espera e devolve o texto.

        Sem o material da sessao — o print e o log ja foram embora com ela — o modelo
        trabalha so com erro e eventos, e o prompt manda dizer o que nao da para
        afirmar.
        """
        if self.diagnosticador is None:
            return None
        return await diagnostico.diagnosticar_job(self.repo, job_id, self.diagnosticador)

    async def _esperar(self, sessao, job: Job, id_remoto: str) -> Estado:
        """Espera a geracao sair de RODANDO. NA_FILA espera igual, e conta diferente.

        Para a espera, "o robo esta gerando" e "o Google ainda nem comecou" sao a
        mesma ordem: continue. Para quem PERGUNTA, sao respostas opostas — e enquanto
        as duas chegavam ao painel como "gerando", toda demora do Flow parecia demora
        do FlowPool. `_anotar_fila` e o unico lugar que sabe da diferenca.

        O teto de `timeout_geracao` continua correndo durante a fila, de proposito:
        ele e a rede que impede um job de esperar para sempre, e afrouxa-lo por causa
        de um rotulo na tela do Google seria trocar uma rede por uma promessa.
        """
        limite = datetime.now(timezone.utc) + self.timeout_geracao
        try:
            while datetime.now(timezone.utc) < limite:
                estado = await self.transporte.consultar(sessao, id_remoto)
                # por VALOR, nao por identidade: `Estado` herda de `str, Enum`
                self._anotar_fila(job, estado == Estado.NA_FILA)
                if estado not in (Estado.RODANDO, Estado.NA_FILA):
                    return estado
                await asyncio.sleep(self.intervalo_polling)
            raise TimeoutError(f"geracao {id_remoto} passou de {self.timeout_geracao}")
        finally:
            # sair da espera e sair da fila, por qualquer porta — inclusive a do
            # timeout. Sem isto o campo ficaria ligado num job que ja terminou, e o
            # painel anunciaria fila para sempre.
            self._anotar_fila(job, False)

    def _anotar_fila(self, job: Job, na_fila: bool) -> None:
        """Liga e desliga o aviso de fila do Flow. So age na VIRADA.

        Idempotente de proposito: `_esperar` chama isto a cada volta do polling, e
        gravar sempre seria um UPDATE por segundo em cada job rodando.

        O CAMPO e o agora e o EVENTO e o historico, e por isso os dois existem. O
        campo zera quando a fila acaba — senao o painel mentiria — e o evento fica,
        que e o que permite responder depois quanto tempo se esperou pelo Google.
        """
        if na_fila == (job.na_fila_desde is not None):
            return
        job.na_fila_desde = datetime.now(timezone.utc) if na_fila else None
        self.repo.salvar_job(job)
        if na_fila:
            self.repo.registrar_evento(
                job.id, job.conta_id, "na_fila_do_flow",
                json.dumps({"id_remoto": job.id_remoto}))

    @staticmethod
    def _pedido(job: Job) -> Pedido:
        return Pedido(modo=job.modo, modelo=job.modelo, prompt=job.prompt,
                      referencias=job.referencias, frame_inicial=job.frame_inicial,
                      frame_final=job.frame_final, proporcao=job.proporcao,
                      quantidade=job.quantidade, duracao=job.duracao)

    async def rodar(self, intervalo: float = 2.0) -> None:
        """Laco eterno, usado pela API. Para com `parar()`.

        Reivindica SEM PARAR enquanto `passo()` disser que despachou. E isso que enche
        as janelas: com cinco contas livres e cinco jobs na fila, as cinco geracoes
        comecam em cinco voltas seguidas do laco, sem esperar nenhuma terminar. Quando
        `passo()` devolve False — sem vaga, sem conta livre ou sem job — o laco dorme.

        Tudo que escapa de `passo()` vira evento e o laco continua. Sem isso a task
        morre e ninguem observa: a API segue respondendo HTTP normalmente enquanto a
        fila nunca mais anda. Tres caminhos reais chegavam aqui — `fechar()` de um
        browser morto, `ModeloSemCusto` vindo de `escolher`, e falha de escrita no
        banco.
        """
        self._rodando = True
        while self._rodando:
            try:
                await self._fechar_ociosas()
                if await self.passo():
                    # ⚠️ SO NA VOLTA QUE DESPACHOU. Espacar tambem na volta vazia
                    # somaria esta espera a do laco ocioso, e o motor demoraria a
                    # reagir a um job novo — o oposto do que `_acordar` foi fazer.
                    self.marcar_despacho()
                    await self._espacar_despacho()
                else:
                    await self._dormir(intervalo)
            except Exception as e:
                self.repo.registrar_evento(
                    None, None, "passo_falhou",
                    json.dumps({"erro": f"{type(e).__name__}: {e}"}))
                await self._dormir(intervalo)
        # parada limpa: as geracoes ja despachadas terminam, e os navegadores — os
        # delas e os guardados — fecham. Sair sem isto deixaria Chrome orfao segurando
        # o perfil, e o proximo boot esbarraria em `PerfilOcupado` numa conta que nao
        # tem defeito nenhum.
        await self.drenar()
        await self.encerrar_todas()

    def parar(self) -> None:
        self._rodando = False
