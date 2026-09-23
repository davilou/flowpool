/* Painel do FlowPool. Cliente da propria API — nao fala com o Flow e nao tem regra de
   negocio: as regras (modelos por modo, custos, tetos) chegam de GET /info, e tudo o
   que o servidor recusaria some da tela antes de virar um 422.

   Oito paginas num roteador por hash, um poll a cada poucos segundos, e tres
   principios de desenho:
     1. o que exige acao e a UNICA coisa colorida na tela;
     2. tabela para operar, galeria para olhar, modal para entender UM job;
     3. nada e redesenhado quando nada mudou — assinatura antes de innerHTML, senao
        todo <video> recarrega o primeiro quadro a cada volta do poll. */

"use strict";

const $ = (id) => document.getElementById(id);

const TETO_PADRAO_MS = 15000;
// Cadastro e revalidacao esperam a pessoa logar: o servidor aguenta ate 900 s, e o
// cliente espera MAIS, para quem desiste primeiro ser o servidor — com mensagem.
const TETO_SESSAO_MS = 1200000;
const TETO_COLAGEM = 20 * 1024 * 1024;
const TETO_COLAGEM_MS = 60000;
// O chip do Flow so oferece estas duas em video. Imagem aceita as cinco de /info.
const PROPORCOES_VIDEO = ["9:16", "16:9"];
const NOMES_MODO = {
  imagem: "Imagem", t2v: "Vídeo · texto", ingredients2v: "Vídeo · refs", frames2v: "Vídeo · frames",
};
const NOMES_ESTADO = {
  pendente: "na fila", rodando: "gerando", pronto: "pronto", falhou: "falhou", cancelado: "cancelado",
};
const PAPEIS_FRAMES = ["frame inicial", "frame final"];

/* ---------------- estado ---------------- */

const E = {
  saude: null, contas: [], jobs: [], projetos: [], info: null,
  atualizadoEm: null, falhaConexao: null, rota: "painel", parametros: {},
};

// preferencias do painel, so deste navegador
const PREF = {
  ler(chave, padrao) {
    try { const v = localStorage.getItem(`flowpool:${chave}`); return v === null ? padrao : JSON.parse(v); }
    catch { return padrao; }
  },
  gravar(chave, valor) {
    try { localStorage.setItem(`flowpool:${chave}`, JSON.stringify(valor)); } catch { /* modo privado */ }
  },
};

// filtros por pagina — vivem fora do DOM para sobreviver ao redesenho
const F = {
  jobs: { busca: "", estado: "todos", modo: "", produto: "", limite: 50 },
  historico: { estado: "todos", limite: 80 },
  saidas: { produto: "", tipo: "todas", selecionando: false },
};
const selecao = new Set();
let sessaoEmCurso = false;
let cronometro = null;
let jobAberto = null;
let contaAberta = null;
let eventosJob = { id: null, lista: null };
let eventosConta = { id: null, lista: null };
const assinaturas = {};

/* ---------------- utilidades ---------------- */

