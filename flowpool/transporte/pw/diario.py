"""O que o transporte conta enquanto trabalha: log de passo e traco do Playwright.

O LOG DE PASSO NAO E CONVENIENCIA: ele conserta uma causa medida.

Em 21/08 varios jobs de duas referencias morreram com

    TargetClosedError: Page.wait_for_timeout: Target page, context or browser
    has been closed

e tres rodadas de investigacao foram gastas atras de crash, memoria e vazamento de
navegador. NAO ERA NADA DISSO. Nas palavras do usuario: "sobre o navegador fechando,
fui eu que fechei. Por que? Porque ele estava demorando muito para enviar as duas
referencias."

Ou seja: o app trabalhava em SILENCIO por minutos, e olhar uma tela parada e concluir
"travou" e a leitura razoavel de quem esta do lado de fora. O silencio prolongado era
o defeito, e a correcao dele e uma linha de vida a cada poucos segundos:

    [ 21s] conta 6: anexando referencia 2 de 2: blusa.png
    [ 51s] conta 6: ainda esperando a miniatura de blusa.png

Com isso o usuario sabe que ha trabalho acontecendo e nao mata o job. E por isso que
`esperando` avisa DURANTE a espera, e nao so nas transicoes: quem so registra
transicao fica calado exatamente no trecho que assusta.

O log tambem e a MEDICAO que hoje falta. A lentidao com duas referencias e real e
irregular — "as vezes ele demora, as vezes ele consegue enviar rapidamente" —, e
ninguem sabe por que. Cada inclusao sai com o tempo que levou; com dezenas de jobs por
dia, em pouco tempo havera distribuicao real em vez de impressao. Uma hipotese que o
log pode fechar sozinho, sem que ninguem corrija as cegas: os arquivos vem de
`_coladas/` com nome por hash, entao repetir a mesma imagem entre jobs e comum, e o
Flow pode tratar reenvio de arquivo JA NO ACERVO de forma diferente do primeiro envio.

A segunda ferramenta e o TRACO DO PLAYWRIGHT — print antes e depois de cada acao, o
DOM daquele instante, a chamada feita e quanto ela levou. Abre com

    python -m playwright show-trace scratch/tracos/<arquivo>.zip

Ele responde o que o log nao responde: o que a TELA mostrava quando a sequencia errou.

REGRAS QUE ESTE MODULO EXISTE PARA CUMPRIR:

1. NADA AQUI PODE DERRUBAR UM JOB. Diagnostico que quebra a coisa que ele diagnostica
   e pior do que nenhum. Mesma regra do `print_de_falha`: toda funcao de traco engole
   a propria falha e segue sem traco. E por isso que elas devolvem bool/None em vez de
   levantar.
2. SO SE GUARDA TRACO DE FALHA. Cada traco pesa alguns MB e o usuario roda dezenas de
   jobs por dia; guardar tudo enche o disco em uma semana. Em sucesso, `stop()` sem
   `path` descarta o que foi gravado.
3. NOME QUE NAO SOBRESCREVE, E SEM RELOGIO. `proximo_traco` acha o proximo numero
   livre olhando a pasta. Nao usa `datetime.now()` — relogio dentro do transporte
   torna o teste dependente da hora, e o que importa aqui e so nao perder o traco
   anterior.
4. DA PARA DESLIGAR. `FLOWPOOL_TRACO=0` no ambiente. A gravacao tem custo por acao, e
   quem estiver medindo tempo precisa poder tirar o instrumento do caminho.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# `scratch/` ja e ignorado pelo git.
RAIZ_TRACOS = Path("scratch/tracos")

# De quanto em quanto tempo uma espera longa volta a dizer que continua esperando.
# Nao e por volta de laco: `_esperar_item_pronto` gira a cada 500 ms e encheria a tela
# com 360 linhas iguais num upload de 3 minutos.
CADA_S = 15.0


def traco_ligado() -> bool:
    """Ligado por padrao; `FLOWPOOL_TRACO=0` desliga.

    Lido a cada chamada, e nao uma vez na importacao: o servico fica de pe por dias, e
    uma constante de modulo obrigaria a reiniciar para trocar o instrumento — que e
    justamente o que nao se pode fazer com o usuario no meio de uma geracao.
    """
    return os.environ.get("FLOWPOOL_TRACO", "1") != "0"


def proximo_traco(conta_id: int, raiz: Path | str = RAIZ_TRACOS) -> Path:
    """O proximo nome livre para o traco daquela conta. Nunca sobrescreve.

    Sem relogio de proposito (ver a regra 3 no topo). O numero sai da propria pasta:
    o maior que ja existe, mais um. Pasta vazia comeca em 1.

    A conta vai no nome porque com geracoes simultaneas ha ate cinco navegadores
    trabalhando ao mesmo tempo, e um traco sem dono nao responde a primeira pergunta
    que se faz dele.
    """
    raiz = Path(raiz)
    maior = 0
    for existente in raiz.glob(f"conta{conta_id}-*.zip"):
        pedaco = existente.stem.rsplit("-", 1)[-1]
        if pedaco.isdigit():
            maior = max(maior, int(pedaco))
    return raiz / f"conta{conta_id}-{maior + 1:04d}.zip"


class Diario:
    """O log de passo de UMA sessao. Um por conta, criado ao abrir o navegador.

    Por conta, e nao por processo, porque o motor roda ate cinco geracoes simultaneas
    e as linhas se intercalam na mesma saida. Sem o numero da conta no prefixo, o log
    de cinco jobs vira um so, ilegivel — que e o problema que ele veio resolver.

    O relogio entra pela porta para o teste poder controla-lo. `time.monotonic` e nao
    `time.time` porque isto mede DURACAO: acerto de relogio do sistema no meio de um
    job faria uma etapa aparecer com tempo negativo.
    """

    def __init__(self, conta_id: int, relogio=time.monotonic) -> None:
        self.conta_id = conta_id
        self._relogio = relogio
        self._inicio = relogio()
        # ultimo aviso de cada espera longa, para nao repetir a cada volta do laco
        self._ultimo: dict[str, float] = {}

    @property
    def decorrido_s(self) -> float:
        return self._relogio() - self._inicio

    def cronometro(self):
        """Devolve uma funcao que diz quantos segundos passaram desde AGORA.

        Serve para medir uma etapa isolada — quanto levou o upload da referencia 2 —
        sem que quem chama tenha que guardar `time.monotonic()` numa variavel solta e
        subtrair a mao em cada ponto. E o que transforma o log em medicao: a lentidao
        com duas referencias e irregular e ninguem sabe por que, e uma coluna de
        tempos por upload responde isso sem investigacao nenhuma.
        """
        partida = self._relogio()
        return lambda: self._relogio() - partida

    def passo(self, texto: str) -> None:
        """Uma etapa. Nunca levanta: log que derruba job nao e log."""
        try:
            print(f"[{round(self.decorrido_s):>4}s] conta {self.conta_id}: {texto}")
        except Exception:
            pass

    def esperando(self, o_que: str, cada_s: float = CADA_S) -> None:
        """"Ainda esperando X", no maximo uma vez a cada `cada_s`.

        A primeira volta de uma espera NAO imprime — quase toda espera termina rapido,
        e uma linha "ainda esperando" no segundo zero seria ruido em todo job. So
        aparece quando a espera de fato passou de `cada_s`, que e exatamente quando ela
        comeca a interessar.
        """
        agora = self._relogio()
        desde = self._ultimo.get(o_que, self._inicio)
        if agora - desde < cada_s:
            return
        self._ultimo[o_que] = agora
        self.passo(f"ainda esperando {o_que}")


# --- o traco: tres funcoes, e nenhuma delas levanta ---------------------


async def iniciar_traco(ctx) -> bool:
    """Comeca a gravar. Devolve se pegou — quem chama so precisa saber disso.

    `screenshots` e `snapshots` sao o que torna o traco util para ESTE defeito: sem
    eles sobra a lista de chamadas, que e mais ou menos o que o log de passo ja da.
    `sources` inclui o nosso codigo, para o visualizador mostrar a linha que fez cada
    acao.
    """
    if not traco_ligado():
        return False
    try:
        await ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        return True
    except Exception as e:
        print(f"traco: nao consegui iniciar ({type(e).__name__}) - seguindo sem ele")
        return False


async def guardar_traco(ctx, caminho: Path) -> Path | None:
    """Grava o traco no disco. `None` se nao deu — e nao dar nao e erro do job.

    O caso em que isto falha e justamente o que mais interessa: com o navegador MORTO
    (o `TargetClosedError` que motivou o modulo) nao ha de onde o Playwright coletar o
    que gravou, e o traco se perde. Por isso o log de passo nao e redundante com o
    traco — ele e stdout, e sobrevive a morte do navegador.

    Chamado no momento da falha, e nao ao fechar a sessao: ali o contexto ainda tem
    chance de estar vivo.
    """
    caminho = Path(caminho)
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        await ctx.tracing.stop(path=str(caminho))
    except Exception as e:
        print(f"traco: nao consegui gravar em {caminho} ({type(e).__name__}) - "
              f"o navegador provavelmente ja tinha morrido")
        return None
    print(f"traco da falha: {caminho}\n"
          f"  abra com: python -m playwright show-trace {caminho}")
    return caminho


async def descartar_traco(ctx) -> None:
    """Para de gravar e joga fora. E o caminho do job que deu certo (regra 2)."""
    try:
        await ctx.tracing.stop()
    except Exception:
        pass
