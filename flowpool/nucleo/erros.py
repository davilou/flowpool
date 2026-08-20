"""Erros de dominio. Camadas de cima importam daqui; nucleo nao importa de ninguem."""


class SaldoInsuficiente(Exception):
    """A conta nao tem credito para o custo pedido. Nao e falha: e hora de trocar de conta."""


class ModeloSemCusto(Exception):
    """Modelo/tier sem custo cadastrado. Erro de configuracao, nao de execucao."""


class DuracaoSemCusto(Exception):
    """Duracao sem custo cadastrado. Irma de `ModeloSemCusto`, e pelo mesmo motivo.

    Chutar um preco para uma duracao que a tabela nao conhece mandaria reservar um
    numero inventado, e o erro so reapareceria mais tarde como conta seca — longe da
    causa. Erro de configuracao, nao de execucao.
    """


class ProxyInvalido(Exception):
    """A conta tem proxy configurado, mas a URL nao da para usar.

    Nao e o caso de nao haver proxy — esse e o comum, e devolve None. E o caso de haver
    um e ele estar errado, e ai falhar cedo e melhor do que abrir um navegador que sai
    pelo IP de casa sem ninguem perceber. O IP proprio por conta existe justamente para
    o Google nao correlacionar as contas entre si.
    """