function esc(v) {
  return String(v === null || v === undefined ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function icone(nome, classe = "") {
  return `<svg class="${classe}"><use href="#i-${nome}"/></svg>`;
}

function mensagemDoErro(status, corpo) {
  const d = corpo && corpo.detail;
  if (typeof d === "string") return d;
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
  const bruto = opcoes.bruto !== undefined && opcoes.bruto !== null;
  try {
    const r = await fetch(caminho, {
      headers: opcoes.corpo ? { "Content-Type": "application/json" }
             : bruto ? { "Content-Type": "application/octet-stream" } : undefined,
      method: opcoes.metodo || "GET",
      body: opcoes.corpo ? JSON.stringify(opcoes.corpo) : bruto ? opcoes.bruto : undefined,
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

function mudou(chave, valor) {
  if (assinaturas[chave] === valor) return false;
  assinaturas[chave] = valor;
  return true;
}

function toast(nivel, texto, ms = 6000) {
  const el = document.createElement("div");
  el.className = `toast ${nivel}`;
  el.innerHTML = `${icone(nivel === "ok" ? "check" : nivel === "erro" ? "alerta" : "info")}
    <span class="cresce">${esc(texto)}</span>
    <button type="button" class="fechar-x">${icone("x")}</button>`;
  el.querySelector(".fechar-x").addEventListener("click", () => el.remove());
  $("toasts").appendChild(el);
  if (ms) setTimeout(() => el.remove(), ms);
}

function mostrar(el, texto) { el.textContent = texto; el.classList.remove("oculto"); }
function esconder(el) { el.classList.add("oculto"); }

const urlMiniatura = (c) => `/miniatura?caminho=${encodeURIComponent(c)}`;
const urlMidia = (c) => `/midia?caminho=${encodeURIComponent(c)}`;
const urlBaixar = (c) => `/baixar?caminho=${encodeURIComponent(c)}`;

function tamanhoLegivel(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function data(iso) { return iso ? new Date(iso) : null; }

/* "há 12 s", "há 3 min", "há 2 h", "ontem", "3 set". Quem le uma tabela de jobs
   quer distancia, nao data — a data inteira fica no detalhe. */
function relativo(iso) {
  const d = data(iso);
  if (!d) return "—";
  const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 0) return "agora";
  if (s < 60) return `há ${s} s`;
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  if (s < 172800) return "ontem";
  return d.toLocaleDateString("pt-BR", { day: "numeric", month: "short" });
}

function emQuanto(iso) {
  const d = data(iso);
  if (!d) return null;
  const s = Math.round((d.getTime() - Date.now()) / 1000);
  if (s <= 0) return "agora";
  if (s < 60) return `em ${s} s`;
  if (s < 3600) return `em ${Math.floor(s / 60)} min`;
  return `em ${Math.floor(s / 3600)} h`;
}

function dataCompleta(iso) {
  const d = data(iso);
  return d ? d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

function decorrido(iso) {
  const d = data(iso);
  if (!d) return "—";
  const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function diaDe(iso) {
  const d = data(iso);
  if (!d) return "sem data";
  const hoje = new Date(); hoje.setHours(0, 0, 0, 0);
  const dia = new Date(d); dia.setHours(0, 0, 0, 0);
  const diff = Math.round((hoje - dia) / 86400000);
  if (diff === 0) return "Hoje";
  if (diff === 1) return "Ontem";
  return d.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
}

const ehVideoJob = (j) => j.modo !== "imagem";

function contaDe(id) { return E.contas.find((c) => c.id === id) || null; }
function emailDaConta(id) { const c = contaDe(id); return c ? c.email : `conta #${id}`; }
function emailCurto(email) { return (email || "").replace(/@gmail\.com$/, ""); }

/* ---------------- regras vindas de /info ---------------- */

function semDuracao(modo) { return E.info ? E.info.sem_duracao.includes(modo) : modo === "imagem" || modo === "frames2v"; }

function tetoAnexos(modo) {
  if (modo === "frames2v") return 2;
  return E.info ? (E.info.max_referencias[modo] || 0) : (modo === "t2v" ? 0 : 3);
}

function papeis(modo) {
  if (modo === "frames2v") return PAPEIS_FRAMES;
  return Array.from({ length: tetoAnexos(modo) }, (_, i) => `referência ${i + 1}`);
}

/* Custo de UMA geracao, na tabela que o nucleo expos. `null` quando o servico nao
   conhece o par — e o painel diz "—" em vez de inventar. */
function custoDe(modelo, tier, modo, duracao) {
  const t = E.info && E.info.custos[modelo] && E.info.custos[modelo][tier];
  if (!t) return null;
  const chave = modo === "imagem" ? "-" : semDuracao(modo) ? E.info.duracao_base : duracao;
  return t[chave] === undefined ? null : t[chave];
}

function custoDoJob(j) {
  if (j.custo_estimado !== null && j.custo_estimado !== undefined) return j.custo_estimado;
  const conta = j.conta_id ? contaDe(j.conta_id) : null;
  const unit = custoDe(j.modelo, conta ? conta.tier : "free", j.modo, j.duracao);
  return unit === null ? null : unit * (j.quantidade || 1);
}

function contaPaga(c, custo) {
  if (!c.ativa || c.saude !== "viva") return false;
  if (c.seca_ate && new Date(c.seca_ate) > new Date()) return false;
  return custo === null ? true : c.saldo >= custo;
}

function contasQuePagam(custo) { return E.contas.filter((c) => contaPaga(c, custo)); }

function resumoConfig(j) {
  return [j.proporcao, semDuracao(j.modo) ? "" : j.duracao, j.quantidade > 1 ? `x${j.quantidade}` : ""]
    .filter(Boolean).join(" · ");
}

/* ---------------- diagnostico ---------------- */

/* A mensagem de erro diz o que o transporte viu. Isto diz o que a pessoa faz com
   isso — cada linha veio de um caso real que custou uma investigacao. */
/* ⚠️ PALAVRA INTEIRA, E NAO SUBSTRING. MEDIDO 2026-09-09, e custou uma investigacao:
   `includes("idade")` casa DENTRO de "atividade". A recusa por ATIVIDADE incomum
   aparecia no painel como "a conta caiu no portao de idade do Flow", mandando o
   usuario mexer na verificacao de idade da conta Google por um problema que nao
   existia — e a regra do portao e a SEGUNDA da lista, entao ela atirava antes de
   qualquer regra mais especifica poder responder.

   Nao e um caso isolado: "identidade" (que este app usa o tempo todo para o `src` da
   miniatura), "qualidade", "prioridade", "velocidade" e "necessidade" casam igual. */
const casaTermo = (e, termos) => termos.some(t =>
  new RegExp(`\\b${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(e));

function explicarErro(erro) {
  const e = (erro || "").toLowerCase();
  if (!e) return null;
  if (casaTermo(e, ["botao de enviar", "saldoinsuficiente", "saldo insuficiente"]))
    return "O Flow não ofereceu o botão de gerar: o modelo escolhido custa mais do que o saldo dessa conta. Crédito não se soma entre contas — o job precisa caber em UMA.";
  if (casaTermo(e, ["atividade incomum"]))
    return "O Flow recusou a geração NESTA conta e não cobrou por ela — é o antiabuso do Google, não a conta. O job já foi para outra conta sozinho, sem gastar tentativa, e esta descansa uma hora. Se estiver acontecendo direto, é concorrência demais saindo pelo mesmo IP: proxy por conta é o conserto.";
  if (casaTermo(e, ["limite de uso"]))
    return "A conta bateu no limite de uso do Flow — não é falta de crédito, e o saldo continua valendo. O job já foi para outra conta sozinho; esta volta ao pool depois do descanso.";
  if (casaTermo(e, ["age-restricted", "portao de idade"]))
    return "A conta caiu no portão de idade do Flow. Resolver é verificar a idade na conta Google, e isso só o dono da conta faz.";
  if (casaTermo(e, ["cardfalhou"]))
    return "O próprio Flow marcou a geração como falha — o texto do card está na mensagem acima. Nas vezes medidas ele não cobrou. O job já foi gerado de novo em OUTRA conta; se falhar em todas as tentativas, o problema provavelmente é o pedido (prompt ou referência), não a conta.";
  if (casaTermo(e, ["nao e o resultado deste job", "cardtrocado"]))
    return "O robô abriu um card que não é o resultado deste job (card de imagem num job de vídeo, em geral a referência enviada). Ele parou na hora em vez de insistir. NÃO se sabe se a geração existe: abra a grade do projeto na conta e veja se há card do tipo certo antes de gerar de novo.";
  if (e.includes("download nativo") || e.includes("nenhum blob"))
    return "A geração aconteceu e o resultado existe no Flow. O Flow mudou o jeito de baixar (o transporte lê o blob da página, e não achou um): o caminho de download precisa ser medido de novo. Abra a conta e baixe pelo projeto — não gere de novo, seria pagar duas vezes.";
  if (e.includes("download falhou") || e.includes("download_falhou") || e.includes("download.path"))
    return "A geração aconteceu e o resultado existe no Flow; o que falhou foi trazer o arquivo. Se o erro cita Download.path, é o Chrome travando no download nativo — corrigido em 06/09 (o arquivo passou a ser lido do blob da página); jobs anteriores a isso só se resolvem baixando pelo projeto. Não gere de novo, seria pagar duas vezes.";
  if (e.includes("targetclosed") || e.includes("target closed") || e.includes("browser has been closed"))
    return "A janela do Chrome foi fechada no meio da geração — normalmente à mão. Não é defeito do serviço.";
  if (casaTermo(e, ["sessaomorta", "sessao morta", "sessão"]))
    return "A sessão do Google caiu nessa conta (dura ~48 h). Revalide a conta pelo painel; o job volta para a fila sozinho.";
  if (e.includes("perfilocupado") || e.includes("perfil ocupado") || e.includes("user-data-dir"))
    return "Já havia um Chrome aberto nesse perfil — provavelmente a janela do botão “Abrir”. Feche a janela: enquanto ela existe, a conta não consegue gerar.";
  if (e.includes("timeout"))
    return "Um passo estourou o prazo. Costuma ser a página do Flow lenta naquele momento, não o seletor errado — o print em scratch/falhas mostra em que tela parou.";
  if (e.includes("repetido") || e.includes("referencia errada"))
    return "O painel detectou que as referências anexadas viraram a mesma imagem no Flow, e recusou gerar com a errada.";
  return null;
}

function nivelDoEvento(tipo) {
  if (/falhou|divergencia|sem_conta|nao_semeado/.test(tipo)) return "erro";
  if (/pronto|semeado|validada|revalidada/.test(tipo)) return "ok";
  if (/retomada|recuperado|espera/.test(tipo)) return "aviso";
  return "info";
}

function payloadLegivel(p) {
  if (!p || !Object.keys(p).length) return "";
  return Object.entries(p).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ");
}

function linhaDoTempo(eventos) {
  if (!eventos) return `<div class="leve pequeno">carregando…</div>`;
  if (!eventos.length) return `<div class="leve pequeno">Nenhum evento registrado.</div>`;
  return `<div class="linha-tempo">${eventos.map((ev) => `
    <div class="evento ${nivelDoEvento(ev.tipo)}">
      <span class="ponto"></span>
      <div><span class="tipo">${esc(ev.tipo)}</span><span class="quando" title="${esc(dataCompleta(ev.em))}">${esc(relativo(ev.em))}</span>
        ${payloadLegivel(ev.payload) ? `<div class="payload">${esc(payloadLegivel(ev.payload))}</div>` : ""}</div>
    </div>`).join("")}</div>`;
}

/* ---------------- estado do sistema e atencao ---------------- */

/* Duas contas ATIVAS saindo pelo mesmo IP. Espelha `nucleo.proxy.colisoes`, e pela
   mesma razao ignora quem nunca foi conferido: ausência de leitura não é evidência, e
   juntar os nunca-conferidos faria o aviso gritar sobre o que ninguém mediu. */
function colisoesDeIp() {
  const por = new Map();
  for (const c of E.contas) {
    if (!c.ativa || !c.ip_saida) continue;
    if (!por.has(c.ip_saida)) por.set(c.ip_saida, []);
    por.get(c.ip_saida).push(c);
  }
  return [...por.entries()].filter(([, cs]) => cs.length > 1);
}

function itensAtencao() {
  const itens = [];
  const agora = new Date();
  for (const c of E.contas) {
    if (!c.ativa) continue;
    if (c.saude === "expirada") {
      itens.push({ nivel: "aviso", texto: c.email, sub: "sessão caída — precisa de login manual",
                   acao: "Revalidar", fn: () => revalidar(c.id, c.email) });
    } else if (c.saude === "bloqueada") {
      itens.push({ nivel: "erro", texto: c.email, sub: "portão de idade do Flow — verificação na conta Google, só o dono faz",
                   acao: "Abrir", fn: () => abrirConta(c.id, c.email) });
    } else if (c.seca_ate && new Date(c.seca_ate) > agora) {
      itens.push({ nivel: "aviso", texto: c.email, sub: `o Flow recusou gerar apesar do saldo — volta ${emQuanto(c.seca_ate)}` });
    } else if (c.falhas > 0) {
      itens.push({ nivel: "aviso", texto: c.email, sub: `${c.falhas} falha${c.falhas > 1 ? "s" : ""} seguida${c.falhas > 1 ? "s" : ""}`,
                   acao: "Ler saldo", fn: () => lerSaldo(c.id, c.email) });
    } else if (c.saldo === 0) {
      itens.push({ nivel: "info", texto: c.email, sub: "sem crédito até o reset diário" });
    } else if (!c.saldo_confiavel) {
      itens.push({ nivel: "info", texto: c.email, sub: "saldo estimado — não foi lido no Flow",
                   acao: "Ler saldo", fn: () => lerSaldo(c.id, c.email) });
    }
  }
  const pendentes = E.jobs.filter((j) => j.estado === "pendente");
  const semQuemPague = pendentes.filter((j) => !contasQuePagam(custoDoJob(j)).length);
  if (semQuemPague.length) {
    itens.push({ nivel: "aviso", texto: `${semQuemPague.length} job${semQuemPague.length > 1 ? "s" : ""} na fila sem conta que pague`,
                 sub: "crédito não se soma entre contas: o custo precisa caber em uma só", acao: "Ver fila", href: "#/fila" });
  }
  const corte = Date.now() - 86400000;
  const falhos = E.jobs.filter((j) => j.estado === "falhou" && data(j.atualizado_em) && data(j.atualizado_em).getTime() > corte);
  if (falhos.length) {
    itens.push({ nivel: "info", texto: `${falhos.length} job${falhos.length > 1 ? "s" : ""} falh${falhos.length > 1 ? "aram" : "ou"} nas últimas 24 h`,
                 sub: "diagnóstico completo no histórico", acao: "Ver", href: "#/historico?estado=falhou" });
  }
  const inativas = E.contas.filter((c) => !c.ativa).length;
  if (inativas) itens.push({ nivel: "info", texto: `${inativas} conta${inativas > 1 ? "s" : ""} desativada${inativas > 1 ? "s" : ""}`, sub: "fora do pool por escolha sua" });
  const peso = { erro: 0, aviso: 1, info: 2 };
  return itens.sort((a, b) => peso[a.nivel] - peso[b.nivel]);
}

function estadoSistema() {
  if (E.falhaConexao) return { nivel: "erro", texto: "Sem conexão com o serviço" };
  const s = E.saude;
  if (!s) return { nivel: "", texto: "conectando…" };
  if (s.contas_vivas === 0) return { nivel: "erro", texto: "Parado · nenhuma conta viva" };
  const caidas = s.contas_expiradas.length + E.contas.filter((c) => c.ativa && c.saude === "bloqueada").length;
  if (caidas) return { nivel: "aviso", texto: `Atenção · ${caidas} sessão${caidas > 1 ? "ões" : ""} fora` };
  if (s.credito_total === 0) return { nivel: "aviso", texto: "Atenção · pool sem crédito" };
  if (s.gerando > 0) return { nivel: "ok", texto: `Operacional · gerando ${s.gerando}` };
  return { nivel: "ok", texto: "Operacional" };
}

/* ---------------- casca: topo, nav ---------------- */

function pintarCasca() {
  const st = estadoSistema();
  const el = $("estado-sistema");
  el.className = `estado-sistema ${st.nivel}`;
  $("estado-sistema-texto").textContent = st.texto;
  $("ponto-servico").className = `ponto ${E.falhaConexao ? "erro" : "ok"}`;
  $("servico-endereco").textContent = location.host;

  const at = $("atualizado");
  if (E.falhaConexao) { at.className = "atualizado falha"; at.textContent = "sem resposta"; at.title = E.falhaConexao; }
  // a hora, e nao "ha 0 s": com o poll a cada 4 s o relativo seria sempre zero
  else if (E.atualizadoEm) { at.className = "atualizado"; at.textContent = `atualizado ${E.atualizadoEm.toLocaleTimeString("pt-BR")}`; at.title = "última atualização dos dados"; }

  const rodando = E.jobs.filter((j) => j.estado === "rodando").length;
  const pendentes = E.jobs.filter((j) => j.estado === "pendente").length;
  const contasAtencao = E.contas.filter((c) => c.ativa && c.saude !== "viva").length;
  $("nav-jobs").textContent = rodando ? String(rodando) : "";
  $("nav-fila").textContent = pendentes ? String(pendentes) : "";
  $("nav-contas").textContent = contasAtencao ? String(contasAtencao) : "";
  $("topo-contas-n").textContent = E.contas.length ? String(E.contas.length) : "";
  $("topo-contas-n").className = `badge ${contasAtencao ? "aviso" : ""}`;
}

/* ---------------- roteador ---------------- */

const ROTAS = {
  painel: { titulo: "Painel", casca: cascaPainel, pintar: pintarPainel },
  jobs: { titulo: "Jobs", casca: cascaJobs, pintar: pintarJobs },
  fila: { titulo: "Fila", casca: cascaFila, pintar: pintarFila },
  historico: { titulo: "Histórico", casca: cascaHistorico, pintar: pintarHistorico },
  contas: { titulo: "Contas", casca: cascaContas, pintar: pintarContas },
  saidas: { titulo: "Saídas", casca: cascaSaidas, pintar: pintarSaidas },
  projetos: { titulo: "Projetos", casca: cascaProjetos, pintar: pintarProjetos },
  config: { titulo: "Configurações", casca: cascaConfig, pintar: pintarConfig },
};

function lerRota() {
  const h = location.hash.replace(/^#\/?/, "");
  const [nome, query] = h.split("?");
  const parametros = {};
  if (query) new URLSearchParams(query).forEach((v, k) => { parametros[k] = v; });
  return { nome: ROTAS[nome] ? nome : "painel", parametros };
}

function navegar() {
  const { nome, parametros } = lerRota();
  E.rota = nome;
  E.parametros = parametros;
  if (nome === "historico" && parametros.estado) F.historico.estado = parametros.estado;
  if (nome === "saidas" && parametros.produto !== undefined) F.saidas.produto = parametros.produto;
  if (nome === "jobs" && parametros.estado) F.jobs.estado = parametros.estado;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("ativo", a.dataset.rota === nome));
  $("topo-titulo").textContent = ROTAS[nome].titulo;
  document.title = `${ROTAS[nome].titulo} · FlowPool`;
  Object.keys(assinaturas).forEach((k) => delete assinaturas[k]);
  $("conteudo").innerHTML = ROTAS[nome].casca();
  $("conteudo").scrollTop = 0;
  ligarCasca(nome);
  ROTAS[nome].pintar();
}

function pintarPagina() { ROTAS[E.rota].pintar(); }

/* ---------------- pecas reutilizadas ---------------- */

function miniatura(j, grande = false) {
  const cls = `mini-midia${grande ? " grande" : ""}`;
  if (!j.caminho) return `<span class="${cls}">${icone(ehVideoJob(j) ? "video" : "imagem")}</span>`;
  if (ehVideoJob(j)) return `<span class="${cls}"><video src="${esc(urlMidia(j.caminho))}#t=0.1" preload="metadata" muted playsinline disablepictureinpicture></video></span>`;
  return `<span class="${cls}"><img src="${esc(urlMidia(j.caminho))}" alt="" loading="lazy"></span>`;
}

// A fila do FlowPool (`pendente`) e a fila do FLOW sao coisas diferentes e o painel
// dizia "gerando" para a segunda. Job com `na_fila_desde` ja foi despachado, ja tem
// conta e ja gastou reserva — quem esta demorando e o Google, e e isso que a pilula
// precisa dizer, ou a pessoa vai procurar defeito no lado errado.
function pilulaEstado(j) {
  if (j.estado === "rodando" && j.na_fila_desde) {
    return `<span class="pilula rodando na-fila-flow" title="o Flow aceitou o pedido e ainda nao comecou a gerar — fila do Google, nao do FlowPool">na fila do Flow</span>`;
  }
  return `<span class="pilula ${esc(j.estado)}">${esc(NOMES_ESTADO[j.estado] || j.estado)}</span>`;
}

function tagModo(j) {
  return `<span class="tag">${esc(NOMES_MODO[j.modo] || j.modo)}</span>`;
}

function custoTexto(j) {
  const c = custoDoJob(j);
  if (c === null) return "—";
  return c === 0 ? "0" : String(c);
}

function celulaJob(j) {
  return `<div class="linha-mini">${miniatura(j)}<div>
    <span class="principal truncar" title="${esc(j.prompt)}">${esc(j.nome)}</span>
    <span class="sub truncar">${esc(j.produto || "—")} · <span class="mono">#${j.id}</span></span></div></div>`;
}

function celulaTipo(j) {
  return `${tagModo(j)}<span class="sub mono">${esc(j.modelo)} · ${esc(resumoConfig(j))}</span>`;
}

function celulaConta(j) {
  if (!j.conta_id) return `<span class="apagado">—</span>`;
  return `<span class="truncar" title="${esc(emailDaConta(j.conta_id))}">${esc(emailCurto(emailDaConta(j.conta_id)))}</span>`;
}

function vazio(titulo, texto) {
  return `<div class="vazio"><strong>${esc(titulo)}</strong>${esc(texto || "")}</div>`;
}

function botoesLinha(j) {
  const b = [];
  if (j.caminho) b.push(`<a class="botao mini icone fantasma" href="${esc(urlBaixar(j.caminho))}" download title="baixar" data-parar>${icone("baixar")}</a>`);
  if (j.estado === "pendente") b.push(`<button type="button" class="botao mini icone fantasma perigo" data-cancelar="${j.id}" title="cancelar">${icone("x")}</button>`);
  b.push(`<button type="button" class="botao mini icone fantasma" data-abrir-job="${j.id}" title="detalhes">${icone("seta-dir")}</button>`);
  return b.join("");
}

/* ---------------- pagina: painel ---------------- */

function cascaPainel() {
  return `<div class="pagina">
    <div class="tiles" id="tiles"></div>
    <div class="duas-colunas">
      <div class="coluna">
        <div class="cartao">
          <div class="cartao-cabeca"><h2>Pipeline ativo</h2><span class="leve" id="pipeline-sub"></span>
            <div class="acoes"><a class="botao mini fantasma" href="#/fila">Ver fila ${icone("seta-dir")}</a></div></div>
          <div class="tabela-envelope" id="pipeline"></div>
        </div>
        <div class="cartao">
          <div class="cartao-cabeca"><h2>Jobs recentes</h2>
            <div class="acoes"><a class="botao mini fantasma" href="#/jobs">Ver todos ${icone("seta-dir")}</a></div></div>
          <div class="tabela-envelope" id="recentes"></div>
        </div>
      </div>
      <div class="coluna">
        <div class="cartao" id="cartao-saude">
          <div class="cartao-cabeca"><h2>Saúde do pool</h2><div class="acoes"><a class="botao mini fantasma" href="#/contas">Contas ${icone("seta-dir")}</a></div></div>
          <div id="saude-pool"></div>
        </div>
        <div class="cartao">
          <div class="cartao-cabeca"><h2>Atenção</h2><span class="leve" id="atencao-sub"></span></div>
          <div class="atencao" id="atencao"></div>
        </div>
      </div>
    </div>
  </div>`;
}

function pintarPainel() {
  const s = E.saude;
  if (!s) { $("tiles").innerHTML = ""; return; }
  const total = E.contas.length;
  const vivas = E.contas.filter((c) => c.ativa && c.saude === "viva");
  const expiradas = E.contas.filter((c) => c.ativa && c.saude !== "viva").length;
  const inativas = E.contas.filter((c) => !c.ativa).length;
  const maior = vivas.reduce((m, c) => Math.max(m, c.saldo), 0);
  const pendentes = E.jobs.filter((j) => j.estado === "pendente");
  const semQuemPague = pendentes.filter((j) => !contasQuePagam(custoDoJob(j)).length).length;

  const tiles = [
    { rotulo: "Crédito disponível", icone: "saldo", valor: `${s.credito_total}`,
      sub: vivas.length ? `maior saldo numa conta: ${maior} — crédito não se soma entre contas` : "nenhuma conta viva",
      nivel: s.credito_total === 0 ? "erro" : maior < 12 ? "aviso" : "" },
    { rotulo: "Contas saudáveis", icone: "contas", valor: `${vivas.length}<small>/ ${total}</small>`,
      sub: expiradas ? `${expiradas} com sessão fora${inativas ? ` · ${inativas} desativada${inativas > 1 ? "s" : ""}` : ""}` : inativas ? `todas as ativas de pé · ${inativas} desativada${inativas > 1 ? "s" : ""}` : "todas as sessões de pé",
      nivel: expiradas ? "aviso" : "" },
    { rotulo: "Gerando agora", icone: "bolt", valor: `${s.gerando}<small>/ ${s.max_simultaneos}</small>`,
      sub: `${s.contas_ocupadas} ocupada${s.contas_ocupadas === 1 ? "" : "s"}${s.sessoes_ociosas ? ` · ${s.sessoes_ociosas} navegador${s.sessoes_ociosas === 1 ? "" : "es"} em espera` : ""}`, nivel: "" },
    { rotulo: "Na fila", icone: "fila", valor: `${pendentes.length}`,
      sub: semQuemPague ? `${semQuemPague} sem conta que pague` : pendentes.length ? "todos têm conta com crédito" : "fila vazia",
      nivel: semQuemPague ? "aviso" : "" },
  ];
  const assTiles = JSON.stringify(tiles);
  if (mudou("tiles", assTiles)) {
    $("tiles").innerHTML = tiles.map((t) => `<div class="cartao tile">
      <span class="tile-rotulo">${icone(t.icone)}${esc(t.rotulo)}</span>
      <span class="tile-valor">${t.valor}</span>
      <span class="tile-sub ${t.nivel}">${esc(t.sub)}</span></div>`).join("");
  }

  // pipeline: redesenha a cada volta — e pequeno, sem midia, e o tempo decorrido conta
  const rodando = E.jobs.filter((j) => j.estado === "rodando");
  $("pipeline-sub").textContent = rodando.length ? `${rodando.length} de ${s.max_simultaneos} janelas` : "";
  $("pipeline").innerHTML = rodando.length ? `<table class="tabela"><thead><tr>
      <th>Job</th><th>Tipo</th><th>Conta</th><th class="num">Custo</th><th class="num">Decorrido</th><th></th></tr></thead>
    <tbody>${rodando.map((j) => `<tr class="clicavel compacta" data-job="${j.id}">
      <td>${celulaJob(j)}</td><td>${celulaTipo(j)}</td><td>${celulaConta(j)}</td>
      <td class="num mono">${custoTexto(j)}</td>
      <td class="num"><span class="pipeline-tempo">${esc(decorrido(j.atualizado_em))}</span></td>
      <td class="acoes">${botoesLinha(j)}</td></tr>`).join("")}</tbody></table>`
    : vazio("Nenhuma geração em curso", pendentes.length ? "há jobs na fila esperando conta com crédito" : "a fila está vazia");

  const recentes = E.jobs.slice(0, 8);
  const assRec = recentes.map((j) => `${j.id}:${j.estado}:${j.caminho || ""}`).join("|") + Math.floor(Date.now() / 60000);
  if (mudou("recentes", assRec)) {
    $("recentes").innerHTML = recentes.length ? `<table class="tabela estreita"><thead><tr>
        <th>Job</th><th>Tipo</th><th class="num">Custo</th><th>Estado</th><th class="num">Criado</th><th></th></tr></thead>
      <tbody>${recentes.map((j) => linhaJob(j, true)).join("")}</tbody></table>`
      : vazio("Nada gerado ainda", "Use “Nova geração” no topo para mandar o primeiro job.");
  }

  // saude do pool: estado + barra de credito por conta
  const st = estadoSistema();
  const teto = E.info ? E.info.teto_diario_free : 50;
  const assSaude = JSON.stringify([st, E.contas.map((c) => [c.id, c.saldo, c.saude, c.ativa, c.tier])]);
  if (mudou("saude", assSaude)) {
    $("saude-pool").innerHTML = `<div class="saude-resumo">
        <span class="pilula ${st.nivel === "ok" ? "viva" : st.nivel === "aviso" ? "expirada" : "bloqueada"}">${esc(st.texto)}</span>
        <span class="leve">${vivas.length} viva${vivas.length === 1 ? "" : "s"} · ${s.credito_total} créditos</span></div>
      <div class="cartao-corpo"><div class="creditos">${E.contas.length ? E.contas.map((c) => {
        const morta = !c.ativa || c.saude !== "viva";
        const base = c.tier === "free" ? teto : Math.max(teto, c.saldo);
        const pct = Math.min(100, Math.round((c.saldo / base) * 100));
        const cls = morta ? "morta" : c.saldo === 0 ? "zerada" : c.saldo < 12 ? "baixa" : "";
        return `<div class="credito-linha ${morta ? "morta" : ""}" title="${esc(c.email)} · ${esc(c.saude)}${c.ativa ? "" : " · desativada"}">
          <span class="email truncar">${esc(emailCurto(c.email))}</span>
          <span class="barra ${cls}"><i style="width:${pct}%"></i></span>
          <span class="num">${c.saldo}${c.saldo_confiavel ? "" : "~"}</span></div>`;
      }).join("") : `<span class="leve pequeno">Nenhuma conta cadastrada.</span>`}</div></div>`;
  }

  const itens = itensAtencao();
  const assAt = JSON.stringify(itens.map((i) => [i.nivel, i.texto, i.sub]));
  if (mudou("atencao", assAt)) {
    $("atencao-sub").textContent = itens.length ? `${itens.length} item${itens.length > 1 ? "s" : ""}` : "";
    $("atencao").innerHTML = itens.length ? itens.slice(0, 8).map((i, n) => `<div class="atencao-item">
        <span class="ponto ${i.nivel === "info" ? "" : i.nivel}"></span>
        <div class="texto"><span class="truncar">${esc(i.texto)}</span><small>${esc(i.sub)}</small></div>
        ${i.acao ? (i.href ? `<a class="botao mini" href="${esc(i.href)}">${esc(i.acao)}</a>` : `<button type="button" class="botao mini" data-atencao="${n}">${esc(i.acao)}</button>`) : ""}
      </div>`).join("") : `<div class="vazio"><strong>Nada pendente</strong>Todas as contas ativas estão de pé e com saldo.</div>`;
    $("atencao").onclick = (ev) => {
      const b = ev.target.closest("[data-atencao]");
      if (b) itensAtencao()[Number(b.dataset.atencao)].fn();
    };
  }
}

/* `estreita`: a versao para cartao de meia largura — sem a coluna da conta e so a
   seta nas acoes. O restante e identico, para a linha ser a mesma em toda tela. */
function linhaJob(j, estreita = false) {
  const erro = j.estado === "falhou" && j.erro ? `<span class="sub erro-curto truncar" title="${esc(j.erro)}">${esc(j.erro)}</span>` : "";
  return `<tr class="clicavel compacta" data-job="${j.id}">
    <td>${celulaJob(j)}</td><td>${celulaTipo(j)}</td>${estreita ? "" : `<td>${celulaConta(j)}</td>`}
    <td class="num mono">${custoTexto(j)}</td>
    <td>${pilulaEstado(j)}${erro}</td>
    <td class="num leve" title="${esc(dataCompleta(j.criado_em))}">${esc(relativo(j.criado_em))}</td>
    <td class="acoes">${estreita ? `<button type="button" class="botao mini icone fantasma" data-abrir-job="${j.id}" title="detalhes">${icone("seta-dir")}</button>` : botoesLinha(j)}</td></tr>`;
}

/* ---------------- pagina: jobs ---------------- */

function cascaJobs() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Jobs</h1><p>Tudo o que entrou na fila, mais recente primeiro.</p></div>
      <div class="acoes"><button type="button" class="botao primario" data-nova>${icone("mais")}Nova geração</button></div></div>
    <div class="barra-filtros">
      <div class="busca">${icone("busca")}<input type="search" id="f-busca" placeholder="Buscar por nome, prompt ou produto" value="${esc(F.jobs.busca)}"></div>
      <div class="abas" id="f-estado"></div>
      <span class="cresce"></span>
      <select id="f-modo"></select>
      <select id="f-produto"></select>
    </div>
    <div class="cartao"><div class="tabela-envelope" id="tabela-jobs"></div><div id="jobs-mais"></div></div>
  </div>`;
}

function selectOpcoes(el, valores, atual, rotuloTodos) {
  const html = `<option value="">${esc(rotuloTodos)}</option>` + valores.map((v) =>
    `<option value="${esc(v.valor)}" ${v.valor === atual ? "selected" : ""}>${esc(v.rotulo)}</option>`).join("");
  if (el.innerHTML !== html) el.innerHTML = html;
}

function produtosConhecidos() {
  return [...new Set(E.jobs.map((j) => j.produto).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function abasEstado(el, chave, atual, contagens, estados) {
  const html = estados.map((e) => `<button type="button" data-valor="${e}" class="${e === atual ? "ativo" : ""}">${esc(e === "todos" ? "Todos" : NOMES_ESTADO[e])} <b>${contagens[e] || 0}</b></button>`).join("");
  if (el.innerHTML !== html) el.innerHTML = html;
}

function filtrarJobs() {
  const f = F.jobs;
  const b = f.busca.trim().toLowerCase();
  return E.jobs.filter((j) =>
    (f.estado === "todos" || j.estado === f.estado) &&
    (!f.modo || j.modo === f.modo) &&
    (!f.produto || j.produto === f.produto) &&
    (!b || `${j.nome} ${j.prompt} ${j.produto} #${j.id}`.toLowerCase().includes(b)));
}

function pintarJobs() {
  const contagens = { todos: E.jobs.length };
  E.jobs.forEach((j) => { contagens[j.estado] = (contagens[j.estado] || 0) + 1; });
  abasEstado($("f-estado"), "jobs", F.jobs.estado, contagens, ["todos", "pendente", "rodando", "pronto", "falhou", "cancelado"]);
  selectOpcoes($("f-modo"), Object.entries(NOMES_MODO).map(([v, r]) => ({ valor: v, rotulo: r })), F.jobs.modo, "Todos os tipos");
  selectOpcoes($("f-produto"), produtosConhecidos().map((p) => ({ valor: p, rotulo: p })), F.jobs.produto, "Todos os produtos");

  const lista = filtrarJobs();
  const visiveis = lista.slice(0, F.jobs.limite);
  const ass = visiveis.map((j) => `${j.id}:${j.estado}:${j.caminho || ""}:${j.conta_id}`).join("|") + `#${lista.length}` + Math.floor(Date.now() / 60000);
  if (!mudou("jobs", ass)) return;
  $("tabela-jobs").innerHTML = visiveis.length ? `<table class="tabela"><thead><tr>
      <th>Job</th><th>Tipo</th><th>Conta</th><th class="num">Custo</th><th>Estado</th><th class="num">Criado</th><th></th></tr></thead>
    <tbody>${visiveis.map((j) => linhaJob(j)).join("")}</tbody></table>`
    : vazio(E.jobs.length ? "Nenhum job com esse filtro" : "Nenhum job ainda", E.jobs.length ? "" : "Use “Nova geração” para mandar o primeiro.");
  $("jobs-mais").innerHTML = lista.length > visiveis.length
    ? `<div class="vazio" style="padding:12px"><button type="button" class="botao mini" data-mais>Mostrar mais ${Math.min(50, lista.length - visiveis.length)} de ${lista.length - visiveis.length}</button></div>` : "";
}

/* ---------------- pagina: fila ---------------- */

function cascaFila() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Fila</h1><p>O que está gerando e o que espera. A ordem é prioridade e depois chegada — a mesma que o motor usa.</p></div>
      <div class="acoes"><button type="button" class="botao primario" data-nova>${icone("mais")}Nova geração</button></div></div>
    <div class="cartao"><div class="cartao-cabeca"><h2>Gerando agora</h2><span class="leve" id="fila-gerando-sub"></span></div><div class="tabela-envelope" id="fila-gerando"></div></div>
    <div class="cartao"><div class="cartao-cabeca"><h2>Na fila</h2><span class="leve" id="fila-pendente-sub"></span></div><div class="tabela-envelope" id="fila-pendente"></div></div>
  </div>`;
}

function pintarFila() {
  const s = E.saude;
  const rodando = E.jobs.filter((j) => j.estado === "rodando");
  $("fila-gerando-sub").textContent = s ? `${rodando.length} de ${s.max_simultaneos} janelas · ${s.contas_ocupadas} conta${s.contas_ocupadas === 1 ? "" : "s"} ocupada${s.contas_ocupadas === 1 ? "" : "s"}` : "";
  $("fila-gerando").innerHTML = rodando.length ? `<table class="tabela"><thead><tr>
      <th>Job</th><th>Tipo</th><th>Conta</th><th class="num">Custo</th><th class="num">Decorrido</th><th class="num">Tentativa</th><th></th></tr></thead>
    <tbody>${rodando.map((j) => `<tr class="clicavel compacta" data-job="${j.id}">
      <td>${celulaJob(j)}</td><td>${celulaTipo(j)}</td><td>${celulaConta(j)}</td>
      <td class="num mono">${custoTexto(j)}</td>
      <td class="num"><span class="pipeline-tempo">${esc(decorrido(j.atualizado_em))}</span></td>
      <td class="num mono leve">${j.tentativas + 1}</td>
      <td class="acoes">${botoesLinha(j)}</td></tr>`).join("")}</tbody></table>`
    : vazio("Nenhuma geração em curso");

  const pendentes = E.jobs.filter((j) => j.estado === "pendente")
    .sort((a, b) => (b.prioridade - a.prioridade) || (a.id - b.id));
  const semQuemPague = pendentes.filter((j) => !contasQuePagam(custoDoJob(j)).length).length;
  $("fila-pendente-sub").textContent = pendentes.length
    ? `${pendentes.length} esperando${semQuemPague ? ` · ${semQuemPague} sem conta que pague` : ""}` : "";
  $("fila-pendente").innerHTML = pendentes.length ? `<table class="tabela"><thead><tr>
      <th style="width:40px">#</th><th>Job</th><th>Tipo</th><th class="num">Custo est.</th><th>Quem paga</th><th>Próxima avaliação</th><th></th></tr></thead>
    <tbody>${pendentes.map((j, n) => {
      const custo = custoDoJob(j);
      const pagam = contasQuePagam(custo);
      const quem = pagam.length
        ? `<span class="leve">${pagam.length} conta${pagam.length > 1 ? "s" : ""}</span><span class="sub truncar" style="max-width:220px">${esc(pagam.map((c) => emailCurto(c.email)).join(", "))}</span>`
        : `<span class="pilula expirada sem-ponto">nenhuma conta paga ${custo === null ? "" : `${custo} cr`}</span>`;
      const quando = j.reavaliar_em ? `<span class="leve">${esc(emQuanto(j.reavaliar_em))}</span>${j.erro ? `<span class="sub truncar erro-curto" style="max-width:240px" title="${esc(j.erro)}">${esc(j.erro)}</span>` : ""}` : `<span class="apagado">na próxima volta</span>`;
      return `<tr class="clicavel compacta" data-job="${j.id}">
        <td><span class="fila-posicao">${n + 1}</span></td>
        <td>${celulaJob(j)}</td><td>${celulaTipo(j)}</td>
        <td class="num mono">${custo === null ? "—" : custo}</td>
        <td>${quem}</td><td>${quando}</td>
        <td class="acoes">${botoesLinha(j)}</td></tr>`;
    }).join("")}</tbody></table>`
    : vazio("Fila vazia", "Jobs novos entram aqui e saem assim que uma conta com crédito estiver livre.");
}

/* ---------------- pagina: historico ---------------- */

function cascaHistorico() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Histórico</h1><p>Jobs encerrados: prontos, falhos e cancelados, por dia.</p></div></div>
    <div class="barra-filtros"><div class="abas" id="f-hist"></div></div>
    <div class="cartao"><div class="tabela-envelope" id="tabela-hist"></div><div id="hist-mais"></div></div>
  </div>`;
}

function pintarHistorico() {
  const encerrados = E.jobs.filter((j) => ["pronto", "falhou", "cancelado"].includes(j.estado))
    .sort((a, b) => (data(b.atualizado_em) || 0) - (data(a.atualizado_em) || 0));
  const contagens = { todos: encerrados.length };
  encerrados.forEach((j) => { contagens[j.estado] = (contagens[j.estado] || 0) + 1; });
  abasEstado($("f-hist"), "historico", F.historico.estado, contagens, ["todos", "pronto", "falhou", "cancelado"]);

  const lista = encerrados.filter((j) => F.historico.estado === "todos" || j.estado === F.historico.estado);
  const visiveis = lista.slice(0, F.historico.limite);
  const ass = visiveis.map((j) => `${j.id}:${j.estado}:${j.caminho || ""}`).join("|") + `#${lista.length}` + Math.floor(Date.now() / 60000);
  if (!mudou("hist", ass)) return;

  let dia = null;
  const linhas = visiveis.map((j) => {
    const d = diaDe(j.atualizado_em);
    const cabeca = d !== dia ? `<tr class="grupo-data"><td colspan="7">${esc(d)}</td></tr>` : "";
    dia = d;
    const diag = j.estado === "falhou"
      ? `<button type="button" class="botao mini sempre" data-abrir-job="${j.id}" data-diagnostico>Diagnóstico</button>` : "";
    return `${cabeca}<tr class="clicavel compacta" data-job="${j.id}">
      <td>${celulaJob(j)}</td><td>${celulaTipo(j)}</td><td>${celulaConta(j)}</td>
      <td class="num mono">${custoTexto(j)}</td>
      <td>${pilulaEstado(j)}${j.estado === "falhou" && j.erro ? `<span class="sub erro-curto truncar" title="${esc(j.erro)}">${esc(j.erro)}</span>` : ""}</td>
      <td class="num leve" title="${esc(dataCompleta(j.atualizado_em))}">${esc(data(j.atualizado_em) ? data(j.atualizado_em).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "—")}</td>
      <td class="acoes">${diag}${botoesLinha(j)}</td></tr>`;
  });
  $("tabela-hist").innerHTML = visiveis.length ? `<table class="tabela"><thead><tr>
      <th>Job</th><th>Tipo</th><th>Conta</th><th class="num">Custo</th><th>Resultado</th><th class="num">Hora</th><th></th></tr></thead>
    <tbody>${linhas.join("")}</tbody></table>` : vazio("Nada encerrado ainda");
  $("hist-mais").innerHTML = lista.length > visiveis.length
    ? `<div class="vazio" style="padding:12px"><button type="button" class="botao mini" data-mais>Mostrar mais</button></div>` : "";
}

/* ---------------- pagina: contas ---------------- */

function cascaContas() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Contas</h1><p id="contas-sub">O pool. Cada conta tem sessão própria, saldo próprio e, se quiser, IP próprio.</p></div>
      <div class="acoes"><button type="button" class="botao primario" data-nova-conta>${icone("mais")}Adicionar conta</button></div></div>
    <div id="aviso-colisao"></div>
    <div class="cartao"><div class="tabela-envelope" id="tabela-contas"></div></div>
  </div>`;
}

function linhaConta(c) {
  const teto = E.info ? E.info.teto_diario_free : 50;
  const base = c.tier === "free" ? teto : Math.max(teto, c.saldo);
  const pct = Math.min(100, Math.round((c.saldo / base) * 100));
  const morta = !c.ativa || c.saude !== "viva";
  const seca = c.seca_ate && new Date(c.seca_ate) > new Date();
  const ocupado = sessaoEmCurso ? "disabled" : "";
  return `<tr class="clicavel" data-conta="${c.id}">
    <td><div><span class="principal">${esc(c.email)}</span><span class="sub mono">#${c.id}${c.proxy ? "" : " · IP desta máquina"}</span></div></td>
    <td><span class="tag tier-${esc(c.tier)}">${esc(c.tier)}</span></td>
    <td>
      <div class="credito-linha" style="grid-template-columns: 36px 90px; gap: 8px">
        <span class="num" style="text-align:left">${c.saldo}${c.saldo_confiavel ? "" : `<span class="leve" title="saldo estimado desde a última leitura">~</span>`}</span>
        <span class="barra ${morta ? "morta" : c.saldo === 0 ? "zerada" : c.saldo < 12 ? "baixa" : ""}"><i style="width:${pct}%"></i></span>
      </div>
      <span class="sub">${c.saldo_lido_em ? `lido ${esc(relativo(c.saldo_lido_em))}` : "nunca lido"}${seca ? ` · <span class="erro-curto">seca ${esc(emQuanto(c.seca_ate))}</span>` : ""}</span>
    </td>
    <td>${c.ativa ? `<span class="pilula ${esc(c.saude)}">${esc(c.saude)}</span>` : `<span class="pilula inativa">desativada</span>`}
        ${c.falhas ? `<span class="sub erro-curto">${c.falhas} falha${c.falhas > 1 ? "s" : ""} seguida${c.falhas > 1 ? "s" : ""}</span>` : ""}</td>
    <td>${c.proxy ? `<span class="mono truncar" style="max-width:200px;display:block" title="${esc(c.proxy)}">${esc(c.proxy)}</span>` : `<span class="apagado">—</span>`}</td>
    <td><label class="interruptor" title="${c.ativa ? "desativar" : "ativar"}" data-parar>
        <input type="checkbox" class="acao-ativa" data-id="${c.id}" ${c.ativa ? "checked" : ""}><span class="trilho"></span></label></td>
    <td class="acoes">
      <button type="button" class="botao mini sempre ${c.saude !== "viva" && c.ativa ? "aviso" : ""}" data-revalidar="${c.id}" ${ocupado} title="abre o Chrome para você logar de novo">${icone("atualizar")}Revalidar</button>
      <button type="button" class="botao mini sempre" data-saldo="${c.id}" title="lê o saldo agora no Flow — abre uma janela, não gasta crédito">${icone("saldo")}Ler saldo</button>
      <button type="button" class="botao mini sempre" data-abrir-conta="${c.id}" title="abre o Chrome logado nesta conta">${icone("janela")}Abrir</button>
      <button type="button" class="botao mini icone fantasma sempre" data-editar-conta="${c.id}" title="editar">${icone("editar")}</button>
    </td></tr>`;
}

function pintarContas() {
  const vivas = E.contas.filter((c) => c.ativa && c.saude === "viva").length;
  $("contas-sub").textContent = E.contas.length
    ? `${E.contas.length} no pool · ${vivas} viva${vivas === 1 ? "" : "s"} · ${E.saude ? E.saude.credito_total : "—"} créditos somados (mas cada job cabe em uma conta só)`
    : "Nenhuma conta ainda. Cadastre a primeira — o cadastro abre um Chrome para você logar.";
  const batem = colisoesDeIp();
  $("aviso-colisao").innerHTML = batem.length ? batem.map(([ip, cs]) =>
    `<div class="alerta aviso">${icone("alerta")}<div class="alerta-texto"><strong>${cs.length} contas saem pelo mesmo IP (${esc(ip)}).</strong>
      ${cs.map((c) => esc(c.email)).join(", ")} — é isso que o proxy por conta existe para evitar. Sem proxy, todas saem pelo IP desta máquina.</div></div>`).join("")
    : "";

  const ass = JSON.stringify(E.contas) + sessaoEmCurso + Math.floor(Date.now() / 60000);
  if (!mudou("contas", ass)) return;
  const foco = document.activeElement;
  if ($("tabela-contas").contains(foco) && foco.tagName === "INPUT" && foco.type !== "checkbox") return;
  $("tabela-contas").innerHTML = E.contas.length ? `<table class="tabela"><thead><tr>
      <th>Conta</th><th>Tier</th><th>Crédito</th><th>Sessão</th><th>Proxy</th><th>Ativa</th><th></th></tr></thead>
    <tbody>${E.contas.map(linhaConta).join("")}</tbody></table>`
    : vazio("Nenhuma conta no pool", "Clique em “Adicionar conta”.");
}

/* ---------------- pagina: saidas ---------------- */

function cascaSaidas() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Saídas</h1><p id="saidas-sub">Tudo o que foi gerado e está em disco. Passe o mouse para tocar o vídeo; clique para abrir.</p></div>
      <div class="acoes"><button type="button" class="botao" id="alternar-selecao">Selecionar</button></div></div>
    <div class="barra-filtros">
      <div class="abas" id="f-tipo">
        <button type="button" data-valor="todas" class="${F.saidas.tipo === "todas" ? "ativo" : ""}">Todas</button>
        <button type="button" data-valor="imagem" class="${F.saidas.tipo === "imagem" ? "ativo" : ""}">Imagens</button>
        <button type="button" data-valor="video" class="${F.saidas.tipo === "video" ? "ativo" : ""}">Vídeos</button>
      </div>
      <span class="cresce"></span>
      <select id="f-saidas-produto"></select>
    </div>
    <div class="galeria" id="galeria"></div>
    <div class="barra-selecao oculto" id="barra-selecao">
      <span id="selecao-conta"></span>
      <button type="button" class="botao mini primario" id="baixar-selecao">${icone("baixar")}Baixar em zip</button>
      <button type="button" class="botao mini" id="limpar-selecao">Limpar</button>
    </div>
  </div>`;
}

function proporcaoCss(p) {
  const [a, b] = String(p || "9:16").split(":");
  return `${Number(a) || 9} / ${Number(b) || 16}`;
}

function cardSaida(j) {
  const marcado = selecao.has(j.id);
  const url = urlMidia(j.caminho);
  const midia = ehVideoJob(j)
    ? `<video src="${esc(url)}#t=0.1" preload="metadata" muted playsinline loop disablepictureinpicture></video>
       <span class="selo">${icone("play")}${esc(j.duracao)}</span>`
    : `<img src="${esc(url)}" alt="" loading="lazy">`;
  return `<article class="card${marcado ? " marcado" : ""}" data-id="${j.id}" style="--prop: ${proporcaoCss(j.proporcao)}">
    <div class="moldura">${midia}
      <label class="marcar" title="selecionar" data-parar><input type="checkbox" class="marcar-job" data-id="${j.id}" ${marcado ? "checked" : ""}></label></div>
    <div class="card-rodape"><span class="nome" title="${esc(j.nome)} — ${esc(j.prompt)}">${esc(j.nome)}</span>
      <span class="tag">${esc(j.modelo)}</span><span class="custo">${custoTexto(j)} cr</span></div>
  </article>`;
}

function medirGrade() {
  const g = $("galeria");
  if (!g) return;
  const estilo = getComputedStyle(g);
  const linha = parseFloat(estilo.gridAutoRows) || 4;
  const vao = parseFloat(estilo.rowGap) || 0;
  g.querySelectorAll(".card").forEach((c) => {
    const h = c.getBoundingClientRect().height;
    if (h > 0) c.style.gridRowEnd = `span ${Math.ceil((h + vao) / (linha + vao))}`;
  });
}

function saidasFiltradas() {
  return E.jobs.filter((j) => j.caminho &&
    (!F.saidas.produto || j.produto === F.saidas.produto) &&
    (F.saidas.tipo === "todas" || (F.saidas.tipo === "video") === ehVideoJob(j)));
}

function pintarSaidas() {
  selectOpcoes($("f-saidas-produto"), produtosConhecidos().map((p) => ({ valor: p, rotulo: p })), F.saidas.produto, "Todos os produtos");
  const lista = saidasFiltradas();
  const g = $("galeria");
  g.classList.toggle("selecionando", F.saidas.selecionando);
  $("alternar-selecao").textContent = F.saidas.selecionando ? "Concluir seleção" : "Selecionar";
  const ass = lista.map((j) => `${j.id}:${j.caminho}`).join("|") + `#${[...selecao].sort().join(",")}`;
  if (mudou("saidas", ass)) {
    g.innerHTML = lista.length ? lista.map(cardSaida).join("")
      : `<div class="vazio" style="grid-column:1/-1"><strong>Nenhuma saída${E.jobs.some((j) => j.caminho) ? " com esse filtro" : " ainda"}</strong>${E.jobs.some((j) => j.caminho) ? "" : "Os arquivos aparecem aqui assim que o primeiro job terminar."}</div>`;
    medirGrade();
  }
  const barra = $("barra-selecao");
  if (!selecao.size) esconder(barra);
  else { barra.classList.remove("oculto"); $("selecao-conta").textContent = `${selecao.size} selecionad${selecao.size === 1 ? "o" : "os"}`; }
}

function baixarSelecao() {
  const caminhos = E.jobs.filter((j) => selecao.has(j.id) && j.caminho).map((j) => j.caminho);
  if (!caminhos.length) return;
  if (caminhos.length === 1) { window.location.href = urlBaixar(caminhos[0]); return; }
  window.location.href = `/baixar-lote?${caminhos.map((c) => `caminho=${encodeURIComponent(c)}`).join("&")}`;
}

/* ---------------- pagina: projetos ---------------- */

function cascaProjetos() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Projetos</h1><p>Um produto é a pasta em disco e, ao mesmo tempo, o nome do projeto dentro do Flow. Projeto pertence a uma conta: cada conta que gerou algo do produto tem o seu.</p></div></div>
    <div class="cartao"><div class="tabela-envelope" id="tabela-projetos"></div></div>
  </div>`;
}

function pintarProjetos() {
  const ass = JSON.stringify(E.projetos) + E.jobs.map((j) => `${j.id}:${j.estado}`).join("|");
  if (!mudou("projetos", ass)) return;
  const produtos = new Map();
  for (const p of produtosConhecidos()) produtos.set(p, { jobs: [], projetos: [] });
  for (const j of E.jobs) { if (j.produto && produtos.has(j.produto)) produtos.get(j.produto).jobs.push(j); }
  for (const p of E.projetos) {
    if (!produtos.has(p.produto)) produtos.set(p.produto, { jobs: [], projetos: [] });
    produtos.get(p.produto).projetos.push(p);
  }
  const linhas = [...produtos.entries()].map(([nome, d]) => {
    const prontos = d.jobs.filter((j) => j.caminho).length;
    const ultimo = d.jobs[0];
    return `<tr>
      <td><div><span class="principal">${esc(nome)}</span><span class="sub mono">${esc(nome)}/clipes</span></div></td>
      <td class="num mono">${d.jobs.length}</td>
      <td class="num mono">${prontos}</td>
      <td class="leve">${ultimo ? esc(relativo(ultimo.criado_em)) : "—"}</td>
      <td><div class="chips">${d.projetos.length ? d.projetos.map((p) => `<span class="chip-projeto" title="${esc(p.nome_remoto)} · ${esc(p.id_remoto)}">
          ${esc(emailCurto(emailDaConta(p.conta_id)))} <span class="mono">${esc(p.id_remoto.slice(0, 8))}</span>
          <a class="fechar-x" href="https://flow.google.com/project/${esc(p.id_remoto)}" target="_blank" rel="noopener" title="abrir no Flow (precisa estar logado nessa conta no navegador)">${icone("externo")}</a>
          <button type="button" class="fechar-x" data-esquecer="${p.id}" title="esquecer o vínculo local (o projeto continua no Flow)">${icone("x")}</button></span>`).join("")
        : `<span class="apagado">nenhum projeto adotado ainda</span>`}</div></td>
      <td class="acoes">
        <button type="button" class="botao mini sempre" data-gerar-produto="${esc(nome)}">${icone("mais")}Gerar</button>
        <a class="botao mini sempre" href="#/saidas?produto=${encodeURIComponent(nome)}">${icone("saidas")}Saídas</a></td></tr>`;
  });
  $("tabela-projetos").innerHTML = linhas.length ? `<table class="tabela"><thead><tr>
      <th>Produto</th><th class="num">Jobs</th><th class="num">Prontos</th><th>Última geração</th><th>Projetos no Flow (por conta)</th><th></th></tr></thead>
    <tbody>${linhas.join("")}</tbody></table>`
    : vazio("Nenhum produto ainda", "O primeiro job cria o produto — e o projeto no Flow.");
}

async function esquecerProjeto(id) {
  const p = E.projetos.find((x) => x.id === id);
  if (!p) return;
  if (!window.confirm(`Esquecer o vínculo de “${p.produto}” em ${emailDaConta(p.conta_id)}?\n\nO projeto continua existindo no Flow e será readotado pelo nome na próxima geração.`)) return;
  try {
    await api(`/projetos/${id}`, { metodo: "DELETE" });
    toast("ok", "Vínculo esquecido. O projeto continua no Flow.");
    await atualizar();
  } catch (e) { toast("erro", e.message); }
}

/* ---------------- pagina: configuracoes ---------------- */

function cascaConfig() {
  return `<div class="pagina">
    <div class="pagina-cabeca"><div><h1>Configurações</h1><p>O que o serviço é e como este painel se comporta. As regras de geração vêm do serviço e não se editam aqui.</p></div></div>
    <div class="config-grade">
      <div class="coluna">
        <div class="cartao"><div class="cartao-cabeca"><h2>Serviço</h2></div><div class="cartao-corpo"><div class="lista-chave" id="cfg-servico"></div></div></div>
        <div class="cartao"><div class="cartao-cabeca"><h2>Painel</h2><span class="leve">só neste navegador</span></div><div class="cartao-corpo"><div class="lista-chave" id="cfg-painel"></div></div></div>
        <div class="cartao"><div class="cartao-cabeca"><h2>Pela linha de comando</h2></div><div class="cartao-corpo">
          <p class="dica">A skill <code>flowpool-gerar</code> do Claude fala com esta mesma API. Da raiz do projeto:</p>
          <pre class="mono" style="margin:10px 0 0;white-space:pre-wrap;color:var(--fg-2)">python ~/.claude/skills/flowpool-gerar/scripts/flowpool.py contas
python ~/.claude/skills/flowpool-gerar/scripts/flowpool.py video "prompt" --produto "Nome" --nome clipe-1 --ref foto.png</pre></div></div>
      </div>
      <div class="coluna">
        <div class="cartao"><div class="cartao-cabeca"><h2>Tabela de custos</h2><span class="leve">créditos por geração</span></div><div class="tabela-envelope" id="cfg-custos"></div></div>
        <div class="cartao"><div class="cartao-cabeca"><h2>Regras por modo</h2></div><div class="tabela-envelope" id="cfg-modos"></div></div>
      </div>
    </div>
  </div>`;
}

function pintarConfig() {
  const i = E.info;
  const ass = JSON.stringify(i) + PREF.ler("intervalo", 4000) + PREF.ler("hover", true);
  if (!mudou("config", ass)) return;
  $("cfg-servico").innerHTML = `
    <div><span>Endereço</span><b class="mono">${esc(location.origin)}</b></div>
    <div><span>Documentação da API</span><b><a class="mono" href="/docs" target="_blank" rel="noopener">${esc(location.origin)}/docs ${icone("externo")}</a></b></div>
    <div><span>Raiz do projeto</span><b class="mono truncar" title="${esc(i ? i.raiz : "")}">${esc(i ? i.raiz : "—")}</b></div>
    <div><span>Gerações simultâneas</span><b class="mono">${i ? i.max_simultaneos : "—"}</b><span class="leve pequeno">teto de janelas — o limite real é o número de contas livres</span></div>
    <div><span>Teto diário (free)</span><b class="mono">${i ? i.teto_diario_free : "—"} créditos</b></div>
    <div><span>Banco</span><b class="mono">flowpool/flowpool.db</b></div>`;
  const intervalo = PREF.ler("intervalo", 4000);
  $("cfg-painel").innerHTML = `
    <div><span>Atualizar a cada</span><b><select id="cfg-intervalo">${[2000, 4000, 8000, 15000].map((v) => `<option value="${v}" ${v === intervalo ? "selected" : ""}>${v / 1000} s</option>`).join("")}</select></b></div>
    <div><span>Tocar vídeo ao passar o mouse</span><b><label class="interruptor"><input type="checkbox" id="cfg-hover" ${PREF.ler("hover", true) ? "checked" : ""}><span class="trilho"></span></label></b></div>
    <div><span>Produto padrão</span><b class="mono">${esc(PREF.ler("produto", "") || "—")}</b><span class="leve pequeno">o último usado na nova geração</span></div>`;
  $("cfg-intervalo").onchange = (ev) => { PREF.gravar("intervalo", Number(ev.target.value)); reagendar(); };
  $("cfg-hover").onchange = (ev) => PREF.gravar("hover", ev.target.checked);

  if (!i) { $("cfg-custos").innerHTML = vazio("Sem dados do serviço"); $("cfg-modos").innerHTML = ""; return; }
  const modelos = Object.keys(i.custos);
  $("cfg-custos").innerHTML = `<table class="tabela tabela-custos"><thead><tr><th>Modelo</th><th>Tier</th>${i.duracoes.map((d) => `<th>${esc(d)}</th>`).join("")}</tr></thead>
    <tbody>${modelos.flatMap((m) => Object.entries(i.custos[m]).map(([tier, t]) => `<tr>
      <td>${esc(m)}</td><td>${esc(tier)}</td>${t["-"] !== undefined
        ? `<td colspan="${i.duracoes.length}" style="text-align:left">${t["-"]} — imagem, sem duração</td>`
        : i.duracoes.map((d) => `<td>${t[d] === undefined ? "—" : t[d]}</td>`).join("")}</tr>`)).join("")}</tbody></table>
    <p class="dica" style="padding:10px 16px">Estimativa para reservar saldo antes de despachar. Quem manda é a leitura real do saldo, reconciliada depois de cada geração.</p>`;
  $("cfg-modos").innerHTML = `<table class="tabela"><thead><tr><th>Modo</th><th>Modelos</th><th class="num">Refs</th><th class="num">Qtd. máx.</th><th>Duração</th></tr></thead>
    <tbody>${Object.keys(NOMES_MODO).map((m) => `<tr>
      <td>${esc(NOMES_MODO[m])}<span class="sub mono">${esc(m)}</span></td>
      <td class="mono pequeno">${esc((i.modelos_do_modo[m] || []).join(", "))}</td>
      <td class="num mono">${m === "frames2v" ? "2 frames" : i.max_referencias[m]}</td>
      <td class="num mono">${i.quantidade_maxima[m]}</td>
      <td class="leve">${i.sem_duracao.includes(m) ? "não tem" : esc(i.duracoes.join(" · "))}</td></tr>`).join("")}</tbody></table>`;
}

/* ---------------- detalhe do job ---------------- */

function abrirDetalhe(id, comDiagnostico = false) {
  const j = E.jobs.find((x) => x.id === id);
  if (!j) return;
  jobAberto = id;
  eventosJob = { id, lista: null };
  delete assinaturas.detalhe;
  delete assinaturas.palco;
  $("detalhe").classList.remove("oculto");
  $("detalhe").dataset.diagnostico = comDiagnostico || j.estado === "falhou" ? "1" : "";
  pintarDetalhe();
  api(`/jobs/${id}/eventos`).then((lista) => {
    if (eventosJob.id !== id) return;
    eventosJob.lista = lista;
    delete assinaturas.detalhe;
    pintarDetalhe();
  }).catch(() => { eventosJob.lista = []; delete assinaturas.detalhe; pintarDetalhe(); });
}

function fecharDetalhe() {
  $("detalhe-palco").innerHTML = "";
  $("detalhe").classList.add("oculto");
  jobAberto = null;
}

function pintarDetalhe() {
  if (jobAberto === null) return;
  const j = E.jobs.find((x) => x.id === jobAberto);
  if (!j) { fecharDetalhe(); return; }

  if (mudou("palco", `${j.caminho || ""}:${j.estado}`)) {
    $("detalhe-palco").innerHTML = j.caminho
      ? (ehVideoJob(j) ? `<video src="${esc(urlMidia(j.caminho))}" controls autoplay loop playsinline></video>` : `<img src="${esc(urlMidia(j.caminho))}" alt="">`)
      : `<div class="sem-midia">${icone(j.estado === "falhou" ? "alerta" : j.estado === "rodando" ? "bolt" : ehVideoJob(j) ? "video" : "imagem")}
          <span>${j.estado === "falhou" ? "Esta geração não produziu arquivo."
            : j.estado === "pronto" ? "O arquivo está fora da raiz do projeto — não dá para mostrar por aqui."
            : j.estado === "rodando" ? `Gerando na conta ${esc(emailDaConta(j.conta_id))} há ${esc(decorrido(j.atualizado_em))}.`
            : j.estado === "cancelado" ? "Job cancelado antes de gerar." : "Na fila, esperando uma conta com crédito."}</span></div>`;
  }

  const ass = JSON.stringify(j) + (eventosJob.lista ? eventosJob.lista.length : -1) + $("detalhe").dataset.diagnostico + sessaoEmCurso;
  if (!mudou("detalhe", ass)) return;
  const foco = document.activeElement;
  if ($("detalhe-lado").contains(foco) && foco.tagName === "INPUT") return;

  const conta = j.conta_id ? contaDe(j.conta_id) : null;
  const explicacao = explicarErro(j.erro);
  const refs = j.modo === "frames2v"
    ? [[j.frame_inicial, "frame inicial"], [j.frame_final, "frame final"]].filter((r) => r[0])
    : (j.referencias || []).map((r, n) => [r, `referência ${n + 1}`]);
  const custo = custoDoJob(j);
  const diag = $("detalhe").dataset.diagnostico === "1";

  $("detalhe-lado").innerHTML = `
    <div class="detalhe-cabeca">
      <div class="linha">${pilulaEstado(j)}${tagModo(j)}<span class="tag">${esc(j.modelo)}</span><span class="mono leve">#${j.id}</span></div>
      <h2>${esc(j.nome)}</h2>
      <div class="linha leve pequeno">${esc(j.produto || "—")} · criado ${esc(relativo(j.criado_em))}${j.tentativas ? ` · ${j.tentativas} tentativa${j.tentativas > 1 ? "s" : ""}` : ""}</div>
    </div>
    ${j.erro && j.estado !== "pronto" ? `<div class="detalhe-secao"><div class="alerta erro">${icone("alerta")}<div class="alerta-texto"><strong>${esc(j.estado === "falhou" ? "Falhou" : "Última falha (voltou para a fila)")}</strong>
        <pre>${esc(j.erro)}</pre>${explicacao ? `<div style="margin-top:8px">${esc(explicacao)}</div>` : ""}</div></div></div>` : ""}
    ${j.erro || j.estado === "falhou" ? secaoDiagnostico(j) : ""}
    <div class="detalhe-secao"><h3>Prompt <button type="button" class="botao mini fantasma" data-copiar-prompt>${icone("copiar")}copiar</button></h3><p class="detalhe-prompt">${esc(j.prompt || "—")}</p></div>
    <div class="detalhe-secao"><h3>Configuração</h3><div class="grade-meta">
      <div><span>Proporção</span><b>${esc(j.proporcao)}</b></div>
      ${semDuracao(j.modo) ? "" : `<div><span>Duração</span><b>${esc(j.duracao)}</b></div>`}
      <div><span>Quantidade</span><b>x${j.quantidade}</b></div>
      <div><span>Custo ${j.custo_estimado === null ? "estimado" : "reservado"}</span><b>${custo === null ? "—" : `${custo} cr`}</b></div>
      <div><span>Conta</span><b>${conta ? esc(emailCurto(conta.email)) : "—"}</b></div>
      <div><span>Atualizado</span><b title="${esc(dataCompleta(j.atualizado_em))}">${esc(relativo(j.atualizado_em))}</b></div>
    </div></div>
    ${refs.length ? `<div class="detalhe-secao"><h3>${j.modo === "frames2v" ? "Frames" : "Referências"}</h3><div class="refs">${refs.map(([c, p]) =>
      `<div class="ref" title="${esc(c)}"><img src="${esc(urlMiniatura(c))}" alt="" loading="lazy"><span class="truncar">${esc(p)}</span></div>`).join("")}</div></div>` : ""}
    ${j.estado === "pendente" ? `<div class="detalhe-secao"><h3>Nome do arquivo</h3><div class="linha-arquivo"><input type="text" id="detalhe-nome" value="${esc(j.nome)}"><button type="button" class="botao mini" data-renomear>Salvar</button></div>
      <p class="dica">O nome vira o arquivo em disco, então só muda enquanto o job está na fila.</p></div>` : ""}
    ${j.arquivo ? `<div class="detalhe-secao"><h3>Arquivo</h3><div class="linha-arquivo"><input type="text" id="detalhe-caminho" value="${esc(j.arquivo)}" readonly><button type="button" class="botao mini" data-copiar-caminho>${icone("copiar")}</button></div></div>` : ""}
    <div class="detalhe-secao"><h3>Ações</h3><div class="detalhe-acoes">
      ${j.caminho ? `<a class="botao mini primario" href="${esc(urlBaixar(j.caminho))}" download>${icone("baixar")}Baixar</a>` : ""}
      ${j.estado === "pendente" ? `<button type="button" class="botao mini perigo" data-cancelar-detalhe>${icone("x")}Cancelar job</button>` : ""}
      ${conta && !sessaoEmCurso ? `<button type="button" class="botao mini" data-abrir-conta-detalhe="${conta.id}" title="abre o Chrome logado na conta que gerou">${icone("janela")}Abrir a conta</button>` : ""}
      ${["pronto", "falhou", "cancelado"].includes(j.estado) ? `<button type="button" class="botao mini" data-repetir>${icone("atualizar")}Gerar de novo</button>` : ""}
    </div></div>
    <div class="detalhe-secao"><h3>Linha do tempo <button type="button" class="botao mini fantasma" data-alternar-diag>${diag ? "ocultar" : "mostrar"}</button></h3>
      ${diag ? linhaDoTempo(eventosJob.lista) : ""}</div>`;
}

/* O diagnostico do modelo local: o motor pede sozinho quando o job falha de vez; o
   botao pede de novo (ou pela primeira vez, em job anterior ao recurso). */
let diagnosticando = null;

function secaoDiagnostico(j) {
  const lista = eventosJob.lista || [];
  const diag = [...lista].reverse().find((e) => e.tipo === "diagnostico");
  const falhou = [...lista].reverse().find((e) => e.tipo === "diagnostico_falhou");
  const emCurso = diagnosticando === j.id;
  let corpo;
  if (emCurso) corpo = `<div class="leve pequeno"><span class="girando" style="display:inline-block;vertical-align:middle;margin-right:6px"></span>perguntando ao modelo local… pode levar um minuto se ele precisar carregar.</div>`;
  else if (diag && (!falhou || falhou.id < diag.id)) {
    corpo = `<p class="detalhe-prompt">${esc(diag.payload.texto || "")}</p>
      <span class="leve pequeno">${esc(diag.payload.modelo || "modelo local")} · ${esc(relativo(diag.em))}${diag.payload.com_imagem ? " · viu o print da tela" : " · sem print"}</span>`;
  } else if (falhou) {
    corpo = `<div class="alerta aviso">${icone("alerta")}<div class="alerta-texto">O modelo local não respondeu: <span class="mono pequeno">${esc(falhou.payload.erro || "?")}</span>. O llama-swap está de pé em 127.0.0.1:9090?</div></div>`;
  } else if (eventosJob.lista === null) corpo = `<div class="leve pequeno">carregando…</div>`;
  else corpo = `<div class="leve pequeno">${j.estado === "falhou" ? "Ainda sem diagnóstico — o motor pede sozinho ao falhar de vez; se o job é anterior a isso, peça aqui." : "Job ainda na fila; o diagnóstico é para falha definitiva."}</div>`;
  return `<div class="detalhe-secao"><h3>Diagnóstico do modelo local
      <button type="button" class="botao mini fantasma" data-diagnosticar ${emCurso ? "disabled" : ""}>${icone("bolt")}${diag ? "refazer" : "pedir"}</button></h3>${corpo}</div>`;
}

async function pedirDiagnostico(id) {
  diagnosticando = id;
  delete assinaturas.detalhe; pintarDetalhe();
  try {
    await api(`/jobs/${id}/diagnosticar`, { metodo: "POST" }, 320000);
    toast("ok", "Diagnóstico pronto.", 3000);
  } catch (e) {
    toast("erro", `Diagnóstico: ${e.message}`, 10000);
  } finally {
    diagnosticando = null;
    try { eventosJob = { id, lista: await api(`/jobs/${id}/eventos`) }; } catch { /* fica como estava */ }
    delete assinaturas.detalhe; pintarDetalhe();
  }
}

async function renomearJob(id, nome) {
  try {
    await api(`/jobs/${id}`, { metodo: "PATCH", corpo: { nome } });
    toast("ok", `Renomeado para ${nome}.`);
    await atualizar();
  } catch (e) { toast("erro", e.message); }
}

async function cancelarJob(id) {
  try {
    await api(`/jobs/${id}`, { metodo: "DELETE" });
    toast("ok", `Job #${id} cancelado.`);
    await atualizar();
  } catch (e) { toast("erro", e.message); }
}

async function copiar(texto, rotulo) {
  try { await navigator.clipboard.writeText(texto); toast("ok", `${rotulo} copiado.`, 2500); }
  catch { toast("aviso", "O navegador não deixou copiar sozinho — selecione o campo e use Ctrl+C."); }
}

/* ---------------- gaveta da conta ---------------- */

function abrirGaveta(id) {
  contaAberta = id;
  eventosConta = { id, lista: null };
  delete assinaturas.gaveta;
  $("gaveta").classList.remove("oculto");
  $("gaveta-fundo").classList.remove("oculto");
  pintarGaveta();
  api(`/contas/${id}/eventos`).then((lista) => {
    if (eventosConta.id !== id) return;
    eventosConta.lista = lista;
    delete assinaturas.gaveta;
    pintarGaveta();
  }).catch(() => { eventosConta.lista = []; delete assinaturas.gaveta; pintarGaveta(); });
}

function fecharGaveta() {
  $("gaveta").classList.add("oculto");
  $("gaveta-fundo").classList.add("oculto");
  contaAberta = null;
}

function pintarGaveta() {
  if (contaAberta === null) return;
  const c = contaDe(contaAberta);
  if (!c) { fecharGaveta(); return; }
  const ass = JSON.stringify(c) + (eventosConta.lista ? eventosConta.lista.length : -1) + sessaoEmCurso;
  if (!mudou("gaveta", ass)) return;
  const foco = document.activeElement;
  if ($("gaveta").contains(foco) && (foco.tagName === "INPUT" || foco.tagName === "SELECT")) return;

  const seca = c.seca_ate && new Date(c.seca_ate) > new Date();
  const jobsDela = E.jobs.filter((j) => j.conta_id === c.id);
  $("gaveta").innerHTML = `
    <div class="detalhe-cabeca">
      <div class="linha">${c.ativa ? `<span class="pilula ${esc(c.saude)}">${esc(c.saude)}</span>` : `<span class="pilula inativa">desativada</span>`}<span class="tag tier-${esc(c.tier)}">${esc(c.tier)}</span><span class="mono leve">#${c.id}</span></div>
      <h2>${esc(c.email)}</h2>
      <button type="button" class="fechar-x" data-fechar="gaveta" title="fechar (Esc)">${icone("x")}</button>
    </div>
    ${c.saude === "expirada" ? `<div class="detalhe-secao"><div class="alerta aviso">${icone("alerta")}<div class="alerta-texto"><strong>Sessão caída.</strong> A sessão do Google dura ~48 h. Revalidar abre o Chrome para você logar de novo; enquanto isso a conta não recebe jobs.</div></div></div>` : ""}
    ${c.saude === "bloqueada" ? `<div class="detalhe-secao"><div class="alerta erro">${icone("alerta")}<div class="alerta-texto"><strong>Portão de idade.</strong> O Flow exige verificação de idade nesta conta Google. Isso é feito na conta, pelo dono — o painel não tem como resolver. Depois de verificar, revalide.</div></div></div>` : ""}
    <div class="detalhe-secao"><h3>Crédito</h3><div class="grade-meta">
      <div><span>Saldo</span><b>${c.saldo}${c.saldo_confiavel ? "" : " ~"}</b></div>
      <div><span>Leitura</span><b>${c.saldo_confiavel ? "lido no Flow" : "estimado"}</b></div>
      <div><span>Lido</span><b title="${esc(dataCompleta(c.saldo_lido_em))}">${esc(c.saldo_lido_em ? relativo(c.saldo_lido_em) : "nunca")}</b></div>
      <div><span>Falhas seguidas</span><b>${c.falhas}</b></div>
      ${seca ? `<div><span>Seca até</span><b class="erro-curto">${esc(emQuanto(c.seca_ate))}</b></div>` : ""}
      <div><span>Jobs por esta conta</span><b>${jobsDela.length}</b></div>
    </div></div>
    <div class="detalhe-secao"><h3>Ações</h3><div class="detalhe-acoes">
      <button type="button" class="botao mini ${c.saude !== "viva" ? "aviso" : ""}" data-revalidar="${c.id}" ${sessaoEmCurso ? "disabled" : ""}>${icone("atualizar")}Revalidar sessão</button>
      <button type="button" class="botao mini" data-saldo="${c.id}">${icone("saldo")}Ler saldo agora</button>
      <button type="button" class="botao mini" data-abrir-conta="${c.id}">${icone("janela")}Abrir o Chrome</button>
      <button type="button" class="botao mini" data-conferir-ip="${c.id}">${icone("janela")}Conferir IP</button>
      <button type="button" class="botao mini" data-mostrar-janela="${c.id}">${icone("janela")}Mostrar a janela</button>
    </div><p class="dica">Ler saldo abre uma janela e não gasta crédito. Abrir o Chrome deixa a conta ocupada até você fechar a janela. <strong>Mostrar a janela</strong> não abre nada: traz para a tela a janela que o robô já está usando — serve quando <code>FLOWPOOL_JANELA=oculta</code> faz o Chrome nascer fora da tela, e funciona com a conta gerando.</p></div>
    <div class="detalhe-secao"><h3>Editar</h3><div class="campos">
      <div class="campos-linha">
        <label class="campo"><span>Tier</span><select class="acao-tier" data-id="${c.id}">${["free", "pro", "ultra"].map((t) => `<option value="${t}" ${t === c.tier ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="campo cresce"><span>Ativa no pool</span><label class="interruptor" style="height:30px"><input type="checkbox" class="acao-ativa" data-id="${c.id}" ${c.ativa ? "checked" : ""}><span class="trilho"></span>${c.ativa ? "recebe jobs" : "fora do pool"}</label></label>
      </div>
      <label class="campo"><span>Proxy</span>
        <span class="mono pequeno leve">${c.proxy ? esc(c.proxy) : "sem proxy — sai pelo IP desta máquina"}</span>
        <div class="linha-arquivo"><input type="text" class="campo-proxy" data-id="${c.id}" placeholder="http://host:porta ou socks5://host:porta"><button type="button" class="botao mini" data-salvar-proxy="${c.id}">Salvar</button>${c.proxy ? `<button type="button" class="botao mini" data-tirar-proxy="${c.id}">Tirar</button>` : ""}</div>
        <p class="dica">O valor atual chega mascarado; por isso o campo nasce vazio. Trocar o proxy troca o IP por onde a conta sai — e o Google repara em mudança de IP.</p></label>
      <label class="campo"><span>IP de saída</span>
        <span class="mono pequeno leve">${c.ip_saida ? `${esc(c.ip_saida)} · lido ${esc(relativo(c.ip_lido_em))}` : "nunca conferido"}</span>
        <p class="dica">“Conferir IP” faz um GET e lê o endereço — não abre navegador, leva menos de um segundo. Mede por onde o proxy sai, e não o que o Chrome mostra ao Google: vazamento de WebRTC ou DNS fora do túnel não aparece aqui.</p></label>
    </div></div>
    <div class="detalhe-secao"><h3>Linha do tempo</h3>${linhaDoTempo(eventosConta.lista)}</div>`;
}

async function alterarConta(id, corpo) {
  try {
    await api(`/contas/${id}`, { metodo: "PATCH", corpo });
    toast("ok", "Conta atualizada.", 2500);
    await atualizar();
  } catch (e) { toast("erro", e.message); }
}

async function conferirIp(id, email) {
  toast("info", `Conferindo por qual IP ${email} sai…`, 3000);
  try {
    const c = await api(`/contas/${id}/conferir-ip`, { metodo: "POST" });
    toast("ok", `${email} sai por ${c.ip_saida}.`);
  } catch (e) { toast("erro", `Não deu para ler o IP de ${email}: ${e.message}`, 10000); }
  await atualizar();
}

async function lerSaldo(id, email) {
  toast("info", `Lendo o saldo de ${email}… abre uma janela, leva alguns segundos.`, 4000);
  try {
    const c = await api(`/contas/${id}/saldo`, { metodo: "POST" }, TETO_SESSAO_MS);
    toast("ok", `${email} está com ${c.saldo} créditos no Flow.`);
  } catch (e) { toast("erro", `Não deu para ler o saldo de ${email}: ${e.message}`, 10000); }
  await atualizar();
}

async function mostrarJanela(id, email) {
  try {
    await api(`/contas/${id}/mostrar-janela`, { metodo: "POST" });
    toast("ok", `Trouxe a janela de ${email} para a tela.`, 5000);
  } catch (e) { toast("aviso", `${e.message}`, 10000); }
}

async function abrirConta(id, email) {
  try {
    await api(`/contas/${id}/abrir`, { metodo: "POST" });
    toast("aviso", `Abri o Chrome em ${email}. Feche a janela quando terminar — enquanto ela estiver aberta, essa conta não consegue gerar.`, 10000);
  } catch (e) { toast("erro", `Não deu para abrir ${email}: ${e.message}`, 10000); }
}

/* ---------------- cadastro e revalidacao ---------------- */

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
  $("nova-conta").classList.remove("oculto");
  $("campos-conta").disabled = true;
  $("botao-cadastrar").disabled = true;
  $("andamento-titulo").textContent = titulo;
  $("andamento-cadastro").classList.remove("oculto");
  esconder($("erro-cadastro"));
  esconder($("ok-cadastro"));
  iniciarCronometro();
  pintarPagina();
}

function destravarSessao() {
  sessaoEmCurso = false;
  $("campos-conta").disabled = false;
  $("botao-cadastrar").disabled = false;
  esconder($("andamento-cadastro"));
  clearInterval(cronometro);
  pintarPagina();
}

function abrirNovaConta() {
  esconder($("erro-cadastro"));
  esconder($("ok-cadastro"));
  $("nova-conta").classList.remove("oculto");
  $("conta-email").focus();
}

async function cadastrar(ev) {
  ev.preventDefault();
  if (sessaoEmCurso) return;
  const email = $("conta-email").value.trim();
  const corpo = { email, tier: $("conta-tier").value };
  const proxy = $("conta-proxy").value.trim();
  if (proxy) corpo.proxy = proxy;
  travarSessao("Abri o Chrome. Agora é com você.");
  try {
    const c = await api("/contas", { metodo: "POST", corpo }, TETO_SESSAO_MS);
    destravarSessao();
    mostrar($("ok-cadastro"), c.saldo_confiavel
      ? `Sessão de pé para ${c.email}. A conta entrou no pool com ${c.saldo} créditos.`
      : `Sessão de pé para ${c.email}, mas não deu para ler o saldo agora. Use “Ler saldo” na conta.`);
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
    $("nova-conta").classList.add("oculto");
    toast("ok", `Sessão de ${email} está de pé de novo.`);
  } catch (e) {
    destravarSessao();
    mostrar($("erro-cadastro"), `Não deu para revalidar ${email}: ${e.message}`);
  }
  await atualizar();
}

/* ---------------- composer: nova geracao ---------------- */

const cfg = { modo: "imagem", modelo: "nano-banana", proporcao: "9:16", duracao: "4s", quantidade: 1 };
let anexos = [];
let subindo = 0;
let produtoAtual = PREF.ler("produto", "");

function abrirComposer(produto) {
  if (produto) produtoAtual = produto;
  esconder($("erro-job"));
  $("composer").classList.remove("oculto");
  ajustarComposer();
  $("job-prompt").focus();
}

function fecharComposer() { $("composer").classList.add("oculto"); }

function opcoesBotoes(el, valores, atual, chave) {
  el.innerHTML = valores.map((v) => `<button type="button" data-chave="${chave}" data-valor="${esc(v)}" class="${String(v) === String(atual) ? "ativo" : ""}">${esc(v)}</button>`).join("");
}

function ajustarComposer() {
  const i = E.info;
  const modelos = i ? i.modelos_do_modo[cfg.modo] : [cfg.modelo];
  if (!modelos.includes(cfg.modelo)) cfg.modelo = i ? i.modelo_padrao[cfg.modo] : modelos[0];
  const proporcoes = cfg.modo === "imagem" ? (i ? i.proporcoes : ["9:16"]) : PROPORCOES_VIDEO;
  if (!proporcoes.includes(cfg.proporcao)) cfg.proporcao = proporcoes[0];
  const qtdMax = i ? i.quantidade_maxima[cfg.modo] : (cfg.modo === "imagem" ? 4 : 1);
  if (cfg.quantidade > qtdMax) cfg.quantidade = 1;

  document.querySelectorAll("#seg-modo button").forEach((b) => b.classList.toggle("ativo", b.dataset.modo === cfg.modo));
  opcoesBotoes($("opc-modelo"), modelos, cfg.modelo, "modelo");
  opcoesBotoes($("opc-proporcao"), proporcoes, cfg.proporcao, "proporcao");
  opcoesBotoes($("opc-duracao"), i ? i.duracoes : ["4s", "6s", "8s", "10s"], cfg.duracao, "duracao");
  opcoesBotoes($("opc-quantidade"), Array.from({ length: qtdMax }, (_, n) => n + 1), cfg.quantidade, "quantidade");
  $("campo-duracao").classList.toggle("oculto", semDuracao(cfg.modo));
  $("campo-quantidade").classList.toggle("oculto", qtdMax === 1);

  const produtos = produtosConhecidos();
  if (produtoAtual && !produtos.includes(produtoAtual)) produtos.unshift(produtoAtual);
  $("job-produto").innerHTML = `<option value="">escolher produto…</option>` + produtos.map((p) =>
    `<option value="${esc(p)}" ${p === produtoAtual ? "selected" : ""}>${esc(p)}</option>`).join("");

  const dicas = {
    imagem: "Imagem custa zero crédito e aceita até 4 variações e até 3 referências — é assim que a foto da roupa vira a imagem da modelo vestida. Itere aqui, gaste no vídeo.",
    t2v: "Vídeo só de texto. A duração muda o preço (4s custa 7 créditos, 8s custa 12 no Omni Flash) e o Flow só oferece 9:16 e 16:9.",
    ingredients2v: "Vídeo a partir de até 3 imagens de referência. A referência cuida da identidade; o prompt cuida do resto — não descreva o que já está na foto.",
    frames2v: "Do primeiro ao último quadro. Só funciona no Veo Lite: outro modelo ignora o frame final e cobra o dobro. Não tem grupo de duração.",
  };
  $("composer-dica").textContent = dicas[cfg.modo];

  const teto = tetoAnexos(cfg.modo);
  $("botao-anexar-texto").textContent = teto ? (cfg.modo === "frames2v" ? "Escolher os frames" : `Anexar do disco (${anexos.length}/${teto})`) : "Anexar referências (vira vídeo · refs)";
  if (anexos.length > teto) anexos = anexos.slice(0, teto);
  pintarAnexos();

  const unit = custoDe(cfg.modelo, "free", cfg.modo, cfg.duracao);
  const custo = unit === null ? null : unit * cfg.quantidade;
  const el = $("composer-custo");
  if (custo === null) { el.textContent = ""; el.className = "leve composer-custo"; }
  else if (custo === 0) { el.textContent = "0 créditos — imagem é de graça"; el.className = "leve composer-custo"; }
  else {
    const pagam = contasQuePagam(custo).length;
    el.textContent = `≈ ${custo} créditos (conta free) · ${pagam ? `${pagam} conta${pagam > 1 ? "s" : ""} pode${pagam > 1 ? "m" : ""} pagar` : "nenhuma conta paga isso agora"}`;
    el.className = `leve composer-custo ${pagam ? "" : "aviso"}`;
  }
}

function pintarAnexos() {
  const p = papeis(cfg.modo);
  const prontas = anexos.map((c, n) => `<span class="tira" title="${esc(c)}"><img src="${esc(urlMiniatura(c))}" alt="" loading="lazy">
      <span><span class="tira-nome">${esc(c.split("/").pop())}</span><span class="tira-papel">${esc(p[n] || "")}</span></span>
      <button type="button" class="tira-x" data-caminho="${esc(c)}" title="tirar">${icone("x")}</button></span>`).join("");
  const emVoo = Array.from({ length: subindo }, (_, n) => `<span class="tira"><span class="girando"></span>
      <span><span class="tira-nome">anexando…</span><span class="tira-papel">${esc(p[anexos.length + n] || "")}</span></span></span>`).join("");
  $("tira-anexos").innerHTML = prontas + emVoo;
}

function nomeAutomatico(p) {
  const base = p.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "job";
  const usados = new Set(E.jobs.filter((j) => j.produto === p).map((j) => j.nome));
  let n = 1;
  while (usados.has(`${base}-${n}`)) n += 1;
  return `${base}-${n}`;
}

function produtoEscolhido() {
  const novo = $("job-produto-novo").value.trim();
  return novo || $("job-produto").value || "";
}

function corpoDoJob() {
  const produto = produtoEscolhido();
  const corpo = {
    modo: cfg.modo, modelo: cfg.modelo, prompt: $("job-prompt").value.trim(),
    produto, nome: nomeAutomatico(produto), proporcao: cfg.proporcao,
    quantidade: cfg.modo === "imagem" ? cfg.quantidade : 1,
  };
  // `duracao` so nos modos que tem o grupo: mandar em imagem ou frames2v e 422.
  if (!semDuracao(cfg.modo)) corpo.duracao = cfg.duracao;
  if (cfg.modo === "frames2v") {
    corpo.frame_inicial = anexos[0] || "";
    if (anexos[1]) corpo.frame_final = anexos[1];
  } else if (tetoAnexos(cfg.modo)) {
    corpo.referencias = anexos.slice(0, tetoAnexos(cfg.modo));
  }
  return corpo;
}

async function enviarJob(ev) {
  ev.preventDefault();
  esconder($("erro-job"));
  if (!$("job-prompt").value.trim()) { mostrar($("erro-job"), "Escreva o prompt antes de enviar."); return; }
  const produto = produtoEscolhido();
  if (!produto) { mostrar($("erro-job"), "Escolha o produto: é a pasta onde o arquivo cai e o nome do projeto dentro do Flow."); $("job-produto").focus(); return; }
  if (subindo) { mostrar($("erro-job"), "Espere a imagem terminar de anexar — falta um instante."); return; }
  if (!anexos.length && (cfg.modo === "frames2v" || cfg.modo === "ingredients2v")) {
    mostrar($("erro-job"), cfg.modo === "frames2v" ? "Este modo gera a partir de frames: escolha ao menos o frame inicial." : "Este modo gera a partir de referências: anexe ao menos uma.");
    return;
  }
  $("botao-enviar").disabled = true;
  try {
    const j = await api("/jobs", { metodo: "POST", corpo: corpoDoJob() });
    produtoAtual = produto;
    PREF.gravar("produto", produto);
    $("job-prompt").value = "";
    $("job-produto-novo").value = "";
    // `require-atomic-updates` acusa aqui, e a leitura dele esta certa: `anexos` e
    // global e isto acontece DEPOIS do await. Nao e corrida mesmo assim — o retrato
    // que vai para o servidor saiu de `corpoDoJob()` ANTES do await, e o composer
    // fecha na linha seguinte. Zerar e o que se quer: o que a pessoa marcou enquanto
    // o POST corria pertencia a um composer que ja esta indo embora.
    // eslint-disable-next-line require-atomic-updates
    anexos = [];
    fecharComposer();
    toast("ok", `Job #${j.id} (${j.nome}) entrou na fila.`);
    await atualizar();
  } catch (e) {
    mostrar($("erro-job"), e.message);
  } finally {
    $("botao-enviar").disabled = false;
  }
}

/* "Gerar de novo": reabre o composer com tudo do job — o caso e o job que falhou por
   conta, nao por prompt. */
function repetirJob(j) {
  cfg.modo = j.modo; cfg.modelo = j.modelo; cfg.proporcao = j.proporcao;
  cfg.quantidade = j.quantidade || 1;
  if (!semDuracao(j.modo)) cfg.duracao = j.duracao;
  anexos = j.modo === "frames2v" ? [j.frame_inicial, j.frame_final].filter(Boolean) : [...(j.referencias || [])];
  fecharDetalhe();
  abrirComposer(j.produto);
  $("job-prompt").value = j.prompt || "";
}

/* ---------------- seletor de imagens (disco) ---------------- */

let seletor = { caminho: "", pai: null, arquivos: [] };

function garantirModoQueAceitaArquivo() {
  if (tetoAnexos(cfg.modo)) return;
  cfg.modo = "ingredients2v";
  ajustarComposer();
}

async function abrirSeletor() {
  garantirModoQueAceitaArquivo();
  if (!tetoAnexos(cfg.modo)) return;
  seletor = { caminho: "", pai: null, arquivos: [] };
  $("seletor-titulo").textContent = cfg.modo === "frames2v" ? "Escolher os frames — inicial e final" : `Escolher referências — até ${tetoAnexos(cfg.modo)}`;
  $("seletor-tres").classList.toggle("oculto", cfg.modo === "frames2v");
  esconder($("seletor-erro"));
  $("seletor-corpo").innerHTML = `<div class="vazio">Lendo a pasta…</div>`;
  $("seletor").classList.remove("oculto");
  await navegarPasta(PREF.ler("ultima-pasta", ""), true);
}

function fecharSeletor() { $("seletor").classList.add("oculto"); }

async function navegarPasta(caminho, cairNaRaiz = false) {
  esconder($("seletor-erro"));
  try {
    const r = await api(`/arquivos?caminho=${encodeURIComponent(caminho)}`);
    seletor = { caminho: r.caminho, pai: r.pai, arquivos: r.arquivos };
    PREF.gravar("ultima-pasta", r.caminho);
    pintarSeletor(r);
  } catch (e) {
    if (cairNaRaiz && caminho) { await navegarPasta("", false); return; }
    $("seletor-corpo").innerHTML = "";
    mostrar($("seletor-erro"), e.message);
  }
}

function atualizarRestam() {
  const teto = tetoAnexos(cfg.modo);
  const p = papeis(cfg.modo);
  $("seletor-restam").textContent = anexos.length < teto
    ? `${anexos.length} de ${teto} — a próxima vira “${p[anexos.length]}”`
    : `${teto} de ${teto} escolhidas — clique numa marcada para tirar`;
}

function pintarSeletor(r) {
  $("seletor-caminho").textContent = r.caminho ? `/${r.caminho}` : "/ raiz do projeto";
  $("seletor-voltar").disabled = r.pai === null;
  $("seletor-tres").disabled = r.arquivos.length === 0;
  atualizarRestam();
  const pastas = r.pastas.map((p) => `<button type="button" class="botao mini ir-pasta" data-caminho="${esc(p.caminho)}">${icone("pasta")}${esc(p.nome)}</button>`).join("");
  const imagens = r.arquivos.map((a) => `<button type="button" class="item-imagem escolher-imagem${anexos.includes(a.caminho) ? " escolhida" : ""}" data-caminho="${esc(a.caminho)}" title="${esc(a.caminho)}">
      <img src="${esc(urlMiniatura(a.caminho))}" alt="" loading="lazy"><span class="nome">${esc(a.nome)}</span><span class="tamanho">${tamanhoLegivel(a.tamanho)}</span></button>`).join("");
  $("seletor-corpo").innerHTML = (pastas ? `<div class="grade-pastas">${pastas}</div>` : "")
    + (imagens ? `<div class="grade-imagens">${imagens}</div>` : `<div class="vazio">Nenhuma imagem nesta pasta.</div>`);
}

function repintarMarcas() {
  document.querySelectorAll("#seletor-corpo .escolher-imagem").forEach((b) => b.classList.toggle("escolhida", anexos.includes(b.dataset.caminho)));
  atualizarRestam();
}

function escolherImagem(caminho) {
  const teto = tetoAnexos(cfg.modo);
  const i = anexos.indexOf(caminho);
  if (i >= 0) anexos.splice(i, 1);
  else if (anexos.length >= teto) { mostrar($("seletor-erro"), `Já são ${teto}, e o Flow não aceita mais neste modo. Clique numa marcada para tirar.`); return; }
  else anexos.push(caminho);
  esconder($("seletor-erro"));
  ajustarComposer();
  repintarMarcas();
  if (cfg.modo === "frames2v" && anexos.length === teto) fecharSeletor();
}

/* ---------------- colar e soltar ---------------- */

const RE_EXTENSAO_IMAGEM = /\.(png|jpe?g|webp|gif)$/i;

function urlDeImagem(texto) {
  const t = (texto || "").trim();
  if (!/^https?:\/\//i.test(t) || /\s/.test(t)) return null;
  try { return RE_EXTENSAO_IMAGEM.test(new URL(t).pathname) ? t : null; } catch { return null; }
}

async function anexarColada(pedido) {
  if ($("composer").classList.contains("oculto")) abrirComposer();
  esconder($("erro-job"));
  garantirModoQueAceitaArquivo();
  const teto = tetoAnexos(cfg.modo);
  if (anexos.length + subindo >= teto) { mostrar($("erro-job"), `Já são ${teto} neste modo. Tire uma antes de colar outra.`); return; }
  if (pedido.blob && pedido.blob.size > TETO_COLAGEM) { mostrar($("erro-job"), `Essa imagem tem ${tamanhoLegivel(pedido.blob.size)} e o limite é 20 MB.`); return; }
  subindo += 1;
  pintarAnexos();
  try {
    const r = pedido.url
      ? await api("/colar-url", { metodo: "POST", corpo: { url: pedido.url } }, TETO_COLAGEM_MS)
      : await api("/colar", { metodo: "POST", bruto: pedido.blob }, TETO_COLAGEM_MS);
    if (!anexos.includes(r.caminho) && anexos.length < tetoAnexos(cfg.modo)) anexos.push(r.caminho);
  } catch (e) {
    mostrar($("erro-job"), `Não deu para anexar a imagem: ${e.message}`);
  } finally {
    subindo -= 1;
    ajustarComposer();
  }
}

document.addEventListener("paste", (ev) => {
  const dados = ev.clipboardData;
  if (!dados) return;
  const imagem = [...(dados.items || [])].filter((i) => i.kind === "file" && i.type.startsWith("image/")).map((i) => i.getAsFile()).find(Boolean);
  if (imagem) { ev.preventDefault(); anexarColada({ blob: imagem }); return; }
  const url = urlDeImagem(dados.getData("text/plain"));
  if (!url) return;
  ev.preventDefault();
  anexarColada({ url });
});

let arrastando = 0;
function arrasteAceito(ev) {
  const tipos = [...((ev.dataTransfer && ev.dataTransfer.types) || [])];
  if (tipos.includes("Files")) return "arquivo";
  if (tipos.includes("text/uri-list") || tipos.includes("text/plain")) return "url";
  return null;
}
function esconderSolta() { arrastando = 0; esconder($("solta")); }
document.addEventListener("dragenter", (ev) => { if (!arrasteAceito(ev)) return; ev.preventDefault(); arrastando += 1; $("solta").classList.remove("oculto"); });
document.addEventListener("dragover", (ev) => { if (arrasteAceito(ev)) ev.preventDefault(); });
document.addEventListener("dragleave", (ev) => { if (!arrasteAceito(ev)) return; arrastando = Math.max(0, arrastando - 1); if (!arrastando) esconder($("solta")); });
document.addEventListener("drop", (ev) => {
  const tipo = arrasteAceito(ev);
  if (!tipo) return;
  ev.preventDefault();
  esconderSolta();
  const arquivos = [...((ev.dataTransfer && ev.dataTransfer.files) || [])].filter((f) => f.type.startsWith("image/"));
  if (arquivos.length) { arquivos.slice(0, 3).forEach((f) => anexarColada({ blob: f })); return; }
  const url = urlDeImagem(ev.dataTransfer.getData("text/uri-list")) || urlDeImagem(ev.dataTransfer.getData("text/plain"));
  if (url) { anexarColada({ url }); return; }
  toast("aviso", "O que você soltou não é imagem — arraste um PNG, JPEG, WEBP ou GIF.");
});
window.addEventListener("dragend", esconderSolta);
window.addEventListener("blur", esconderSolta);

/* ---------------- poll ---------------- */

let relogioPoll = null;

async function atualizar() {
  try {
    const [saude, contas, jobs, projetos] = await Promise.all([
      api("/saude"), api("/contas"), api("/jobs"), api("/projetos"),
    ]);
    E.saude = saude; E.contas = contas; E.jobs = jobs; E.projetos = projetos;
    E.atualizadoEm = new Date();
    E.falhaConexao = null;
    [...selecao].forEach((id) => { if (!jobs.some((j) => j.id === id && j.caminho)) selecao.delete(id); });
  } catch (e) {
    E.falhaConexao = e.message;
  }
  pintarCasca();
  pintarPagina();
  pintarDetalhe();
  pintarGaveta();
}

function reagendar() {
  clearInterval(relogioPoll);
  relogioPoll = setInterval(() => { if (!document.hidden) atualizar(); }, PREF.ler("intervalo", 4000));
}

/* ---------------- ligacao ---------------- */

function ligarCasca(rota) {
  const c = $("conteudo");
  if (rota === "jobs") {
    $("f-busca").addEventListener("input", (ev) => { F.jobs.busca = ev.target.value; F.jobs.limite = 50; pintarJobs(); });
    $("f-estado").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; F.jobs.estado = b.dataset.valor; F.jobs.limite = 50; pintarJobs(); });
    $("f-modo").addEventListener("change", (ev) => { F.jobs.modo = ev.target.value; pintarJobs(); });
    $("f-produto").addEventListener("change", (ev) => { F.jobs.produto = ev.target.value; pintarJobs(); });
  } else if (rota === "historico") {
    $("f-hist").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; F.historico.estado = b.dataset.valor; pintarHistorico(); });
  } else if (rota === "saidas") {
    $("f-tipo").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; F.saidas.tipo = b.dataset.valor; $("f-tipo").querySelectorAll("button").forEach((x) => x.classList.toggle("ativo", x === b)); pintarSaidas(); });
    $("f-saidas-produto").addEventListener("change", (ev) => { F.saidas.produto = ev.target.value; pintarSaidas(); });
    $("alternar-selecao").addEventListener("click", () => { F.saidas.selecionando = !F.saidas.selecionando; if (!F.saidas.selecionando) selecao.clear(); delete assinaturas.saidas; pintarSaidas(); });
    $("baixar-selecao").addEventListener("click", baixarSelecao);
    $("limpar-selecao").addEventListener("click", () => { selecao.clear(); delete assinaturas.saidas; pintarSaidas(); });
    $("galeria").addEventListener("mouseover", (ev) => { const v = ev.target.closest("video"); if (v && PREF.ler("hover", true)) v.play().catch(() => {}); });
    $("galeria").addEventListener("mouseout", (ev) => { const v = ev.target.closest("video"); if (v) { v.pause(); v.currentTime = 0.1; } });
  }
  c.addEventListener("click", cliqueNoConteudo);
  c.addEventListener("change", (ev) => {
    const a = ev.target;
    const id = Number(a.dataset.id);
    if (a.classList.contains("acao-ativa")) alterarConta(id, { ativa: a.checked });
    else if (a.classList.contains("marcar-job")) {
      if (selecao.has(id)) selecao.delete(id); else selecao.add(id);
      delete assinaturas.saidas; pintarSaidas();
    }
  });
}

