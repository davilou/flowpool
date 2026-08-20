/* Painel do FlowPool. Cliente da propria API - nao fala com o Flow e nao tem regra de
   negocio: tudo o que o servidor recusaria, aqui some da tela antes de virar um 422. */

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

let contas = [];
let sessaoEmCurso = false;   // cadastro OU revalidacao: os dois abrem Chrome e bloqueiam
let cronometro = null;

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
  try {
    const r = await fetch(caminho, {
      headers: opcoes.corpo ? { "Content-Type": "application/json" } : undefined,
      method: opcoes.metodo || "GET",
      body: opcoes.corpo ? JSON.stringify(opcoes.corpo) : undefined,
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

/* ---------------- saude ---------------- */

function pintarSaude(s) {
  $("credito-total").textContent = s.credito_total;
  $("contas-vivas").textContent = s.contas_vivas;
  $("fila-pendente").textContent = s.fila_pendente;

  const faixa = $("faixa-expiradas");
  const expiradas = s.contas_expiradas || [];
  if (expiradas.length === 0) {
    esconder(faixa);
    return;
  }
  faixa.classList.remove("oculto");
  $("titulo-expiradas").textContent = expiradas.length === 1
    ? "1 conta com a sessão caída — precisa de você"
    : `${expiradas.length} contas com a sessão caída — precisam de você`;

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

/* ---------------- contas ---------------- */

function linhaConta(c) {
  const precisa = c.saude !== "viva";
  const classes = [precisa ? "precisa-acao" : "", c.ativa ? "" : "inativa"]
    .filter(Boolean).join(" ");
  const tiers = ["free", "pro", "ultra"].map(
    (t) => `<option value="${t}" ${t === c.tier ? "selected" : ""}>${t}</option>`).join("");
  const saldo = `<span class="saldo ${c.saldo === 0 ? "zerado" : ""}">${c.saldo}</span>` +
    (c.saldo_confiavel ? "" :
      ` <span class="duvida" title="saldo estimado desde a última leitura no Flow">~</span>`);

  return `
  <tr class="${classes}" data-id="${c.id}">
    <td><span class="email-conta">${esc(c.email)}</span>${
      c.falhas > 0 ? `<span class="falhas" title="falhas consecutivas">${c.falhas} falhas</span>` : ""}</td>
    <td><select class="acao-tier" data-id="${c.id}">${tiers}</select></td>
    <td class="num">${saldo}</td>
    <td><span class="pilula ${esc(c.saude)}">${esc(c.saude)}</span></td>
    <td>
      <div class="linha-proxy">
        <span class="proxy-atual ${c.proxy ? "mono-peq" : "leve"}">${
          c.proxy ? esc(c.proxy) : "sem proxy (IP desta máquina)"}</span>
        <input type="text" class="mono campo-proxy" data-id="${c.id}" placeholder="trocar…">
        <button class="mini acao-proxy" data-id="${c.id}">Salvar</button>
        ${c.proxy ? `<button class="mini acao-tirar-proxy" data-id="${c.id}">Tirar</button>` : ""}
      </div>
    </td>
    <td>
      <label class="interruptor" title="${c.ativa ? "desativar" : "ativar"}">
        <input type="checkbox" class="acao-ativa" data-id="${c.id}" ${c.ativa ? "checked" : ""}>
        <span class="trilho"></span>
      </label>
    </td>
    <td>
      <div class="acoes">
        <button class="mini acao-saldo" data-id="${c.id}" data-email="${esc(c.email)}"
                title="lê o saldo agora no Flow — abre uma janela e não gasta crédito">
          Ler saldo</button>
        <button class="mini ${precisa ? "alerta" : ""} acao-revalidar"
                data-id="${c.id}" data-email="${esc(c.email)}"
                ${sessaoEmCurso ? "disabled" : ""}>Revalidar</button>
      </div>
    </td>
  </tr>`;
}

function pintarContas(lista) {
  contas = lista;
  const corpo = $("corpo-contas");
  // nao redesenha por cima de quem esta digitando o proxy: o poll apagaria o texto no
  // meio da edicao, e a pessoa culparia o campo.
  const foco = document.activeElement;
  if (corpo.contains(foco) && (foco.tagName === "INPUT" || foco.tagName === "SELECT")) return;

  $("contador-contas").textContent = lista.length
    ? `${lista.length} no pool · ${lista.filter((c) => c.ativa).length} ativas`
    : "";
  corpo.innerHTML = lista.length
    ? lista.map(linhaConta).join("")
    : `<tr class="vazio"><td colspan="7">Nenhuma conta ainda. Cadastre a primeira acima.</td></tr>`;
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
    // o saldo vem LIDO do Flow no próprio cadastro. Quando a leitura falha a conta
    // entra assim mesmo (a sessão é a parte cara), e aí o número seria 0 — dizer
    // "entrou com 0 créditos" faria o usuário achar que a conta está seca.
    mostrar($("ok-cadastro"), c.saldo_confiavel
      ? `Sessão de pé para ${c.email}. A conta entrou no pool com ${c.saldo} créditos.`
      : `Sessão de pé para ${c.email}, mas não deu para ler o saldo agora. `
        + `A conta entrou no pool; use “Ler saldo” na linha dela.`);
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

/* ---------------- jobs ---------------- */

function ehVideo() { return $("job-modo").value !== "imagem"; }

function preencher(select, valores, atual) {
  const escolhido = valores.includes(atual) ? atual : valores[0];
  select.innerHTML = valores.map(
    (v) => `<option value="${v}" ${v === escolhido ? "selected" : ""}>${v}</option>`).join("");
}

/* Some da tela tudo o que a API recusaria. Melhor o campo nao existir do que o usuario
   preencher e tomar 422: duracao so em video, quantidade acima de 1 so em imagem, e
   proporcao de video so 9:16 ou 16:9. */
function ajustarFormulario() {
  const modo = $("job-modo").value;
  const video = ehVideo();

  preencher($("job-modelo"), video ? MODELOS_VIDEO : MODELOS_IMAGEM, $("job-modelo").value);
  preencher($("job-proporcao"), video ? PROPORCOES_VIDEO : PROPORCOES_IMAGEM,
            $("job-proporcao").value);

  $("campo-duracao").classList.toggle("oculto", !video);
  $("campo-quantidade").classList.toggle("oculto", video);
  $("campo-frames").classList.toggle("oculto", modo !== "frames2v");
  $("campo-referencias").classList.toggle("oculto", modo !== "ingredients2v");

  $("dica-job").textContent = video
    ? "Vídeo sai sempre em uma geração — take é escolha sua, não sorteio — e o chip do Flow só "
      + "oferece 9:16 e 16:9. A duração muda o preço: 4s custa 7 créditos e 8s custa 12 no Omni Flash."
    : "Imagem não tem duração (o painel do Flow não oferece o grupo nesse modo) e aceita até 4 "
      + "variações na mesma geração — cada uma custa cheio.";
}

function corpoDoJob() {
  const modo = $("job-modo").value;
  const corpo = {
    modo,
    modelo: $("job-modelo").value,
    prompt: $("job-prompt").value,
    produto: $("job-produto").value.trim(),
    nome: $("job-nome").value.trim(),
    proporcao: $("job-proporcao").value,
    quantidade: ehVideo() ? 1 : Number($("job-quantidade").value),
  };
  // `duracao` so entra em video: mandar o campo em imagem e 422 por si so, e o teste do
  // servidor e pelo campo TER VINDO no corpo, nao pelo valor.
  if (ehVideo()) corpo.duracao = $("job-duracao").value;
  const destino = $("job-destino").value.trim();
  if (destino) corpo.destino = destino;
  if (modo === "frames2v") {
    corpo.frame_inicial = $("job-frame-inicial").value.trim();
    const fim = $("job-frame-final").value.trim();
    if (fim) corpo.frame_final = fim;
  }
  if (modo === "ingredients2v") {
    corpo.referencias = $("job-referencias").value
      .split("\n").map((s) => s.trim()).filter(Boolean);
  }
  return corpo;
}

async function enfileirar(ev) {
  ev.preventDefault();
  const botao = $("botao-job");
  botao.disabled = true;
  esconder($("erro-job"));
  try {
    await api("/jobs", { metodo: "POST", corpo: corpoDoJob() });
    $("job-prompt").value = "";
    $("job-nome").value = "";
    await atualizar();
  } catch (e) {
    mostrar($("erro-job"), e.message);
  } finally {
    botao.disabled = false;
  }
}

function emailDaConta(id) {
  const c = contas.find((x) => x.id === id);
  return c ? c.email : `#${id}`;
}

function linhaJob(j) {
  const config = [j.proporcao, j.quantidade > 1 ? `x${j.quantidade}` : "",
                  j.modo === "imagem" ? "" : j.duracao].filter(Boolean).join(" · ");
  const saida = j.erro
    ? `<span class="corta" style="color:var(--erro)" title="${esc(j.erro)}">${esc(j.erro)}</span>`
    : (j.arquivo
      ? `<span class="corta mono-peq" title="${esc(j.arquivo)}">${esc(j.arquivo)}</span>`
      : `<span class="leve">—</span>`);
  return `
  <tr>
    <td class="num">${j.id}</td>
    <td><span class="pilula ${esc(j.estado)}">${esc(j.estado)}</span>${
      j.tentativas > 0 ? `<span class="leve"> ${j.tentativas}ª</span>` : ""}</td>
    <td><span class="mono-peq">${esc(j.modo)}</span><br><span class="leve">${esc(j.modelo)}</span></td>
    <td>${esc(j.produto)}<br><span class="leve">${esc(j.nome)}</span></td>
    <td><span class="mono-peq">${esc(config)}</span></td>
    <td><span class="mono-peq">${j.conta_id === null ? "—" : esc(emailDaConta(j.conta_id))}</span></td>
    <td>${saida}</td>
    <td>${j.estado === "pendente"
      ? `<button class="mini acao-cancelar" data-id="${j.id}">Cancelar</button>` : ""}</td>
  </tr>`;
}

function pintarJobs(lista) {
  const porEstado = {};
  lista.forEach((j) => { porEstado[j.estado] = (porEstado[j.estado] || 0) + 1; });
  $("contador-jobs").textContent = Object.entries(porEstado)
    .map(([e, n]) => `${n} ${e}`).join(" · ");
  $("corpo-jobs").innerHTML = lista.length
    ? lista.map(linhaJob).join("")
    : `<tr class="vazio"><td colspan="8">Nenhum job ainda.</td></tr>`;
}

async function cancelar(id) {
  try {
    await api(`/jobs/${id}`, { metodo: "DELETE" });
    await atualizar();
  } catch (e) {
    mostrar($("erro-job"), e.message);
  }
}

/* ---------------- poll ---------------- */

async function atualizar() {
  try {
    const [saude, listaContas, jobs] = await Promise.all([
      api("/saude"), api("/contas"), api("/jobs"),
    ]);
    pintarContas(listaContas);
    pintarSaude(saude);
    pintarJobs(jobs);
    $("ponto-servico").className = "ponto vivo";
    $("estado-conexao").className = "estado-conexao";
    $("estado-conexao").textContent =
      `atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
  } catch (e) {
    $("ponto-servico").className = "ponto morto";
    $("estado-conexao").className = "estado-conexao falha";
    $("estado-conexao").textContent = `sem resposta do serviço: ${e.message}`;
  }
}

/* ---------------- ligacao ---------------- */

document.addEventListener("click", (ev) => {
  const alvo = ev.target.closest("button");
  if (!alvo) return;
  const id = Number(alvo.dataset.id);
  if (alvo.classList.contains("abrir-seletor")) abrirSeletor(alvo.dataset.campo, !!alvo.dataset.multiplo);
  else if (alvo.classList.contains("tira-x")) tirarEscolhido(alvo.dataset.campo, alvo.dataset.caminho);
  else if (alvo.classList.contains("acao-revalidar")) revalidar(id, alvo.dataset.email);
  else if (alvo.classList.contains("acao-saldo")) lerSaldo(id, alvo.dataset.email, alvo);
  else if (alvo.classList.contains("acao-cancelar")) cancelar(id);
  else if (alvo.classList.contains("acao-proxy")) {
    // O proxy da conta chega MASCARADO da API (`http://usuario:***@host:porta`). Por
    // isso o campo nasce vazio e o valor atual e so texto ao lado: um input preenchido
    // com o valor mascarado devolveria `***` no lugar da senha ao primeiro Salvar - e
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

$("form-conta").addEventListener("submit", cadastrar);
$("form-job").addEventListener("submit", enfileirar);
$("job-modo").addEventListener("change", ajustarFormulario);

ajustarFormulario();
atualizar();
setInterval(() => { if (!document.hidden) atualizar(); }, INTERVALO_MS);

/* ---------------- seletor de imagens ----------------

   Por que o servidor navega o disco em vez de o navegador subir o arquivo: o transporte
   entrega a imagem ao Flow com `set_input_files`, que precisa de um arquivo EM DISCO. E
   o `<input type="file">` do navegador entrega o conteudo do arquivo e nunca o caminho
   real — protecao do navegador, nao detalhe que se contorna. Como a API e local, caminho
   e a moeda natural.

   O CAMPO DE TEXTO continua sendo o dono do valor. Este modal so escreve nele, e a tira
   de miniaturas abaixo do campo e o reflexo do que esta escrito. Uma fonte de verdade
   so: quem digita o caminho ve a miniatura aparecer, e nao precisa navegar por nada. */

const CAMPOS_DE_ARQUIVO = ["job-frame-inicial", "job-frame-final", "job-referencias"];
const MAX_REFERENCIAS = 3;

// A ultima pasta visitada volta na proxima abertura. As referencias do usuario moram em
// poucas pastas conhecidas; comecar sempre da raiz cobraria a mesma descida toda vez.
const ULTIMA_PASTA = "flowpool:ultima-pasta";

let seletor = { campo: null, multiplo: false, caminho: "", pai: null, arquivos: [] };

function ehMultiplo(campo) { return campo === "job-referencias"; }

/* Le o campo como LISTA. Referencias sao uma por linha; frame e um caminho so. */
function caminhosDoCampo(campo) {
  const valor = $(campo).value;
  if (ehMultiplo(campo)) return valor.split("\n").map((s) => s.trim()).filter(Boolean);
  const um = valor.trim();
  return um ? [um] : [];
}

function gravarCampo(campo, lista) {
  $(campo).value = ehMultiplo(campo) ? lista.join("\n") : (lista[0] || "");
  atualizarTiras();
}

function urlMiniatura(caminho) {
  return `/miniatura?caminho=${encodeURIComponent(caminho)}`;
}

function tamanhoLegivel(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function atualizarTiras() {
  CAMPOS_DE_ARQUIVO.forEach((campo) => {
    const alvo = $(`tira-${campo}`);
    if (!alvo) return;
    alvo.innerHTML = caminhosDoCampo(campo).map((c) => {
      const nome = c.split("/").pop();
      return `<span class="tira" title="${esc(c)}">
        <img src="${esc(urlMiniatura(c))}" alt="" loading="lazy">
        <span class="tira-nome">${esc(nome)}</span>
        <button type="button" class="tira-x" data-campo="${campo}" data-caminho="${esc(c)}"
                title="tirar esta imagem">&times;</button></span>`;
    }).join("");
  });
}

function tirarEscolhido(campo, caminho) {
  gravarCampo(campo, caminhosDoCampo(campo).filter((c) => c !== caminho));
  if (seletor.campo === campo && !$("seletor").classList.contains("oculto")) repintarMarcas();
}

/* ---- modal ---- */

async function abrirSeletor(campo, multiplo) {
  seletor = { campo, multiplo, caminho: "", pai: null, arquivos: [] };
  $("seletor-titulo").textContent = multiplo
    ? "Escolher referências — até 3" : "Escolher imagem";
  $("seletor-tres").classList.toggle("oculto", !multiplo);
  esconder($("seletor-erro"));
  $("seletor-corpo").innerHTML = `<div class="modal-vazio">Lendo a pasta…</div>`;
  $("seletor").classList.remove("oculto");
  // `true`: se a pasta guardada foi renomeada ou apagada desde a ultima vez, cai na raiz
  // em vez de abrir o modal num erro
  await navegar(localStorage.getItem(ULTIMA_PASTA) || "", true);
}

function fecharSeletor() {
  $("seletor").classList.add("oculto");
  seletor.campo = null;
}

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
  if (!seletor.multiplo) { $("seletor-restam").textContent = ""; return; }
  const quantas = caminhosDoCampo(seletor.campo).length;
  const faltam = MAX_REFERENCIAS - quantas;
  $("seletor-restam").textContent = faltam > 0
    ? `${quantas} de 3 escolhidas — cabem mais ${faltam}`
    : "3 de 3 escolhidas — clique numa marcada para tirar";
}

function pintarSeletor(r) {
  $("seletor-caminho").textContent = r.caminho ? `/${r.caminho}` : "/ raiz do projeto";
  $("seletor-voltar").disabled = r.pai === null;
  $("seletor-tres").disabled = r.arquivos.length === 0;
  atualizarRestam();

  const escolhidos = caminhosDoCampo(seletor.campo);
  const pastas = r.pastas.map((p) =>
    `<button type="button" class="mini item-pasta ir-pasta"
             data-caminho="${esc(p.caminho)}">${esc(p.nome)}</button>`).join("");
  const imagens = r.arquivos.map((a) =>
    `<button type="button" class="item-imagem escolher-imagem${
        escolhidos.includes(a.caminho) ? " escolhida" : ""}"
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
  const escolhidos = caminhosDoCampo(seletor.campo);
  document.querySelectorAll("#seletor-corpo .escolher-imagem").forEach((b) => {
    b.classList.toggle("escolhida", escolhidos.includes(b.dataset.caminho));
  });
  atualizarRestam();
}

function escolherImagem(caminho) {
  if (!seletor.multiplo) {
    gravarCampo(seletor.campo, [caminho]);
    fecharSeletor();
    return;
  }
  const atual = caminhosDoCampo(seletor.campo);
  const i = atual.indexOf(caminho);
  if (i >= 0) {
    atual.splice(i, 1);          // clicar de novo na marcada tira — e como se corrige
  } else if (atual.length >= MAX_REFERENCIAS) {
    // impede a quarta AQUI em vez de deixar o 422 do servidor explicar depois
    mostrar($("seletor-erro"),
      "Já são 3 referências, e o Flow não aceita a quarta. Clique numa marcada para tirar.");
    return;
  } else {
    atual.push(caminho);
  }
  esconder($("seletor-erro"));
  gravarCampo(seletor.campo, atual);
  repintarMarcas();
}

/* As tres primeiras imagens da pasta aberta, de uma vez. E o atalho do caso comum:
   identidade de personagem se mantem com um trio fixo de referencias, e escolher as
   mesmas tres a mao toda vez e atrito puro.

   Deliberadamente "as 3 desta pasta" e nao "as 3 do personagem X": nao ha no projeto uma
   convencao de pasta por personagem que permita descobrir isso sem chutar, e um botao
   que as vezes pega o arquivo errado e pior do que nenhum botao. Aqui a pasta e a que
   esta na tela, a ordem e a alfabetica que a grade mostra, e as tres escolhidas aparecem
   marcadas na hora — nao ha o que adivinhar. */
function usarTresDaPasta() {
  const tres = seletor.arquivos.slice(0, MAX_REFERENCIAS).map((a) => a.caminho);
  if (!tres.length) return;
  esconder($("seletor-erro"));
  gravarCampo(seletor.campo, tres);
  repintarMarcas();
}

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
  if (ev.key === "Escape" && !$("seletor").classList.contains("oculto")) fecharSeletor();
});

/* Digitar continua valendo: a tira e o reflexo do campo, nao o contrario. Com uma
   espera curta no meio — sem ela, cada tecla de um caminho pela metade viraria um pedido
   de miniatura que responde 404, e o campo piscaria imagem quebrada enquanto se digita. */
let tirasAgendadas = null;
CAMPOS_DE_ARQUIVO.forEach((c) => $(c).addEventListener("input", () => {
  clearTimeout(tirasAgendadas);
  tirasAgendadas = setTimeout(atualizarTiras, 300);
}));

atualizarTiras();
