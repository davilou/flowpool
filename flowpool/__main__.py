"""Sobe o servico e o painel.

    python -m flowpool

O painel abre em http://127.0.0.1:8777/ — mesma porta da API, porque o painel E cliente
dela: servido pelo proprio FastAPI, sem build step e sem segundo servidor.
"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.transporte.pw.transporte import TransportePlaywright

RAIZ = Path(__file__).resolve().parent.parent   # a pasta "Tiktok Shop"
BANCO = RAIZ / "flowpool" / "flowpool.db"

PORTA = 8777

# UM por enquanto, e o motivo nao e RAM.
#
# Cada worker precisa de uma janela de Chrome VISIVEL: o IntersectionObserver que
# renderiza a lista de projetos e libera o aviso de privacidade nao dispara com
# `visibilityState == 'hidden'`, e o Chrome no Windows marca como oculta a janela que
# esta totalmente coberta por outra. Duas janelas empilhadas numa tela so sao uma
# janela funcionando e outra travada sem erro nenhum.
#
# Alem disso, duas sessoes no mesmo perfil sao impossiveis: dois Chromes com o mesmo
# --user-data-dir fazem o segundo entregar para o primeiro e morrer.
MAX_SIMULTANEOS = 1


def main() -> None:
    repo = Repo(BANCO)
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransportePlaywright(), raiz=RAIZ,
                    rodar_motor=True, max_simultaneos=MAX_SIMULTANEOS)
    print(f"FlowPool no ar. Painel: http://127.0.0.1:{PORTA}/")
    uvicorn.run(app, host="127.0.0.1", port=PORTA)


if __name__ == "__main__":
    main()