function cliqueNoConteudo(ev) {
  const alvo = ev.target.closest("[data-parar], [data-nova], [data-nova-conta], [data-mais], [data-cancelar], [data-abrir-job], [data-revalidar], [data-saldo], [data-abrir-conta], [data-editar-conta], [data-esquecer], [data-gerar-produto], .card, tr[data-job], tr[data-conta]");
  if (!alvo) return;
  if (alvo.hasAttribute("data-parar")) return;
  const d = alvo.dataset;
  if (d.nova !== undefined) abrirComposer();
  else if (d.novaConta !== undefined) abrirNovaConta();
  else if (d.mais !== undefined) { if (E.rota === "jobs") F.jobs.limite += 50; else F.historico.limite += 80; pintarPagina(); }
  else if (d.cancelar) { ev.stopPropagation(); cancelarJob(Number(d.cancelar)); }
  else if (d.abrirJob) { ev.stopPropagation(); abrirDetalhe(Number(d.abrirJob), alvo.hasAttribute("data-diagnostico")); }
  else if (d.revalidar) { ev.stopPropagation(); revalidar(Number(d.revalidar), emailDaConta(Number(d.revalidar))); }
  else if (d.saldo) { ev.stopPropagation(); lerSaldo(Number(d.saldo), emailDaConta(Number(d.saldo))); }
  else if (d.abrirConta) { ev.stopPropagation(); abrirConta(Number(d.abrirConta), emailDaConta(Number(d.abrirConta))); }
  else if (d.editarConta) { ev.stopPropagation(); abrirGaveta(Number(d.editarConta)); }
  else if (d.esquecer) { ev.stopPropagation(); esquecerProjeto(Number(d.esquecer)); }
  else if (d.gerarProduto) abrirComposer(d.gerarProduto);
  else if (alvo.classList.contains("card")) abrirDetalhe(Number(d.id));
  else if (d.job) abrirDetalhe(Number(d.job));
  else if (d.conta) abrirGaveta(Number(d.conta));
}

