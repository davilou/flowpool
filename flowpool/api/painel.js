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
    mostrar($("ok-cadastro"),
      `Sessão de pé para ${c.email}. A conta entrou no pool com ${c.saldo} créditos.`);
    $("form-conta").reset();
  } catch (e) {
    destravarSessao();
    mostrar($("erro-cadastro"), `Não deu para cadastrar ${email}: ${e.message}`);
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
  if (alvo.classList.contains("acao-revalidar")) revalidar(id, alvo.dataset.email);
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
