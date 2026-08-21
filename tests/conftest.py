"""A suite NUNCA fala com o modelo de visao.

A auto-cura de seletores vem LIGADA por padrao — e o certo em producao, onde o Ollama
esta de pe. Mas um teste que resolve o curador padrao acabaria mandando um POST para o
`localhost:11434` da maquina de quem roda a suite, carregando 20 GB de modelo na GPU
por causa de um seletor falso.

Desligar por variavel de ambiente e o cinto; os testes de cura passam um curador FALSO
explicito, e curador explicito ignora a variavel — e assim que eles continuam medindo o
comportamento real sem tocar em rede.
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _cura_desligada_na_suite():
    anterior = os.environ.get("FLOWPOOL_CURA")
    os.environ["FLOWPOOL_CURA"] = "0"
    yield
    if anterior is None:
        os.environ.pop("FLOWPOOL_CURA", None)
    else:
        os.environ["FLOWPOOL_CURA"] = anterior
