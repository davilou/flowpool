"""Fumaca contra o Flow real, pelo transporte de verdade.

GASTA CREDITO em video. Imagem e gratuita (MEDIDO em 2026-08-20: saldo 50 -> 50).

A versao anterior deste arquivo falava com o Flow direto — foi assim que o chip foi
descoberto, e cumpriu o papel. A partir daqui ela exercita o TRANSPORTE, senao sao
dois codigos concorrentes e so um e o que roda em producao.

Os arquivos posicionais mudam de papel com o MODO, igual ao transporte: em `frames2v`
sao o frame inicial e o final; em `ingredients2v` e em `imagem`, ate tres referencias.

Uso:
    python -m flowpool.transporte.pw.fumaca <perfil> <modo> <modelo> "<prompt>" \
        [arquivos...] [--proporcao 9:16] [--quantidade 1] [--duracao 4s]

    # imagem com referencia — o comeco do pipeline: foto da roupa -> modelo vestida.
    # Nao gasta credito (imagem e gratuita) e exercita as DUAS correcoes de hoje: a
    # troca de modo vindo de video e o anexo em modo imagem.
    python -m flowpool.transporte.pw.fumaca perfis/g1 imagem nano-banana \
        "a modelo vestindo a roupa da referencia" "Moda/roupa.png"
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from flowpool.nucleo.tipos import (
    DURACAO_PADRAO, DURACOES, Conta, Modelo, Modo, Tier, aceita_referencia,
    max_referencias,
)
from flowpool.transporte.base import Estado, Pedido, Sessao
from flowpool.transporte.pw.transporte import TransportePlaywright

PRODUTO_FUMACA = "Fumaca"


async def entrar_no_projeto(t: TransportePlaywright, s: Sessao, produto: str) -> str:
    """Adota pelo nome e so cria se nao existir — a MESMA ordem do orquestrador.

    De proposito: rodar a fumaca duas vezes nao pode deixar dois "Fumaca" na conta, e
    e essa a unica forma de a fumaca provar a adocao.
    """
    achado = next((p for p in await t.listar_projetos(s) if p.nome == produto), None)
    id_remoto = achado.id_remoto if achado else await t.criar_projeto(s, produto)
    print("adotado" if achado else "criado", "projeto:", id_remoto)
    await t.abrir_projeto(s, id_remoto)
    return id_remoto


async def main(a: argparse.Namespace) -> None:
    t = TransportePlaywright()
    conta = Conta(id=1, email="fumaca", tier=Tier.FREE, perfil=a.perfil)
    modo = Modo(a.modo)
    pedido = Pedido(
        modo=modo, modelo=Modelo(a.modelo), prompt=a.prompt,
        # IMAGEM TAMBEM: o Flow gera imagem a partir de uma foto, e e por ai que o
        # pipeline do usuario comeca. Quem aceita sai do dominio, nao de um `if` daqui.
        referencias=(a.arquivos[:max_referencias(modo)]
                     if aceita_referencia(modo) else []),
        frame_inicial=a.arquivos[0] if modo is Modo.FRAMES2V and a.arquivos else None,
        frame_final=a.arquivos[1] if modo is Modo.FRAMES2V and len(a.arquivos) > 1 else None,
        proporcao=a.proporcao, quantidade=a.quantidade, duracao=a.duracao,
    )
    s = await t.abrir_sessao(conta)
    try:
        antes = await t.saldo(s)
        print("saldo antes:", antes)
        await entrar_no_projeto(t, s, PRODUTO_FUMACA)
        id_remoto = await t.gerar(s, pedido)
        print("gerando:", id_remoto)
        while await t.consultar(s, id_remoto) is Estado.RODANDO:
            await asyncio.sleep(10)
            print("...")
        # extensao de mentira de proposito: o transporte troca pela real do Flow
        print("arquivo:", await t.baixar(s, id_remoto, Path("scratch/fumaca.mp4")))
        depois = await t.saldo(s)
        print(f"saldo depois: {depois}  (custo real: {antes - depois})")
    finally:
        await t.fechar(s)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("perfil")
    p.add_argument("modo", choices=[m.value for m in Modo])
    p.add_argument("modelo", choices=[m.value for m in Modelo])
    p.add_argument("prompt")
    p.add_argument("arquivos", nargs="*")
    p.add_argument("--proporcao", default="9:16")
    p.add_argument("--quantidade", type=int, default=1)
    # a fumaca GASTA CREDITO em video, e a duracao e o que mais mexe na conta: 7
    # creditos em 4s contra 12 em 8s no Omni Flash. Default no padrao do dominio.
    p.add_argument("--duracao", choices=list(DURACOES), default=DURACAO_PADRAO)
    asyncio.run(main(p.parse_args()))
