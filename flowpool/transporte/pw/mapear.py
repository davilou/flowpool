"""Abre o Flow num Chrome real com perfil dedicado, para login manual e inspecao.

Uso:
    python -m flowpool.transporte.pw.mapear <pasta-do-perfil>

A janela fica aberta ate voce fechar. O perfil persiste, entao o login so acontece
uma vez — e essa mesma pasta serve depois como perfil de uma conta do pool.

Por que Chrome instalado e nao o Chromium do Playwright: o stack de auth do Google
recusa o Chromium empacotado. E por que nao headless: varias partes da interface do
Flow (o portao do aviso de privacidade, a lista de projetos) dependem de
IntersectionObserver, que nao dispara em pagina que nao compoe quadros.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

FLOW = "https://labs.google/fx/pt/tools/flow"

# Coletado do DOM ao vivo. Serve de ponto de partida para seletores.py.
SONDA = r"""
() => {
  const txt = e => (e.innerText || '').trim().slice(0, 60);
  const sel = e => {
    if (e.id) return '#' + e.id;
    const cls = (e.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2);
    return e.tagName.toLowerCase() + (cls.length ? '.' + cls.join('.') : '');
  };
  return {
    url: location.href,
    visivel: document.visibilityState,
    dialogo: (() => {
      const d = document.querySelector('[role=dialog],[aria-modal=true],dialog');
      return d ? {texto: txt(d), botoes: [...d.querySelectorAll('button')].map(txt)} : null;
    })(),
    botoes: [...document.querySelectorAll('button')]
      .filter(b => txt(b)).map(b => ({t: txt(b), s: sel(b), aria: b.getAttribute('aria-label')})),
    links: [...document.querySelectorAll('a[href]')]
      .map(a => ({t: txt(a), href: a.getAttribute('href')}))
      .filter(x => x.href && x.href.includes('/flow/')),
    campos: [...document.querySelectorAll('textarea,input,[contenteditable=true]')]
      .map(e => ({tag: e.tagName, tipo: e.getAttribute('type'), ph: e.getAttribute('placeholder'), s: sel(e)})),
    uploads: [...document.querySelectorAll('input[type=file]')]
      .map(e => ({s: sel(e), multiplo: e.multiple, aceita: e.getAttribute('accept')})),
  };
}
"""


async def main(perfil: str) -> None:
    caminho = Path(perfil).resolve()
    caminho.mkdir(parents=True, exist_ok=True)
    print(f"perfil: {caminho}")

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(caminho),
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        pagina = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await pagina.goto(FLOW, wait_until="domcontentloaded")

        print("\nA janela do Chrome esta aberta.")
        print("Loge na conta se pedir, e passe pelas telas de boas-vindas.")
        print("A sonda roda a cada 5s e grava em sonda.json.\n")

        saida = caminho.parent / "sonda.json"
        while True:
            try:
                dados = await pagina.evaluate(SONDA)
            except Exception as e:
                print(f"[sonda] {type(e).__name__}: {e}")
                await asyncio.sleep(5)
                continue
            saida.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
            d = dados.get("dialogo")
            print(f"[{dados['visivel']}] {dados['url']}"
                  f" | botoes={len(dados['botoes'])}"
                  f" campos={len(dados['campos'])}"
                  f" uploads={len(dados['uploads'])}"
                  f"{' | DIALOGO: ' + d['texto'][:40] if d else ''}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "flowpool/perfis/mapeamento"))
