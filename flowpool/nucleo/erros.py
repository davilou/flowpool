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


class LimiteDeUso(Exception):
    """O Flow recusou por LIMITE DE USO da conta. Nao e falta de credito.

    ⚠️ NAO CONFUNDIR COM `SaldoInsuficiente`, e a diferenca importa na tela.
    Geracao de IMAGEM custa zero: a conta pode estar cheia de credito e mesmo assim
    bater no limite. Reportar isso como saldo faria o painel acusar problema de
    credito numa conta com credito na mao, e mandaria o dono investigar a coisa
    errada.

    O card diz as duas coisas: "Voce chegou ao limite de uso" e "Nao houve cobranca
    por esta geracao".

    A ACAO e a mesma de `SaldoInsuficiente` — por prazo a conta para de ser
    escolhida, o job volta para a fila sem gastar tentativa, outra conta pega. O que
    muda e o nome, a mensagem e o evento; o saldo local continua CONFIAVEL, porque
    nada foi cobrado.

    ⚠️ `motivo` E CAMPO, E NAO SO TEXTO NA MENSAGEM, e a razao e uma pergunta que o
    historico nao soube responder. MEDIDO 2026-09-10: o evento `conta_no_limite` de
    todas as recusas guardava so `{"id_remoto": ..., "tentativa": ...}`, e "isso foi
    cota da conta ou foi o antiabuso?" — a pergunta que decide entre esperar o reset e
    mexer no navegador — so dava para responder relendo o log de passo do terminal, se
    ele ainda existisse. As duas recusas continuam pedindo a MESMA acao (trocar de
    conta), que e por que sao um erro so; o que se separa aqui e o diagnostico.
    """

    MOTIVO_DESCONHECIDO = "desconhecido"
    # Os dois motivos MEDIDOS. Sao constantes, e nao literais no transporte, porque
    # o motor DECIDE por eles desde 2026-09-16: antiabuso manda a conta sair por outro
    # IP (fuga), cota manda esperar o reset. Um literal digitado diferente nos dois
    # lados faria a fuga nunca acontecer, sem erro nenhum.
    MOTIVO_ANTIABUSO = "atividade_incomum"
    MOTIVO_COTA = "limite_de_uso"

    def __init__(self, mensagem: str, motivo: str = MOTIVO_DESCONHECIDO) -> None:
        super().__init__(mensagem)
        self.motivo = motivo
