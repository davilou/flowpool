"""Perfil de conta: cadastro (Chrome comum) e validacao (Playwright).

A divisao nao e estilistica. MEDIDO em 2026-08-20: o Google recusa o login em qualquer
Chrome dirigido por CDP — "esse navegador ou app pode nao ser seguro" — e nao adianta
`channel="chrome"`, user-agent nem flag nenhuma. Entao:

    login    -> chrome.exe comum, por subprocess, humano na frente
    operacao -> Playwright reaproveitando o MESMO perfil

Uma vez autenticado, o Playwright dirige a sessao sem reclamacao. A checagem e
exclusiva do fluxo de login.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import async_playwright

from flowpool.nucleo.proxy import analisar, argumento_chrome
from flowpool.transporte.base import SessaoMorta
from flowpool.transporte.pw import captura
from flowpool.transporte.pw import seletores as sel

_CANDIDATOS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]

# Quanto tempo esperar a pessoa logar e passar as duas telas de boas-vindas.
TETO_LOGIN_S = 900.0
# Quanto esperar o perfil ficar livre depois que a janela fecha. Chrome deixa processo
# filho vivo por alguns segundos depois de a janela sumir.
TETO_LIBERAR_S = 120.0

INSTRUCOES = """
Uma janela do Chrome abriu no perfil desta conta.

  1. Faca o login na conta Google.
  2. Tela de consentimento: DEIXE AS DUAS CAIXAS DESMARCADAS e clique em "Avancar".
  3. Aviso de privacidade: ROLE ATE O FIM DO TEXTO — o botao "Continuar" so habilita
     depois que o fim do texto entra na tela de verdade. Janela minimizada nao conta:
     o portao e IntersectionObserver.
  4. Confirme que a lista de projetos do Flow carregou.
  5. FECHE A JANELA DO CHROME. O cadastro continua sozinho a partir dai.

