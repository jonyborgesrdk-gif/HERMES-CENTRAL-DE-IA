'use strict';
/* A página principal (publico/index.html + hermes.js + hermes.css): sem dado fictício, ligada ao contrato do dados.json.
   Não há jsdom neste projeto: o teste monta um DOM mínimo a partir do próprio index.html, então id faltando na página quebra aqui. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const pub = (n) => path.join(__dirname, '..', '..', 'publico', n);
const ler = (n) => fs.readFileSync(pub(n), 'utf8');
const html = ler('index.html'), js = ler('hermes.js'), css = ler('hermes.css');
const Hermes = require('../../publico/hermes.js');
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'dados-hermes.json'), 'utf8'));
const T0 = fixture.gerado_em_epoch;
const EMAIL = 'segredo.usuario@example.com';
const nb = (s) => s.replace(/\u00a0/g, ' ');

/* ---------- DOM mínimo ---------- */
class Texto { constructor(t) { this.texto = t; this.parentNode = null; } get textContent() { return this.texto; } }
class El {
  constructor(doc, tag) {
    this.doc = doc; this.tag = tag; this.childNodes = []; this.parentNode = null; this.attrs = {}; this.className = '';
    this.listeners = {}; this.hidden = false; this.disabled = false; this.value = '';
    this.style = { setProperty(k, v) { this[k] = v; } };
    const self = this;
    this.classList = {
      contains: (n) => self.className.split(/\s+/).includes(n),
      add: (n) => { if (!self.classList.contains(n)) self.className = (self.className + ' ' + n).trim(); },
      remove: (n) => { self.className = self.className.split(/\s+/).filter((c) => c && c !== n).join(' '); },
      toggle: (n, f) => { const on = f === undefined ? !self.classList.contains(n) : !!f; on ? self.classList.add(n) : self.classList.remove(n); return on; }
    };
  }
  get children() { return this.childNodes.filter((n) => n instanceof El); }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(v) { this.childNodes.forEach((n) => { n.parentNode = null; }); this.childNodes = []; if (v !== '') this.appendChild(new Texto(String(v))); }
  set innerHTML(v) { throw new Error('innerHTML proibido'); }
  set outerHTML(v) { throw new Error('outerHTML proibido'); }
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); return n; }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    n.parentNode = this; if (i < 0) this.childNodes.push(n); else this.childNodes.splice(i, 0, n); return n;
  }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; } return n; }
  replaceChildren(...nos) { this.childNodes.forEach((n) => { n.parentNode = null; }); this.childNodes = []; nos.forEach((n) => this.appendChild(n)); }
  replaceWith(n) { const p = this.parentNode; p.insertBefore(n, this); p.removeChild(this); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) { if (k === 'class') this.className = String(v); else { this.attrs[k] = String(v); if (k === 'hidden') this.hidden = true; } }
  getAttribute(k) { return k === 'class' ? this.className : (k in this.attrs ? this.attrs[k] : null); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  focus() { this.doc.activeElement = this; }
  click() { const ev = { target: this, key: '', shiftKey: false, preventDefault() {} }; for (let n = this; n instanceof El; n = n.parentNode) (n.listeners.click || []).forEach((f) => f(ev)); }
  emit(t, ev) { const e = Object.assign({ target: this, key: '', shiftKey: false, preventDefault() {} }, ev); (this.listeners[t] || []).forEach((f) => f(e)); }
  getBoundingClientRect() { return { top: 0 }; }
  descendentes() { const r = []; (function w(n) { n.childNodes.forEach((c) => { if (c instanceof El) { r.push(c); w(c); } }); })(this); return r; }
  querySelectorAll(sel) {
    const m = /^\[([\w-]+)\]$/.exec(sel); assert.ok(m, 'seletor não suportado: ' + sel);
    return this.descendentes().filter((e) => m[1] in e.attrs);
  }
}
const VAZIOS = new Set(['meta', 'link', 'input', 'br', 'hr', 'img']);
function montarDocumento() {
  const doc = { ids: {}, listeners: {}, activeElement: null };
  doc.createElement = (t) => new El(doc, t);
  doc.createElementNS = (ns, t) => new El(doc, t);
  doc.createTextNode = (t) => new Texto(t);
  doc.getElementById = (id) => doc.ids[id] || null;
  doc.addEventListener = (t, f) => { (doc.listeners[t] = doc.listeners[t] || []).push(f); };
  doc.querySelectorAll = (s) => doc.documentElement.querySelectorAll(s);
  const raiz = new El(doc, '#raiz'), pilha = [raiz];
  const re = /<!--[\s\S]*?-->|<!doctype[^>]*>|<(\/?)([a-zA-Z][\w-]*)([^>]*)>|[^<]+/gi;
  let m;
  while ((m = re.exec(html))) {
    if (m[0].startsWith('<!')) continue;
    if (m[2] === undefined) { pilha.at(-1).appendChild(new Texto(m[0])); continue; }
    const tag = m[2].toLowerCase();
    if (m[1]) { if (!VAZIOS.has(tag)) pilha.pop(); continue; }
    const e = new El(doc, tag);
    const at = /([\w:-]+)(?:="([^"]*)")?/g; let a;
    while ((a = at.exec(m[3].replace(/\/\s*$/, '')))) e.setAttribute(a[1], a[2] === undefined ? '' : a[2]);
    if (e.attrs.id) doc.ids[e.attrs.id] = e;
    if ('disabled' in e.attrs) e.disabled = true;
    pilha.at(-1).appendChild(e);
    if (!VAZIOS.has(tag) && !/\/\s*$/.test(m[3])) pilha.push(e);
  }
  doc.documentElement = raiz.children.find((e) => e.tag === 'html');
  doc.body = doc.documentElement.children.find((e) => e.tag === 'body');
  return doc;
}
function montarJanela(extra = {}) {
  const memoria = {}, timers = [];
  const win = {
    localStorage: { getItem: (k) => (k in memoria ? memoria[k] : null), setItem: (k, v) => { memoria[k] = String(v); }, memoria },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    setTimeout: (f, ms) => { timers.push({ f, ms }); return timers.length; }, clearTimeout() {},
    setInterval: () => 1, clearInterval() {}, addEventListener() {}, requestAnimationFrame: (f) => { f(); return 1; },
    innerHeight: 800, scrollY: 0, location: { protocol: 'http:' }, timers,
    Blob: class { constructor(partes, op) { this.partes = partes; this.op = op; } },
    URL: { createObjectURL: (b) => { win.ultimoBlob = b; return 'blob:fake'; }, revokeObjectURL() {} },
    fetch: () => Promise.reject(new Error('sem rede')), AbortController: class { constructor() { this.signal = {}; } abort() {} }
  };
  return Object.assign(win, extra);
}
function app(opcoes = {}) {
  const doc = montarDocumento(), win = montarJanela(opcoes.win);
  const a = Hermes.criarApp(doc, win, { agora: () => opcoes.agora || T0 + 10, agoraMs: () => (opcoes.agora || T0 + 10) * 1000 });
  a.ligar();
  return { a, doc, win, txt: (id) => nb(doc.getElementById(id).textContent) };
}
function todoTexto(doc) {          // texto visível + valores de atributos: onde um e-mail poderia vazar
  const partes = [];
  doc.documentElement.descendentes().forEach((e) => { partes.push(e.childNodes.filter((n) => n instanceof Texto).map((n) => n.texto).join('')); Object.values(e.attrs).forEach((v) => partes.push(v)); });
  return nb(partes.join('\n'));
}
const clone = (o) => JSON.parse(JSON.stringify(o));

