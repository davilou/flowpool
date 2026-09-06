"""Baixar SEM o download do Chrome: capturar o blob na pagina e ler os bytes.

POR QUE ISTO EXISTE — medido em 2026-09-06, Chrome 152.0.7977.76:

    [5.75s] download evento: camera_...mp4 | url: blob:https://flow.google.com/e758...
    [5.88s] !! pagina fechou
    [5.88s] !! contexto fechou
    pw:browser <process did exit: exitCode=3221225477>      (0xC0000005, access violation)

O Flow baixa montando um `<a download="nome.mp4" href="blob:...">` e chamando
`a.click()`. No instante em que o download NATIVO comeca, o processo inteiro do Chrome
trava. Nao e a pagina fechando nem o contexto sendo derrubado pela ultima aba: e o
navegador morrendo, e por isso nenhuma das defesas anteriores (aba de guarda, `path()`
em vez de `save_as`, traco desligado) resolvia de verdade — elas so ganhavam a corrida
as vezes (1 em 4 ontem, 0 em 22 hoje). Tambem nao e o modo de download do CDP: com
`Browser.setDownloadBehavior allow` para uma pasta propria o processo morreu igual.

O blob e so bytes na memoria da pagina. Entao: um init script guarda cada blob que
`URL.createObjectURL` cria, cancela o clique do `<a download>` (sem clique nativo, sem
download nativo, sem trava) e registra a URL e o nome. O Python le os bytes em pedacos
por `evaluate` e grava o arquivo. Medido: 862.943 bytes de `video/mp4` validos, Chrome
vivo, sessao fechada com codigo 0.

`revokeObjectURL` e adiado porque o Flow revoga a URL logo depois do clique — antes de
termos lido o blob.
"""

from __future__ import annotations

import base64
from pathlib import Path

# Instalado por `sessao.abrir_contexto` em TODO contexto, antes da primeira navegacao.
INTERCEPTADOR = r"""
(() => {
  const blobs = new Map();
  const cria = URL.createObjectURL.bind(URL);
  URL.createObjectURL = (o) => { const u = cria(o); if (o instanceof Blob) blobs.set(u, o); return u; };
  const revoga = URL.revokeObjectURL.bind(URL);
  URL.revokeObjectURL = (u) => { setTimeout(() => { revoga(u); blobs.delete(u); }, 120000); };
  window.__flowpool = { blobs, capturados: [] };
  const guardar = (url, nome, via) => { window.__flowpool.capturados.push({ url, nome, via }); };
  // clique de verdade OU MouseEvent sintetico num <a download> de blob: cancela o
  // padrao antes de o navegador iniciar o download
  document.addEventListener('click', (ev) => {
    const a = ev.target && ev.target.closest ? ev.target.closest('a[download]') : null;
    if (a && String(a.href).startsWith('blob:')) {
      ev.preventDefault(); ev.stopImmediatePropagation(); guardar(a.href, a.download, 'evento-click');
    }
  }, true);
  // `a.click()` programatico num <a> fora do documento nao passa pelo listener acima
  const clique = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (String(this.href).startsWith('blob:')) { guardar(this.href, this.download || '', 'a.click()'); return; }
    return clique.call(this);
  };
  const abrir = window.open.bind(window);
  window.open = (u, ...r) => {
    if (String(u).startsWith('blob:')) { guardar(String(u), '', 'window.open'); return null; }
    return abrir(u, ...r);
  };
})();
"""

_ZERAR = "() => { if (window.__flowpool) window.__flowpool.capturados = []; }"
_CAPTURADOS = "() => window.__flowpool ? window.__flowpool.capturados : null"
_TAMANHO = "({url}) => { const b = window.__flowpool.blobs.get(url); return b ? {tamanho: b.size, tipo: b.type} : null; }"
# Um pedaco por chamada. 2 MB em base64 sao ~2,7 MB de texto por mensagem do CDP —
# longe de qualquer teto, e um video de 10 MB vira cinco chamadas.
_PEDACO = r"""
async ({url, inicio, fim}) => {
  const b = window.__flowpool.blobs.get(url);
  if (!b) return null;
  const buf = new Uint8Array(await b.slice(inicio, fim).arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(s);
}
"""
TAMANHO_PEDACO = 2 * 1024 * 1024


async def instalar(ctx) -> None:
    """Init script no contexto: vale para toda pagina e toda navegacao dali em diante."""
    await ctx.add_init_script(INTERCEPTADOR)


async def instalado(pagina) -> bool:
    return bool(await pagina.evaluate("() => !!window.__flowpool"))


async def zerar(pagina) -> None:
    """Antes do clique: capturas de um download anterior nao podem contar como este."""
    await pagina.evaluate(_ZERAR)


async def capturados(pagina) -> list[dict] | None:
    """`None` = o interceptador nao esta nesta pagina (pagina aberta antes dele)."""
    return await pagina.evaluate(_CAPTURADOS)


async def gravar_blob(pagina, url: str, destino: Path,
                      pedaco: int = TAMANHO_PEDACO) -> int:
    """Le o blob `url` da pagina em pedacos e grava em `destino`. Devolve os bytes."""
    meta = await pagina.evaluate(_TAMANHO, {"url": url})
    if not meta:
        raise RuntimeError(f"o blob {url[:60]} ja nao esta na pagina")
    total = int(meta["tamanho"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    escritos = 0
    with destino.open("wb") as f:
        inicio = 0
        while inicio < total:
            fim = min(inicio + pedaco, total)
            b64 = await pagina.evaluate(_PEDACO, {"url": url, "inicio": inicio, "fim": fim})
            if b64 is None:
                raise RuntimeError(f"o blob {url[:60]} sumiu no meio da leitura")
            dados = base64.b64decode(b64)
            f.write(dados)
            escritos += len(dados)
            inicio = fim
    if escritos != total:
        raise RuntimeError(f"blob de {total} bytes, gravados {escritos}")
    return escritos
