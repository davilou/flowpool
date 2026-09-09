"""Ctrl+V e arrastar-soltar: bytes que ainda nao existem em disco viram arquivo."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from flowpool.api import colar as colagem
from flowpool.api.esquemas import (
    PedidoColarUrl,
    RespostaColagem,
)


def rotas_colagem(raiz: Path | str) -> APIRouter:
    rota = APIRouter()

    def _gravar_colagem(dados: bytes) -> RespostaColagem:
        """Grava os bytes colados, traduzindo a recusa no HTTP certo.

        400 para "isto nao e imagem" e 413 para "passou do teto" — dois motivos
        diferentes que o painel mostra com palavras diferentes.
        """
        try:
            return RespostaColagem(**colagem.guardar(raiz, dados))
        except colagem.ConteudoGrande as e:
            raise HTTPException(413, str(e)) from e
        except colagem.ConteudoRecusado as e:
            raise HTTPException(400, str(e)) from e

    async def _corpo_limitado(req: Request) -> bytes:
        """Le o corpo inteiro, desistindo assim que passar do teto.

        Le por pedacos em vez de `await req.body()`: aquele traria o corpo inteiro para
        a memoria ANTES de qualquer conferencia, e ai o teto ja teria sido pago. O
        `Content-Length` e conferido primeiro so por gentileza — e um numero que o
        cliente escreve, entao a conta que vale e a dos bytes que realmente chegaram.
        """
        grande = HTTPException(
            413, f"a imagem passa de {colagem.TETO_BYTES // 1048576} MB")
        declarado = req.headers.get("content-length")
        if declarado and declarado.isdigit() and int(declarado) > colagem.TETO_BYTES:
            raise grande
        pedacos = bytearray()
        async for pedaco in req.stream():
            pedacos += pedaco
            if len(pedacos) > colagem.TETO_BYTES:
                raise grande
        return bytes(pedacos)

    @rota.post("/colar", response_model=RespostaColagem, status_code=201)
    async def colar(req: Request) -> RespostaColagem:
        """Recebe os BYTES de uma imagem colada e devolve o caminho dela em disco.

        Diferente do seletor: la a imagem ja existe e o painel so aponta para ela. Aqui
        a imagem veio da area de transferencia — um "copiar imagem" em qualquer site, um
        print de tela — e nao existe em lugar nenhum. Como o transporte anexa a
        referencia com `set_input_files`, que abre um arquivo em disco, gravar e a unica
        saida.

        O corpo e a imagem crua, sem `multipart`: aquilo exigiria `python-multipart`,
        uma dependencia nova para embrulhar bytes que ja viajam sozinhos muito bem.

        O nome do arquivo sai do HASH do conteudo, e o tipo sai da ASSINATURA dos bytes.
        Nada do que o cliente escreve — nome, extensao, `Content-Type` — decide onde o
        arquivo cai nem se ele e aceito.
        """
        return _gravar_colagem(await _corpo_limitado(req))

    @rota.post("/colar-url", response_model=RespostaColagem, status_code=201)
    async def colar_url(p: PedidoColarUrl) -> RespostaColagem:
        """O mesmo, quando o que foi colado foi o ENDERECO da imagem e nao ela.

        No menu do navegador "Copiar imagem" e "Copiar endereco da imagem" sao vizinhas,
        e cair na segunda sem querer e o caso comum, nao a excecao. Entao o painel trata
        as duas, e a diferenca fica aqui dentro.

        Esta rota SAI PARA A INTERNET a partir de um servico local sem autenticacao, e
        `colar.validar_url` e a cerca: so http e https, e so endereco publico, conferido
        pelo IP RESOLVIDO. Redirecionamento e revalidado salto a salto — senao um `302`
        para `127.0.0.1` entraria por baixo da cerca.
        """
        try:
            dados = await colagem.buscar(p.url)
        except colagem.UrlRecusada as e:
            raise HTTPException(400, str(e)) from e
        except colagem.ConteudoGrande as e:
            raise HTTPException(413, str(e)) from e
        except colagem.BuscaFalhou as e:
            # 502 e nao 400: a URL era aceitavel e quem falhou foi o outro lado. A
            # diferenca importa para quem le a mensagem — nao ha nada a corrigir no
            # texto colado.
            raise HTTPException(502, str(e)) from e
        return _gravar_colagem(dados)


    return rota
