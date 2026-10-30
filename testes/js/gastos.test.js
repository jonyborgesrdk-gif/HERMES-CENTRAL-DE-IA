'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const arquivo = path.join(__dirname, '../../publico/gastos.js');
function api() {
  assert.ok(fs.existsSync(arquivo), 'módulo dos cartões opt-in deve existir');
  return require(arquivo);
}
function ambiente() {
  class Elemento {
    constructor(tag) { this.tagName = tag; this.children = []; this._text = ''; this.id = ''; this.style = {}; }
    set textContent(t) { this._text = String(t); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(' '); }
    set innerHTML(t) { throw new Error('não usar innerHTML'); }
    appendChild(e) { e.parentNode = this; this.children.push(e); return e; }
    insertBefore(e, referencia) {
      e.parentNode = this;
      this.children.splice(this.children.indexOf(referencia), 0, e); return e;
    }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(e => e !== this); }
  }
  const raiz = new Elemento('main');
  const doc = { createElement: tag => new Elemento(tag), getElementById: id => {
    function achar(e) { return e.id === id ? e : e.children.map(achar).find(Boolean); }
    return achar(raiz) || null;
  }};
  for (const id of ['servicos-grade', 'rodape']) {
    const e = doc.createElement('div'); e.id = id; raiz.appendChild(e);
  }
  return { doc, raiz };
}
const ponte = { estado: 'connected', gateway_state: 'running', pid_vivo: true,
  needs_attention: false, updated_at: '2026-10-30T12:00:00-03:00', idade_s: 1 };
const linha = source => ({ source, input_tokens: 10, output_tokens: 5,
  cache_read_tokens: 3, cache_write_tokens: 2, cost_usd: 0.2 });
function gastos() {
  return { hoje: { origens: [linha('WhatsApp · conversa direta')], total: linha('total') },
    '7d': { origens: [linha('Terminal'), linha('WhatsApp · conversa direta')], total: { ...linha('total'), cost_usd: 0.4 } } };
}

test('sem chaves novas, DOM idêntico; retirar opt-in remove cartões', () => {
  const { doc, raiz } = ambiente();
  const antes = raiz.textContent;
  api().desenhar({}, 1000, doc);
  assert.equal(raiz.textContent, antes);
  assert.equal(raiz.children.length, 2);
  api().desenhar({ whatsapp: ponte, gastos_hermes: gastos() }, 1000, doc);
  assert.ok(doc.getElementById('whatsapp-cartao'));
  assert.ok(doc.getElementById('gastos-cartao'));
  api().desenhar({}, 1001, doc);
  assert.equal(raiz.textContent, antes);
  assert.equal(raiz.children.length, 2);
});

test('ponte conectado, caído, atenção, ausente e erro sem HTML', () => {
  const { doc } = ambiente();
  for (const [alteracao, esperado] of [[{}, 'conectado'], [{ pid_vivo: false }, 'caído'],
    [{ estado: 'disconnected' }, 'caído'], [{ needs_attention: true }, 'atenção'],
    [{ estado: 'ausente' }, 'ausente'], [{ estado: 'erro' }, 'erro'],
    [{ idade_s: 121 }, 'atenção']]) {
    api().desenhar({ whatsapp: { ...ponte, ...alteracao, error_message: '<img src=x onerror=alert(1)>' } }, 1000, doc);
    const c = doc.getElementById('whatsapp-cartao');
    assert.match(c.textContent, new RegExp(esperado));
    assert.match(c.textContent, /Última atualização/);
    assert.doesNotMatch(c.textContent, /<img src=x onerror=alert\(1\)>/);
    assert.equal(c.parentNode.id, 'servicos-grade');
    assert.equal(doc.getElementById('servicos-grade').children.length, 1);
  }
});

test('mensagem livre legada nunca aparece no cartão WhatsApp', () => {
  const { doc } = ambiente();
  api().desenhar({ whatsapp: { ...ponte, error_code: 'logged_out',
    error_message: 'chat_id=12345678-1234-5678-9abc-123456789abc', tem_mensagem_erro: true } }, 1000, doc);
  const texto = doc.getElementById('whatsapp-cartao').textContent;
  assert.match(texto, /logged_out/);
  assert.doesNotMatch(texto, /chat_id=|12345678-1234-5678-9abc-123456789abc/);
});

