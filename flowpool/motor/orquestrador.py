"""Casa job com conta e conduz o ciclo de vida. Nao conhece Playwright."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flowpool.dados.repo import Repo
from flowpool.nucleo.credito import LIMIAR_DIVERGENCIA, liberar, reconciliar, reservar
from flowpool.nucleo.escolha import apta, escolher
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modo, SaudeSessao
from flowpool.transporte.base import (
    Estado, FlowTransport, GeracaoFalhou, Pedido, SaldoInsuficiente, SessaoMorta,
)

MAX_TENTATIVAS = 3
ESPERA_RESET = timedelta(hours=1)
TIMEOUT_GERACAO = timedelta(minutes=10)
INTERVALO_POLLING = 5.0


def _extensao(modo: Modo) -> str:
    return ".png" if modo is Modo.IMAGEM else ".mp4"


class Orquestrador:
    def __init__(self, repo: Repo, transporte: FlowTransport, raiz: Path | str,
                 max_simultaneos: int = 3,
                 timeout_geracao: timedelta = TIMEOUT_GERACAO,
                 intervalo_polling: float = INTERVALO_POLLING) -> None:
        self.repo = repo
        self.transporte = transporte
        self.raiz = Path(raiz)
        self.sem = asyncio.Semaphore(max_simultaneos)
        # parametrizados para o teste nao ter que esperar dez minutos de verdade
        self.timeout_geracao = timeout_geracao
        self.intervalo_polling = intervalo_polling
        self._rodando = False

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

    async def passo(self) -> bool:
        """Puxa um job e leva ate o fim. Devolve False se nao havia job.

        O semaforo e adquirido ANTES de reivindicar. Reivindicar primeiro deixaria o
        job puxado visivel como `pendente` enquanto o worker espera na fila do
        semaforo — e outro worker o pegaria, gerando e pagando duas vezes pelo mesmo
        job, com o perdedor sobrescrevendo o arquivo do vencedor.
        """
        async with self.sem:
            agora = datetime.now(timezone.utc)
            job = self.repo.reivindicar(agora)
            if job is None:
                return False

            try:
                conta = self._conta_para(job, agora)
            except Exception as e:
                # `reivindicar` ja marcou o job `rodando`. Deixar a excecao subir
                # daqui o deixaria travado nesse estado ate o proximo boot — e
                # `ModeloSemCusto`, que e erro de configuracao, e exatamente o caso
                # que chega aqui. Gasta tentativa: reprocessar para sempre um modelo
                # sem custo cadastrado nao conserta o cadastro.
                erro = f"{type(e).__name__}: {e}"
                self.repo.registrar_evento(job.id, None, "escolha_falhou",
                                           json.dumps({"erro": erro}))
                self._devolver(job, erro, gasta_tentativa=True)
                return True

            if conta is None:
                job.estado = EstadoJob.PENDENTE
                job.reavaliar_em = agora + ESPERA_RESET
                self.repo.salvar_job(job)
                self.repo.registrar_evento(job.id, None, "sem_conta_apta")
                return True

            await self._executar(job, conta)
        return True

    def _conta_para(self, job: Job, agora: datetime) -> Conta | None:
        """Conta em que este job deve rodar.

        Job que volta a fila com `id_remoto` tem geracao paga em curso no Flow, e ela
        so pode ser retomada de dentro da conta que a criou — por isso aquela conta
        tem preferencia. Se ela nao servir mais, a escolha normal vale e o job gera do
        zero.
        """
        contas = self.repo.contas()
        if job.id_remoto is not None and job.conta_id is not None:
            anterior = next((c for c in contas if c.id == job.conta_id), None)
            if anterior is not None and apta(anterior, job.modelo, agora,
                                             job.quantidade, job.duracao):
                return anterior
        return escolher(contas, job.modelo, agora, job.quantidade, job.duracao)

    async def _executar(self, job: Job, conta: Conta) -> None:
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
        try:
            sessao = await self.transporte.abrir_sessao(conta)
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
                self.repo.salvar_conta(conta)
                self.repo.salvar_job(job)

                job.id_remoto = await self.transporte.gerar(sessao, self._pedido(job))
                self.repo.salvar_job(job)

            estado = await self._esperar(sessao, job.id_remoto)
            if estado is not Estado.PRONTO:
                raise GeracaoFalhou(f"Flow devolveu {estado.value}")

            destino = self.raiz / job.destino / f"{job.nome}{_extensao(job.modo)}"
            arquivo = await self.transporte.baixar(sessao, job.id_remoto, destino)

            job.arquivo = str(arquivo)
            job.estado = EstadoJob.PRONTO
            job.erro = None
            self.repo.salvar_job(job)
            self.repo.registrar_evento(job.id, conta.id, "pronto")

        except SessaoMorta as e:
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
            self._devolver(job, str(e), gasta_tentativa=False)

        except SaldoInsuficiente as e:
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
            conta.seca_ate = datetime.now(timezone.utc) + ESPERA_RESET
            conta.saldo_confiavel = False
            self.repo.salvar_conta(conta)
            job.reavaliar_em = conta.seca_ate
            self._evento_de_falha(job, conta, "conta_seca")
            self._devolver(job, str(e), gasta_tentativa=False)

        except Exception as e:  # falha de rede, timeout, geracao errada
            # `_estornar` decide sozinho se devolve: so devolve enquanto o Flow ainda
            # nao aceitou o pedido. Falha de download ou geracao recusada acontece
            # depois da cobranca, e ali estornar inflaria o saldo.
            self._estornar(job, conta)
            conta.falhas += 1
            self.repo.salvar_conta(conta)
            self._evento_de_falha(job, conta, "falhou", {"erro": str(e)})
            self._devolver(job, f"{type(e).__name__}: {e}", gasta_tentativa=True)

        finally:
            if sessao is not None:
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
                except Exception as e:
                    # o `except: pass` daqui engolia tambem falha de `salvar_conta` —
                    # o silencio que transformava conta seca em laco infinito
                    self.repo.registrar_evento(job.id, conta.id, "reconciliacao_falhou",
                                               json.dumps({"erro": str(e)}))
                try:
                    # fechar um browser ja morto levanta, e isso escapava do `try` de
                    # cima: a task de `rodar()` terminava e a fila parava em silencio
                    await self.transporte.fechar(sessao)
                except Exception as e:
                    self.repo.registrar_evento(job.id, conta.id, "fechar_falhou",
                                               json.dumps({"erro": str(e)}))

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

    def _estornar(self, job: Job, conta: Conta) -> bool:
        """Devolve ao saldo a reserva de um job que nao chegou a virar geracao.

        So estorna enquanto `id_remoto is None` — ou seja, enquanto o Flow ainda nao
        aceitou o pedido. Depois disso o credito provavelmente JA foi cobrado, e
        devolver inflaria o saldo local. Nao da para contar com a reconciliacao do
        `finally` para consertar: a falha que trouxe o codigo ate aqui costuma ser de
        rede, e a leitura de saldo falha junto. O saldo inflado sobreviveria, a conta
        seria escolhida de novo, o Flow recusaria, e o ciclo se repetiria.

        Idempotente: zera `custo_estimado` para nao estornar duas vezes. Quem chama
        salva o job logo em seguida (`_devolver`), entao o zero chega ao banco.

        Devolve True quando credito voltou de fato ao saldo em memoria — quem chamou
        usa isso para saber se ainda precisa persistir a conta.
        """
        if job.custo_estimado is None:
            return False
        devolveu = job.id_remoto is None
        if devolveu:
            liberar(conta, job.custo_estimado)
        job.custo_estimado = None
        return devolveu

    def _devolver(self, job: Job, erro: str, gasta_tentativa: bool) -> None:
        """Devolve o job a fila.

        Com `id_remoto` preenchido, `id_remoto` e `conta_id` sao PRESERVADOS: existe
        geracao paga em curso no Flow, e a proxima tentativa retoma aquela em vez de
        gerar (e pagar) de novo deixando a primeira orfa. Um Quality de 11 minutos
        custa 200 creditos e nao entrega nada; na terceira tentativa, 300.
        """
        job.erro = erro
        if gasta_tentativa:
            job.tentativas += 1
        if job.tentativas >= MAX_TENTATIVAS:
            job.estado = EstadoJob.FALHOU
        else:
            job.estado = EstadoJob.PENDENTE
            if job.id_remoto is None:
                job.conta_id = None
        self.repo.salvar_job(job)

    async def _esperar(self, sessao, id_remoto: str) -> Estado:
        limite = datetime.now(timezone.utc) + self.timeout_geracao
        while datetime.now(timezone.utc) < limite:
            estado = await self.transporte.consultar(sessao, id_remoto)
            if estado is not Estado.RODANDO:
                return estado
            await asyncio.sleep(self.intervalo_polling)
        raise TimeoutError(f"geracao {id_remoto} passou de {self.timeout_geracao}")

    @staticmethod
    def _pedido(job: Job) -> Pedido:
        return Pedido(modo=job.modo, modelo=job.modelo, prompt=job.prompt,
                      referencias=job.referencias, frame_inicial=job.frame_inicial,
                      frame_final=job.frame_final, proporcao=job.proporcao,
                      quantidade=job.quantidade, duracao=job.duracao)

    async def rodar(self, intervalo: float = 2.0) -> None:
        """Laco eterno, usado pela API. Para com `parar()`.

        Tudo que escapa de `passo()` vira evento e o laco continua. Sem isso a task
        morre e ninguem observa: a API segue respondendo HTTP normalmente enquanto a
        fila nunca mais anda. Tres caminhos reais chegavam aqui — `fechar()` de um
        browser morto, `ModeloSemCusto` vindo de `escolher`, e falha de escrita no
        banco.
        """
        self._rodando = True
        while self._rodando:
            try:
                if not await self.passo():
                    await asyncio.sleep(intervalo)
            except Exception as e:
                self.repo.registrar_evento(
                    None, None, "passo_falhou",
                    json.dumps({"erro": f"{type(e).__name__}: {e}"}))
                await asyncio.sleep(intervalo)

    def parar(self) -> None:
        self._rodando = False
