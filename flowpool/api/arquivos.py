"""Navegacao de disco para o seletor de imagens do painel.

O painel precisa que a pessoa ESCOLHA OLHANDO a imagem, e o transporte precisa de um
CAMINHO em disco (`set_input_files` do Playwright abre o arquivo; nao ha upload no meio).
As duas coisas so se encontram se for o servidor a navegar o disco: o `<input type=file>`
do navegador entrega o conteudo do arquivo e nunca o caminho real — e uma protecao do
navegador, nao um detalhe que se contorna.

Servico local e sem autenticacao. Entao `resolver` e a UNICA barreira que existe entre
uma querystring e o disco inteiro desta maquina, e e por isso que ela e uma funcao pura,
testada a parte, e nao um `if` no meio de uma rota.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

# So imagem. Video e .md nao servem de referencia para o Flow e so atrapalhariam a
# leitura da grade.
#
# `.gif` entrou junto com a colagem (`colar.py`): a validacao por assinatura reconhece
# GIF, entao um GIF colado VAI existir em `_coladas/`. Sem a extensao aqui, ele seria
# gravado e logo em seguida recusado pela propria `/miniatura` — a tira do painel
# nasceria quebrada, e o seletor nao conseguiria mostrar o arquivo que o painel acabou
# de criar. Duas listas discordando sobre o que e imagem e o comeco de um bug calado.
EXTENSOES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

# O que a GALERIA mostra e o que o download entrega. Diferente de `EXTENSOES`, que e a
# lista do seletor de referencias: la video nao serve de entrada para o Flow, aqui video
# e justamente o que a pessoa gerou. `.webm` e `.mov` entram porque o arquivo em disco e
# o que o Flow entregou, e nao ha promessa de que sera sempre `.mp4`.
EXTENSOES_VIDEO = frozenset({".mp4", ".webm", ".mov"})

# Tipo declarado a mao em vez de `mimetypes.guess_type`: aquele le o registro do Windows,
# onde a extensao pode estar associada a outra coisa por qualquer programa instalado.
TIPOS = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4",
    ".webm": "video/webm", ".mov": "video/quicktime",
}

# Pastas que nao tem nada que interesse e enterrariam o que interessa. `flowpool/perfis`
# entra pelo caminho, e nao pelo nome: e um lugar so, e la moram os perfis do Chrome com
# as sessoes das contas — nem listar convem.
NOMES_IGNORADOS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "scratch", ".pytest_cache",
})
CAMINHOS_IGNORADOS = frozenset({"flowpool/perfis"})

# `C:` , `c:/...`, e tambem o `\\servidor\share` do UNC depois da normalizacao das barras.
_COM_UNIDADE = re.compile(r"^[A-Za-z]:")


class CaminhoForaDaRaiz(ValueError):
    """O caminho pedido nao cai dentro da raiz do projeto."""


def _relativo(raiz: Path, alvo: Path) -> str:
    """Caminho de `alvo` visto da raiz, sempre com barra normal.

    Barra normal em todo lugar: este texto viaja em JSON, volta em querystring e vira
    chave de comparacao no JS. Deixar o separador do Windows vazar daria dois nomes
    diferentes para o mesmo arquivo.
    """
    return alvo.relative_to(raiz).as_posix() if alvo != raiz else ""


def resolver(raiz: Path | str, pedido: str | None) -> Path:
    """Resolve `pedido` DENTRO de `raiz`, ou levanta `CaminhoForaDaRaiz`.

    Vale: vazio (ou `.`), que significa a propria raiz, e qualquer caminho relativo que
    caia dentro dela. Nao vale: `..` de qualquer forma, disfarcado no meio ou nao;
    caminho ancorado (`/etc/passwd`, `C:\\Windows`, `\\\\servidor\\share`); e link
    simbolico que aponte para fora — este ultimo cai no `resolve()`, que segue o link e
    entrega o destino de verdade, que e o que a comparacao final precisa ver.

    A recusa do caminho ancorado e explicita, antes do `join`, e nao sobra do
    `is_relative_to`: `Path("/proj") / "C:\\Windows"` no Linux produz um caminho DENTRO
    da raiz, com um nome esquisito. A regra ficaria valendo no Windows e falhando calada
    no Linux — e regra de seguranca que muda com o sistema nao e regra.
    """
    raiz_abs = Path(raiz).resolve()
    bruto = (pedido or "").strip().replace("\\", "/")
    if not bruto or bruto == ".":
        return raiz_abs
    if bruto.startswith("/") or _COM_UNIDADE.match(bruto):
        raise CaminhoForaDaRaiz(
            f"caminho {pedido!r} e absoluto; use um caminho relativo a raiz do projeto")
    alvo = (raiz_abs / bruto).resolve()
    if alvo != raiz_abs and not alvo.is_relative_to(raiz_abs):
        raise CaminhoForaDaRaiz(
            f"caminho {pedido!r} sai da raiz do projeto")
    return alvo


def e_ruido(relativo: str) -> bool:
    """Diz se o caminho relativo cai dentro de uma das pastas que a navegacao ignora.

    Vale para a pasta e para tudo abaixo dela: sem o teste por prefixo, esconder
    `flowpool/perfis` da grade nao impediria ninguem de pedir `flowpool/perfis/x/Cookies`
    direto na querystring.
    """
    if not relativo:
        return False
    partes = PurePosixPath(relativo).parts
    if any(p in NOMES_IGNORADOS for p in partes):
        return True
    # Pasta oculta e ruido por definicao: `.git`, `.venv` e `.pytest_cache` da lista sao
    # tres casos da mesma regra, e o projeto tem outras (`.claude`, `.obsidian`) que
    # apareceriam na primeira tela do seletor sem nunca ter uma imagem dentro.
    if any(p.startswith(".") and p not in {".", ".."} for p in partes):
        return True
    return any(relativo == c or relativo.startswith(f"{c}/")
               for c in CAMINHOS_IGNORADOS)


def e_imagem(nome: str) -> bool:
    return Path(nome).suffix.lower() in EXTENSOES


def e_video(nome: str) -> bool:
    return Path(nome).suffix.lower() in EXTENSOES_VIDEO


def e_midia(nome: str) -> bool:
    """Imagem ou video — o que a galeria mostra e o que o download entrega."""
    return e_imagem(nome) or e_video(nome)


def tipo_de(alvo: Path) -> str:
    return TIPOS.get(alvo.suffix.lower(), "application/octet-stream")


def relativo_seguro(raiz: Path | str, absoluto: str | None) -> str | None:
    """Caminho relativo a raiz de um `Job.arquivo`, ou `None` se ele nao serve.

    O motor grava `Job.arquivo` ABSOLUTO (`str(self.raiz / destino / nome.mp4)`), e o
    painel precisa do relativo: e ele que `resolver` aceita, e caminho absoluto na
    querystring e exatamente o que a barreira recusa. A conversao mora aqui, ao lado da
    barreira, e nao numa expressao no JS — o JS nao tem como saber qual e a raiz.

    Devolve `None` em vez de levantar: um job antigo pode ter sido gerado com outra
    raiz, e um card sem link e melhor do que uma listagem que quebra inteira.
    """
    if not absoluto:
        return None
    raiz_abs = Path(raiz).resolve()
    try:
        alvo = Path(absoluto).resolve()
    except OSError:
        return None
    if alvo == raiz_abs or not alvo.is_relative_to(raiz_abs):
        return None
    rel = _relativo(raiz_abs, alvo)
    return None if e_ruido(rel) else rel


def listar(raiz: Path | str, pedido: str | None) -> dict:
    """Pastas e imagens de um diretorio, ja separadas e em ordem alfabetica.

    Pastas antes de arquivos porque a navegacao e o movimento comum: quem abriu o modal
    quase sempre precisa descer mais um nivel antes de escolher.

    Levanta `CaminhoForaDaRaiz` (fora da raiz ou pasta ignorada), `FileNotFoundError`
    (nao existe) e `NotADirectoryError` (existe mas e arquivo).
    """
    raiz_abs = Path(raiz).resolve()
    alvo = resolver(raiz_abs, pedido)
    rel = _relativo(raiz_abs, alvo)
    if e_ruido(rel):
        raise CaminhoForaDaRaiz(f"pasta {rel!r} nao entra na navegacao")
    if not alvo.exists():
        raise FileNotFoundError(f"{rel or '.'} nao existe")
    if not alvo.is_dir():
        raise NotADirectoryError(f"{rel} nao e pasta")

    pastas, arquivos = [], []
    # `iterdir` pode esbarrar em pasta sem permissao no meio do caminho; uma pasta ilegivel
    # nao pode derrubar a listagem inteira, entao o erro vira ausencia.
    try:
        itens = sorted(alvo.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        itens = []
    for item in itens:
        try:
            pasta = item.is_dir()
        except OSError:
            continue
        filho = f"{rel}/{item.name}" if rel else item.name
        if pasta:
            if e_ruido(filho):
                continue
            pastas.append({"nome": item.name, "caminho": filho})
        elif e_imagem(item.name):
            try:
                tamanho = item.stat().st_size
            except OSError:
                tamanho = 0
            arquivos.append({"nome": item.name, "caminho": filho, "tamanho": tamanho})

    # `pai` vem do servidor em vez de sair de um `split` no JS: e ele que o botao
    # "voltar" usa, e a raiz e "" — que e um valor que o cliente nao teria como
    # distinguir de "nao ha para onde voltar" sozinho.
    acima = str(PurePosixPath(rel).parent) if rel else "."
    pai = None if not rel else ("" if acima == "." else acima)
    return {"caminho": rel, "pai": pai, "pastas": pastas, "arquivos": arquivos}


def _arquivo_servivel(raiz: Path | str, pedido: str | None,
                      aceita, oferece: str) -> Path:
    """Resolve UM arquivo para servir, pela mesma barreira da listagem.

    `resolver` continua sendo a unica barreira entre a querystring e o disco desta
    maquina; o que muda de uma rota para outra e so o filtro de extensao. Ele nao e
    cosmetico: sem ele estas rotas serviriam qualquer arquivo do projeto (o .db, o .env)
    para quem soubesse o nome.
    """
    raiz_abs = Path(raiz).resolve()
    alvo = resolver(raiz_abs, pedido)
    rel = _relativo(raiz_abs, alvo)
    if not rel or e_ruido(rel):
        raise CaminhoForaDaRaiz(f"caminho {rel!r} nao entra na navegacao")
    if not aceita(alvo.name):
        raise CaminhoForaDaRaiz(f"{rel} nao serve aqui; esta rota so serve {oferece}")
    if not alvo.is_file():
        raise FileNotFoundError(f"{rel} nao existe")
    return alvo


def arquivo_de_midia(raiz: Path | str, pedido: str | None) -> Path:
    """Resolve UM arquivo de midia — imagem ou video — para servir ou baixar."""
    return _arquivo_servivel(raiz, pedido, e_midia,
                             str(sorted(EXTENSOES | EXTENSOES_VIDEO)))


def arquivo_de_imagem(raiz: Path | str, pedido: str | None) -> Path:
    """Resolve o caminho de UMA imagem para servir na miniatura.

    Mesma barreira da listagem, mais duas exigencias: tem que ser arquivo e tem que ter
    extensao de imagem. Video NAO entra aqui: esta rota alimenta o seletor de
    referencias, e referencia do Flow e imagem.
    """
    return _arquivo_servivel(raiz, pedido, e_imagem, str(sorted(EXTENSOES)))
