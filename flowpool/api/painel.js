/* Painel do FlowPool. Cliente da propria API — nao fala com o Flow e nao tem regra de
   negocio: tudo o que o servidor recusaria some da tela antes de virar um 422.

   A estrutura e a do proprio Flow: galeria no centro, barra de prompt flutuando no
   rodape, operacao do pool fora do caminho. O campo de texto e SO O PROMPT — nao ha
   agente conversacional aqui. Quem decide modo, modelo, proporcao, duracao e quantidade
   e o chip ao lado, que e determinista e mostra o estado inteiro sem ser aberto. */

"use strict";

const $ = (id) => document.getElementById(id);

// intervalo do poll. A fila anda devagar e o servico e local: alguns segundos bastam,
// e WebSocket seria complexidade sem retorno.
const INTERVALO_MS = 4000;

// teto do fetch comum. Rotas de leitura respondem em milissegundos.
const TETO_PADRAO_MS = 15000;

// teto do cadastro e da revalidacao. Do outro lado o servidor espera ATE 900s a pessoa
// logar, mais o tempo de liberar o perfil e validar a sessao. O teto do cliente e
// deliberadamente maior do que o do servidor: assim quem desiste primeiro e o servidor,
// que devolve uma mensagem dizendo o que faltou, em vez do navegador abortando calado.
const TETO_SESSAO_MS = 1200000;

const PROPORCOES_VIDEO = ["9:16", "16:9"];
const PROPORCOES_IMAGEM = ["9:16", "16:9", "4:3", "1:1", "3:4"];
const MODELOS_VIDEO = ["omni-flash", "veo-lite", "veo-fast", "veo-quality"];
const MODELOS_IMAGEM = ["nano-banana"];
const DURACOES = ["4s", "6s", "8s", "10s"];
const MAX_REFERENCIAS = 3;

// Teto da colagem, o MESMO do servidor (`colar.TETO_BYTES`). Repetido aqui de proposito:
// recusar 40 MB antes de subir e melhor do que subir para tomar 413 no fim.
const TETO_COLAGEM = 20 * 1024 * 1024;
// A colagem sobe bytes e a busca por URL sai para a internet — os dois demoram mais do
// que uma leitura, e nenhum dos dois chega perto do teto de sessao.
const TETO_COLAGEM_MS = 60000;

// O nome curto do chip. Precisa SEPARAR os tres modos de video — um chip que diz so
// "Vídeo" nos tres deixaria a pessoa enviar em `frames2v` achando que estava em texto
// puro, e o transporte anexaria arquivo nenhum. O nome por extenso de cada modo esta
// nos proprios botoes do popover, no HTML.
const CHIP_DO_MODO = {
  "t2v": "Vídeo", "frames2v": "Vídeo·frames", "ingredients2v": "Vídeo·refs",
  "imagem": "Imagem",
};

// Quantos arquivos cada modo anexa, e o que cada um significa. O transporte usa UM
// `input[type=file]` para tudo e deixa o MODO decidir o papel de cada arquivo — em
// `frames2v` viram frame inicial e final, em `ingredients2v` e em `imagem` viram
// referencias. Em `t2v` o transporte nao anexa nada, e por isso o `+` la troca o modo.
//
// IMAGEM ENTROU AQUI, e nao e detalhe: o Flow gera imagem A PARTIR de uma foto (e o
// Nano Banana), e o pipeline do usuario COMECA nisso — foto da roupa -> imagem da
// modelo vestida. Enquanto imagem nao estava neste mapa, colar uma referencia trocava
// o modo para video sozinho, e trocar de volta para imagem apagava a referencia: o
// vaivem que a pessoa relatou como "colo a imagem e ela some".
const PAPEIS = {
  "frames2v": ["frame inicial", "frame final"],
  "ingredients2v": ["referência 1", "referência 2", "referência 3"],
  "imagem": ["referência 1", "referência 2", "referência 3"],
};

// ACEITAR arquivo e EXIGIR arquivo sao coisas diferentes. `frames2v` sem frame inicial
// e `ingredients2v` sem referencia nao tem o que gerar; imagem sem referencia e o caso
// comum — geracao por texto puro. Confundir as duas transformaria toda geracao de
// imagem num erro antes de sair. Mesma regra do servidor (`tipos.exige_arquivo`).
const EXIGEM_ANEXO = ["frames2v", "ingredients2v"];

function exigeAnexo() { return EXIGEM_ANEXO.includes(cfg.modo); }

let contas = [];
let jobs = [];
let sessaoEmCurso = false;   // cadastro OU revalidacao: os dois abrem Chrome e bloqueiam
let cronometro = null;
let assinaturaGaleria = null;   // evita redesenhar a grade quando nada mudou
const selecao = new Set();

// O que o chip mostra e o que vai no corpo do POST /jobs.
const cfg = {
  modo: "t2v", modelo: "omni-flash", proporcao: "9:16", duracao: "4s", quantidade: 1,
};

// Caminhos escolhidos no `+` ou colados. O papel de cada um sai do modo, nao da ordem
// de escolha.
let anexos = [];

// Colagens em voo. Nao sao caminhos ainda — viram tira "carregando" na barra ate o
// servidor devolver o caminho, e contam no teto para dois Ctrl+V seguidos nao passarem
// de tres.
let subindo = 0;

let produto = null;
let jobAberto = null;

/* ---------------- utilidades ---------------- */

