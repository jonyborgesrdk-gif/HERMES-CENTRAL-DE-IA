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
  assert.match(lim, /Limite semanal.*70%.*reinicia 31\/10 às 20h00/); assert.match(lim, /Codex.*SEM DADOS.*Conta ainda não identificada/);
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
  ['agentes', 'contas', 'servicos', 'processos', 'fontes', 'whatsapp', 'gastos_hermes', 'quadro', 'prs', 'origem', 'gerado_em_epoch'].forEach((k) => assert.ok(lidos.has(k), 'não leu ' + k));
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
