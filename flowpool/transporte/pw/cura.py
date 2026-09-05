"""Auto-cura de seletores: quando um seletor do Flow quebra, o app pergunta.

O PROBLEMA. A interface do Google muda. Num unico dia de trabalho apareceram Flow
Music, Flow TV, Gemini Omni Flash e elenco de personagens. Quando um seletor quebra
hoje, o job falha, gasta as tres tentativas e para — e o conserto e alguem lendo um
print de madrugada.

O QUE A ARQUITETURA JA DAVA. Todo clique passa por `_clicar_unico`, e todo seletor
mora num arquivo so. Sao os dois ganchos de que a cura precisa, e eles ja existiam
por outro motivo.

O CICLO, com 0 candidatos:
  1. captura o contexto — print da tela (`print_de_falha`) e um resumo do DOM
     (`dom.CANDIDATOS_CLICAVEIS`, que exclui <script>: regra 2)
  2. pergunta a um modelo de VISAO qual elemento corresponde a descricao do seletor
  3. VALIDA: a proposta tem que casar com exatamente 1 elemento no mesmo escopo
  4. grava em `curados.json`, separado de `seletores.py`
  5. repete a acao com o seletor curado

DUAS COISAS QUE NAO SAO NEGOCIAVEIS, e que moram em `seletores.py` porque sao fato da
interface e nao detalhe deste modulo:

  * `seletores.NAO_CURAVEIS` — item de menu do card, excluir, lixeira. Falhar ali e o
    comportamento CERTO. "Casou 1 elemento" nao protege de nada quando o elemento
    errado tambem casa 1, e o errado, nesse menu, e "Mover para a lixeira".
  * `seletores.DESCRICAO` — sem descricao nao ha o que perguntar, e o seletor
    simplesmente nao e curavel.

E o teto de curas por execucao (padrao 3): interface que quebrou em dez lugares nao e
seletor obsoleto, e mudanca grande — e ai a pessoa precisa olhar.

O CLIENTE E TROCAVEL, e o padrao e LOCAL. `Curador` e um Protocol de um metodo so;
`CuradorLocal` fala com o llama.cpp da propria maquina (padrao) e `CuradorAnthropic`
existe para quem tiver credencial. Sem nenhum dos dois respondendo, a cura se desliga
sozinha e o comportamento e o de hoje: erro claro com print. Nada de estourar por
falta de modelo.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from flowpool.transporte.pw import dom
from flowpool.transporte.pw import seletores as sel

# --- configuracao -------------------------------------------------------
# Modelo e URL saem do ambiente: a maquina tem outros modelos instalados e trocar nao
# pode exigir editar codigo.
# Dialeto OpenAI, com o `/v1`. Aponta para o llama-swap, que proxia o `llama-server`
# do llama.cpp e sobe o modelo sob demanda (o Ollama foi desinstalado em 2026-08-25).
PADRAO_URL = "http://127.0.0.1:9090/v1"
# PRECISA TER VISAO. No llama.cpp isso exige subir o modelo com `--mmproj` (a torre de
# visao vem em GGUF separado): sem ele o servidor sobe normal e IGNORA a imagem em
# silencio — a cura continua respondendo, so que adivinhando pelo DOM.
# A entrada `qwen3.8:27b` do llama-swap ja carrega o mmproj; a `-leve` NAO.
PADRAO_MODELO = "qwen3.8:27b"
MODELO_ANTHROPIC = "claude-opus-5"

# GENEROSO DE PROPOSITO. O modelo tem 20 GB e a placa tem 24, e o ComfyUI da mesma
# maquina disputa essa memoria: carregar o Qwen pode custar meio minuto so de troca de
# residente. A cura e rara e nunca urgente — esperar cinco minutos e barato perto de
# perder o job. E estourar o prazo significa "nao curei", nunca "quebrou".
TIMEOUT_S = 300.0

TETO_POR_EXECUCAO = 3
LIMITE_CANDIDATOS = 120        # elementos no resumo do DOM
MAX_ALTERNATIVAS = 3

# `FLOWPOOL_CURA` com qualquer destes desliga a cura por completo.
DESLIGADO = {"0", "nao", "não", "off", "false", "desligado"}

# O modelo local erra mais que um de fronteira, e a resposta certa a isso NAO e confiar
# menos na validacao — e pedir mais tentativas. Ele propoe ate tres seletores, a
# validacao experimenta em ordem e fica com o primeiro que casar sozinho. Barato, e
# cobre exatamente o modo de erro de um modelo menor: acertar o elemento e errar a
# forma de escreve-lo.
ESQUEMA = {
    "type": "object",
    "properties": {
        "seletores": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_ALTERNATIVAS,
        },
        "porque": {"type": "string"},
    },
    "required": ["seletores", "porque"],
}


@dataclass
class Proposta:
    """O que o modelo devolveu: seletores candidatos, do mais provavel ao menos."""
    seletores: list[str] = field(default_factory=list)
    porque: str = ""


class Curador(Protocol):
    """Um metodo so. O falso dos testes satisfaz isto tanto quanto o Ollama."""

    async def propor(self, descricao: str, imagem: bytes | None,
                     resumo: str) -> Proposta | None:
        ...


# --- a pergunta (pura, e por isso testavel) -----------------------------

INSTRUCOES = """Voce esta olhando a interface do Google Flow (labs.google/fx, em portugues).
Um seletor do Playwright parou de casar com a tela e preciso do seletor novo.

