"""Colagem de imagem: bytes que ainda nao existem em disco viram arquivo.

O seletor (`arquivos.py`) resolve o caso em que a imagem JA esta no disco e o painel so
precisa APONTAR para ela. Aqui e o caso oposto e por isso um modulo separado: a imagem
veio da area de transferencia — um "copiar imagem" em qualquer site, um print de tela —
e nao existe em lugar nenhum. O transporte entrega a referencia ao Flow com
`set_input_files`, que abre um arquivo EM DISCO, entao a unica saida e GRAVAR.

Esta e a primeira coisa do projeto que ESCREVE arquivo a pedido do navegador, e por isso
tres regras nao sao opcionais:

1. O nome sai do CONTEUDO, nunca do cliente. Nome vindo do navegador (`../..`, `C:\\...`,
   um `.py` dentro do pacote) e o caminho classico para gravar fora do lugar; hash nao
   tem como sair da pasta. De brinde vem o dedupe: colar duas vezes a mesma imagem
   reaproveita o arquivo em vez de encher a pasta de copias.
2. O tipo sai dos BYTES, nunca da extensao nem do `Content-Type` declarado — os dois sao
   texto que o cliente escreve.
3. Ha teto de tamanho, e ele e conferido enquanto se le, nao depois.

E `/colar-url` SAI PARA A INTERNET a partir de um servico local sem autenticacao. Sem
`validar_url` o painel viraria um jeito de alcancar, de dentro desta maquina, o que so
esta maquina alcanca: o roteador, o painel do NAS, um servico em `127.0.0.1` que nao
espera visita. E a checagem tem que ser pelo IP RESOLVIDO — um nome que aponta para
`127.0.0.1` e um nome comum, com DNS comum, que passaria por qualquer teste de texto.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# Fora de `flowpool/` (que e o pacote) e fora das pastas de produto (que sao as fotos da
# pessoa). Imagem de terceiro, baixada de qualquer lugar, nao se mistura com o acervo
# nem entra em git — a pasta esta no `.gitignore`.
PASTA_COLADAS = "_coladas"

# 20 MB. Nao e limite de disco: e o tamanho que o servico aceita segurar em memoria de
# uma vez, e um print de tela ou uma foto de site nao chegam perto disso.
TETO_BYTES = 20 * 1024 * 1024

# Teto de tempo da busca por URL. O site do outro lado pode estar lento ou nem responder,
# e um `fetch` sem prazo deixaria a tira "carregando" para sempre.
TETO_SEGUNDOS = 15.0

# Redirecionamento e comum em CDN de imagem, mas cada salto e um endereco NOVO — e um
# endereco novo precisa passar por `validar_url` de novo. O teto evita o pingue-pongue.
MAX_SALTOS = 3

# Assinaturas de arquivo. Um `Content-Type: image/png` num .exe e so uma string; estes
# bytes sao o que o decodificador de imagem realmente vai encontrar.
_ASSINATURAS = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


class ConteudoRecusado(ValueError):
    """O que chegou nao e uma imagem que este projeto aceita."""


class ConteudoGrande(ValueError):
    """Passou do teto de tamanho."""


class UrlRecusada(ValueError):
    """A URL nao pode ser buscada — esquema errado, ou aponta para a rede local."""


class BuscaFalhou(RuntimeError):
    """A URL era aceitavel, mas o outro lado nao entregou a imagem."""


def extensao_da_imagem(dados: bytes) -> str | None:
    """Extensao pela assinatura REAL do conteudo, ou `None` se nao for imagem."""
    for assinatura, ext in _ASSINATURAS:
        if dados.startswith(assinatura):
            return ext
    # WEBP e um container RIFF: os quatro primeiros bytes dizem so "RIFF" e sao os
    # mesmos de um .wav. O que separa os dois e o rotulo em 8..12.
    if len(dados) >= 12 and dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return ".webp"
    return None


def guardar(raiz: Path | str, dados: bytes) -> dict:
    """Grava os bytes em `_coladas/` e devolve nome, caminho relativo e tamanho.

    O caminho volta no MESMO formato que o resto do painel usa (relativo a raiz, barra
    normal), para o campo de referencia aceita-lo sem traducao nenhuma no meio.

    Levanta `ConteudoRecusado` se os bytes nao forem imagem e `ConteudoGrande` se
    passarem do teto.
    """
    if not dados:
        raise ConteudoRecusado("nao veio imagem nenhuma")
    if len(dados) > TETO_BYTES:
        raise ConteudoGrande(
            f"a imagem tem {len(dados) // 1048576} MB e o teto e "
            f"{TETO_BYTES // 1048576} MB")
    ext = extensao_da_imagem(dados)
    if ext is None:
        raise ConteudoRecusado(
            "o que foi colado nao e uma imagem PNG, JPEG, WEBP ou GIF")

    pasta = Path(raiz).resolve() / PASTA_COLADAS
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"{hashlib.sha256(dados).hexdigest()[:16]}{ext}"
    alvo = pasta / nome
    if not alvo.exists():
        # grava num temporario e renomeia: o nome e o hash, entao a proxima colagem
        # CONFIA no arquivo que ja esta la sem reler os bytes. Um arquivo truncado por
        # uma gravacao interrompida herdaria essa confianca para sempre.
        temp = pasta / f".{nome}.parcial"
        temp.write_bytes(dados)
        temp.replace(alvo)
    return {"nome": nome, "caminho": f"{PASTA_COLADAS}/{nome}", "tamanho": len(dados)}


def _resolver_padrao(host: str) -> list[str]:
    return [info[4][0]
            for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def validar_url(url: str, resolver: Callable[[str], list[str]] | None = None) -> str:
    """Aceita a URL para busca, ou levanta `UrlRecusada`.

    Funcao pura de proposito, com teste proprio: e a UNICA coisa entre uma caixa de
    texto do painel e um `GET` saindo desta maquina. Regra de seguranca escondida dentro
    de uma rota vira, com o tempo, regra que ninguem sabe que existe.

    Recusa o que nao for `http`/`https`, e recusa endereco que nao seja publico —
    `localhost`, `127/8`, `::1`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16` e
    companhia. A conferencia e no IP RESOLVIDO, e nao no texto do host: um nome de
    dominio comum pode apontar para `127.0.0.1`, e ai o teste por texto nao ve nada.

    `resolver` entra como parametro para o teste nao depender de DNS de verdade; quando
    nao vem, o padrao e procurado no MODULO a cada chamada, e nao amarrado no `def` —
    e o que deixa a busca por URL ser testada sem rede tambem.

    O que ela NAO cobre: reamarracao de DNS — entre esta resolucao e a do httpx o nome
    pode passar a responder outro IP. Fechar isso exigiria conectar no IP ja conferido
    mandando o `Host` a mao, e para um servico de uma pessoa so, na maquina dela, o
    preco em complexidade nao paga.
    """
    partes = urlsplit((url or "").strip())
    if partes.scheme not in {"http", "https"}:
        raise UrlRecusada(
            f"so http e https; {partes.scheme or 'sem esquema'} nao serve aqui")
    host = partes.hostname
    if not host:
        raise UrlRecusada("a URL nao tem endereco de host")

    baixo = host.rstrip(".").lower()
    # `localhost` antes do DNS: e o nome que a maquina resolve por conta propria e o
    # unico que precisa ser recusado mesmo sem resolvedor nenhum a mao.
    if baixo == "localhost" or baixo.endswith(".localhost"):
        raise UrlRecusada("localhost e esta propria maquina; nao da para buscar dai")

    try:
        # host que JA e um endereco nao passa por resolvedor nenhum: resolver um
        # literal e um nao-fazer-nada que so daria a um resolvedor trocado a chance de
        # dizer que `127.0.0.1` fica em outro lugar
        enderecos = [str(ipaddress.ip_address(baixo))]
    except ValueError:
        try:
            enderecos = (resolver or _resolver_padrao)(host)
        except OSError as e:
            raise UrlRecusada(f"nao deu para resolver o nome {host}") from e
    if not enderecos:
        raise UrlRecusada(f"o nome {host} nao resolveu para endereco nenhum")

    for bruto in enderecos:
        try:
            ip = ipaddress.ip_address(bruto.split("%")[0])
        except ValueError as e:
            raise UrlRecusada(f"{host} resolveu para algo que nao e endereco") from e
        # `::ffff:127.0.0.1` e loopback escrito em IPv6; sem desembrulhar, `is_loopback`
        # daria False e o endereco passaria.
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise UrlRecusada(
                f"{host} aponta para {ip}, que e endereco de rede local ou reservado; "
                "esta rota so busca imagem na internet publica")
    return url


async def buscar(url: str, transporte: httpx.AsyncBaseTransport | None = None) -> bytes:
    """Busca a imagem da URL, com teto de tamanho e de tempo, e devolve os bytes.

    Redirecionamento NAO e seguido pelo httpx de proposito. `validar_url` olhou o
    endereco que a pessoa colou; um `302` para `http://127.0.0.1:8777/` passaria por
    baixo dela sem nenhum aviso. Cada salto e revalidado a mao, aqui.

    `transporte` existe para o teste montar as respostas sem rede.
    """
    atual = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=TETO_SEGUNDOS,
                                 transport=transporte) as cliente:
        for _ in range(MAX_SALTOS + 1):
            validar_url(atual)
            try:
                async with cliente.stream("GET", atual) as r:
                    if r.is_redirect:
                        destino = r.headers.get("location")
                        if not destino:
                            raise BuscaFalhou("o site redirecionou para lugar nenhum")
                        atual = str(httpx.URL(atual).join(destino))
                        continue
                    if r.status_code >= 400:
                        raise BuscaFalhou(f"o site respondeu {r.status_code}")
                    declarado = r.headers.get("content-length")
                    if declarado and declarado.isdigit() and int(declarado) > TETO_BYTES:
                        raise ConteudoGrande(
                            f"a imagem passa de {TETO_BYTES // 1048576} MB")
                    pedacos = bytearray()
                    async for pedaco in r.aiter_bytes():
                        pedacos += pedaco
                        # o teto e conferido ENQUANTO se le: confiar no
                        # `Content-Length` deixaria um site mentiroso encher a memoria
                        if len(pedacos) > TETO_BYTES:
                            raise ConteudoGrande(
                                f"a imagem passa de {TETO_BYTES // 1048576} MB")
                    return bytes(pedacos)
            except httpx.HTTPError as e:
                raise BuscaFalhou(f"nao deu para buscar a imagem: {e}") from e
    raise BuscaFalhou(f"o site redirecionou mais de {MAX_SALTOS} vezes")
