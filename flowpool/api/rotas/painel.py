"""Os tres arquivos do painel, servidos de dentro do pacote."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# Os tres arquivos do painel moram AO LADO do pacote `api`. Servi-los um a um, por
# caminho fixo, e diferente de montar um `StaticFiles`: aqui nao ha diretorio exposto,
# entao nao ha caminho que o navegador possa pedir e receber outra coisa.
_PAINEL = Path(__file__).resolve().parent.parent

# `no-cache` nos tres: sem cabecalho o navegador guarda o JS por conta propria e, depois
# de uma atualizacao, mostra o painel VELHO com a API nova — foi assim que o redesenho
# de 2026-09-06 pareceu "nao ter mudado nada". `no-cache` ainda revalida por
# ETag/Last-Modified, entao o custo e um 304 por arquivo, e nao o download.
_SEM_CACHE = {"Cache-Control": "no-cache"}


def rotas_painel() -> APIRouter:
    rota = APIRouter()


    @rota.get("/", include_in_schema=False)
    def painel() -> FileResponse:
        """O painel. HTML/JS puro servido pelo proprio FastAPI, sem build step."""
        return FileResponse(_PAINEL / "painel.html", media_type="text/html",
                            headers=_SEM_CACHE)

    @rota.get("/painel.css", include_in_schema=False)
    def painel_css() -> FileResponse:
        return FileResponse(_PAINEL / "painel.css", media_type="text/css",
                            headers=_SEM_CACHE)

    @rota.get("/painel.js", include_in_schema=False)
    def painel_js() -> FileResponse:
        return FileResponse(_PAINEL / "painel.js", media_type="text/javascript",
                            headers=_SEM_CACHE)

    return rota