/* ---------- a página não tem nada de demonstração ---------- */
test('página nova: nenhum texto de demonstração nem array fictício', () => {
  const proibidos = ['Ambiente de demonstração', 'Dados de demonstração', 'dados de demonstração', 'Valores ilustrativos', 'custo de exemplo', 'Verificação de exemplo',
    'Processos de exemplo', 'Prévia em HTML', 'Dados fictícios', 'Baixar painel HTML', 'Workspace pessoal', 'Jony', 'Simular verificação', 'dados de exemplo', 'Demonstração'];
  [['index.html', html], ['hermes.js', js], ['hermes.css', css]].forEach(([nome, fonte]) => {
    proibidos.forEach((p) => assert.ok(!fonte.includes(p), nome + ' contém "' + p + '"'));
  });
  assert.doesNotMatch(js, /\bconst\s+(agents|services|rows|eventData|periods)\s*=/);
  assert.doesNotMatch(js, /\b(eventData|periods)\b/);
  assert.doesNotMatch(html, /id="hermes-app"/);
  assert.doesNotMatch(html + js + css, /hermes\.exe × 2 · node\.exe × 1|Claude · Temp|Claude Pro · janela|246,6 milhões/);
});
test('página nova: nenhuma URL externa (só o namespace do SVG), sem CDN, fonte web nem imagem remota', () => {
  [['index.html', html], ['hermes.js', js], ['hermes.css', css]].forEach(([nome, fonte]) => {
    const resto = fonte.replace(/xmlns=['"]http:\/\/www\.w3\.org\/2000\/svg['"]/g, '').replace(/var SVGNS = "http:\/\/www\.w3\.org\/2000\/svg";/, '').replace('http://127.0.0.1:7421/', '');   // endereço local do próprio painel (aviso de file:), não é externo
    assert.doesNotMatch(resto, /https?:\/\//i, nome + ' tem URL absoluta');
    assert.doesNotMatch(resto, /@import|@font-face|\/\/cdn\.|\/\/fonts\./i, nome);
  });
  assert.doesNotMatch(html, /<img\b|<iframe\b|<object\b|<embed\b/i);
  assert.doesNotMatch(css, /url\((?!["']?data:image\/svg\+xml)/);
  assert.ok(/<script src="hermes\.js"><\/script>/.test(html) && !/<script>[\s\S]*?\S[\s\S]*?<\/script>/.test(html), 'só o script externo do mesmo diretório');
  assert.doesNotMatch(js, /\bfetch\(\s*["'](?!\/dados\.json|\/refresh)/);
  assert.doesNotMatch(js, /new EventSource\((?!"\/eventos")/);
});
test('hermes.js nunca monta HTML: sem innerHTML, outerHTML, insertAdjacentHTML, document.write, eval', () => {
  assert.doesNotMatch(js, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|setTimeout\(\s*["']/);
  assert.doesNotMatch(html, /\son\w+\s*=/i);
});
test('página nova: links entre as duas páginas', () => {
  assert.match(html, /href="\/classico"/);
  assert.match(ler('classico.html'), /<a id="link-painel" href="\/"/);
});

/* ---------- modelos puros ---------- */
test('dinheiro: R$ principal e US$ ao lado com cotação; só US$ sem ela', () => {
  assert.deepEqual(Object.assign({}, Hermes.dinheiro(18.5, { usdBrl: 5.5 })), { moeda: 'R$', valor: nb('101,75'), principal: 'R$ 101,75', secundario: 'US$ 18,50' });
  const so = Hermes.dinheiro(18.5, null);
  assert.equal(so.principal, 'US$ 18,50'); assert.equal(so.secundario, ''); assert.equal(so.moeda, 'US$');
  assert.equal(Hermes.fmtUsd(0.00123), 'US$ 0,0012');
  assert.equal(Hermes.fmtUsd(NaN), 'US$ 0,00');
  assert.equal(Hermes.fmtTokens(246600000), '246,6 milhões de tokens');
  assert.equal(Hermes.fmtTokens(55800), '55,8 mil tokens');
});
test('reset em pt-BR: epoch vira "hoje às", texto em inglês da tela vira dd/mm às HHhMM', () => {
  assert.equal(Hermes.resetEmPtBr('Oct 31, 8pm (America/Sao_Paulo)'), '31/10 às 20h00');
  assert.equal(Hermes.resetEmPtBr('Nov 1, 2:10am (America/Sao_Paulo)'), '01/11 às 02h10');
  assert.equal(Hermes.resetEmPtBr('12am'), 'às 00h00');
  assert.equal(Hermes.resetEmPtBr('lixo'), null);
  const agora = T0;
  assert.match(Hermes.textoReset({ renovaEpoch: agora + 3 * 3600 + 600, renovaTexto: '' }, agora), /^reinicia (hoje|amanhã) às \d\dh\d\d \(em 3 h 10 min\)$/);
  assert.equal(Hermes.textoReset({ renovaEpoch: null, renovaTexto: 'Oct 31, 8pm (America/Sao_Paulo)' }, agora), 'reinicia 31/10 às 20h00');
  assert.equal(Hermes.textoReset({ renovaEpoch: null, renovaTexto: '' }, agora), 'reset não informado');
  assert.match(Hermes.textoReset({ renovaEpoch: agora - 5, renovaTexto: '' }, agora), /já passou/);
});
test('gastosDe: janela, agrupamento, ordem por custo, sem zeros, assinatura por último, cotação', () => {
  const g = Hermes.gastosDe(fixture, '7d', 'canal');
  assert.equal(g.estado, 'ok'); assert.equal(g.usd, 18.5); assert.equal(g.cotacao.usdBrl, 5.5);
  assert.deepEqual(g.grupos.map((x) => x.nome), ['Terminal', 'WhatsApp', 'Ferramentas']);
  assert.equal(g.grupos[2].assinatura, true);
  assert.equal(g.serie.length, 7); assert.equal(g.serie[0].rotulo, '24/10');
  const modelos = Hermes.gastosDe(fixture, '7d', 'modelo');
  assert.deepEqual(modelos.grupos.map((x) => x.nome), ['modelo-alfa', 'modelo-beta']); assert.equal(modelos.grupos[1].sub, 'em segundo plano');
  assert.equal(Hermes.gastosDe(fixture, '24h', 'canal').serie[1].curto, '01h');
  assert.equal(Hermes.gastosDe({}, '7d', 'canal').estado, 'ausente');
  assert.equal(Hermes.gastosDe({ gastos_hermes: { erro: 'banco indisponível' } }, '7d', 'canal').estado, 'erro');
  assert.equal(Hermes.gastosDe({ gastos_hermes: {} }, '7d', 'canal').estado, 'sem_janela');
  const semCotacao = clone(fixture); delete semCotacao.gastos_hermes.cotacao;
  assert.equal(Hermes.gastosDe(semCotacao, '7d', 'canal').cotacao, null);
});
test('modelos tolerantes: dados vazios, tipos errados e campos ausentes não quebram', () => {
  [undefined, null, {}, { agentes: 'x', servicos: 3, contas: [], quadro: null, whatsapp: 'x', fontes: [] }].forEach((d) => {
    assert.ok(Array.isArray(Hermes.agentesDe(d))); assert.ok(Array.isArray(Hermes.contasDe(d)));
    assert.ok(Array.isArray(Hermes.servicosDe(d, T0))); assert.ok(Array.isArray(Hermes.pendenciasDe(d)));
    assert.ok(Array.isArray(Hermes.fontesComProblema(d)));
  });
  const a = Hermes.agentesDe({ agentes: [{ nome: 'x' }, { nome: 'x' }, null, 'lixo'] });
  assert.deepEqual(a.map((x) => x.chave), ['x', 'x#2']);
  assert.equal(a[0].estado, 'sem_leitura'); assert.equal(a[0].contexto, null);
});
test('serviços reais + WhatsApp; atenção = não ok', () => {
  const s = Hermes.servicosDe(fixture, T0 + 10);
  assert.deepEqual(s.map((x) => [x.nome, x.rotulo]), [['Hindsight', 'Disponível'], ['Ollama', 'Indisponível'], ['Vigia', 'Indisponível'], ['Hermes', 'Disponível'], ['WhatsApp', 'Conectado']]);
  assert.equal(s.filter((x) => !x.bom).length, 2);
  assert.equal(Hermes.servicosDe(fixture, T0 + 4000).at(-1).bom, false);       // leitura velha demais: atenção
});
test('WhatsApp: reconexão e estado desconhecido não significam caído nem disponível', () => {
  const base = { gateway_state: 'running', pid_vivo: true, idade_s: 5 };
  const casos = [['connecting', 'reconectando'], ['reconnecting', 'reconectando'], ['retrying', 'reconectando'], ['starting', 'reconectando'], ['desconhecido', 'desconhecido'], ['connected', 'conectado'], ['disconnected', 'caído']];
  for (const [estado, esperado] of casos) {
    const w = { ...base, estado };
    assert.equal(Hermes.estadoWhatsapp(w, {}, T0), esperado);
    const d = clone(fixture); d.whatsapp = w;
    const s = Hermes.servicosDe(d, T0).at(-1);
    assert.equal(s.bom, estado === 'connected');
    assert.equal(s.rotulo.toLowerCase(), esperado);
    const { a, doc, txt } = app(); a.receber(d);
    assert.match(txt('services-grid'), new RegExp(s.rotulo + 'WhatsApp'));
    assert.equal(txt('k-serv'), estado === 'connected' ? '3' : '2');
    const filtro = doc.getElementById('service-filter'); filtro.value = 'atencao'; filtro.emit('change');
    assert.equal(txt('services-grid').includes('WhatsApp'), estado !== 'connected');
    a.abrirModal({ tipo: 'servico', chave: 'whatsapp' });
    assert.ok(txt('modal-body').includes(s.rotulo));
    assert.equal(Hermes.estadoWhatsapp({ ...w, pid_vivo: false }, {}, T0), 'caído');
    assert.equal(Hermes.estadoWhatsapp({ ...w, gateway_state: 'stopped' }, {}, T0), 'caído');
    assert.equal(Hermes.estadoWhatsapp({ ...w, needs_attention: true }, {}, T0), 'atenção');
    assert.equal(Hermes.estadoWhatsapp(w, { desatualizado: true }, T0), 'atenção');
  }
  for (const estado of ['ausente', 'erro']) assert.equal(Hermes.estadoWhatsapp({ ...base, estado, needs_attention: true }, {}, T0), estado);
  assert.equal(Hermes.estadoWhatsapp({ ...base, estado: 'connected', idade_s: 121 }, {}, T0), 'atenção');
});
test('privacidade: máscara de e-mail igual à da página clássica', () => {
  assert.equal(Hermes.mascararEmails('a ' + EMAIL + ' b'), 'a se••••••@example.com b');
  assert.equal(Hermes.mascararEmails('ab@x.com'), '••••••@x.com');
  assert.equal(Hermes.mascararEmails(null), '');
  const snap = Hermes.snapshotParaExportar({ a: [EMAIL, { b: 'x ' + EMAIL }], n: 1 }, true, 0);
  assert.ok(!JSON.stringify(snap).includes(EMAIL)); assert.equal(snap.privacidade_ligada, true);
});

/* ---------- render ligado à fixture ---------- */
test('render: KPIs, tabela, limites, gastos, pendências e serviços mostram os valores da fixture', () => {
  const { a, doc, txt } = app();
  a.estado.privado = false; a.alternarPrivacidade(); a.alternarPrivacidade();      // liga e desliga: valores à mostra
  a.receber(clone(fixture));
  assert.equal(txt('k-trab'), '1'); assert.equal(txt('k-total'), '/ 3');
  assert.equal(txt('k-sessao'), '40'); assert.equal(txt('k-sessao-un'), '%'); assert.match(txt('k-sessao-texto'), /Claude Pro · janela de 5 horas/);
  assert.equal(txt('k-moeda'), 'R$'); assert.equal(txt('k-gasto'), '101,75'); assert.equal(txt('k-gasto-usd'), 'US$ 18,50 · ');
  assert.match(txt('k-gasto-texto'), /Últimos 7 dias/);
  assert.equal(txt('k-serv'), '3'); assert.equal(txt('k-serv-total'), '/ 5'); assert.match(txt('k-serv-texto'), /2 serviços precisam de atenção/);
  assert.equal(txt('nav-agentes'), '3'); assert.equal(txt('nav-servicos'), '2'); assert.equal(doc.getElementById('nav-servicos').hidden, false);
  assert.equal(txt('side-origem'), 'origem: vivo');
  const linhas = doc.getElementById('agent-rows').children;
  assert.equal(linhas.length, 3);
  assert.match(linhas[0].textContent, /Claude · projeto-alfa.*Claude Code · Opus 5\.5.*Trabalhando.*há 3m 26s.*58%.*Migrar o módulo de contas/);
  assert.match(linhas[1].textContent, /Codex · revisao.*Codex · gpt-6-astra high.*Ocioso.*desde 20h03/);
  assert.equal(txt('tab-todos'), '3'); assert.equal(txt('tab-ativos'), '2'); assert.equal(txt('tab-ociosos'), '1');
  const lim = txt('providers');
  assert.match(lim, /Claude.*Code.*Pro/); assert.match(lim, /Janela de 5 horas.*40%.*reinicia (hoje|amanhã) às \d\dh\d\d \(em 3 h 09 min\)/);
  assert.match(lim, /Limite semanal.*70%.*reinicia 31\/10 às 20h00/); assert.doesNotMatch(lim, /Codex/); assert.match(txt('limits-local-cards'), /Codex.*observado localmente.*OBSERVADO/);
  assert.equal(txt('cost-total'), 'R$101,75'); assert.match(txt('cost-tokens'), /US\$ 18,50 · 55,8 mil tokens/);
  assert.equal(doc.getElementById('chart').children.length, 3);
  assert.equal(doc.getElementById('chart').children[1].children.length, 7);
  const rotulos = txt('breakdown');
  assert.match(rotulos, /Terminal.*R\$ 88,00.*WhatsApp.*R\$ 13,75.*Ferramentas.*assinatura/); assert.ok(!/Vazio/.test(rotulos));
  assert.match(txt('cost-notes'), /Cotação US\$ 1 = R\$ 5,50 \(awesomeapi, 31\/10\/2026 \d\d:\d\d\).*não inclui Claude Code\/Codex fora do Hermes.*rateio aproximado.*aproximada pelas mensagens/);
  assert.equal(txt('attention-badge'), '2'); const aten = txt('attention-body');
  assert.match(aten, /RESPONDA.*Aprovar a migração.*Recomendo: Sim.*há 32 min.*FAÇA.*Renovar o token.*há 1 h 02 min/);
  assert.equal(doc.getElementById('services-grid').children.length, 5);
  assert.match(txt('services-grid'), /DisponívelHindsightresposta válida.*IndisponívelOllamasem resposta.*IndisponívelVigiapid 1823.*DisponívelHermesbatimento por mtime.*ConectadoWhatsApp/);
  assert.equal(txt('services-summary-text'), '3 disponíveis / 2 precisam de atenção');
  assert.equal(txt('services-processes'), 'Processos: hermes.exe × 2 · node.exe × 1');
  assert.match(txt('sources-foot'), /maestri_wire \(Wire indisponível\)/);
  assert.equal(doc.getElementById('attention-foot').hidden, false); assert.match(txt('attention-foot-text'), /2 serviços precisam de atenção/);
  assert.match(txt('live-text'), /^conectando… · atualizado há 10 s$/);
  assert.match(txt('updated-label'), /^Última leitura do coletor: 31\/10\/2026 \d\d:\d\d$/);
});
test('gráfico: valores positivos em texto, sem rótulos nas séries densas e sem HTML de dados', () => {
  for (const comCotacao of [true, false]) {
    const d = clone(fixture); if (!comCotacao) delete d.gastos_hermes.cotacao;
    const { a, doc } = app();
    d.gastos_hermes['7d'].serie = [
      { rotulo: '2026-10-24', total: [1, 1] },
      { rotulo: '2026-10-25', total: [0, 0] },
      { rotulo: '<img src=x onerror=alert(1)>', total: [10000, 1] }
    ];
    a.receber(d);
    const chart = doc.getElementById('chart');
    const valores = () => chart.descendentes().filter((e) => e.classList.contains('chart-value'));
    assert.equal(valores().length, 2);
    assert.equal(valores()[0].textContent, comCotacao ? 'R$ 5,50' : 'US$ 1,00');
    assert.match(valores()[1].textContent, /mil/);
    assert.ok(chart.classList.contains('sensitive'));
    valores().forEach((e) => { assert.equal(e.tag, 'span'); assert.equal(e.children.length, 0); assert.deepEqual(e.attrs, {}); });
    assert.equal(chart.descendentes().some((e) => e.tag === 'img'), false);
    for (const [janela, quantidade] of [['24h', 24], ['30d', 30]]) {
      d.gastos_hermes[janela].serie = Array.from({ length: quantidade }, (_, i) => ({ rotulo: String(i), total: [i + 1, 1] }));
      a.receber(d);
      const select = doc.getElementById('period-select'); select.value = janela; select.emit('change');
      assert.equal(chart.children[1].children.length, quantidade);
      assert.equal(valores().length, 0, 'série densa usa title, não amontoa rótulos');
      a.alternarPrivacidade();
      assert.ok(chart.children[1].children.every((b) => b.getAttribute('title')));
      a.alternarPrivacidade();
    }
  }
});
test('render: período e agrupamento mudam gastos, KPI e gráfico', () => {
  const { a, doc, txt } = app(); a.estado.privado = false; a.receber(clone(fixture));
  doc.getElementById('period-select').emit('change', { target: Object.assign(doc.getElementById('period-select'), { value: '30d' }) });
  assert.equal(txt('k-gasto'), '165,00'); assert.equal(txt('cost-total'), 'R$165,00'); assert.match(txt('k-gasto-texto'), /Últimos 30 dias/);
  assert.equal(doc.getElementById('chart').children[1].children.length, 2);
  doc.getElementById('period-select').emit('change', { target: Object.assign(doc.getElementById('period-select'), { value: '24h' }) });
  assert.equal(txt('k-gasto'), '19,25');
  assert.equal(doc.getElementById('chart').children[1].children.length, 4);
  doc.getElementById('period-select').emit('change', { target: Object.assign(doc.getElementById('period-select'), { value: '7d' }) });
  doc.getElementById('group-select').emit('change', { target: Object.assign(doc.getElementById('group-select'), { value: 'modelo' }) });
  assert.match(txt('breakdown'), /modelo-alfa.*R\$ 79,75.*modelo-beta · em segundo plano.*R\$ 22,00/);
});
test('render: sem cotação mostra só US$ e sem dados mostra "sem dados" honesto', () => {
  const { a, txt } = app(); a.estado.privado = false;
  assert.equal(txt('k-trab'), '—'); assert.equal(txt('k-gasto'), '—'); assert.equal(txt('k-serv'), '—'); assert.match(txt('k-sessao-texto'), /sem dados de cota do Claude/);
  assert.match(txt('agent-empty'), /Aguardando a primeira leitura/);
  const d = clone(fixture); delete d.gastos_hermes.cotacao; delete d.whatsapp; d.agentes = []; d.contas.lista = []; d.quadro.pendencias = []; d.servicos = [];
  a.receber(d);
  assert.equal(txt('k-moeda'), 'US$'); assert.equal(txt('k-gasto'), '18,50'); assert.equal(txt('k-gasto-usd'), '');
  assert.match(txt('agent-empty'), /Nenhum agente na leitura do coletor/); assert.match(txt('providers'), /Sem contas de uso/);
  assert.match(txt('attention-body'), /Tudo em dia por aqui\..*Nada esperando por você agora\./);
  assert.match(txt('k-sessao-texto'), /sem dados de cota do Claude/); assert.match(txt('services-empty'), /Nenhum serviço na leitura/);
  const sem = clone(fixture); delete sem.gastos_hermes;
  a.receber(sem);
  assert.equal(txt('k-gasto'), '—'); assert.match(txt('k-gasto-texto'), /sem dados de gastos/); assert.match(txt('breakdown'), /Sem dados: o coletor não publicou os gastos/);
});
test('seção Eventos: estado vazio honesto, sem evento inventado', () => {
  const { txt } = app();
  assert.match(txt('events'), /Sem histórico de eventos\./); assert.match(txt('events'), /sem fonte/);
  assert.doesNotMatch(txt('events'), /\d\d:\d\d:\d\d/);
});

test('contrato: a página só lê, na raiz do dados.json, campos que o coletor publica (os da fixture)', () => {
  const lidos = new Set(), raiz = clone(fixture);
  const proxy = new Proxy(raiz, { get(alvo, chave) { if (typeof chave === 'string') lidos.add(chave); return alvo[chave]; } });
  const { a } = app(); a.receber(proxy);
  a.alternarPrivacidade(); a.alternarPrivacidade();
  a.abrirModal({ tipo: 'agente', chave: 'sess-1' }); a.abrirModal({ tipo: 'agente', chave: 'sess-2' }); a.abrirModal({ tipo: 'servico', chave: 'whatsapp' }); a.abrirModal({ tipo: 'conta', chave: 'claude:abc' });
  const fora = [...lidos].filter((k) => !(k in fixture) && k !== 'toJSON');
  assert.deepEqual(fora, []);
  ['agentes', 'contas', 'servicos', 'processos', 'fontes', 'whatsapp', 'gastos_hermes', 'presenca_processos', 'presenca_linux', 'vigilante2', 'maquina', 'custo_agentes', 'erros_agentes', 'sessoes_grok', 'sessoes_hermes', 'chat_cursor', 'rede_uso', 'cota_codex_local', 'cota_grok_bot', 'cota_cursor', 'quadro', 'prs', 'origem', 'gerado_em_epoch'].forEach((k) => assert.ok(lidos.has(k), 'não leu ' + k));
});

/* ---------- segurança e privacidade ---------- */
test('dado com <img onerror> e <b> aparece como texto, nunca como elemento', () => {
  const { a, doc, txt } = app();
  const contar = () => { const c = {}; doc.documentElement.descendentes().forEach((e) => { c[e.tag] = (c[e.tag] || 0) + 1; }); return c; };
  const estaticos = contar();            // <b> do HTML estático (nome do coletor) já existe antes de qualquer dado
  a.receber(clone(fixture));
  const depois = contar();
  ['img', 'b', 'script', 'iframe'].forEach((t) => assert.equal(depois[t] || 0, estaticos[t] || 0, 'elemento <' + t + '> criado a partir de dado'));
  assert.match(txt('agent-rows'), /<img src=x onerror=alert\(1\)>/); assert.match(txt('agent-rows'), /<b>negrito<\/b>/);
  assert.match(txt('attention-body'), /<img src=x onerror=alert\(2\)>/);
  a.abrirModal({ tipo: 'agente', chave: 'sess-3' });
  assert.match(txt('modal-title'), /<img src=x onerror=alert\(1\)>/);
  assert.equal(contar().img || 0, 0);
  const atrs = doc.documentElement.descendentes().filter((e) => Object.keys(e.attrs).some((k) => /^on/.test(k)));
  assert.equal(atrs.length, 0);
});
test('privacidade ligada (padrão): e-mail mascarado na tela e nos atributos; desligada: visível', () => {
  const { a, doc, win } = app();
  assert.equal(a.estado.privado, true);
  a.receber(clone(fixture));
  let tudo = todoTexto(doc);
  assert.ok(!tudo.includes(EMAIL), 'e-mail vazou na tela'); assert.ok(!tudo.includes('segredo.usuario'));
  assert.ok(tudo.includes('se••••••@example.com'));
  a.abrirModal({ tipo: 'agente', chave: 'sess-3' });
  assert.ok(!todoTexto(doc).includes(EMAIL), 'e-mail vazou no modal');
  a.fecharModal();
  a.alternarPrivacidade();
  assert.equal(win.localStorage.memoria['painel-privacidade'], '0');
  assert.ok(todoTexto(doc).includes(EMAIL));
  assert.equal(doc.body.classList.contains('privacy-on'), false);
  a.alternarPrivacidade();
  assert.equal(win.localStorage.memoria['painel-privacidade'], '1');
  assert.ok(!todoTexto(doc).includes(EMAIL)); assert.equal(doc.body.classList.contains('privacy-on'), true);
});
test('privacidade: aviso local visível sem blur, botão revela valores e transfere foco ao período', () => {
  const { a, doc, win } = app();
  const aviso = doc.getElementById('cost-privacy-notice');
  assert.ok(aviso, 'falta aviso dentro de Gastos de IA');
  assert.equal(aviso.parentNode, doc.getElementById('usage'));
  assert.equal(aviso.parentNode.children[1], aviso, 'aviso deve vir logo após o cabeçalho');
  assert.match(aviso.textContent, /Valores ocultos pelo modo privacidade\./);
  assert.equal(aviso.hidden, false);
  for (let no = aviso; no; no = no.parentNode) assert.equal(no.classList.contains('sensitive'), false);
  const botao = doc.getElementById('cost-show-values');
  assert.equal(botao.tag, 'button'); assert.equal(botao.getAttribute('type'), 'button');
  assert.equal(botao.textContent, 'Mostrar valores');
  a.receber(clone(fixture)); botao.focus(); botao.click();
  assert.equal(a.estado.privado, false); assert.equal(aviso.hidden, true);
  assert.equal(doc.activeElement, doc.getElementById('period-select'));
  assert.equal(win.localStorage.memoria['painel-privacidade'], '0');
  const topo = doc.getElementById('privacy-button'); topo.focus(); topo.click();
  assert.equal(aviso.hidden, false); assert.equal(doc.activeElement, topo);
  const outro = app({ win: { localStorage: { getItem: (k) => k === 'painel-privacidade' ? '0' : null, setItem() {} } } });
  assert.equal(outro.doc.getElementById('cost-privacy-notice').hidden, true);
});
test('privacidade: valor do gráfico some do aria-label; preferência vem do localStorage compartilhado com a clássica', () => {
  const { a, doc } = app();
  a.receber(clone(fixture));
  const barras = doc.getElementById('chart').children[1].children;
  assert.match(barras[0].getAttribute('aria-label'), /^24\/10: valor oculto$/);
  a.alternarPrivacidade();
  assert.match(doc.getElementById('chart').children[1].children[0].getAttribute('aria-label'), /^24\/10: R\$ \d/);
  const outro = app({ win: { localStorage: { getItem: (k) => (k === 'painel-privacidade' ? '0' : k === 'painel-tema' ? 'escuro' : null), setItem() {} } } });
  assert.equal(outro.a.estado.privado, false); assert.equal(outro.a.estado.tema, 'escuro');
  assert.equal(outro.doc.documentElement.getAttribute('data-theme'), 'dark');
});
test('gráfico: title completo só sem privacidade; alternar remove valores de todos os atributos', () => {
  for (const comCotacao of [true, false]) {
    const d = clone(fixture); if (!comCotacao) delete d.gastos_hermes.cotacao;
    const { a, doc } = app(); a.receber(d);
    const barras = () => doc.getElementById('chart').children[1].children;
    const ocultas = () => barras().forEach((b) => {
      assert.equal(b.getAttribute('title'), null);
      assert.match(b.getAttribute('aria-label'), /: valor oculto$/);
      Object.values(b.attrs).forEach((v) => assert.doesNotMatch(v, /(?:R\$|US\$)\s*\d|tokens/));
    });
    ocultas(); a.alternarPrivacidade();
    const modelo = Hermes.gastosDe(d, '7d', 'canal');
    barras().forEach((b, i) => {
      const ponto = modelo.serie[i], valor = Hermes.dinheiro(ponto.usd, modelo.cotacao).principal;
      assert.equal(b.getAttribute('title'), ponto.rotulo + ': ' + valor + (comCotacao ? ' (' + Hermes.fmtUsd(ponto.usd) + ')' : ''));
    });
    barras()[0].focus(); a.alternarPrivacidade(); ocultas();
    assert.equal(doc.activeElement, barras()[0], 'foco da barra deve sobreviver à troca');
  }
});
test('exportar: baixa o snapshot atual em JSON; com privacidade, e-mail mascarado; sem, completo', () => {
  const { a, doc, win } = app();
  a.exportar();
  assert.match(nb(doc.getElementById('toast-text').textContent), /Sem dados para exportar/); assert.equal(doc.getElementById('toast').classList.contains('error'), true);
  a.receber(clone(fixture));
  a.exportar();
  const json1 = win.ultimoBlob.partes.join('');
  assert.equal(win.ultimoBlob.op.type, 'application/json;charset=utf-8');
  assert.ok(!json1.includes(EMAIL)); const s1 = JSON.parse(json1);
  assert.equal(s1.privacidade_ligada, true); assert.equal(s1.dados.contas.lista[0].email, 'se••••••@example.com'); assert.equal(s1.dados.gastos_hermes['7d'].total.cost_usd, 18.5);
  assert.match(nb(doc.getElementById('toast-text').textContent), /^Dados exportados com e-mails mascarados: hermes-painel-\d{8}-\d{6}\.json$/);
  a.alternarPrivacidade(); a.exportar();
  const s2 = JSON.parse(win.ultimoBlob.partes.join(''));
  assert.equal(s2.privacidade_ligada, false); assert.equal(s2.dados.contas.lista[0].email, EMAIL);
  assert.deepEqual(s2.dados.agentes.map((x) => x.nome), fixture.agentes.map((x) => x.nome));
});

/* ---------- interação: filtros, busca, seleção e foco sobrevivem às atualizações ---------- */
test('filtros Todos/Ativos/Ociosos e busca', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const abas = doc.querySelectorAll('[data-agent-filter]');
  const clicar = (nome) => abas.find((b) => b.attrs['data-agent-filter'] === nome).click();
  clicar('ativos'); assert.equal(doc.getElementById('agent-rows').children.length, 2);
  assert.equal(abas.find((b) => b.attrs['data-agent-filter'] === 'ativos').getAttribute('aria-pressed'), 'true');
  clicar('ociosos'); assert.equal(doc.getElementById('agent-rows').children.length, 1); assert.match(txt('agent-rows'), /Codex · revisao/);
  clicar('todos');
  const busca = doc.getElementById('agent-search');
  busca.emit('input', { target: Object.assign(busca, { value: 'GPT' }) });
  assert.equal(doc.getElementById('agent-rows').children.length, 1);
  busca.emit('input', { target: Object.assign(busca, { value: 'zzz' }) });
  assert.equal(doc.getElementById('agent-rows').children.length, 0); assert.equal(doc.getElementById('agent-empty').hidden, false); assert.match(txt('agent-empty'), /Nenhum agente encontrado/);
  busca.emit('input', { target: Object.assign(busca, { value: '' }) });
  clicar('ativos');
  const d = clone(fixture); d.agentes.forEach((x) => { x.estado = 'ocioso'; });
  a.receber(d); assert.match(txt('agent-empty'), /Nenhum agente em atividade agora/);
});
test('atualização ao vivo não perde busca, filtro, aba nem seleção do agente e reaproveita as linhas', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const busca = doc.getElementById('agent-search'), seletor = doc.getElementById('period-select');
  busca.focus(); busca.emit('input', { target: Object.assign(busca, { value: 'claude' }) });
  seletor.value = '30d'; seletor.emit('change', { target: seletor });
  doc.querySelectorAll('[data-agent-filter]').find((b) => b.attrs['data-agent-filter'] === 'ativos').click();
  const linhas = doc.getElementById('agent-rows').children;
  assert.equal(linhas.length, 2);
  const primeira = linhas[0], botao = primeira._r.btn;
  botao.focus();
  busca.focus();
  const d0 = clone(fixture); d0.gerado_em_epoch += 1; d0.agentes[0].contexto_pct = 57; a.receber(d0);
  assert.ok(doc.activeElement === busca, 'a busca perdeu o foco numa atualização');
  botao.focus(); const d1 = clone(fixture); d1.gerado_em_epoch += 2; d1.agentes[0].contexto_pct = 58; a.receber(d1);
  assert.ok(doc.activeElement === botao && doc.getElementById('agent-rows').children[0] === primeira, 'o botão da linha perdeu o foco numa atualização');
  botao.click();
  assert.ok(doc.activeElement === doc.getElementById('modal-dialog'), 'o foco deveria ir para a janela de detalhes');
  assert.equal(a.estado.modal.chave, 'sess-1'); assert.equal(primeira.classList.contains('selected'), true);
  assert.match(txt('modal-title'), /Claude · projeto-alfa/); assert.equal(doc.getElementById('overlay').classList.contains('open'), true);
  const antes = doc.getElementById('modal-body').childNodes[0];
  for (let i = 0; i < 3; i++) { const d = clone(fixture); d.gerado_em_epoch += i + 1; d.agentes[0].contexto_pct = 59 + i; a.receber(d); }
  assert.ok(doc.getElementById('agent-rows').children[0] === primeira, 'a linha foi recriada');
  assert.equal(primeira.classList.contains('selected'), true); assert.match(primeira.textContent, /61%/);
  assert.equal(busca.value, 'claude'); assert.equal(doc.getElementById('period-select').value, '30d'); assert.equal(a.estado.filtroAgente, 'ativos');
  assert.equal(doc.getElementById('agent-rows').children.length, 2);
  assert.match(txt('modal-body'), /Contexto utilizado61%/);
  assert.match(txt('modal-body'), /PR e integração contínuaPR meu-app #42|PR e integração contínua.*#42.*CI passou.*Migra contas/);
  assert.ok(antes !== doc.getElementById('modal-body').childNodes[0], 'o conteúdo do modal deveria refletir o dado novo');
  const sem = clone(fixture); sem.agentes = sem.agentes.slice(1);
  a.receber(sem);
  assert.match(txt('modal-body'), /não está mais na leitura/);
  a.fecharModal();
  assert.equal(a.estado.modal, null); assert.equal(doc.getElementById('overlay').classList.contains('open'), false);
  assert.ok(doc.activeElement === botao, 'o foco deveria voltar ao botão que abriu os detalhes');
});
test('Precisa de você: abrir passo a passo mantém o foco e o estado entre atualizações; crases viram <code>', () => {
  const { a, doc } = app(); a.estado.privado = false; a.receber(clone(fixture));
  const corpo = doc.getElementById('attention-body');
  const botao = corpo.descendentes().find((e) => e.tag === 'button');
  assert.equal(botao.getAttribute('aria-expanded'), 'false');
  botao.focus(); botao.click();
  const novo = corpo.descendentes().find((e) => e.tag === 'button');
  assert.equal(novo.getAttribute('aria-expanded'), 'true'); assert.ok(doc.activeElement === novo, 'o foco não voltou ao botão');
  assert.deepEqual(corpo.descendentes().filter((e) => e.tag === 'code').map((e) => e.textContent), ['gh auth login']);
  assert.match(nb(corpo.textContent), /Destrava: o deploy.*Tempo: 5 min.*Abra gh auth login.*Cole o código de segredo\.usuario@example\.com.*Fonte: README\.md/);
  a.receber(Object.assign(clone(fixture), { gerado_em_epoch: T0 + 1 }));
  assert.equal(corpo.descendentes().find((e) => e.tag === 'button').getAttribute('aria-expanded'), 'true');
  assert.ok(doc.activeElement === corpo.descendentes().find((e) => e.tag === 'button'));
});
test('filtro de serviços e atalho do aviso', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const filtro = doc.getElementById('service-filter');
  filtro.emit('change', { target: Object.assign(filtro, { value: 'atencao' }) });
  assert.equal(doc.getElementById('services-grid').children.length, 2); assert.equal(txt('services-badge'), '2 serviços');
  filtro.emit('change', { target: Object.assign(filtro, { value: 'todos' }) });
  assert.equal(doc.getElementById('services-grid').children.length, 5);
  doc.getElementById('attention-foot-link').click();
  assert.equal(filtro.value, 'atencao'); assert.equal(doc.getElementById('services-grid').children.length, 2);
  const card = doc.getElementById('services-grid').children[0]; card.click();
  assert.match(txt('modal-title'), /Ollama/); assert.match(txt('modal-body'), /EstadoIndisponível/);
});
test('modal da conta sem dados (Codex) usa os campos reais', () => {
  const { a, txt } = app(); a.receber(clone(fixture));
  a.abrirModal({ tipo: 'conta', chave: 'codex:def' });
  assert.match(txt('modal-title'), /Codex/); assert.match(txt('modal-body'), /ContaNão identificada.*Situaçãosem dado.*Janela de 5 horasSem dados.*Limite semanalSem dados/);
});

/* ---------- tema e privacidade persistidos, atualizar, transporte ---------- */
test('tema: automático -> claro -> escuro, salvo em localStorage e aplicado em data-theme', () => {
  const { a, doc, win } = app();
  assert.equal(a.estado.tema, 'auto'); assert.equal(doc.documentElement.getAttribute('data-theme'), 'dark');
  a.alternarTema(); assert.equal(win.localStorage.memoria['painel-tema'], 'claro'); assert.equal(doc.documentElement.getAttribute('data-theme'), 'light');
  a.alternarTema(); assert.equal(win.localStorage.memoria['painel-tema'], 'escuro'); assert.equal(doc.documentElement.getAttribute('data-theme'), 'dark');
  a.alternarTema(); assert.equal(win.localStorage.memoria['painel-tema'], 'auto');
  assert.match(doc.getElementById('theme-button').getAttribute('aria-label'), /Tema: automático/);
  const claro = app({ win: { matchMedia: () => ({ matches: true, addEventListener() {} }) } });
  assert.equal(claro.doc.documentElement.getAttribute('data-theme'), 'light');
});
test('Atualizar: POST /refresh, sucesso e falha ficam visíveis, botão volta ao normal', async () => {
  const chamadas = [];
  const mk = (resposta) => app({ win: { fetch: (url, op) => { chamadas.push([url, op && op.method]); return url === '/refresh' ? resposta() : Promise.resolve({ ok: true, json: () => Promise.resolve(clone(fixture)) }); } } });
  let t = mk(() => Promise.resolve({ ok: true, status: 204 }));
  const p = t.a.atualizarAgora();
  assert.equal(t.doc.getElementById('refresh-button').disabled, true); assert.equal(t.txt('refresh-label'), 'Atualizando');
  await p;
  assert.deepEqual(chamadas[0], ['/refresh', 'POST']);
  assert.match(t.txt('toast-text'), /Atualização pedida ao coletor/); assert.equal(t.doc.getElementById('toast').classList.contains('error'), false);
  assert.equal(t.doc.getElementById('toast').classList.contains('show'), true);
  assert.equal(t.doc.getElementById('refresh-button').disabled, false); assert.equal(t.txt('refresh-label'), 'Atualizar');
  for (const [status, re] of [[503, /coletor está fora do ar \(503\)/], [502, /respondeu com erro \(502\)/], [403, /recusado.*\(403\)/]]) {
    t = mk(() => Promise.resolve({ ok: false, status })); await t.a.atualizarAgora();
    assert.match(t.txt('toast-text'), re); assert.equal(t.doc.getElementById('toast').classList.contains('error'), true);
    assert.equal(t.doc.getElementById('refresh-button').disabled, false);
  }
  t = mk(() => Promise.reject(new Error('rede'))); await t.a.atualizarAgora();
  assert.match(t.txt('toast-text'), /sem resposta do servidor/);
});
test('chip do topo: estado real da conexão e idade da última atualização', () => {
  const { a, doc, txt } = app({ agora: T0 + 5 });
  const chip = doc.getElementById('live-chip');
  assert.equal(txt('live-text'), 'conectando… · sem dados');
  a.estado.transporte = 'ao vivo'; a.receber(clone(fixture));
  assert.equal(txt('live-text'), 'ao vivo · atualizado há 5 s'); assert.equal(chip.classList.contains('ok'), true);
  a.estado.transporte = 'reconectando'; a.desenharChip();
  assert.equal(txt('live-text'), 'reconectando · atualizado há 5 s'); assert.equal(chip.classList.contains('ok'), false); assert.equal(chip.classList.contains('warn'), true);
  a.estado.transporte = 'desconectado'; a.desenharChip();
  assert.equal(txt('live-text'), 'desconectado · atualizado há 5 s'); assert.equal(chip.classList.contains('off'), true);
  const velho = app({ agora: T0 + 300 }); velho.a.estado.transporte = 'ao vivo'; velho.a.receber(clone(fixture));
  assert.equal(velho.txt('live-text'), 'ao vivo · atualizado há 5 min 00 s'); assert.equal(velho.doc.getElementById('live-chip').classList.contains('warn'), true);
  assert.match(velho.doc.getElementById('live-chip').getAttribute('title'), /Dados velhos/);
});
test('transporte: SSE em /eventos, fallback de polling se cair, reconexão e fechamento', () => {
  function amb(protocol = 'http:', sse = true) {
    const a = { location: { protocol }, fontes: [], timers: new Set(), carregou: 0, estados: [], dados: [] };
    a.setInterval = (fn) => { const id = { fn }; a.timers.add(id); return id; }; a.clearInterval = (id) => a.timers.delete(id);
    if (sse) a.EventSource = class { constructor(u) { this.url = u; this.readyState = 0; this.l = {}; a.fontes.push(this); } addEventListener(n, f) { this.l[n] = f; } close() { this.fechado = true; } };
    a.fechar = Hermes.iniciarTransporte(a, (d) => a.dados.push(d), (e) => a.estados.push(e), () => { a.carregou++; }, 5000);
    return a;
  }
  const a = amb();
  assert.equal(a.fontes[0].url, '/eventos'); assert.equal(a.timers.size, 0);
  a.fontes[0].onopen(); assert.equal(a.estados.at(-1), 'ao vivo');
  a.fontes[0].l.dados({ data: '{"origem":"vivo","agentes":[]}' }); assert.equal(a.dados[0].origem, 'vivo');
  a.fontes[0].l.dados({ data: 'lixo{' }); assert.equal(a.dados.length, 1);
  a.fontes[0].readyState = 0; a.fontes[0].onerror();
  assert.equal(a.estados.at(-1), 'reconectando'); assert.equal(a.timers.size, 1); assert.equal(a.carregou, 1);
  a.fontes[0].onerror(); assert.equal(a.timers.size, 1);
  a.fontes[0].readyState = 2; a.fontes[0].onerror(); assert.equal(a.estados.at(-1), 'desconectado');
  a.fontes[0].onopen(); assert.equal(a.timers.size, 0); assert.equal(a.estados.at(-1), 'ao vivo');
  a.fechar(); assert.equal(a.fontes[0].fechado, true);
  const f = amb('file:'); assert.equal(f.fontes.length, 0); assert.equal(f.timers.size, 1); assert.equal(f.estados.at(-1), 'desconectado');
  assert.equal(amb('http:', false).timers.size, 1);
});
test('navegação lateral: marca a seção ativa e atualiza o título', () => {
  const { doc, win } = app();
  const links = doc.getElementById('nav').querySelectorAll('[data-nav]');
  assert.deepEqual(links.map((l) => l.attrs['data-nav']), ['overview', 'agents', 'usage', 'services', 'events']);
  links.find((l) => l.attrs['data-nav'] === 'services').click();
  assert.equal(links.find((l) => l.attrs['data-nav'] === 'services').classList.contains('active'), true);
  assert.equal(links.find((l) => l.attrs['data-nav'] === 'overview').classList.contains('active'), false);
  assert.equal(doc.getElementById('breadcrumb-current').textContent, 'Serviços');
  for (const id of ['overview', 'agents', 'usage', 'services', 'events']) assert.ok(doc.getElementById(id), 'âncora ausente: ' + id);
  assert.ok(win);
});
test('ícones do HTML são hidratados sem perder a moldura com classe', () => {
  const { doc } = app();
  assert.equal(doc.documentElement.querySelectorAll('[data-icon]').length, 0);
  assert.equal(doc.getElementById('side-origem').parentNode.parentNode.children[0].children[0].tag, 'svg');
});

/* ---------- correções da revisão: file:, isolamento de seção, chaves do dado, privacidade em atributos ---------- */
function silenciarErros(fn) {
  const antes = console.error, registros = [];
  console.error = (...args) => { registros.push(args); };
  let r;
  try { r = fn(registros); } catch (e) { console.error = antes; throw e; }
  if (r && typeof r.then === 'function') return r.finally(() => { console.error = antes; });
  console.error = antes;
  return r;
}
const textosDeAtributos = (doc) => doc.documentElement.descendentes().flatMap((e) => ['title', 'aria-label'].map((k) => e.getAttribute(k)).filter((v) => v !== null));
test('protocolo file: mostra o aviso claro em vez de "desconectado · sem dados"', () => {
  const { a, doc, txt } = app({ win: { location: { protocol: 'file:' } } });
  a.iniciarFluxo();
  const aviso = doc.getElementById('file-warning');
  assert.equal(aviso.hidden, false);
  assert.equal(txt('file-warning'), 'Esta página precisa do servidor do painel. Abra por http://127.0.0.1:7421/ (ou use classico.html para abrir como arquivo).');
  assert.doesNotMatch(txt('live-text'), /desconectado|sem dados/); assert.match(txt('live-text'), /arquivo/);
  assert.equal(app().doc.getElementById('file-warning').hidden, true);          // por http o aviso fica escondido
});
test('payload com session_id e nome "constructor"/"__proto__" desenha tudo sem lançar', () => {
  const d = clone(fixture);
  d.agentes = [{ nome: 'constructor', session_id: 'constructor', estado: 'constructor' }, { nome: '__proto__', session_id: '__proto__', estado: '__proto__' },
    { nome: 'toString', session_id: 'toString' }, { nome: 'x', session_id: '__proto__' }];
  d.servicos = [{ nome: 'constructor', estado: 'ok' }, { nome: '__proto__', estado: 'fora' }];
  d.quadro = { pendencias: [{ id: '__proto__', tipo: 'pergunta', texto: 'pendência __proto__', passos: ['p1'] }, { id: 'constructor', texto: 'pendência constructor' }] };
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    a.receber(clone(d));                                         // segunda leitura reaproveita o mapa de linhas
    assert.equal(erros.length, 0, 'seção falhou: ' + (erros[0] && String(erros[0][1])));
  });
  assert.equal(doc.getElementById('agent-rows').children.length, 4);
  assert.equal(new Set(Hermes.agentesDe(d).map((x) => x.chave)).size, 4);
  assert.match(txt('agent-rows'), /constructor/); assert.match(txt('agent-rows'), /__proto__/);
  assert.equal(Hermes.agentesDe(d)[0].rotuloEstado, 'constructor');                 // não herda Object.prototype.constructor
  assert.equal(typeof Hermes.servicosDe(d, T0)[0].icone, 'string');
  assert.match(txt('services-grid'), /constructor/); assert.match(txt('attention-body'), /pendência __proto__/);
  assert.match(txt('providers'), /\S/);                                              // seções depois dos agentes desenharam
  assert.equal(doc.getElementById('agents')._avisoSecao, undefined);
  a.abrirModal({ tipo: 'agente', chave: '__proto__' }); assert.match(txt('modal-title'), /__proto__/);
  assert.equal(JSON.parse(a.gerarExportacao()).dados.agentes.length, 4);
});
test('uma seção forçada a lançar mostra aviso discreto e não impede as outras', () => {
  const d = clone(fixture);
  Object.defineProperty(d.quadro, 'pendencias', { enumerable: true, get() { throw new Error('boom pendências'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /Precisa de você/); assert.match(String(erros[0][1]), /boom pendências/);
  });
  assert.match(txt('attention'), /não foi possível desenhar esta seção/);
  assert.equal(doc.getElementById('agent-rows').children.length, d.agentes.length);
  assert.match(txt('services-grid'), /Hindsight/); assert.match(txt('providers'), /\S/); assert.match(txt('cost-total'), /\d/);
  silenciarErros(() => a.receber(clone(fixture)));                       // na próxima leitura boa o aviso some
  assert.doesNotMatch(txt('attention'), /não foi possível desenhar/);
});
test('falha em um modelo (gastos) derruba só as seções que o usam; erro no SSE e no polling é registrado e a página segue viva', async () => {
  const ruim = clone(fixture);
  Object.defineProperty(ruim.gastos_hermes, 'cotacao', { enumerable: true, get() { throw new Error('boom gastos'); } });
  const { a, doc, txt } = app({ win: { fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(ruim) }) } });
  await silenciarErros(async (erros) => {
    await a.carregarDados();                                            // o then do polling não rejeita
    assert.ok(erros.some((e) => /boom gastos/.test(String(e[1]))), 'erro não registrado');
  });
  assert.match(txt('usage'), /não foi possível desenhar/);
  assert.equal(doc.getElementById('agent-rows').children.length, ruim.agentes.length);
  assert.match(txt('services-grid'), /Hindsight/);
  // SSE: receber que lança é registrado, JSON ilegível é ignorado e o próximo evento ainda chega
  const fontes = [], chamadas = [];
  const amb = { location: { protocol: 'http:' }, setInterval: () => 1, clearInterval() {}, EventSource: class { constructor() { this.readyState = 0; this.l = {}; fontes.push(this); } addEventListener(n, f) { this.l[n] = f; } close() {} } };
  Hermes.iniciarTransporte(amb, (x) => { chamadas.push(x); if (x.quebra) throw new Error('boom receber'); }, () => {}, () => {}, 5000);
  silenciarErros((erros) => {
    assert.doesNotThrow(() => fontes[0].l.dados({ data: '{"quebra":true}' }));
    assert.equal(erros.length, 1); assert.match(String(erros[0][1]), /boom receber/);
    fontes[0].l.dados({ data: 'lixo{' }); assert.equal(erros.length, 1);
    fontes[0].l.dados({ data: '{"ok":1}' }); assert.equal(chamadas.length, 2);
  });
});
test('privacidade ligada: nenhum title/aria-label leva valor monetário ou e-mail (com e sem cotação, por canal e por modelo)', () => {
  const dinheiroOuEmail = /(R\$|US\$)\s*\d|\d+[.,]\d{2}\b|[\w.+-]+@[\w-]+\.\w+/;
  const semCotacao = clone(fixture); delete semCotacao.gastos_hermes.cotacao;
  [fixture, semCotacao].forEach((base) => {
    ['canal', 'modelo'].forEach((agrupamento) => {
      const { a, doc } = app();
      a.receber(clone(base)); a.estado.agrupamento = agrupamento; a.desenharTudo();
      a.abrirModal({ tipo: 'agente', chave: 'sess-3' });
      assert.equal(a.estado.privado, true);
      const lidos = textosDeAtributos(doc);
      assert.ok(lidos.length > 5);
      lidos.forEach((v) => assert.doesNotMatch(v, dinheiroOuEmail, 'atributo expõe valor: ' + v));
      assert.ok(!doc.getElementById('breakdown').descendentes().some((e) => e.getAttribute('title') !== null), 'title na distribuição com privacidade ligada');
      a.fecharModal();
    });
  });
  // desligar a privacidade mostra o title; ligar de novo tira (a região é refeita ao alternar)
  const { a, doc } = app();
  a.receber(clone(fixture));
  a.alternarPrivacidade();
  assert.ok(doc.getElementById('breakdown').descendentes().some((e) => /US\$ \d/.test(e.getAttribute('title') || '')));
  a.alternarPrivacidade();
  assert.ok(!doc.getElementById('breakdown').descendentes().some((e) => e.getAttribute('title') !== null));
});

/* ---------- E16: presença por processo (aditiva; não muda os elementos que já existem) ---------- */
const linhasPresenca = (doc) => doc.getElementById('presence-rows').children;
const detalhePresenca = (tr) => nb(tr.descendentes().find((e) => e.tag === 'small').textContent);
test('presencaDe: ausente/erro = inativa; válida soma instâncias; inválidos descartados; repetido fica o primeiro; família e tipo desconhecidos viram "outra"', () => {
  const inativa = { ativa: false, itens: [], total: 0 };
  [undefined, null, {}, { presenca_processos: null }, { presenca_processos: 'ok' }, { presenca_processos: [] }, { presenca_processos: { estado: 'erro' } },
   { presenca_processos: { itens: clone(fixture.presenca_processos.itens) } }].forEach((d) => assert.deepEqual(Hermes.presencaDe(d), inativa));
  const p = Hermes.presencaDe(fixture);
  assert.equal(p.ativa, true); assert.equal(p.total, 3);
  assert.deepEqual(p.itens.map((i) => [i.produto, i.rotulo, i.familia, i.tipo, i.instancias]), [['claude_cli', 'Claude Code CLI', 'claude', 'cli', 2], ['grok_bot', 'Grok Bot', 'grok', 'app', 1]]);
  assert.deepEqual(Hermes.presencaDe({ presenca_processos: { estado: 'ok' } }), { ativa: true, itens: [], total: 0 });
  const sujo = Hermes.presencaDe({ presenca_processos: { estado: 'ok', itens: [
    { produto: 'sem_rotulo', familia: 'claude', tipo: 'cli', instancias: 1 },
    { produto: 'rotulo_vazio', rotulo: '', familia: 'claude', tipo: 'cli', instancias: 1 },
    { produto: 'rotulo_numero', rotulo: 7, familia: 'claude', tipo: 'cli', instancias: 1 },
    { produto: 'zero', rotulo: 'Zero', familia: 'claude', tipo: 'cli', instancias: 0 },
    { produto: 'negativo', rotulo: 'Negativo', familia: 'claude', tipo: 'cli', instancias: -1 },
    { produto: 'texto', rotulo: 'Texto', familia: 'claude', tipo: 'cli', instancias: '2' },
    { produto: 'fracao', rotulo: 'Fração', familia: 'claude', tipo: 'cli', instancias: 1.5 },
    { produto: 'nan', rotulo: 'NaN', familia: 'claude', tipo: 'cli', instancias: NaN },
    'lixo', null, 7, ['x'],
    { produto: '', rotulo: 'Produto vazio', instancias: 1 },
    { produto: 12, rotulo: 'Produto número', instancias: 1 },
    { produto: 'p'.repeat(41), rotulo: 'Produto longo', instancias: 1 },
    { produto: 'rotulo_longo', rotulo: 'r'.repeat(61), instancias: 1 },
    { produto: 'p'.repeat(40), rotulo: 'r'.repeat(60), familia: 'cursor', tipo: 'app', instancias: 1 },
    { produto: 'dup', rotulo: 'Primeiro', familia: 'codex', tipo: 'app', instancias: 2 },
    { produto: 'dup', rotulo: 'Segundo', familia: 'claude', tipo: 'cli', instancias: 9 },
    { produto: 'estranho', rotulo: 'Estranho', familia: 'gemini', tipo: 'daemon', instancias: 1 },
    { produto: 'sem_familia', rotulo: 'Sem família', instancias: 3 },
    { produto: '__proto__', rotulo: 'Proto', familia: 'constructor', tipo: 'toString', instancias: 1 }
  ] } });
  assert.equal(sujo.ativa, true);
  assert.deepEqual(sujo.itens.map((i) => [i.produto, i.rotulo, i.familia, i.tipo, i.instancias]), [
    ['p'.repeat(40), 'r'.repeat(60), 'cursor', 'app', 1], ['dup', 'Primeiro', 'codex', 'app', 2], ['estranho', 'Estranho', 'outra', 'outra', 1],
    ['sem_familia', 'Sem família', 'outra', 'outra', 3], ['__proto__', 'Proto', 'outra', 'outra', 1]]);
  assert.equal(sujo.total, 8);
  sujo.itens.forEach((i) => assert.deepEqual(Object.keys(i).sort(), ['familia', 'instancias', 'produto', 'rotulo', 'tipo']));
});
test('presença: render com a fixture mostra bloco e linha do cartão; k-trab, k-total, nav-agentes, abas e agent-rows iguais a sem a chave', () => {
  const semChave = clone(fixture); delete semChave.presenca_processos; delete semChave.fontes.presenca_processos;
  const antes = app(); antes.a.receber(semChave);
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  assert.equal(doc.getElementById('presence').hidden, false);
  const linhas = linhasPresenca(doc);
  assert.equal(linhas.length, 2);
  assert.equal(txt('presence-badge'), '3 processos');
  assert.equal(doc.getElementById('k-online-foot').hidden, false);
  assert.equal(txt('k-online-texto'), '3 processos de IA online');
  assert.equal(nb(linhas[0].textContent), 'Claude Code CLICLI · 2 instânciasonlineatividade desconhecida');
  assert.equal(nb(linhas[1].textContent), 'Grok Botaplicativoonlineatividade desconhecida');
  assert.equal(linhas[0].className, 'presence-row');
  assert.ok(linhas[0].descendentes().some((e) => e.className === 'status good'));
  assert.ok(!linhas[0].descendentes().some((e) => e.tag === 'button' || 'aria-haspopup' in e.attrs), 'linha de presença não é botão');
  linhas[0].click(); assert.equal(a.estado.modal, null);
  assert.equal(txt('k-trab'), '1'); assert.equal(txt('k-total'), '/ 3'); assert.equal(txt('nav-agentes'), '3');
  assert.equal(doc.getElementById('agent-rows').children.length, 3);
  assert.equal(doc.getElementById('agent-rows').children.length, antes.doc.getElementById('agent-rows').children.length);
  ['k-trab', 'k-total', 'k-agentes-texto', 'nav-agentes', 'agents-badge', 'tab-todos', 'tab-ativos', 'tab-ociosos', 'agent-rows', 'agent-empty']
    .forEach((id) => {
      assert.equal(txt(id), antes.txt(id), 'mudou ' + id);
      assert.equal(doc.getElementById(id).hidden, antes.doc.getElementById(id).hidden, 'mudou a visibilidade de ' + id);
    });
  assert.equal(antes.doc.getElementById('presence').hidden, true); assert.equal(antes.doc.getElementById('k-online-foot').hidden, true);
  // o bloco não depende das abas nem da busca
  doc.querySelectorAll('[data-agent-filter]').find((b) => b.attrs['data-agent-filter'] === 'ociosos').click();
  const busca = doc.getElementById('agent-search'); busca.emit('input', { target: Object.assign(busca, { value: 'zzz' }) });
  assert.equal(doc.getElementById('agent-rows').children.length, 0);
  assert.equal(linhasPresenca(doc).length, 2); assert.equal(doc.getElementById('presence').hidden, false);
});
test('presença: sem a chave, com estado erro ou antes da primeira leitura, bloco e linha do cartão ocultos e tabela vazia', () => {
  assert.match(html, /<div class="stat-foot" id="k-online-foot" hidden>/);
  assert.match(html, /<div class="presence" id="presence" role="group" aria-label="Processos de IA online" hidden>/);
  const { a, doc } = app();
  const oculto = (quando) => {
    assert.equal(doc.getElementById('presence').hidden, true, 'bloco visível ' + quando);
    assert.equal(doc.getElementById('k-online-foot').hidden, true, 'linha do cartão visível ' + quando);
    assert.equal(linhasPresenca(doc).length, 0, 'linhas de presença ' + quando);
  };
  oculto('antes da primeira leitura');
  const sem = clone(fixture); delete sem.presenca_processos;
  a.receber(sem); oculto('sem a chave');
  a.receber(clone(fixture)); assert.equal(linhasPresenca(doc).length, 2);
  a.receber(clone(sem)); oculto('quando a chave some');
  a.receber(clone(fixture));
  a.receber(Object.assign(clone(fixture), { presenca_processos: { estado: 'erro' } })); oculto('com estado erro');
});
test('presença: estado ok sem itens esconde o bloco e mostra "nenhum processo de IA online" no cartão', () => {
  const { a, doc, txt } = app();
  a.receber(Object.assign(clone(fixture), { presenca_processos: { estado: 'ok', itens: [], ilegiveis: 2 } }));
  assert.equal(doc.getElementById('presence').hidden, true);
  assert.equal(doc.getElementById('k-online-foot').hidden, false);
  assert.equal(txt('k-online-texto'), 'nenhum processo de IA online');
  assert.equal(linhasPresenca(doc).length, 0);
  a.receber(Object.assign(clone(fixture), { presenca_processos: { estado: 'ok', itens: [{ produto: 'x', instancias: 1 }] } }));   // só item inválido
  assert.equal(doc.getElementById('presence').hidden, true); assert.equal(txt('k-online-texto'), 'nenhum processo de IA online');
});
test('presença: rótulo hostil aparece como texto literal e nenhum elemento é criado a partir dele', () => {
  const { a, doc, txt } = app();
  const d = clone(fixture);
  d.presenca_processos.itens = [{ produto: 'hostil', rotulo: '<img src=x onerror=alert(1)>', familia: '<script>', tipo: '<b>', instancias: 2 }];
  d.agentes = [];                                                   // só o rótulo da presença traz HTML nesta leitura
  a.receber(d);
  const rows = doc.getElementById('presence-rows');
  assert.equal(linhasPresenca(doc).length, 1);
  assert.equal(nb(rows.textContent), '<img src=x onerror=alert(1)>2 instânciasonlineatividade desconhecida');
  assert.match(txt('presence'), /<img src=x onerror=alert\(1\)>/);
  ['img', 'script', 'b'].forEach((t) => assert.ok(!rows.descendentes().some((e) => e.tag === t), 'elemento <' + t + '> criado a partir do rótulo'));
  assert.equal(doc.documentElement.descendentes().filter((e) => e.tag === 'img').length, 0);
  assert.ok(!rows.descendentes().some((e) => Object.keys(e.attrs).some((k) => /^on/.test(k))));
});
test('presença: atualização ao vivo remove o produto que saiu, atualiza instâncias, troca o ícone e reaproveita a linha', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const primeira = linhasPresenca(doc)[0];
  const icone = (tr) => tr.descendentes().find((e) => e.tag === 'path').attrs.d;
  assert.equal(detalhePresenca(linhasPresenca(doc)[0]), 'CLI · 2 instâncias'); assert.equal(detalhePresenca(linhasPresenca(doc)[1]), 'aplicativo');
  assert.equal(icone(primeira), Hermes.ICONES.claude[0][1].d);
  const d1 = clone(fixture);
  d1.presenca_processos.itens = d1.presenca_processos.itens.filter((i) => i.produto !== 'grok_bot'); d1.presenca_processos.itens[0].instancias = 5;
  a.receber(d1);
  assert.equal(linhasPresenca(doc).length, 1); assert.ok(linhasPresenca(doc)[0] === primeira, 'a linha foi recriada');
  assert.doesNotMatch(txt('presence-rows'), /Grok Bot/);
  assert.equal(detalhePresenca(primeira), 'CLI · 5 instâncias');
  assert.equal(txt('presence-badge'), '5 processos'); assert.equal(txt('k-online-texto'), '5 processos de IA online');
  const d2 = clone(fixture); d2.presenca_processos.itens.forEach((i) => { i.instancias = 1; });
  a.receber(d2);
  assert.equal(linhasPresenca(doc).length, 2); assert.ok(linhasPresenca(doc)[0] === primeira);
  assert.equal(detalhePresenca(linhasPresenca(doc)[0]), 'CLI'); assert.equal(detalhePresenca(linhasPresenca(doc)[1]), 'aplicativo');
  assert.equal(txt('presence-badge'), '2 processos');
  const d3 = clone(fixture); d3.presenca_processos.itens = [Object.assign(d3.presenca_processos.itens[0], { familia: 'codex', instancias: 1 })];
  a.receber(d3);
  assert.equal(icone(linhasPresenca(doc)[0]), Hermes.ICONES.code[0][1].d);
  assert.equal(txt('presence-badge'), '1 processo'); assert.equal(txt('k-online-texto'), '1 processo de IA online');
  const d4 = clone(fixture);
  d4.presenca_processos.itens = [{ produto: 'novo', rotulo: 'Novo', familia: 'nova', tipo: 'servico', instancias: 4 }, { produto: 'unico', rotulo: 'Único', instancias: 1 }];
  a.receber(d4);
  assert.equal(detalhePresenca(linhasPresenca(doc)[0]), '4 instâncias'); assert.equal(detalhePresenca(linhasPresenca(doc)[1]), '');
});
test('presença que lança não impede KPIs nem a lista de sessões; o aviso fica só no bloco de presença', () => {
  const d = clone(fixture);
  Object.defineProperty(d.presenca_processos, 'itens', { enumerable: true, get() { throw new Error('boom presença'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /presença por processo/); assert.match(String(erros[0][1]), /boom presença/);
  });
  assert.equal(txt('k-trab'), '1'); assert.equal(txt('k-total'), '/ 3'); assert.equal(txt('nav-agentes'), '3');
  assert.equal(doc.getElementById('agent-rows').children.length, d.agentes.length);
  assert.equal(doc.getElementById('agents')._avisoSecao, undefined); assert.equal(doc.getElementById('stats')._avisoSecao, undefined);
  assert.match(txt('services-grid'), /Hindsight/); assert.match(txt('providers'), /\S/); assert.match(txt('cost-total'), /\d/);
  assert.match(txt('presence'), /não foi possível desenhar esta seção/);
  silenciarErros(() => a.receber(clone(fixture)));                       // na próxima leitura boa o aviso some
  assert.doesNotMatch(txt('presence'), /não foi possível desenhar/);
  assert.equal(doc.getElementById('presence').hidden, false); assert.equal(linhasPresenca(doc).length, 2);
});

/* ---------- E18: presença no WSL (bloco próprio; a presença da E16 e o cartão-contador não mudam) ---------- */
const linhasLinux = (doc) => doc.getElementById('presence-linux-rows').children;
const comLinux = (pl) => Object.assign(clone(fixture), { presenca_linux: pl });
const itensWsl = () => [{ produto: 'claude_cli', rotulo: 'Claude Code CLI', familia: 'claude', tipo: 'cli', instancias: 2 },
                        { produto: 'cursor_agent', rotulo: 'Cursor Agent CLI', familia: 'cursor', tipo: 'cli', instancias: 1 }];
const itensSujos = () => [
  { produto: 'sem_rotulo', familia: 'claude', tipo: 'cli', instancias: 1 },
  { produto: 'zero', rotulo: 'Zero', familia: 'claude', tipo: 'cli', instancias: 0 },
  { produto: 'texto', rotulo: 'Texto', familia: 'claude', tipo: 'cli', instancias: '2' },
  { produto: 'fracao', rotulo: 'Fração', familia: 'claude', tipo: 'cli', instancias: 1.5 },
  { produto: 'nan', rotulo: 'NaN', familia: 'claude', tipo: 'cli', instancias: NaN },
  'lixo', null, 7, ['x'],
  { produto: 'p'.repeat(41), rotulo: 'Produto longo', instancias: 1 },
  { produto: 'rotulo_longo', rotulo: 'r'.repeat(61), instancias: 1 },
  { produto: 'dup', rotulo: 'Primeiro', familia: 'codex', tipo: 'cli', instancias: 2 },
  { produto: 'dup', rotulo: 'Segundo', familia: 'claude', tipo: 'cli', instancias: 9 },
  { produto: 'estranho', rotulo: 'Estranho', familia: 'gemini', tipo: 'daemon', instancias: 1 },
  { produto: '__proto__', rotulo: 'Proto', familia: 'constructor', tipo: 'toString', instancias: 1 }
];
test('presencaLinuxDe: ausente/erro = inativa; parada ignora itens; ok valida itens pelas regras de presencaDe; host inválido vira ""', () => {
  const inativa = { ativa: false, estado: '', host: '', itens: [], total: 0 };
  [undefined, null, {}, { presenca_linux: null }, { presenca_linux: 'ok' }, { presenca_linux: [] }, { presenca_linux: { estado: 'erro' } },
   { presenca_linux: { estado: 'OK', host: 'Ubuntu-26.04' } }, { presenca_linux: { host: 'Ubuntu-26.04', itens: itensWsl() } },
   { presenca_processos: clone(fixture.presenca_processos) }].forEach((d) => assert.deepEqual(Hermes.presencaLinuxDe(d), inativa));
  assert.deepEqual(Hermes.presencaLinuxDe(fixture), { ativa: true, estado: 'parada', host: 'Ubuntu-26.04', itens: [], total: 0 });
  assert.deepEqual(Hermes.presencaLinuxDe({ presenca_linux: { estado: 'parada', host: 'Ubuntu-26.04', itens: itensWsl() } }),
    { ativa: true, estado: 'parada', host: 'Ubuntu-26.04', itens: [], total: 0 }, 'parada nunca traz itens');
  const ok = Hermes.presencaLinuxDe({ presenca_linux: { estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() } });
  assert.equal(ok.ativa, true); assert.equal(ok.estado, 'ok'); assert.equal(ok.host, 'Ubuntu-26.04'); assert.equal(ok.total, 3);
  assert.deepEqual(ok.itens.map((i) => [i.produto, i.rotulo, i.familia, i.tipo, i.instancias]),
    [['claude_cli', 'Claude Code CLI', 'claude', 'cli', 2], ['cursor_agent', 'Cursor Agent CLI', 'cursor', 'cli', 1]]);
  assert.deepEqual(Hermes.presencaLinuxDe({ presenca_linux: { estado: 'ok', host: 'Ubuntu-26.04' } }), { ativa: true, estado: 'ok', host: 'Ubuntu-26.04', itens: [], total: 0 });
  const sujo = Hermes.presencaLinuxDe({ presenca_linux: { estado: 'ok', host: 'Ubuntu-26.04', itens: itensSujos() } });
  const e16 = Hermes.presencaDe({ presenca_processos: { estado: 'ok', itens: itensSujos() } });
  assert.deepEqual(sujo.itens, e16.itens); assert.equal(sujo.total, e16.total);
  assert.deepEqual(sujo.itens.map((i) => [i.produto, i.rotulo, i.familia, i.tipo, i.instancias]),
    [['dup', 'Primeiro', 'codex', 'cli', 2], ['estranho', 'Estranho', 'outra', 'outra', 1], ['__proto__', 'Proto', 'outra', 'outra', 1]]);
  assert.equal(sujo.total, 4);
  sujo.itens.forEach((i) => assert.deepEqual(Object.keys(i).sort(), ['familia', 'instancias', 'produto', 'rotulo', 'tipo']));
  [undefined, null, 7, true, '', 'h'.repeat(41), ['Ubuntu'], { nome: 'Ubuntu' }].forEach((host) => {
    assert.equal(Hermes.presencaLinuxDe({ presenca_linux: { estado: 'ok', host, itens: [] } }).host, '', 'host ' + JSON.stringify(host));
    assert.equal(Hermes.presencaLinuxDe({ presenca_linux: { estado: 'parada', host } }).host, '', 'host parada ' + JSON.stringify(host));
  });
  assert.equal(Hermes.presencaLinuxDe({ presenca_linux: { estado: 'ok', host: 'h'.repeat(40) } }).host, 'h'.repeat(40));
  assert.equal(Hermes.presencaLinuxDe({ presenca_linux: { estado: 'parada', host: 'k' } }).host, 'k');
});
test('presença no WSL: render com a fixture (parada) mostra bloco, host e selo; o bloco e o cartão da E16 ficam iguais a sem a chave', () => {
  assert.match(html, /<div class="presence" id="presence-linux" role="group" aria-label="Processos de IA no WSL" hidden><div class="presence-head"><span id="presence-linux-titulo">WSL<\/span><span class="badge" id="presence-linux-badge"><\/span><\/div>/);
  const semChave = clone(fixture); delete semChave.presenca_linux; delete semChave.fontes.presenca_linux;
  const antes = app(); antes.a.receber(semChave);
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const bloco = doc.getElementById('presence-linux'), e16 = doc.getElementById('presence');
  assert.ok(bloco.parentNode === e16.parentNode && bloco.parentNode === doc.getElementById('agents'), 'irmão de #presence na seção de agentes');
  const irmaos = e16.parentNode.children; assert.ok(irmaos[irmaos.indexOf(e16) + 1] === bloco, 'logo depois de #presence');
  assert.equal(bloco.hidden, false);
  assert.equal(txt('presence-linux-titulo'), 'WSL · Ubuntu-26.04');
  assert.equal(txt('presence-linux-badge'), 'parada');
  assert.equal(linhasLinux(doc).length, 0);
  assert.equal(txt('presence-badge'), '3 processos'); assert.equal(linhasPresenca(doc).length, 2);
  assert.equal(txt('k-online-texto'), '3 processos de IA online'); assert.equal(doc.getElementById('k-online-foot').hidden, false);
  ['presence', 'presence-badge', 'presence-rows', 'k-online-foot', 'k-online-texto', 'k-trab', 'k-total', 'nav-agentes', 'agent-rows', 'agent-empty']
    .forEach((id) => {
      assert.equal(txt(id), antes.txt(id), 'mudou ' + id);
      assert.equal(doc.getElementById(id).hidden, antes.doc.getElementById(id).hidden, 'mudou a visibilidade de ' + id);
    });
  assert.equal(antes.doc.getElementById('presence-linux').hidden, true); assert.equal(linhasLinux(antes.doc).length, 0);
});
test('presença no WSL: estado ok com itens desenha as linhas e o selo "ligada"; o selo e o cartão da E16 não somam o WSL', () => {
  const { a, doc, txt } = app();
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() }));
  assert.equal(doc.getElementById('presence-linux').hidden, false);
  const linhas = linhasLinux(doc);
  assert.equal(linhas.length, 2);
  assert.equal(txt('presence-linux-badge'), 'ligada · 3 processos');
  assert.equal(txt('presence-linux-titulo'), 'WSL · Ubuntu-26.04');
  assert.equal(detalhePresenca(linhas[0]), 'CLI · 2 instâncias'); assert.equal(detalhePresenca(linhas[1]), 'CLI');
  assert.equal(nb(linhas[0].textContent), 'Claude Code CLICLI · 2 instânciasonlineatividade desconhecida');
  assert.equal(nb(linhas[1].textContent), 'Cursor Agent CLICLIonlineatividade desconhecida');
  assert.equal(linhas[0].className, 'presence-row');
  assert.ok(!linhas[0].descendentes().some((e) => e.tag === 'button' || 'aria-haspopup' in e.attrs), 'linha de presença não é botão');
  assert.equal(txt('presence-badge'), '3 processos'); assert.equal(txt('k-online-texto'), '3 processos de IA online');
  assert.equal(linhasPresenca(doc).length, 2);
  assert.equal(txt('k-trab'), '1'); assert.equal(txt('k-total'), '/ 3'); assert.equal(txt('nav-agentes'), '3');
  const um = comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: [itensWsl()[1]] });
  a.receber(um);
  assert.equal(txt('presence-linux-badge'), 'ligada · 1 processo'); assert.equal(linhasLinux(doc).length, 1);
  assert.equal(txt('presence-badge'), '3 processos'); assert.equal(txt('k-online-texto'), '3 processos de IA online');
  const semE16 = comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() }); delete semE16.presenca_processos;
  a.receber(semE16);                                                // só o WSL tem processos: o cartão continua sem a linha de presença
  assert.equal(linhasLinux(doc).length, 2); assert.equal(txt('presence-linux-badge'), 'ligada · 3 processos');
  assert.equal(doc.getElementById('presence').hidden, true); assert.equal(linhasPresenca(doc).length, 0);
  assert.equal(doc.getElementById('k-online-foot').hidden, true); assert.equal(txt('k-online-texto'), 'nenhum processo de IA online');
});
test('presença no WSL: estado ok sem itens mostra o bloco com "ligada · nenhum processo" e sem linhas', () => {
  const { a, doc, txt } = app();
  const vazio = (quando) => {
    assert.equal(doc.getElementById('presence-linux').hidden, false, 'bloco oculto ' + quando);
    assert.equal(txt('presence-linux-badge'), 'ligada · nenhum processo', 'selo ' + quando);
    assert.equal(linhasLinux(doc).length, 0, 'linhas ' + quando);
  };
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: [] })); vazio('com itens []');
  assert.equal(txt('presence-linux-titulo'), 'WSL · Ubuntu-26.04');
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() })); assert.equal(linhasLinux(doc).length, 2);
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: [{ produto: 'x', instancias: 1 }] })); vazio('só com item inválido');
  a.receber(comLinux({ estado: 'ok', itens: [] })); vazio('sem host');
  assert.equal(txt('presence-linux-titulo'), 'WSL');
  assert.equal(txt('presence-badge'), '3 processos'); assert.equal(txt('k-online-texto'), '3 processos de IA online');
});
test('presença no WSL: sem a chave, com estado erro ou antes da primeira leitura, bloco oculto e tabela vazia', () => {
  const { a, doc, txt } = app();
  const oculto = (quando) => {
    assert.equal(doc.getElementById('presence-linux').hidden, true, 'bloco visível ' + quando);
    assert.equal(linhasLinux(doc).length, 0, 'linhas no WSL ' + quando);
  };
  oculto('antes da primeira leitura');
  const sem = clone(fixture); delete sem.presenca_linux; delete sem.fontes.presenca_linux;
  a.receber(sem); oculto('sem a chave');
  assert.equal(doc.getElementById('presence').hidden, false); assert.equal(linhasPresenca(doc).length, 2);
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() })); assert.equal(linhasLinux(doc).length, 2);
  a.receber(clone(sem)); oculto('quando a chave some');
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() })); assert.equal(linhasLinux(doc).length, 2);
  a.receber(comLinux({ estado: 'erro' })); oculto('com estado erro');
  a.receber(comLinux(null)); oculto('com a chave nula');
  a.receber(comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: itensWsl() }));
  a.receber(clone(fixture));                                        // de ok com linhas para parada: linhas somem, selo "parada"
  assert.equal(doc.getElementById('presence-linux').hidden, false); assert.equal(linhasLinux(doc).length, 0); assert.equal(txt('presence-linux-badge'), 'parada');
});
test('presença no WSL: rótulo e host hostis aparecem como texto literal e nenhum elemento é criado a partir deles', () => {
  const { a, doc, txt } = app();
  const hostil = '<img src=x onerror=alert(1)>';
  const d = comLinux({ estado: 'ok', host: hostil, itens: [{ produto: 'hostil', rotulo: hostil, familia: '<script>', tipo: '<b>', instancias: 2 }] });
  d.agentes = [];                                                   // só a presença no WSL traz HTML nesta leitura
  a.receber(d);
  const bloco = doc.getElementById('presence-linux'), rows = doc.getElementById('presence-linux-rows');
  assert.equal(txt('presence-linux-titulo'), 'WSL · ' + hostil);
  assert.equal(linhasLinux(doc).length, 1);
  assert.equal(nb(rows.textContent), hostil + '2 instânciasonlineatividade desconhecida');
  ['img', 'script', 'b'].forEach((t) => assert.ok(!bloco.descendentes().some((e) => e.tag === t), 'elemento <' + t + '> criado a partir do dado do WSL'));
  assert.equal(doc.documentElement.descendentes().filter((e) => e.tag === 'img').length, 0);
  assert.ok(!bloco.descendentes().some((e) => Object.keys(e.attrs).some((k) => /^on/.test(k))));
  a.receber(comLinux({ estado: 'parada', host: EMAIL }));            // o host passa pela máscara de privacidade como todo texto de dado
  if (!a.estado.privado) a.alternarPrivacidade();
  assert.ok(!txt('presence-linux-titulo').includes(EMAIL), 'e-mail no título com a privacidade ligada');
  assert.match(txt('presence-linux-titulo'), /^WSL · \S/);
  a.alternarPrivacidade();
  assert.equal(a.estado.privado, false); assert.equal(txt('presence-linux-titulo'), 'WSL · ' + EMAIL);
});
test('presença no WSL que lança não impede o bloco da E16, os KPIs nem a lista de sessões; o aviso fica só no bloco do WSL', () => {
  const d = comLinux({ estado: 'ok', host: 'Ubuntu-26.04', itens: [] });
  Object.defineProperty(d.presenca_linux, 'itens', { enumerable: true, get() { throw new Error('boom wsl'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /presença no WSL/); assert.match(String(erros[0][1]), /boom wsl/);
  });
  assert.equal(txt('k-trab'), '1'); assert.equal(txt('k-total'), '/ 3'); assert.equal(txt('nav-agentes'), '3');
  assert.equal(doc.getElementById('agent-rows').children.length, d.agentes.length);
  assert.equal(doc.getElementById('agents')._avisoSecao, undefined); assert.equal(doc.getElementById('stats')._avisoSecao, undefined);
  assert.equal(doc.getElementById('presence')._avisoSecao, undefined);
  assert.equal(doc.getElementById('presence').hidden, false); assert.equal(linhasPresenca(doc).length, 2);
  assert.equal(txt('presence-badge'), '3 processos'); assert.equal(txt('k-online-texto'), '3 processos de IA online');
  assert.doesNotMatch(txt('presence'), /não foi possível desenhar/);
  assert.match(txt('services-grid'), /Hindsight/); assert.match(txt('providers'), /\S/); assert.match(txt('cost-total'), /\d/);
  assert.match(txt('presence-linux'), /não foi possível desenhar esta seção/);
  silenciarErros(() => a.receber(clone(fixture)));                       // na próxima leitura boa o aviso some
  assert.doesNotMatch(txt('presence-linux'), /não foi possível desenhar/);
  assert.equal(doc.getElementById('presence-linux').hidden, false); assert.equal(txt('presence-linux-badge'), 'parada');
});
test('terminal do Maestri com pasta, atividade e última fala nulos desenha sem erro e a linha mostra o nome', () => {
  const base = app();
  silenciarErros(() => base.a.receber(clone(fixture)));
  const antes = base.doc.getElementById('agent-rows').children.length;
  const d = clone(fixture);
  d.agentes.push({ nome: 'Time/Agente', ferramenta: 'maestri', ferramenta_nome: 'Maestri · custom', estado: 'trabalhando', leitura: 'ok', session_id: 't-1', fonte: 'maestri_wire', pasta: null, atividade: null, ultima_fala: null, missao: null, prs: [] });
  const ag = Hermes.agentesDe(d).filter((x) => x.nome === 'Time/Agente')[0];
  assert.equal(ag.atividade, ''); assert.equal(ag.ultimaFala, '');
  const { a, doc } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 0);
  });
  const linhas = doc.getElementById('agent-rows').children;
  assert.equal(linhas.length, antes + 1);
  assert.ok(linhas.some((l) => nb(l.textContent).includes('Time/Agente')));
});

