/* Cartões opt-in Hermes. Dados sempre como texto, nunca como HTML. */
(function (raiz) {
"use strict";

function numero(v) { return typeof v === "number" && isFinite(v); }
function tem(d, chave) { return Object.prototype.hasOwnProperty.call(d, chave); }
function elemento(doc, tag, texto, classe) {
  var e = doc.createElement(tag);
  if (texto != null) e.textContent = String(texto);
  if (classe) e.className = classe;
  return e;
}
function hora(valor) {
  var d = new Date(valor);
  return valor != null && !isNaN(d.getTime()) ? d.toLocaleString("pt-BR") : "sem horário";
}
function saude(meta, agoraS) {
  if (meta.desatualizado) return "dado retido" + (numero(meta.ultima_leitura_ok)
    ? " de há " + Math.max(0, Math.floor(agoraS - meta.ultima_leitura_ok)) + " s" : "");
  return meta.ok === false ? meta.erro || "sem leitura" : "";
}
function whatsapp(doc, ponte, meta, agoraS) {
  var estado = "caído";
  var leitura = numero(meta.ultima_leitura_ok) ? meta.ultima_leitura_ok : meta.hora;
  var idade = numero(ponte.idade_s) ? ponte.idade_s + (numero(leitura) ? Math.max(0, agoraS - leitura) : 0) : null;
  if (ponte.estado === "ausente" || ponte.estado === "erro") estado = ponte.estado;
  else if (ponte.needs_attention || meta.desatualizado) estado = "atenção";
  else if (ponte.estado === "connected" && ponte.pid_vivo && ponte.gateway_state === "running") {
    estado = idade === null || idade > 120 ? "atenção" : "conectado";
  }
  var c = elemento(doc, "article", null, "servico"); c.id = "whatsapp-cartao";
  var titulo = c.appendChild(elemento(doc, "strong", "WhatsApp"));
  titulo.appendChild(elemento(doc, "span", estado, "saude-" + (estado === "conectado" ? "ok" : "erro")));
  var detalhe = [ponte.error_code, saude(meta, agoraS)].filter(function (v) { return v != null && v !== ""; });
  c.appendChild(elemento(doc, "p", detalhe.join(" · ") || "Ponte do gateway Hermes"));
  c.appendChild(elemento(doc, "small", "Última atualização: " + hora(ponte.updated_at) +
    " · estado do arquivo: " + (idade === null ? "sem horário" : "há " + Math.floor(idade) + " s")));
  return c;
}
var cot = null;
function contagem(v) { return (numero(v) ? v : 0).toLocaleString("pt-BR", { maximumFractionDigits: 0 }); }
function brl(usd) {
  var r = usd * cot.usd_brl;
  var opcoes = { style: "currency", currency: "BRL" };
  if (r > 0 && r < 0.01) opcoes.maximumFractionDigits = 4;
  return r.toLocaleString("pt-BR", opcoes);
}
// Texto de dinheiro: R$ principal (US$ entre parênteses) com cotação; senão o US$ de sempre.
function dinheiro(usd) {
  return cot ? brl(usd) + " (US$ " + usd.toFixed(4) + ")" : "US$ " + usd.toFixed(4);
}
function linhaCotacao(c) {
  var data = numero(c.data_epoch) ? new Date(c.data_epoch * 1000) : null;
  var quando = data && !isNaN(data.getTime()) ? data.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "sem horário";
  return "Cotação US$ 1 = " + c.usd_brl.toLocaleString("pt-BR", { style: "currency", currency: "BRL" }) +
    " (" + (typeof c.fonte === "string" ? c.fonte : "desconhecida") + ", " + quando + ")" +
    (c.desatualizada === true ? " · desatualizada" : "");
}
function custo(dado) { return numero(dado.cost_usd) && dado.cost_usd > 0 ? dado.cost_usd : 0; }
function assinatura(dado) {
  return dado.inclui_assinatura === true ? (custo(dado) === 0 ? " · assinatura" : " · inclui assinatura") : "";
}
function tokens(dado) {
  return ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"].reduce(function (soma, campo) {
    return soma + (numero(dado[campo]) ? dado[campo] : 0);
  }, 0);
}
function ordenarCanais(origens) {
  // Não modificar o snapshot. Índice explícito mantém empates na ordem recebida.
  return origens.map(function (dado, indice) { return { dado: dado, indice: indice }; }).sort(function (a, b) {
    var diferenca = custo(b.dado) - custo(a.dado);
    if (diferenca) return diferenca;
    if (custo(a.dado) === 0) {
      var assinaturaA = a.dado.inclui_assinatura === true ? 1 : 0;
      var assinaturaB = b.dado.inclui_assinatura === true ? 1 : 0;
      if (assinaturaA !== assinaturaB) return assinaturaA - assinaturaB;
    }
    return a.indice - b.indice;
  }).map(function (item) { return item.dado; });
}
function valores(doc, tr, dado) {
  dado = dado || {};
  tr.appendChild(elemento(doc, "td", contagem(dado.input_tokens)));
  tr.appendChild(elemento(doc, "td", contagem(dado.output_tokens)));
  var cache = (numero(dado.cache_read_tokens) ? dado.cache_read_tokens : 0) +
              (numero(dado.cache_write_tokens) ? dado.cache_write_tokens : 0);
  tr.appendChild(elemento(doc, "td", contagem(cache)));
  if (!cot) { tr.appendChild(elemento(doc, "td", custo(dado).toFixed(4) + assinatura(dado))); return; }
  var td = tr.appendChild(elemento(doc, "td", brl(custo(dado)) + assinatura(dado)));
  td.appendChild(elemento(doc, "span", "US$ " + custo(dado).toFixed(4), "gastos-usd"));
}
function estadoDe(doc) {
  var dono = doc.defaultView || doc;
  if (!dono.__gastosEstado) dono.__gastosEstado = { janela: "7d", agrupamento: "canal", filtro: "" };
  return dono.__gastosEstado;
}
function grupo(janela, agrupamento) { return janela[agrupamento === "modelo" ? "modelos" : "origens"] || []; }
function nomeGrupo(dado, agrupamento) { return String(agrupamento === "modelo" ? dado.model : dado.source); }
function serieGrupo(b, agrupamento) { return (agrupamento === "modelo" ? b.por_modelo : b.por_canal) || {}; }
function controle(doc, tag, id, classe) {
  var e = elemento(doc, tag, null, classe); e.id = id; if (tag === "button") e.type = "button"; return e;
}
function seletor(doc, id, opcoes, valor, aoMudar) {
  var e = controle(doc, "select", id, "gastos-seletor");
  opcoes.forEach(function (opcao) {
    var o = e.appendChild(elemento(doc, "option", opcao[1])); o.value = opcao[0];
    if (opcao[0] === valor) o.selected = true;
  });
  e.value = valor;
  e.addEventListener("change", function () { aoMudar(e.value); });
  return e;
}
function barra(doc, dado, maior, ativo, aoClicar) {
  var botao = elemento(doc, "button", null, "gastos-item" + (ativo ? " ativo" : ""));
  botao.id = "gastos-grupo-" + encodeURIComponent(dado.rotulo);
  botao.type = "button"; botao.ariaPressed = ativo ? "true" : "false";
  botao.title = dinheiro(custo(dado)) + assinatura(dado) + " · " + contagem(tokens(dado)) + " tokens";
  botao.addEventListener("click", aoClicar);
  botao.appendChild(elemento(doc, "span", dado.rotulo, "gastos-canal"));
  botao.appendChild(elemento(doc, "span", botao.title, "gastos-valor"));
  var trilho = botao.appendChild(elemento(doc, "div", null, "gastos-trilho")); trilho.ariaHidden = "true";
  var largura = maior > 0 ? Math.max(0, Math.min(100, custo(dado) / maior * 100)) : 0;
  trilho.appendChild(elemento(doc, "div", null, "gastos-barra")).style.width = largura + "%";
  return botao;
}
function colunas(doc, janela, agrupamento, filtro) {
  var bloco = elemento(doc, "section", null, "gastos-serie"); bloco.id = "gastos-colunas";
  bloco.appendChild(elemento(doc, "h3", (cot ? "R$" : "US$") + " por " + janela.granularidade));
  var serie = janela.serie || [];
  var maior = serie.reduce(function (m, b) {
    var par = filtro ? serieGrupo(b, agrupamento)[filtro] : b.total;
    return Math.max(m, custo({ cost_usd: par ? par[0] : 0 }));
  }, 0);
  var lista = bloco.appendChild(elemento(doc, "ol"));
  serie.forEach(function (b) {
    var valores = filtro ? serieGrupo(b, agrupamento)[filtro] || [0, 0] : b.total || [0, 0];
    var item = lista.appendChild(elemento(doc, "li"));
    var botao = item.appendChild(elemento(doc, "button", b.rotulo, "gastos-coluna")); botao.type = "button";
    botao.title = dinheiro(numero(valores[0]) && valores[0] > 0 ? valores[0] : 0) + " · " + contagem(valores[1]) + " tokens";
    var trilho = item.appendChild(elemento(doc, "div", null, "gastos-trilho")); trilho.ariaHidden = "true";
    trilho.appendChild(elemento(doc, "div", null, "gastos-barra")).style.width =
      (maior > 0 ? Math.max(0, Math.min(100, valores[0] / maior * 100)) : 0) + "%";
  });
  return bloco;
}
function tabela(doc, janela, agrupamento, filtro) {
  var tabela = elemento(doc, "table"); tabela.id = "gastos-tabela";
  tabela.appendChild(elemento(doc, "caption", "Tokens e " + (cot ? "R$" : "US$") + " — " + (agrupamento === "modelo" ? "modelo" : "canal")));
  var cabeca = tabela.appendChild(elemento(doc, "thead")).appendChild(elemento(doc, "tr"));
  ["", "Entrada", "Saída", "Cache", cot ? "R$" : "US$"].forEach(function (nome) { cabeca.appendChild(elemento(doc, "th", nome)).scope = "col"; });
  var linhas = grupo(janela, agrupamento).filter(function (d) { return !filtro || nomeGrupo(d, agrupamento) === filtro; });
  var corpo = tabela.appendChild(elemento(doc, "tbody"));
  linhas.forEach(function (d) {
    var tr = corpo.appendChild(elemento(doc, "tr"));
    tr.appendChild(elemento(doc, "th", nomeGrupo(d, agrupamento))).scope = "row";
    valores(doc, tr, d);
  });
  var total = corpo.appendChild(elemento(doc, "tr", null, "gastos-total"));
  total.appendChild(elemento(doc, "th", "Total")).scope = "row";
  valores(doc, total, filtro ? linhas[0] : janela.total);
  return tabela;
}
function gastos(doc, dado, meta, agoraS) {
  var bruta = dado.cotacao;
  cot = bruta && typeof bruta === "object" && numero(bruta.usd_brl) && bruta.usd_brl > 0 ? bruta : null;
  var c = elemento(doc, "section", null, "cartao"); c.id = "gastos-cartao";
  c.appendChild(elemento(doc, "div", null, "cabeca")).appendChild(elemento(doc, "h2", "Gastos"));
  var aviso = saude(meta, agoraS);
  if (aviso) c.appendChild(elemento(doc, "p", aviso, "infra-linha saude-erro"));
  var estado = estadoDe(doc);
  var janela = dado[estado.janela];
  if (!janela) {
    c.appendChild(elemento(doc, "p", "sem leitura dos gastos do Hermes", "infra-linha"));
  } else {
    var controles = c.appendChild(elemento(doc, "div", null, "gastos-controles"));
    controles.appendChild(seletor(doc, "gastos-janela", [["24h", "24 h"], ["hoje", "Hoje"], ["7d", "7 dias"], ["30d", "30 dias"]], estado.janela, function (valor) {
      estado.janela = valor; redesenhar();
    }));
    controles.appendChild(seletor(doc, "gastos-agrupamento", [["canal", "Canal"], ["modelo", "Modelo"]], estado.agrupamento, function (valor) {
      estado.agrupamento = valor; estado.filtro = ""; redesenhar();
    }));
    var limpar = controles.appendChild(controle(doc, "button", "gastos-limpar", "gastos-limpar"));
    limpar.textContent = "limpar"; limpar.addEventListener("click", function () { estado.filtro = ""; redesenhar(); });
    var grupos = ordenarCanais(grupo(janela, estado.agrupamento)).map(function (d) {
      return Object.assign({}, d, { rotulo: nomeGrupo(d, estado.agrupamento) });
    });
    if (estado.filtro && !grupos.some(function (d) { return d.rotulo === estado.filtro; })) {
      estado.filtro = "";
    }
    var maior = grupos.reduce(function (m, d) { return Math.max(m, custo(d)); }, 0);
    var lista = c.appendChild(elemento(doc, "section", null, "gastos-grupos")); lista.id = "gastos-grupos";
    var ol = lista.appendChild(elemento(doc, "ol"));
    grupos.forEach(function (d) {
      ol.appendChild(barra(doc, d, maior, d.rotulo === estado.filtro, function () {
        estado.filtro = estado.filtro === d.rotulo ? "" : d.rotulo; redesenhar();
      }));
    });
    c.appendChild(colunas(doc, janela, estado.agrupamento, estado.filtro));
    c.appendChild(elemento(doc, "div", null, "gastos-rolagem")).appendChild(tabela(doc, janela, estado.agrupamento, estado.filtro));
    if (estado.filtro) c.appendChild(elemento(doc, "p", "filtro: " + estado.filtro, "infra-linha")).id = "gastos-filtro";
  }
  if (cot) c.appendChild(elemento(doc, "p", linhaCotacao(cot), "infra-linha")).id = "gastos-cotacao";
  c.appendChild(elemento(doc, "p", "custo estimado pelo Hermes; não inclui Claude Code/Codex fora do Hermes", "infra-linha"));
  if (dado.custo_modelo_rateado) c.appendChild(elemento(doc, "p", "custo por modelo é rateio aproximado", "infra-linha"));
  if (dado.distribuicao_aproximada) c.appendChild(elemento(doc, "p", "distribuição aproximada pelas mensagens", "infra-linha"));
  function redesenhar() {
    var foco = focoNoCartao(doc);
    var novo = gastos(doc, dado, meta, agoraS);
    c.replaceWith(novo);
    restaurarFoco(doc, foco);
  }
  return c;
}
// Id do elemento focado somente se ele estiver dentro do cartão de gastos.
function focoNoCartao(doc) {
  var ativo = doc.activeElement;
  if (!ativo || !ativo.id) return "";
  for (var e = ativo; e; e = e.parentNode) {
    if (e.id === "gastos-cartao") return ativo.id;
  }
  return "";
}
function restaurarFoco(doc, id) {
  var alvo = id && doc.getElementById(id);
  if (alvo && alvo.focus) alvo.focus();
}
function desenhar(dados, agoraS, doc) {
  doc = doc || raiz.document;
  var foco = focoNoCartao(doc);
  ["whatsapp-cartao", "gastos-cartao"].forEach(function (id) {
    var anterior = doc.getElementById(id); if (anterior) anterior.remove();
  });
  var fontes = dados.fontes || {}, grade = doc.getElementById("servicos-grade"), rodape = doc.getElementById("rodape");
  if (tem(dados, "whatsapp") && grade) grade.appendChild(whatsapp(doc, dados.whatsapp || {}, fontes.whatsapp || {}, agoraS));
  if (tem(dados, "gastos_hermes") && rodape) rodape.parentNode.insertBefore(
    gastos(doc, dados.gastos_hermes || {}, fontes.gastos_hermes || {}, agoraS), rodape);
  restaurarFoco(doc, foco);
}
var api = { desenhar: desenhar };
if (typeof module !== "undefined" && module.exports) module.exports = api;
else raiz.__painelGastos = api;
})(typeof window !== "undefined" ? window : this);
