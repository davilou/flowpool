"""Navegar o disco, ver miniatura, servir e baixar o que foi gerado."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from flowpool.api.arquivos import (
    CaminhoForaDaRaiz,
    arquivo_de_imagem,
    arquivo_de_midia,
    listar,
    tipo_de,
)
from flowpool.api.esquemas import (
    RespostaArquivos,
)

# Teto do zip de "baixar tudo". Nao e limite de disco: e o tamanho que cabe na MEMORIA
# sem o servico local engasgar, porque o zip e montado inteiro antes de sair. Acima
# disso a resposta e um 413 dizendo para baixar por item, que sempre funciona.
_TETO_ZIP = 512 * 1024 * 1024


def rotas_midia(raiz: Path | str) -> APIRouter:
    rota = APIRouter()

    @rota.get("/arquivos", response_model=RespostaArquivos)
    def arquivos(caminho: str = "") -> RespostaArquivos:
        """Pastas e imagens de um diretorio, relativo a raiz do projeto.

        Existe porque o painel escolhe imagem OLHANDO, e o transporte precisa do caminho
        em disco: `set_input_files` abre o arquivo. Upload nao resolveria — o
        `<input type=file>` do navegador entrega o conteudo e nunca o caminho real.
        """
        try:
            return RespostaArquivos(**listar(raiz, caminho))
        except CaminhoForaDaRaiz as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except NotADirectoryError as e:
            raise HTTPException(400, str(e)) from e

    @rota.get("/miniatura", include_in_schema=False)
    def miniatura(caminho: str) -> FileResponse:
        """Serve a imagem para a grade do seletor.

        Serve o ORIGINAL, sem redimensionar: as imagens tem alguns MB, mas o servico e
        local e a rede e o loopback. Reduzir custaria uma dependencia nova (Pillow) para
        economizar um trafego que nunca sai da maquina — troca ruim. O `Cache-Control`
        e o que faz a diferenca de verdade: a mesma pasta reaberta nao rele nada.
        """
        try:
            alvo = arquivo_de_imagem(raiz, caminho)
        except CaminhoForaDaRaiz as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(alvo, media_type=tipo_de(alvo),
                            headers={"Cache-Control": "private, max-age=3600"})

    def _midia(caminho: str) -> Path:
        """Resolve um caminho de midia, ou levanta o HTTP certo.

        As tres rotas abaixo passam por aqui, e por `arquivos.resolver` dentro dela: e a
        MESMA barreira do seletor de imagens, e nao uma segunda escrita a mao. Servico
        local sem autenticacao — duas barreiras parecidas viram, com o tempo, uma
        barreira certa e uma errada.
        """
        try:
            return arquivo_de_midia(raiz, caminho)
        except CaminhoForaDaRaiz as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e

    @rota.get("/midia", include_in_schema=False)
    def midia(caminho: str) -> FileResponse:
        """Serve o que foi gerado — imagem OU video — para a galeria olhar.

        Separada de `/miniatura` porque as duas respondem a perguntas diferentes:
        `/miniatura` alimenta o SELETOR de referencias, e referencia do Flow e imagem;
        aqui o assunto e a saida, e a saida costuma ser video.

        `FileResponse` responde a `Range`, e e disso que o `<video preload=metadata>`
        depende para mostrar o primeiro quadro sem baixar o clipe inteiro.
        """
        alvo = _midia(caminho)
        return FileResponse(alvo, media_type=tipo_de(alvo),
                            headers={"Cache-Control": "private, max-age=3600"})

    @rota.get("/baixar", include_in_schema=False)
    def baixar(caminho: str) -> FileResponse:
        """O mesmo arquivo, mas como download, com o nome original.

        O arquivo JA esta em disco — o transporte baixou para `<produto>/clipes/`. O que
        esta rota resolve nao e guardar, e ALCANCAR: quem esta no painel nao quer sair
        dele e caçar a pasta para levar o clipe para o editor.
        """
        alvo = _midia(caminho)
        return FileResponse(alvo, media_type=tipo_de(alvo), filename=alvo.name)

    @rota.get("/baixar-lote", include_in_schema=False)
    def baixar_lote(caminho: list[str] = Query(default_factory=list)) -> Response:
        """Varios arquivos num zip so.

        `zipfile` e da biblioteca padrao — nenhuma dependencia nova entrou por isto.
        Montado em memoria e devolvido inteiro: sao clipes de poucos MB num servico
        local, e streaming exigiria um zip incremental para economizar um pico de RAM
        que dura o tempo da resposta. O teto existe para o dia em que a selecao for
        grande demais, e a mensagem manda para o caminho que sempre funciona.

        Cada item passa pela MESMA barreira do download de um — um caminho torto no meio
        da lista derruba o lote inteiro com 400, em vez de sair do zip calado.
        """
        if not caminho:
            raise HTTPException(400, "nada selecionado para baixar")
        alvos = [_midia(c) for c in caminho]
        total = 0
        buffer = io.BytesIO()
        # ZIP_STORED e nao DEFLATE: mp4 e png ja estao comprimidos, entao comprimir de
        # novo gasta CPU para ganhar quase nada.
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as z:
            vistos: dict[str, int] = {}
            for alvo in alvos:
                total += alvo.stat().st_size
                if total > _TETO_ZIP:
                    raise HTTPException(
                        413, "a selecao passa de 512 MB e o zip nao cabe de uma vez; "
                             "baixe por item ou selecione menos")
                # dois produtos podem ter um `c1.mp4` cada. Sem o desempate, o segundo
                # entraria no zip com o mesmo nome e um dos dois sumiria na extracao.
                nome = alvo.name
                if nome in vistos:
                    vistos[nome] += 1
                    nome = f"{alvo.stem} ({vistos[alvo.name]}){alvo.suffix}"
                else:
                    vistos[nome] = 1
                z.write(alvo, arcname=nome)
        carimbo = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            buffer.getvalue(), media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="flowpool-{carimbo}.zip"'})


    return rota