/* ---------- E19: uso observado em arquivos locais (bloco próprio na seção Limites de uso; #providers não muda; desde a E19b, com o Codex num cartão só) ---------- */
const CHAVES_LOCAIS = ['cota_codex_local', 'cota_grok_bot', 'cota_cursor'];
const semCotasLocais = () => { const d = clone(fixture); CHAVES_LOCAIS.forEach((k) => { delete d[k]; delete d.fontes[k]; }); return d; };
const erroNasTres = () => Object.assign(semCotasLocais(), { cota_codex_local: { estado: 'erro' }, cota_grok_bot: { estado: 'erro' }, cota_cursor: { estado: 'erro' } });
const cartoesLocais = (doc) => doc.getElementById('limits-local-cards').children;
const porClasse = (no, cls) => no.descendentes().filter((e) => e.classList.contains(cls));
const textosDe = (no, cls) => porClasse(no, cls).map((e) => nb(e.textContent));
const rotulosDe = (cartao) => porClasse(cartao, 'usage-label').map((l) => nb(l.children[0].textContent));
test('cotasLocaisDe: sem as chaves = []; erro fica fora; ordem fixa; barras validadas e limitadas; plano e assinatura por formato; sem_dado sem barras', () => {
  assert.deepEqual(Hermes.cotasLocaisDe(null), []);
  assert.deepEqual(Hermes.cotasLocaisDe(semCotasLocais()), []);
  assert.deepEqual(Hermes.cotasLocaisDe(erroNasTres()), []);
  [null, 'ok', 7, ['ok'], {}, { estado: 'outro' }].forEach((v) => assert.deepEqual(Hermes.cotasLocaisDe({ cota_codex_local: v, cota_cursor: v }), [], JSON.stringify(v)));
  assert.deepEqual(Hermes.cotasLocaisDe(fixture), [
    { chave: 'cota_codex_local', nome: 'Codex', detalhe: 'observado localmente', icone: 'code', estado: 'ok', observadoEm: T0 - 7200,
      barras: [{ tipo: '5h', pct: 23, renovaEpoch: T0 + 3600 }, { tipo: 'semana', pct: 61.5, renovaEpoch: T0 + 259200 }], plano: '', assinatura: '' },
    { chave: 'cota_grok_bot', nome: 'Grok Bot', detalhe: 'observado localmente', icone: 'agents', estado: 'ok', observadoEm: T0 - 600,
      barras: [{ tipo: 'janela', pct: 41, renovaEpoch: T0 + 86400 }], plano: '', assinatura: '' },
    { chave: 'cota_cursor', nome: 'Cursor', detalhe: 'plano local', icone: 'agents', estado: 'ok', observadoEm: null, barras: [], plano: 'pro_plus', assinatura: 'active' }
  ]);
  const invertido = { cota_cursor: clone(fixture.cota_cursor), cota_grok_bot: clone(fixture.cota_grok_bot), cota_codex_local: clone(fixture.cota_codex_local) };
  assert.deepEqual(Hermes.cotasLocaisDe(invertido).map((x) => x.chave), CHAVES_LOCAIS);
  assert.deepEqual(Hermes.cotasLocaisDe(Object.assign(clone(fixture), { cota_grok_bot: { estado: 'erro' } })).map((x) => x.chave), ['cota_codex_local', 'cota_cursor']);
  const sujo = Hermes.cotasLocaisDe({ cota_codex_local: { estado: 'ok', observado_epoch: 'ontem', barras: [
    { tipo: '5h', usado_pct: 150, renova_epoch: 'amanhã' }, { tipo: 'semana', usado_pct: -3, renova_epoch: Infinity }, { tipo: 'mensal', usado_pct: 10, renova_epoch: T0 },
    { tipo: 'semana', usado_pct: '50' }, { tipo: '5h' }, { tipo: '5h', usado_pct: NaN }, { tipo: '5h', usado_pct: true }, null, 'lixo', 7,
    { usado_pct: 20 }, { tipo: 'janela', usado_pct: 30 }] } })[0];
  assert.equal(sujo.observadoEm, null);
  assert.deepEqual(sujo.barras, [{ tipo: '5h', pct: 100, renovaEpoch: null }, { tipo: 'semana', pct: 0, renovaEpoch: null }, { tipo: 'janela', pct: 10, renovaEpoch: T0 },
                                 { tipo: 'janela', pct: 20, renovaEpoch: null }]);
  const vazio = Hermes.cotasLocaisDe({ cota_codex_local: { estado: 'sem_dado', observado_epoch: null, barras: [{ tipo: '5h', usado_pct: 10 }] },
                                       cota_grok_bot: { estado: 'sem_dado', observado_epoch: null, barras: [] }, cota_cursor: { estado: 'sem_dado', plano: null, assinatura: null } });
  assert.deepEqual(vazio.map((x) => [x.chave, x.estado, x.barras, x.plano, x.assinatura]),
                   [['cota_codex_local', 'sem_dado', [], '', ''], ['cota_grok_bot', 'sem_dado', [], '', ''], ['cota_cursor', 'sem_dado', [], '', '']]);
  ['Pro', 'pro-plus', 'pro plus', 'p'.repeat(25), '<img src=x onerror=alert(1)>', '', 7, null, ['pro']].forEach((v) => {
    const c = Hermes.cotasLocaisDe({ cota_cursor: { estado: 'ok', plano: v, assinatura: v } })[0];
    assert.equal(c.plano, '', 'plano ' + JSON.stringify(v)); assert.equal(c.assinatura, '', 'assinatura ' + JSON.stringify(v));
  });
  const limite = Hermes.cotasLocaisDe({ cota_cursor: { estado: 'ok', plano: 'p'.repeat(24), assinatura: 'outra' } })[0];
  assert.equal(limite.plano, 'p'.repeat(24)); assert.equal(limite.assinatura, 'outra');
  assert.equal(Hermes.cotasLocaisDe({ cota_codex_local: { estado: 'ok', plano: 'pro', barras: [] } })[0].plano, '');
});
test('limites locais: render com a fixture mostra o bloco depois de #providers com 3 cartões, selos, barras e rodapés; em #providers só sai a conta do Codex sem dados', () => {
  assert.match(html, /<div class="providers" id="providers"><\/div>\s*<div class="presence" id="limits-local" role="group" aria-label="Uso observado em arquivos locais" hidden><div class="presence-head"><span>Observado em arquivos locais<\/span><span class="badge" id="limits-local-badge"><\/span><\/div><div class="providers" id="limits-local-cards"><\/div><\/div>\s*<\/section>/);
  const antes = app(); antes.a.receber(semCotasLocais());
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const bloco = doc.getElementById('limits-local'), prov = doc.getElementById('providers');
  assert.ok(bloco.parentNode === doc.getElementById('limits'), 'dentro da seção Limites de uso');
  const irmaos = prov.parentNode.children; assert.ok(irmaos[irmaos.indexOf(prov) + 1] === bloco, 'logo depois de #providers');
  assert.equal(bloco.hidden, false);
  assert.equal(txt('limits-local-badge'), '3 fontes');
  const cards = cartoesLocais(doc); assert.equal(cards.length, 3);
  cards.forEach((c) => assert.equal(c.className, 'provider'));
  const [codex, grok, cursor] = cards;
  assert.deepEqual(cards.map((c) => nb(porClasse(c, 'provider-name')[0].childNodes[0].textContent)), ['Codex', 'Grok Bot', 'Cursor']);
  assert.deepEqual(cards.map((c) => nb(porClasse(c, 'provider-name')[0].children[0].textContent)), ['observado localmente', 'observado localmente', 'plano local']);
  assert.deepEqual(textosDe(codex, 'badge'), ['OBSERVADO']);
  assert.deepEqual(textosDe(codex, 'mono'), ['23%', '62%']);
  assert.deepEqual(rotulosDe(codex), ['Janela de 5 horas', 'Limite semanal']);
  assert.deepEqual(porClasse(codex, 'usage-row').map((r) => r.className), ['usage-row', 'usage-row week']);
  const trilhos = porClasse(codex, 'track');
  assert.deepEqual(trilhos.map((t) => [t.getAttribute('role'), t.getAttribute('aria-label'), t.getAttribute('aria-valuemin'), t.getAttribute('aria-valuemax'), t.getAttribute('aria-valuenow'), t.children[0].style.width]),
                   [['meter', 'Janela de 5 horas de Codex', '0', '100', '23', '23%'], ['meter', 'Limite semanal de Codex', '0', '100', '62', '61.5%']]);
  porClasse(codex, 'usage-row').forEach((r) => assert.match(nb(r.children[2].textContent), /^reinicia .+ \(em .+\)$/));
  assert.equal(nb(codex.children.at(-1).textContent), 'último observado há 2 h'); assert.equal(codex.children.at(-1).className, 'usage-sub');
  assert.deepEqual(textosDe(grok, 'badge'), ['OBSERVADO']);
  assert.deepEqual(textosDe(grok, 'mono'), ['41%']);
  assert.deepEqual(rotulosDe(grok), ['Janela atual']);
  assert.equal(nb(grok.children.at(-1).textContent), 'último observado há 10 min');
  assert.deepEqual(textosDe(cursor, 'badge'), ['Pro plus']);
  assert.deepEqual(textosDe(cursor, 'missing'), ['Sem dados de cota: o Cursor não guarda a cota em arquivo local.']);
  assert.deepEqual(textosDe(cursor, 'usage-sub'), ['assinatura: ativa']);
  assert.equal(porClasse(cursor, 'usage-row').length, 0);
  assert.equal(prov.children.length, antes.doc.getElementById('providers').children.length - 1);
  assert.doesNotMatch(txt('providers'), /Codex/); assert.match(antes.txt('providers'), /Codex.*SEM DADOS.*Conta ainda não identificada/);
  assert.match(txt('providers'), /Claude.*Code.*Pro/);
  assert.equal(antes.doc.getElementById('limits-local').hidden, true); assert.equal(cartoesLocais(antes.doc).length, 0);
});
test('limites locais: sem as chaves, com erro nas três ou sem leitura, o bloco fica oculto e sem cartões', () => {
  [['sem as chaves', semCotasLocais()], ['erro nas três', erroNasTres()]].forEach(([quando, d]) => {
    const { a, doc } = app(); a.receber(d);
    assert.equal(doc.getElementById('limits-local').hidden, true, 'visível ' + quando);
    assert.equal(cartoesLocais(doc).length, 0, 'cartões ' + quando);
  });
  const nulo = app();
  assert.equal(nulo.doc.getElementById('limits-local').hidden, true); assert.equal(cartoesLocais(nulo.doc).length, 0);
  const { a, doc } = app(); a.receber(clone(fixture)); assert.equal(cartoesLocais(doc).length, 3);
  a.receber(Object.assign(clone(fixture), { cota_codex_local: { estado: 'erro' }, cota_grok_bot: { estado: 'erro' }, cota_cursor: { estado: 'erro' } }));
  assert.equal(doc.getElementById('limits-local').hidden, true); assert.equal(cartoesLocais(doc).length, 0);
});
test('limites locais: sem_dado no Codex mostra selo "SEM DADOS" e o aviso, nada mais; selo do bloco "1 fonte" (sem conta do Codex)', () => {
  const d = Object.assign(semCotasLocais(), { cota_codex_local: { estado: 'sem_dado', observado_epoch: null, barras: [] } });
  d.contas.lista = d.contas.lista.filter((c) => c.ferramenta !== 'codex');   // sem conta do Codex, o cartão local sem dados é o único
  const { a, doc, txt } = app(); a.receber(d);
  assert.equal(doc.getElementById('limits-local').hidden, false);
  assert.equal(txt('limits-local-badge'), '1 fonte');
  const cards = cartoesLocais(doc); assert.equal(cards.length, 1);
  assert.deepEqual(textosDe(cards[0], 'badge'), ['SEM DADOS']);
  assert.deepEqual(textosDe(cards[0], 'missing'), ['Nenhum uso encontrado nos arquivos locais.']);
  assert.equal(porClasse(cards[0], 'usage-row').length, 0); assert.equal(porClasse(cards[0], 'usage-sub').length, 0);
  assert.equal(cards[0].children.length, 2);
});
test('limites locais: reset já passado mantém o percentual com o texto de reset vencido; reset nulo = "reset não informado"', () => {
  const d = Object.assign(semCotasLocais(), {
    cota_codex_local: { estado: 'ok', observado_epoch: T0 - 90000, barras: [{ tipo: '5h', usado_pct: 87.0, renova_epoch: T0 - 60, janela_min: 300 },
                                                                            { tipo: 'semana', usado_pct: 12.0, renova_epoch: null, janela_min: 10080 }] },
    cota_grok_bot: { estado: 'ok', observado_epoch: null, barras: [{ tipo: 'janela', usado_pct: 5.0, renova_epoch: null, janela_min: null }] } });
  const { a, doc } = app(); a.receber(d);
  const [codex, grok] = cartoesLocais(doc);
  assert.deepEqual(textosDe(codex, 'mono'), ['87%', '12%']);
  assert.equal(porClasse(codex, 'track')[0].getAttribute('aria-valuenow'), '87'); assert.equal(porClasse(codex, 'track')[0].children[0].style.width, '87%');
  assert.deepEqual(porClasse(codex, 'usage-row').map((r) => nb(r.children[2].textContent)), ['reset já passou; aguardando nova leitura', 'reset não informado']);
  assert.equal(nb(codex.children.at(-1).textContent), 'último observado há 1 dia 1 h');
  assert.deepEqual(textosDe(grok, 'mono'), ['5%']);
  assert.deepEqual(porClasse(grok, 'usage-row').map((r) => nb(r.children[2].textContent)), ['reset não informado']);
  assert.equal(nb(grok.children.at(-1).textContent), 'horário da observação não informado');
});
test('limites locais: textos hostis em plano, tipo e assinatura não viram elemento; selo "PLANO NÃO LIDO", rótulo "Janela atual" e assinatura fora da lista = "outra"', () => {
  const hostil = '<img src=x onerror=alert(1)>';
  const d = Object.assign(semCotasLocais(), {
    cota_codex_local: { estado: 'ok', observado_epoch: T0 - 60, barras: [{ tipo: hostil, usado_pct: 33.0, renova_epoch: null, janela_min: hostil }] },
    cota_cursor: { estado: 'ok', plano: hostil, assinatura: hostil } });
  const { a, doc } = app(); a.receber(d);
  const bloco = doc.getElementById('limits-local'), [codex, cursor] = cartoesLocais(doc);
  assert.deepEqual(textosDe(cursor, 'badge'), ['PLANO NÃO LIDO']);
  assert.equal(porClasse(cursor, 'usage-sub').length, 0, 'assinatura hostil não vira linha');
  assert.deepEqual(rotulosDe(codex), ['Janela atual']);
  assert.equal(porClasse(codex, 'track')[0].getAttribute('aria-label'), 'Janela atual de Codex');
  assert.equal(bloco.descendentes().filter((e) => e.tag === 'img').length, 0);
  assert.ok(!nb(bloco.textContent).includes('onerror'), 'texto hostil no bloco');
  bloco.descendentes().forEach((e) => Object.values(e.attrs).forEach((v) => assert.ok(!v.includes('onerror'), 'texto hostil em atributo')));
  const outra = app(); outra.a.receber(Object.assign(semCotasLocais(), { cota_cursor: { estado: 'ok', plano: null, assinatura: 'constructor' } }));
  const c2 = cartoesLocais(outra.doc)[0];
  assert.deepEqual(textosDe(c2, 'badge'), ['PLANO NÃO LIDO']); assert.deepEqual(textosDe(c2, 'usage-sub'), ['assinatura: outra']);
});
test('limites locais que lançam não impedem #providers, os KPIs nem a lista de sessões; o aviso fica só no bloco local', () => {
  const base = app(); silenciarErros(() => base.a.receber(clone(fixture)));
  const d = clone(fixture);
  Object.defineProperty(d.cota_codex_local, 'barras', { enumerable: true, get() { throw new Error('boom cotas locais'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /uso observado em arquivos locais/); assert.match(String(erros[0][1]), /boom cotas locais/);
  });
  ['k-trab', 'k-total', 'nav-agentes'].forEach((id) => assert.equal(txt(id), base.txt(id), 'mudou ' + id));
  const semLocais = app(); semLocais.a.receber(semCotasLocais());
  assert.equal(txt('providers'), semLocais.txt('providers'), 'com a lista local falhando, #providers é o de sem as chaves');
  assert.match(txt('providers'), /\S/); assert.doesNotMatch(txt('providers'), /não foi possível desenhar/);
  assert.equal(doc.getElementById('agent-rows').children.length, d.agentes.length);
  ['stats', 'agents', 'limits', 'presence', 'presence-linux'].forEach((id) => assert.equal(doc.getElementById(id)._avisoSecao, undefined, 'aviso em ' + id));
  assert.match(txt('limits-local'), /não foi possível desenhar esta seção/);
  assert.equal(cartoesLocais(doc).length, 0);
  silenciarErros(() => a.receber(clone(fixture)));                       // na próxima leitura boa o aviso some
  assert.doesNotMatch(txt('limits-local'), /não foi possível desenhar/);
  assert.equal(doc.getElementById('limits-local').hidden, false); assert.equal(cartoesLocais(doc).length, 3);
});

/* ---------- E23: bloco "Vigilante 2" na seção Saúde dos serviços ---------- */
const semVig = () => { const d = clone(fixture); delete d.vigilante2; return d; };
const comVig = (extra) => { const d = clone(fixture); Object.assign(d.vigilante2, extra); return d; };
const cartoesVig = (doc) => doc.getElementById('vigilante-grid').children;
test('vigilanteDe: sem a chave, "erro" e "desligado" ficam inativos; "ok" normaliza campos, tipos, contagens e limite de alertas', () => {
  [undefined, { estado: 'erro' }, { estado: 'desligado' }, null, 'x', []].forEach((v) => {
    const d = semVig(); if (v !== undefined) d.vigilante2 = v;
    const r = Hermes.vigilanteDe(d); assert.equal(r.ativo, false); assert.deepEqual(r.alertas, []);
  });
  assert.equal(Hermes.vigilanteDe(null).ativo, false);
  const r = Hermes.vigilanteDe(fixture);
  assert.equal(r.ativo, true); assert.equal(r.estado, 'ok'); assert.equal(r.geradoEm, T0 - 40); assert.equal(r.wireOk, true); assert.equal(r.painelOk, true);
  assert.equal(r.online, 8); assert.equal(r.terminais, 21); assert.equal(r.falhas, 0);
  assert.deepEqual(r.porTipo, { claude_code: 9, codex: 4, custom: 5, generic_shell: 3, outra: 0 });
  assert.deepEqual(r.alertas, [{ tipo: 'login', desde: T0 - 600 }, { tipo: 'cota_baixa', desde: T0 - 7200 }]);
  const x = Hermes.vigilanteDe(comVig({ alertas: [{ tipo: 'inventado', desde_epoch: 'x' }], online: -3, trabalhando: 'dois', atencao: NaN, workspaces: 2.7, wire_ok: 1 }));
  assert.deepEqual(x.alertas, [{ tipo: 'outro', desde: null }]);
  assert.equal(x.online, 0); assert.equal(x.trabalhando, 0); assert.equal(x.atencao, 0); assert.equal(x.workspaces, 2); assert.equal(x.wireOk, false);
  const muitos = Array.from({ length: 20 }, () => ({ tipo: 'login', desde_epoch: T0 }));
  assert.equal(Hermes.vigilanteDe(comVig({ alertas: muitos })).alertas.length, 11);
});
test('vigilante: render da fixture mostra o bloco ATIVO com quatro cartões sem botão e não mexe na grade de serviços nem nos KPIs', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const base = app(); base.a.receber(semVig());
  assert.equal(doc.getElementById('vigilante').hidden, false);
  assert.equal(txt('vigilante-badge'), 'ATIVO'); assert.equal(doc.getElementById('vigilante-badge').classList.contains('bad'), false);
  const cs = cartoesVig(doc); assert.equal(cs.length, 4);
  cs.forEach((c) => { assert.notEqual(c.tag, 'button'); assert.equal(c.getAttribute('data-service'), null); });
  const t = (c) => nb(c.textContent);
  assert.match(t(cs[0]), /Ativo/); assert.match(t(cs[0]), /8 online · 2 trabalhando · 1 em atenção/); assert.match(t(cs[0]), /atualizado há menos de 1 min/);
  assert.match(t(cs[1]), /2 alertas/); assert.match(t(cs[1]), /login · cota baixa/); assert.match(t(cs[1]), /o mais antigo há 2 h/);
  assert.match(t(cs[2]), /Em ordem/); assert.match(t(cs[2]), /Wire ok · Painel ok/); assert.match(t(cs[2]), /sem falhas seguidas/);
  assert.match(t(cs[3]), /21 terminais/); assert.match(t(cs[3]), /Claude Code 9 · Codex 4 · Personalizado 5 · Shell 3 · Outros 0/); assert.match(t(cs[3]), /5 workspaces/);
  assert.equal(doc.getElementById('services-grid').children.length, 5);
  ['k-serv', 'k-serv-total', 'nav-servicos'].forEach((id) => assert.equal(txt(id), base.txt(id), 'mudou ' + id));
  assert.equal(base.doc.getElementById('vigilante').hidden, true);
});
test('vigilante: estado "parado" mostra um cartão e nenhum número do retrato velho', () => {
  const { a, doc, txt } = app(); a.receber(comVig({ estado: 'parado' }));
  assert.equal(doc.getElementById('vigilante').hidden, false);
  assert.equal(txt('vigilante-badge'), 'PARADO'); assert.equal(doc.getElementById('vigilante-badge').classList.contains('bad'), true);
  const cs = cartoesVig(doc); assert.equal(cs.length, 1);
  const t = nb(cs[0].textContent);
  assert.match(t, /Parado/); assert.match(t, /Vigilante 2 parado/); assert.match(t, /último sinal há \S+/);
  ['online', 'trabalhando', 'em atenção', 'terminais', 'workspaces', 'Claude Code', 'alerta', 'Wire'].forEach((x) => assert.ok(!t.includes(x), 'apareceu ' + x));
});
test('vigilante: sem a chave, "erro" e "desligado" deixam o bloco oculto e sem cartões', () => {
  [undefined, { estado: 'erro' }, { estado: 'desligado' }].forEach((v) => {
    const d = semVig(); if (v !== undefined) d.vigilante2 = v;
    const { a, doc } = app(); a.receber(d);
    assert.equal(doc.getElementById('vigilante').hidden, true); assert.equal(cartoesVig(doc).length, 0);
  });
});
test('vigilante: variações do estado ok (sem alertas, Wire fora, falhas seguidas, alerta sem horário)', () => {
  const ver = (extra) => { const { a, doc } = app(); a.receber(comVig(extra)); return Array.from(cartoesVig(doc)).map((c) => nb(c.textContent)); };
  let cs = ver({ alertas: [] });
  assert.match(cs[1], /Nenhum alerta/); assert.match(cs[1], /Nada pendente/); assert.match(cs[1], /sem alertas/);
  cs = ver({ wire_ok: false });
  assert.match(cs[2], /Com falha/); assert.match(cs[2], /Wire fora · Painel ok/);
  cs = ver({ falhas_seguidas: 3 });
  assert.match(cs[2], /Com falha/); assert.match(cs[2], /3 falhas seguidas/);
  cs = ver({ alertas: [{ tipo: 'login', desde_epoch: null }] });
  assert.match(cs[1], /1 alerta/); assert.match(cs[1], /início não informado/);
});
test('vigilante: tipo de alerta hostil vira "outro" e não cria elemento', () => {
  const { a, doc } = app(); a.receber(comVig({ alertas: [{ tipo: '<img src=x onerror=alert(1)>', desde_epoch: T0 - 60 }] }));
  const bloco = doc.getElementById('vigilante');
  assert.match(nb(cartoesVig(doc)[1].textContent), /outro/);
  assert.equal(bloco.descendentes().filter((e) => e.tag === 'img').length, 0);
  assert.ok(!nb(bloco.textContent).includes('onerror'));
  bloco.descendentes().forEach((e) => Object.values(e.attrs).forEach((v) => assert.ok(!v.includes('onerror'), 'texto hostil em atributo')));
});
test('vigilante que lança não impede a grade de serviços, os KPIs nem a lista de sessões; o aviso fica só no bloco', () => {
  const base = app(); silenciarErros(() => base.a.receber(clone(fixture)));
  const d = clone(fixture);
  Object.defineProperty(d.vigilante2, 'alertas', { enumerable: true, get() { throw new Error('boom vigilante'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /Vigilante 2/); assert.match(String(erros[0][1]), /boom vigilante/);
  });
  ['k-serv', 'k-serv-total', 'nav-servicos', 'services-grid', 'k-trab', 'nav-agentes'].forEach((id) => assert.equal(txt(id), base.txt(id), 'mudou ' + id));
  assert.equal(doc.getElementById('agent-rows').children.length, d.agentes.length);
  assert.equal(doc.getElementById('services')._avisoSecao, undefined);
  assert.match(txt('vigilante'), /não foi possível desenhar esta seção/);
});

/* ---------- E25: custo por agente ---------- */
const semCusto = () => { const d = clone(fixture); delete d.custo_agentes; return d; };
test('custo: fixture mostra hoje, 7 dias, agentes, selos, sessões e cotação', () => {
  const { a, doc, txt } = app(); a.estado.privado = false; a.receber(clone(fixture));
  assert.equal(doc.getElementById('custo').hidden, false);
  assert.match(txt('custo-body'), /Hoje: R\$ 10,00 · US\$ 2,00/);
  assert.match(txt('custo-body'), /7 dias: R\$ 30,00 · US\$ 6,00/);
  ['Claude Code', 'Codex', 'Grok', 'Hermes', 'Cursor-agent', 'assinatura (equivalente de API)', 'informado pelo Grok', 'estimado', 'sem leitura', 'claude-opus-5-5', '110 tokens', 'Cotação: R$ 5,0000', 'manual'].forEach((v) => assert.ok(txt('custo-body').includes(v), v));
  const barras = porClasse(doc.getElementById('custo-body'), 'track');
  assert.deepEqual(barras.map((b) => b.children[0].style.width), ['100%', '50%']);
  const base = app(); base.a.estado.privado = false; base.a.receber(semCusto());
  ['k-trab', 'k-total', 'k-serv', 'agent-rows'].forEach((id) => assert.equal(txt(id), base.txt(id)));
});
test('custo: sem chave ou estado erro oculta e limpa o cartão', () => {
  const { a, doc } = app(); a.receber(clone(fixture));
  [undefined, null, {}, { estado: 'erro' }, { estado: 'ok', dias: null }].forEach((c) => {
    const d = semCusto(); if (c !== undefined) d.custo_agentes = c;
    a.receber(d);
    assert.equal(doc.getElementById('custo').hidden, true);
    assert.equal(doc.getElementById('custo-body').children.length, 0);
  });
});
test('custo: sem cotação mostra sem leitura em R$ e mantém US$; data, cache velho e avisos', () => {
  const d = clone(fixture), c = d.custo_agentes;
  c.cotacao = null; c.hoje.brl = null; c.sete_dias.brl = null;
  c.avisos = ['tarifa conferida há mais de 30 dias', 'modelo sem tarifa; custo sem leitura', 'arquivo de tarifas do usuário inválido; usando o padrão'];
  c.sessoes[0].usd = null; c.sessoes[0].brl = null; c.sessoes[0].flags = ['sem_tarifa', 'contexto_longo_nao_ajustado'];
  const { a, txt } = app(); a.receber(d);
  assert.match(txt('custo-body'), /Hoje: R\$ sem leitura · US\$ 2,00/);
  assert.match(txt('custo-body'), /Cotação: sem leitura · sem data/);
  ['tarifa conferida há mais de 30 dias', 'modelo sem tarifa', 'sem tarifa', 'contexto longo não ajustado', 'usando o padrão'].forEach((v) => assert.ok(txt('custo-body').includes(v)));
  c.cotacao = { usd_brl: 5.4, data_epoch: T0, fonte: 'cache', desatualizada: true }; a.receber(d);
  assert.ok(txt('custo-body').includes(new Date(T0 * 1000).toLocaleString('pt-BR')));
  assert.match(txt('custo-body'), /cache · desatualizada/);
});
test('custo: números e textos hostis são filtrados; barras finitas de 0 a 100', () => {
  const { a, doc, txt } = app(), d = clone(fixture), c = d.custo_agentes;
  c.dias = [NaN, -2, '33', Infinity, 20, 40].map((v, i) => ({ dia: '2026-11-' + (10+i), total: { usd: v, brl: v } }));
  c.agentes.push({ agente: '<img src=x onerror=alert(1)>', selo: '<script>' });
  c.sessoes[0].modelo = '<img src=x onerror=alert(1)>'; c.sessoes[0].flags = ['<script>'];
  c.sessoes[0].inicio_epoch = Infinity; c.sessoes[0].fim_epoch = 1e300;
  c.cotacao = { usd_brl: NaN, data_epoch: 1e300, fonte: '<script>' };
  c.hoje = { usd: NaN, brl: -1 }; c.avisos = ['<script>'];
  silenciarErros((erros) => { a.receber(d); assert.equal(erros.length, 0); });
  assert.deepEqual(porClasse(doc.getElementById('custo-body'), 'track').map((b) => b.children[0].style.width), ['0%', '0%', '0%', '0%', '50%', '100%']);
  assert.ok(!/NaN|Infinity|undefined|<img|<script>|onerror/.test(txt('custo-body')));
  assert.equal(doc.getElementById('custo').descendentes().some((e) => e.tag === 'script' || e.tag === 'img'), false);
});
test('custo: exceção isolada na seção e limite de 20 sessões', () => {
  const d = clone(fixture); d.custo_agentes.sessoes = Array(30).fill(d.custo_agentes.sessoes[0]);
  assert.equal(Hermes.custosAgentesDe(d).sessoes.length, 20);
  Object.defineProperty(d, 'custo_agentes', { get() { throw new Error('boom custo'); } });
  const { a, txt } = app();
  silenciarErros((erros) => { a.receber(d); assert.equal(erros.length, 1); });
  assert.match(txt('custo'), /não foi possível desenhar esta seção/);
  assert.match(txt('maquina'), /CPU/);
});
test('custo: parcial preserva os valores conhecidos em totais, agente, dia e sessão', () => {
  const d = clone(fixture), c = d.custo_agentes;
  [c.hoje, c.sete_dias, c.agentes[0].hoje, c.agentes[0].sete_dias, c.dias[0].total, c.sessoes[0]].forEach((v) => {
    v.parcial = true; v.eventos_sem_custo = 2;
  });
  c.sessoes[0].tokens.total = 1234567;
  const { a, doc, txt } = app(); a.receber(d);
  assert.match(txt('custo-body'), /Hoje: ≥ R\$ 10,00 \(parcial\) · ≥ US\$ 2,00/);
  assert.match(txt('custo-body'), /7 dias: ≥ R\$ 30,00 \(parcial\) · ≥ US\$ 6,00/);
  assert.match(txt('custo-body'), /2026-10-30 · ≥ R\$ 20,00 \(parcial\) · ≥ US\$ 4,00/);
  assert.ok(txt('custo-body').includes('1.234.567 tokens'));
  assert.deepEqual(porClasse(doc.getElementById('custo-body'), 'track').map((b) => b.children[0].style.width), ['100%', '50%']);
  c.hoje.brl = null; a.receber(d);
  assert.match(txt('custo-body'), /Hoje: R\$ sem leitura · ≥ US\$ 2,00/);
});
test('custo: strings de dinheiro mostram sem leitura, barras zeradas e parcial sem preço fica nulo', () => {
  const { a, doc, txt } = app(), d = clone(fixture), c = d.custo_agentes;
  c.hoje = { usd: '2', brl: '10', parcial: true, eventos_sem_custo: 1 };
  c.dias[0].total = { usd: '40', brl: '200' };
  c.sessoes[0].usd = null; c.sessoes[0].brl = null; c.sessoes[0].parcial = true;
  a.receber(d);
  assert.match(txt('custo-body'), /Hoje: R\$ sem leitura · US\$ sem leitura/);
  assert.match(txt('custo-body'), /2026-10-30 · R\$ sem leitura · US\$ sem leitura/);
  assert.equal(porClasse(doc.getElementById('custo-body'), 'track')[0].children[0].style.width, '0%');
  assert.ok(!txt('custo-body').includes('≥ R$ sem leitura'));
  assert.ok(!txt('custo-body').includes('≥ US$ sem leitura'));
});

/* ---------- E24: bloco "Máquina" (CPU e discos) na seção Saúde dos serviços ---------- */
const semMaq = () => { const d = clone(fixture); delete d.maquina; return d; };
const comMaq = (extra) => { const d = clone(fixture); Object.assign(d.maquina, extra); return d; };
const cartoesMaq = (doc) => doc.getElementById('maquina-grid').children;
const larguras = (doc) => Array.from(cartoesMaq(doc)).map((c) => porClasse(c, 'track')[0].children[0].style.width);
test('maquinaDe: sem a chave, "erro" e formatos errados ficam inativos; valores hostis são normalizados', () => {
  [undefined, { estado: 'erro' }, null, 'x', [], { discos: 'x' }].forEach((v) => {
    const d = semMaq(); if (v !== undefined) d.maquina = v;
    const r = Hermes.maquinaDe(d); assert.equal(r.ativo, false); assert.deepEqual(r.discos, []);
  });
  assert.equal(Hermes.maquinaDe(null).ativo, false);
  const r = Hermes.maquinaDe(fixture);
  assert.equal(r.ativo, true); assert.equal(r.cpu, 23.4); assert.equal(r.janela, 10); assert.equal(r.discos.length, 2);
  assert.deepEqual(r.discos[0], { unidade: 'C:', total: 536870912000, livre: 214748364800, usado: 60 });
  assert.equal(Hermes.maquinaDe(comMaq({ cpu_pct: null })).cpu, null);
  assert.equal(Hermes.maquinaDe(comMaq({ cpu_pct: NaN })).cpu, null);
  assert.equal(Hermes.maquinaDe(comMaq({ cpu_pct: 'alto' })).cpu, null);
  assert.equal(Hermes.maquinaDe(comMaq({ cpu_pct: -5 })).cpu, 0);
  assert.equal(Hermes.maquinaDe(comMaq({ cpu_pct: 250 })).cpu, 100);
  const h = Hermes.maquinaDe(comMaq({ discos: [{ unidade: '<img src=x onerror=alert(1)>', total_bytes: 10, livre_bytes: 5, usado_pct: 50 },
    { unidade: 'E:', total_bytes: 'x', livre_bytes: -1, usado_pct: NaN }, { unidade: 'F:', total_bytes: 100, livre_bytes: 900, usado_pct: 'y' }, null, 'G:'] }));
  assert.deepEqual(h.discos, [{ unidade: 'E:', total: 0, livre: 0, usado: 0 }, { unidade: 'F:', total: 100, livre: 100, usado: 0 }]);
  assert.equal(Hermes.maquinaDe(comMaq({ discos: [{ unidade: 'C:', total_bytes: 100, livre_bytes: 25, usado_pct: 400 }] })).discos[0].usado, 100);
});
test('maquina: render da fixture mostra CPU e dois discos, com barra limitada, sem botão e sem mexer nos serviços nem nos KPIs', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const base = app(); base.a.receber(semMaq());
  assert.equal(doc.getElementById('maquina').hidden, false); assert.equal(txt('maquina-badge'), '2 discos');
  const cs = cartoesMaq(doc); assert.equal(cs.length, 3);
  cs.forEach((c) => assert.notEqual(c.tag, 'button'));
  const t = (c) => nb(c.textContent);
  assert.match(t(cs[0]), /CPU/); assert.match(t(cs[0]), /uso 23,4%/); assert.match(t(cs[0]), /média de 10 s/);
  assert.match(t(cs[1]), /Disco C:/); assert.match(t(cs[1]), /300,0 GiB de 500,0 GiB/); assert.match(t(cs[1]), /60,0% usado/); assert.match(t(cs[1]), /livre 200,0 GiB/);
  assert.match(t(cs[2]), /Disco D:/); assert.match(t(cs[2]), /90,0% usado/);
  assert.ok(cs[2].children[0].children[1].classList.contains('bad')); assert.ok(cs[1].children[0].children[1].classList.contains('good'));
  assert.deepEqual(larguras(doc), ['23.4%', '60%', '90%']);
  assert.equal(doc.getElementById('services-grid').children.length, 5);
  ['k-serv', 'k-serv-total', 'nav-servicos'].forEach((id) => assert.equal(txt(id), base.txt(id), 'mudou ' + id));
  assert.equal(base.doc.getElementById('maquina').hidden, true);
});
test('maquina E24b: CPU a 90% ou 100% nunca fica vermelha; disco a 90% fica', () => {
  [90, 100].forEach((cpu) => {
    const { a, doc } = app(); a.receber(comMaq({ cpu_pct: cpu }));
    const cs = cartoesMaq(doc);
    assert.equal(porClasse(cs[0], 'status')[0].className, 'status good');
    assert.equal(porClasse(cs[2], 'status')[0].className, 'status bad');
  });
});
test('maquina: CPU nulo mostra "sem leitura" e barra zerada', () => {
  const { a, doc } = app(); a.receber(comMaq({ cpu_pct: null, cpu_janela_s: null }));
  const cs = cartoesMaq(doc); const t = nb(cs[0].textContent);
  assert.match(t, /sem leitura/); assert.match(t, /aguardando duas leituras/); assert.ok(!/\d,\d%/.test(t));
  assert.equal(larguras(doc)[0], '0%'); assert.equal(cs.length, 3);
});
test('maquina: sem a chave e "erro" deixam o bloco oculto e sem cartões', () => {
  [undefined, { estado: 'erro' }].forEach((v) => {
    const d = semMaq(); if (v !== undefined) d.maquina = v;
    const { a, doc } = app(); a.receber(d);
    assert.equal(doc.getElementById('maquina').hidden, true); assert.equal(cartoesMaq(doc).length, 0);
  });
});
test('maquina: valores hostis (NaN, negativo, acima de 100, texto) nunca geram largura fora de 0-100 nem elemento', () => {
  const { a, doc } = app();
  a.receber(comMaq({ cpu_pct: 250, discos: [{ unidade: 'C:', total_bytes: 100, livre_bytes: 50, usado_pct: -20 }, { unidade: 'D:', total_bytes: 100, livre_bytes: 50, usado_pct: NaN },
    { unidade: 'E:', total_bytes: 100, livre_bytes: 50, usado_pct: 'x' }, { unidade: '<img src=x onerror=alert(1)>', total_bytes: 1, livre_bytes: 1, usado_pct: 1 }] }));
  assert.equal(cartoesMaq(doc).length, 4);
  assert.deepEqual(larguras(doc), ['100%', '0%', '0%', '0%']);   // negativo, NaN e texto valem 0; a unidade hostil é descartada
  const bloco = doc.getElementById('maquina');
  assert.equal(bloco.descendentes().filter((e) => e.tag === 'img').length, 0);
  assert.ok(!nb(bloco.textContent).includes('onerror'));
});
test('maquina que lança não impede a grade de serviços nem os KPIs; o aviso fica só no bloco', () => {
  const base = app(); silenciarErros(() => base.a.receber(clone(fixture)));
  const d = clone(fixture);
  Object.defineProperty(d.maquina, 'discos', { enumerable: true, get() { throw new Error('boom maquina'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /Máquina/); assert.match(String(erros[0][1]), /boom maquina/);
  });
  ['k-serv', 'k-serv-total', 'nav-servicos', 'services-grid', 'k-trab', 'nav-agentes'].forEach((id) => assert.equal(txt(id), base.txt(id), 'mudou ' + id));
  assert.match(txt('maquina'), /não foi possível desenhar esta seção/);
});

/* ---------- E19b: o Codex num cartão só ---------- */
const contaCodexComNumeros = (d) => { const c = d.contas.lista.filter((x) => x.ferramenta === 'codex')[0]; c.situacao = 'ok'; c.barras[0].usado_pct = 33; c.barras[1].usado_pct = 70; return d; };
const codexEm = (doc, id) => doc.getElementById(id).children.filter((c) => /Codex/.test(c.textContent)).length;
const contaCodexComErro = (d) => { const c = d.contas.lista.filter((x) => x.ferramenta === 'codex')[0]; c.erro = true; c.erro_detalhe = 'fonte de sessões indisponível'; return d; };
test('unificarCodex: regras (a), (b) e (c), conta com erro, sem item local, entradas inválidas e listas recebidas intactas', () => {
  const C = (dados) => Hermes.contasDe(dados), L = (dados) => Hermes.cotasLocaisDe(dados);
  const chaves = (r) => r.locais.map((x) => x.chave);
  const semLocalDoCodex = (dados) => L(dados).filter((x) => x.chave !== 'cota_codex_local');
  const semDadoLocal = () => Object.assign(clone(fixture), { cota_codex_local: { estado: 'sem_dado', observado_epoch: null, barras: [] } });
  // (b) conta do Codex sem números e COM erro, local com dados: a conta fica
  let d = contaCodexComErro(clone(fixture)), r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 2);
  assert.deepEqual(chaves(r), ['cota_codex_local', 'cota_grok_bot', 'cota_cursor']);
  // sem item local do Codex
  d = semCotasLocais(); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.contas, C(d)); assert.deepEqual(r.locais, L(d));
  d = clone(fixture); delete d.cota_codex_local; delete d.fontes.cota_codex_local; r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.contas, C(d)); assert.deepEqual(r.locais, L(d));
  // (a) conta com números e local com dados
  d = contaCodexComNumeros(clone(fixture)); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(chaves(r), ['cota_grok_bot', 'cota_cursor']); assert.deepEqual(r.locais, semLocalDoCodex(d)); assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 2);
  // (a) conta com números e local sem_dado
  d = contaCodexComNumeros(semDadoLocal()); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.locais, semLocalDoCodex(d)); assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 2);
  // (b) fixture como está
  d = clone(fixture); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.contas, C(d).filter((c) => c.ferramenta !== 'codex')); assert.equal(r.contas.length, 1);
  assert.deepEqual(chaves(r), ['cota_codex_local', 'cota_grok_bot', 'cota_cursor']);
  // (c) conta sem números e local sem_dado
  d = semDadoLocal(); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.locais, semLocalDoCodex(d)); assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 2);
  // (c) nenhuma conta do Codex e local sem_dado
  d = semDadoLocal(); d.contas.lista = d.contas.lista.filter((c) => c.ferramenta !== 'codex'); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.contas, C(d)); assert.deepEqual(r.locais, L(d));
  // a regra (b) exige as DUAS condições no item local: estado "ok" E barras; faltando uma, vale a (c)
  const localDireto = (estado, barras) => [{ chave: 'cota_codex_local', estado: estado, barras: barras }, { chave: 'cota_grok_bot', estado: 'ok', barras: [] }];
  const umaBarra = [{ tipo: '5h', pct: 10 }];
  d = clone(fixture);
  r = Hermes.unificarCodex(C(d), localDireto('ok', []));             // "ok" sem barras: regra (c)
  assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 2); assert.deepEqual(chaves(r), ['cota_grok_bot']);
  r = Hermes.unificarCodex(C(d), localDireto('sem_dado', umaBarra)); // barras sem "ok": regra (c)
  assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 2); assert.deepEqual(chaves(r), ['cota_grok_bot']);
  r = Hermes.unificarCodex(C(d), localDireto('ok', umaBarra));       // as duas: regra (b)
  assert.equal(r.contas.length, 1); assert.deepEqual(chaves(r), ['cota_codex_local', 'cota_grok_bot']);
  // duas contas do Codex, uma com números: regra (a)
  d = contaCodexComNumeros(clone(fixture));
  const outra = clone(d.contas.lista.filter((x) => x.ferramenta === 'codex')[0]); outra.id = 'codex:ghi'; outra.situacao = 'sem dado'; outra.barras.forEach((b) => { b.usado_pct = null; });
  d.contas.lista.push(outra); r = Hermes.unificarCodex(C(d), L(d));
  assert.deepEqual(r.contas, C(d)); assert.equal(r.contas.length, 3); assert.deepEqual(r.locais, semLocalDoCodex(d));
  // entradas que não são lista
  [null, undefined, 'x'].forEach((v) => assert.deepEqual(Hermes.unificarCodex(v, v), { contas: [], locais: [] }));
  // listas recebidas não são alteradas
  d = clone(fixture); const cs = C(d), ls = L(d), csClone = clone(cs), lsClone = clone(ls);
  Hermes.unificarCodex(cs, ls); assert.deepEqual(cs, csClone); assert.deepEqual(ls, lsClone);
  d = contaCodexComNumeros(clone(fixture)); const cs2 = C(d), ls2 = L(d), cs2Clone = clone(cs2), ls2Clone = clone(ls2);
  Hermes.unificarCodex(cs2, ls2); assert.deepEqual(cs2, cs2Clone); assert.deepEqual(ls2, ls2Clone);
});
test('Codex num cartão só: com sessão aberta (conta com números) o cartão é o da conta e o observado some', () => {
  const { a, doc, txt } = app(); a.receber(contaCodexComNumeros(clone(fixture)));
  assert.equal(codexEm(doc, 'providers'), 1);
  assert.match(nb(doc.getElementById('providers').children.filter((c) => /Codex/.test(c.textContent))[0].textContent), /33%/);
  assert.equal(codexEm(doc, 'limits-local-cards'), 0);
  assert.equal(txt('limits-local-badge'), '2 fontes');
  assert.deepEqual(cartoesLocais(doc).map((c) => nb(porClasse(c, 'provider-name')[0].childNodes[0].textContent)), ['Grok Bot', 'Cursor']);
});
test('Codex num cartão só: sem sessão (fixture como está) só o observado aparece e o Claude continua em #providers', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  assert.equal(codexEm(doc, 'providers'), 0);
  assert.equal(codexEm(doc, 'limits-local-cards'), 1);
  assert.equal(txt('limits-local-badge'), '3 fontes');
  assert.match(txt('providers'), /Claude/);
});
test('Codex num cartão só: sem a variável nada muda em #providers', () => {
  const sem = app(); sem.a.receber(semCotasLocais());
  assert.equal(codexEm(sem.doc, 'providers'), 1); assert.match(sem.txt('providers'), /SEM DADOS/);
  assert.equal(sem.doc.getElementById('limits-local').hidden, true);
  const d = clone(fixture); delete d.cota_codex_local; delete d.fontes.cota_codex_local;
  const { a, doc, txt } = app(); a.receber(d);
  assert.equal(codexEm(doc, 'providers'), 1);
  assert.equal(txt('limits-local-badge'), '2 fontes');
  assert.equal(txt('providers'), sem.txt('providers'));
});
test('Codex num cartão só: fonte local com erro deixa #providers como sem a variável e não desenha o observado', () => {
  const sem = app(); sem.a.receber(semCotasLocais());
  const { a, doc, txt } = app(); a.receber(Object.assign(clone(fixture), { cota_codex_local: { estado: 'erro' } }));
  assert.equal(codexEm(doc, 'providers'), 1); assert.match(txt('providers'), /Codex.*SEM DADOS/);
  assert.equal(txt('providers'), sem.txt('providers'));
  assert.equal(codexEm(doc, 'limits-local-cards'), 0);
});
test('Codex num cartão só: nenhum dos dois com dados mantém a conta (SEM DADOS) e esconde o observado vazio', () => {
  const { a, doc, txt } = app(); a.receber(Object.assign(semCotasLocais(), { cota_codex_local: { estado: 'sem_dado', observado_epoch: null, barras: [] } }));
  assert.equal(codexEm(doc, 'providers'), 1); assert.match(txt('providers'), /SEM DADOS/);
  assert.equal(doc.getElementById('limits-local').hidden, true); assert.equal(cartoesLocais(doc).length, 0);
});
test('Codex num cartão só: atualização ao vivo troca de lugar, e KPIs e modal usam a conta escondida', () => {
  const { a, doc, txt } = app();
  const soUm = () => assert.equal(codexEm(doc, 'providers') + codexEm(doc, 'limits-local-cards'), 1);
  a.receber(clone(fixture)); soUm(); assert.equal(codexEm(doc, 'limits-local-cards'), 1);
  assert.equal(txt('k-sessao'), '40');
  a.abrirModal({ tipo: 'conta', chave: 'codex:def' }); assert.match(txt('modal-title'), /Codex/);
  a.receber(contaCodexComNumeros(clone(fixture))); soUm(); assert.equal(codexEm(doc, 'providers'), 1);
  assert.equal(txt('k-sessao'), '40');
  a.abrirModal({ tipo: 'conta', chave: 'codex:def' }); assert.match(txt('modal-title'), /Codex/);
  a.receber(clone(fixture)); soUm(); assert.equal(codexEm(doc, 'limits-local-cards'), 1);
  assert.equal(txt('k-sessao'), '40');
  a.abrirModal({ tipo: 'conta', chave: 'codex:def' }); assert.match(txt('modal-title'), /Codex/);
});
test('Codex num cartão só: conta do Codex com erro nunca é escondida (exceção declarada), e com números vale a regra (a)', () => {
  const { a, doc, txt } = app(); a.receber(contaCodexComErro(clone(fixture)));
  assert.equal(codexEm(doc, 'providers'), 1); assert.match(txt('providers'), /Codex.*ERRO.*fonte de sessões indisponível/);
  assert.equal(codexEm(doc, 'limits-local-cards'), 1); assert.equal(txt('limits-local-badge'), '3 fontes');
  const outra = app(); outra.a.receber(contaCodexComNumeros(contaCodexComErro(clone(fixture))));
  assert.equal(codexEm(outra.doc, 'providers'), 1); assert.equal(codexEm(outra.doc, 'limits-local-cards'), 0);
});