$("botao-nova").addEventListener("click", () => abrirComposer());
$("topo-contas").addEventListener("click", () => { location.hash = "#/contas"; });

// fechar por [data-fechar] em qualquer modal/gaveta, e clique no fundo
document.addEventListener("click", (ev) => {
  const f = ev.target.closest("[data-fechar]");
  if (f) {
    const alvo = f.dataset.fechar;
    if (alvo === "detalhe") fecharDetalhe();
    else if (alvo === "composer") fecharComposer();
    else if (alvo === "seletor") fecharSeletor();
    else if (alvo === "gaveta") fecharGaveta();
    else if (alvo === "nova-conta" && !sessaoEmCurso) $("nova-conta").classList.add("oculto");
    return;
  }
  if (ev.target === $("detalhe")) fecharDetalhe();
  else if (ev.target === $("seletor")) fecharSeletor();
  else if (ev.target === $("gaveta-fundo")) fecharGaveta();
});

// detalhe do job
$("detalhe-lado").addEventListener("click", (ev) => {
  const b = ev.target.closest("button, a");
  if (!b || jobAberto === null) return;
  const j = E.jobs.find((x) => x.id === jobAberto);
  if (!j) return;
  if (b.hasAttribute("data-copiar-prompt")) copiar(j.prompt || "", "Prompt");
  else if (b.hasAttribute("data-copiar-caminho")) copiar(j.arquivo || "", "Caminho");
  else if (b.hasAttribute("data-renomear")) renomearJob(j.id, $("detalhe-nome").value.trim());
  else if (b.hasAttribute("data-cancelar-detalhe")) cancelarJob(j.id);
  else if (b.dataset.abrirContaDetalhe) abrirConta(Number(b.dataset.abrirContaDetalhe), emailDaConta(Number(b.dataset.abrirContaDetalhe)));
  else if (b.hasAttribute("data-repetir")) repetirJob(j);
  else if (b.hasAttribute("data-diagnosticar")) pedirDiagnostico(j.id);
  else if (b.hasAttribute("data-alternar-diag")) { $("detalhe").dataset.diagnostico = $("detalhe").dataset.diagnostico === "1" ? "" : "1"; delete assinaturas.detalhe; pintarDetalhe(); }
});
$("detalhe-lado").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.target.id === "detalhe-nome") { ev.preventDefault(); renomearJob(jobAberto, ev.target.value.trim()); }
});

