"""Fumaca contra o Flow real, pelo transporte de verdade.

GASTA CREDITO em video. Imagem e gratuita (MEDIDO em 2026-08-20: saldo 50 -> 50).

A versao anterior deste arquivo falava com o Flow direto — foi assim que o chip foi
descoberto, e cumpriu o papel. A partir daqui ela exercita o TRANSPORTE, senao sao
dois codigos concorrentes e so um e o que roda em producao.

Uso:
    python -m flowpool.transporte.pw.fumaca <perfil> <modo> <modelo> "<prompt>" \
        [arquivos...] [--proporcao 9:16] [--quantidade 1]
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from flowpool.nucleo.tipos import Conta, Modelo, Modo, Tier
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
        referencias=a.arquivos if modo is Modo.INGREDIENTS2V else [],
        frame_inicial=a.arquivos[0] if modo is Modo.FRAMES2V and a.arquivos else None,
        frame_final=a.arquivos[1] if modo is Modo.FRAMES2V and len(a.arquivos) > 1 else None,
        proporcao=a.proporcao, quantidade=a.quantidade,
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
    asyncio.run(main(p.parse_args()))
