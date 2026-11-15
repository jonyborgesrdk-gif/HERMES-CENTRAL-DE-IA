/* Hermes — Central de operações (página principal do painel).
   Lê o mesmo JSON do dados.json: SSE em /eventos (evento "dados") com polling em /dados.json se o SSE cair.
   Dado do servidor só entra na tela por textContent e atributos (nunca como HTML); nenhuma requisição externa.
   As funções puras (modelos, dinheiro, privacidade, transporte) são exportadas para os testes em node. */
(function (raiz) {
"use strict";

var ICONES = {
  grid: [["rect",{"x":"3","y":"3","width":"7","height":"7","rx":"1.5"}],["rect",{"x":"14","y":"3","width":"7","height":"7","rx":"1.5"}],["rect",{"x":"3","y":"14","width":"7","height":"7","rx":"1.5"}],["rect",{"x":"14","y":"14","width":"7","height":"7","rx":"1.5"}]],
  agents: [["rect",{"x":"5","y":"6","width":"14","height":"13","rx":"3"}],["path",{"d":"M12 3v3M9 11h.01M15 11h.01M9 15h6M2 10v5m20-5v5"}]],
  chart: [["path",{"d":"M4 3v17h17M8 15v-4m5 4V6m5 9v-6"}]],
  layers: [["path",{"d":"m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5m-18 5 9 5 9-5"}]],
  activity: [["path",{"d":"M2 12h4l3-8 5 16 3-8h5"}]],
  monitor: [["rect",{"x":"3","y":"4","width":"18","height":"13","rx":"2"}],["path",{"d":"M8 21h8m-4-4v4"}]],
  chevron: [["path",{"d":"m9 5 7 7-7 7"}]],
  home: [["path",{"d":"m3 10 9-7 9 7v10H3V10Zm6 10v-7h6v7"}]],
  download: [["path",{"d":"M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"}]],
  shield: [["path",{"d":"m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"}],["path",{"d":"m9 12 2 2 4-4"}]],
  eye: [["path",{"d":"M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"}],["circle",{"cx":"12","cy":"12","r":"3"}]],
  eyeOff: [["path",{"d":"m3 3 18 18M10 5c7-1 12 7 12 7s-1 3-4 5M6 6c-3 2-4 6-4 6s3 7 10 7c2 0 3-.4 4-1M9 9a4 4 0 0 0 6 6"}]],
  sun: [["circle",{"cx":"12","cy":"12","r":"4"}],["path",{"d":"M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"}]],
  moon: [["path",{"d":"M21 13A9 9 0 0 1 11 3a9 9 0 1 0 10 10Z"}]],
  refresh: [["path",{"d":"M20 7v5h-5M4 17v-5h5"}],["path",{"d":"M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"}]],
  search: [["circle",{"cx":"10.5","cy":"10.5","r":"6.5"}],["path",{"d":"m16 16 5 5"}]],
  claude: [["path",{"d":"M12 2v20M2 12h20M5 5l14 14M5 19 19 5M8 3l8 18M3 8l18 8M3 16 21 8M8 21 16 3"}]],
  code: [["path",{"d":"m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 20"}]],
  check: [["path",{"d":"m5 12 4 4L19 6"}]],
  checkCircle: [["circle",{"cx":"12","cy":"12","r":"9"}],["path",{"d":"m8 12 3 3 5-6"}]],
  warning: [["path",{"d":"M10.4 4 2 19c-.5 1 .2 2 1.3 2h17.4c1.1 0 1.8-1 1.3-2L13.6 4c-.7-1.3-2.5-1.3-3.2 0Z"}],["path",{"d":"M12 9v4m0 4h.01"}]],
  clock: [["circle",{"cx":"12","cy":"12","r":"9"}],["path",{"d":"M12 7v5l3 2"}]],
  terminal: [["path",{"d":"m5 6 5 6-5 6m8 0h6"}]],
  database: [["ellipse",{"cx":"12","cy":"5","rx":"8","ry":"3"}],["path",{"d":"M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"}]],
  message: [["path",{"d":"M21 11a9 9 0 0 1-13 8l-6 2 2-6a9 9 0 1 1 17-4Z"}],["path",{"d":"M8 9h8m-8 4h5"}]],
  server: [["rect",{"x":"3","y":"3","width":"18","height":"7","rx":"2"}],["rect",{"x":"3","y":"14","width":"18","height":"7","rx":"2"}],["path",{"d":"M7 6.5h.01M7 17.5h.01m5-11h5m-5 11h5"}]],
  close: [["path",{"d":"m6 6 12 12M6 18 18 6"}]],
  menu: [["path",{"d":"M4 6h16M4 12h16M4 18h16"}]],
  arrow: [["path",{"d":"M4 12h16m-5-5 5 5-5 5"}]],
  wallet: [["path",{"d":"M20 7V5H5a3 3 0 0 0 0 6h16v9H5a3 3 0 0 1-3-3V8m19 5h-5v4h5"}]],
  info: [["circle",{"cx":"12","cy":"12","r":"9"}],["path",{"d":"M12 11v6m0-10h.01"}]]
};

var SVGNS = "http://www.w3.org/2000/svg";           // namespace do SVG, não é endereço de rede
var CHAVE_TEMA = "painel-tema", CHAVE_PRIVACIDADE = "painel-privacidade";   // as mesmas da página clássica
var TEMAS = ["auto", "claro", "escuro"], ROTULO_TEMA = { auto: "automático", claro: "claro", escuro: "escuro" };
var VELHO_DEPOIS_DE_S = 60, LER_A_CADA_MS = 5000, TEMPO_REFRESH_MS = 15000;
var JANELAS = { "24h": "Últimas 24 h", hoje: "Hoje", "7d": "Últimos 7 dias", "30d": "Últimos 30 dias" };
var SECOES = [["overview", "Visão geral"], ["agents", "Agentes"], ["usage", "Consumo e gastos"], ["services", "Serviços"], ["events", "Eventos"]];
var ROTULO_ESTADO = { trabalhando: "Trabalhando", ocioso: "Ocioso", esperando: "Esperando", parado: "Parado", indeterminado: "Indeterminado", sem_leitura: "Sem leitura" };
var AVISO_ARQUIVO = "Esta página precisa do servidor do painel. Abra por http://127.0.0.1:7421/ (ou use classico.html para abrir como arquivo).";
var ICONE_SERVICO = { hindsight: "database", ollama: "server", vigia: "eye", hermes: "layers", whatsapp: "message" };

/* ---------- utilitários puros ---------- */
function numero(v) { return typeof v === "number" && isFinite(v); }
function texto(v) { return typeof v === "string" ? v : numero(v) ? String(v) : ""; }
function objeto(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : {}; }
function lista(v) { return Array.isArray(v) ? v : []; }
function dois(n) { return (n < 10 ? "0" : "") + n; }
function plural(n, um, varios) { return n + " " + (n === 1 ? um : varios); }
function propria(mapa, chave) { return Object.prototype.hasOwnProperty.call(mapa, chave) ? mapa[chave] : undefined; }   // chave vinda do dado
function capitalizar(t) { return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }

/* Mesma regra da página clássica: começo + miolo fixo + domínio; o miolo não revela o tamanho do nome. */
var PADRAO_EMAIL = /[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+/g;
function mascararEmails(t) {
  return String(t == null ? "" : t).replace(PADRAO_EMAIL, function (email) {
    var arroba = email.lastIndexOf("@"), local = email.slice(0, arroba);
    var visiveis = local.length >= 6 ? 2 : local.length >= 3 ? 1 : 0;
    return local.slice(0, visiveis) + "••••••" + email.slice(arroba);
  });
}
function mascararProfundo(v) {
  if (typeof v === "string") return mascararEmails(v);
  if (Array.isArray(v)) return v.map(mascararProfundo);
  if (v && typeof v === "object") {
    var r = Object.create(null);
    Object.keys(v).forEach(function (k) { r[k] = mascararProfundo(v[k]); });
    return r;
  }
  return v;
}
/* O que o botão "Exportar dados" grava: o snapshot atual; com a privacidade ligada, e-mails mascarados. */
function snapshotParaExportar(dados, privado, agoraMs) {
  return { exportado_em: new Date(agoraMs).toISOString(), privacidade_ligada: !!privado, dados: privado ? mascararProfundo(dados) : dados };
}

/* ---------- tempo ---------- */
function haSegundos(segundos) {
  var s = Math.max(0, Math.floor(segundos));
  if (s < 60) return s + " s";
  var m = Math.floor(s / 60);
  if (m < 10) return m + " min " + dois(s % 60) + " s";
  if (m < 60) return m + " min";
  var h = Math.floor(m / 60);
  if (h < 24) return h + " h " + dois(m % 60) + " min";
  var d = Math.floor(h / 24);
  return d + (d === 1 ? " dia" : " dias");
}
function duracao(segundos) {          // granularidade de minuto: não muda a cada segundo
  var s = Math.max(0, Math.floor(segundos));
  if (s < 60) return "menos de 1 min";
  var m = Math.floor(s / 60);
  if (m < 60) return m + " min";
  var h = Math.floor(m / 60);
  if (h < 24) return h + " h" + (m % 60 ? " " + dois(m % 60) + " min" : "");
  var d = Math.floor(h / 24);
  return d + (d === 1 ? " dia" : " dias") + (h % 24 ? " " + (h % 24) + " h" : "");
}
function dataHora(epoch) {            // 31/10/2026 05:01
  var d = new Date(epoch * 1000);
  return dois(d.getDate()) + "/" + dois(d.getMonth() + 1) + "/" + d.getFullYear() + " " + dois(d.getHours()) + ":" + dois(d.getMinutes());
}
function quando(epoch, agoraS) {      // "hoje às 21h12", "ontem às 09h05", "amanhã às 08h00", "28/09 às 14h00"
  var d = new Date(epoch * 1000), h = new Date(agoraS * 1000);
  var hora = dois(d.getHours()) + "h" + dois(d.getMinutes());
  var dia0 = new Date(h.getFullYear(), h.getMonth(), h.getDate()).getTime();
  var dias = Math.round((dia0 - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86400000);
  if (dias === 0) return "hoje às " + hora;
  if (dias === 1) return "ontem às " + hora;
  if (dias === -1) return "amanhã às " + hora;
  return dois(d.getDate()) + "/" + dois(d.getMonth() + 1) + " às " + hora;
}
var MESES = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
/* "Oct 31, 8pm (America/Sao_Paulo)" (texto da tela do Claude, em inglês) -> "31/10 às 20h00"; null se não reconhecer. */
function resetEmPtBr(t) {
  var m = /^\s*(?:([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:\s*,|\s+at)?\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b/i.exec(String(t || ""));
  if (!m) return null;
  var h = Number(m[3]) % 12 + (m[5].toLowerCase() === "pm" ? 12 : 0);
  var mes = m[1] ? MESES[m[1].slice(0, 3).toLowerCase()] : 0;
  if (m[1] && !mes) return null;
  return (mes ? dois(Number(m[2])) + "/" + dois(mes) + " " : "") + "às " + dois(h) + "h" + dois(m[4] ? Number(m[4]) : 0);
}
function textoReset(b, agoraS) {
  if (numero(b.renovaEpoch)) {
    var falta = b.renovaEpoch - agoraS;
    return falta > 0 ? "reinicia " + quando(b.renovaEpoch, agoraS) + " (em " + duracao(falta) + ")" : "reset já passou; aguardando nova leitura";
  }
  if (b.renovaTexto) return "reinicia " + (resetEmPtBr(b.renovaTexto) || b.renovaTexto);
  return "reset não informado";
}

/* ---------- dinheiro e números ---------- */
function fmtNum(v, min, max) { return v.toLocaleString("pt-BR", { minimumFractionDigits: min, maximumFractionDigits: max }); }
function valorMonetario(v) { return v > 0 && v < 0.01 ? fmtNum(v, 4, 4) : fmtNum(v, 2, 2); }
function fmtUsd(v) { return "US$ " + valorMonetario(numero(v) && v > 0 ? v : 0); }
function fmtBrl(v) { return "R$ " + valorMonetario(numero(v) && v > 0 ? v : 0); }
/* {moeda, valor, principal, secundario}: R$ principal e US$ ao lado quando há cotação; senão só US$. */
function dinheiro(usd, cot) {
  usd = numero(usd) && usd > 0 ? usd : 0;
  if (cot) {
    var brl = usd * cot.usdBrl;
    return { moeda: "R$", valor: valorMonetario(brl), principal: fmtBrl(brl), secundario: fmtUsd(usd) };
  }
  return { moeda: "US$", valor: valorMonetario(usd), principal: fmtUsd(usd), secundario: "" };
}
function eixo(x, cot) {          // x já está na moeda exibida
  return (cot ? "R$ " : "US$ ") + (x >= 10 ? fmtNum(x, 0, 0) : fmtNum(x, 0, 2));
}
function maxBonito(v) {
  if (!(v > 0)) return 1;
  var e = Math.pow(10, Math.floor(Math.log10(v))), f = v / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
}
function fmtTokens(n) {
  n = numero(n) && n > 0 ? n : 0;
  function g(v, um, varios) { var r = Math.round(v * 10) / 10; return fmtNum(r, 0, 1) + " " + (r === 1 ? um : varios); }
  if (n >= 1e9) return g(n / 1e9, "bilhão", "bilhões") + " de tokens";
  if (n >= 1e6) return g(n / 1e6, "milhão", "milhões") + " de tokens";
  if (n >= 1e3) return fmtNum(Math.round(n / 100) / 10, 0, 1) + " mil tokens";
  return fmtNum(Math.round(n), 0, 0) + " tokens";
}

/* ---------- modelos de dados (do dados.json para o que a tela mostra) ---------- */
function agentesDe(dados) {
  var vistas = Object.create(null);          // chave vem do dado: sem protótipo, "constructor" e "__proto__" são chaves comuns
  return lista(dados && dados.agentes).filter(function (a) { return a && typeof a === "object"; }).map(function (a, i) {
    var nome = texto(a.nome) || "Agente " + (i + 1), chave = texto(a.session_id) || nome;
    vistas[chave] = (vistas[chave] || 0) + 1;
    if (vistas[chave] > 1) chave += "#" + vistas[chave];
    var estado = texto(a.estado) || "sem_leitura";
    var m = objeto(a.missao), modelo = texto(a.modelo).replace(/^[\s\u2500-\u257f-]+/, "").replace(/\s*\([^)]*\)\s*$/, "");
    var ferramenta = texto(a.ferramenta_nome) || capitalizar(texto(a.ferramenta));
    if (modelo && texto(a.esforco) && a.ferramenta === "codex") modelo += " " + texto(a.esforco);
    return {
      chave: chave, nome: nome, ferramenta: texto(a.ferramenta), ferramentaNome: ferramenta, modelo: modelo, esforco: texto(a.esforco),
      conta: texto(a.conta), estado: estado, rotuloEstado: propria(ROTULO_ESTADO, estado) || estado,
      categoria: estado === "trabalhando" || estado === "esperando" ? "ativo" : estado === "ocioso" ? "ocioso" : "outro",
      sub: estado === "trabalhando" && a.trabalhando_ha ? "há " + texto(a.trabalhando_ha)
        : estado === "ocioso" && a.ultima_resposta_as ? "desde " + texto(a.ultima_resposta_as)
        : estado === "indeterminado" ? "a tela não diz" : "",
      contexto: numero(a.contexto_pct) ? Math.max(0, Math.min(100, a.contexto_pct)) : null,
      missao: texto(m.texto), situacao: texto(m.situacao), esperando: texto(m.esperando), previsao: texto(m.previsao),
      atividade: texto(a.atividade), ultimaFala: texto(a.ultima_fala), leitura: texto(a.leitura), motivo: texto(a.motivo),
      prs: lista(a.prs).filter(function (p) { return p && typeof p === "object"; }).map(function (p) {
        return { repo: texto(p.repo), numero: texto(p.numero), titulo: texto(p.titulo), rascunho: p.rascunho === true, ci: texto(objeto(p.ci).estado) };
      })
    };
  });
}
function contasDe(dados) {
  return lista(objeto(dados && dados.contas).lista).filter(function (c) { return c && typeof c === "object"; }).map(function (c) {
    var barras = lista(c.barras).filter(function (b) { return b && typeof b === "object"; }).map(function (b) {
      return { tipo: texto(b.tipo), rotulo: texto(b.rotulo), pct: numero(b.usado_pct) ? Math.max(0, Math.min(100, b.usado_pct)) : null,
               renovaEpoch: numero(b.renova_epoch) ? b.renova_epoch : null, renovaTexto: texto(b.renova_texto) };
    });
    return {
      id: texto(c.id) || texto(c.titulo), ferramenta: texto(c.ferramenta), ferramentaNome: texto(c.ferramenta_nome) || capitalizar(texto(c.ferramenta)),
      titulo: texto(c.titulo), email: texto(c.email), plano: texto(c.plano), situacao: texto(c.situacao),
      erro: c.erro ? (texto(c.erro_detalhe) || "falhou") : "", lidoEm: numero(c.lido_em_epoch) ? c.lido_em_epoch : null,
      barras: barras, semDados: !barras.some(function (b) { return b.pct !== null; })
    };
  });
}
function consumoClaude5h(contas) {
  var achada = null;
  contas.forEach(function (c) {
    if (achada || c.ferramenta !== "claude") return;
    c.barras.forEach(function (b) { if (!achada && b.tipo === "5h" && b.pct !== null) achada = { pct: b.pct, plano: c.plano }; });
  });
  return achada;
}
function rotuloBarra(b) {
  var resto = b.rotulo.indexOf(" · ") >= 0 ? b.rotulo.slice(b.rotulo.indexOf(" · ")) : "";
  if (b.tipo === "5h") return "Janela de 5 horas" + resto;
  if (b.tipo === "semana") return "Limite semanal" + resto;
  return b.rotulo || "Limite";
}
function cotacaoDe(g) {
  var c = g && g.cotacao;
  if (!(c && typeof c === "object" && numero(c.usd_brl) && c.usd_brl > 0)) return null;
  return { usdBrl: c.usd_brl, fonte: texto(c.fonte) || "desconhecida", dataEpoch: numero(c.data_epoch) ? c.data_epoch : null, desatualizada: c.desatualizada === true };
}
function custoDe(d) { return numero(d.cost_usd) && d.cost_usd > 0 ? d.cost_usd : 0; }
function tokensDe(d) {
  return ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"].reduce(function (s, k) { return s + (numero(d[k]) ? d[k] : 0); }, 0);
}
function dataPadrao(rotulo) {
  var r = String(rotulo), m;
  if ((m = /^\d{4}-(\d{2})-(\d{2})$/.exec(r))) return m[2] + "/" + m[1];
  if ((m = /^(\d{2}\/\d{2}) (\d{2}):\d{2}$/.exec(r))) return m[1] + " " + m[2] + ":00";
  return r;
}
function rotuloCurto(rotulo, granularidade, quantidade) {
  var completo = dataPadrao(rotulo), m;
  if (granularidade === "hora" && (m = /^\d{2}\/\d{2} (\d{2}):00$/.exec(completo))) return m[1] + "h";
  if (granularidade === "dia" && quantidade > 10 && (m = /^(\d{2})\/\d{2}$/.exec(completo))) return m[1];
  return completo;
}
/* Gastos de uma janela (24h, hoje, 7d, 30d), por canal ou por modelo. estado: ok | ausente | erro | sem_janela. */
function gastosDe(dados, janela, agrupamento) {
  var g = dados && dados.gastos_hermes;
  if (!g || typeof g !== "object") return { estado: "ausente" };
  if (g.erro) return { estado: "erro", erro: texto(g.erro) };
  var cot = cotacaoDe(g), j = g[janela];
  if (!j || typeof j !== "object") return { estado: "sem_janela", cotacao: cot };
  var total = objeto(j.total), bruta = lista(j.serie).filter(function (b) { return b && typeof b === "object"; });
  var serie = bruta.map(function (b) {
    var t = lista(b.total);
    return { rotulo: dataPadrao(b.rotulo), curto: rotuloCurto(b.rotulo, j.granularidade, bruta.length),
             usd: numero(t[0]) && t[0] > 0 ? t[0] : 0, tokens: numero(t[1]) ? t[1] : 0 };
  });
  var campo = agrupamento === "modelo" ? "modelos" : "origens";
  var grupos = lista(j[campo]).filter(function (d) { return d && typeof d === "object" && (tokensDe(d) > 0 || custoDe(d) > 0); })
    .map(function (d, i) {
      return { nome: texto(agrupamento === "modelo" ? d.model : d.source) || "sem nome", sub: texto(d.sub_rotulo), usd: custoDe(d), tokens: tokensDe(d),
               assinatura: d.inclui_assinatura === true, indice: i };
    }).sort(function (a, b) {
      if (b.usd !== a.usd) return b.usd - a.usd;
      if (a.usd === 0 && a.assinatura !== b.assinatura) return a.assinatura ? 1 : -1;
      return a.indice - b.indice;
    });
  return { estado: "ok", cotacao: cot, usd: custoDe(total), tokens: tokensDe(total), serie: serie, granularidade: texto(j.granularidade), grupos: grupos,
           rateado: g.custo_modelo_rateado === true, aproximada: g.distribuicao_aproximada === true, tipoCusto: texto(g.custo_tipo) };
}
/* Mesma regra da página clássica para a ponte do WhatsApp. */
function estadoWhatsapp(w, meta, agoraS) {
  meta = objeto(meta);
  var leitura = numero(meta.ultima_leitura_ok) ? meta.ultima_leitura_ok : meta.hora;
  var idade = numero(w.idade_s) ? w.idade_s + (numero(leitura) ? Math.max(0, agoraS - leitura) : 0) : null;
  if (w.estado === "ausente" || w.estado === "erro") return w.estado;
  if (w.needs_attention || meta.desatualizado) return "atenção";
  if (w.estado === "connected" && w.pid_vivo && w.gateway_state === "running") return idade === null || idade > 120 ? "atenção" : "conectado";
  return "caído";
}
function servicosDe(dados, agoraS) {
  var saida = [];
  lista(dados && dados.servicos).forEach(function (s) {
    if (!s || typeof s !== "object") return;
    var nome = texto(s.nome) || "Serviço", estado = s.estado === "ok" ? "ok" : s.estado === "fora" ? "fora" : "sem";
    saida.push({ id: nome.toLowerCase(), nome: nome, bom: estado === "ok", estado: estado,
                 rotulo: estado === "ok" ? "Disponível" : estado === "fora" ? "Indisponível" : "Sem leitura",
                 detalhe: texto(s.detalhe), hora: numero(s.hora) ? s.hora : null, icone: propria(ICONE_SERVICO, nome.toLowerCase()) || "server" });
  });
  var w = dados && dados.whatsapp;
  if (w && typeof w === "object" && !saida.some(function (s) { return s.id === "whatsapp"; })) {
    var meta = objeto(objeto(dados.fontes).whatsapp), est = estadoWhatsapp(w, meta, agoraS), bom = est === "conectado";
    var detalhe = [texto(w.error_code), meta.desatualizado ? "dado retido" : meta.ok === false ? texto(meta.erro) || "sem leitura" : ""].filter(Boolean).join(" · ");
    saida.push({ id: "whatsapp", nome: "WhatsApp", bom: bom, estado: bom ? "ok" : "fora", rotulo: capitalizar(est),
                 detalhe: detalhe || "Ponte do gateway Hermes", hora: numero(meta.hora) ? meta.hora : null, icone: "message" });
  }
  return saida;
}
function pendenciasDe(dados) {
  var itens = lista(objeto(dados && dados.quadro).pendencias).filter(function (p) { return p && typeof p === "object"; }).map(function (p, i) {
    var t = Date.parse(p.criada_em);
    return { chave: p.id ? "id:" + texto(p.id) : "t:" + texto(p.texto), tipo: p.tipo === "pergunta" ? "pergunta" : "acao", texto: texto(p.texto),
             recomendacao: texto(p.recomendacao), criadaEm: isNaN(t) ? null : t / 1000, destrava: texto(p.destrava), tempo: texto(p.tempo),
             passos: lista(p.passos).map(texto).filter(Boolean), fontes: lista(p.fontes).map(texto).filter(Boolean), indice: i };
  });
  return itens.sort(function (a, b) {
    if (a.tipo !== b.tipo) return a.tipo === "pergunta" ? -1 : 1;
    if (a.criadaEm === null || b.criadaEm === null) return a.criadaEm === null && b.criadaEm === null ? a.indice - b.indice : a.criadaEm === null ? 1 : -1;
    return b.criadaEm - a.criadaEm || a.indice - b.indice;
  });
}
function fontesComProblema(dados) {
  var fontes = objeto(dados && dados.fontes);
  return Object.keys(fontes).sort().filter(function (n) { var f = objeto(fontes[n]); return f.ok === false || f.desatualizado === true; }).map(function (n) {
    var f = objeto(fontes[n]);
    return n + " (" + (f.desatualizado === true ? "dado retido" : texto(f.erro) || "sem leitura") + ")";
  });
}
/* Presença por processo: produtos de IA com processo vivo; atividade desconhecida. O rótulo vem do dado (só validado aqui). */
function presencaDe(dados) {
  var p = dados && dados.presenca_processos;
  if (!p || typeof p !== "object" || p.estado !== "ok") return { ativa: false, itens: [], total: 0 };
  var familias = ["claude", "codex", "grok", "hermes", "cursor", "copilot", "maestri"], vistos = Object.create(null), itens = [], total = 0;
  lista(p.itens).forEach(function (it) {
    if (!it || typeof it !== "object") return;
    var produto = it.produto, rotulo = it.rotulo, n = it.instancias;
    if (typeof produto !== "string" || !produto || produto.length > 40) return;
    if (typeof rotulo !== "string" || !rotulo || rotulo.length > 60) return;
    if (!Number.isInteger(n) || n < 1 || vistos[produto]) return;
    vistos[produto] = true;
    itens.push({ produto: produto, rotulo: rotulo, familia: familias.indexOf(it.familia) >= 0 ? it.familia : "outra",
                 tipo: it.tipo === "cli" || it.tipo === "app" ? it.tipo : "outra", instancias: n });
    total += n;
  });
  return { ativa: true, itens: itens, total: total };
}
/* Presença no WSL (E18): bloco próprio, com o host no título; os itens passam pela mesma validação de presencaDe (sem alterá-la). */
function presencaLinuxDe(dados) {
  var p = dados && dados.presenca_linux;
  if (!p || typeof p !== "object" || (p.estado !== "ok" && p.estado !== "parada")) return { ativa: false, estado: "", host: "", itens: [], total: 0 };
  var host = typeof p.host === "string" && p.host.length >= 1 && p.host.length <= 40 ? p.host : "";
  if (p.estado === "parada") return { ativa: true, estado: "parada", host: host, itens: [], total: 0 };
  var v = presencaDe({ presenca_processos: { estado: "ok", itens: p.itens } });
  return { ativa: true, estado: "ok", host: host, itens: v.itens, total: v.total };
}
/* Uso observado em arquivos locais (E19): bloco próprio na seção Limites de uso; não entra em contasDe nem em #providers. Ordem fixa. */
function cotasLocaisDe(dados) {
  var fontes = [["cota_codex_local", "Codex", "observado localmente", "code"], ["cota_grok_bot", "Grok Bot", "observado localmente", "agents"],
                ["cota_cursor", "Cursor", "plano local", "agents"]];
  var enumerado = function (v) { return typeof v === "string" && /^[a-z_]{1,24}$/.test(v) ? v : ""; };
  var saida = [];
  fontes.forEach(function (f) {
    var v = dados && dados[f[0]];
    if (!v || typeof v !== "object" || Array.isArray(v) || (v.estado !== "ok" && v.estado !== "sem_dado")) return;
    var cursor = f[0] === "cota_cursor", barras = [];
    if (!cursor && v.estado === "ok") {
      barras = lista(v.barras).filter(function (b) { return b && typeof b === "object" && numero(b.usado_pct); }).slice(0, 4).map(function (b) {
        return { tipo: b.tipo === "5h" || b.tipo === "semana" ? b.tipo : "janela", pct: Math.max(0, Math.min(100, b.usado_pct)),
                 renovaEpoch: numero(b.renova_epoch) ? b.renova_epoch : null };
      });
    }
    saida.push({ chave: f[0], nome: f[1], detalhe: f[2], icone: f[3], estado: v.estado,
                 observadoEm: !cursor && numero(v.observado_epoch) ? v.observado_epoch : null, barras: barras,
                 plano: cursor ? enumerado(v.plano) : "", assinatura: cursor ? enumerado(v.assinatura) : "" });
  });
  return saida;
}
/* E19b: o Codex aparece num cartão só. Com números na conta, sai o observado; sem números na conta e com uso observado, saem as contas do Codex sem erro (conta com erro nunca é escondida). */
function unificarCodex(contas, locais) {
  contas = lista(contas); locais = lista(locais);
  var local = locais.filter(function (x) { return x && x.chave === "cota_codex_local"; })[0];
  if (!local) return { contas: contas.slice(), locais: locais.slice() };
  var doCodex = function (c) { return !!c && c.ferramenta === "codex"; };
  var semOLocal = function () { return locais.filter(function (x) { return x !== local; }); };
  if (contas.some(function (c) { return doCodex(c) && c.semDados === false; })) return { contas: contas.slice(), locais: semOLocal() };
  if (local.estado === "ok" && lista(local.barras).length > 0) return { contas: contas.filter(function (c) { return !doCodex(c) || !!c.erro; }), locais: locais.slice() };
  if (contas.some(doCodex)) return { contas: contas.slice(), locais: semOLocal() };
  return { contas: contas.slice(), locais: locais.slice() };
}
/* Vigilante 2 (E23): bloco próprio na seção Saúde dos serviços; não entra em servicosDe nem em pendenciasDe. */
var TIPOS_VIGILANTE = ["fim_etapa", "aguarda_decisao", "confianca_pasta", "login", "cota_baixa", "terminal_caiu", "servico_fora", "painel_fora", "wire_fora", "falhas_repetidas", "outro"];
var ROTULOS_VIGILANTE = { fim_etapa: "fim de etapa", aguarda_decisao: "aguarda decisão", confianca_pasta: "confiança de pasta", login: "login", cota_baixa: "cota baixa",
  terminal_caiu: "terminal caiu", servico_fora: "serviço fora", painel_fora: "painel fora", wire_fora: "Wire fora", falhas_repetidas: "falhas repetidas", outro: "outro" };
function vigilanteDe(dados) {
  var v = dados && dados.vigilante2;
  var vazio = { ativo: false, estado: "", geradoEm: null, wireOk: false, painelOk: false, online: 0, trabalhando: 0, atencao: 0, workspaces: 0, terminais: 0, falhas: 0,
                porTipo: { claude_code: 0, codex: 0, custom: 0, generic_shell: 0, outra: 0 }, alertas: [] };
  if (!v || typeof v !== "object" || Array.isArray(v) || (v.estado !== "ok" && v.estado !== "parado")) return vazio;
  var conta = function (x) { return numero(x) && x >= 0 ? Math.floor(x) : 0; };
  var pt = objeto(v.por_tipo);
  return { ativo: true, estado: v.estado, geradoEm: numero(v.gerado_em_epoch) ? v.gerado_em_epoch : null, wireOk: v.wire_ok === true, painelOk: v.painel_ok === true,
           online: conta(v.online), trabalhando: conta(v.trabalhando), atencao: conta(v.atencao), workspaces: conta(v.workspaces), terminais: conta(v.terminais), falhas: conta(v.falhas_seguidas),
           porTipo: { claude_code: conta(pt.claude_code), codex: conta(pt.codex), custom: conta(pt.custom), generic_shell: conta(pt.generic_shell), outra: conta(pt.outra) },
           alertas: lista(v.alertas).filter(function (a) { return a && typeof a === "object" && !Array.isArray(a); }).slice(0, 11).map(function (a) {
             return { tipo: TIPOS_VIGILANTE.indexOf(a.tipo) >= 0 ? a.tipo : "outro", desde: numero(a.desde_epoch) ? a.desde_epoch : null };
           }) };
}
function processosDe(dados) {
  var p = objeto(dados && dados.processos);
  return Object.keys(p).sort().map(function (n) { return n + " × " + texto(p[n]); });
}
function filtrarAgentes(agentes, filtro, busca) {
  var q = String(busca || "").toLocaleLowerCase("pt-BR").trim();
  return agentes.filter(function (a) {
    if (filtro === "ativos" && a.categoria !== "ativo") return false;
    if (filtro === "ociosos" && a.categoria !== "ocioso") return false;
    return !q || [a.nome, a.ferramentaNome, a.modelo, a.missao].join(" ").toLocaleLowerCase("pt-BR").indexOf(q) >= 0;
  });
}
function ordemDosServicos(servicos, filtro) { return servicos.filter(function (s) { return filtro !== "atencao" || !s.bom; }); }

/* ---------- transporte: SSE com fallback de polling ---------- */
function registrarErro(contexto, e) {
  if (typeof console !== "undefined" && console.error) console.error("hermes: falha em " + contexto + ":", e);
}
function iniciarTransporte(ambiente, receber, estado, carregarFallback, intervaloMs) {
  var fonte = null, timer = null;
  function periodico() { carregarFallback(); }
  function fallback() {
    if (timer !== null) return;
    periodico();
    timer = ambiente.setInterval(periodico, intervaloMs);
  }
  if (/^https?:$/.test(ambiente.location.protocol) && ambiente.EventSource) {
    try {
      fonte = new ambiente.EventSource("/eventos");
      fonte.onopen = function () {
        if (timer !== null) { ambiente.clearInterval(timer); timer = null; }
        estado("ao vivo");
      };
      fonte.addEventListener("dados", function (evento) {
        var dados;
        try { dados = JSON.parse(evento.data); } catch (e) { return; }          // publicação ilegível: aguarda a próxima
        try { receber(dados); } catch (e) { registrarErro("dados recebidos por /eventos", e); }
      });
      fonte.onerror = function () { fallback(); estado(fonte.readyState === 2 ? "desconectado" : "reconectando"); };
    } catch (e) { fallback(); estado("desconectado"); }
  } else { fallback(); estado("desconectado"); }
  return function () {
    if (fonte) fonte.close();
    if (timer !== null) { ambiente.clearInterval(timer); timer = null; }
  };
}

/* ---------- DOM: só textContent e atributos; nenhum dado vira HTML ---------- */
function el(doc, tag, classe, txt) {
  var e = doc.createElement(tag);
  if (classe) e.className = classe;
  if (txt != null) e.textContent = txt;
  return e;
}
function icone(doc, nome, classe) {
  var s = doc.createElementNS(SVGNS, "svg");
  s.setAttribute("class", "icon" + (classe ? " " + classe : ""));
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  (ICONES[nome] || ICONES.info).forEach(function (parte) {
    var n = doc.createElementNS(SVGNS, parte[0]);
    Object.keys(parte[1]).forEach(function (k) { n.setAttribute(k, parte[1][k]); });
    s.appendChild(n);
  });
  return s;
}
function setTxt(e, t) { if (e && e.textContent !== t) e.textContent = t; }
function setAttr(e, k, v) { v = String(v); if (e.getAttribute(k) !== v) e.setAttribute(k, v); }
function setCls(e, nome, ligada) { if (e.classList.contains(nome) !== !!ligada) e.classList.toggle(nome, !!ligada); }
/* Mantém os filhos de um contêiner alinhados a uma lista com chave, reaproveitando os nós (o foco e o clique não se perdem). */
function sincronizar(pai, itens, chaveDe, criar, atualizar) {
  var antigos = pai._mapa || Object.create(null), novos = Object.create(null);
  itens.forEach(function (it, i) {
    var k = chaveDe(it), no = antigos[k] || criar(k);
    novos[k] = no;
    atualizar(no, it);
    if (pai.children[i] !== no) pai.insertBefore(no, pai.children[i] || null);
  });
  Object.keys(antigos).forEach(function (k) { if (!novos[k]) pai.removeChild(antigos[k]); });
  pai._mapa = novos;
}
/* Região que só é refeita quando o modelo muda; o foco volta ao mesmo controle (marcado por foco(...)) depois. */
function desenharRegiao(doc, regiao, modelo, construir) {
  var assinatura = JSON.stringify(modelo);
  if (regiao._assinatura === assinatura) return;
  var ativo = doc.activeElement, antes = regiao._focos || {}, idFoco = ativo && ativo._focoId && antes[ativo._focoId] === ativo ? ativo._focoId : "";
  var focos = {};
  var filhos = construir(function (no, id) { no._focoId = id; focos[id] = no; return no; });
  regiao.replaceChildren.apply(regiao, filhos);
  regiao._assinatura = assinatura;
  regiao._focos = focos;
  if (idFoco && focos[idFoco] && focos[idFoco].focus) focos[idFoco].focus();
}

function criarApp(doc, win, opcoes) {
  opcoes = opcoes || {};
  var agoraS = opcoes.agora || function () { return Date.now() / 1000; };
  function $(id) { return doc.getElementById(id); }
  var armazenamento = null;
  try { armazenamento = win.localStorage; } catch (e) { armazenamento = null; }
  function ler(k) { try { return armazenamento ? armazenamento.getItem(k) : null; } catch (e) { return null; } }
  function gravar(k, v) { try { if (armazenamento) armazenamento.setItem(k, v); } catch (e) { /* vale só nesta sessão */ } }

  var temaSalvo = ler(CHAVE_TEMA);
  var estado = {
    dados: null, recebidoEm: null, transporte: "conectando…", filtroAgente: "todos", busca: "", janela: "7d", agrupamento: "canal",
    filtroServico: "todos", modal: null, abertas: {}, atualizando: false, secao: "overview",
    tema: TEMAS.indexOf(temaSalvo) >= 0 ? temaSalvo : "auto", privado: ler(CHAVE_PRIVACIDADE) !== "0"
  };
  var timerToast = null, focoAntesDoModal = null, fecharTransporte = null;
  var comoArquivo = !!(win.location && win.location.protocol === "file:");     // fetch e EventSource não funcionam por file:
  var m = function (t) { return estado.privado ? mascararEmails(t) : t; };    // todo texto de dado passa por aqui

  /* ---- preferências ---- */
  function temaResolvido() {
    if (estado.tema === "claro") return "light";
    if (estado.tema === "escuro") return "dark";
    return win.matchMedia && win.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }
  function trocarIcone(botao, nome) { botao.replaceChildren(icone(doc, nome)); }
  function aplicarPreferencias() {
    doc.documentElement.setAttribute("data-theme", temaResolvido());
    doc.body.classList.toggle("privacy-on", estado.privado);
    var pb = $("privacy-button"), tb = $("theme-button");
    trocarIcone(pb, estado.privado ? "eyeOff" : "eye");
    setAttr(pb, "aria-pressed", estado.privado);
    var rotuloP = estado.privado ? "Privacidade ligada: e-mails e valores ocultos. Clique para mostrar" : "Privacidade desligada. Clique para ocultar e-mails e valores";
    setAttr(pb, "aria-label", rotuloP); setAttr(pb, "title", rotuloP);
    trocarIcone(tb, estado.tema === "auto" ? "monitor" : estado.tema === "claro" ? "sun" : "moon");
    var rotuloT = "Tema: " + ROTULO_TEMA[estado.tema] + ". Clique para alternar";
    setAttr(tb, "aria-label", rotuloT); setAttr(tb, "title", rotuloT);
  }

  /* ---- aviso ---- */
  function notificar(mensagem, erro) {
    var t = $("toast");
    win.clearTimeout(timerToast);
    $("toast-icon").replaceChildren(icone(doc, erro ? "warning" : "checkCircle"));
    setTxt($("toast-text"), mensagem);
    setCls(t, "error", !!erro);
    t.classList.add("show");
    timerToast = win.setTimeout(function () { t.classList.remove("show"); }, erro ? 6000 : 3500);
  }

  /* ---- chip do topo, rodapé e barra lateral ---- */
  function desenharChip() {
    var chip = $("live-chip"), d = estado.dados, agora = agoraS(), t = estado.transporte;
    var idade = d && numero(d.gerado_em_epoch) ? Math.max(0, agora - d.gerado_em_epoch) : null;
    var velho = idade !== null && idade > VELHO_DEPOIS_DE_S;
    var txt = comoArquivo && !d ? "aberto como arquivo · sem servidor" : t + (idade !== null ? " · atualizado há " + haSegundos(idade) : d ? "" : " · sem dados");
    setTxt($("live-text"), txt);
    setCls(chip, "ok", t === "ao vivo" && !velho && !!d);
    setCls(chip, "warn", (velho && t !== "desconectado") || t === "reconectando" || t === "conectando…");
    setCls(chip, "off", t === "desconectado" || (!d && t === "ao vivo"));
    setAttr(chip, "title", d && numero(d.gerado_em_epoch) ? (velho ? "Dados velhos: o coletor não atualiza há " : "Última leitura do coletor há ") + haSegundos(idade) + " (" + dataHora(d.gerado_em_epoch) + ")" : "Ainda sem leitura do coletor");
    setTxt($("updated-label"), d && numero(d.gerado_em_epoch) ? "Última leitura do coletor: " + dataHora(d.gerado_em_epoch) : "sem leitura do coletor");
  }

  /* ---- KPIs ---- */
  function desenharKpis(agentes, contas, servicos, gastos) {
    var d = estado.dados, trab = 0, esp = 0;
    agentes.forEach(function (a) { if (a.estado === "trabalhando") trab++; if (a.estado === "esperando") esp++; });
    setTxt($("k-trab"), d ? String(trab) : "—");
    setTxt($("k-total"), "/ " + (d ? agentes.length : "—"));
    setTxt($("k-agentes-texto"), !d ? "sem dados" : !agentes.length ? "nenhum agente na leitura do coletor"
      : trab ? plural(trab, "agente trabalhando agora", "agentes trabalhando agora") + (esp ? " · " + esp + " aguardando" : "")
      : esp ? plural(esp, "agente aguardando você", "agentes aguardando você") : "todos os agentes em espera");
    var c5 = consumoClaude5h(contas);
    setTxt($("k-sessao"), c5 ? String(Math.round(c5.pct)) : "—");
    setTxt($("k-sessao-un"), c5 ? "%" : "");
    setTxt($("k-sessao-texto"), c5 ? "Claude" + (c5.plano ? " " + capitalizar(c5.plano) : "") + " · janela de 5 horas" : "sem dados de cota do Claude");
    if (gastos.estado === "ok") {
      var p = dinheiro(gastos.usd, gastos.cotacao);
      setTxt($("k-moeda"), p.moeda); setTxt($("k-gasto"), p.valor);
      setTxt($("k-gasto-usd"), p.secundario ? p.secundario + " · " : "");
      setTxt($("k-gasto-texto"), JANELAS[estado.janela] + " · estimado pelo Hermes");
    } else {
      setTxt($("k-moeda"), ""); setTxt($("k-gasto"), "—"); setTxt($("k-gasto-usd"), "");
      setTxt($("k-gasto-texto"), "sem dados de gastos");
    }
    var ok = servicos.filter(function (s) { return s.bom; }).length, atencao = servicos.length - ok;
    setTxt($("k-serv"), d ? String(ok) : "—");
    setTxt($("k-serv-total"), "/ " + (d ? servicos.length : "—"));
    var foot = $("k-serv-foot");
    setCls(foot, "warning", atencao > 0); setCls(foot, "ok", d && servicos.length > 0 && atencao === 0);
    setTxt($("k-serv-texto"), !d ? "sem dados" : !servicos.length ? "nenhum serviço na leitura"
      : atencao ? (atencao === 1 ? "1 serviço precisa de atenção" : atencao + " serviços precisam de atenção") : "todos os serviços respondendo");
    var na = $("nav-agentes"), ns = $("nav-servicos");
    setTxt(na, String(agentes.length)); na.hidden = !(d && agentes.length > 0);
    setTxt(ns, String(atencao)); ns.hidden = !(d && atencao > 0);
    setTxt($("side-origem"), d ? "origem: " + (texto(d.origem) || "desconhecida") : "sem dados");
  }

  /* ---- agentes ---- */
  function criarLinha(chave) {
    var tr = el(doc, "tr", "agent-row"), r = tr._r = {};
    tr._chave = chave;
    var td1 = el(doc, "td"), btn = r.btn = el(doc, "button", "agent-name");
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "dialog");
    r.logo = el(doc, "span", "agent-logo");
    var quem = el(doc, "span");
    r.nome = el(doc, "strong"); r.sub = el(doc, "small");
    quem.appendChild(r.nome); quem.appendChild(r.sub);
    btn.appendChild(r.logo); btn.appendChild(quem); td1.appendChild(btn);
    var td2 = el(doc, "td"); r.status = el(doc, "span", "status"); r.status.appendChild(el(doc, "span", "dot")); r.estadoTxt = el(doc, "span");
    r.status.appendChild(r.estadoTxt); r.estadoSub = el(doc, "small", "status-sub"); td2.appendChild(r.status); td2.appendChild(r.estadoSub);
    var td3 = el(doc, "td"); r.ctx = el(doc, "div", "context-cell");
    var trilho = el(doc, "div", "track"); r.barra = el(doc, "span"); trilho.appendChild(r.barra);
    r.pct = el(doc, "span"); r.ctx.appendChild(trilho); r.ctx.appendChild(r.pct); td3.appendChild(r.ctx);
    r.missao = el(doc, "td", "mission-col mission");
    var td5 = el(doc, "td", "row-chevron"); td5.appendChild(icone(doc, "chevron"));
    [td1, td2, td3, r.missao, td5].forEach(function (td) { tr.appendChild(td); });
    tr.addEventListener("click", function () { abrirModal({ tipo: "agente", chave: tr._chave }, tr._r.btn); });
    return tr;
  }
  function iconeDoAgente(f) { return f === "claude" ? "claude" : f === "codex" ? "code" : f === "maestri" ? "terminal" : "agents"; }
  function atualizarLinha(tr, a) {
    var r = tr._r;
    if (r.ferramenta !== a.ferramenta) { r.ferramenta = a.ferramenta; r.logo.replaceChildren(icone(doc, iconeDoAgente(a.ferramenta))); }
    setTxt(r.nome, m(a.nome));
    setTxt(r.sub, m([a.ferramentaNome, a.modelo].filter(Boolean).join(" · ")));
    setAttr(r.btn, "aria-label", "Ver detalhes de " + m(a.nome));
    var cls = a.estado === "trabalhando" ? "work" : a.estado === "esperando" ? "wait" : a.estado === "ocioso" ? "idle" : "stop";
    if (r.status.className !== "status " + cls) r.status.className = "status " + cls;
    setTxt(r.estadoTxt, a.rotuloEstado);
    setTxt(r.estadoSub, a.sub); r.estadoSub.hidden = !a.sub;
    var ctxCls = "context-cell" + (a.contexto === null ? " none" : a.contexto >= 60 ? " crit" : a.contexto >= 55 ? " warn" : "");
    if (r.ctx.className !== ctxCls) r.ctx.className = ctxCls;
    r.barra.style.width = a.contexto === null ? "0%" : a.contexto + "%";
    setTxt(r.pct, a.contexto === null ? "—" : Math.round(a.contexto) + "%");
    var textoMissao = a.missao || (a.estado === "trabalhando" ? a.atividade : "");
    setTxt(r.missao, textoMissao ? m(textoMissao) : "—");
    setAttr(r.missao, "title", m(textoMissao || (a.leitura && a.leitura !== "ok" ? a.motivo : "")));
    setCls(tr, "selected", !!estado.modal && estado.modal.tipo === "agente" && estado.modal.chave === a.chave);
    tr._chave = a.chave;
  }
  function desenharAgentes(agentes) {
    var d = estado.dados;
    var contagem = { todos: agentes.length, ativos: 0, ociosos: 0 };
    agentes.forEach(function (a) { if (a.categoria === "ativo") contagem.ativos++; if (a.categoria === "ocioso") contagem.ociosos++; });
    setTxt($("tab-todos"), String(contagem.todos)); setTxt($("tab-ativos"), String(contagem.ativos)); setTxt($("tab-ociosos"), String(contagem.ociosos));
    setTxt($("agents-badge"), plural(agentes.length, "sessão", "sessões"));
    var visiveis = filtrarAgentes(agentes, estado.filtroAgente, estado.busca);
    sincronizar($("agent-rows"), visiveis, function (a) { return a.chave; }, criarLinha, atualizarLinha);
    var vazio = $("agent-empty");
    vazio.hidden = visiveis.length > 0;
    if (!visiveis.length) {
      setTxt(vazio, !d ? "Aguardando a primeira leitura do coletor."
        : !agentes.length ? "Nenhum agente na leitura do coletor."
        : estado.filtroAgente === "ativos" && !estado.busca ? "Nenhum agente em atividade agora." : "Nenhum agente encontrado.");
    }
  }

  /* ---- presença por processo (aditiva: não entra na lista de sessões nem nos números de agentes; não depende de abas nem da busca) ---- */
  function criarLinhaPresenca(chave) {
    var tr = el(doc, "tr", "presence-row"), r = tr._r = {};
    var td1 = el(doc, "td"), nome = el(doc, "span", "agent-name");
    r.logo = el(doc, "span", "agent-logo");
    var quem = el(doc, "span");
    r.rotulo = el(doc, "strong"); r.detalhe = el(doc, "small");
    quem.appendChild(r.rotulo); quem.appendChild(r.detalhe);
    nome.appendChild(r.logo); nome.appendChild(quem); td1.appendChild(nome);
    var td2 = el(doc, "td"), status = el(doc, "span", "status good");
    status.appendChild(el(doc, "span", "dot")); status.appendChild(el(doc, "span", null, "online"));
    td2.appendChild(status); td2.appendChild(el(doc, "small", "status-sub", "atividade desconhecida"));
    tr.appendChild(td1); tr.appendChild(td2);
    return tr;
  }
  function atualizarLinhaPresenca(tr, item) {
    var r = tr._r;
    if (r.familia !== item.familia) { r.familia = item.familia; r.logo.replaceChildren(icone(doc, iconeDoAgente(item.familia))); }
    setTxt(r.rotulo, m(item.rotulo));
    var tipo = item.tipo === "cli" ? "CLI" : item.tipo === "app" ? "aplicativo" : "";
    setTxt(r.detalhe, [tipo, item.instancias > 1 ? item.instancias + " instâncias" : ""].filter(Boolean).join(" · "));
  }
  function desenharPresenca(p) {
    $("presence").hidden = !(p.ativa && p.itens.length > 0);
    sincronizar($("presence-rows"), p.itens, function (item) { return item.produto; }, criarLinhaPresenca, atualizarLinhaPresenca);
    setTxt($("presence-badge"), plural(p.total, "processo", "processos"));
    $("k-online-foot").hidden = !p.ativa;
    setTxt($("k-online-texto"), p.total > 0 ? plural(p.total, "processo de IA online", "processos de IA online") : "nenhum processo de IA online");
  }
  function desenharPresencaLinux(l) {      // bloco próprio do WSL: não mexe no bloco nem no cartão da E16 (o cartão-contador não soma o WSL)
    $("presence-linux").hidden = !l.ativa;
    setTxt($("presence-linux-titulo"), l.host ? "WSL · " + m(l.host) : "WSL");
    setTxt($("presence-linux-badge"), l.estado === "parada" ? "parada" : l.total > 0 ? "ligada · " + plural(l.total, "processo", "processos") : "ligada · nenhum processo");
    sincronizar($("presence-linux-rows"), l.itens, function (item) { return item.produto; }, criarLinhaPresenca, atualizarLinhaPresenca);
  }

  /* ---- limites de uso ---- */
  function desenharLimites(contas) {
    var d = estado.dados, agora = agoraS();
    var modelo = contas.map(function (c) {
      return { c: c, barras: c.barras.map(function (b) { return { r: rotuloBarra(b), p: b.pct, t: textoReset(b, agora), w: b.tipo === "semana" }; }),
               sub: [c.email, c.lidoEm ? "lido há " + duracao(agora - c.lidoEm) : ""].filter(Boolean).join(" · ") };
    });
    desenharRegiao(doc, $("providers"), { modelo: modelo, privado: estado.privado, d: !!d }, function (foco) {
      if (!d) return [el(doc, "p", "usage-sub", "Aguardando a primeira leitura do coletor.")];
      if (!modelo.length) return [el(doc, "p", "usage-sub", "Sem contas de uso na leitura do coletor.")];
      return modelo.map(function (item) {
        var c = item.c, prov = el(doc, "div", "provider");
        var cab = el(doc, "div", "provider-header");
        var logo = el(doc, "span", "provider-logo" + (c.ferramenta === "claude" ? "" : " codex"));
        logo.appendChild(icone(doc, c.ferramenta === "claude" ? "claude" : "code"));
        var nome = el(doc, "div", "provider-name"), partes = c.ferramentaNome.split(" ");
        nome.appendChild(doc.createTextNode(partes[0] || "Conta"));
        if (partes.length > 1) nome.appendChild(el(doc, "small", null, partes.slice(1).join(" ")));
        cab.appendChild(logo); cab.appendChild(nome);
        cab.appendChild(el(doc, "span", "badge" + (c.erro ? " bad" : ""), c.erro ? "ERRO" : c.semDados ? "SEM DADOS" : (c.plano ? capitalizar(c.plano) : "ATIVA")));
        prov.appendChild(cab);
        if (c.semDados) {
          var falta = el(doc, "div", "missing");
          falta.appendChild(el(doc, "p", null, c.erro ? m(c.erro) : c.email ? "Cota ainda não lida para " + m(c.email) + "." : "Conta ainda não identificada."));
          var ver = el(doc, "button", "text-button", "Ver detalhes ");
          ver.type = "button"; ver.appendChild(icone(doc, "arrow"));
          ver.addEventListener("click", function () { abrirModal({ tipo: "conta", chave: c.id }, ver); });
          falta.appendChild(foco(ver, "conta:" + c.id));
          prov.appendChild(falta);
          return prov;
        }
        item.barras.forEach(function (b) {
          var linha = el(doc, "div", "usage-row" + (b.w ? " week" : ""));
          var rot = el(doc, "div", "usage-label");
          rot.appendChild(el(doc, "span", null, b.r));
          rot.appendChild(el(doc, "strong", "mono", b.p === null ? "sem dados" : Math.round(b.p) + "%"));
          var trilho = el(doc, "div", "track");
          trilho.setAttribute("role", "meter"); trilho.setAttribute("aria-label", m(b.r + " de " + c.ferramentaNome));
          trilho.setAttribute("aria-valuemin", "0"); trilho.setAttribute("aria-valuemax", "100");
          if (b.p !== null) trilho.setAttribute("aria-valuenow", String(Math.round(b.p)));
          var cheio = el(doc, "span"); cheio.style.width = (b.p === null ? 0 : b.p) + "%"; trilho.appendChild(cheio);
          linha.appendChild(rot); linha.appendChild(trilho); linha.appendChild(el(doc, "div", "usage-sub", b.p === null ? "" : b.t));
          prov.appendChild(linha);
        });
        if (item.sub || c.erro) prov.appendChild(el(doc, "div", "usage-sub" + (c.erro ? " err" : ""), m(c.erro || item.sub)));
        return prov;
      });
    });
  }
  /* Bloco "Observado em arquivos locais" (E19): o modelo traz todos os textos já calculados; #providers não muda. */
  function desenharCotasLocais(lista) {
    var agora = agoraS();
    var assinaturas = { active: "ativa", trialing: "em teste", past_due: "pagamento atrasado", canceled: "cancelada", unpaid: "não paga",
                        incomplete: "incompleta", incomplete_expired: "incompleta (expirada)", paused: "pausada" };
    $("limits-local").hidden = lista.length === 0;
    setTxt($("limits-local-badge"), plural(lista.length, "fonte", "fontes"));
    var modelo = lista.map(function (item) {
      var cursor = item.chave === "cota_cursor", ok = item.estado === "ok";
      var selo = !ok ? "SEM DADOS" : !cursor ? "OBSERVADO" : item.plano ? capitalizar(item.plano.replace(/_/g, " ")) : "PLANO NÃO LIDO";
      return {
        nome: item.nome, detalhe: item.detalhe, icone: item.icone, selo: selo, semDado: !ok, cursor: ok && cursor,
        assinatura: ok && cursor && item.assinatura ? "assinatura: " + (propria(assinaturas, item.assinatura) || "outra") : "",
        barras: ok && !cursor ? item.barras.map(function (b) {
          var r = rotuloBarra({ tipo: b.tipo, rotulo: "Janela atual" });
          return { r: r, p: b.pct, t: textoReset({ renovaEpoch: b.renovaEpoch }, agora), w: b.tipo === "semana", a: m(r + " de " + item.nome) };
        }) : [],
        rodape: ok && !cursor ? (item.observadoEm !== null ? "último observado há " + duracao(agora - item.observadoEm) : "horário da observação não informado") : ""
      };
    });
    desenharRegiao(doc, $("limits-local-cards"), modelo, function () {
      return modelo.map(function (c) {
        var prov = el(doc, "div", "provider"), cab = el(doc, "div", "provider-header");
        var logo = el(doc, "span", "provider-logo codex");
        logo.appendChild(icone(doc, c.icone));
        var nome = el(doc, "div", "provider-name", c.nome);
        nome.appendChild(el(doc, "small", null, c.detalhe));
        cab.appendChild(logo); cab.appendChild(nome); cab.appendChild(el(doc, "span", "badge", c.selo));
        prov.appendChild(cab);
        if (c.semDado || c.cursor) {
          var falta = el(doc, "div", "missing");
          falta.appendChild(el(doc, "p", null, c.semDado ? "Nenhum uso encontrado nos arquivos locais." : "Sem dados de cota: o Cursor não guarda a cota em arquivo local."));
          prov.appendChild(falta);
          if (c.assinatura) prov.appendChild(el(doc, "div", "usage-sub", c.assinatura));
          return prov;
        }
        c.barras.forEach(function (b) {
          var linha = el(doc, "div", "usage-row" + (b.w ? " week" : ""));
          var rot = el(doc, "div", "usage-label");
          rot.appendChild(el(doc, "span", null, b.r));
          rot.appendChild(el(doc, "strong", "mono", Math.round(b.p) + "%"));
          var trilho = el(doc, "div", "track");
          trilho.setAttribute("role", "meter"); trilho.setAttribute("aria-label", b.a);
          trilho.setAttribute("aria-valuemin", "0"); trilho.setAttribute("aria-valuemax", "100");
          trilho.setAttribute("aria-valuenow", String(Math.round(b.p)));
          var cheio = el(doc, "span"); cheio.style.width = b.p + "%"; trilho.appendChild(cheio);
          linha.appendChild(rot); linha.appendChild(trilho); linha.appendChild(el(doc, "div", "usage-sub", b.t));
          prov.appendChild(linha);
        });
        prov.appendChild(el(doc, "div", "usage-sub", c.rodape));
        return prov;
      });
    });
  }

  /* ---- gastos ---- */
  function desenharGastos(g) {
    var d = estado.dados, cot = g.cotacao || null;
    var tot = g.estado === "ok" ? dinheiro(g.usd, cot) : null;
    setTxt($("cost-desc"), "Distribuição do consumo · " + JANELAS[estado.janela]);
    desenharRegiao(doc, $("cost-total"), { t: tot }, function () {
      return tot ? [el(doc, "small", null, tot.moeda), doc.createTextNode(tot.valor)] : [doc.createTextNode("—")];
    });
    setTxt($("cost-tokens"), g.estado === "ok" ? [tot.secundario, fmtTokens(g.tokens)].filter(Boolean).join(" · ") : "");
    var maximo = 0, fator = cot ? cot.usdBrl : 1;
    if (g.estado === "ok") g.serie.forEach(function (b) { if (b.usd * fator > maximo) maximo = b.usd * fator; });
    var topo = maxBonito(maximo);          // na moeda exibida
    desenharRegiao(doc, $("chart"), { g: g.estado === "ok" ? g.serie : g.estado, cot: cot && cot.usdBrl, priv: estado.privado }, function (foco) {
      if (g.estado !== "ok" || !g.serie.length) return [el(doc, "div", "chart-empty", g.estado === "ok" ? "sem série neste período" : "sem dados")];
      var y = el(doc, "div", "chart-y");
      [topo, topo / 2, 0].forEach(function (v) { y.appendChild(el(doc, "span", null, eixo(v, cot))); });
      var plot = el(doc, "div", "chart-plot"), x = el(doc, "div", "chart-x");
      var passo = Math.max(1, Math.ceil(g.serie.length / 8));
      g.serie.forEach(function (b, i) {
        var barra = el(doc, "button", "chart-bar" + (b.usd > 0 ? "" : " zero"));
        barra.type = "button";
        barra.style.setProperty("--height", (b.usd > 0 ? Math.max(2, b.usd * fator / topo * 100) : 0) + "%");
        var valor = dinheiro(b.usd, cot).principal;
        barra.setAttribute("aria-label", b.rotulo + ": " + (estado.privado ? "valor oculto" : valor));
        barra.addEventListener("click", function () { notificar(b.rotulo + " · " + (estado.privado ? "••••" : valor + (cot ? " (" + fmtUsd(b.usd) + ")" : ""))); });
        plot.appendChild(foco(barra, "barra:" + i));
        x.appendChild(el(doc, "span", null, i % passo === 0 || i === g.serie.length - 1 ? b.curto : ""));
      });
      return [y, plot, x];
    });
    var maiorGrupo = 0;
    if (g.estado === "ok") g.grupos.forEach(function (x) { if (x.usd > maiorGrupo) maiorGrupo = x.usd; });
    desenharRegiao(doc, $("breakdown"), { g: g.estado === "ok" ? g.grupos : g.estado, e: g.erro || "", cot: cot && cot.usdBrl, priv: estado.privado }, function () {
      if (g.estado !== "ok") {
        return [el(doc, "p", "neutral-label", g.estado === "erro" ? g.erro : g.estado === "sem_janela" ? "Sem leitura desta janela de gastos." : "Sem dados: o coletor não publicou os gastos do Hermes.")];
      }
      if (!g.grupos.length) return [el(doc, "p", "neutral-label", "Sem consumo registrado neste período.")];
      return g.grupos.map(function (x) {
        var linha = el(doc, "div", "breakdown-row"), rot = el(doc, "div", "breakdown-label");
        rot.appendChild(el(doc, "span", null, x.nome + (x.sub ? " · " + x.sub : "")));
        var v = dinheiro(x.usd, cot);
        var forte = el(doc, "strong", "mono sensitive", x.usd === 0 && x.assinatura ? "assinatura" : v.principal);
        if (v.secundario && !estado.privado) forte.setAttribute("title", v.secundario + (x.assinatura ? " · inclui assinatura" : ""));   // com privacidade, valor nenhum em atributo
        rot.appendChild(forte);
        var trilho = el(doc, "div", "track sensitive"), cheio = el(doc, "span");
        cheio.style.width = (maiorGrupo > 0 ? Math.round(x.usd / maiorGrupo * 100) : 0) + "%";
        trilho.appendChild(cheio); linha.appendChild(rot); linha.appendChild(trilho);
        return linha;
      });
    });
    var avisos = [], notas = [], problema = d && objeto(objeto(d.fontes).gastos_hermes);
    if (problema && problema.ok === false) avisos.push("Fonte de gastos com problema: " + (texto(problema.erro) || "sem leitura"));
    else if (problema && problema.desatualizado === true) avisos.push("Gastos retidos: a leitura mais recente falhou");
    if (cot) notas.push("Cotação US$ 1 = R$ " + fmtNum(cot.usdBrl, 2, 4) + " (" + cot.fonte + (cot.dataEpoch ? ", " + dataHora(cot.dataEpoch) : "") + ")" + (cot.desatualizada ? " · desatualizada" : ""));
    if (g.estado === "ok") {
      notas.push((g.tipoCusto ? "custo " + g.tipoCusto : "custo estimado") + "; não inclui Claude Code/Codex fora do Hermes");
      if (g.rateado) notas.push("custo por modelo é rateio aproximado");
      if (g.aproximada) notas.push("distribuição aproximada pelas mensagens");
    }
    var notasEl = $("cost-notes");
    setCls(notasEl, "warn", avisos.length > 0);
    desenharRegiao(doc, notasEl, { a: avisos, n: notas }, function () {
      return avisos.map(function (t) { return el(doc, "span", null, "▲ " + t); }).concat(notas.map(function (t) { return el(doc, "span", null, t); }));
    });
  }

  /* ---- precisa de você ---- */
  function comCodigo(pai, t) {      // trecho entre crases vira <code>; o resto é texto
    var partes = String(t).split("`");
    partes.forEach(function (p, i) {
      if (!p) return;
      if (i % 2 === 1 && i < partes.length - 1) pai.appendChild(el(doc, "code", null, m(p)));
      else pai.appendChild(doc.createTextNode(m(i % 2 === 1 ? "`" + p : p)));
    });
  }
  function desenharPendencias(pendencias) {
    var d = estado.dados, agora = agoraS();
    setTxt($("attention-badge"), String(pendencias.length));
    var erro = d ? texto(objeto(d.quadro).erro) : "";
    var nota = $("attention-note");
    nota.hidden = !erro; setTxt(nota, erro ? "▲ " + m(erro) : "");
    var itens = pendencias.map(function (p) {
      return { p: p, idade: p.criadaEm === null ? "" : "há " + duracao(agora - p.criadaEm), aberta: !!estado.abertas[p.chave] && p.passos.length > 0 };
    });
    desenharRegiao(doc, $("attention-body"), { itens: itens, priv: estado.privado, d: !!d }, function (foco) {
      if (!d) return [el(doc, "p", "usage-sub", "Aguardando a primeira leitura do coletor.")];
      if (!itens.length) {
        var vazio = el(doc, "div", "empty-state"), ic = el(doc, "div", "empty-icon");
        ic.appendChild(icone(doc, "check")); vazio.appendChild(ic);
        vazio.appendChild(el(doc, "h3", null, "Tudo em dia por aqui."));
        vazio.appendChild(el(doc, "p", null, "Nada esperando por você agora."));
        return [vazio];
      }
      var ul = el(doc, "ul", "pending-list"), n = 0, tipoAnterior = "";
      itens.forEach(function (it) {
        var p = it.p;
        if (p.tipo !== tipoAnterior) { tipoAnterior = p.tipo; ul.appendChild(el(doc, "li", "pending-kind", p.tipo === "pergunta" ? "RESPONDA" : "FAÇA")); }
        var li = el(doc, "li", "pending-item"), podeAbrir = p.passos.length > 0;
        var topo = el(doc, podeAbrir ? "button" : "div", "pending-main");
        if (podeAbrir) {
          topo.type = "button"; topo.setAttribute("aria-expanded", String(it.aberta));
          topo.addEventListener("click", function () {
            if (estado.abertas[p.chave]) delete estado.abertas[p.chave]; else estado.abertas[p.chave] = 1;
            desenharPendencias(pendenciasDe(estado.dados));
          });
        }
        topo.appendChild(el(doc, "span", "pending-n", String(++n)));
        var corpo = el(doc, "span", "pending-text", m(p.texto));
        if (p.tipo === "pergunta" && p.recomendacao) corpo.appendChild(el(doc, "small", null, "Recomendo: " + m(p.recomendacao)));
        topo.appendChild(corpo);
        if (it.idade) topo.appendChild(el(doc, "span", "pending-age", it.idade));
        li.appendChild(podeAbrir ? foco(topo, "pend:" + p.chave) : topo);
        if (it.aberta) {
          var det = el(doc, "div", "pending-detail");
          if (p.destrava) det.appendChild(el(doc, "span", null, "Destrava: " + m(p.destrava)));
          if (p.tempo) det.appendChild(el(doc, "span", null, "Tempo: " + m(p.tempo)));
          var ol = el(doc, "ol");
          p.passos.forEach(function (passo) { var item = el(doc, "li"); comCodigo(item, passo); ol.appendChild(item); });
          det.appendChild(ol);
          if (p.fontes.length) det.appendChild(el(doc, "span", null, (p.fontes.length === 1 ? "Fonte: " : "Fontes: ") + m(p.fontes.join(", "))));
          li.appendChild(det);
        }
        ul.appendChild(li);
      });
      return [ul];
    });
  }

  /* ---- serviços ---- */
  function desenharServicos(servicos) {
    var d = estado.dados, agora = agoraS();
    var atencao = servicos.filter(function (s) { return !s.bom; }).length, ok = servicos.length - atencao;
    var vis = ordemDosServicos(servicos, estado.filtroServico);
    setTxt($("services-badge"), plural(vis.length, "serviço", "serviços"));
    var itens = vis.map(function (s) { return { s: s, t: s.hora === null ? "sem horário" : "verificado há " + duracao(agora - s.hora) }; });
    desenharRegiao(doc, $("services-grid"), { itens: itens, priv: estado.privado }, function (foco) {
      return itens.map(function (it) {
        var s = it.s, b = el(doc, "button", "service");
        b.type = "button"; b.setAttribute("data-service", s.id);
        b.setAttribute("aria-label", "Ver diagnóstico de " + m(s.nome) + ", " + s.rotulo);
        var topo = el(doc, "div", "service-top"), ic = el(doc, "span", "service-icon");
        ic.appendChild(icone(doc, s.icone));
        var st = el(doc, "span", "status " + (s.bom ? "good" : s.estado === "fora" ? "bad" : "stop"));
        st.appendChild(el(doc, "span", "dot")); st.appendChild(el(doc, "span", null, s.rotulo));
        topo.appendChild(ic); topo.appendChild(st); b.appendChild(topo);
        b.appendChild(el(doc, "h3", null, m(s.nome)));
        b.appendChild(el(doc, "p", null, m(s.detalhe) || "—"));
        var tempo = el(doc, "div", "service-time"); tempo.appendChild(icone(doc, "clock")); tempo.appendChild(el(doc, "span", null, it.t));
        b.appendChild(tempo);
        b.addEventListener("click", function () { abrirModal({ tipo: "servico", chave: s.id }, b); });
        return foco(b, "serv:" + s.id);
      });
    });
    var vazio = $("services-empty");
    vazio.hidden = itens.length > 0;
    if (!itens.length) setTxt(vazio, !d ? "Aguardando a primeira leitura do coletor." : !servicos.length ? "Nenhum serviço na leitura do coletor." : "Nenhum serviço precisa de atenção.");
    var resumo = $("services-summary");
    setCls(resumo, "warn", atencao > 0);
    setTxt($("services-summary-text"), !d ? "sem dados" : ok + (ok === 1 ? " disponível" : " disponíveis") + " / " + atencao + (atencao === 1 ? " precisa de atenção" : " precisam de atenção"));
    var procs = processosDe(d);
    setTxt($("services-processes"), procs.length ? "Processos: " + procs.join(" · ") : "sem processos observados");
    var fontes = d ? fontesComProblema(d) : [];
    var rodape = $("sources-foot");
    rodape.hidden = !fontes.length;
    setTxt(rodape, fontes.length ? "Fontes com problema: " + m(fontes.join(" · ")) : "");
    var foot = $("attention-foot");
    foot.hidden = !(d && atencao > 0);
    setTxt($("attention-foot-text"), atencao === 1 ? "1 serviço precisa de atenção" : atencao + " serviços precisam de atenção");
  }

  /* ---- Vigilante 2 (E23): cartões só de leitura, sem clique e sem modal ---- */
  function desenharVigilante(v) {
    var agora = agoraS(), parado = v.estado === "parado";
    $("vigilante").hidden = !v.ativo;
    setTxt($("vigilante-badge"), parado ? "PARADO" : "ATIVO");
    setCls($("vigilante-badge"), "bad", parado);
    var desdeHa = function (t) { return t === null ? "sem horário" : duracao(agora - t); };
    var cartoes = [];
    if (v.ativo && parado) {
      cartoes.push({ icone: "shield", cls: "bad", rotulo: "Parado", titulo: "Vigilante 2", texto: "Vigilante 2 parado",
                     tempo: v.geradoEm === null ? "sem horário" : "último sinal há " + desdeHa(v.geradoEm) });
    } else if (v.ativo) {
      cartoes.push({ icone: "shield", cls: "good", rotulo: "Ativo", titulo: "Vigilante 2",
                     texto: v.online + " online · " + v.trabalhando + " trabalhando · " + v.atencao + " em atenção",
                     tempo: v.geradoEm === null ? "sem horário" : "atualizado há " + desdeHa(v.geradoEm) });
      if (!v.alertas.length) {
        cartoes.push({ icone: "warning", cls: "good", rotulo: "Nenhum alerta", titulo: "Alertas ativos", texto: "Nada pendente", tempo: "sem alertas" });
      } else {
        var desdes = v.alertas.filter(function (a) { return a.desde !== null; }).map(function (a) { return a.desde; });
        cartoes.push({ icone: "warning", cls: "bad", rotulo: plural(v.alertas.length, "alerta", "alertas"), titulo: "Alertas ativos",
                       texto: v.alertas.map(function (a) { return ROTULOS_VIGILANTE[a.tipo]; }).join(" · "),
                       tempo: desdes.length ? "o mais antigo há " + duracao(agora - Math.min.apply(null, desdes)) : "início não informado" });
      }
      var emOrdem = v.wireOk && v.painelOk && v.falhas === 0;
      cartoes.push({ icone: "activity", cls: emOrdem ? "good" : "bad", rotulo: emOrdem ? "Em ordem" : "Com falha", titulo: "Fontes do Vigilante",
                     texto: "Wire " + (v.wireOk ? "ok" : "fora") + " · Painel " + (v.painelOk ? "ok" : "fora"),
                     tempo: v.falhas > 0 ? plural(v.falhas, "falha seguida", "falhas seguidas") : "sem falhas seguidas" });
      var pt = v.porTipo;
      cartoes.push({ icone: "monitor", cls: "idle", rotulo: plural(v.terminais, "terminal", "terminais"), titulo: "Terminais por tipo",
                     texto: "Claude Code " + pt.claude_code + " · Codex " + pt.codex + " · Personalizado " + pt.custom + " · Shell " + pt.generic_shell + " · Outros " + pt.outra,
                     tempo: plural(v.workspaces, "workspace", "workspaces") });
    }
    desenharRegiao(doc, $("vigilante-grid"), { cartoes: cartoes }, function () {
      return cartoes.map(function (c) {
        var card = el(doc, "div", "service"), topo = el(doc, "div", "service-top"), ic = el(doc, "span", "service-icon");
        ic.appendChild(icone(doc, c.icone));
        var st = el(doc, "span", "status " + c.cls);
        st.appendChild(el(doc, "span", "dot")); st.appendChild(el(doc, "span", null, c.rotulo));
        topo.appendChild(ic); topo.appendChild(st); card.appendChild(topo);
        card.appendChild(el(doc, "h3", null, c.titulo));
        var p = el(doc, "p", null, c.texto); p.setAttribute("title", c.texto); card.appendChild(p);
        var tempo = el(doc, "div", "service-time"); tempo.appendChild(icone(doc, "clock")); tempo.appendChild(el(doc, "span", null, c.tempo));
        card.appendChild(tempo);
        return card;
      });
    });
  }

  /* ---- janela de detalhes (agente, serviço ou conta); atualiza no lugar quando os dados mudam ---- */
  function modeloDoModal() {
    var sel = estado.modal, d = estado.dados, agora = agoraS();
    if (!sel) return null;
    if (sel.tipo === "agente") {
      var a = agentesDe(d).filter(function (x) { return x.chave === sel.chave; })[0];
      if (!a) return { titulo: "Agente", desc: "Este agente não está mais na leitura do coletor.", linhas: [], nota: "" };
      var prs = a.prs.length ? a.prs.map(function (p) { return (p.repo ? p.repo + " " : "") + "#" + p.numero + (p.rascunho ? " (rascunho)" : "") + (p.ci ? " · CI " + p.ci : "") + (p.titulo ? " · " + p.titulo : ""); }).join("\n")
        : objeto(d && d.prs).consultado ? "Nenhum PR vinculado" : "Não consultados nesta leitura";
      var ctx = a.contexto === null ? "Sem dado" : Math.round(a.contexto) + "%";
      var linhas = [["Estado", a.rotuloEstado + (a.sub ? " · " + a.sub : "")], ["Ferramenta", a.ferramentaNome || "—"], ["Modelo", a.modelo || "—"], ["Conta", a.conta || "—"],
        ["Contexto utilizado", ctx], ["Missão atual", a.missao ? a.missao + (a.situacao ? " (" + a.situacao + (a.esperando ? " " + a.esperando : "") + ")" : "") : "Sem missão registrada"]];
      if (a.previsao) linhas.push(["Previsão", a.previsao]);
      if (a.atividade) linhas.push(["Agora", a.atividade.length > 160 ? a.atividade.slice(0, 159) + "…" : a.atividade]);
      if (a.ultimaFala) linhas.push(["Última fala", a.ultimaFala.length > 220 ? a.ultimaFala.slice(0, 219) + "…" : a.ultimaFala]);
      linhas.push(["PR e integração contínua", prs]);
      if (a.leitura && a.leitura !== "ok") linhas.push(["Leitura", a.motivo || a.leitura]);
      return { titulo: a.nome, desc: "", linhas: linhas, nota: "" };
    }
    if (sel.tipo === "servico") {
      var s = servicosDe(d, agora).filter(function (x) { return x.id === sel.chave; })[0];
      if (!s) return { titulo: "Serviço", desc: "Este serviço não está mais na leitura do coletor.", linhas: [], nota: "" };
      return { titulo: s.nome, desc: s.detalhe, linhas: [["Estado", s.rotulo], ["Última verificação", s.hora === null ? "sem horário" : quando(s.hora, agora) + " (há " + duracao(agora - s.hora) + ")"], ["Origem", "coletor local (" + (texto(d && d.origem) || "?") + ")"]],
        nota: s.bom ? "" : "Confira se o serviço está em execução e se o endereço de verificação responde. Este painel só lê o estado: não inicia nem reinicia processos." };
    }
    var c = contasDe(d).filter(function (x) { return x.id === sel.chave; })[0];
    if (!c) return { titulo: "Conta", desc: "Esta conta não está mais na leitura do coletor.", linhas: [], nota: "" };
    var ls = [["Conta", c.email || "Não identificada"], ["Plano", c.plano ? capitalizar(c.plano) : "—"], ["Situação", c.situacao || "—"], ["Última leitura", c.lidoEm ? quando(c.lidoEm, agora) : "sem leitura"]];
    c.barras.forEach(function (b) { ls.push([rotuloBarra(b), b.pct === null ? "Sem dados" : Math.round(b.pct) + "% · " + textoReset(b, agora)]); });
    return { titulo: c.ferramentaNome, desc: "", linhas: ls, nota: c.erro };
  }
  function desenharModal() {
    var mod = modeloDoModal();
    if (!mod) return;
    setTxt($("modal-title"), m(mod.titulo));
    desenharRegiao(doc, $("modal-body"), { mod: mod, priv: estado.privado }, function () {
      var out = [];
      if (mod.desc) out.push(el(doc, "p", "modal-description", m(mod.desc)));
      if (mod.linhas.length) {
        var dl = el(doc, "dl", "detail-list");
        mod.linhas.forEach(function (l) {
          var linha = el(doc, "div"), dd = el(doc, "dd", null, m(l[1]));
          dd.style.whiteSpace = "pre-line";
          linha.appendChild(el(doc, "dt", null, l[0])); linha.appendChild(dd); dl.appendChild(linha);
        });
        out.push(dl);
      }
      if (mod.nota) out.push(el(doc, "div", "detail-note", m(mod.nota)));
      return out;
    });
  }
  function abrirModal(sel, origem) {
    estado.modal = sel;
    focoAntesDoModal = origem || doc.activeElement;
    $("overlay").classList.add("open");
    desenharModal();
    desenharTudo();
    var caixa = doc.getElementById("modal-dialog");
    if (caixa && caixa.focus) caixa.focus();
  }
  function fecharModal() {
    if (!estado.modal) return;
    estado.modal = null;
    $("overlay").classList.remove("open");
    desenharTudo();
    if (focoAntesDoModal && focoAntesDoModal.focus) focoAntesDoModal.focus();
  }

  /* ---- desenho geral ---- */
  /* Cada seção é desenhada isolada: se uma falha, ela mostra um aviso discreto e as outras seguem. */
  function avisoDaSecao(id, falhou) {
    var host = $(id);
    if (!host) return;
    var aviso = host._avisoSecao;
    if (!falhou) { if (aviso && aviso.parentNode) aviso.parentNode.removeChild(aviso); return; }
    if (!aviso) { aviso = host._avisoSecao = el(doc, "p", "section-error", "não foi possível desenhar esta seção"); aviso.setAttribute("role", "status"); }
    if (aviso.parentNode !== host) host.insertBefore(aviso, host.children[0] || null);
  }
  function secao(id, nome, desenhar) {
    try { desenhar(); avisoDaSecao(id, false); }
    catch (e) { registrarErro("a seção " + nome, e); try { avisoDaSecao(id, true); } catch (e2) { registrarErro("o aviso da seção " + nome, e2); } }
  }
  function modeloUmaVez(calcular) {      // calcula na primeira vez que a seção pede; se falhar, falha para quem usar
    var pronto = false, valor, erro = null;
    return function () {
      if (!pronto) { pronto = true; try { valor = calcular(); } catch (e) { erro = e; } }
      if (erro) throw erro;
      return valor;
    };
  }
  function desenharTudo() {
    var d = estado.dados, agora = agoraS();
    var agentes = modeloUmaVez(function () { return agentesDe(d); }), contas = modeloUmaVez(function () { return contasDe(d); });
    var servicos = modeloUmaVez(function () { return servicosDe(d, agora); });
    var gastos = modeloUmaVez(function () { return gastosDe(d, estado.janela, estado.agrupamento); });
    var locais = modeloUmaVez(function () { return cotasLocaisDe(d); });
    try { desenharChip(); } catch (e) { registrarErro("o indicador do topo", e); }
    secao("stats", "de resumo", function () { desenharKpis(agentes(), contas(), servicos(), gastos()); });
    secao("agents", "de agentes", function () { desenharAgentes(agentes()); });
    secao("presence", "de presença por processo", function () { desenharPresenca(presencaDe(d)); });
    secao("presence-linux", "de presença no WSL", function () { desenharPresencaLinux(presencaLinuxDe(d)); });
    secao("limits", "de limites de uso", function () {
      var l; try { l = locais(); } catch (e) { l = []; }          // o erro da lista local é registrado pela seção dela
      desenharLimites(unificarCodex(contas(), l).contas);
    });
    secao("limits-local", "de uso observado em arquivos locais", function () {
      var l = locais(), c; try { c = contas(); } catch (e) { c = []; }   // o erro das contas é registrado pela seção delas
      desenharCotasLocais(unificarCodex(c, l).locais);
    });
    secao("usage", "de gastos", function () { desenharGastos(gastos()); });
    secao("attention", "Precisa de você", function () { desenharPendencias(pendenciasDe(d)); });
    secao("services", "de serviços", function () { desenharServicos(servicos()); });
    secao("vigilante", "do Vigilante 2", function () { desenharVigilante(vigilanteDe(d)); });
    if (estado.modal) secao("modal-dialog", "da janela de detalhes", desenharModal);
  }
  function receber(d) {
    if (!d || typeof d !== "object" || !Array.isArray(d.agentes)) return;
    estado.dados = d; estado.recebidoEm = agoraS();
    desenharTudo();
  }
  function carregarDados() {
    if (!win.fetch) return Promise.resolve();
    return win.fetch("/dados.json", { cache: "no-store" }).then(function (r) { if (!r.ok) throw new Error("dados " + r.status); return r.json(); })
      .then(function (d) {
        try { receber(d); } catch (e) { registrarErro("dados recebidos por /dados.json", e); }
      }, function () { /* continua com o último dado; o chip mostra a idade */ });
  }

  /* ---- ações ---- */
  function mensagemDeFalha(status) {
    if (status === 503) return "Falha ao atualizar: o coletor está fora do ar (503).";
    if (status === 502) return "Falha ao atualizar: o coletor respondeu com erro (502).";
    if (status === 403) return "Falha ao atualizar: pedido recusado pelo servidor (403).";
    return status ? "Falha ao atualizar (HTTP " + status + ")." : "Falha ao atualizar: sem resposta do servidor.";
  }
  function atualizarAgora() {
    if (estado.atualizando) return;
    estado.atualizando = true;
    var botao = $("refresh-button"), rot = $("refresh-label");
    botao.disabled = true; setTxt(rot, "Atualizando");
    var giro = botao._giro || (botao._giro = botao.children[0]);
    if (giro && giro.classList) giro.classList.add("spin");
    var ctrl = win.AbortController ? new win.AbortController() : null;
    var corte = win.setTimeout(function () { if (ctrl) ctrl.abort(); }, TEMPO_REFRESH_MS);
    function fim() {
      win.clearTimeout(corte);
      estado.atualizando = false; botao.disabled = false; setTxt(rot, "Atualizar");
      if (giro && giro.classList) giro.classList.remove("spin");
    }
    return win.fetch("/refresh", { method: "POST", cache: "no-store", signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        if (!r.ok) { var e = new Error("refresh"); e.status = r.status; throw e; }
        notificar("Atualização pedida ao coletor. Os dados novos chegam em instantes.");
        win.setTimeout(carregarDados, 400);
      })
      .catch(function (e) { notificar(mensagemDeFalha(e && e.status), true); carregarDados(); })
      .then(fim);
  }
  function gerarExportacao() {
    return JSON.stringify(snapshotParaExportar(estado.dados, estado.privado, opcoes.agoraMs ? opcoes.agoraMs() : Date.now()), null, 2);
  }
  function exportar() {
    if (!estado.dados) { notificar("Sem dados para exportar ainda.", true); return; }
    var agora = new Date(opcoes.agoraMs ? opcoes.agoraMs() : Date.now());
    var nome = "hermes-painel-" + agora.getFullYear() + dois(agora.getMonth() + 1) + dois(agora.getDate()) + "-" + dois(agora.getHours()) + dois(agora.getMinutes()) + dois(agora.getSeconds()) + ".json";
    var blob = new win.Blob([gerarExportacao()], { type: "application/json;charset=utf-8" });
    var url = win.URL.createObjectURL(blob), a = doc.createElement("a");
    a.href = url; a.download = nome; doc.body.appendChild(a); a.click(); a.remove();
    win.setTimeout(function () { win.URL.revokeObjectURL(url); }, 1000);
    notificar("Dados exportados" + (estado.privado ? " com e-mails mascarados" : "") + ": " + nome);
  }
  function alternarPrivacidade() {
    estado.privado = !estado.privado; gravar(CHAVE_PRIVACIDADE, estado.privado ? "1" : "0");
    aplicarPreferencias(); desenharTudo();
    notificar(estado.privado ? "Privacidade ligada: e-mails e valores ocultos." : "Privacidade desligada.");
  }
  function alternarTema() {
    estado.tema = TEMAS[(TEMAS.indexOf(estado.tema) + 1) % TEMAS.length]; gravar(CHAVE_TEMA, estado.tema);
    aplicarPreferencias();
  }

  /* ---- navegação lateral ---- */
  function marcarSecao(id) {
    estado.secao = id;
    var nome = id;
    SECOES.forEach(function (s) { if (s[0] === id) nome = s[1]; });
    setTxt($("breadcrumb-current"), nome);
    $("nav").querySelectorAll("[data-nav]").forEach(function (a) {
      var ativa = a.getAttribute("data-nav") === id;
      setCls(a, "active", ativa);
      if (ativa) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function secaoVisivel() {
    var achada = SECOES[0][0], doc_ = doc.documentElement;
    SECOES.forEach(function (s) {
      var no = $(s[0]);
      if (no && no.getBoundingClientRect().top <= 140) achada = s[0];
    });
    if (win.innerHeight + (win.scrollY || 0) >= (doc_.scrollHeight || 0) - 4 && (win.scrollY || 0) > 0) achada = SECOES[SECOES.length - 1][0];
    return achada;
  }
  function fecharNav() {
    $("sidebar").classList.remove("open"); $("mobile-backdrop").classList.remove("open");
    $("menu-button").setAttribute("aria-expanded", "false");
  }

  function avisarArquivo() {
    var aviso = $("file-warning");
    if (!aviso) return;
    aviso.hidden = !comoArquivo;
    setTxt(aviso, comoArquivo ? AVISO_ARQUIVO : "");
  }
  function ligar() {
    var hidratar = function (raiz) {
      raiz.querySelectorAll("[data-icon]").forEach(function (no) {
        var s = icone(doc, no.getAttribute("data-icon"), "");
        if (no.className) { no.removeAttribute("data-icon"); no.appendChild(s); }   // o elemento com classe fica como moldura do ícone
        else no.replaceWith(s);
      });
    };
    hidratar(doc);
    aplicarPreferencias();
    if (win.matchMedia) {
      var mq = win.matchMedia("(prefers-color-scheme: light)");
      var trocou = function () { if (estado.tema === "auto") aplicarPreferencias(); };
      if (mq.addEventListener) mq.addEventListener("change", trocou); else if (mq.addListener) mq.addListener(trocou);
    }
    $("privacy-button").addEventListener("click", alternarPrivacidade);
    $("theme-button").addEventListener("click", alternarTema);
    $("refresh-button").addEventListener("click", atualizarAgora);
    $("export-button").addEventListener("click", exportar);
    $("close-modal").addEventListener("click", fecharModal);
    $("modal-ok").addEventListener("click", fecharModal);
    $("overlay").addEventListener("click", function (e) { if (e.target === $("overlay")) fecharModal(); });
    $("agent-search").addEventListener("input", function (e) { estado.busca = e.target.value; desenharTudo(); });
    doc.querySelectorAll("[data-agent-filter]").forEach(function (b) {
      b.addEventListener("click", function () {
        estado.filtroAgente = b.getAttribute("data-agent-filter");
        doc.querySelectorAll("[data-agent-filter]").forEach(function (o) {
          var ativo = o === b; setCls(o, "active", ativo); o.setAttribute("aria-pressed", String(ativo));
        });
        desenharTudo();
      });
    });
    $("period-select").addEventListener("change", function (e) { estado.janela = e.target.value; desenharTudo(); });
    $("group-select").addEventListener("change", function (e) { estado.agrupamento = e.target.value; desenharTudo(); });
    $("service-filter").addEventListener("change", function (e) { estado.filtroServico = e.target.value; desenharTudo(); });
    $("attention-foot-link").addEventListener("click", function () { estado.filtroServico = "atencao"; $("service-filter").value = "atencao"; desenharTudo(); });
    $("menu-button").addEventListener("click", function () {
      var abrir = !$("sidebar").classList.contains("open");
      $("sidebar").classList.toggle("open", abrir); $("mobile-backdrop").classList.toggle("open", abrir);
      $("menu-button").setAttribute("aria-expanded", String(abrir));
    });
    $("mobile-backdrop").addEventListener("click", fecharNav);
    $("nav").querySelectorAll("[data-nav]").forEach(function (a) {
      a.addEventListener("click", function () { marcarSecao(a.getAttribute("data-nav")); fecharNav(); });
    });
    var quadro = 0;
    win.addEventListener("scroll", function () {
      if (quadro) return;
      quadro = win.requestAnimationFrame(function () { quadro = 0; marcarSecao(secaoVisivel()); });
    });
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { if (estado.modal) fecharModal(); else fecharNav(); }
      if (e.key === "Tab" && estado.modal) {
        var focaveis = ["close-modal", "modal-ok"].map($);
        var ativo = doc.activeElement, primeiro = focaveis[0], ultimo = focaveis[focaveis.length - 1];
        if (e.shiftKey && (ativo === primeiro || ativo === $("modal-dialog"))) { e.preventDefault(); ultimo.focus(); }
        else if (!e.shiftKey && ativo === ultimo) { e.preventDefault(); primeiro.focus(); }
      }
    });
    marcarSecao("overview");
    avisarArquivo();
    desenharTudo();
  }
  function iniciarFluxo() {
    fecharTransporte = iniciarTransporte(win, receber, function (t) { estado.transporte = t; desenharChip(); }, carregarDados, LER_A_CADA_MS);
    win.setInterval(desenharChip, 1000);
    win.addEventListener("beforeunload", function () { if (fecharTransporte) fecharTransporte(); });
  }
  return { estado: estado, ligar: ligar, iniciarFluxo: iniciarFluxo, receber: receber, desenharTudo: desenharTudo, atualizarAgora: atualizarAgora, exportar: exportar,
           gerarExportacao: gerarExportacao, alternarPrivacidade: alternarPrivacidade, alternarTema: alternarTema, abrirModal: abrirModal, fecharModal: fecharModal, notificar: notificar,
           carregarDados: carregarDados, desenharChip: desenharChip };
}

var api = {
  numero: numero, mascararEmails: mascararEmails, snapshotParaExportar: snapshotParaExportar, haSegundos: haSegundos, duracao: duracao, quando: quando,
  resetEmPtBr: resetEmPtBr, textoReset: textoReset, dinheiro: dinheiro, fmtUsd: fmtUsd, fmtBrl: fmtBrl, fmtTokens: fmtTokens, maxBonito: maxBonito,
  agentesDe: agentesDe, contasDe: contasDe, consumoClaude5h: consumoClaude5h, gastosDe: gastosDe, servicosDe: servicosDe, pendenciasDe: pendenciasDe,
  fontesComProblema: fontesComProblema, presencaDe: presencaDe, presencaLinuxDe: presencaLinuxDe, cotasLocaisDe: cotasLocaisDe, unificarCodex: unificarCodex, vigilanteDe: vigilanteDe, filtrarAgentes: filtrarAgentes, estadoWhatsapp: estadoWhatsapp, iniciarTransporte: iniciarTransporte, criarApp: criarApp, ICONES: ICONES
};
if (typeof module !== "undefined" && module.exports) module.exports = api;
else {
  raiz.Hermes = api;
  var iniciar = function () { var app = criarApp(document, window); app.ligar(); app.iniciarFluxo(); raiz.__hermesApp = app; };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar); else iniciar();
}
})(typeof window !== "undefined" ? window : this);