/* ---------- E20: erros de ferramenta dos agentes (bloco próprio na seção de agentes; só listas fixas, números e o id curto) ---------- */
const semErros = () => { const d = clone(fixture); delete d.erros_agentes; delete d.fontes.erros_agentes; return d; };
const comErros = (mudancas) => { const d = clone(fixture); Object.assign(d.erros_agentes, mudancas); return d; };
const linhasErro = (doc) => doc.getElementById('errors-rows').children;
const lerLinhaErro = (tr) => {          // lê a linha pelo DOM: rótulo, detalhe, classe e texto do estado, e a linha de baixo (evidência e tempo)
  const ds = tr.descendentes(), st = ds.find((e) => e.classList.contains('status'));
  return { rotulo: nb(ds.find((e) => e.tag === 'strong').textContent), detalhe: nb(ds.find((e) => e.tag === 'small' && !e.classList.contains('status-sub')).textContent),
           cls: st.className, estado: nb(st.textContent), sub: nb(ds.find((e) => e.classList.contains('status-sub')).textContent) };
};
test('errosDe: ausente, null, lista, estado "erro" e sem estado ficam inativos e zerados; a fixture dá 4 itens normalizados e as contagens', () => {
  const vazio = { ativo: false, contagens: { aberto: 0, corrigido: 0, verificado: 0, sem_tratamento: 0, contado: 0 }, itens: [] };
  const semEstado = clone(fixture); delete semEstado.erros_agentes.estado;
  [semErros(), { erros_agentes: null }, { erros_agentes: [] }, comErros({ estado: 'erro' }), semEstado, undefined, null]
    .forEach((d, i) => assert.deepEqual(Hermes.errosDe(d), vazio, 'caso ' + i));
  const r = Hermes.errosDe(clone(fixture));
  assert.equal(r.ativo, true);
  assert.deepEqual(r.contagens, { aberto: 2, corrigido: 1, verificado: 1, sem_tratamento: 3, contado: 5 });
  assert.deepEqual(r.itens, [
    { id: '0a1b2c3d4e', agente: 'claude', estado: 'aberto', ferramenta: 'bash', classe: 'codigo_saida', codigo: 2, hora: T0 - 300, evidencia: null },
    { id: '1b2c3d4e5f', agente: 'codex', estado: 'aberto', ferramenta: 'comando', classe: 'codigo_saida', codigo: 1, hora: T0 - 7200, evidencia: null },
    { id: '2c3d4e5f6a', agente: 'claude', estado: 'corrigido', ferramenta: 'edit', classe: 'edicao', codigo: null, hora: T0 - 3600, evidencia: null },
    { id: '3d4e5f6a7b', agente: 'claude', estado: 'verificado', ferramenta: 'write', classe: 'arquivo', codigo: null, hora: T0 - 600, evidencia: { tipo: 'teste', codigo: 0 } }
  ]);
});
test('errosDe: id, agente e estado ruins descartam o item; ferramenta, classe, código, hora, evidência e contagens ruins viram valor neutro; no máximo 30 itens', () => {
  const item = (extra) => Object.assign({ id: 'aaaaaaaaaa', agente: 'claude', ferramenta: 'bash', classe: 'codigo_saida', codigo: 2, hora: T0 - 60, estado: 'aberto', evidencia: null }, extra);
  const de = (itens, contagens) => Hermes.errosDe({ erros_agentes: { estado: 'ok', contagens: contagens || {}, itens: itens } });
  const um = (extra) => { const r = de([item(extra)]).itens; assert.equal(r.length, 1, JSON.stringify(extra)); return r[0]; };
  assert.deepEqual(um({}), { id: 'aaaaaaaaaa', agente: 'claude', estado: 'aberto', ferramenta: 'bash', classe: 'codigo_saida', codigo: 2, hora: T0 - 60, evidencia: null });   // controle: o item-base passa
  [{ id: '0A1B2C3D4E' }, { id: '0a1b2c3d4' }, { id: '0a1b2c3d4<' }, { agente: 'grok' }, { estado: 'contado' }, { estado: 'sem_tratamento' }, { estado: 'pendente' }]
    .forEach((extra) => assert.deepEqual(de([item(extra)]).itens, [], 'não descartou ' + JSON.stringify(extra)));
  const repetido = de([item({ codigo: 1 }), item({ codigo: 2 })]).itens;
  assert.equal(repetido.length, 1); assert.equal(repetido[0].codigo, 1);
  ['serrote', 'constructor'].forEach((v) => { assert.equal(um({ ferramenta: v }).ferramenta, 'outra'); assert.equal(um({ classe: v }).classe, 'outra'); });
  [2.5, '2', true].forEach((v) => assert.equal(um({ codigo: v }).codigo, null, 'codigo ' + JSON.stringify(v)));
  ['123', 0, -5].forEach((v) => assert.equal(um({ hora: v }).hora, null, 'hora ' + JSON.stringify(v)));
  assert.equal(um({ evidencia: { tipo: 'teste', codigo: 0 } }).evidencia, null);              // evidência só vale em item verificado
  [{ tipo: 'olhei', codigo: 0 }, { tipo: 'teste', codigo: 0.5 }, { tipo: 'comando', codigo: '0' }]
    .forEach((ev) => assert.equal(um({ estado: 'verificado', evidencia: ev }).evidencia, null, 'evidencia ' + JSON.stringify(ev)));
  assert.deepEqual(um({ estado: 'verificado', evidencia: { tipo: 'comando', codigo: 1 } }).evidencia, { tipo: 'comando', codigo: 1 });   // controle
  assert.deepEqual(de([], { aberto: -1, corrigido: NaN, verificado: '3', sem_tratamento: -2, contado: 'muitos' }).contagens,
    { aberto: 0, corrigido: 0, verificado: 0, sem_tratamento: 0, contado: 0 });
  const muitos = Array.from({ length: 40 }, (_, i) => item({ id: i.toString(16).padStart(10, '0') }));
  const r = de(muitos).itens;
  assert.equal(r.length, 30); assert.deepEqual(r.map((x) => x.id), muitos.slice(0, 30).map((x) => x.id));
});
test('erros de ferramenta: com a fixture o bloco aparece logo depois do WSL, com 4 linhas de texto fixo, e o resto da seção de agentes não muda', () => {
  assert.match(html, /<div class="presence" id="errors" role="group" aria-label="Erros de ferramenta dos agentes" hidden><div class="presence-head"><span>Erros de ferramenta<\/span><span class="badge" id="errors-badge"><\/span>/);
  const sem = app(); sem.a.receber(semErros());
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const bloco = doc.getElementById('errors'), agentes = doc.getElementById('agents');
  assert.equal(bloco.parentNode, agentes);
  assert.equal(agentes.children[agentes.children.indexOf(bloco) - 1], doc.getElementById('presence-linux'));
  assert.equal(bloco.hidden, false);
  assert.equal(txt('errors-badge'), '2 abertos'); assert.equal(doc.getElementById('errors-badge').classList.contains('warn'), true);
  assert.deepEqual(linhasErro(doc).map(lerLinhaErro), [
    { rotulo: 'Bash · código de saída 2', detalhe: 'Claude Code · id 0a1b2c3d4e', cls: 'status wait', estado: 'aberto', sub: 'há ' + Hermes.duracao(310) },
    { rotulo: 'comando · código de saída 1', detalhe: 'Codex · id 1b2c3d4e5f', cls: 'status wait', estado: 'aberto', sub: 'há ' + Hermes.duracao(7210) },
    { rotulo: 'Edit · edição não aplicada', detalhe: 'Claude Code · id 2c3d4e5f6a', cls: 'status idle', estado: 'corrigido', sub: 'há ' + Hermes.duracao(3610) },
    { rotulo: 'Write · arquivo', detalhe: 'Claude Code · id 3d4e5f6a7b', cls: 'status good', estado: 'verificado', sub: 'teste · código 0 · há ' + Hermes.duracao(610) }
  ]);
  assert.equal(txt('errors-resumo'), 'Em 30 dias: 2 abertos · 1 corrigido · 1 verificado · 3 sem tratamento · 5 só contados (permissão negada, interrupção, erro de API)');
  assert.match(txt('errors-nota'), /evidência anexada/); assert.match(txt('errors-nota'), /painel-erro ver/);
  ['agent-rows', 'agent-empty', 'presence', 'presence-rows', 'presence-linux', 'presence-linux-rows', 'k-trab', 'k-total', 'k-online-texto', 'nav-agentes'].forEach((id) => {
    assert.equal(txt(id), sem.txt(id), 'texto mudou: ' + id);
    assert.equal(doc.getElementById(id).hidden, sem.doc.getElementById(id).hidden, 'visibilidade mudou: ' + id);
  });
});
test('erros de ferramenta: sem a chave, com estado "erro" ou sem estado o bloco fica oculto, sem linhas e sem textos', () => {
  const semEstado = clone(fixture); delete semEstado.erros_agentes.estado;
  const ver = ({ doc, txt }, caso) => {
    assert.equal(doc.getElementById('errors').hidden, true, 'visível: ' + caso); assert.equal(linhasErro(doc).length, 0, 'linhas: ' + caso);
    assert.equal(txt('errors-resumo'), '', 'resumo: ' + caso); assert.equal(txt('errors-nota'), '', 'nota: ' + caso);
  };
  [semErros(), comErros({ estado: 'erro' }), semEstado].forEach((d, i) => {
    const novo = app(); novo.a.receber(clone(d)); ver(novo, 'app novo, caso ' + i);
    const depois = app(); depois.a.receber(clone(fixture)); depois.a.receber(clone(d)); ver(depois, 'depois da fixture, caso ' + i);
  });
});
test('erros de ferramenta: ativo sem itens mostra "nenhum aberto" sem destaque, nenhuma linha e o resumo com as contagens', () => {
  const d = comErros({ itens: [], contagens: { aberto: 0, corrigido: 0, verificado: 0, sem_tratamento: 2, contado: 7 } });
  const novo = app(); novo.a.receber(clone(d));
  const depois = app(); depois.a.receber(clone(fixture)); depois.a.receber(clone(d));
  [novo, depois].forEach(({ doc, txt }) => {
    assert.equal(doc.getElementById('errors').hidden, false);
    assert.equal(txt('errors-badge'), 'nenhum aberto'); assert.equal(doc.getElementById('errors-badge').classList.contains('warn'), false);
    assert.equal(linhasErro(doc).length, 0);
    assert.equal(txt('errors-resumo'), 'Em 30 dias: 0 abertos · 0 corrigidos · 0 verificados · 2 sem tratamento · 7 só contados (permissão negada, interrupção, erro de API)');
  });
});
test('erros de ferramenta: valor hostil vira texto fixo e não cria elemento; id hostil não cria linha', () => {
  const hostil = { id: 'abcdef0123', agente: 'claude', ferramenta: '<b>x</b>', classe: '<img src=x onerror=alert(1)>', codigo: null, hora: T0 - 60, estado: 'aberto', evidencia: null };
  const idHostil = Object.assign({}, hostil, { id: '<script>', ferramenta: 'bash', classe: 'codigo_saida' });
  const { a, doc } = app(); a.receber(comErros({ itens: [hostil, idHostil] }));
  const bloco = doc.getElementById('errors');
  assert.equal(linhasErro(doc).length, 1);
  const l = lerLinhaErro(linhasErro(doc)[0]);
  assert.equal(l.rotulo, 'outra ferramenta · outro erro'); assert.equal(l.detalhe, 'Claude Code · id abcdef0123');
  assert.equal(bloco.descendentes().filter((e) => ['img', 'b', 'script'].includes(e.tag)).length, 0);
  assert.ok(!/onerror|<b>|script/.test(nb(bloco.textContent)), 'texto hostil no bloco');
  bloco.descendentes().forEach((e) => Object.values(e.attrs).forEach((v) => assert.ok(!/onerror|<b>|script/.test(v), 'texto hostil em atributo')));
});
test('erros de ferramenta: atualização ao vivo tira a linha que saiu e reaproveita o mesmo elemento da que mudou de estado', () => {
  const { a, doc } = app(); a.receber(clone(fixture));
  const antes = linhasErro(doc)[0];
  assert.equal(lerLinhaErro(antes).detalhe, 'Claude Code · id 0a1b2c3d4e'); assert.equal(lerLinhaErro(antes).cls, 'status wait');
  const d = clone(fixture);
  d.erros_agentes.itens = d.erros_agentes.itens.filter((i) => i.id !== '1b2c3d4e5f');
  Object.assign(d.erros_agentes.itens.find((i) => i.id === '0a1b2c3d4e'), { estado: 'verificado', evidencia: { tipo: 'comando', codigo: 1 } });
  a.receber(d);
  const linhas = linhasErro(doc);
  assert.equal(linhas.length, 3);
  assert.deepEqual(linhas.map((tr) => lerLinhaErro(tr).detalhe), ['Claude Code · id 0a1b2c3d4e', 'Claude Code · id 2c3d4e5f6a', 'Claude Code · id 3d4e5f6a7b']);
  assert.equal(linhas[0], antes);
  const agora = lerLinhaErro(antes);
  assert.equal(agora.cls, 'status good'); assert.equal(agora.estado, 'verificado');
  assert.equal(agora.sub, 'comando · código 1 · há ' + Hermes.duracao(310));
});
test('erros de ferramenta que lançam não derrubam a lista de agentes nem o bloco do WSL; a leitura boa seguinte desenha as 4 linhas', () => {
  const base = app(); silenciarErros(() => base.a.receber(semErros()));
  const d = clone(fixture);
  Object.defineProperty(d.erros_agentes, 'itens', { enumerable: true, get() { throw new Error('boom erros'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /erros de ferramenta/); assert.match(String(erros[0][1]), /boom erros/);
  });
  ['agent-rows', 'presence-linux'].forEach((id) => {
    assert.equal(txt(id), base.txt(id), 'texto mudou: ' + id);
    assert.equal(doc.getElementById(id).hidden, base.doc.getElementById(id).hidden, 'visibilidade mudou: ' + id);
  });
  silenciarErros(() => a.receber(clone(fixture)));
  assert.equal(linhasErro(doc).length, 4);
});

/* ---------- E21: outros agentes por sessão (bloco próprio na seção de agentes; só listas fixas, números, modelo filtrado e a chave curta) ---------- */
const CHAVES_EXTRA = ['sessoes_grok', 'sessoes_hermes', 'chat_cursor'];
const semExtras = () => { const d = clone(fixture); CHAVES_EXTRA.forEach((k) => { delete d[k]; delete d.fontes[k]; }); return d; };
const linhasExtra = (doc) => doc.getElementById('extra-agents-rows').children;
test('outrosAgentesDe: ausente, null, {}, as três como lista, como null e com estado "erro" ficam inativos; só sem_dado fica inativo com o cursor sem_dado; a fixture dá 3 sessões e o último chat', () => {
  const vazio = { ativo: false, sessoes: [], cursor: { estado: null, ultimo: null, chats: 0 } };
  const as3 = (f) => Object.fromEntries(CHAVES_EXTRA.map((k) => [k, f(k)]));
  const comEstado = (estado) => { const d = clone(fixture); CHAVES_EXTRA.forEach((k) => { d[k].estado = estado; }); return d; };
  [undefined, null, {}, semExtras(), as3(() => []), as3(() => null), as3(() => ({ estado: 'erro' })), comEstado('erro')]
    .forEach((d, i) => assert.deepEqual(Hermes.outrosAgentesDe(d), vazio, 'caso ' + i));
  const soSemDado = { ativo: false, sessoes: [], cursor: { estado: 'sem_dado', ultimo: null, chats: 0 } };
  assert.deepEqual(Hermes.outrosAgentesDe(as3(() => ({ estado: 'sem_dado' }))), soSemDado);
  assert.deepEqual(Hermes.outrosAgentesDe(comEstado('sem_dado')), soSemDado);       // sem_dado com sessões e data no bloco: nada passa
  assert.deepEqual(Hermes.outrosAgentesDe(clone(fixture)), {
    ativo: true,
    sessoes: [
      { id: 'grok:a1b2c3d4e5', ferramenta: 'grok', estado: 'trabalhando', origem: null, modelo: 'grok-4.7', contexto: 25, tokens: 2000000, chamadas: 34, ultima: 'terminal', atividade: 1793433692 },
      { id: 'grok:b2c3d4e5f6', ferramenta: 'grok', estado: 'ocioso', origem: null, modelo: null, contexto: null, tokens: null, chamadas: null, ultima: null, atividade: 1793426512 },
      { id: 'hermes:c3d4e5f6a7', ferramenta: 'hermes', estado: 'ativa', origem: 'whatsapp', modelo: 'claude-opus-5-5', contexto: null, tokens: 52000, chamadas: 1, ultima: 'web', atividade: 1793433112 }
    ],
    cursor: { estado: 'ok', ultimo: 1793347312, chats: 1 }
  });
});
test('outrosAgentesDe: chave, estado e item ruins descartam a sessão; origem, modelo, tokens, chamadas, contexto, horário e última ferramenta ruins viram valor neutro; no máximo 20 do Grok e 50 do Hermes', () => {
  const sG = (extra) => Object.assign({ chave: 'aaaaaaaaaa', ferramenta: 'grok', estado: 'trabalhando', modelo: 'grok-4.7', contexto_pct: 50, tokens: { entrada: 100, saida: 20 }, chamadas: 3, ultima_ferramenta: 'busca', atividade_epoch: T0 - 60 }, extra);
  const sH = (extra) => Object.assign({ chave: 'aaaaaaaaaa', ferramenta: 'hermes', estado: 'ativa', origem: 'cli', modelo: 'claude-opus-5-5', tokens: { entrada: 100, saida: 20 }, chamadas: 3, ultima_ferramenta: 'busca', atividade_epoch: T0 - 60 }, extra);
  const de = (grok, hermes) => Hermes.outrosAgentesDe({ sessoes_grok: { estado: 'ok', sessoes: grok || [] }, sessoes_hermes: { estado: 'ok', sessoes: hermes || [] } }).sessoes;
  const umG = (extra) => { const r = de([sG(extra)]); assert.equal(r.length, 1, 'grok ' + JSON.stringify(extra)); return r[0]; };
  const umH = (extra) => { const r = de([], [sH(extra)]); assert.equal(r.length, 1, 'hermes ' + JSON.stringify(extra)); return r[0]; };
  assert.deepEqual(umG({}), { id: 'grok:aaaaaaaaaa', ferramenta: 'grok', estado: 'trabalhando', origem: null, modelo: 'grok-4.7', contexto: 50, tokens: 120, chamadas: 3, ultima: 'busca', atividade: T0 - 60 });   // controle
  assert.deepEqual(umH({}), { id: 'hermes:aaaaaaaaaa', ferramenta: 'hermes', estado: 'ativa', origem: 'cli', modelo: 'claude-opus-5-5', contexto: null, tokens: 120, chamadas: 3, ultima: 'busca', atividade: T0 - 60 });   // controle
  [{ chave: 'AAAAAAAAAA' }, { chave: 'aaaaaaaaa' }, { chave: 'aaaaaaaaaaa' }, { chave: 'aaaaaaaaa<' }, { chave: 1234567890 }, { chave: null },
   { estado: 'ativa' }, { estado: '__proto__' }, { estado: 'constructor' }, { estado: null }]
    .forEach((extra) => assert.deepEqual(de([sG(extra)]), [], 'grok não descartou ' + JSON.stringify(extra)));
  [{ chave: 'AAAAAAAAAA' }, { chave: 'aaaaaaaaa<' }, { estado: 'trabalhando' }, { estado: 'esperando' }, { estado: '__proto__' }, { estado: 'constructor' }]
    .forEach((extra) => assert.deepEqual(de([], [sH(extra)]), [], 'hermes não descartou ' + JSON.stringify(extra)));
  const repG = de([sG({ chamadas: 1 }), sG({ chamadas: 2 })]), repH = de([], [sH({ chamadas: 1 }), sH({ chamadas: 2 })]);
  assert.equal(repG.length, 1); assert.equal(repG[0].chamadas, 1); assert.equal(repH.length, 1); assert.equal(repH[0].chamadas, 1);
  assert.deepEqual(de([null, 'aaaaaaaaaa', 7, [sG({})], sG({ chave: 'bbbbbbbbbb' })]).map((s) => s.id), ['grok:bbbbbbbbbb']);   // item que não é objeto: sem linha
  assert.deepEqual(de([sG({})], [sH({})]).map((s) => s.id), ['grok:aaaaaaaaaa', 'hermes:aaaaaaaaaa']);                           // mesma chave nas duas ferramentas: duas linhas
  ['telegram', '__proto__', 'constructor', null, 5].forEach((v) => assert.equal(umH({ origem: v }).origem, 'outra', 'origem ' + JSON.stringify(v)));
  ['Grok-4', 'grok 4', 42, '-grok', 'a'.repeat(65)].forEach((v) => {
    assert.equal(umG({ modelo: v }).modelo, null, 'modelo grok ' + JSON.stringify(v)); assert.equal(umH({ modelo: v }).modelo, null, 'modelo hermes ' + JSON.stringify(v));
  });
  assert.equal(umG({ modelo: 'outro modelo' }).modelo, 'outro modelo'); assert.equal(umH({ modelo: 'outro modelo' }).modelo, 'outro modelo');
  [{ entrada: -1, saida: 20 }, { entrada: 1.5, saida: 20 }, { entrada: '100', saida: 20 }].forEach((t) => {
    assert.equal(umG({ tokens: t }).tokens, null, 'tokens grok ' + JSON.stringify(t)); assert.equal(umH({ tokens: t }).tokens, null, 'tokens hermes ' + JSON.stringify(t));
  });
  [-1, 2.5, '3', true].forEach((v) => assert.equal(umG({ chamadas: v }).chamadas, null, 'chamadas ' + JSON.stringify(v)));
  [-0.5, 100.5, '50', true].forEach((v) => assert.equal(umG({ contexto_pct: v }).contexto, null, 'contexto ' + JSON.stringify(v)));
  assert.equal(umG({ contexto_pct: 0 }).contexto, 0); assert.equal(umG({ contexto_pct: 100 }).contexto, 100);   // controle: as pontas valem
  [0, -5, '1793433652', true].forEach((v) => assert.equal(umG({ atividade_epoch: v }).atividade, null, 'atividade ' + JSON.stringify(v)));
  ['serrote', '__proto__', 'constructor', null].forEach((v) => assert.equal(umG({ ultima_ferramenta: v }).ultima, null, 'ultima ' + JSON.stringify(v)));
  const hex = (i) => i.toString(16).padStart(10, '0');
  const g25 = Array.from({ length: 25 }, (_, i) => sG({ chave: hex(i) })), h55 = Array.from({ length: 55 }, (_, i) => sH({ chave: hex(i) }));
  const lim = de(g25, h55);
  assert.deepEqual(lim.filter((s) => s.ferramenta === 'grok').map((s) => s.id), g25.slice(0, 20).map((s) => 'grok:' + s.chave));
  assert.deepEqual(lim.filter((s) => s.ferramenta === 'hermes').map((s) => s.id), h55.slice(0, 50).map((s) => 'hermes:' + s.chave));
  assert.deepEqual(Hermes.outrosAgentesDe({ sessoes_grok: { estado: 'ok', sessoes: [] } }), { ativo: true, sessoes: [], cursor: { estado: null, ultimo: null, chats: 0 } });
  assert.deepEqual(Hermes.outrosAgentesDe({ chat_cursor: { estado: 'ok', ultimo_chat_epoch: '1793347312', chats: 1 } }), { ativo: false, sessoes: [], cursor: { estado: null, ultimo: null, chats: 0 } });
  const semCursor = Hermes.outrosAgentesDe({});
  assert.deepStrictEqual(Hermes.outrosAgentesDe({ chat_cursor: { estado: 'ok', ultimo_chat_epoch: 0, chats: 3 } }), semCursor);
  assert.deepStrictEqual(Hermes.outrosAgentesDe({ chat_cursor: { estado: 'ok', ultimo_chat_epoch: -5, chats: 3 } }), semCursor);
});
test('outros agentes: com a fixture o bloco aparece logo depois do de erros, com 3 linhas de texto fixo e o rodapé do cursor-agent, e o resto da seção de agentes não muda', () => {
  assert.ok(html.includes('<div class="presence" id="extra-agents" role="group" aria-label="Sessões de outros agentes" hidden>'));
  const sem = app(); sem.a.receber(semExtras());
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const bloco = doc.getElementById('extra-agents'), agentes = doc.getElementById('agents');
  assert.equal(bloco.parentNode, agentes);
  assert.equal(agentes.children[agentes.children.indexOf(bloco) - 1], doc.getElementById('errors'));
  assert.equal(bloco.hidden, false);
  assert.equal(txt('extra-agents-badge'), '3 sessões');
  assert.deepEqual(linhasExtra(doc).map(lerLinhaErro), [
    { rotulo: 'Grok CLI', detalhe: 'grok-4.7 · ' + Hermes.fmtTokens(2000000), cls: 'status work', estado: 'trabalhando', sub: '34 chamadas · última: terminal · contexto 25% · atividade há ' + Hermes.duracao(30) },
    { rotulo: 'Grok CLI', detalhe: '', cls: 'status idle', estado: 'ocioso', sub: 'atividade há ' + Hermes.duracao(7210) },
    { rotulo: 'Hermes · WhatsApp', detalhe: 'claude-opus-5-5 · ' + Hermes.fmtTokens(52000), cls: 'status good', estado: 'ativa', sub: '1 chamada · última: web · atividade há ' + Hermes.duracao(610) }
  ]);
  linhasExtra(doc).map(lerLinhaErro).forEach((l, i) => assert.ok(l.detalhe.length <= 40, 'detalhe com mais de 40 caracteres na linha ' + i));
  assert.equal(txt('extra-agents-cursor'), 'cursor-agent: último chat há ' + Hermes.duracao(86410) + ' · 1 chat local');
  ['agent-rows', 'agent-empty', 'presence', 'presence-rows', 'presence-linux', 'presence-linux-rows', 'errors', 'errors-rows', 'errors-badge', 'errors-resumo', 'k-trab', 'k-total', 'k-online-texto', 'nav-agentes'].forEach((id) => {
    assert.equal(txt(id), sem.txt(id), 'texto mudou: ' + id);
    assert.equal(doc.getElementById(id).hidden, sem.doc.getElementById(id).hidden, 'visibilidade mudou: ' + id);
  });
});
test('outros agentes: sem as três chaves, com as três em "erro" e com as três em sem_dado o bloco fica oculto, sem linhas e sem rodapé', () => {
  const comEstado = (estado) => { const d = clone(fixture); CHAVES_EXTRA.forEach((k) => { d[k].estado = estado; }); return d; };
  const ver = ({ doc, txt }, caso) => {
    assert.equal(doc.getElementById('extra-agents').hidden, true, 'visível: ' + caso); assert.equal(linhasExtra(doc).length, 0, 'linhas: ' + caso);
    assert.equal(txt('extra-agents-cursor'), '', 'rodapé: ' + caso);
  };
  [semExtras(), comEstado('erro'), comEstado('sem_dado')].forEach((d, i) => {
    const novo = app(); novo.a.receber(clone(d)); ver(novo, 'app novo, caso ' + i);
    const depois = app(); depois.a.receber(clone(fixture)); depois.a.receber(clone(d)); ver(depois, 'depois da fixture, caso ' + i);
  });
});
test('outros agentes: ativo sem sessões mostra "nenhuma sessão", nenhuma linha e o rodapé conforme o estado do cursor-agent', () => {
  const d = clone(fixture);
  d.sessoes_grok = { estado: 'ok', sessoes: [] }; d.sessoes_hermes = { estado: 'erro' }; d.chat_cursor = { estado: 'sem_dado' };
  const novo = app(); novo.a.receber(clone(d));
  const depois = app(); depois.a.receber(clone(fixture)); depois.a.receber(clone(d));
  [novo, depois].forEach(({ a, doc, txt }, i) => {
    assert.equal(doc.getElementById('extra-agents').hidden, false, 'oculto: caso ' + i);
    assert.equal(txt('extra-agents-badge'), 'nenhuma sessão'); assert.equal(linhasExtra(doc).length, 0);
    assert.equal(txt('extra-agents-cursor'), 'cursor-agent: nenhum chat local');
    const e = clone(d); e.chat_cursor = { estado: 'erro' }; a.receber(e);
    assert.equal(txt('extra-agents-cursor'), '', 'rodapé com o cursor-agent em erro: caso ' + i);
    assert.equal(doc.getElementById('extra-agents').hidden, false, 'oculto com o cursor-agent em erro: caso ' + i);
  });
});
test('outros agentes: valor hostil vira texto fixo e não cria elemento; chave e estado hostis não criam linha', () => {
  const H = '<img src=x onerror=alert(1)>';
  const d = clone(fixture);
  d.sessoes_grok.sessoes = [{ chave: H, ferramenta: 'grok', estado: 'trabalhando', atividade_epoch: T0 - 60 }, { chave: 'ddddeeeeff', ferramenta: 'grok', estado: H, atividade_epoch: T0 - 60 }];
  d.sessoes_hermes.sessoes = [
    { chave: 'deadbeef01', ferramenta: 'hermes', estado: 'ativa', origem: H, modelo: H, contexto_pct: '50', tokens: [1, 2], chamadas: 2, ultima_ferramenta: H, atividade_epoch: T0 - 60 },
    { chave: H, ferramenta: 'hermes', estado: 'ativa', origem: 'cli', atividade_epoch: T0 - 60 },
    { chave: 'deadbeef02', ferramenta: 'hermes', estado: H, origem: 'cli', atividade_epoch: T0 - 60 }
  ];
  const { a, doc, txt } = app();
  silenciarErros((erros) => { a.receber(d); assert.equal(erros.length, 0, 'alguma seção falhou com o valor hostil'); });
  const bloco = doc.getElementById('extra-agents');
  assert.equal(bloco.hidden, false); assert.equal(linhasExtra(doc).length, 1); assert.equal(txt('extra-agents-badge'), '1 sessão');
  assert.deepEqual(lerLinhaErro(linhasExtra(doc)[0]), { rotulo: 'Hermes · outra origem', detalhe: '', cls: 'status good', estado: 'ativa', sub: '2 chamadas · atividade há ' + Hermes.duracao(70) });
  assert.equal(bloco.descendentes().filter((e) => e.tag === 'img').length, 0);
  assert.ok(!/onerror|<img|alert/.test(nb(bloco.textContent)), 'texto hostil no bloco');
  bloco.descendentes().forEach((e) => Object.values(e.attrs).forEach((v) => assert.ok(!/onerror|<img|alert/.test(v), 'texto hostil em atributo')));
});
test('outros agentes: atualização ao vivo tira a linha que saiu e reaproveita o mesmo elemento da que mudou de estado', () => {
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const antes = linhasExtra(doc)[0];
  assert.equal(lerLinhaErro(antes).cls, 'status work'); assert.equal(lerLinhaErro(antes).estado, 'trabalhando');
  const d = clone(fixture);
  d.sessoes_grok.sessoes = d.sessoes_grok.sessoes.filter((s) => s.chave !== 'b2c3d4e5f6');
  d.sessoes_grok.sessoes.find((s) => s.chave === 'a1b2c3d4e5').estado = 'esperando';
  a.receber(d);
  const linhas = linhasExtra(doc);
  assert.equal(linhas.length, 2);
  assert.equal(linhas[0], antes);
  assert.deepEqual(linhas.map((tr) => lerLinhaErro(tr).rotulo), ['Grok CLI', 'Hermes · WhatsApp']);
  const agora = lerLinhaErro(antes);
  assert.equal(agora.cls, 'status wait'); assert.equal(agora.estado, 'esperando permissão');
  assert.equal(txt('extra-agents-badge'), '2 sessões');
});
test('outros agentes que lançam não derrubam a lista de agentes nem o bloco de erros; a leitura boa seguinte desenha as 3 linhas', () => {
  const base = app(); base.a.receber(clone(fixture));
  const d = clone(fixture);
  Object.defineProperty(d.sessoes_grok, 'sessoes', { enumerable: true, get() { throw new Error('boom extras'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /outros agentes/); assert.match(String(erros[0][1]), /boom extras/);
  });
  ['agent-rows', 'errors'].forEach((id) => {
    assert.equal(txt(id), base.txt(id), 'texto mudou: ' + id);
    assert.equal(doc.getElementById(id).hidden, base.doc.getElementById(id).hidden, 'visibilidade mudou: ' + id);
  });
  silenciarErros(() => a.receber(clone(fixture)));
  assert.equal(linhasExtra(doc).length, 3);
});

/* ---------- E22: rede por programa ---------- */
const semRede = () => { const d = clone(fixture); delete d.rede_uso; delete d.fontes.rede_uso; return d; };
const comRede = (mudancas) => { const d = clone(fixture); Object.assign(d.rede_uso, mudancas); return d; };
const linhasRede = (doc) => doc.getElementById('network-rows').children;
const parRede = (p) => ({ local: [0, 0], privada: [0, 0], tailnet: [0, 0], internet: [0, 0], ...p });
const progRede = (produto, extra = {}) => ({ produto, rotulo: 'Programa ' + produto, familia: 'claude', tipo: 'cli', conexoes: 1, janela: parRede({ internet: [10, 10] }), total: parRede({}), ...extra });
const REDE_VAZIA = { estado: null, itens: [], cpu: null, eventos: null, perdidos: 0, retoma: null, sinal: null, motivo: null, noTeto: false, gerado: null, desde: null };
test('fmtBytes: zero, negativo, NaN e texto dão "0 B"; base 1.000 com uma casa e vírgula decimal até TB', () => {
  [0, -1, NaN, '1500', undefined, null, Infinity, true].forEach((v) => assert.equal(Hermes.fmtBytes(v), '0 B', 'valor ' + String(v)));
  assert.equal(Hermes.fmtBytes(999), '999 B'); assert.equal(Hermes.fmtBytes(1000), '1 kB'); assert.equal(Hermes.fmtBytes(1500), '1,5 kB');
  assert.equal(Hermes.fmtBytes(28000000), '28 MB'); assert.equal(Hermes.fmtBytes(2000000000), '2 GB'); assert.equal(Hermes.fmtBytes(3500000000000), '3,5 TB');
});
test('redeDe: ausente, null, lista, desligado e estado desconhecido dão estado null sem itens; a fixture dá 3 itens na ordem de bytes, com outros por último', () => {
  [semRede(), { rede_uso: null }, { rede_uso: [] }, { rede_uso: 'ativo' }, comRede({ estado: 'desligado' }), comRede({ estado: 'serrote' }), comRede({ estado: '__proto__' }), undefined, null]
    .forEach((d, i) => assert.deepStrictEqual(Hermes.redeDe(d), REDE_VAZIA, 'caso ' + i));
  const r = Hermes.redeDe(clone(fixture));
  assert.deepStrictEqual({ ...r, itens: null }, { estado: 'ativo', itens: null, cpu: 0.4, eventos: 1236, perdidos: 0, retoma: null, sinal: null, motivo: null, noTeto: false, gerado: 1793433717, desde: 1793430112 });
  assert.deepStrictEqual(r.itens, [
    { id: 'claude_cli', rotulo: 'Claude Code CLI', familia: 'claude', conexoes: 3, enviados: 1500000, recebidos: 28000000, destinos: [{ cat: 'internet', bytes: 29500000 }], totalEnv: 90000000, totalRec: 2000000000 },
    { id: 'grok_cli', rotulo: 'Grok CLI', familia: 'grok', conexoes: 1, enviados: 302000, recebidos: 500, destinos: [{ cat: 'tailnet', bytes: 300000 }, { cat: 'local', bytes: 2500 }], totalEnv: 302000, totalRec: 500 },
    { id: 'outros', rotulo: 'Outros programas', familia: 'outra', conexoes: 42, enviados: 1205000, recebidos: 5000000, totalEnv: 1205000, totalRec: 5000000,
      destinos: [{ cat: 'internet', bytes: 4200000 }, { cat: 'local', bytes: 2000000 }, { cat: 'privada', bytes: 5000 }] }
  ]);
});
test('redeDe: produto, rótulo, família, par, conexões e motivo fora do formato; 40 programas no máximo; outros zerado não entra; ordem por bytes com empate pelo arquivo', () => {
  const de = (programas, extra = {}) => Hermes.redeDe(comRede({ programas, outros: { conexoes: 0, janela: parRede({}) }, ...extra }));
  const ids = (r) => r.itens.map((x) => x.id);
  const r1 = de([null, [], 'texto', progRede('a b'), progRede('<img>'), progRede('Maiusculo'), progRede(''), progRede('x'.repeat(41)), progRede(7), progRede('outros'),
                 progRede('ok_1'), progRede('ok_1', { rotulo: 'repetido' }), progRede('__proto__'), progRede('__proto__', { rotulo: 'repetido' }), progRede('y'.repeat(40))]);
  assert.deepEqual(ids(r1), ['ok_1', '__proto__', 'y'.repeat(40)]);
  assert.deepEqual(r1.itens.map((x) => x.rotulo), ['Programa ok_1', 'Programa __proto__', 'Programa ' + 'y'.repeat(40)]);
  assert.deepEqual(ids(de([progRede('r0', { rotulo: '' }), progRede('r1', { rotulo: 'x'.repeat(61) }), progRede('r2', { rotulo: 42 }), progRede('r3', { rotulo: 'x'.repeat(60) })])), ['r3']);
  ['serrote', '__proto__', null, 3, 'Claude'].forEach((f) => assert.equal(de([progRede('p', { familia: f })]).itens[0].familia, 'outra', 'familia ' + String(f)));
  assert.equal(de([progRede('p', { familia: 'maestri' })]).itens[0].familia, 'maestri');
  const ruins = de([progRede('p', { janela: { local: [1, 2, 3], privada: [-1, 5], tailnet: ['7', 7], internet: [1.5, 2] } }),
                    progRede('q', { janela: { local: [true, 1], privada: [NaN, 1], tailnet: { 0: 1, 1: 2 }, internet: [100, 0] } }), progRede('z', { janela: [[1, 1]] })]).itens;
  assert.deepEqual(ruins.map((x) => [x.id, x.enviados, x.recebidos, x.destinos]), [['q', 100, 0, [{ cat: 'internet', bytes: 100 }]], ['p', 0, 0, []], ['z', 0, 0, []]]);
  [-1, 1.5, '3', true, null].forEach((c) => assert.equal(de([progRede('p', { conexoes: c })]).itens[0].conexoes, 0, 'conexoes ' + String(c)));
  assert.equal(de([progRede('p', { conexoes: 5 })]).itens[0].conexoes, 5);
  const iguais = de([progRede('p', { janela: { local: [1, 0], privada: [0, 1], tailnet: [1, 0], internet: [0, 1] } })]).itens[0];
  assert.deepEqual(iguais.destinos.map((x) => x.cat), ['local', 'privada', 'tailnet', 'internet']);
  ['sem_elevacao', '__proto__', 42, null].forEach((mo) => assert.equal(de([], { estado: 'erro_captura', motivo: mo }).motivo, 'outro', 'motivo ' + String(mo)));
  assert.equal(de([], { estado: 'erro_captura' }).motivo, 'outro');
  ['sessao', 'provedor', 'consumidor', 'outro'].forEach((mo) => assert.equal(de([], { estado: 'erro_captura', motivo: mo }).motivo, mo));
  const p45 = Array.from({ length: 45 }, (_, i) => progRede('p' + i, { janela: parRede({ internet: [i, 0] }) }));
  assert.deepEqual(ids(de(p45)), Array.from({ length: 40 }, (_, i) => 'p' + (44 - i)));
  assert.deepEqual(ids(de([progRede('a')])), ['a']);
  assert.deepEqual(ids(de([progRede('a')], { outros: { conexoes: 2, janela: parRede({}) } })), ['a', 'outros']);
  assert.deepEqual(ids(de([progRede('a')], { outros: { conexoes: 0, janela: parRede({ local: [0, 1] }) } })), ['a', 'outros']);
  [null, [], 'x', { conexoes: -3, janela: parRede({ local: [-1, 1] }) }].forEach((o) => assert.deepEqual(ids(de([progRede('a')], { outros: o })), ['a'], 'outros ' + JSON.stringify(o)));
  assert.deepEqual(ids(de([progRede('b1', { janela: parRede({ local: [5, 0] }) }), progRede('a2', { janela: parRede({ internet: [0, 9] }) }),
                           progRede('c3', { janela: parRede({ tailnet: [2, 3] }) }), progRede('d4', { janela: parRede({ privada: [9, 0] }) })])), ['a2', 'd4', 'b1', 'c3']);
});
test('rede: com a fixture o bloco aparece logo depois do de outros agentes, com selo, 3 linhas e a nota, e o resto da seção de agentes não muda', () => {
  assert.ok(html.includes('<div class="presence" id="network" role="group" aria-label="Uso de rede por programa" hidden>'));
  const sem = app(); sem.a.receber(semRede());
  const { a, doc, txt } = app(); a.receber(clone(fixture));
  const bloco = doc.getElementById('network'), agentes = doc.getElementById('agents');
  assert.equal(bloco.parentNode, agentes);
  assert.equal(agentes.children[agentes.children.indexOf(bloco) - 1], doc.getElementById('extra-agents'));
  assert.equal(bloco.hidden, false);
  assert.equal(txt('network-badge'), 'ativa');
  assert.deepEqual(linhasRede(doc).map(lerLinhaErro), [
    { rotulo: 'Claude Code CLI', detalhe: '3 conexões', cls: 'status good', estado: '↑ ' + Hermes.fmtBytes(1500000) + ' · ↓ ' + Hermes.fmtBytes(28000000), sub: 'internet ' + Hermes.fmtBytes(29500000) },
    { rotulo: 'Grok CLI', detalhe: '1 conexão', cls: 'status good', estado: '↑ ' + Hermes.fmtBytes(302000) + ' · ↓ ' + Hermes.fmtBytes(500), sub: 'tailnet ' + Hermes.fmtBytes(300000) + ' · local ' + Hermes.fmtBytes(2500) },
    { rotulo: 'Outros programas', detalhe: '42 conexões', cls: 'status good', estado: '↑ ' + Hermes.fmtBytes(1205000) + ' · ↓ ' + Hermes.fmtBytes(5000000),
      sub: 'internet ' + Hermes.fmtBytes(4200000) + ' · local ' + Hermes.fmtBytes(2000000) + ' · rede privada ' + Hermes.fmtBytes(5000) }
  ]);
  assert.equal(txt('network-nota'), nb('Captura ativa · 0,4% de um núcleo · ' + (1236).toLocaleString('pt-BR') + ' eventos por segundo'));
  ['agent-rows', 'presence', 'presence-linux', 'errors', 'errors-rows', 'extra-agents', 'extra-agents-rows', 'k-trab', 'k-total', 'k-online-texto', 'nav-agentes'].forEach((id) => {
    assert.equal(txt(id), sem.txt(id), 'texto mudou: ' + id);
    assert.equal(doc.getElementById(id).hidden, sem.doc.getElementById(id).hidden, 'visibilidade mudou: ' + id);
  });
});
test('rede: pausado por carga, parado, encerrado e erro de captura mostram selo e nota próprios; só a pausa mantém as linhas', () => {
  const ver = (mud, selo, nota, linhas, caso) => {
    const { a, doc, txt } = app(); a.receber(comRede(mud));
    assert.equal(doc.getElementById('network').hidden, false, 'oculto: ' + caso);
    assert.equal(txt('network-badge'), selo, 'selo: ' + caso); assert.equal(txt('network-nota'), nb(nota), 'nota: ' + caso);
    assert.equal(linhasRede(doc).length, linhas, 'linhas: ' + caso);
  };
  ver({ estado: 'pausado_carga', retoma_em_epoch: T0 + 70 }, 'pausada por carga', 'Captura pausada por carga; nova tentativa em ' + Hermes.duracao(60), 3, 'pausa');
  ver({ estado: 'pausado_carga', retoma_em_epoch: T0 + 5 }, 'pausada por carga', 'Captura pausada por carga', 3, 'pausa vencida');
  ver({ estado: 'pausado_carga', retoma_em_epoch: 'amanhã' }, 'pausada por carga', 'Captura pausada por carga', 3, 'pausa sem retoma válido');
  ver({ estado: 'parado', ultimo_sinal_epoch: T0 - 110 }, 'parada', 'Captura sem sinal há ' + Hermes.duracao(120), 0, 'parado');
  ver({ estado: 'encerrado' }, 'encerrada', 'Captura encerrada', 0, 'encerrado');
  ['sessao', 'provedor', 'consumidor'].forEach((mo) => ver({ estado: 'erro_captura', motivo: mo }, 'erro', 'Não foi possível abrir a sessão de captura', 0, mo));
  [{ motivo: 'sem_elevacao' }, { motivo: 'outro' }, { motivo: '__proto__' }, {}, { motivo: 42 }, { motivo: null }]
    .forEach((mo) => ver({ estado: 'erro_captura', ...mo }, 'erro', 'A captura parou com erro', 0, 'motivo ' + JSON.stringify(mo)));
});
test('rede: sem a chave, desligado e a chave como lista deixam o bloco oculto, sem linhas, selo e nota, num app novo e depois da fixture', () => {
  const ver = ({ doc, txt }, caso) => {
    assert.equal(doc.getElementById('network').hidden, true, 'visível: ' + caso); assert.equal(linhasRede(doc).length, 0, 'linhas: ' + caso);
    assert.equal(txt('network-badge'), '', 'selo: ' + caso); assert.equal(txt('network-nota'), '', 'nota: ' + caso);
  };
  [semRede(), Object.assign(clone(fixture), { rede_uso: { estado: 'desligado' } }), Object.assign(clone(fixture), { rede_uso: [clone(fixture.rede_uso)] })].forEach((d, i) => {
    const novo = app(); novo.a.receber(clone(d)); ver(novo, 'app novo, caso ' + i);
    const depois = app(); depois.a.receber(clone(fixture)); depois.a.receber(clone(d)); ver(depois, 'depois da fixture, caso ' + i);
  });
});
test('rede: programa sem tráfego fica ocioso; rótulo hostil vira texto literal sem criar elemento; eventos perdidos e teto entram na nota', () => {
  const H = '<img src=x onerror=alert(1)>';
  const d = clone(fixture);
  d.rede_uso.programas = [progRede('quieto', { rotulo: 'Programa quieto', conexoes: 0, janela: parRede({}) }), progRede('hostil', { rotulo: H, familia: H, janela: parRede({ internet: [10, 0] }) })];
  d.rede_uso.perdidos = 7; d.rede_uso.conexoes_no_teto = true;
  const { a, doc, txt } = app();
  silenciarErros((erros) => { a.receber(d); assert.equal(erros.length, 0, 'alguma seção falhou com o valor hostil'); });
  const bloco = doc.getElementById('network'), linhas = linhasRede(doc).map(lerLinhaErro);
  assert.equal(linhas.length, 3);
  assert.deepEqual(linhas[0], { rotulo: H, detalhe: '1 conexão', cls: 'status good', estado: '↑ 10 B · ↓ 0 B', sub: 'internet 10 B' });
  assert.deepEqual(linhas[1], { rotulo: 'Programa quieto', detalhe: '0 conexões', cls: 'status idle', estado: '↑ 0 B · ↓ 0 B', sub: 'sem tráfego no último minuto' });
  assert.equal(linhas[2].rotulo, 'Outros programas');
  assert.equal(bloco.descendentes().filter((e) => e.tag === 'img').length, 0);
  bloco.descendentes().forEach((e) => Object.values(e.attrs).forEach((v) => assert.ok(!/onerror|<img|alert/.test(v), 'texto hostil em atributo')));
  assert.ok(txt('network-nota').endsWith(' · 7 eventos perdidos · contagem de conexões no teto'), txt('network-nota'));
  [1, 'true', 'sim', {}, []].forEach((valor) => {
    const dv = clone(fixture); dv.rede_uso.conexoes_no_teto = valor;
    assert.equal(Hermes.redeDe(dv).noTeto, false, 'noTeto aceitou ' + JSON.stringify(valor));
    const ap = app(); ap.a.receber(dv);
    assert.ok(!ap.txt('network-nota').includes('teto'), 'nota com teto para ' + JSON.stringify(valor));
  });
});
test('rede: atualização ao vivo tira a linha que saiu e reaproveita o mesmo elemento do programa que mudou', () => {
  const { a, doc } = app(); a.receber(clone(fixture));
  const antes = linhasRede(doc)[0], estadoAntes = lerLinhaErro(antes).estado;
  assert.equal(lerLinhaErro(antes).rotulo, 'Claude Code CLI');
  const d = clone(fixture);
  d.rede_uso.programas = d.rede_uso.programas.filter((p) => p.produto !== 'grok_cli');
  d.rede_uso.programas[0].janela.internet = [4000, 9000000];
  a.receber(d);
  const linhas = linhasRede(doc);
  assert.equal(linhas.length, 2);
  assert.equal(linhas[0], antes);
  assert.deepEqual(linhas.map((tr) => lerLinhaErro(tr).rotulo), ['Claude Code CLI', 'Outros programas']);
  assert.notEqual(lerLinhaErro(antes).estado, estadoAntes);
  assert.equal(lerLinhaErro(antes).estado, '↑ ' + Hermes.fmtBytes(4000) + ' · ↓ ' + Hermes.fmtBytes(9000000));
});
test('rede que lança não derruba a lista de agentes, o bloco de erros nem o de outros agentes; a leitura boa seguinte desenha as 3 linhas', () => {
  const base = app(); base.a.receber(clone(fixture));
  const d = clone(fixture);
  Object.defineProperty(d.rede_uso, 'programas', { enumerable: true, get() { throw new Error('boom rede'); } });
  const { a, doc, txt } = app();
  silenciarErros((erros) => {
    assert.doesNotThrow(() => a.receber(d));
    assert.equal(erros.length, 1); assert.match(String(erros[0][0]), /de rede/); assert.match(String(erros[0][1]), /boom rede/);
  });
  ['agent-rows', 'errors', 'extra-agents'].forEach((id) => {
    assert.equal(txt(id), base.txt(id), 'texto mudou: ' + id);
    assert.equal(doc.getElementById(id).hidden, base.doc.getElementById(id).hidden, 'visibilidade mudou: ' + id);
  });
  silenciarErros(() => a.receber(clone(fixture)));
  assert.equal(linhasRede(doc).length, 3);
});

/* ---------- E22b: gráfico de rede por programa ---------- */
const G0 = fixture.rede_uso.gerado_em_epoch, D0 = fixture.rede_uso.desde_epoch;
const itemRede = (id, totalEnv, totalRec) => ({ id, rotulo: 'P ' + id, familia: 'claude', conexoes: 1, enviados: 0, recebidos: 0, destinos: [], totalEnv, totalRec });
const modeloRede = (gerado, itens, extra = {}) => ({ ...REDE_VAZIA, estado: 'ativo', gerado, desde: D0, itens, ...extra });
const plano = (o) => JSON.parse(JSON.stringify(o));      // os mapas do histórico não têm protótipo: compara pelo conteúdo
const retratoRede = (seg, totalClaude, mud = {}) => {
  const d = clone(fixture); d.rede_uso.gerado_em_epoch = G0 + seg;
  if (totalClaude) d.rede_uso.programas[0].total.internet = totalClaude;
  Object.assign(d.rede_uso, mud); return d;
};
const lerGrafico = (tr) => {
  const ds = tr.descendentes(), caixa = ds.find((e) => e.className === 'net-graph'), svg = ds.find((e) => e.tag === 'svg' && e.className === 'net-spark'), taxa = ds.find((e) => e.className === 'net-rate');
  return { caixa, svg, oculto: caixa.hidden, rec: svg.children[0].attrs.d, env: svg.children[1].attrs.d, taxas: taxa.children.map((e) => nb(e.textContent)), texto: nb(taxa.textContent) };
};
test('fmtTaxa: bytes por segundo, valor inválido vira 0 B/s', () => {
  [0, -1, NaN, '1500', undefined, null, Infinity].forEach((v) => assert.equal(Hermes.fmtTaxa(v), '0 B/s'));
  assert.equal(nb(Hermes.fmtTaxa(999)), '999 B/s');
  assert.equal(nb(Hermes.fmtTaxa(1500)), '1,5 kB/s');
  assert.equal(nb(Hermes.fmtTaxa(500000)), '500 kB/s');
  assert.equal(nb(Hermes.fmtTaxa(28000000)), '28 MB/s');
});
test('redeDe: gerado, desde e total por programa', () => {
  const r = Hermes.redeDe(clone(fixture));
  assert.equal(r.gerado, 1793433717); assert.equal(r.desde, 1793430112);
  ['gerado_em_epoch', 'desde_epoch'].forEach((campo) => {
    const outro = campo === 'gerado_em_epoch' ? 'desde' : 'gerado';
    ['amanhã', null, 0, -5, undefined].forEach((v) => {
      const d = clone(fixture);
      if (v === undefined) delete d.rede_uso[campo]; else d.rede_uso[campo] = v;
      const x = Hermes.redeDe(d);
      assert.equal(x[campo === 'gerado_em_epoch' ? 'gerado' : 'desde'], null, campo + ' ' + String(v));
      assert.equal(x[outro], r[outro]);
      assert.deepStrictEqual(x.itens, r.itens);
    });
  });
  const p = Hermes.redeDe(comRede({ estado: 'parado', gerado_em_epoch: 123 }));
  assert.equal(p.gerado, 123); assert.deepStrictEqual(p.itens, []);
  const tot = (total) => { const d = comRede({ programas: [progRede('a', { total })], outros: { conexoes: 0, janela: parRede({}) } }); return Hermes.redeDe(d).itens[0]; };
  [undefined, null, 'texto', [], parRede({ local: ['1', 2] }), parRede({ local: [1, 2, 3] }), parRede({ local: [-1, 5] }), parRede({ local: [1.5, 2] })].forEach((t, i) => {
    const it = tot(t);
    assert.equal(it.totalEnv, 0, 'env caso ' + i); assert.equal(it.totalRec, 0, 'rec caso ' + i);
  });
  const mistura = tot(parRede({ local: [10, 20], internet: ['1', 2], tailnet: [3, 4] }));
  assert.equal(mistura.totalEnv, 13); assert.equal(mistura.totalRec, 24);
  const o = Hermes.redeDe(comRede({ programas: [], outros: { conexoes: 2, janela: parRede({}), total: parRede({ privada: [7, 8] }) } })).itens[0];
  assert.equal(o.id, 'outros'); assert.equal(o.totalEnv, 7); assert.equal(o.totalRec, 8);
});
test('acumularRede: base, primeiro ponto, repetição e intervalo', () => {
  const h0 = Hermes.histRedeVazio();
  assert.deepStrictEqual(plano(h0), { gerado: null, desde: null, fim: null, totais: {}, series: {}, picos: {} });
  ['totais', 'series', 'picos'].forEach((k) => assert.equal(Object.getPrototypeOf(h0[k]), null));
  const h1 = Hermes.acumularRede(h0, modeloRede(G0, [itemRede('a', 1000, 5000), itemRede('b', 0, 0)]));
  assert.deepStrictEqual(plano(h1), { gerado: G0, desde: D0, fim: G0, totais: { a: [1000, 5000], b: [0, 0] }, series: {}, picos: {} });
  const h2 = Hermes.acumularRede(h1, modeloRede(G0 + 10, [itemRede('a', 21000, 5005000), itemRede('b', 0, 0)]));
  assert.deepStrictEqual(plano(h2.series), { a: [{ t: G0 + 10, env: 2000, rec: 500000 }], b: [{ t: G0 + 10, env: 0, rec: 0 }] });
  assert.deepStrictEqual(plano(h2.picos), { a: 500000, b: 0 });
  assert.equal(h2.gerado, G0 + 10); assert.equal(h2.fim, G0 + 10);
  assert.deepStrictEqual(plano(h2.totais), { a: [21000, 5005000], b: [0, 0] });
  ['totais', 'series', 'picos'].forEach((k) => assert.equal(Object.getPrototypeOf(h2[k]), null));
  assert.equal(Hermes.acumularRede(h2, modeloRede(G0 + 10, [itemRede('a', 999999, 999999)])), h2);
  assert.deepStrictEqual(plano(h0), { gerado: null, desde: null, fim: null, totais: {}, series: {}, picos: {} });
  assert.deepStrictEqual(plano(h1), { gerado: G0, desde: D0, fim: G0, totais: { a: [1000, 5000], b: [0, 0] }, series: {}, picos: {} });
  const h12 = Hermes.acumularRede(h1, modeloRede(G0 + 12, [itemRede('a', 1000 + 24000, 5000 + 1200), itemRede('b', 0, 0)]));
  assert.deepStrictEqual(plano(h12.series.a), [{ t: G0 + 12, env: 2000, rec: 100 }]);
  const hu = Hermes.acumularRede(undefined, modeloRede(G0, []));
  assert.equal(hu.gerado, G0);
});
test('acumularRede: casos que não geram ponto', () => {
  const base = () => {
    const h1 = Hermes.acumularRede(Hermes.histRedeVazio(), modeloRede(G0, [itemRede('a', 1000, 5000), itemRede('b', 0, 0)]));
    return Hermes.acumularRede(h1, modeloRede(G0 + 10, [itemRede('a', 21000, 5005000), itemRede('b', 0, 0)]));
  };
  const par = (h) => [h.series.a.length, h.series.b.length];
  let h = Hermes.acumularRede(base(), modeloRede(G0 + 20, [itemRede('a', 20000, 5006000), itemRede('b', 5, 5)]));
  assert.deepStrictEqual(par(h), [1, 2]);
  assert.deepStrictEqual(plano(h.series.b[1]), { t: G0 + 20, env: 0.5, rec: 0.5 });
  assert.deepStrictEqual(plano(h.totais), { a: [20000, 5006000], b: [5, 5] });
  h = Hermes.acumularRede(base(), modeloRede(G0 + 20, [itemRede('a', 30000, 5000000), itemRede('b', 5, 5)]));
  assert.deepStrictEqual(par(h), [1, 2]);
  assert.deepStrictEqual(plano(h.totais), { a: [30000, 5000000], b: [5, 5] });
  h = Hermes.acumularRede(base(), modeloRede(G0 + 20, [itemRede('a', 30000, 6000000), itemRede('b', 10, 10)], { desde: D0 + 1 }));
  assert.deepStrictEqual(par(h), [1, 1]);
  assert.equal(h.desde, D0 + 1); assert.equal(h.gerado, G0 + 20);
  h = Hermes.acumularRede(h, modeloRede(G0 + 30, [itemRede('a', 31000, 6002000), itemRede('b', 10, 10)], { desde: D0 + 1 }));
  assert.deepStrictEqual(plano(h.series.a[1]), { t: G0 + 30, env: 100, rec: 200 });
  assert.deepStrictEqual(plano(h.series.b[1]), { t: G0 + 30, env: 0, rec: 0 });
  h = Hermes.acumularRede(base(), modeloRede(G0 + 41, [itemRede('a', 40000, 6000000), itemRede('b', 10, 10)]));
  assert.deepStrictEqual(par(h), [1, 1]);
  h = Hermes.acumularRede(base(), modeloRede(G0 + 40, [itemRede('a', 40000, 6000000), itemRede('b', 10, 10)]));
  assert.deepStrictEqual(par(h), [2, 2]);
  h = Hermes.acumularRede(base(), modeloRede(G0 + 5, [itemRede('a', 40000, 6000000), itemRede('b', 10, 10)]));
  assert.deepStrictEqual(par(h), [1, 1]); assert.equal(h.gerado, G0 + 5);
  h = Hermes.acumularRede(base(), modeloRede(G0 + 20, [itemRede('a', 21000, 5005000), itemRede('b', 0, 0), itemRede('c', 9e9, 9e9)]));
  assert.equal('c' in h.series, false); assert.deepStrictEqual(plano(h.totais).c, [9e9, 9e9]);
  h = Hermes.acumularRede(h, modeloRede(G0 + 30, [itemRede('a', 21000, 5005000), itemRede('b', 0, 0), itemRede('c', 9e9 + 100, 9e9 + 300)]));
  assert.deepStrictEqual(plano(h.series.c), [{ t: G0 + 30, env: 10, rec: 30 }]);
  assert.equal(h.picos.c, 30);
});
test('acumularRede: sem retrato ativo a base cai e o desenho fica', () => {
  const h2 = Hermes.acumularRede(Hermes.acumularRede(Hermes.histRedeVazio(), modeloRede(G0, [itemRede('a', 1000, 5000), itemRede('b', 0, 0)])),
    modeloRede(G0 + 10, [itemRede('a', 21000, 5005000), itemRede('b', 0, 0)]));
  const itens = [itemRede('a', 30000, 6000000)];
  const entradas = [modeloRede(G0 + 20, itens, { estado: 'pausado_carga' }), ...['parado', 'encerrado', 'erro_captura', null].map((estado) => modeloRede(G0 + 20, itens, { estado })), modeloRede(null, itens), undefined, null];
  entradas.forEach((x, i) => {
    const h = Hermes.acumularRede(h2, x);
    assert.equal(h.gerado, null, 'gerado ' + i); assert.equal(h.desde, null); assert.equal(h.fim, G0 + 10);
    assert.deepStrictEqual(plano(h.totais), {}); assert.equal(Object.getPrototypeOf(h.totais), null);
    assert.equal(h.series, h2.series); assert.equal(h.picos, h2.picos);
    assert.equal(Hermes.acumularRede(h, x), h);
  });
  const vazio = Hermes.histRedeVazio();
  assert.equal(Hermes.acumularRede(vazio, modeloRede(G0 + 20, itens, { estado: 'pausado_carga' })), vazio);
  let h = Hermes.acumularRede(h2, entradas[0]);
  h = Hermes.acumularRede(h, modeloRede(G0 + 70, [itemRede('a', 900000, 9000000), itemRede('b', 0, 0)]));
  assert.equal(h.series.a.length, 1); assert.equal(h.series.b.length, 1); assert.equal(h.fim, G0 + 70);
  h = Hermes.acumularRede(h, modeloRede(G0 + 80, [itemRede('a', 901000, 9002000), itemRede('b', 0, 0)]));
  assert.deepStrictEqual(plano(h.series.a[1]), { t: G0 + 80, env: 100, rec: 200 });
  assert.equal(h.picos.a, 500000);
});
test('acumularRede: janela de 600 s, teto de 240 pontos, programa que some e ids sem protótipo', () => {
  let h = Hermes.histRedeVazio(), tot = 0;
  for (let i = 0; i <= 70; i++) {
    tot += i === 0 ? 0 : i === 1 ? 10000 : 10;
    h = Hermes.acumularRede(h, modeloRede(G0 + 10 * i, [itemRede('a', 0, tot)]));
  }
  assert.equal(h.fim, G0 + 700);
  assert.ok(h.series.a.every((p) => p.t >= h.fim - 600));
  assert.equal(h.series.a.length, 61);
  assert.ok(h.series.a.every((p) => p.rec !== 1000));
  assert.equal(h.picos.a, 1000);
  h = Hermes.histRedeVazio();
  for (let i = 0; i <= 300; i++) h = Hermes.acumularRede(h, modeloRede(G0 + i, [itemRede('a', 0, i * 100)]));
  assert.equal(h.series.a.length, 240); assert.equal(h.series.a[0].t, h.fim - 239);
  h = Hermes.acumularRede(Hermes.histRedeVazio(), modeloRede(G0, [itemRede('a', 0, 0), itemRede('b', 0, 0)]));
  h = Hermes.acumularRede(h, modeloRede(G0 + 10, [itemRede('a', 100, 100), itemRede('b', 100, 100)]));
  assert.deepStrictEqual(plano(h.series.b), [{ t: G0 + 10, env: 10, rec: 10 }]); assert.equal(h.picos.b, 10);
  for (let s = 20; s <= 610; s += 10) {
    h = Hermes.acumularRede(h, modeloRede(G0 + s, [itemRede('a', s * 10, s * 10)]));
    assert.ok('b' in h.series, 'series ' + s); assert.ok('b' in h.picos, 'picos ' + s); assert.equal('b' in h.totais, false);
  }
  h = Hermes.acumularRede(h, modeloRede(G0 + 620, [itemRede('a', 6200, 6200)]));
  assert.equal('b' in h.series, false); assert.equal('b' in h.picos, false);
  assert.equal(h.series.a.length, 61); assert.equal(h.picos.a, 10);
  h = Hermes.acumularRede(Hermes.histRedeVazio(), modeloRede(G0, [itemRede('__proto__', 0, 0), itemRede('constructor', 0, 0)]));
  h = Hermes.acumularRede(h, modeloRede(G0 + 10, [itemRede('__proto__', 1000, 2000), itemRede('constructor', 3000, 4000)]));
  assert.deepStrictEqual(plano(h.series['__proto__']), [{ t: G0 + 10, env: 100, rec: 200 }]);
  assert.deepStrictEqual(plano(h.series['constructor']), [{ t: G0 + 10, env: 300, rec: 400 }]);
  assert.equal(h.picos['__proto__'], 200); assert.equal(h.picos['constructor'], 400);
  assert.deepStrictEqual(Object.keys(h.series).sort(), ['__proto__', 'constructor']);
  assert.equal(Object.getPrototypeOf(h.series), null);
  assert.equal(Object.keys(Object.prototype).length, 0); assert.equal(({}).a, undefined);
});
test('tracoRede: coordenadas, saltos, escala e pontos fora do formato', () => {
  const F = G0 + 700, t = (p, c, pico) => Hermes.tracoRede(p, c, F, pico, 120, 24);
  assert.equal(Hermes.tracoRede([], 'rec', F, 100, 120, 24), '');
  assert.equal(Hermes.tracoRede(undefined, 'rec', F, 100, 120, 24), '');
  assert.equal(Hermes.tracoRede(null, 'rec', F, 100, 120, 24), '');
  [null, NaN, 'x'].forEach((f) => assert.equal(Hermes.tracoRede([{ t: F, rec: 1 }], 'rec', f, 100, 120, 24), ''));
  assert.equal(t([{ t: F, rec: 100 }], 'rec', 100), 'M120 1L120 1');
  assert.equal(t([{ t: F, rec: 0 }], 'rec', 100), 'M120 23L120 23');
  [0, -1, NaN, undefined, 'x'].forEach((pico) => assert.equal(t([{ t: F, rec: 50 }], 'rec', pico), 'M120 23L120 23'));
  assert.equal(t([{ t: F - 300, rec: 50 }], 'rec', 100), 'M60 12L60 12');
  assert.equal(t([{ t: F - 600, rec: 100 }], 'rec', 100), 'M0 1L0 1');
  assert.equal(t([{ t: F - 700, rec: 100 }], 'rec', 100), 'M0 1L0 1');
  assert.equal(t([{ t: F + 5, rec: 100 }], 'rec', 100), 'M120 1L120 1');
  assert.equal(t([{ t: F - 10, rec: 50 }, { t: F, rec: 100 }], 'rec', 100), 'M118 12L118 12L120 1');
  assert.equal(t([{ t: F - 100, rec: 100 }, { t: F - 90, rec: 0 }, { t: F - 59, rec: 100 }, { t: F - 29, rec: 0 }], 'rec', 100), 'M100 1L100 1L102 23M108.2 1L108.2 1L114.2 23');
  assert.equal(t([{ t: F, rec: 500 }], 'rec', 100), 'M120 1L120 1');
  assert.equal(t([{ t: F, env: 2000 }], 'env', 500000), 'M120 22.9L120 22.9');
  const dois = [{ t: F - 10, env: 10, rec: 80 }, { t: F, env: 20, rec: 40 }];
  assert.notEqual(t(dois, 'env', 100), t(dois, 'rec', 100));
  assert.equal(t([{ t: F - 10, rec: 50 }, null, { t: F - 5, rec: 'x' }, { t: 'y', rec: 1 }, { t: F - 4, rec: -1 }, { t: F, rec: 100 }], 'rec', 100), 'M118 12L118 12L120 1');
});
test('rede: o gráfico nasce oculto, aparece no segundo retrato e reaproveita os elementos', () => {
  const { a, doc } = app(); a.receber(retratoRede(0));
  let linhas = linhasRede(doc);
  assert.equal(linhas.length, 3);
  const antes = linhas.map(lerLinhaErro);
  linhas.forEach((tr) => { const g = lerGrafico(tr); assert.equal(g.oculto, true); assert.equal(g.rec, ''); assert.equal(g.env, ''); });
  assert.deepStrictEqual(antes, [
    { rotulo: 'Claude Code CLI', detalhe: '3 conexões', cls: 'status good', estado: '↑ ' + Hermes.fmtBytes(1500000) + ' · ↓ ' + Hermes.fmtBytes(28000000), sub: 'internet ' + Hermes.fmtBytes(29500000) },
    { rotulo: 'Grok CLI', detalhe: '1 conexão', cls: 'status good', estado: '↑ ' + Hermes.fmtBytes(302000) + ' · ↓ ' + Hermes.fmtBytes(500), sub: 'tailnet ' + Hermes.fmtBytes(300000) + ' · local ' + Hermes.fmtBytes(2500) },
    { rotulo: 'Outros programas', detalhe: '42 conexões', cls: 'status good', estado: '↑ ' + Hermes.fmtBytes(1205000) + ' · ↓ ' + Hermes.fmtBytes(5000000),
      sub: 'internet ' + Hermes.fmtBytes(4200000) + ' · local ' + Hermes.fmtBytes(2000000) + ' · rede privada ' + Hermes.fmtBytes(5000) }
  ]);
  const g0 = lerGrafico(linhas[0]);
  assert.equal(g0.svg.attrs.viewBox, '0 0 120 24'); assert.equal(g0.svg.attrs.width, '120'); assert.equal(g0.svg.attrs.height, '24'); assert.equal(g0.svg.attrs['aria-hidden'], 'true');
  assert.equal(g0.svg.children.length, 2);
  assert.deepStrictEqual(g0.svg.children.map((e) => e.tag), ['path', 'path']);
  assert.deepStrictEqual(g0.svg.children.map((e) => e.className), ['net-down', 'net-up']);
  assert.equal(g0.caixa.attrs.title, 'Taxa média entre duas leituras, nos últimos 10 minutos. A escala é o máximo atingido desde que a página abriu.');
  const taxa = g0.caixa.descendentes().find((e) => e.className === 'net-rate');
  assert.deepStrictEqual(taxa.children.map((e) => e.className), ['net-up', 'net-down', '']);
  const primeira = linhas[0];
  a.receber(retratoRede(10, [90020000, 2005000000]));
  linhas = linhasRede(doc);
  assert.equal(linhas[0], primeira); assert.equal(lerGrafico(linhas[0]).svg, g0.svg);
  linhas.forEach((tr) => assert.equal(lerGrafico(tr).oculto, false));
  const g1 = lerGrafico(linhas[0]);
  assert.equal(g1.rec, 'M120 1L120 1'); assert.equal(g1.env, 'M120 22.9L120 22.9');
  assert.deepStrictEqual(g1.taxas, ['↑ 2 kB/s', '↓ 500 kB/s', 'máx. 500 kB/s']);
  assert.equal(g1.texto, '↑ 2 kB/s · ↓ 500 kB/s · máx. 500 kB/s');
  [1, 2].forEach((i) => {
    const g = lerGrafico(linhas[i]);
    assert.equal(g.rec, 'M120 23L120 23'); assert.equal(g.env, 'M120 23L120 23');
    assert.deepStrictEqual(g.taxas, ['↑ 0 B/s', '↓ 0 B/s', 'máx. 0 B/s']);
  });
  assert.deepStrictEqual(linhas.map(lerLinhaErro), antes);
});
test('rede: escala pelo máximo, retrato repetido, pausa, volta e chave que some e volta', () => {
  const { a, doc } = app();
  a.receber(retratoRede(0)); a.receber(retratoRede(10, [90020000, 2005000000]));
  const ler = () => lerGrafico(linhasRede(doc)[0]);
  a.receber(retratoRede(20, [90030000, 2006000000]));
  let g = ler();
  assert.equal(g.rec, 'M118 1L118 1L120 18.6'); assert.equal(g.env, 'M118 22.9L118 22.9L120 23');
  assert.deepStrictEqual(g.taxas, ['↑ 1 kB/s', '↓ 100 kB/s', 'máx. 500 kB/s']);
  a.receber(retratoRede(20, [90030000, 2006000000])); a.desenharTudo();
  let g2 = ler();
  assert.equal(g2.rec, g.rec); assert.equal(g2.env, g.env); assert.deepStrictEqual(g2.taxas, g.taxas);
  a.receber(retratoRede(30, null, { estado: 'pausado_carga', retoma_em_epoch: G0 + 90 }));
  assert.equal(linhasRede(doc).length, 3);
  g2 = ler();
  assert.equal(g2.oculto, false); assert.equal(g2.rec, g.rec); assert.equal(g2.env, g.env);
  assert.deepStrictEqual(g2.taxas, ['↑ —', '↓ —', 'máx. 500 kB/s']);
  a.receber(retratoRede(100, [90530000, 2056000000]));
  g = ler();
  assert.equal(g.rec, 'M102 1L102 1L104 18.6'); assert.deepStrictEqual(g.taxas, ['↑ —', '↓ —', 'máx. 500 kB/s']);
  a.receber(retratoRede(110, [90540000, 2058000000]));
  g = ler();
  assert.equal(g.rec, 'M100 1L100 1L102 18.6M120 14.2L120 14.2');
  assert.deepStrictEqual(g.taxas, ['↑ 1 kB/s', '↓ 200 kB/s', 'máx. 500 kB/s']);
  a.receber(semRede());
  assert.equal(linhasRede(doc).length, 0);
  a.receber(retratoRede(120, [90550000, 2060000000]));
  assert.equal(linhasRede(doc).length, 3);
  g = ler();
  assert.equal(g.oculto, false); assert.equal(g.taxas[0], '↑ —');
});
test('rede: valores hostis não viram marcação nem número inválido, e o CSS tem as regras do gráfico', () => {
  const { a, doc } = app();
  silenciarErros((erros) => {
    a.receber(retratoRede(0));
    const total = (seg, t, mud = {}) => { const d = retratoRede(seg, null, mud); d.rede_uso.programas[0].total = t; a.receber(d); };
    total(10, '<img src=x onerror=alert(1)>');
    total(20, { internet: ['9', '9'] });
    total(30, { internet: [9e15, 9e15] });
    total(40, { internet: [9e15 + 2000000, 9e15 + 4000000] });
    a.receber(retratoRede(50, null, { gerado_em_epoch: '<script>' }));
    assert.equal(erros.length, 0);
  });
  const bloco = doc.getElementById('network'), ds = bloco.descendentes();
  assert.equal(ds.some((e) => e.tag === 'img' || e.tag === 'script'), false);
  linhasRede(doc).forEach((tr) => {
    const g = lerGrafico(tr);
    assert.match(g.rec, /^(?:[ML]\d+(?:\.\d)? \d+(?:\.\d)?)*$/); assert.match(g.env, /^(?:[ML]\d+(?:\.\d)? \d+(?:\.\d)?)*$/);
    assert.ok(!/NaN|Infinity|undefined/.test(g.texto));
  });
  ds.forEach((e) => Object.values(e.attrs).forEach((v) => assert.ok(!/onerror|<img|alert/.test(String(v)))));
  const linhasCss = css.split('\n');
  ['.net-graph{display:block;margin-top:5px}', '.net-graph[hidden]{display:none}', '.net-spark{width:120px;height:24px;border-bottom:1px solid var(--line)}',
    '.net-spark path{fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}',
    '.net-rate{display:block;margin-top:3px;font-size:10px;color:var(--faint);white-space:normal}', '.net-rate span{white-space:nowrap}',
    '.net-up{color:var(--accent)}', '.net-down{color:var(--green)}'].forEach((l) => assert.ok(linhasCss.includes(l), l));
  assert.equal(linhasCss.filter((l) => l.startsWith('.net-') && l.includes('#')).length, 0);
});
