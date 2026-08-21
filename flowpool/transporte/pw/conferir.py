"""Confere que cada seletor de `seletores.py` ainda casa com o Flow de hoje.

E a PRIMEIRA coisa a rodar quando alguma coisa parar de funcionar. Sem ele, o sintoma
de um deploy do Google e um job que falha tres vezes, troca de conta, falha de novo e
morre com "TimeoutError" — que nao diz qual seletor morreu. Com ele, o diagnostico e
uma linha.

Nao gera nada e nao gasta credito: so navega, abre paineis, TROCA DE MODO, ANEXA uma
referencia e desfaz. Trocar de modo entrou no roteiro porque a lista de modos do painel
depende do modo atual (`seletores.MODO_PAI`), e porque e a unica forma de medir o que o
chip escreve em `frames2v` e `ingredients2v` sem mandar uma geracao — ver `_medir_modos`.

Anexar entrou porque contar seletor nao bastava. O defeito de 2026-08-20 — a referencia
subindo para o acervo e nunca entrando no comando — passava por TODOS os `count()`: o
`input[type=file]` existia, o botao de anexar existia, e mesmo assim a geracao saia por
texto puro. So percorrer o caminho pega isso. Ver `_medir_anexo`.

Uso:
    python -m flowpool.transporte.pw.conferir <perfil> [<uuid-do-projeto>]

Sem o uuid, confere so a lista de projetos e o menu da conta.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from flowpool.transporte.pw import analise, dom
from flowpool.transporte.pw import seletores as sel

PENDENTE = sel.PENDENTE   # marcador dos seletores que ainda nao foram medidos

# Ajuda a MEDIR `CARD_GRADE`. Sobe da miniatura ate o ancestral clicavel e imprime a
# assinatura estrutural de cada degrau — tag, role, data-* e classe. So estrutura: nao
# le innerText, entao nao ha por onde o access_token sair (regra 2).
ASSINATURA_DA_GRADE = r"""
() => {
  const midias = [...document.querySelectorAll('img[src*="trpc"], img[src*="media.getMedia"], video')];
  const assinatura = e => {
    const attrs = [...e.attributes]
      .filter(a => a.name === 'role' || a.name.startsWith('data-') || a.name === 'aria-label')
      .map(a => `${a.name}="${a.value}"`).join(' ');
    return `${e.tagName.toLowerCase()}${attrs ? ' ' + attrs : ''}`;
  };
  return midias.slice(0, 3).map(m => {
    const degraus = [];
    let e = m;
    for (let i = 0; i < 6 && e && e.tagName !== 'BODY'; i++) {
      degraus.push(assinatura(e));
      e = e.parentElement;
    }
    return degraus;
  });
}
"""


class Placar:
    def __init__(self) -> None:
        self.ok = 0
        self.faltando: list[str] = []
        self.pendentes: list[str] = []

    async def checar(self, escopo, contexto: str, nome: str, seletor: str,
                     minimo: int = 1) -> None:
        if not seletor or seletor == PENDENTE:
            self.pendentes.append(f"{contexto}.{nome}")
            print(f"[{contexto:9}] {nome:24} PENDENTE (nunca medido)")
            return
        try:
            n = await escopo.locator(seletor).count()
        except Exception as e:                       # seletor malformado
            self.faltando.append(f"{contexto}.{nome}")
            print(f"[{contexto:9}] {nome:24} INVALIDO ({type(e).__name__})")
            return
        if n >= minimo:
            self.ok += 1
            print(f"[{contexto:9}] {nome:24} OK ({n})")
        else:
            self.faltando.append(f"{contexto}.{nome}")
            print(f"[{contexto:9}] {nome:24} FALTA")


async def _candidatos(pagina) -> None:
    """Imprime o texto curto e visivel da tela, para achar o rotulo que mudou.

    Passa por `dom.seguro`: sem segredo (regra 2) e sem acento (console cp1252).
    """
    textos = await pagina.evaluate(dom.TEXTOS_CURTOS, 40)
    vistos = sorted({dom.seguro(t) for t in textos})
    print("\ncandidatos visiveis na tela:")
    for t in vistos:
        print("   ", t)


async def _medir_grade(pagina) -> None:
    """Sobe da miniatura ate o container, para escolher o `CARD_GRADE`.

    Escolha o degrau que responde ao clique DIREITO e que aparece uma vez por item da
    grade — nao o `<video>`, que e miniatura e nao e clicavel.
    """
    caminhos = await pagina.evaluate(ASSINATURA_DA_GRADE)
    if not caminhos:
        print("\nnenhuma midia na grade — gere alguma coisa antes de medir CARD_GRADE")
        return
    print("\nancestrais de cada midia da grade (para escolher CARD_GRADE):")
    for i, degraus in enumerate(caminhos):
        print(f"  midia {i}:")
        for nivel, sig in enumerate(degraus):
            print(f"    {nivel}: {sig}")


async def _abrir_painel(pagina):
    """Abre o painel do chip e devolve o localizador dele."""
    await pagina.locator(sel.CHIP_CONFIG).first.click()
    await pagina.wait_for_timeout(2000)
    return pagina.locator(sel.PAINEL_CHIP).first


async def _clicar_modo(pagina, painel, modo: str) -> bool:
    """Clica no botao do modo, se houver exatamente um. Ambiguidade e nao, nao talvez."""
    loc = painel.locator(sel.BOTAO_MODO[modo])
    if await loc.count() != 1:
        return False
    await loc.first.click()
    await pagina.wait_for_timeout(1500)
    return True


async def _medir_modos(pagina, p: Placar):
    """Percorre os quatro modos e imprime o texto do chip em cada um.

    NAO GERA NADA E NAO GASTA CREDITO: so troca o modo e le o chip. E o instrumento das
    duas medicoes que o mapa ainda deve:

    1. A HIERARQUIA. A lista de modos do painel depende do modo atual — com o chip em
       imagem so existem `image` e `videocam`. Cada linha diz por onde se chegou:
       "direto" quando o botao ja estava la, "via t2v" quando foi preciso passar pelo
       video primeiro (`seletores.MODO_PAI`).
    2. O ROTULO NO CHIP. Nao se sabe se o chip escreve "Frames"/"Elementos" nos
       submodos ou se continua dizendo so "Video". O veredito sai impresso: enquanto o
       marcador proprio nao aparecer, a tolerancia de `ROTULO_MODO_NO_CHIP` fica.

    Termina com o painel de volta em VIDEO, que e o modo com mais grupos para conferir.
    """
    print("\nmodos do chip (troca de modo nao gasta credito):")
    for modo in sel.BOTAO_MODO:
        visivel = await _modo_no_painel(pagina, modo)
        painel = pagina.locator(sel.PAINEL_CHIP).first
        via = "direto"
        if not visivel:
            pai = sel.MODO_PAI.get(modo)
            if not pai or not await _clicar_modo(pagina, painel, pai):
                p.faltando.append(f"chip.MODO[{modo}]")
                print(f"[{'chip':9}] {('MODO[' + modo + ']'):24} FALTA "
                      f"(nao esta no painel e nao ha por onde chegar)")
                painel = await _abrir_painel(pagina)
                continue
            via = f"via {pai}"
        if not await _clicar_modo(pagina, painel, modo):
            p.faltando.append(f"chip.MODO[{modo}]")
            print(f"[{'chip':9}] {('MODO[' + modo + ']'):24} FALTA ({via})")
            painel = await _abrir_painel(pagina)
            continue
        p.ok += 1
        await pagina.keyboard.press("Escape")
        await pagina.wait_for_timeout(800)
        chip = await _texto_do_chip(pagina)
        print(f"[{'chip':9}] {('MODO[' + modo + ']'):24} OK ({via})  chip: {chip!r}")
        _veredito_do_rotulo(modo, chip)
        painel = await _abrir_painel(pagina)

    # deixa o painel em video: e la que existem duracao e o dropdown de video
    painel = pagina.locator(sel.PAINEL_CHIP).first
    await _clicar_modo(pagina, painel, "t2v")
    return pagina.locator(sel.PAINEL_CHIP).first


async def _modo_no_painel(pagina, modo: str) -> bool:
    """Se o botao daquele modo esta no painel ABERTO, agora."""
    painel = pagina.locator(sel.PAINEL_CHIP).first
    return await painel.locator(sel.BOTAO_MODO[modo]).count() > 0


def _veredito_do_rotulo(modo: str, chip: str) -> None:
    """Diz, em uma linha, se `ROTULO_MODO_NO_CHIP` pode largar a tolerancia."""
    aceitos = sel.ROTULO_MODO_NO_CHIP[modo]
    if len(aceitos) < 2:
        return
    proprio, pai = aceitos[0], aceitos[1]
    if proprio in chip:
        print(f"    -> ROTULO_MODO_NO_CHIP[{modo}]: o chip DIZ {proprio!r} — da para "
              f"deixar so ele em seletores.py")
    elif pai in chip:
        print(f"    -> ROTULO_MODO_NO_CHIP[{modo}]: o chip NAO diz {proprio!r}, so "
              f"{pai!r} — a tolerancia e a unica verificacao possivel, mantenha")
    else:
        print(f"    -> ROTULO_MODO_NO_CHIP[{modo}]: o chip nao diz nem {proprio!r} nem "
              f"{pai!r} — o modo pode nao ter trocado")


async def _medir_custo(pagina, p: Placar) -> None:
    """Le o "A geração vai usar N créditos" do painel do chip. Nao gasta nada.

    O painel precisa estar ABERTO — e onde a frase vive. Vale a pena conferir aqui
    porque este numero e o custo exato na interface, e a alternativa (a tabela de
    custos) envelhece calada quando o Google muda de preco.
    """
    await pagina.locator(sel.CHIP_CONFIG).first.click()
    await pagina.wait_for_timeout(2000)
    try:
        bruto = await pagina.locator(sel.CUSTO_DA_GERACAO).first.inner_text(timeout=5_000)
        custo = analise.custo_do_texto(dom.seguro(bruto))
    except Exception as e:
        p.faltando.append("chip.CUSTO_DA_GERACAO")
        print(f"[{'chip':9}] {'CUSTO_DA_GERACAO':24} FALTA ({type(e).__name__})")
    else:
        p.ok += 1
        print(f"[{'chip':9}] {'CUSTO_DA_GERACAO':24} OK (anuncia {custo} creditos)")
    await pagina.keyboard.press("Escape")
    await pagina.wait_for_timeout(1000)


async def _medir_anexo(pagina, p: Placar) -> None:
    """Percorre o seletor de midia INTEIRO — abrir, incluir, desfazer. Custo zero.

    E o unico jeito de saber que anexar referencia ainda funciona sem mandar uma
    geracao. E o caminho precisa ser percorrido, nao so contado: o defeito que criou
    este bloco passava por todos os `count()` do mundo — o `input[type=file]` existia,
    o botao existia — e mesmo assim a referencia nunca entrava no comando.

    Nao deixa rastro: o que for incluido sai por "Apagar comando" no fim.
    """
    await pagina.locator(sel.BOTAO_ANEXAR).first.click()
    await pagina.wait_for_timeout(2500)
    await p.checar(pagina, "anexo", "SELETOR_MIDIA", sel.SELETOR_MIDIA)
    await p.checar(pagina, "anexo", "ITEM_MIDIA", sel.ITEM_MIDIA)

    itens = await pagina.evaluate(dom.ITENS_DE_MIDIA, sel.ITEM_MIDIA)
    print(f"    itens no seletor: {len(itens)}"
          + "".join(f"\n      {'*' if i['selecionado'] else '-'} "
                    f"{dom.seguro(analise.nome_do_item(i['texto']))}"
                    f"{'' if analise.item_pronto(i) else '  (ainda subindo)'}"
                    for i in itens))
    if not itens:
        print("    projeto vazio: nao da para exercitar o anexo — gere alguma coisa "
              "antes, ou rode a fumaca de imagem, que e gratuita")
        await pagina.keyboard.press("Escape")
        return

    await p.checar(pagina, "anexo", "BOTAO_INCLUIR_NO_COMANDO",
                   sel.BOTAO_INCLUIR_NO_COMANDO)
    # MEDIDO: clicar num item JA selecionado o desmarca, e sem selecao o botao some
    if not itens[0]["selecionado"]:
        await pagina.locator(sel.ITEM_MIDIA).first.click()
        await pagina.wait_for_timeout(1000)
    await pagina.locator(sel.BOTAO_INCLUIR_NO_COMANDO).first.click()
    await pagina.wait_for_timeout(2500)

    quantas = await pagina.locator(sel.REFERENCIA_ANEXADA).count()
    if quantas == 1:
        p.ok += 1
        print(f"[{'anexo':9}] {'REFERENCIA_ANEXADA':24} OK (1 na barra do prompt)")
    else:
        p.faltando.append("anexo.REFERENCIA_ANEXADA")
        print(f"[{'anexo':9}] {'REFERENCIA_ANEXADA':24} FALTA (achei {quantas} — a "
              f"midia foi para o acervo mas NAO entrou no comando)")

    await p.checar(pagina, "anexo", "BOTAO_APAGAR_COMANDO", sel.BOTAO_APAGAR_COMANDO)
    if await pagina.locator(sel.BOTAO_APAGAR_COMANDO).count() == 1:
        await pagina.locator(sel.BOTAO_APAGAR_COMANDO).first.click()
        await pagina.wait_for_timeout(1500)
        print(f"    referencias depois de apagar o comando: "
              f"{await pagina.locator(sel.REFERENCIA_ANEXADA).count()}")


async def main(perfil: str, uuid: str | None = None) -> int:
    p = Placar()
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(Path(perfil).resolve()), channel="chrome",
            headless=False, no_viewport=True, args=["--start-maximized"])
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(sel.URL_FLOW, wait_until="domcontentloaded")
        await pagina.wait_for_selector(sel.CARD_PROJETO, timeout=30_000)

        await p.checar(pagina, "lista", "CARD_PROJETO", sel.CARD_PROJETO)
        await p.checar(pagina, "lista", "BOTAO_NOVO_PROJETO", sel.BOTAO_NOVO_PROJETO)
        await p.checar(pagina, "lista", "AVATAR_CONTA", sel.AVATAR_CONTA)

        # saldo: abre e fecha o menu do avatar. Dois cliques por leitura — e por isso
        # que o transporte le saldo uma vez por job, e nao a cada polling.
        await pagina.locator(sel.AVATAR_CONTA).first.click()
        await pagina.wait_for_timeout(1500)
        textos = await pagina.evaluate(dom.TEXTOS_CURTOS, 60)
        achou = any(re.search(sel.PADRAO_CREDITOS, t) for t in textos)
        print(f"[{'conta':9}] {'PADRAO_CREDITOS':24} {'OK' if achou else 'FALTA'}")
        if achou:
            p.ok += 1
        else:
            p.faltando.append("conta.PADRAO_CREDITOS")
        await pagina.keyboard.press("Escape")

        if uuid:
            # regra 3: entra-se CLICANDO no card, nunca por goto direto
            await pagina.locator(f'a[href*="{uuid}"]').first.click()
            await pagina.wait_for_timeout(8000)

            await p.checar(pagina, "projeto", "CAMPO_PROMPT", sel.CAMPO_PROMPT)
            await p.checar(pagina, "projeto", "BOTAO_ENVIAR", sel.BOTAO_ENVIAR)
            await p.checar(pagina, "projeto", "BOTAO_ANEXAR", sel.BOTAO_ANEXAR)
            await p.checar(pagina, "projeto", "BOTAO_MAIS_OPCOES", sel.BOTAO_MAIS_OPCOES)
            await p.checar(pagina, "projeto", "CHIP_CONFIG", sel.CHIP_CONFIG)
            await p.checar(pagina, "projeto", "CARD_GRADE", sel.CARD_GRADE, minimo=0)

            painel = await _abrir_painel(pagina)
            await p.checar(pagina, "chip", "PAINEL_CHIP", sel.PAINEL_CHIP)
            # A lista de MODOS depende do modo atual, e conferir os quatro de uma vez
            # num painel so era o proprio engano: com o chip em imagem, `crop_free` e
            # `chrome_extension` acusavam FALTA sem nada estar quebrado. `_medir_modos`
            # percorre a hierarquia e devolve o painel ja de volta em VIDEO.
            painel = await _medir_modos(pagina, p)
            # daqui para baixo o painel esta em video: e o modo que tem grupo de
            # duracao, e o que oferece so duas proporcoes
            do_video = sel.PROPORCOES_POR_MODO["t2v"]
            for razao, seletor in sel.PROPORCAO.items():
                # as outras tres faltarem AQUI e o esperado: elas so existem em imagem
                await p.checar(painel, "chip", f"PROPORCAO[{razao}]", seletor,
                               minimo=1 if razao in do_video else 0)
            for d, seletor in sel.DURACAO.items():
                await p.checar(painel, "chip", f"DURACAO[{d}]", seletor)
            for n, seletor in sel.QUANTIDADE.items():
                await p.checar(painel, "chip", f"QUANTIDADE[x{n}]", seletor)
            await p.checar(painel, "chip", "DROPDOWN_MODELO", sel.DROPDOWN_MODELO)

            await painel.locator(sel.DROPDOWN_MODELO).first.click()
            await pagina.wait_for_timeout(1500)
            for chave, seletor in sel.OPCAO_MODELO.items():
                # o dropdown lista so os modelos DO MODO ATUAL: os de imagem faltarem
                # aqui e esperado, nao quebra
                await p.checar(pagina, "modelos", chave, seletor, minimo=0)
            await pagina.keyboard.press("Escape")
            await pagina.keyboard.press("Escape")

            # o chip nao mostra o modelo; a verificacao le o dropdown com o painel
            # aberto. Imprimir aqui e o que confirma de onde esse texto sai.
            print("\ntexto do chip:", await _texto_do_chip(pagina))

            await _medir_custo(pagina, p)
            await _medir_anexo(pagina, p)

        await p.checar(pagina, "pendente", "AVISO_SEM_CREDITO", sel.AVISO_SEM_CREDITO)

        print(f"\nresumo: {p.ok} ok, {len(p.faltando)} faltando, "
              f"{len(p.pendentes)} pendentes")
        if p.pendentes:
            print("pendentes:", ", ".join(p.pendentes))
        if uuid and sel.CARD_GRADE == PENDENTE:
            await _medir_grade(pagina)
        if p.faltando:
            print("faltando:", ", ".join(p.faltando))
            await _candidatos(pagina)
        await ctx.close()
    return 1 if p.faltando else 0


async def _texto_do_chip(pagina) -> str:
    try:
        bruto = await pagina.locator(sel.CHIP_CONFIG).first.inner_text(timeout=5_000)
    except Exception as e:
        return f"<nao consegui ler: {type(e).__name__}>"
    return dom.seguro(bruto.replace("\n", " "))


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)))