test('gastos: canais de ambas as janelas, entrada/saída/cache/US$, total e legenda', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: gastos() }, 1000, doc);
  const c = doc.getElementById('gastos-cartao');
  for (const trecho of ['Gastos', 'Hoje', '7 dias', 'Entrada', 'Saída', 'Cache', 'US$',
    'Canal', 'Terminal', 'WhatsApp · conversa direta', 'Total', '0.2000', '0.4000',
    'custo estimado pelo Hermes; não inclui Claude Code/Codex fora do Hermes']) {
    assert.ok(c.textContent.includes(trecho), trecho);
  }
  const table = c.children.find(e => e.className === 'gastos-rolagem').children[0];
  const rows = table.children.find(e => e.tagName === 'tbody').children;
  assert.equal(rows.length, 3);
  assert.equal(rows[0].children[0].textContent, 'Terminal');
  assert.equal(rows[0].children[1].textContent, '0');
  assert.equal(rows[1].children[3].textContent, '5');
});

test('gráficos por janela: custo decrescente, empates estáveis e assinatura zero no fim', () => {
  const { doc } = ambiente();
  const canais = [
    { ...linha('Subagentes'), cost_usd: 0, inclui_assinatura: true },
    { ...linha('Ferramentas'), cost_usd: 2 },
    { ...linha('Terminal'), cost_usd: 9, inclui_assinatura: true },
    { ...linha('WhatsApp · grupo'), cost_usd: 2 },
    { ...linha('One-shot'), cost_usd: 0 },
    { ...linha('App desktop'), cost_usd: 0, inclui_assinatura: true }
  ];
  const g = { hoje: { origens: canais, total: {} },
    '7d': { origens: canais.map(o => ({ ...o, cost_usd: o.source === 'Ferramentas' ? 12 : o.cost_usd })), total: {} } };
  const antes = JSON.stringify(g);
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  for (const [janela, nomes] of [
    ['hoje', ['Terminal', 'Ferramentas', 'WhatsApp · grupo', 'One-shot', 'Subagentes', 'App desktop']],
    ['7d', ['Ferramentas', 'Terminal', 'WhatsApp · grupo', 'One-shot', 'Subagentes', 'App desktop']]
  ]) {
    const grafico = doc.getElementById('gastos-grafico-' + janela);
    assert.ok(grafico, 'gráfico ' + janela);
    const linhas = grafico.children.find(e => e.tagName === 'ol').children;
    assert.deepEqual(linhas.map(e => e.children[0].textContent), nomes);
    assert.equal(linhas[0].children[2].children[0].style.width, '100%');
    const segunda = parseFloat(linhas[1].children[2].children[0].style.width);
    assert.ok(Math.abs(segunda - (janela === 'hoje' ? 2 / 9 * 100 : 9 / 12 * 100)) < 0.000001);
    for (const e of linhas) {
      const largura = parseFloat(e.children[2].children[0].style.width);
      assert.ok(Number.isFinite(largura) && largura >= 0 && largura <= 100);
    }
    for (const e of linhas.slice(-2)) {
      assert.match(e.textContent, /US\$ 0\.0000 · assinatura · 20 tokens/);
      assert.equal(e.children[2].children[0].style.width, '0%');
    }
    assert.match(linhas.find(e => e.children[0].textContent === 'Terminal').textContent, /inclui assinatura/);
  }
  const tabela = doc.getElementById('gastos-cartao').children.find(e => e.className === 'gastos-rolagem');
  assert.match(tabela.textContent, /assinatura/);
  assert.equal(JSON.stringify(g), antes, 'renderização não altera snapshot nem sua ordem');
});

