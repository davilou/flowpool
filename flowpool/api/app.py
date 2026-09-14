"""Monta o servico: o motor, o ciclo de vida e os grupos de rota.

Este modulo nao TEM rota nenhuma — ele decide o que existe e em que ordem. Cada grupo
mora em `api/rotas/` e e uma funcao-fabrica que recebe o que precisa (`repo`, `orq`,
`raiz`) e devolve um `APIRouter`. A fabrica existe no lugar de um router de modulo
justamente para isso: as rotas fecham sobre essas tres coisas, e um router global
exigiria `Depends` ou estado de modulo para chegar nelas.

Nenhum router leva `prefix`: os caminhos sao os mesmos de sempre, e a ordem de inclusao
aqui e a ordem em que o FastAPI casa as rotas.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from flowpool.api.rotas.colagem import rotas_colagem
from flowpool.api.rotas.contas import rotas_contas
from flowpool.api.rotas.jobs import rotas_jobs
from flowpool.api.rotas.midia import rotas_midia
from flowpool.api.rotas.painel import rotas_painel
from flowpool.api.rotas.projetos import rotas_projetos
from flowpool.api.rotas.servico import rotas_servico
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import MAX_SIMULTANEOS, Orquestrador
from flowpool.transporte.base import FlowTransport


def criar_app(repo: Repo, transporte: FlowTransport, raiz: Path | str,
              rodar_motor: bool = False,
              max_simultaneos: int = MAX_SIMULTANEOS,
              diagnosticador=None) -> FastAPI:
    orq = Orquestrador(repo, transporte, raiz, max_simultaneos=max_simultaneos,
                       diagnosticador=diagnosticador)

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        # antes de qualquer coisa: um crash anterior pode ter deixado job preso em
        # `rodando` com credito reservado que nunca voltou
        orq.recuperar()
        tarefa = asyncio.create_task(orq.rodar()) if rodar_motor else None
        yield
        if tarefa:
            orq.parar()
            tarefa.cancel()
            # ⚠️ O `cancel` ACIMA TORNA INALCANCAVEL O FIM DE `rodar()`, e era la que
            # morava a parada limpa. O laco fica quase sempre dormindo entre voltas;
            # cancelar ali levanta `CancelledError` no `await`, o `while` sai por
            # excecao e o `await self.encerrar_todas()` do rodape nunca roda. Efeito:
            # TODO desligamento do servico deixava as janelas do Chrome abertas —
            # sem dono, segurando o `--user-data-dir`, e o boot seguinte esbarrando em
            # perfil ocupado.
            #
            # Fechar aqui, e nao esperar o laco: desligamento tem que ser rapido e
            # previsivel. A geracao em curso morre — ja morria —, e o job preso em
            # `rodando` e resolvido por `orq.recuperar()` no boot seguinte. O que muda
            # e que agora o navegador dela fecha junto.
            await orq.encerrar_todas()

    app = FastAPI(title="FlowPool", lifespan=ciclo)
    app.state.repo = repo
    app.state.orq = orq

    app.include_router(rotas_painel())
    app.include_router(rotas_midia(raiz))
    app.include_router(rotas_colagem(raiz))
    app.include_router(rotas_jobs(repo, orq, raiz))
    app.include_router(rotas_projetos(repo))
    app.include_router(rotas_contas(repo, orq, raiz))
    app.include_router(rotas_servico(repo, orq, raiz))
    return app
