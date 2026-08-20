"""Casa job com conta e conduz o ciclo de vida. Nao conhece Playwright."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flowpool.dados.repo import Repo
from flowpool.nucleo.credito import liberar, reconciliar, reservar
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modo, SaudeSessao
from flowpool.transporte.base import (
    Estado, FlowTransport, GeracaoFalhou, Pedido, SaldoInsuficiente, SessaoMorta,
)

MAX_TENTATIVAS = 3
ESPERA_RESET = timedelta(hours=1)
TIMEOUT_GERACAO = timedelta(minutes=10)


def _extensao(modo: Modo) -> str:
    return ".png" if modo is Modo.IMAGEM else ".mp4"


class Orquestrador:
    def __init__(self, repo: Repo, transporte: FlowTransport, raiz: Path | str,
                 max_simultaneos: int = 3) -> None:
        self.repo = repo
        self.transporte = transporte
        self.raiz = Path(raiz)
        self.sem = asyncio.Semaphore(max_simultaneos)
        self._rodando = False

    async def passo(self) -> bool:
        """Puxa um job e leva ate o fim. Devolve False se nao havia job."""
        agora = datetime.now(timezone.utc)
        job = self.repo.proximo_pendente(agora)
        if job is None:
            return False

        conta = escolher(self.repo.contas(), job.modelo)
        if conta is None:
            job.reavaliar_em = agora + ESPERA_RESET
            self.repo.salvar_job(job)
            self.repo.registrar_evento(job.id, None, "sem_conta_apta")
            return True

        async with self.sem:
            await self._executar(job, conta)
        return True

    async def _executar(self, job: Job, conta: Conta) -> None:
        job.estado = EstadoJob.RODANDO
        job.conta_id = conta.id
        self.repo.salvar_job(job)
        self.repo.registrar_evento(job.id, conta.id, "despachado",
                                   json.dumps({"modelo": job.modelo.value}))

        sessao = None
        try:
            sessao = await self.transporte.abrir_sessao(conta)
            # guardar o reservado no job e o que permite estornar depois — inclusive
            # depois de um restart, por isso vai para o banco na hora
            job.custo_estimado = reservar(conta, job.modelo)
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
            # nao ha estorno aqui: `abrir_sessao` falha antes de `reservar` rodar
            conta.saude = SaudeSessao.EXPIRADA
            self.repo.salvar_conta(conta)
            self._devolver(job, str(e), gasta_tentativa=False)
            self.repo.registrar_evento(job.id, conta.id, "sessao_morta")

        except SaldoInsuficiente as e:
            # Vem de duas fontes: o guarda local em `reservar` (a conta tem credito
            # real, so nao o bastante para este modelo) ou o proprio Flow. Estornar e
            # deixar a reconciliacao do `finally` adotar o numero verdadeiro — zerar o
            # saldo na mao apagaria credito que existe e tiraria a conta do pool.
            self._estornar(job, conta)
            conta.saldo_confiavel = False
            self.repo.salvar_conta(conta)
            self._devolver(job, str(e), gasta_tentativa=False)
            self.repo.registrar_evento(job.id, conta.id, "conta_seca")

        except Exception as e:  # falha de rede, timeout, geracao errada
            # `_estornar` decide sozinho se devolve: so devolve enquanto o Flow ainda
            # nao aceitou o pedido. Falha de download ou geracao recusada acontece
            # depois da cobranca, e ali estornar inflaria o saldo.
            self._estornar(job, conta)
            conta.falhas += 1
            self.repo.salvar_conta(conta)
            self._devolver(job, f"{type(e).__name__}: {e}", gasta_tentativa=True)
            self.repo.registrar_evento(job.id, conta.id, "falhou",
                                       json.dumps({"erro": str(e)}))

        finally:
            if sessao is not None:
                try:
                    lido = await self.transporte.saldo(sessao)
                    reconciliar(conta, lido, datetime.now(timezone.utc))
                    conta.falhas = 0 if job.estado is EstadoJob.PRONTO else conta.falhas
                    self.repo.salvar_conta(conta)
                except Exception:
                    pass
                await self.transporte.fechar(sessao)

    def _estornar(self, job: Job, conta: Conta) -> None:
        """Devolve ao saldo a reserva de um job que nao chegou a virar geracao.

        So estorna enquanto `id_remoto is None` — ou seja, enquanto o Flow ainda nao
        aceitou o pedido. Depois disso o credito provavelmente JA foi cobrado, e
        devolver inflaria o saldo local. Nao da para contar com a reconciliacao do
        `finally` para consertar: a falha que trouxe o codigo ate aqui costuma ser de
        rede, e a leitura de saldo falha junto. O saldo inflado sobreviveria, a conta
        seria escolhida de novo, o Flow recusaria, e o ciclo se repetiria.

        Idempotente: zera `custo_estimado` para nao estornar duas vezes. Quem chama
        salva o job logo em seguida (`_devolver`), entao o zero chega ao banco.
        """
        if job.custo_estimado is None:
            return
        if job.id_remoto is None:
            liberar(conta, job.custo_estimado)
        job.custo_estimado = None

    def _devolver(self, job: Job, erro: str, gasta_tentativa: bool) -> None:
        job.erro = erro
        if gasta_tentativa:
            job.tentativas += 1
        if job.tentativas >= MAX_TENTATIVAS:
            job.estado = EstadoJob.FALHOU
        else:
            job.estado = EstadoJob.PENDENTE
            job.conta_id = None
            job.id_remoto = None
        self.repo.salvar_job(job)

    async def _esperar(self, sessao, id_remoto: str) -> Estado:
        limite = datetime.now(timezone.utc) + TIMEOUT_GERACAO
        while datetime.now(timezone.utc) < limite:
            estado = await self.transporte.consultar(sessao, id_remoto)
            if estado is not Estado.RODANDO:
                return estado
            await asyncio.sleep(5)
        raise TimeoutError(f"geracao {id_remoto} passou de {TIMEOUT_GERACAO}")

    @staticmethod
    def _pedido(job: Job) -> Pedido:
        return Pedido(modo=job.modo, modelo=job.modelo, prompt=job.prompt,
                      referencias=job.referencias, frame_inicial=job.frame_inicial,
                      frame_final=job.frame_final)

    async def rodar(self, intervalo: float = 2.0) -> None:
        """Laco eterno, usado pela API. Para com `parar()`."""
        self._rodando = True
        while self._rodando:
            if not await self.passo():
                await asyncio.sleep(intervalo)

    def parar(self) -> None:
        self._rodando = False