REGRAS DESTA INTERFACE, medidas ao vivo — respeite-as:
- NUNCA use classe CSS. As classes sao styled-components com hash e mudam a cada
  deploy do Google.
- Os icones sao ligaduras de fonte e aparecem no innerText do proprio botao
  (add_2, videocam, crop_9_16, arrow_forward, redo). Casar pela ligadura e a forma
  MAIS estavel: `button:has-text("add_2")`.
- Depois da ligadura, prefira `role`, `aria-label` e atributos `data-*`.
- O rotulo visivel e traduzido e acentuado; se usar texto, use o texto exato da tela.
- O seletor tem que identificar UM UNICO elemento. Se ele casar dois, sera recusado.

Responda com ate tres seletores, do mais provavel para o menos, e uma frase curta
dizendo por que voce acha que e esse elemento."""


def pergunta(descricao: str, resumo: str) -> str:
    """O texto que vai para o modelo. Os dois clientes montam a partir daqui."""
    return (f"{INSTRUCOES}\n\n"
            f"{descricao}\n\n"
            f"Elementos clicaveis visiveis na tela agora:\n{resumo}")


def proposta_do_json(bruto: str) -> Proposta | None:
    """Le a resposta estruturada. Tolera cerca de prosa, recusa o resto.

    O `format` do Ollama e o `output_config` da Anthropic ja obrigam JSON — mas modelo
    local as vezes embrulha em crase, e uma cura perdida por causa de tres caracteres
    seria irritante sem ser instrutiva. Tolerar a cerca nao afrouxa nada: o que decide
    e a validacao contra a pagina, nao o formato da resposta.
    """
    texto = (bruto or "").strip()
    if texto.startswith("```"):
        texto = texto.split("```")[1] if "```" in texto[3:] else texto[3:]
        texto = texto.split("\n", 1)[-1] if texto[:4].lower().startswith("json") else texto
    try:
        dados = json.loads(texto)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    seletores = dados.get("seletores") or dados.get("seletor") or []
    if isinstance(seletores, str):
        seletores = [seletores]
    limpos = [s.strip() for s in seletores if isinstance(s, str) and s.strip()]
    if not limpos:
        return None
    return Proposta(seletores=limpos[:MAX_ALTERNATIVAS],
                    porque=str(dados.get("porque") or "")[:500])


# --- clientes -----------------------------------------------------------


class CuradorLocal:
    """O padrao: um modelo de visao rodando na propria maquina, de graca.

    Fala o dialeto **OpenAI** (`POST /v1/chat/completions`), que e o que o `llama-server`
    do llama.cpp expoe. A imagem vai como `image_url` com data-URI (o Ollama nativo
    queria `images` em base64 puro; nao e o caso aqui). O JSON Schema vai em
    `response_format`, o que dispensa extrair seletor de prosa. A resposta vem em
    `choices[0].message.content` como texto JSON.

    Como o dialeto e o padrao da industria, dá para apontar `FLOWPOOL_CURA_URL` /
    `FLOWPOOL_CURA_MODELO` / `FLOWPOOL_CURA_CHAVE` para um provedor de fronteira sem
    tocar em codigo.
    """

    def __init__(self, url: str | None = None, modelo: str | None = None,
                 chave: str | None = None, timeout_s: float = TIMEOUT_S) -> None:
        # FLOWPOOL_OLLAMA_URL segue aceito para nao quebrar ambiente antigo.
        self.url = (url or os.environ.get("FLOWPOOL_CURA_URL")
                    or os.environ.get("FLOWPOOL_OLLAMA_URL") or PADRAO_URL).rstrip("/")
        self.modelo = modelo or os.environ.get("FLOWPOOL_CURA_MODELO") or PADRAO_MODELO
        self.chave = chave or os.environ.get("FLOWPOOL_CURA_CHAVE") or ""
        self.timeout_s = timeout_s

    async def propor(self, descricao, imagem, resumo):
        conteudo: list[dict] = [{"type": "text", "text": pergunta(descricao, resumo)}]
        if imagem:
            # ver a tela vale mais que ler o DOM: foi olhando o print que dois
            # diagnosticos desta interface se resolveram em segundos
            b64 = base64.b64encode(imagem).decode("ascii")
            conteudo.append({"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}"}})
        corpo = {
            "model": self.modelo,
            "stream": False,
            "temperature": 0,                # nao e criatividade, e identificacao
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "proposta", "schema": ESQUEMA},
            },
            "messages": [{"role": "user", "content": conteudo}],
        }
        cabecalhos = {"Authorization": f"Bearer {self.chave}"} if self.chave else {}
        async with httpx.AsyncClient(timeout=self.timeout_s) as cliente:
            resposta = await cliente.post(f"{self.url}/chat/completions",
                                          json=corpo, headers=cabecalhos)
            resposta.raise_for_status()
            dados = resposta.json()
        escolhas = dados.get("choices") or [{}]
        return proposta_do_json((escolhas[0].get("message") or {}).get("content", ""))


# Nome antigo mantido para nao quebrar import de fora.
CuradorOllama = CuradorLocal


class CuradorAnthropic:
    """A alternativa, so para quem tiver credencial.

    O SDK NAO esta no `pyproject.toml` de proposito: o caminho principal e local e nao
    custa nada, e uma dependencia que a maquina padrao nao usa e peso morto. Quem
    quiser este caminho instala `anthropic` e resolve a credencial como o SDK espera —
    `ANTHROPIC_API_KEY` ou um perfil de `ant auth login`. `Anthropic()` sem argumento
    ja faz essa resolucao sozinho; nao ha leitura de arquivo de config aqui.
    """

    def __init__(self, modelo: str = MODELO_ANTHROPIC) -> None:
        self.modelo = modelo

    async def propor(self, descricao, imagem, resumo):
        import anthropic       # opcional: so quem escolhe este caminho precisa dele

        conteudo: list[dict] = []
        if imagem:
            conteudo.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(imagem).decode("ascii")}})
        conteudo.append({"type": "text", "text": pergunta(descricao, resumo)})

        cliente = anthropic.AsyncAnthropic()
        resposta = await cliente.messages.create(
            model=self.modelo,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"format": {
                "type": "json_schema",
                "schema": dict(ESQUEMA, additionalProperties=False),
            }},
            messages=[{"role": "user", "content": conteudo}],
        )
        texto = next((b.text for b in resposta.content if b.type == "text"), "")
        return proposta_do_json(texto)


def curador_padrao() -> Curador | None:
    """Quem responde, ou None quando a cura esta desligada.

    Nao mede se o Ollama esta de pe: descobrir isso custa uma requisicao a mais e o
    caminho de falha ja e o mesmo — conexao recusada vira "nao curei".
    """
    if (os.environ.get("FLOWPOOL_CURA") or "").strip().lower() in DESLIGADO:
        return None
    escolha = (os.environ.get("FLOWPOOL_CURA_CLIENTE") or "ollama").strip().lower()
    if escolha == "anthropic":
        return CuradorAnthropic()
    return CuradorLocal()


# --- do seletor quebrado para o nome dele -------------------------------


def catalogo() -> dict[str, str | None]:
    """Valor do seletor -> nome. `None` quando dois nomes tem o mesmo valor.

    Ambiguidade recusa, como em todo o resto deste transporte: se dois nomes casam o
    mesmo texto, nao da para saber O QUE se procurava, e perguntar a coisa errada e
    pior do que nao perguntar.
    """
    indice: dict[str, str | None] = {}

    def anotar(valor, nome):
        if not isinstance(valor, str) or not valor:
            return
        indice[valor] = None if valor in indice and indice[valor] != nome else nome

    for nome, valor in vars(sel).items():
        if not nome.isupper() or nome.startswith("_"):
            continue
        if isinstance(valor, str):
            anotar(valor, nome)
        elif isinstance(valor, dict):
            for chave, interno in valor.items():
                anotar(interno, f"{nome}[{chave}]")
    return indice


def nome_do_seletor(seletor: str) -> str | None:
    """O nome em `seletores.py` de um seletor, ou None se ele nao mora la.

    Seletor montado na hora (`a[href*="<uuid>"]`) nao tem nome, e por isso nao tem
    descricao, e por isso nao e curavel. E o resultado certo: o que quebrou ali nao foi
    o mapa, foi o dado.
    """
    return catalogo().get(seletor)


def valor_do_nome(nome: str) -> str | None:
    """O caminho inverso, para conferir que uma DESCRICAO aponta para algo real."""
    casado = sel._ALVO.match(nome)
    if not casado:
        return None
    base, chave = casado.group(1), casado.group(2)
    alvo = getattr(sel, base, None)
    if chave is None:
        return alvo if isinstance(alvo, str) else None
    if isinstance(alvo, dict):
        real = sel._chave_de(alvo, chave)
        valor = alvo.get(real) if real is not None else None
        return valor if isinstance(valor, str) else None
    return None


def _base_do_nome(nome: str) -> str:
    return nome.split("[", 1)[0]


# --- a cura -------------------------------------------------------------


class AutoCura:
    """A politica. O cliente de IA e trocavel; isto aqui nao muda com ele."""

    def __init__(self, curador: Curador | None = None,
                 teto: int = TETO_POR_EXECUCAO,
                 arquivo: Path | str | None = None,
                 registrar=None) -> None:
        # curador EXPLICITO ignora a variavel de ambiente: e assim que a suite testa o
        # comportamento inteiro com um dublê, sem tocar em rede
        self._curador = curador
        self._resolvido = curador is not None
        self.teto = teto
        self.curas = 0
        self.arquivo = Path(arquivo) if arquivo else None
        self.registrar = registrar

    # -- o arquivo de curados
    @property
    def alvo(self) -> Path:
        """Resolvido tarde: `sel.CURADOS` pode ser trocado (teste, outra pasta)."""
        return self.arquivo or sel.CURADOS

    def _curador_agora(self) -> Curador | None:
        if not self._resolvido:
            self._curador = curador_padrao()
            self._resolvido = True
        return self._curador

    async def curar(self, pagina, escopo, seletor: str, o_que: str,
                    print_da_tela=None) -> str | None:
        """Devolve o seletor curado, ou None. NUNCA levanta.

        None significa "segue o erro original", e essa e a unica degradacao aceitavel:
        a cura e um extra: quando ela nao consegue, o app volta a ser o que era antes
        dela existir — falha clara com print.
        """
        try:
            return await self._curar(pagina, escopo, seletor, o_que, print_da_tela)
        except Exception as e:
            # inclusive erro de programacao daqui de dentro: a cura nao pode virar um
            # segundo modo de falha por cima do primeiro
            print(f"cura: falhou por dentro ({type(e).__name__}: {e}) - "
                  f"segue o erro original")
            return None

    async def _curar(self, pagina, escopo, seletor, o_que, print_da_tela):
        nome = nome_do_seletor(seletor)
        if nome is None:
            print(f"cura: {o_que}: {seletor!r} nao esta em seletores.py "
                  f"(montado na hora?) - nao e curavel")
            return None

        # A REGRA DE SEGURANCA, e ela vem ANTES de qualquer outra coisa: nem o print e
        # tirado, nem o modelo e consultado. Ver `seletores.NAO_CURAVEIS`.
        if nome in sel.NAO_CURAVEIS or _base_do_nome(nome) in sel.NAO_CURAVEIS:
            print(f"cura: {nome} NAO e curavel — um clique errado aqui apaga trabalho "
                  f"do usuario. Falhar e o comportamento certo; veja o print e decida.")
            return None

        descricao = sel.DESCRICAO.get(nome)
        if not descricao:
            print(f"cura: {nome} nao tem descricao em seletores.DESCRICAO - "
                  f"sem saber o que se procurava, nao da para perguntar")
            return None

        if self.curas >= self.teto:
            print(f"cura: teto de {self.teto} curas nesta execucao ja foi atingido. "
                  f"Interface quebrada em varios lugares nao e seletor obsoleto - "
                  f"e mudanca grande, e ai alguem precisa olhar.")
            return None

        curador = self._curador_agora()
        if curador is None:
            print(f"cura: desligada (FLOWPOOL_CURA) - segue o erro original")
            return None

        imagem = await self._imagem(pagina, nome, print_da_tela)
        resumo = await self._resumo(pagina)
        pedido = (f"O seletor {seletor!r} deveria achar: {descricao}.\n"
                  f"Ele nao casa com nenhum elemento da tela agora "
                  f"(o app tentou clicar em: {o_que}).")
        try:
            proposta = await curador.propor(pedido, imagem, resumo)
        except Exception as e:
            # Ollama fora do ar, modelo que nao coube na GPU, prazo estourado: tudo
            # isso e "nao curei", nunca "quebrou"
            print(f"cura: {nome}: o modelo nao respondeu "
                  f"({type(e).__name__}: {e}) - segue o erro original")
            return None
        if not proposta or not proposta.seletores:
            print(f"cura: {nome}: o modelo nao propos nada utilizavel")
            return None

        for candidato in proposta.seletores[:MAX_ALTERNATIVAS]:
            if not self._aceitavel(nome, candidato, seletor):
                continue
            quantos = await self._contar(escopo, candidato)
            if quantos == 1:
                self._gravar(nome, candidato, seletor, proposta.porque)
                return candidato
            print(f"cura: {nome}: {candidato!r} casa {quantos} elementos - recusado "
                  f"(so 1 vale)")
        return None

    # -- contexto
    async def _imagem(self, pagina, nome: str, print_da_tela) -> bytes | None:
        """O print. Sem ele a cura continua, so que cega — e pior, nao impossivel."""
        if print_da_tela is None:
            return None
        try:
            caminho = await print_da_tela(pagina, f"cura {nome}")
            return Path(caminho).read_bytes() if caminho else None
        except Exception:
            return None

    async def _resumo(self, pagina) -> str:
        """O DOM, pelo `dom.py` — nunca varrendo <script> (regra 2).

        `sem_segredo` e nao `seguro`: o destino aqui e o modelo, e tirar acento faria
        ele propor `has-text("Video")` para uma tela que diz "Vídeo", que a validacao
        recusaria. Mascarar token continua valendo, e com mais razao.
        """
        try:
            linhas = await pagina.evaluate(dom.CANDIDATOS_CLICAVEIS, LIMITE_CANDIDATOS)
        except Exception:
            return "(nao consegui ler o DOM)"
        return "\n".join(dom.sem_segredo(str(linha)) for linha in linhas)

    # -- validacao
    def _aceitavel(self, nome: str, candidato: str, antigo: str) -> bool:
        if not candidato or candidato == antigo or len(candidato) > 300:
            return False
        if ".sc-" in candidato:
            # regra 1: styled-component com hash. Casa hoje e morre no proximo deploy —
            # e uma cura que expira sozinha e pior que nenhuma, porque some do radar
            print(f"cura: {nome}: {candidato!r} usa classe com hash - recusado")
            return False
        return True

    async def _contar(self, escopo, candidato: str) -> int:
        """Quantos elementos o candidato casa NO MESMO ESCOPO em que se ia clicar.

        No escopo, e nao na pagina: o painel do chip existe justamente para separar o
        `crop_9_16` do botao do proprio chip, que tambem traz a ligadura no texto.
        Validar na pagina inteira aprovaria um seletor que, na hora do clique, casaria
        dois.
        """
        try:
            loc = escopo.locator(candidato)
            try:
                await loc.first.wait_for(timeout=5000)
            except Exception:
                pass
            return await loc.count()
        except Exception as e:
            print(f"cura: {candidato!r} nao e um seletor valido ({type(e).__name__})")
            return -1

    # -- gravacao
    def _gravar(self, nome: str, novo: str, antigo: str, porque: str) -> None:
        entrada = {
            "valor": novo,
            "antigo": antigo,
            "em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "porque": porque,
        }
        dados = sel._ler_curados() if self.alvo == sel.CURADOS else self._ler_alvo()
        dados[nome] = entrada
        self.alvo.parent.mkdir(parents=True, exist_ok=True)
        self.alvo.write_text(json.dumps(dados, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        # passa os dados na mao: em teste o arquivo nao e `sel.CURADOS`, e reler o
        # caminho padrao desfaria justamente o que se acabou de gravar
        sel.recarregar_curados(dados)
        self.curas += 1
        # a justificativa vem do modelo, acentuada, e o console do Windows e cp1252:
        # `dom.seguro` e o que este projeto imprime. No JSON ela fica inteira
        print(f"cura: {nome}: {antigo!r} -> {novo!r} ({dom.seguro(porque)}). "
              f"Gravado em {self.alvo.name} - apague a entrada para reverter.")
        if self.registrar:
            try:
                self.registrar("seletor_curado", entrada | {"nome": nome})
            except Exception as e:
                print(f"cura: nao consegui registrar o evento ({type(e).__name__})")

    def _ler_alvo(self) -> dict:
        try:
            dados = json.loads(self.alvo.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return dados if isinstance(dados, dict) else {}


def ligar_no_banco(auto: AutoCura, repo) -> None:
    """Cada cura vira um evento `seletor_curado`.

    E a diferenca entre "o app se adaptou" e "o app mudou sozinho e ninguem soube": o
    JSON diz o estado atual, o evento diz QUANDO aconteceu e fica na mesma linha do
    tempo dos outros eventos do job. Falha ao gravar nao derruba a cura — o seletor
    curado ja vale, e perder a anotacao e menos grave do que perder o job.
    """
    def registrar(tipo: str, payload: dict) -> None:
        repo.registrar_evento(None, None, tipo,
                              json.dumps(payload, ensure_ascii=False))

    auto.registrar = registrar