// gaveta da conta
$("gaveta").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  const d = b.dataset;
  if (d.revalidar) revalidar(Number(d.revalidar), emailDaConta(Number(d.revalidar)));
  else if (d.saldo) lerSaldo(Number(d.saldo), emailDaConta(Number(d.saldo)));
  else if (d.abrirConta) abrirConta(Number(d.abrirConta), emailDaConta(Number(d.abrirConta)));
  else if (d.conferirIp) conferirIp(Number(d.conferirIp), emailDaConta(Number(d.conferirIp)));
  else if (d.mostrarJanela) mostrarJanela(Number(d.mostrarJanela), emailDaConta(Number(d.mostrarJanela)));
  else if (d.salvarProxy) {
    const campo = $("gaveta").querySelector(`.campo-proxy[data-id="${d.salvarProxy}"]`);
    const novo = campo.value.trim();
    if (!novo) { toast("aviso", "Digite o proxy novo para salvar. Para voltar ao IP desta máquina, use “Tirar”."); return; }
    alterarConta(Number(d.salvarProxy), { proxy: novo });
  } else if (d.tirarProxy) alterarConta(Number(d.tirarProxy), { proxy: "" });
});
$("gaveta").addEventListener("change", (ev) => {
  const a = ev.target;
  const id = Number(a.dataset.id);
  if (a.classList.contains("acao-ativa")) alterarConta(id, { ativa: a.checked });
  else if (a.classList.contains("acao-tier")) alterarConta(id, { tier: a.value });
});