function esc(v) {
  return String(v === null || v === undefined ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function mensagemDoErro(status, corpo) {
  const d = corpo && corpo.detail;
  if (typeof d === "string") return d;
  // 422 do Pydantic: lista de {loc, msg}. Sem esta traducao o usuario ve "[object Object]".
  if (Array.isArray(d)) {
    return d.map((e) => {
      const campo = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : "";
      return campo ? `${campo}: ${e.msg}` : e.msg;
    }).join("\n");
  }
  return `o serviço respondeu ${status}`;
}

async function api(caminho, opcoes = {}, teto = TETO_PADRAO_MS) {
  const ctrl = new AbortController();
  const relogio = setTimeout(() => ctrl.abort(), teto);
  // `bruto` e o corpo binario de `/colar`: os bytes da imagem colada viajam crus, sem
  // multipart e sem base64 — o servidor le a assinatura deles direto.
  const bruto = opcoes.bruto !== undefined && opcoes.bruto !== null;
  try {
    const r = await fetch(caminho, {
      headers: opcoes.corpo ? { "Content-Type": "application/json" }
             : bruto ? { "Content-Type": "application/octet-stream" } : undefined,
      method: opcoes.metodo || "GET",
      body: opcoes.corpo ? JSON.stringify(opcoes.corpo)
          : bruto ? opcoes.bruto : undefined,
      signal: ctrl.signal,
    });
    const texto = await r.text();
    const corpo = texto ? JSON.parse(texto) : null;
    if (!r.ok) throw new Error(mensagemDoErro(r.status, corpo));
    return corpo;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("o serviço não respondeu a tempo");
    throw e;
  } finally {
    clearTimeout(relogio);
  }
}

function mostrar(el, texto) {
  el.textContent = texto;
  el.classList.remove("oculto");
}

function esconder(el) { el.classList.add("oculto"); }

function urlMiniatura(caminho) {
  return `/miniatura?caminho=${encodeURIComponent(caminho)}`;
}

function urlMidia(caminho) {
  return `/midia?caminho=${encodeURIComponent(caminho)}`;
}

function urlBaixar(caminho) {
  return `/baixar?caminho=${encodeURIComponent(caminho)}`;
}

function tamanhoLegivel(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function ehVideo() { return cfg.modo !== "imagem"; }

function ehVideoDoJob(j) { return j.modo !== "imagem"; }

function emailDaConta(id) {
  const c = contas.find((x) => x.id === id);
  return c ? c.email : `#${id}`;
}

/* ---------------- L4: a faixa de cima ---------------- */

function pintarSaude(s) {
  $("credito-total").textContent = s.credito_total;
  $("contas-vivas").textContent = s.contas_vivas;
  $("fila-pendente").textContent = s.fila_pendente;

  const faixa = $("faixa-expiradas");
  const expiradas = s.contas_expiradas || [];
  if (expiradas.length === 0) { esconder(faixa); return; }

  faixa.classList.remove("oculto");
  $("titulo-expiradas").textContent = expiradas.length === 1
    ? "1 sessão caída:" : `${expiradas.length} sessões caídas:`;

  // o botao precisa do id da conta, e /saude devolve so o email: o cruzamento e com a
  // ultima lista de contas. Enquanto ela nao chegou, o chip aparece sem botao.
  $("lista-expiradas").innerHTML = expiradas.map((email) => {
    const c = contas.find((x) => x.email === email);
    const botao = c
      ? `<button class="mini alerta acao-revalidar" data-id="${c.id}" data-email="${esc(email)}"
                 ${sessaoEmCurso ? "disabled" : ""}>Revalidar</button>`
      : "";
    return `<span class="chip-expirada"><span class="email">${esc(email)}</span>${botao}</span>`;
  }).join("");
}

/* ---------------- L1: a galeria ---------------- */

function proporcaoCss(p) {
  // "9:16" -> "9 / 16". Card com a forma do arquivo: 9:16 fica alto, 16:9 fica largo.
  const [a, b] = String(p || "9:16").split(":");
  return `${Number(a) || 9} / ${Number(b) || 16}`;
}

function resumoConfig(j) {
  return [j.modo === "imagem" ? "imagem" : "vídeo", j.modelo,
          j.proporcao, j.modo === "imagem" ? "" : j.duracao,
          j.quantidade > 1 ? `x${j.quantidade}` : ""].filter(Boolean).join(" · ");
}

function cardDeMidia(j) {
  const url = urlMidia(j.caminho);
  if (!ehVideoDoJob(j)) {
    return `<img src="${esc(url)}" alt="" loading="lazy">`;
  }
  // `preload="metadata"` desenha o primeiro quadro sem baixar o clipe inteiro — e por
  // isso que `/midia` responde a `Range`. O `#t=0.1` e o que faz o Chrome SEEKAR ate um
  // quadro de verdade: sem ele, o poster de um mp4 costuma sair preto.
  return `<video src="${esc(url)}#t=0.1" preload="metadata" muted playsinline
                 loop disablepictureinpicture></video>
          <span class="selo">▶ ${esc(j.duracao)}</span>`;
}

function card(j) {
  const marcado = selecao.has(j.id);
  const rodape = `
    <div class="rodape-card">
      <span class="modo" title="${esc(resumoConfig(j))}">${esc(j.modo)} · ${esc(j.modelo)}</span>
      <span class="credito">${j.custo_estimado === null ? "—" : `${j.custo_estimado} cr`}</span>
    </div>`;

  let dentro;
  if (j.caminho) {
    dentro = cardDeMidia(j) + `
      <label class="marcar" title="selecionar para baixar">
        <input type="checkbox" class="marcar-job" data-id="${j.id}" ${marcado ? "checked" : ""}>
      </label>`;
  } else if (j.estado === "falhou") {
    dentro = `<div class="card-texto">
        <span class="pilula falhou">falhou</span>
        <span class="prompt-curto">${esc(j.erro || "sem detalhe do erro")}</span>
        <span class="leve">${esc(j.nome)}</span>
      </div>`;
  } else if (j.estado === "pronto") {
    // pronto sem caminho: o job foi gerado com OUTRA raiz de projeto, ou o arquivo
    // saiu do lugar. Card mudo seria pior do que card que diz o que houve.
    dentro = `<div class="card-texto">
        <span class="pilula pronto">pronto</span>
        <span class="prompt-curto">O arquivo está fora da raiz do projeto — não dá para
          mostrar nem baixar por aqui.</span>
        <span class="leve">${esc(j.arquivo || j.nome)}</span>
      </div>`;
  } else {
    const rodando = j.estado === "rodando";
    dentro = `<div class="card-texto">
        <span class="pilula ${esc(j.estado)}">${rodando ? '<span class="girando"></span>' : ""}${esc(j.estado)}</span>
        <span class="prompt-curto">${esc(j.prompt)}</span>
        <span class="leve">${esc(j.nome)}</span>
      </div>`;
  }

  return `<article class="card ${esc(j.estado)}${j.caminho ? "" : " sem-arquivo"}${marcado ? " marcado" : ""}"
                   data-id="${j.id}" style="--prop: ${proporcaoCss(j.proporcao)}">
    <div class="moldura">${dentro}</div>${rodape}
  </article>`;
}

/* A grade so e redesenhada quando ALGO MUDOU. O poll roda a cada 4s, e reescrever o
   innerHTML a cada volta faria todo `<video>` recarregar o primeiro quadro — a galeria
   piscaria sozinha, e o card sob o mouse pararia de tocar no meio. */
function assinatura(lista) {
  return lista.map((j) => `${j.id}:${j.estado}:${j.caminho || ""}:${j.custo_estimado}`).join("|");
}

/* Cada card ocupa quantas linhas de 4px a sua altura pedir. E o que fecha os buracos
   que uma grade comum abriria entre um card 16:9 e um 9:16 na mesma fileira. */
function medirGrade() {
  const g = $("galeria");
  const estilo = getComputedStyle(g);
  const linha = parseFloat(estilo.gridAutoRows) || 4;
  const vao = parseFloat(estilo.rowGap) || 0;
  g.querySelectorAll(".card").forEach((c) => {
    const h = c.getBoundingClientRect().height;
    // altura 0 e a galeria que ainda nao foi disposta (aba em segundo plano, por
    // exemplo). Gravar `span 1` ali achataria todo card ate a proxima medicao.
    if (h > 0) c.style.gridRowEnd = `span ${Math.ceil((h + vao) / (linha + vao))}`;
  });
}

function pintarGaleria(lista) {
  const nova = assinatura(lista) + `#${[...selecao].sort().join(",")}`;
  if (nova === assinaturaGaleria) return;
  assinaturaGaleria = nova;

  const g = $("galeria");
  if (!lista.length) {
    g.innerHTML = `<div class="galeria-vazia">
      <strong>Nada gerado ainda.</strong>
      Escreva o prompt ali embaixo, confira o chip e envie.</div>`;
    return;
  }
  g.innerHTML = lista.map(card).join("");
  medirGrade();
}

function atualizarBarraSelecao() {
  const barra = $("barra-selecao");
  if (!selecao.size) { esconder(barra); return; }
  barra.classList.remove("oculto");
  $("selecao-conta").textContent = selecao.size === 1
    ? "1 selecionado" : `${selecao.size} selecionados`;
}

/* L2: baixar. Os arquivos JA estao em disco — o transporte os pos em `produto/clipes/`.
   O que faltava era alcanca-los sem sair do painel. */
function baixarSelecao() {
  const caminhos = jobs.filter((j) => selecao.has(j.id) && j.caminho).map((j) => j.caminho);
  if (!caminhos.length) return;
  if (caminhos.length === 1) { window.location.href = urlBaixar(caminhos[0]); return; }
  const q = caminhos.map((c) => `caminho=${encodeURIComponent(c)}`).join("&");
  window.location.href = `/baixar-lote?${q}`;
}

/* ---------------- visualizador ---------------- */

function abrirVisor(id) {
  const j = jobs.find((x) => x.id === id);
  if (!j) return;
  jobAberto = id;
  esconder($("visor-aviso"));

  $("visor-titulo").textContent = j.nome || `job ${j.id}`;
  $("visor-prompt").textContent = j.prompt || "—";
  $("visor-config").textContent = resumoConfig(j) +
    (j.custo_estimado === null ? "" : ` · ${j.custo_estimado} créditos`);
  $("visor-origem").textContent =
    `${j.produto || "—"} · ${j.conta_id === null ? "nenhuma conta ainda" : emailDaConta(j.conta_id)}`;

  $("visor-linha-erro").classList.toggle("oculto", !j.erro);
  $("visor-erro").textContent = j.erro || "";

  // O nome vira o nome do ARQUIVO em disco. Depois que o arquivo existe, trocar o campo
  // faria o job mentir — e o servidor recusa com 409. Some daqui pelo mesmo motivo.
  const podeRenomear = j.estado === "pendente";
  $("visor-linha-nome").classList.toggle("oculto", !podeRenomear);
  $("visor-nome").value = j.nome || "";

  $("visor-linha-caminho").classList.toggle("oculto", !j.arquivo);
  $("visor-caminho").value = j.arquivo || "";

  $("visor-baixar").classList.toggle("oculto", !j.caminho);
  if (j.caminho) $("visor-baixar").href = urlBaixar(j.caminho);
  $("visor-cancelar").classList.toggle("oculto", j.estado !== "pendente");

  const palco = $("visor-palco");
  if (j.caminho) {
    palco.innerHTML = ehVideoDoJob(j)
      ? `<video src="${esc(urlMidia(j.caminho))}" controls autoplay loop playsinline></video>`
      : `<img src="${esc(urlMidia(j.caminho))}" alt="">`;
  } else {
    palco.innerHTML = `<div class="sem-midia">${
      j.estado === "falhou" ? "Esta geração não produziu arquivo."
                            : "Ainda não há arquivo — o job está " + esc(j.estado) + "."}</div>`;
  }
  $("visor").classList.remove("oculto");
}

function fecharVisor() {
  // zera o `src` antes de esconder: sem isso o video continua tocando (e baixando) atras
  // do modal fechado
  $("visor-palco").innerHTML = "";
  $("visor").classList.add("oculto");
  jobAberto = null;
}

async function renomear() {
  const nome = $("visor-nome").value.trim();
  if (!nome || jobAberto === null) return;
  try {
    await api(`/jobs/${jobAberto}`, { metodo: "PATCH", corpo: { nome } });
    esconder($("visor-aviso"));
    $("visor-titulo").textContent = nome;
    await atualizar();
  } catch (e) {
    mostrar($("visor-aviso"), e.message);
  }
}

async function copiarCaminho() {
  const campo = $("visor-caminho");
  campo.select();
  try {
    await navigator.clipboard.writeText(campo.value);
    $("visor-copiar").textContent = "Copiado";
    setTimeout(() => { $("visor-copiar").textContent = "Copiar"; }, 1200);
  } catch (e) {
    // sem permissao de area de transferencia o texto ja ficou SELECIONADO acima, entao
    // Ctrl+C resolve — dizer isso e melhor do que um erro que nao leva a lugar nenhum
    mostrar($("visor-aviso"), "O navegador não deixou copiar sozinho; o caminho está "
      + "selecionado, use Ctrl+C.");
  }
}

/* ---------------- L3: o chip ---------------- */

function textoDoChip() {
  const partes = [CHIP_DO_MODO[cfg.modo], cfg.modelo];
  if (ehVideo()) partes.push(cfg.duracao);
  partes.push(cfg.proporcao);
  if (!ehVideo() && cfg.quantidade > 1) partes.push(`x${cfg.quantidade}`);
  return partes.join(" · ");
}

function opcoes(alvo, valores, atual, chave) {
  alvo.innerHTML = valores.map((v) =>
    `<button type="button" data-chave="${chave}" data-valor="${esc(v)}"
             class="${String(v) === String(atual) ? "ativo" : ""}">${esc(v)}</button>`).join("");
}

/* Some da tela tudo o que a API recusaria. Melhor a opcao nao existir do que o usuario
   escolher e tomar 422: duracao so em video, quantidade acima de 1 so em imagem,
   proporcao de video so 9:16 ou 16:9, e modelo de imagem separado do de video. */
function ajustarChip() {
  const video = ehVideo();
  const modelos = video ? MODELOS_VIDEO : MODELOS_IMAGEM;
  const proporcoes = video ? PROPORCOES_VIDEO : PROPORCOES_IMAGEM;
  if (!modelos.includes(cfg.modelo)) cfg.modelo = modelos[0];
  if (!proporcoes.includes(cfg.proporcao)) cfg.proporcao = proporcoes[0];
  if (video) cfg.quantidade = 1;

  document.querySelectorAll("#pop-tipo button").forEach((b) => {
    b.classList.toggle("ativo", b.dataset.modo === cfg.modo);
  });
  opcoes($("pop-modelo"), modelos, cfg.modelo, "modelo");
  opcoes($("pop-proporcao"), proporcoes, cfg.proporcao, "proporcao");
  opcoes($("pop-duracao"), DURACOES, cfg.duracao, "duracao");
  opcoes($("pop-quantidade"), [1, 2, 3, 4], cfg.quantidade, "quantidade");

  $("grupo-duracao").classList.toggle("oculto", !video);
  $("grupo-quantidade").classList.toggle("oculto", video);

  $("pop-dica").textContent = video
    ? "Vídeo sai sempre em uma geração — take é escolha sua, não sorteio — e o chip do Flow "
      + "só oferece 9:16 e 16:9. A duração muda o preço: 4s custa 7 créditos e 8s custa 12 "
      + "no Omni Flash."
    : "Imagem não tem duração (o painel do Flow não oferece o grupo nesse modo) e aceita até "
      + "4 variações na mesma geração — cada uma custa cheio. Aceita também até 3 "
      + "referências: é assim que a foto da roupa vira a imagem da modelo vestida.";

  $("chip-texto").textContent = textoDoChip();

  // O `+` fica clicavel em TODOS os modos. Em `t2v` — o unico que nao anexa nada — ele
  // troca o modo para "vídeo — referências", que e o que a pessoa quis dizer ao anexar.
  // Nos outros tres o modo FICA COMO ESTA: era o vaivem antigo que trocava imagem por
  // video sozinho e fazia a referencia sumir na volta.
  $("botao-mais").title = PAPEIS[cfg.modo]
    ? "anexar imagens do disco (ou cole com Ctrl+V, ou arraste para cá)"
    : "anexar referências — o tipo passa para “vídeo — referências”";

  ajustarAnexos();
}

/* Os anexos sobrevivem a troca de modo, mas o TETO muda: frames2v usa dois arquivos
   (inicial e final) e ingredients2v usa tres. O excedente cai fora aqui, e nao no 422. */
function ajustarAnexos() {
  const teto = (PAPEIS[cfg.modo] || []).length;
  if (anexos.length > teto) anexos = anexos.slice(0, teto);
  pintarAnexos();
}

function pintarAnexos() {
  const papeis = PAPEIS[cfg.modo] || [];
  const prontas = anexos.map((c, i) => {
    const nome = c.split("/").pop();
    return `<span class="tira" title="${esc(c)}">
      <img src="${esc(urlMiniatura(c))}" alt="" loading="lazy">
      <span><span class="tira-nome">${esc(nome)}</span>
        <span class="tira-papel">${esc(papeis[i] || "")}</span></span>
      <button type="button" class="tira-x" data-caminho="${esc(c)}"
              title="tirar">&times;</button></span>`;
  }).join("");

  // No lugar da miniatura, enquanto a imagem colada sobe. Sem isto o Ctrl+V nao teria
  // resposta nenhuma por um ou dois segundos, e a pessoa colaria de novo.
  const emVoo = Array.from({ length: subindo }, (_, i) => `
    <span class="tira subindo"><span class="girando"></span>
      <span><span class="tira-nome">anexando…</span>
        <span class="tira-papel">${esc(papeis[anexos.length + i] || "")}</span></span>
    </span>`).join("");

  $("tira-anexos").innerHTML = prontas + emVoo;
}

function posicionar(pop, botao) {
  pop.classList.remove("oculto");
  const r = botao.getBoundingClientRect();
  const largura = pop.offsetWidth;
  const altura = pop.offsetHeight;
  // ancorado ACIMA do chip, porque a barra vive no rodape; preso as bordas da janela
  // para nao nascer metade fora da tela num navegador estreito
  const esquerda = Math.min(Math.max(8, r.left + r.width / 2 - largura / 2),
                            window.innerWidth - largura - 8);
  pop.style.left = `${esquerda}px`;
  pop.style.top = `${Math.max(8, r.top - altura - 8)}px`;
}

function fecharPopovers() {
  ["pop-chip", "pop-produto"].forEach((id) => esconder($(id)));
  $("chip").classList.remove("aberto");
  $("chip-produto").classList.remove("aberto");
}

function alternarPopover(popId, botaoId) {
  const pop = $(popId);
  const aberto = !pop.classList.contains("oculto");
  fecharPopovers();
  if (aberto) return;
  posicionar(pop, $(botaoId));
  $(botaoId).classList.add("aberto");
}

/* ---------------- L3: o produto ---------------- */

function produtosConhecidos() {
  return [...new Set(jobs.map((j) => j.produto).filter(Boolean))];
}

function pintarProdutos() {
  const lista = produtosConhecidos();
  $("lista-produtos").innerHTML = lista.length
    ? lista.map((p) => `<button type="button" class="escolher-produto ${
        p === produto ? "ativo" : ""}" data-produto="${esc(p)}">${esc(p)}</button>`).join("")
    : `<div class="vazio">Nenhum produto ainda — escreva o primeiro abaixo.</div>`;
}

function definirProduto(p) {
  produto = p || null;
  $("chip-produto-texto").textContent = produto || "escolher produto";
  $("chip-produto").classList.toggle("faltando", !produto);
  try { localStorage.setItem("flowpool:produto", produto || ""); } catch (e) { /* modo privado */ }
}

/* O nome do arquivo sai automatico. `<produto>-<n>` com o primeiro `n` livre: repetir um
   nome ja usado faria a geracao nova SOBRESCREVER o arquivo da antiga em disco, sem
   aviso. Continua editavel no visualizador enquanto o job esta na fila. */
function nomeAutomatico(p) {
  const base = p.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "job";
  const usados = new Set(jobs.filter((j) => j.produto === p).map((j) => j.nome));
  let n = 1;
  while (usados.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

/* ---------------- L3: enviar ---------------- */

function corpoDoJob() {
  const corpo = {
    modo: cfg.modo,
    modelo: cfg.modelo,
    prompt: $("job-prompt").value.trim(),
    produto,
    nome: nomeAutomatico(produto),
    proporcao: cfg.proporcao,
    quantidade: ehVideo() ? 1 : cfg.quantidade,
  };
  // `duracao` so entra em video: mandar o campo em imagem e 422 por si so, e o teste do
  // servidor e pelo campo TER VINDO no corpo, nao pelo valor.
  if (ehVideo()) corpo.duracao = cfg.duracao;
  if (cfg.modo === "frames2v") {
    corpo.frame_inicial = anexos[0] || "";
    if (anexos[1]) corpo.frame_final = anexos[1];
  }
  // referencias valem em `ingredients2v` E em `imagem` — nos dois o teto e tres. Em
  // imagem a lista pode ser vazia, e vazia significa geracao por texto puro.
  if (PAPEIS[cfg.modo] && cfg.modo !== "frames2v") {
    corpo.referencias = anexos.slice(0, MAX_REFERENCIAS);
  }
  return corpo;
}

async function enviar(ev) {
  ev.preventDefault();
  esconder($("erro-job"));
  if (!$("job-prompt").value.trim()) {
    mostrar($("erro-job"), "Escreva o prompt antes de enviar.");
    return;
  }
  if (!produto) {
    mostrar($("erro-job"), "Escolha o produto: é a pasta onde o arquivo cai e o nome do "
      + "projeto dentro do Flow.");
    alternarPopover("pop-produto", "chip-produto");
    return;
  }
  // Mesma regra do servidor, dita antes: `frames2v` exige o frame inicial e
  // `ingredients2v` exige ao menos uma referencia. Tomar 422 depois de escrever o
  // prompt inteiro seria o pior lugar para descobrir isso.
  if (subindo) {
    // enviar com uma colagem no meio do caminho mandaria o job SEM aquela referencia,
    // e o card sairia diferente do que a barra estava mostrando na hora do Enter
    mostrar($("erro-job"), "Espere a imagem terminar de anexar — falta um instante.");
    return;
  }
  if (!anexos.length && exigeAnexo()) {
    mostrar($("erro-job"), cfg.modo === "frames2v"
      ? "Este modo gera a partir de frames: use o + para escolher ao menos o frame inicial."
      : "Este modo gera a partir de referências: use o + para escolher ao menos uma.");
    return;
  }
  const botao = $("botao-enviar");
  botao.disabled = true;
  try {
    await api("/jobs", { metodo: "POST", corpo: corpoDoJob() });
    $("job-prompt").value = "";
    crescerPrompt();
    await atualizar();
  } catch (e) {
    mostrar($("erro-job"), e.message);
  } finally {
    botao.disabled = false;
  }
}

/* Textarea que cresce com o texto ate um teto, e depois rola. */
function crescerPrompt() {
  const t = $("job-prompt");
  t.style.height = "auto";
  t.style.height = `${Math.min(t.scrollHeight, 168)}px`;
}

async function cancelar(id) {
  try {
    await api(`/jobs/${id}`, { metodo: "DELETE" });
    if (jobAberto === id) fecharVisor();
    await atualizar();
  } catch (e) {
    mostrar($(jobAberto === id ? "visor-aviso" : "erro-job"), e.message);
  }
}

/* ---------------- L4: contas, na gaveta ---------------- */

function blocoConta(c) {
  const precisa = c.saude !== "viva";
  const tiers = ["free", "pro", "ultra"].map(
    (t) => `<option value="${t}" ${t === c.tier ? "selected" : ""}>${t}</option>`).join("");

  return `
  <div class="conta ${precisa ? "precisa-acao" : ""} ${c.ativa ? "" : "inativa"}" data-id="${c.id}">
    <div class="conta-cabeca">
      <span class="email-conta">${esc(c.email)}</span>
      <span class="pilula ${esc(c.saude)}">${esc(c.saude)}</span>
      ${c.falhas > 0 ? `<span class="falhas" title="falhas consecutivas">${c.falhas} falhas</span>` : ""}
      <span class="saldo ${c.saldo === 0 ? "zerado" : ""}">${c.saldo}${
        c.saldo_confiavel ? "" :
        `<span class="duvida" title="saldo estimado desde a última leitura no Flow">~</span>`}</span>
    </div>

    <div class="conta-linha">
      <select class="acao-tier" data-id="${c.id}">${tiers}</select>
      <label class="interruptor" title="${c.ativa ? "desativar" : "ativar"}">
        <input type="checkbox" class="acao-ativa" data-id="${c.id}" ${c.ativa ? "checked" : ""}>
        <span class="trilho"></span>${c.ativa ? "ativa" : "inativa"}
      </label>
      <button class="mini acao-saldo" data-id="${c.id}" data-email="${esc(c.email)}"
              title="lê o saldo agora no Flow — abre uma janela e não gasta crédito">Ler saldo</button>
      <button class="mini ${precisa ? "alerta" : ""} acao-revalidar" data-id="${c.id}"
              data-email="${esc(c.email)}" ${sessaoEmCurso ? "disabled" : ""}>Revalidar</button>
    </div>

    <div class="conta-linha">
      <span class="proxy-atual ${c.proxy ? "mono-peq" : "leve"}">${
        c.proxy ? esc(c.proxy) : "sem proxy (IP desta máquina)"}</span>
    </div>
    <div class="conta-linha">
      <input type="text" class="mono campo-proxy" data-id="${c.id}" placeholder="trocar proxy…">
      <button class="mini acao-proxy" data-id="${c.id}">Salvar</button>
      ${c.proxy ? `<button class="mini acao-tirar-proxy" data-id="${c.id}">Tirar</button>` : ""}
    </div>
  </div>`;
}

function pintarContas(lista) {
  contas = lista;
  const alvo = $("lista-contas");
  // nao redesenha por cima de quem esta digitando o proxy: o poll apagaria o texto no
  // meio da edicao, e a pessoa culparia o campo.
  const foco = document.activeElement;
  if (alvo.contains(foco) && (foco.tagName === "INPUT" || foco.tagName === "SELECT")) return;

  $("contador-contas").textContent = lista.length
    ? `${lista.length} no pool · ${lista.filter((c) => c.ativa).length} ativas` : "";
  alvo.innerHTML = lista.length
    ? lista.map(blocoConta).join("")
    : `<div class="galeria-vazia">Nenhuma conta ainda. Cadastre a primeira acima.</div>`;
}

async function alterarConta(id, corpo) {
  esconder($("erro-cadastro"));
  try {
    await api(`/contas/${id}`, { metodo: "PATCH", corpo });
    await atualizar();
  } catch (e) {
    mostrar($("erro-cadastro"), e.message);
  }
}

function abrirGaveta() {
  $("gaveta").classList.remove("oculto");
  $("gaveta-fundo").classList.remove("oculto");
}

function fecharGaveta() {
  $("gaveta").classList.add("oculto");
  $("gaveta-fundo").classList.add("oculto");
}

/* ---------------- sessao: cadastro e revalidacao ---------------- */

function iniciarCronometro() {
  const inicio = Date.now();
  $("cronometro").textContent = "0:00";
  cronometro = setInterval(() => {
    const s = Math.floor((Date.now() - inicio) / 1000);
    $("cronometro").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);
}

function travarSessao(titulo) {
  sessaoEmCurso = true;
  abrirGaveta();
  $("campos-conta").disabled = true;
  document.querySelectorAll(".acao-revalidar").forEach((b) => { b.disabled = true; });
  $("andamento-titulo").textContent = titulo;
  $("andamento-cadastro").classList.remove("oculto");
  esconder($("erro-cadastro"));
  esconder($("ok-cadastro"));
  iniciarCronometro();
}

function destravarSessao() {
  sessaoEmCurso = false;
  $("campos-conta").disabled = false;
  document.querySelectorAll(".acao-revalidar").forEach((b) => { b.disabled = false; });
  esconder($("andamento-cadastro"));
  clearInterval(cronometro);
}

async function cadastrar(ev) {
  ev.preventDefault();
  if (sessaoEmCurso) return;   // cinto de seguranca alem do fieldset desabilitado
  const email = $("conta-email").value.trim();
  const corpo = { email, tier: $("conta-tier").value };
  const proxy = $("conta-proxy").value.trim();
  if (proxy) corpo.proxy = proxy;

  travarSessao("Abri o Chrome. Agora é com você.");
  try {
    const c = await api("/contas", { metodo: "POST", corpo }, TETO_SESSAO_MS);
    destravarSessao();
    // o saldo vem LIDO do Flow no próprio cadastro. Quando a leitura falha a conta entra
    // assim mesmo (a sessão é a parte cara), e aí o número seria 0 — dizer "entrou com 0
    // créditos" faria o usuário achar que a conta está seca.
    mostrar($("ok-cadastro"), c.saldo_confiavel
      ? `Sessão de pé para ${c.email}. A conta entrou no pool com ${c.saldo} créditos.`
      : `Sessão de pé para ${c.email}, mas não deu para ler o saldo agora. `
        + `A conta entrou no pool; use “Ler saldo” no bloco dela.`);
    $("form-conta").reset();
  } catch (e) {
    destravarSessao();
    mostrar($("erro-cadastro"), `Não deu para cadastrar ${email}: ${e.message}`);
  }
  await atualizar();
}

/* O motor relê o saldo sozinho quando precisa despachar e não acha conta apta. Este
   botão é para quem quer conferir sem esperar a fila andar. Abre uma janela do lado do
   servidor, então demora segundos — mas não gasta crédito: ler saldo é abrir o menu do
   avatar, não gerar nada. */
async function lerSaldo(id, email, botao) {
  botao.disabled = true;
  const antes = botao.textContent;
  botao.textContent = "Lendo…";
  esconder($("erro-cadastro"));
  try {
    const c = await api(`/contas/${id}/saldo`, { metodo: "POST" }, TETO_SESSAO_MS);
    mostrar($("ok-cadastro"), `${email} está com ${c.saldo} créditos no Flow.`);
  } catch (e) {
    mostrar($("erro-cadastro"), `Não deu para ler o saldo de ${email}: ${e.message}`);
  } finally {
    botao.disabled = false;
    botao.textContent = antes;
  }
  await atualizar();
}

async function revalidar(id, email) {
  if (sessaoEmCurso) return;
  travarSessao(`Abri o Chrome no perfil de ${email}. Agora é com você.`);
  try {
    await api(`/contas/${id}/revalidar`, { metodo: "POST" }, TETO_SESSAO_MS);
    destravarSessao();
    mostrar($("ok-cadastro"), `Sessão de ${email} está de pé de novo.`);
  } catch (e) {
    destravarSessao();
    mostrar($("erro-cadastro"), `Não deu para revalidar ${email}: ${e.message}`);
  }
  await atualizar();
}

/* ---------------- poll ---------------- */

async function atualizar() {
  try {
    const [saude, listaContas, lista] = await Promise.all([
      api("/saude"), api("/contas"), api("/jobs"),
    ]);
    jobs = lista;
    pintarContas(listaContas);
    pintarSaude(saude);
    // um job selecionado que sumiu do banco nao pode continuar contando na barra
    [...selecao].forEach((id) => { if (!jobs.some((j) => j.id === id)) selecao.delete(id); });
    pintarGaleria(jobs);
    pintarProdutos();
    atualizarBarraSelecao();
    if (!produto && jobs.length) definirProduto(jobs[0].produto);
    $("ponto-servico").className = "ponto vivo";
    $("estado-conexao").className = "estado-conexao";
    $("estado-conexao").textContent =
      `atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
  } catch (e) {
    $("ponto-servico").className = "ponto morto";
    $("estado-conexao").className = "estado-conexao falha";
    $("estado-conexao").textContent = `sem resposta: ${e.message}`;
  }
}

/* ---------------- seletor de imagens ----------------

   Por que o servidor navega o disco em vez de o navegador subir o arquivo: o transporte
   entrega a imagem ao Flow com `set_input_files`, que precisa de um arquivo EM DISCO. E
   o `<input type="file">` do navegador entrega o conteudo do arquivo e nunca o caminho
   real — protecao do navegador, nao detalhe que se contorna. Como a API e local,
   caminho e a moeda natural. */

// A ultima pasta visitada volta na proxima abertura. As referencias do usuario moram em
// poucas pastas conhecidas; comecar sempre da raiz cobraria a mesma descida toda vez.
const ULTIMA_PASTA = "flowpool:ultima-pasta";

let seletor = { caminho: "", pai: null, arquivos: [] };

function tetoAnexos() { return (PAPEIS[cfg.modo] || []).length; }

async function abrirSeletor() {
  // anexar num modo que nao anexa so pode querer dizer uma coisa: virar geracao por
  // referencias. Trocar o modo aqui e melhor do que aceitar o arquivo e o transporte
  // ignora-lo depois. Mesma regra do Ctrl+V e do arrastar-soltar, uma funcao so.
  garantirModoQueAceitaArquivo();
  if (!tetoAnexos()) return;

  seletor = { caminho: "", pai: null, arquivos: [] };
  $("seletor-titulo").textContent = cfg.modo === "frames2v"
    ? "Escolher os frames — inicial e final" : "Escolher referências — até 3";
  // o atalho das tres serve a qualquer modo de referencia, e imagem virou um deles
  $("seletor-tres").classList.toggle("oculto", cfg.modo === "frames2v"
                                    || !PAPEIS[cfg.modo]);
  esconder($("seletor-erro"));
  $("seletor-corpo").innerHTML = `<div class="modal-vazio">Lendo a pasta…</div>`;
  $("seletor").classList.remove("oculto");
  // `true`: se a pasta guardada foi renomeada ou apagada desde a ultima vez, cai na raiz
  // em vez de abrir o modal num erro
  await navegar(localStorage.getItem(ULTIMA_PASTA) || "", true);
}

function fecharSeletor() { $("seletor").classList.add("oculto"); }

async function navegar(caminho, cairNaRaiz = false) {
  esconder($("seletor-erro"));
  try {
    const r = await api(`/arquivos?caminho=${encodeURIComponent(caminho)}`);
    seletor.caminho = r.caminho;
    seletor.pai = r.pai;
    seletor.arquivos = r.arquivos;
    try { localStorage.setItem(ULTIMA_PASTA, r.caminho); } catch (e) { /* modo privado */ }
    pintarSeletor(r);
  } catch (e) {
    if (cairNaRaiz && caminho) { await navegar("", false); return; }
    $("seletor-corpo").innerHTML = "";
    mostrar($("seletor-erro"), e.message);
  }
}

function atualizarRestam() {
  const teto = tetoAnexos();
  const faltam = teto - anexos.length;
  const papeis = PAPEIS[cfg.modo] || [];
  $("seletor-restam").textContent = faltam > 0
    ? `${anexos.length} de ${teto} — a próxima vira “${papeis[anexos.length]}”`
    : `${teto} de ${teto} escolhidas — clique numa marcada para tirar`;
}

function pintarSeletor(r) {
  $("seletor-caminho").textContent = r.caminho ? `/${r.caminho}` : "/ raiz do projeto";
  $("seletor-voltar").disabled = r.pai === null;
  $("seletor-tres").disabled = r.arquivos.length === 0;
  atualizarRestam();

  const pastas = r.pastas.map((p) =>
    `<button type="button" class="mini item-pasta ir-pasta"
             data-caminho="${esc(p.caminho)}">${esc(p.nome)}</button>`).join("");
  const imagens = r.arquivos.map((a) =>
    `<button type="button" class="item-imagem escolher-imagem${
        anexos.includes(a.caminho) ? " escolhida" : ""}"
             data-caminho="${esc(a.caminho)}" title="${esc(a.caminho)}">
       <img src="${esc(urlMiniatura(a.caminho))}" alt="" loading="lazy">
       <span class="nome">${esc(a.nome)}</span>
       <span class="tamanho">${tamanhoLegivel(a.tamanho)}</span>
     </button>`).join("");

  $("seletor-corpo").innerHTML =
    (pastas ? `<div class="grade-pastas">${pastas}</div>` : "") +
    (imagens ? `<div class="grade-imagens">${imagens}</div>`
             : `<div class="modal-vazio">Nenhuma imagem nesta pasta.</div>`);
}

function repintarMarcas() {
  document.querySelectorAll("#seletor-corpo .escolher-imagem").forEach((b) => {
    b.classList.toggle("escolhida", anexos.includes(b.dataset.caminho));
  });
  atualizarRestam();
}

function escolherImagem(caminho) {
  const teto = tetoAnexos();
  const i = anexos.indexOf(caminho);
  if (i >= 0) {
    anexos.splice(i, 1);          // clicar de novo na marcada tira — e como se corrige
  } else if (anexos.length >= teto) {
    // impede a de mais AQUI em vez de deixar o 422 do servidor explicar depois
    mostrar($("seletor-erro"),
      `Já são ${teto}, e o Flow não aceita mais neste modo. Clique numa marcada para tirar.`);
    return;
  } else {
    anexos.push(caminho);
  }
  esconder($("seletor-erro"));
  pintarAnexos();
  repintarMarcas();
  if (cfg.modo === "frames2v" && anexos.length === teto) fecharSeletor();
}

/* As tres primeiras imagens da pasta aberta, de uma vez. E o atalho do caso comum:
   identidade de personagem se mantem com um trio fixo de referencias, e escolher as
   mesmas tres a mao toda vez e atrito puro.

   Deliberadamente "as 3 desta pasta" e nao "as 3 do personagem X": nao ha no projeto uma
   convencao de pasta por personagem que permita descobrir isso sem chutar, e um botao
   que as vezes pega o arquivo errado e pior do que nenhum botao. */
function usarTresDaPasta() {
  const tres = seletor.arquivos.slice(0, MAX_REFERENCIAS).map((a) => a.caminho);
  if (!tres.length) return;
  esconder($("seletor-erro"));
  anexos = tres.slice(0, tetoAnexos());
  pintarAnexos();
  repintarMarcas();
}

/* ---------------- colar e soltar ----------------

   O seletor cobre a imagem que JA esta no disco. Isto cobre a que nao esta em lugar
   nenhum: um "copiar imagem" em qualquer site, um print de tela do Win+Shift+S, um
   arquivo arrastado de uma pasta. Como o transporte anexa com `set_input_files`, que
   abre um arquivo em disco, o servidor GRAVA os bytes (`POST /colar`) e devolve o
   caminho — que e a mesma moeda que o seletor ja usa. */

// Extensao no caminho da URL, e nao "qualquer coisa que comece com http": sem este
// filtro, colar um link comum no prompt viraria uma busca na internet em vez de texto.
const RE_EXTENSAO_IMAGEM = /\.(png|jpe?g|webp|gif)$/i;

function urlDeImagem(texto) {
  const t = (texto || "").trim();
  if (!/^https?:\/\//i.test(t) || /\s/.test(t)) return null;
  try {
    return RE_EXTENSAO_IMAGEM.test(new URL(t).pathname) ? t : null;
  } catch (e) {
    return null;
  }
}

/* Trocar o modo e o ULTIMO recurso, nao o primeiro. Se o modo atual ja aceita arquivo,
   fica como esta — e por isso imagem, que agora aceita, deixou de ser sequestrada.

   O vaivem que a pessoa descreveu vinha justamente daqui: colava, o painel forcava
   `ingredients2v`; ela trocava para imagem, e a referencia caia fora pelo teto zero;
   colava de novo, voltava para video. Duas regras discordando na mesma barra.

   Em `t2v` a troca continua: la o transporte nao anexa nada, entao aceitar o arquivo e
   nao trocar o modo seria aceitar e ignorar em silencio. */
function garantirModoQueAceitaArquivo() {
  if (PAPEIS[cfg.modo]) return;
  cfg.modo = "ingredients2v";
  ajustarChip();
}

/* Uma colagem, seja ela bytes (`{blob}`) ou endereco (`{url}`). As duas terminam igual:
   um caminho em disco entrando na lista de anexos. */
async function anexarColada(pedido) {
  esconder($("erro-job"));
  garantirModoQueAceitaArquivo();

  const teto = tetoAnexos();
  if (anexos.length + subindo >= teto) {
    mostrar($("erro-job"),
      `Já são ${teto} neste modo. Tire uma (no × da tira) antes de colar outra.`);
    return;
  }
  if (pedido.blob && pedido.blob.size > TETO_COLAGEM) {
    // recusado aqui e recusado antes de subir 40 MB pelo loopback para tomar 413
    mostrar($("erro-job"),
      `Essa imagem tem ${tamanhoLegivel(pedido.blob.size)} e o limite é 20 MB.`);
    return;
  }

  subindo += 1;
  pintarAnexos();
  try {
    const r = pedido.url
      ? await api("/colar-url", { metodo: "POST", corpo: { url: pedido.url } },
                  TETO_COLAGEM_MS)
      : await api("/colar", { metodo: "POST", bruto: pedido.blob }, TETO_COLAGEM_MS);
    // o nome do arquivo e o hash do conteudo, entao colar a mesma imagem duas vezes
    // devolve o MESMO caminho — anexar duas vezes viraria referencia repetida
    if (!anexos.includes(r.caminho) && anexos.length < tetoAnexos()) {
      anexos.push(r.caminho);
    }
  } catch (e) {
    mostrar($("erro-job"), `Não deu para anexar a imagem: ${e.message}`);
  } finally {
    subindo -= 1;
    pintarAnexos();
  }
}

/* Ctrl+V em qualquer lugar do painel. Print de tela e "copiar imagem" do navegador
   chegam os dois como arquivo aqui; "copiar endereço da imagem" chega como texto, e e o
   vizinho de menu que se clica sem querer — por isso os dois casos sao tratados. */
document.addEventListener("paste", (ev) => {
  const dados = ev.clipboardData;
  if (!dados) return;

  const imagem = [...(dados.items || [])]
    .filter((i) => i.kind === "file" && i.type.startsWith("image/"))
    .map((i) => i.getAsFile())
    .find(Boolean);
  if (imagem) {
    ev.preventDefault();
    anexarColada({ blob: imagem });
    return;
  }

  const url = urlDeImagem(dados.getData("text/plain"));
  if (!url) return;   // texto comum segue para o campo, como sempre
  ev.preventDefault();
  anexarColada({ url });
});

/* Arrastar e soltar. `dragenter`/`dragleave` disparam a cada FILHO que o ponteiro
   atravessa, entao o destaque pisca se for ligado e desligado por evento; o contador e o
   que o mantem estavel do primeiro enter ao ultimo leave. */
let arrastando = 0;

function arrasteAceito(ev) {
  const tipos = [...((ev.dataTransfer && ev.dataTransfer.types) || [])];
  // "Files" e o arquivo vindo de uma pasta; "text/uri-list" e a imagem arrastada de
  // dentro de uma pagina, que e justamente o caso que motivou tudo isto
  if (tipos.includes("Files")) return "arquivo";
  if (tipos.includes("text/uri-list") || tipos.includes("text/plain")) return "url";
  return null;
}

function esconderSolta() {
  arrastando = 0;
  esconder($("solta"));
}

document.addEventListener("dragenter", (ev) => {
  if (!arrasteAceito(ev)) return;
  ev.preventDefault();
  arrastando += 1;
  $("solta").classList.remove("oculto");
});

document.addEventListener("dragover", (ev) => {
  // sem este preventDefault o navegador recusa a soltura e ABRE o arquivo, trocando o
  // painel por uma aba com a imagem
  if (arrasteAceito(ev)) ev.preventDefault();
});

document.addEventListener("dragleave", (ev) => {
  if (!arrasteAceito(ev)) return;
  arrastando = Math.max(0, arrastando - 1);
  if (!arrastando) esconder($("solta"));
});

document.addEventListener("drop", (ev) => {
  const tipo = arrasteAceito(ev);
  if (!tipo) return;
  ev.preventDefault();
  esconderSolta();

  const arquivos = [...((ev.dataTransfer && ev.dataTransfer.files) || [])]
    .filter((f) => f.type.startsWith("image/"));
  if (arquivos.length) {
    arquivos.slice(0, MAX_REFERENCIAS).forEach((f) => anexarColada({ blob: f }));
    return;
  }
  // imagem arrastada de dentro de uma pagina nao vem com arquivo: vem o endereco
  const url = urlDeImagem(ev.dataTransfer.getData("text/uri-list"))
           || urlDeImagem(ev.dataTransfer.getData("text/plain"));
  if (url) { anexarColada({ url }); return; }
  mostrar($("erro-job"), "O que você soltou não é imagem — arraste um PNG, JPEG, WEBP "
    + "ou GIF, ou copie a imagem e cole com Ctrl+V.");
});

// o navegador nao manda `dragleave` quando o arrasto sai pela borda da janela nem quando
// e cancelado com Esc; sem isto o destaque ficaria na tela sem nada acontecendo
window.addEventListener("dragend", esconderSolta);
window.addEventListener("blur", esconderSolta);

/* ---------------- ligacao ---------------- */

$("galeria").addEventListener("click", (ev) => {
  const caixa = ev.target.closest(".marcar-job");
  const cartao = ev.target.closest(".card");
  if (!cartao) return;
  const id = Number(cartao.dataset.id);
  if (caixa) {
    // marcar nao abre o visualizador: a caixa e para juntar o lote de download
    if (selecao.has(id)) selecao.delete(id); else selecao.add(id);
    cartao.classList.toggle("marcado", selecao.has(id));
    assinaturaGaleria = null;
    atualizarBarraSelecao();
    return;
  }
  abrirVisor(id);
});

// tocar no hover: o primeiro quadro ja esta na tela, e o movimento e o que diferencia
// dois takes do mesmo prompt
$("galeria").addEventListener("mouseover", (ev) => {
  const v = ev.target.closest("video");
  if (v) v.play().catch(() => { /* o navegador pode recusar; o quadro parado basta */ });
});
$("galeria").addEventListener("mouseout", (ev) => {
  const v = ev.target.closest("video");
  if (v) { v.pause(); v.currentTime = 0.1; }
});

$("baixar-selecao").addEventListener("click", baixarSelecao);
$("limpar-selecao").addEventListener("click", () => {
  selecao.clear();
  assinaturaGaleria = null;
  pintarGaleria(jobs);
  atualizarBarraSelecao();
});

$("form-job").addEventListener("submit", enviar);
$("job-prompt").addEventListener("input", crescerPrompt);
$("job-prompt").addEventListener("keydown", (ev) => {
  // Enter envia, Shift+Enter quebra linha. E o gesto de todo campo de prompt.
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    $("form-job").requestSubmit();
  }
});

$("botao-mais").addEventListener("click", abrirSeletor);
$("tira-anexos").addEventListener("click", (ev) => {
  const b = ev.target.closest(".tira-x");
  if (!b) return;
  anexos = anexos.filter((c) => c !== b.dataset.caminho);
  pintarAnexos();
});

$("chip").addEventListener("click", () => alternarPopover("pop-chip", "chip"));
$("chip-produto").addEventListener("click", () => {
  pintarProdutos();
  alternarPopover("pop-produto", "chip-produto");
});

$("pop-chip").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.modo) cfg.modo = b.dataset.modo;
  else if (b.dataset.chave === "quantidade") cfg.quantidade = Number(b.dataset.valor);
  else if (b.dataset.chave) cfg[b.dataset.chave] = b.dataset.valor;
  ajustarChip();
  posicionar($("pop-chip"), $("chip"));   // o popover muda de altura ao esconder grupos
});

$("pop-produto").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.id === "usar-produto-novo") {
    const novo = $("produto-novo").value.trim();
    if (!novo) return;
    definirProduto(novo);
    $("produto-novo").value = "";
    pintarProdutos();
    fecharPopovers();
  } else if (b.classList.contains("escolher-produto")) {
    definirProduto(b.dataset.produto);
    pintarProdutos();
    fecharPopovers();
  }
});
$("produto-novo").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { ev.preventDefault(); $("usar-produto-novo").click(); }
});

document.addEventListener("click", (ev) => {
  // clique fora fecha os popovers, mas o clique NO chip e tratado pelo proprio chip
  if (!ev.target.closest(".popover") && !ev.target.closest(".chip")) fecharPopovers();
});

$("visor").addEventListener("click", (ev) => {
  if (ev.target === $("visor")) { fecharVisor(); return; }   // clique no fundo fecha
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.id === "visor-fechar") fecharVisor();
  else if (b.id === "visor-copiar") copiarCaminho();
  else if (b.id === "visor-renomear") renomear();
  else if (b.id === "visor-cancelar") cancelar(jobAberto);
});

$("abrir-contas").addEventListener("click", abrirGaveta);
$("fechar-contas").addEventListener("click", fecharGaveta);
$("gaveta-fundo").addEventListener("click", fecharGaveta);
$("form-conta").addEventListener("submit", cadastrar);

document.addEventListener("click", (ev) => {
  const alvo = ev.target.closest("button");
  if (!alvo) return;
  const id = Number(alvo.dataset.id);
  if (alvo.classList.contains("acao-revalidar")) revalidar(id, alvo.dataset.email);
  else if (alvo.classList.contains("acao-saldo")) lerSaldo(id, alvo.dataset.email, alvo);
  else if (alvo.classList.contains("acao-proxy")) {
    // O proxy da conta chega MASCARADO da API (`http://usuario:***@host:porta`). Por
    // isso o campo nasce vazio e o valor atual e so texto ao lado: um input preenchido
    // com o valor mascarado devolveria `***` no lugar da senha ao primeiro Salvar — e
    // como `***` forma uma URL perfeitamente valida, ninguem seria avisado; a conta so
    // pararia de autenticar no proxy, calada.
    const campo = document.querySelector(`.campo-proxy[data-id="${id}"]`);
    const novo = campo.value.trim();
    if (!novo) {
      mostrar($("erro-cadastro"),
        "Digite o proxy novo para salvar. Para voltar a conta ao IP desta máquina, use “Tirar”.");
      return;
    }
    alterarConta(id, { proxy: novo });
  } else if (alvo.classList.contains("acao-tirar-proxy")) {
    // string vazia e como se TIRA o proxy: o servidor distingue "campo ausente" de
    // "campo vazio" justamente para este caso.
    alterarConta(id, { proxy: "" });
  }
});

document.addEventListener("change", (ev) => {
  const alvo = ev.target;
  const id = Number(alvo.dataset.id);
  if (alvo.classList.contains("acao-ativa")) alterarConta(id, { ativa: alvo.checked });
  else if (alvo.classList.contains("acao-tier")) alterarConta(id, { tier: alvo.value });
});

$("seletor").addEventListener("click", (ev) => {
  if (ev.target === $("seletor")) { fecharSeletor(); return; }   // clique no fundo fecha
  const b = ev.target.closest("button");
  if (!b || b.disabled) return;
  if (b.id === "seletor-fechar") fecharSeletor();
  else if (b.id === "seletor-voltar") { if (seletor.pai !== null) navegar(seletor.pai); }
  else if (b.id === "seletor-tres") usarTresDaPasta();
  else if (b.classList.contains("ir-pasta")) navegar(b.dataset.caminho);
  else if (b.classList.contains("escolher-imagem")) escolherImagem(b.dataset.caminho);
});

document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Escape") return;
  if (!$("seletor").classList.contains("oculto")) fecharSeletor();
  else if (!$("visor").classList.contains("oculto")) fecharVisor();
  else if (!$("pop-chip").classList.contains("oculto")
           || !$("pop-produto").classList.contains("oculto")) fecharPopovers();
  else if (!$("gaveta").classList.contains("oculto")) fecharGaveta();
});

window.addEventListener("resize", () => { fecharPopovers(); medirGrade(); });
// a altura do card de texto so se estabiliza quando a fonte real termina de carregar
if (document.fonts) document.fonts.ready.then(medirGrade);

/* ---------------- partida ---------------- */

try { definirProduto(localStorage.getItem("flowpool:produto")); }
catch (e) { definirProduto(null); }
ajustarChip();
crescerPrompt();
atualizar();
setInterval(() => { if (!document.hidden) atualizar(); }, INTERVALO_MS);
