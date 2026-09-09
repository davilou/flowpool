"""Por onde a conta sai. Puro: sem rede, sem I/O, sem navegador.

Cada conta do pool sai por um IP proprio para o Google nao correlacionar as contas
entre si. Medicao de campo (20/08): o Flow ja recusa geracao a partir do IP domestico
do Davi e uma VPN gratuita destrava — ou seja, o bloqueio e por reputacao de IP e a
barra e baixa. Nao e preciso proxy residencial premium; e preciso OUTRO IP.

Isto mora no nucleo porque e logica pura e precisa de teste sem rede. O transporte le
o resultado; a decisao inteira sobre o que e uma URL usavel acontece aqui.

Duas coisas moram aqui, e sao o mesmo assunto: traduzir a URL que a conta usa
(`analisar`, `argumento_chrome`, `mascarar`) e dizer quando duas contas acabaram no
MESMO IP (`agrupar_por_ip`, `colisoes`). Quem le o IP de verdade e `transporte/ip.py`;
daqui nao sai pacote nenhum.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from urllib.parse import SplitResult, unquote, urlsplit

from flowpool.nucleo.erros import ProxyInvalido

# O que o Chrome e o Playwright sabem falar. `https` entra porque existe proxy que
# atende em TLS; `socks5h` e o socks5 que resolve DNS do lado do proxy — sem ele, o
# DNS vazaria pelo IP de casa, que e metade do que este arquivo existe para evitar.
ESQUEMAS = ("http", "https", "socks5", "socks5h")

# O que substitui a senha na resposta da API. Nao e a senha truncada: e um carimbo.
SENHA_MASCARADA = "***"


def _partes(url: str) -> tuple[SplitResult, int]:
    """`urlsplit` com os erros dele traduzidos para `ProxyInvalido`.

    `.port` levanta `ValueError` para porta fora da faixa, e so na hora em que alguem
    le o atributo. Sem traduzir aqui, o estouro apareceria dentro do transporte, longe
    do cadastro que o causou.
    """
    if "://" not in url:
        raise ProxyInvalido(
            f"proxy {url!r} sem esquema; use http://host:porta, "
            f"http://usuario:senha@host:porta ou socks5://host:porta")
    partes = urlsplit(url)
    if partes.scheme not in ESQUEMAS:
        raise ProxyInvalido(
            f"esquema de proxy {partes.scheme!r} desconhecido; use um de {list(ESQUEMAS)}")
    if not partes.hostname:
        raise ProxyInvalido(f"proxy {url!r} sem host")
    try:
        porta = partes.port
    except ValueError as e:
        raise ProxyInvalido(f"proxy {url!r} com porta invalida: {e}") from e
    if porta is None:
        raise ProxyInvalido(
            f"proxy {url!r} sem porta; proxy sem porta explicita nao se conecta")
    return partes, porta


def analisar(url: str | None) -> dict[str, str] | None:
    """Converte a URL de proxy no formato que o Playwright espera.

    Devolve None quando nao ha proxy (o caso comum hoje).
    Levanta ProxyInvalido quando a URL existe mas nao da para usar — falhar cedo e
    melhor que abrir um navegador que vai sair pelo IP errado sem ninguem perceber.

    `username` e `password` so aparecem quando a URL os traz: o Playwright trata a
    chave presente com valor None como credencial vazia e tenta autenticar assim.
    """
    if url is None or not url.strip():
        return None
    partes, porta = _partes(url.strip())

    cfg: dict[str, str] = {"server": f"{partes.scheme}://{partes.hostname}:{porta}"}
    # a credencial so cabe na URL escapada quando tem `@` ou `:`; mandar o escape cru
    # para o Playwright autenticaria com a senha errada e o proxy recusaria calado
    if partes.username:
        cfg["username"] = unquote(partes.username)
    if partes.password:
        cfg["password"] = unquote(partes.password)
    return cfg


def argumento_chrome(url: str | None) -> str | None:
    """Traduz o proxy para a linha de comando do chrome.exe do cadastro.

    O Chrome cru aceita `--proxy-server=host:porta` mas NAO aceita credencial: ele
    abre um dialogo pedindo usuario e senha, que trava o cadastro sem mensagem util.
    Por isso, proxy COM credencial e recusado aqui com ProxyInvalido — e a mensagem
    precisa dizer o que fazer: usar proxy autorizado por IP, ou por um retransmissor
    local sem senha.

    O cadastro tambem precisa do proxy, e nao so a operacao: se a conta nasce no IP de
    casa e depois passa a operar por proxy, o Google ve a mudanca — que e exatamente o
    sinal que o proxy existe para evitar. O login tem que sair do MESMO IP do trabalho.
    """
    cfg = analisar(url)
    if cfg is None:
        return None
    if "username" in cfg or "password" in cfg:
        raise ProxyInvalido(
            "o Chrome do cadastro nao aceita proxy com usuario e senha: ele abre um "
            "dialogo de autenticacao e o cadastro fica parado sem erro nenhum. Use um "
            "proxy autorizado por IP, ou aponte esta conta para um retransmissor local "
            "sem senha (ex.: http://127.0.0.1:PORTA) que carregue a credencial por voce."
        )
    return f"--proxy-server={cfg['server']}"


def mascarar(url: str | None) -> str | None:
    """Esconde a senha do proxy antes de a URL sair na resposta da API.

    A resposta vai para o painel e para log; credencial de proxy nao precisa passear
    por ali. Nao levanta de proposito: `mascarar` alimenta o `GET /contas`, e derrubar
    a listagem inteira por causa de uma URL torta esconderia justamente a URL que a
    pessoa precisa ver para consertar. O que ele nao faz e deixar passar o que nao
    entendeu — havendo `@` no meio, tudo antes dele some.
    """
    if url is None or not url.strip():
        return None
    bruto = url.strip()
    try:
        partes = urlsplit(bruto)
        esquema = partes.scheme
        usuario, host, porta = partes.username, partes.hostname, partes.port
    except ValueError:
        esquema = ""
        usuario = host = porta = None
    if usuario and host:
        sufixo = f":{porta}" if porta else ""
        return f"{esquema}://{unquote(usuario)}:{SENHA_MASCARADA}@{host}{sufixo}"
    if host:
        return bruto
    return re.sub(r"(//)[^/@]*@", rf"\1{SENHA_MASCARADA}@", bruto)


def agrupar_por_ip(ips: Mapping[int, str | None]) -> dict[str, list[int]]:
    """Quais contas saem por cada IP. `None` fica de fora.

    ⚠️ CONTA NUNCA CONFERIDA NAO ENTRA. Ausencia de leitura nao e evidencia de nada, e
    juntar todos os `None` num balde faria as nove contas de hoje — nenhuma conferida —
    aparecerem como se dividissem um IP. O aviso nasceria gritando sobre o que ninguem
    mediu, e aviso que grita sempre e aviso que ninguem le.

    As listas saem ordenadas para a mensagem ser estavel entre duas leituras: "contas
    1 e 4" nao pode virar "4 e 1" so porque o dicionario mudou de ordem.
    """
    por_ip: dict[str, list[int]] = defaultdict(list)
    for conta_id, ip in ips.items():
        if ip:
            por_ip[ip].append(conta_id)
    return {ip: sorted(contas) for ip, contas in por_ip.items()}


def colisoes(ips: Mapping[int, str | None]) -> dict[str, list[int]]:
    """So os IPs com DUAS OU MAIS contas — o recorte que pede acao.

    E a checagem que o proxy existe para permitir e que ninguem fazia: proxy morto
    falha alto, porque o Chrome nao conecta; proxy vivo e repetido entre duas contas
    passa em silencio. Duas contas correlacionadas pelo IP e exatamente o que o proxy
    por conta existe para evitar.

    Quem decide QUAIS contas entram no mapa e o chamador. A regra "so as ativas" mora
    na rota, porque depende do estado do banco — aqui nao ha banco.
    """
    return {ip: contas for ip, contas in agrupar_por_ip(ips).items() if len(contas) > 1}