test('gráficos sem custo: barras zero finitas, tokens e assinatura continuam visíveis', () => {
  const { doc } = ambiente();
  for (const cost_usd of [0, null, undefined, '100%; color:red', NaN, Infinity]) {
    const janela = { origens: [{ ...linha('Subagentes'), cost_usd, inclui_assinatura: true }], total: {} };
    api().desenhar({ gastos_hermes: { hoje: janela, '7d': janela } }, 1000, doc);
    const grafico = doc.getElementById('gastos-grafico-hoje');
    const item = grafico.children.find(e => e.tagName === 'ol').children[0];
    assert.equal(item.children[2].children[0].style.width, '0%');
    assert.match(item.textContent, /US\$ 0\.0000 · assinatura · 20 tokens/);
  }
});

test('vazio mostra total zero; erro não inventa custo e retido é explícito', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: { hoje: { origens: [], total: {} }, '7d': { origens: [], total: {} } } }, 1000, doc);
  assert.match(doc.getElementById('gastos-cartao').textContent, /Total/);
  assert.match(doc.getElementById('gastos-cartao').textContent, /0\.0000/);
  api().desenhar({ gastos_hermes: { estado: 'erro' }, fontes: { gastos_hermes: { ok: false, erro: 'travado' } } }, 1001, doc);
  assert.match(doc.getElementById('gastos-cartao').textContent, /sem leitura/);
  assert.doesNotMatch(doc.getElementById('gastos-cartao').textContent, /0\.0000/);
  api().desenhar({ gastos_hermes: gastos(), whatsapp: ponte, fontes: {
    gastos_hermes: { ok: false, desatualizado: true, ultima_leitura_ok: 900 },
    whatsapp: { ok: false, desatualizado: true, ultima_leitura_ok: 900 }
  } }, 1001, doc);
  for (const id of ['gastos-cartao', 'whatsapp-cartao']) {
    assert.match(doc.getElementById(id).textContent, /dado retido/);
    assert.match(doc.getElementById(id).textContent, /101 s/);
  }
});

test('idade do snapshot retido parte da última leitura boa, não da tentativa', () => {
  const { doc } = ambiente();
  const dados = { whatsapp: { ...ponte, idade_s: 5 }, fontes: { whatsapp: {
    ok: false, desatualizado: true, hora: 1060, ultima_leitura_ok: 1000
  } } };
  for (const [agora, idade] of [[1060, 65], [1070, 75]]) {
    api().desenhar(dados, agora, doc);
    const texto = doc.getElementById('whatsapp-cartao').textContent;
    assert.match(texto, /atenção/);
    assert.match(texto, new RegExp('estado do arquivo: há ' + idade + ' s'));
  }
  dados.fontes.whatsapp = { ok: true, hora: 1000 };
  api().desenhar(dados, 1060, doc);
  assert.match(doc.getElementById('whatsapp-cartao').textContent, /estado do arquivo: há 65 s/);
});

test('origem maliciosa continua texto; desenhar repetido não duplica cartões', () => {
  const { doc, raiz } = ambiente();
  const g = gastos(); g.hoje.origens[0].source = '<script>alert(1)</script>';
  for (let i = 0; i < 3; i++) api().desenhar({ gastos_hermes: g }, 1000, doc);
  assert.match(doc.getElementById('gastos-cartao').textContent, /<script>alert\(1\)<\/script>/);
  assert.equal(raiz.children.length, 3);
});

test('página só carrega módulo novo quando uma chave opt-in chega', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../publico/index.html'), 'utf8');
  assert.ok(html.includes('function desenharExtras('));
  const codigo = 'function desenharExtras(' + html.split('function desenharExtras(')[1].split('function desenhar()')[0];
  const scripts = [], a = { dados: {}, window: {}, gastosCarregando: false,
    desenhar() {}, document: { createElement: () => ({}), head: { appendChild: s => scripts.push(s) } } };
  vm.createContext(a); vm.runInContext(codigo, a);
  a.desenharExtras(1000); assert.equal(scripts.length, 0);
  a.dados = { whatsapp: ponte };
  a.desenharExtras(1000); a.desenharExtras(1001);
  assert.equal(scripts.length, 1);
  assert.match(scripts[0].src, /^gastos\.js/);
  let chamadas = 0;
  a.window.__painelGastos = { desenhar: () => chamadas++ };
  a.desenharExtras(1002); assert.equal(chamadas, 1);
});
