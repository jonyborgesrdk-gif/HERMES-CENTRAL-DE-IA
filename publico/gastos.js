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
function contagem(v) { return (numero(v) ? v : 0).toLocaleString("pt-BR"); }
function custo(dado) { return numero(dado.cost_usd) ? dado.cost_usd : 0; }
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
function grafico(doc, janela, chave, nome) {
  var bloco = elemento(doc, "section", null, "gastos-grafico"); bloco.id = "gastos-grafico-" + chave;
  bloco.appendChild(elemento(doc, "h3", nome + " · US$ por canal"));
  var canais = ordenarCanais(janela.origens || []);
  var maior = canais.reduce(function (maximo, dado) { return Math.max(maximo, custo(dado)); }, 0);
  var lista = bloco.appendChild(elemento(doc, "ol"));
  canais.forEach(function (dado) {
    var item = lista.appendChild(elemento(doc, "li"));
    item.appendChild(elemento(doc, "span", dado.source, "gastos-canal"));
    item.appendChild(elemento(doc, "span", "US$ " + custo(dado).toFixed(4) + assinatura(dado) +
      " · " + contagem(tokens(dado)) + " tokens", "gastos-valor"));
    var trilho = item.appendChild(elemento(doc, "div", null, "gastos-trilho"));
    trilho.ariaHidden = "true";
    var barra = trilho.appendChild(elemento(doc, "div", null, "gastos-barra"));
    barra.style.width = (maior > 0 ? Math.max(0, Math.min(100, custo(dado) / maior * 100)) : 0) + "%";
  });
  if (!canais.length) bloco.appendChild(elemento(doc, "p", "Sem sessões nesta janela."));
  return bloco;
}
function valores(doc, tr, dado) {
  dado = dado || {};
  tr.appendChild(elemento(doc, "td", contagem(dado.input_tokens)));
  tr.appendChild(elemento(doc, "td", contagem(dado.output_tokens)));
  var cache = (numero(dado.cache_read_tokens) ? dado.cache_read_tokens : 0) +
              (numero(dado.cache_write_tokens) ? dado.cache_write_tokens : 0);
  tr.appendChild(elemento(doc, "td", contagem(cache)));
  tr.appendChild(elemento(doc, "td", custo(dado).toFixed(4) + assinatura(dado)));
}
function gastos(doc, dado, meta, agoraS) {
  var c = elemento(doc, "section", null, "cartao"); c.id = "gastos-cartao";
  c.appendChild(elemento(doc, "div", null, "cabeca")).appendChild(elemento(doc, "h2", "Gastos"));
  var aviso = saude(meta, agoraS);
  if (aviso) c.appendChild(elemento(doc, "p", aviso, "infra-linha saude-erro"));
  if (!dado.hoje || !dado["7d"]) {
    c.appendChild(elemento(doc, "p", "sem leitura dos gastos do Hermes", "infra-linha"));
  } else {
    var graficos = c.appendChild(elemento(doc, "div", null, "gastos-graficos"));
    graficos.appendChild(grafico(doc, dado.hoje, "hoje", "Hoje"));
    graficos.appendChild(grafico(doc, dado["7d"], "7d", "7 dias"));
    var tabela = c.appendChild(elemento(doc, "div", null, "gastos-rolagem")).appendChild(elemento(doc, "table"));
    tabela.appendChild(elemento(doc, "caption", "Tokens e US$ por canal — hoje e últimos 7 dias"));
    var cabeca = tabela.appendChild(elemento(doc, "thead"));
    var grupos = cabeca.appendChild(elemento(doc, "tr"));
    var origem = grupos.appendChild(elemento(doc, "th", "Canal")); origem.rowSpan = 2; origem.scope = "col";
    ["Hoje", "7 dias"].forEach(function (nome) {
      var th = grupos.appendChild(elemento(doc, "th", nome)); th.colSpan = 4; th.scope = "colgroup";
    });
    var colunas = cabeca.appendChild(elemento(doc, "tr"));
    ["Entrada", "Saída", "Cache", "US$", "Entrada", "Saída", "Cache", "US$"].forEach(function (nome) {
      var th = colunas.appendChild(elemento(doc, "th", nome)); th.scope = "col";
    });
    var corpo = tabela.appendChild(elemento(doc, "tbody")), origens = new Set(), mapas = [];
    [dado.hoje, dado["7d"]].forEach(function (janela) {
      var mapa = new Map();
      (janela.origens || []).forEach(function (o) { origens.add(o.source); mapa.set(o.source, o); });
      mapas.push(mapa);
    });
    Array.from(origens).sort().forEach(function (nome) {
      var tr = corpo.appendChild(elemento(doc, "tr"));
      var th = tr.appendChild(elemento(doc, "th", nome)); th.scope = "row";
      mapas.forEach(function (mapa) { valores(doc, tr, mapa.get(nome)); });
    });
    var total = corpo.appendChild(elemento(doc, "tr", null, "gastos-total"));
    var rotulo = total.appendChild(elemento(doc, "th", "Total")); rotulo.scope = "row";
    valores(doc, total, dado.hoje.total); valores(doc, total, dado["7d"].total);
  }
  c.appendChild(elemento(doc, "p", "custo estimado pelo Hermes; não inclui Claude Code/Codex fora do Hermes", "infra-linha"));
  c.appendChild(elemento(doc, "p", "Barras em escala própria por janela. Tokens = entrada + saída + cache. Assinatura = inclui sessões com custo incluído na assinatura.", "infra-linha"));
  c.appendChild(elemento(doc, "p", "Cache = leitura + escrita. Janelas pela data de início da sessão; hoje desde 00:00 local.", "infra-linha"));
  return c;
}
function desenhar(dados, agoraS, doc) {
  doc = doc || raiz.document;
  ["whatsapp-cartao", "gastos-cartao"].forEach(function (id) {
    var anterior = doc.getElementById(id); if (anterior) anterior.remove();
  });
  var fontes = dados.fontes || {}, grade = doc.getElementById("servicos-grade"), rodape = doc.getElementById("rodape");
  if (tem(dados, "whatsapp") && grade) grade.appendChild(whatsapp(doc, dados.whatsapp || {}, fontes.whatsapp || {}, agoraS));
  if (tem(dados, "gastos_hermes") && rodape) rodape.parentNode.insertBefore(
    gastos(doc, dados.gastos_hermes || {}, fontes.gastos_hermes || {}, agoraS), rodape);
}
var api = { desenhar: desenhar };
if (typeof module !== "undefined" && module.exports) module.exports = api;
else raiz.__painelGastos = api;
})(typeof window !== "undefined" ? window : this);
