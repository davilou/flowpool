"""Projetos do Flow ja adotados, por conta."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from flowpool.api.esquemas import (
    RespostaProjeto,
)
from flowpool.dados.repo import Repo


def rotas_projetos(repo: Repo) -> APIRouter:
    rota = APIRouter()

    @rota.get("/projetos", response_model=list[RespostaProjeto])
    def listar_projetos(conta_id: int | None = None,
                        produto: str | None = None) -> list[RespostaProjeto]:
        """Projetos do Flow ja vinculados a cada conta.

        Um produto aparece uma vez por conta que gerou algo dele: projeto pertence a
        uma conta so, e a `g2` nao abre o projeto da `g1`.
        """
        achados = repo.projetos(conta_id)
        if produto is not None:
            achados = [p for p in achados if p.produto == produto]
        return [RespostaProjeto(id=p.id, conta_id=p.conta_id, produto=p.produto,
                                id_remoto=p.id_remoto, nome_remoto=p.nome_remoto)
                for p in achados]

    @rota.delete("/projetos/{id_}")
    def esquecer_projeto(id_: int) -> dict:
        """Esquece o vinculo LOCAL. NAO apaga o projeto no Flow.

        O projeto continua la, com tudo que ja foi gerado dentro dele. Na proxima
        geracao daquele produto naquela conta ele volta a ser adotado pelo nome.
        """
        alvo = next((p for p in repo.projetos() if p.id == id_), None)
        if alvo is None:
            raise HTTPException(404, "projeto nao existe")
        repo.esquecer_projeto(id_)
        return {"id": id_, "esquecido": True,
                "aviso": "o vinculo local foi apagado; o projeto continua existindo "
                         "no Flow e sera readotado pelo nome na proxima geracao"}


    return rota