// composer
$("form-job").addEventListener("submit", enviarJob);
$("seg-modo").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; cfg.modo = b.dataset.modo; ajustarComposer(); });
$("form-job").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-chave]");
  if (b) { cfg[b.dataset.chave] = b.dataset.chave === "quantidade" ? Number(b.dataset.valor) : b.dataset.valor; ajustarComposer(); return; }
  const x = ev.target.closest(".tira-x");
  if (x) { anexos = anexos.filter((c) => c !== x.dataset.caminho); ajustarComposer(); }
});
$("botao-anexar").addEventListener("click", abrirSeletor);
$("job-prompt").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); $("form-job").requestSubmit(); }
});
$("job-produto").addEventListener("change", () => { produtoAtual = $("job-produto").value; $("job-produto-novo").value = ""; ajustarComposer(); });
$("job-produto-novo").addEventListener("input", () => { if ($("job-produto-novo").value.trim()) $("job-produto").value = ""; });

// seletor
$("seletor").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b || b.disabled) return;
  if (b.id === "seletor-voltar") { if (seletor.pai !== null) navegarPasta(seletor.pai); }
  else if (b.id === "seletor-tres") { anexos = seletor.arquivos.slice(0, tetoAnexos(cfg.modo)).map((a) => a.caminho); ajustarComposer(); repintarMarcas(); }
  else if (b.classList.contains("ir-pasta")) navegarPasta(b.dataset.caminho);
  else if (b.classList.contains("escolher-imagem")) escolherImagem(b.dataset.caminho);
});

// nova conta
$("form-conta").addEventListener("submit", cadastrar);

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    if (!$("seletor").classList.contains("oculto")) fecharSeletor();
    else if (!$("composer").classList.contains("oculto")) fecharComposer();
    else if (!$("detalhe").classList.contains("oculto")) fecharDetalhe();
    else if (!$("gaveta").classList.contains("oculto")) fecharGaveta();
    else if (!$("nova-conta").classList.contains("oculto") && !sessaoEmCurso) $("nova-conta").classList.add("oculto");
    return;
  }
  const emCampo = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (ev.key === "n" && !emCampo && !ev.ctrlKey && !ev.metaKey && !ev.altKey) { ev.preventDefault(); abrirComposer(); }
});

window.addEventListener("hashchange", navegar);
window.addEventListener("resize", medirGrade);
if (document.fonts) document.fonts.ready.then(medirGrade);

/* ---------------- partida ---------------- */

(async () => {
  try { E.info = await api("/info"); } catch { /* o painel funciona com os padroes; a tabela de custos fica vazia */ }
  navegar();
  await atualizar();
  reagendar();
})();
