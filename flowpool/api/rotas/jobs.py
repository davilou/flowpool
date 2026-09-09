"""A fila: criar, ver, renomear, cancelar e diagnosticar job."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from flowpool.api.esquemas import (
    PatchJob,
    PedidoJob,
    PedidoLote,
    RespostaEvento,
    RespostaJob,
)
from flowpool.api.respostas import evento, job_de, resposta_job
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import Orquestrador
from flowpool.nucleo.tipos import (
    EstadoJob,
)


def rotas_jobs(repo: Repo, orq: Orquestrador, raiz: Path | str) -> APIRouter:
    rota = APIRouter()

    @rota.post("/jobs", response_model=RespostaJob, status_code=201)
    def criar_job(p: PedidoJob) -> RespostaJob:
        return resposta_job(repo.job(repo.criar_job(job_de(p))), raiz)

    @rota.post("/jobs/lote", status_code=201)
    def criar_lote(p: PedidoLote) -> dict:
        return {"ids": [repo.criar_job(job_de(j)) for j in p.jobs]}

    @rota.get("/jobs/{id_}", response_model=RespostaJob)
    def ver_job(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        return resposta_job(j, raiz)

    @rota.get("/jobs", response_model=list[RespostaJob])
    def listar_jobs(estado: EstadoJob | None = None) -> list[RespostaJob]:
        return [resposta_job(j, raiz) for j in repo.jobs(estado)]

    @rota.post("/jobs/{id_}/diagnosticar", response_model=RespostaEvento)
    async def diagnosticar_job(id_: int) -> RespostaEvento:
        """Pede ao modelo local um diagnostico do job AGORA, e espera a resposta.

        O motor ja faz isso sozinho quando um job falha de vez; esta rota e para
        pedir de novo, ou para job anterior ao diagnostico automatico. Sem o print e
        o log da sessao (que ja foram embora), o modelo trabalha com erro e eventos.
        """
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if not j.erro and j.estado is not EstadoJob.FALHOU:
            raise HTTPException(409, "o job nao tem falha registrada para diagnosticar")
        if orq.diagnosticador is None:
            raise HTTPException(
                503, "diagnostico desligado: sem modelo local configurado "
                     "(FLOWPOOL_DIAGNOSTICO / FLOWPOOL_CURA_URL)")
        texto = await orq.diagnosticar(id_)
        eventos = repo.eventos(id_)
        ultimo = next((e for e in reversed(eventos)
                       if e["tipo"] in ("diagnostico", "diagnostico_falhou")), None)
        if ultimo is None:
            raise HTTPException(502, "o diagnostico nao produziu evento")
        if texto is None:
            raise HTTPException(502, f"o modelo nao respondeu: "
                                     f"{json.loads(ultimo['payload']).get('erro', '?')}")
        return evento(ultimo)

    @rota.get("/jobs/{id_}/eventos", response_model=list[RespostaEvento])
    def eventos_do_job(id_: int) -> list[RespostaEvento]:
        """A linha do tempo de um job, do despacho ao fim.

        E o diagnostico que o painel abre quando um job falha: a mensagem de erro diz
        o ultimo passo, os eventos dizem o caminho — em que conta caiu, quantas vezes
        o download falhou, se ficou sem conta apta antes de rodar.
        """
        if repo.job(id_) is None:
            raise HTTPException(404, "job nao existe")
        return [evento(e) for e in repo.eventos(id_)]

    @rota.delete("/jobs/{id_}", response_model=RespostaJob)
    def cancelar(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if j.estado is not EstadoJob.PENDENTE:
            raise HTTPException(409, f"job esta {j.estado.value}, nao da pra cancelar")
        j.estado = EstadoJob.CANCELADO
        repo.salvar_job(j)
        return resposta_job(j, raiz)

    @rota.patch("/jobs/{id_}", response_model=RespostaJob)
    def renomear_job(id_: int, p: PatchJob) -> RespostaJob:
        """Troca o nome do job — so enquanto ele ainda esta na fila.

        O nome VIRA o nome do arquivo em disco (`<destino>/<nome>.mp4`). Depois que o
        arquivo existe, mudar o campo nao move nada: ficaria um job dizendo `heroi-2`
        apontando para um `heroi-1.mp4`. Renomear o arquivo de verdade seria mexer em
        disco por causa de um campo de texto, e nao e o que um botao de renomear deve
        fazer sem avisar. Entao 409, com o motivo.
        """
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if j.estado is not EstadoJob.PENDENTE:
            raise HTTPException(
                409, f"job esta {j.estado.value}: o nome so muda enquanto o arquivo "
                     "ainda nao existe em disco")
        repo.renomear_job(id_, p.nome)
        return resposta_job(repo.job(id_), raiz)


    return rota