Nao abra este mesmo perfil em outro Chrome enquanto isso: o segundo entrega a URL
para o primeiro e morre, e a validacao falharia com TargetClosedError.
"""


class ChromeAusente(RuntimeError):
    """O Google recusa o Chromium empacotado do Playwright; precisa do Chrome instalado."""


# Quanto tempo se considera uma sessao valida DEPOIS de ela ter sido vista viva.
#
# E ESTIMATIVA, e a mudanca de natureza importa: a aplicacao antiga LIA a data de
# expiracao de `/fx/api/auth/session`; a nova nao expoe nada equivalente, entao aqui se
# arbitra um prazo. As ~48h vem do que foi observado em agosto com o valor real.
#
# Estimar para MENOS e o lado seguro: quando o prazo vence, o pior que acontece e uma
# revalidacao a mais. Estimar para mais faria o pool insistir com uma sessao morta e
# gastar tentativa de job para descobrir.
VALIDADE_ESTIMADA = timedelta(hours=40)


class PerfilOcupado(RuntimeError):
    """Ja existe um Chrome usando este --user-data-dir."""


def checar_chrome() -> Path:
    for p in _CANDIDATOS:
        if p.exists():
            return p
    raise ChromeAusente(
        "Chrome nao encontrado. O Flow recusa o Chromium do Playwright - "
        "instale o Google Chrome ou ajuste _CANDIDATOS em sessao.py."
    )


def caminho_perfil(raiz: Path, email: str) -> Path:
    """Pasta de perfil estavel e legivel para um email."""
    slug = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")
    digest = hashlib.sha1(email.encode()).hexdigest()[:8]
    return Path(raiz) / f"{slug}-{digest}"


def argumentos_chrome(exe: Path, perfil: Path, url: str,
                      proxy: str | None = None) -> list[str]:
    """A linha de comando do Chrome de LOGIN.

    Nenhuma flag de automacao entra aqui. `--remote-debugging-port` e o que faz o
    Google recusar; `--headless` mata o IntersectionObserver que libera o aviso de
    privacidade. As tres que ficam sao so para o perfil novo nao abrir assistente.

    ESTE Chrome e o unico do app que pode ser afetado por oclusao. O Chrome de trabalho
    sobe pelo Playwright, que passa `--disable-backgrounding-occluded-windows` — medido
    em 2026-08-21, aquela janela fica `visible` mesmo coberta por outra. Aqui nao ha
    essa flag, entao a instrucao ao usuario continua sendo nao minimizar a janela de
    login. E uma janela so, uma vez por conta, com a pessoa na frente.

    O proxy entra porque o LOGIN tem que sair do mesmo IP do trabalho: conta que nasce
    no IP de casa e depois passa a operar por proxy mostra ao Google exatamente a
    mudanca que o proxy existe para evitar. `argumento_chrome` levanta `ProxyInvalido`
    quando a URL traz credencial — o Chrome cru abriria um dialogo de autenticacao, e
    a janela ficaria parada ate o teto de login sem erro nenhum.
    """
    args = [
        str(exe),
        f"--user-data-dir={Path(perfil).resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    flag = argumento_chrome(proxy)
    if flag:
        args.append(flag)
    # a URL fica sempre por ultimo: e assim que o Chrome a distingue de uma flag
    args.append(url)
    return args


def _linhas_de_comando() -> list[str]:
    """CommandLine de cada chrome.exe vivo. Isolado para o teste poder trocar."""
    consulta = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "ForEach-Object { $_.CommandLine }")
    saida = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", consulta],
        capture_output=True, text=True, timeout=60)
    return [linha for linha in saida.stdout.splitlines() if linha.strip()]


def perfil_em_uso(perfil: Path) -> bool:
    """Se algum Chrome vivo esta com este --user-data-dir.

    Nao da para usar arquivo de lock: `SingletonLock` e coisa de POSIX, e o
    `Default/LOCK` do Windows e do LevelDB e existe com o Chrome fechado. Sobra olhar
    a linha de comando dos processos.
    """
    alvo = str(Path(perfil).resolve()).lower()
    try:
        return any(alvo in linha.lower() for linha in _linhas_de_comando())
    except Exception:
        # sem conseguir consultar, o seguro e assumir livre e deixar o erro real
        # aparecer na validacao — travar o cadastro por causa da consulta seria pior
        return False


def esperar_perfil_livre(perfil: Path, teto_s: float = TETO_LIBERAR_S) -> None:
    limite = time.monotonic() + teto_s
    while time.monotonic() < limite:
        if not perfil_em_uso(perfil):
            return
        time.sleep(2.0)
    raise PerfilOcupado(
        f"ainda ha Chrome usando {perfil}. Feche todas as janelas desse perfil.")


def interpretar_sessao(dados: dict) -> datetime:
    """Le a resposta de `/fx/api/auth/session` e devolve SO a data de expiracao.

    O token NAO sai daqui. A resposta inteira carrega o access_token OAuth em texto
    puro, e o valor de retorno desta funcao vai para log e para evento — devolver o
    dicionario seria abrir a mesma porta que a regra 2 fecha no DOM.

    Deslogado, o Flow devolve `{}` com HTTP 200. Conferir so o status deixaria uma
    sessao morta passar por viva ate a primeira geracao falhar, tres tentativas
    depois, com a conta marcada por um erro que nao era dela.
    """
    if not dados.get("access_token"):
        raise SessaoMorta("sessao sem access_token - a conta nao esta logada")
    bruto = str(dados.get("expires") or "")
    if not bruto:
        raise SessaoMorta("sessao sem campo `expires`")
    return datetime.fromisoformat(bruto.replace("Z", "+00:00"))


async def abrir_contexto(perfil: Path, proxy: str | None = None,
                         headless: bool = False):
    """Devolve (playwright, contexto). Quem chama fecha os dois.

    `headless` existe so na assinatura, e o default e o unico valor sensato: sem
    janela visivel o IntersectionObserver nao dispara, a lista de projetos nao termina
    de renderizar e nada disso da erro — so nao aparece.

    `proxy` e a URL da conta. `analisar` devolve None quando nao ha proxy (o caso
    comum hoje), e `launch_persistent_context` com `proxy=None` e o mesmo que nao
    passar nada. Diferente do Chrome de login, aqui credencial e aceita: o Playwright
    autentica sozinho, sem dialogo.
    """
    checar_chrome()
    Path(perfil).mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(Path(perfil).resolve()),
        channel="chrome",              # o Chromium empacotado e recusado pelo auth
        headless=headless,
        no_viewport=True,
        args=["--start-maximized"],
        accept_downloads=True,
        proxy=analisar(proxy),
    )
    # ANTES da primeira navegacao: o download do Flow e lido do blob na pagina, e nao
    # pelo download do Chrome, que TRAVA o processo inteiro (medido 2026-09-06, Chrome
    # 152). Ver `captura.py`. Instalar no contexto cobre toda pagina dali em diante.
    await captura.instalar(ctx)
    return pw, ctx


async def validar_perfil(perfil: Path, proxy: str | None = None) -> datetime:
    """Abre o perfil com Playwright e prova que a sessao esta viva.

    VALIDA PELA TELA, e nao mais por API. A aplicacao antiga tinha
    `/fx/api/auth/session`, que devolvia `access_token` e `expires`; a nova NAO TEM
    rota equivalente — MEDIDO 2026-09-05 observando as 42 requisicoes do carregamento,
    onde a autenticacao aparece so como cookie do Google
    (`accounts.google.com/rotatecookies`).

    O SINAL DE LOGIN E O AVATAR, e essa escolha tem motivo. O candidato obvio seria
    "tem cartao de projeto na home", e ele esta ERRADO pelo mesmo motivo que ja travou
    todo projeto novo em agosto: conta recem-criada, ou conta que teve os projetos
    apagados, tem ZERO cartoes — e reprovaria uma sessao perfeitamente viva. Lista
    vazia e estado legitimo. O avatar existe sempre que ha login.

    O PORTAO DE IDADE tem erro proprio. Sem isso, conta bloqueada vira "sessao morta",
    o painel manda revalidar, o usuario faz um login inteiro e cai no mesmo portao —
    que foi exatamente o que aconteceu.

    Valida pelo MESMO proxy por que a conta vai operar. Validar pelo IP de casa e
    depois gerar por outro IP e a divergencia que este campo existe para nao criar.
    """
    pw, ctx = await abrir_contexto(perfil, proxy)
    try:
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        try:
            await pagina.locator(sel.AVATAR_CONTA).first.wait_for(timeout=45_000)
        except Exception:
            if sel.URL_PORTAO_IDADE in pagina.url:
                raise SessaoMorta(
                    "o Flow bloqueou esta conta no portao de idade "
                    f"({sel.URL_PORTAO_IDADE}). Revalidar NAO resolve: a conta Google "
                    "precisa ter a idade verificada") from None
            raise SessaoMorta(
                f"nao achei o avatar em {pagina.url} - a conta parece deslogada"
            ) from None
        return datetime.now(timezone.utc) + VALIDADE_ESTIMADA
    finally:
        await ctx.close()
        await pw.stop()


async def cadastrar(raiz_perfis: Path, email: str,
                    proxy: str | None = None) -> tuple[Path, datetime]:
    """Sobe o Chrome de login, espera a pessoa, e valida. Devolve (perfil, expira_em).

    Revalidar sessao caida e a MESMA coisa: mesmo perfil, mesmo fluxo. Por isso nao ha
    duas funcoes.

    O login sai pelo proxy da conta, e a montagem dos argumentos vem ANTES do `Popen`:
    proxy com credencial levanta `ProxyInvalido` ali, com o Chrome ainda nao aberto e
    o perfil ainda livre. Levantar depois deixaria uma janela orfa segurando o perfil,
    e a proxima tentativa morreria em `PerfilOcupado` — um erro que aponta para o lugar
    errado.
    """
    perfil = caminho_perfil(raiz_perfis, email)
    perfil.mkdir(parents=True, exist_ok=True)
    exe = checar_chrome()
    args = argumentos_chrome(exe, perfil, sel.URL_FLOW, proxy)
    if perfil_em_uso(perfil):
        raise PerfilOcupado(f"{perfil} ja esta aberto em outro Chrome - feche antes")

    proc = subprocess.Popen(args)
    print(INSTRUCOES)

    # `to_thread` nao e detalhe: `cadastrar` e aguardado dentro de uma rota do FastAPI,
    # e o motor roda no MESMO event loop. Um `proc.wait()` direto congelaria a fila
    # inteira pelos minutos que a pessoa levar para logar.
    await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=TETO_LOGIN_S)
    if perfil_em_uso(perfil):
        # o Chrome que subimos morreu na hora porque outro ja tinha o perfil e ficou
        # com a janela. Continuar daria TargetClosedError na validacao.
        raise PerfilOcupado(
            f"{perfil} continua em uso depois de o Chrome de login sair - "
            "outro Chrome pegou a janela. Feche tudo e repita.")
    await asyncio.to_thread(esperar_perfil_livre, perfil)
    return perfil, await validar_perfil(perfil, proxy)


def abrir_para_conferir(perfil: Path, proxy: str | None = None,
                        url: str | None = None) -> None:
    """Sobe o Chrome comum no perfil da conta e SAI. Nao espera, nao valida.

    Existe para o botao "Abrir" do painel: o usuario quer olhar a conta com os proprios
    olhos — conferir se um video que o app deu por falho esta la, ver o saldo real, ver
    o que o Flow mostrou. Ate agora a unica forma de fazer isso pelo app era "Revalidar",
    que exige passar por um login inteiro sem necessidade nenhuma.

    NAO CHECA `perfil_em_uso`, e isso e deliberado. Chrome com o mesmo `--user-data-dir`
    ja aberto nao abre uma segunda instancia: ele entrega a URL a janela existente e
    sai. Ou seja, clicar "Abrir" duas vezes traz a janela para a frente, que e
    exatamente o que se espera de um botao chamado Abrir. Recusar aqui transformaria o
    comportamento certo do Chrome em erro na cara do usuario.

    Quem PRECISA recusar e a rota, e por outro motivo: se a conta estiver GERANDO, o
    perfil esta nas maos do Playwright, e um Chrome comum no mesmo perfil derruba o job.
    Essa checagem e `orq.ocupada`, que sabe distinguir "o app esta usando" de "o usuario
    deixou uma janela aberta" — coisa que olhar a linha de comando nao sabe.

    O proxy vai junto pelo mesmo motivo do login: conferir a conta por um IP diferente
    do de trabalho mostra ao Google a mudanca que o proxy existe para evitar.

    `Popen` sem `wait`: quem chama nao pode ficar preso enquanto a pessoa olha a tela.
    """
    exe = checar_chrome()
    args = argumentos_chrome(exe, perfil, url or sel.URL_FLOW, proxy)
    subprocess.Popen(args)
