"""Por qual IP uma conta sai de verdade.

Fronteira do sistema, e uma fronteira estreita: **nao conhece Playwright nem o Flow**.
Faz um GET e le um endereco de texto.

Existe porque nada conferia isso. Proxy MORTO falha alto — o Chrome nao conecta, e o
cadastro morre com mensagem. Proxy VIVO e repetido entre duas contas passa em silencio,
e e exatamente o defeito que o proxy por conta existe para evitar. Quem cruza os
resultados e `nucleo.proxy.colisoes`; aqui so se le um por vez.

⚠️ O QUE ELE MEDE, E O QUE NAO MEDE. Mede o IP pelo qual AQUELE proxy sai. Nao mede o
que o Chrome mostra ao Google: com o proxy vivo e configurado os dois sao o mesmo, mas
vazamento de WebRTC ou consulta de DNS por fora do tunel nao aparecem aqui. Confirmar o
IP nao e o mesmo que garantir anonimato, e a diferenca importa quando alguem for
concluir da leitura que "a conta esta protegida".

POR HTTP, E NAO ABRINDO O CHROME. Ler IP nao precisa de perfil nem de sessao: sao
~300 ms contra os ~8 s de subir um navegador, e nao esbarra na regra de que duas
sessoes no mesmo perfil sao impossiveis — dava para querer conferir o IP de uma conta
que esta gerando.
"""

from __future__ import annotations

import ipaddress

import httpx

from flowpool.nucleo.erros import ProxyInvalido
from flowpool.nucleo.proxy import analisar

# Texto puro, sem JSON para analisar: a resposta e o endereco e mais nada, entao um
# corpo que nao seja IP ja e sinal de que quem respondeu nao foi quem se pensava —
# portal de rede cativa, pagina de erro do proxy, bloqueio do provedor.
URL_DE_LEITURA = "https://api.ipify.org"

# Curto de proposito. Isto roda sob comando do operador, com ele olhando a tela: um
# proxy que demora dez segundos para dizer o proprio IP nao vai servir para gerar
# video, e esperar mais so adia a mesma conclusao.
TETO_SEGUNDOS = 10.0


class LeituraDeIpFalhou(Exception):
    """Nao deu para saber por onde a conta sai. A mensagem diz por qual proxy."""


def _proxy_para_httpx(proxy: str | None) -> str | None:
    """A URL que o httpx entende, ou None para sair pelo IP desta maquina.

    Passa por `analisar` — que e quem levanta `ProxyInvalido`, ANTES de qualquer rede.
    Assim uma URL torta falha na hora, com a URL na mensagem, e nao vira um timeout de
    dez segundos que nao explica nada.
    """
    cfg = analisar(proxy)
    if cfg is None:
        return None
    if "username" in cfg:
        # o httpx quer a credencial DENTRO da URL; `analisar` a devolve separada porque
        # e assim que o Playwright a quer
        esquema, resto = cfg["server"].split("://", 1)
        senha = cfg.get("password", "")
        return f"{esquema}://{cfg['username']}:{senha}@{resto}"
    return cfg["server"]


async def ip_de_saida(proxy: str | None,
                      transporte: httpx.AsyncBaseTransport | None = None) -> str:
    """O IP pelo qual aquele proxy sai. `proxy=None` mede o IP desta maquina.

    `None` NAO E ERRO: e o que as nove contas de hoje usam, e saber qual e esse IP e
    metade da resposta sobre colisao — duas contas sem proxy colidem entre si.

    `transporte` existe para o teste montar a resposta sem rede, no mesmo formato de
    `api/colar.py`.
    """
    alvo = _proxy_para_httpx(proxy)      # levanta ProxyInvalido antes de abrir socket
    # ⚠️ `proxy=` E `transport=` NAO CONVIVEM. Com os dois, o httpx monta o transporte
    # do proxy e ignora o injetado — o teste "sem rede" ia buscar DNS de verdade e
    # falhava com `getaddrinfo failed`. Quando o duplo entra, ele substitui a rede
    # INTEIRA, proxy incluso.
    #
    # O que isso custa em cobertura, dito em voz alta: a suite exercita a leitura, a
    # validacao da resposta e a traducao da URL, mas NAO exercita o roteamento pelo
    # proxy. Isso so se prova com um proxy de verdade, e e teste de campo.
    #
    # As duas chamadas escritas por extenso, e nao um `**kwargs`: com o dicionario
    # desempacotado o checador nao sabe para qual parametro cada valor vai e confere a
    # uniao contra TODOS eles — dezoito erros por uma economia de duas linhas.
    if transporte is not None:
        cliente = httpx.AsyncClient(timeout=TETO_SEGUNDOS, transport=transporte)
    else:
        cliente = httpx.AsyncClient(timeout=TETO_SEGUNDOS, proxy=alvo)
    try:
        async with cliente:
            r = await cliente.get(URL_DE_LEITURA)
            r.raise_for_status()
            bruto = r.text.strip()
    except httpx.HTTPError as e:
        onde = f"pelo proxy {proxy}" if proxy else "sem proxy, pelo IP desta maquina"
        raise LeituraDeIpFalhou(
            f"nao deu para ler o IP de saida {onde}: {type(e).__name__}: {e}") from e

    try:
        ipaddress.ip_address(bruto)
    except ValueError as e:
        # QUEM RESPONDEU NAO FOI QUEM SE PENSAVA. Gravar isto como "IP de saida" seria
        # pior que falhar: a conta apareceria conferida, com um valor que nao e IP.
        onde = f"o proxy {proxy}" if proxy else "a saida sem proxy"
        raise LeituraDeIpFalhou(
            f"{onde} respondeu algo que nao e um IP: {bruto[:80]!r}") from e
    return bruto


__all__ = ["URL_DE_LEITURA", "LeituraDeIpFalhou", "ProxyInvalido", "ip_de_saida"]
