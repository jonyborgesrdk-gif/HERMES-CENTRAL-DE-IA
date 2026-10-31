'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const arquivo = path.join(__dirname, '../../publico/gastos.js');
function api() {
  assert.ok(fs.existsSync(arquivo), 'módulo dos cartões opt-in deve existir');
  delete require.cache[require.resolve(arquivo)];
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
    addEventListener(tipo, fn) { this['on' + tipo] = fn; }
    click() { if (this.onclick) this.onclick(); }
    focus() { doc.activeElement = this; }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(e => e !== this); }
    replaceWith(e) { if (this.parentNode) { e.parentNode = this.parentNode; this.parentNode.children[this.parentNode.children.indexOf(this)] = e; } }
  }
  const raiz = new Elemento('main');
  const doc = { createElement: tag => new Elemento(tag), defaultView: globalThis, getElementById: id => {
    function achar(e) { return e.id === id ? e : e.children.map(achar).find(Boolean); }
    return achar(raiz) || null;
  }};
  for (const id of ['servicos-grade', 'rodape']) {
    const e = doc.createElement('div'); e.id = id; raiz.appendChild(e);
  }
  return { doc, raiz };
}
test.beforeEach(() => { delete globalThis.__gastosEstado; });
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
    assert.match(c.textContent, /Estado da ponte registrado em: /);
    assert.doesNotMatch(c.textContent, /Última atualização/);
    assert.match(c.textContent, /estado do arquivo: há \d+ s/);
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
  for (const trecho of ['Gastos', 'Hoje', '7 dias', 'Canal', 'Terminal', 'WhatsApp · conversa direta', 'Total', '0.2000',
    'custo estimado pelo Hermes; não inclui Claude Code/Codex fora do Hermes']) {
    assert.ok(c.textContent.includes(trecho), trecho);
  }
  const table = doc.getElementById('gastos-tabela');
  const rows = table.children.find(e => e.tagName === 'tbody').children;
  assert.equal(rows.length, 3);
  assert.equal(rows[0].children[0].textContent, 'Terminal');
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
  for (const [janela, nomes] of [['7d', ['Ferramentas', 'Terminal', 'WhatsApp · grupo', 'One-shot', 'Subagentes', 'App desktop']]]) {
  const grafico = doc.getElementById('gastos-grupos');
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
  const tabela = doc.getElementById('gastos-tabela');
  assert.match(tabela.textContent, /assinatura/);
  assert.equal(JSON.stringify(g), antes, 'renderização não altera snapshot nem sua ordem');
});

test('gráficos sem custo: barras zero finitas, tokens e assinatura continuam visíveis', () => {
  const { doc } = ambiente();
  for (const cost_usd of [0, -1, null, undefined, '100%; color:red', NaN, Infinity]) {
    const janela = { origens: [{ ...linha('Subagentes'), cost_usd, inclui_assinatura: true }], total: {} };
    api().desenhar({ gastos_hermes: { hoje: janela, '7d': janela } }, 1000, doc);
    const grafico = doc.getElementById('gastos-grupos');
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
  const g = gastos(); g['7d'].origens[1].source = '<script>alert(1)</script>';
  for (let i = 0; i < 3; i++) api().desenhar({ gastos_hermes: g }, 1000, doc);
  assert.match(doc.getElementById('gastos-cartao').textContent, /<script>alert\(1\)<\/script>/);
  assert.equal(raiz.children.length, 3);
});

function gastosE10() {
  const a = { ...linha('Terminal'), model: 'modelo-a', cost_usd: 3 };
  const b = { ...linha('Subagentes'), model: 'modelo-b', cost_usd: 1 };
  const j = { origens: [{ ...a, modelos: [a] }, { ...b, modelos: [b] }], modelos: [a, b],
    total: { ...linha('total'), input_tokens: 20, output_tokens: 10, cache_read_tokens: 6, cache_write_tokens: 4, cost_usd: 4 },
    granularidade: 'dia', serie: [{ inicio: 1793329200, fim: 1793372400, rotulo: '2026-10-30',
      total: [4, 40], por_canal: { Terminal: [3, 20], Subagentes: [1, 20] },
      por_modelo: { 'modelo-a': [3, 20], 'modelo-b': [1, 20] } }] };
  return { custo_modelo_rateado: true, distribuicao_aproximada: true,
    hoje: { ...j, granularidade: 'hora' }, '24h': { ...j, granularidade: 'hora' }, '7d': j, '30d': j };
}
function escolher(doc, id, valor) {
  const e = doc.getElementById(id); assert.ok(e, id); e.value = valor; e.onchange();
}
function barras(doc) { return doc.getElementById('gastos-grupos').children.find(e => e.tagName === 'ol').children; }
function linhasTabela(doc) { return doc.getElementById('gastos-tabela').children.find(e => e.tagName === 'tbody').children; }

test('E10 seletores de janela e agrupamento, série horário/dia e avisos', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: gastosE10() }, 1000, doc);
  const seletor = doc.getElementById('gastos-janela');
  assert.ok(seletor);
  assert.deepEqual(seletor.children.map(e => e.value), ['24h', 'hoje', '7d', '30d']);
  assert.equal(seletor.value, '7d');
  for (const [chave, granularidade] of [['24h', 'hora'], ['hoje', 'hora'], ['7d', 'dia'], ['30d', 'dia']]) {
    escolher(doc, 'gastos-janela', chave);
    assert.match(doc.getElementById('gastos-colunas').textContent, new RegExp('por ' + granularidade));
  }
  escolher(doc, 'gastos-agrupamento', 'modelo');
  assert.match(doc.getElementById('gastos-tabela').textContent, /modelo-a/);
  const texto = doc.getElementById('gastos-cartao').textContent;
  assert.match(texto, /custo por modelo é rateio aproximado/);
  assert.match(texto, /distribuição aproximada pelas mensagens/);
});

test('E10 clique filtra série e tabela, alterna e limpar remove filtro', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: gastosE10() }, 1000, doc);
  const botao = barras(doc)[0];
  assert.equal(botao.tagName, 'button');
  assert.equal(botao.type, 'button');
  assert.match(botao.title, /US\$ 3/);
  botao.click();
  assert.equal(barras(doc)[0].ariaPressed, 'true');
  assert.equal(linhasTabela(doc).length, 2);
  assert.match(linhasTabela(doc)[0].textContent, /Terminal/);
  let coluna = doc.getElementById('gastos-colunas').children.find(e => e.tagName === 'ol').children[0];
  assert.match(coluna.children[0].title, /US\$ 3\.0000 · 20 tokens/);
  barras(doc)[0].click();
  assert.equal(linhasTabela(doc).length, 3);
  barras(doc)[1].click();
  doc.getElementById('gastos-limpar').click();
  assert.equal(linhasTabela(doc).length, 3);
  escolher(doc, 'gastos-agrupamento', 'modelo');
  barras(doc)[1].click();
  assert.match(linhasTabela(doc)[0].textContent, /modelo-b/);
  coluna = doc.getElementById('gastos-colunas').children.find(e => e.tagName === 'ol').children[0];
  assert.match(coluna.children[0].title, /US\$ 1\.0000 · 20 tokens/);
});

test('E10 estado e foco sobrevivem refresh; filtro ausente não vira total geral', () => {
  const { doc } = ambiente();
  const g = gastosE10();
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  escolher(doc, 'gastos-janela', '24h');
  escolher(doc, 'gastos-agrupamento', 'modelo');
  barras(doc)[0].click();
  doc.getElementById('gastos-janela').focus();
  api().desenhar({ gastos_hermes: gastosE10() }, 1060, doc);
  assert.equal(doc.getElementById('gastos-janela').value, '24h');
  assert.equal(doc.getElementById('gastos-agrupamento').value, 'modelo');
  assert.equal(barras(doc)[0].ariaPressed, 'true');
  assert.equal(linhasTabela(doc).length, 2);
  assert.equal(doc.activeElement && doc.activeElement.id, 'gastos-janela');
  g['24h'] = { ...g['24h'], modelos: [], serie: [{ rotulo: 'vazio', total: [9, 90], por_modelo: {} }] };
  api().desenhar({ gastos_hermes: g }, 1120, doc);
  // Filtro que deixou de existir é limpo em silêncio: sem barra fantasma nem rodapé de filtro.
  assert.equal(doc.getElementById('gastos-filtro'), null);
  assert.equal(globalThis.__gastosEstado.filtro, '');
  assert.equal(barras(doc).length, 0);
  assert.ok(!doc.getElementById('gastos-grupos').textContent.includes('modelo-a'));
  assert.equal(linhasTabela(doc).length, 1);
  assert.match(doc.getElementById('gastos-colunas').children.find(e => e.tagName === 'ol').children[0].children[0].title, /US\$ 9\.0000 · 90 tokens/);
});

test('E10 selects com type somente leitura não quebram o desenho', () => {
  const { doc } = ambiente();
  const criar = doc.createElement;
  doc.createElement = tag => {
    const e = criar(tag);
    if (tag === 'select') Object.defineProperty(e, 'type', { get: () => 'select-one' });
    return e;
  };
  assert.doesNotThrow(() => api().desenhar({ gastos_hermes: gastosE10() }, 1000, doc));
  assert.ok(doc.getElementById('gastos-janela'));
  assert.ok(doc.getElementById('gastos-agrupamento'));
  assert.equal(doc.getElementById('gastos-janela').type, 'select-one');
  assert.equal(doc.getElementById('gastos-limpar').type, 'button');
});

test('E10 escala das colunas com filtro usa o maior valor do grupo', () => {
  const { doc } = ambiente();
  const g = gastosE10();
  g['7d'].serie = [
    { rotulo: 'a', total: [100, 10], por_canal: {}, por_modelo: {} },
    { rotulo: 'b', total: [104, 40], por_canal: { Terminal: [3, 20], Subagentes: [1, 20] }, por_modelo: {} },
    { rotulo: 'c', total: [102, 20], por_canal: { Subagentes: [2, 20] }, por_modelo: {} }
  ];
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  barras(doc).find(e => e.children[0].textContent === 'Subagentes').click();
  const itens = doc.getElementById('gastos-colunas').children.find(e => e.tagName === 'ol').children;
  assert.deepEqual(itens.map(i => i.children[1].children[0].style.height), ['0%', '50%', '100%']);
});

test('E10 foco volta à mesma barra após refresh', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: gastosE10() }, 1000, doc);
  const antes = barras(doc)[1];
  assert.equal(antes.id, 'gastos-grupo-Subagentes');
  antes.focus();
  api().desenhar({ gastos_hermes: gastosE10() }, 1060, doc);
  assert.equal(doc.activeElement.id, 'gastos-grupo-Subagentes');
  assert.equal(doc.activeElement, doc.getElementById('gastos-grupo-Subagentes'));
  assert.notEqual(doc.activeElement, antes);
});

test('E10 foco fora do cartão permanece intacto, sem estado residual', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: gastosE10() }, 1000, doc);
  doc.getElementById('gastos-janela').focus();
  api().desenhar({ gastos_hermes: gastosE10() }, 1060, doc);
  assert.equal(doc.activeElement.id, 'gastos-janela');
  const rodape = doc.getElementById('rodape');
  rodape.focus();
  api().desenhar({ gastos_hermes: gastosE10() }, 1120, doc);
  assert.equal(doc.activeElement, rodape);
  // Interação dentro do cartão também não rouba o foco de fora.
  doc.getElementById('gastos-limpar').click();
  assert.equal(doc.activeElement, rodape);
});

test('E10 negativos/NaN/Infinity são zero na ordem, assinatura zero sempre no fim', () => {
  const { doc } = ambiente();
  const g = gastosE10();
  g['7d'].modelos = [];
  g['7d'].origens = [
    { ...linha('assinatura'), cost_usd: 0, inclui_assinatura: true },
    { ...linha('negativo'), cost_usd: -2 }, { ...linha('nan'), cost_usd: NaN },
    { ...linha('infinito'), cost_usd: Infinity }, { ...linha('positivo'), cost_usd: 2 }
  ];
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  assert.deepEqual(barras(doc).map(e => e.children[0].textContent), ['positivo', 'negativo', 'nan', 'infinito', 'assinatura']);
  for (const b of barras(doc).slice(1)) assert.equal(b.children[2].children[0].style.width, '0%');
  assert.match(barras(doc).at(-1).textContent, /US\$ 0\.0000 · assinatura/);
});

test('página só carrega módulo novo quando uma chave opt-in chega', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../publico/classico.html'), 'utf8');
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

// E11: cotação em R$
const semEspaco = t => t.replace(/[\s  ]+/g, ' ');
const reais = v => semEspaco(v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' }));
const cotacaoOk = { usd_brl: 5, fonte: 'awesomeapi', data_epoch: 1761825600, desatualizada: false };

test('E11 com cotação mostra R$ principal e US$ entre parênteses/ao lado', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: { ...gastos(), cotacao: cotacaoOk } }, 1000, doc);
  const barra = barras(doc)[0];
  assert.equal(semEspaco(barra.title), reais(1) + ' (US$ 0.2000) · 20 tokens');
  const texto = semEspaco(doc.getElementById('gastos-cartao').textContent);
  assert.ok(texto.includes('Tokens e R$ —'));
  assert.ok(texto.includes('R$ por '));
  assert.ok(!texto.includes('US$ por '));
  const celula = linhasTabela(doc)[0].children.at(-1);
  assert.ok(semEspaco(celula.textContent).includes(reais(1)));
  assert.equal(celula.children[0].className, 'gastos-usd');
  assert.equal(celula.children[0].textContent, 'US$ 0.2000');
  const cabeca = doc.getElementById('gastos-tabela').children.find(e => e.tagName === 'thead');
  assert.equal(cabeca.children[0].children.at(-1).textContent, 'R$');
});

test('E11 linha de cotação, com e sem desatualizada', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: { ...gastos(), cotacao: cotacaoOk } }, 1000, doc);
  const l = semEspaco(doc.getElementById('gastos-cotacao').textContent);
  assert.ok(l.startsWith('Cotação US$ 1 = ' + reais(5) + ' (awesomeapi, '));
  assert.ok(!l.includes('desatualizada'));
  api().desenhar({ gastos_hermes: { ...gastos(), cotacao: { ...cotacaoOk, desatualizada: true } } }, 1000, doc);
  assert.ok(doc.getElementById('gastos-cotacao').textContent.endsWith(' · desatualizada'));
});

test('E11 sem cotação ou cotação inválida cai em US$ sem linha de cotação', () => {
  for (const cotacao of [undefined, null, { usd_brl: 0 }, { usd_brl: NaN }, { usd_brl: '5' }, { usd_brl: -1 }, 'x']) {
    const { doc } = ambiente();
    api().desenhar({ gastos_hermes: { ...gastos(), cotacao } }, 1000, doc);
    assert.equal(doc.getElementById('gastos-cotacao'), null);
    assert.equal(barras(doc)[0].title, 'US$ 0.2000 · 20 tokens');
    assert.ok(doc.getElementById('gastos-cartao').textContent.includes('Tokens e US$ —'));
  }
});

test('E11 tokens sem casas decimais', () => {
  const { doc } = ambiente();
  const g = gastos();
  const um = t => ({ ...linha('Terminal'), input_tokens: t, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 });
  g['7d'].origens = [um(10.7)]; g['7d'].total = um(10.7);
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  assert.match(barras(doc)[0].title, /· 11 tokens$/);
  g['7d'].origens = [um(1234.5)]; g['7d'].total = um(1234.5);
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  const t = barras(doc)[0].title;
  assert.ok(t.includes('1.235') && !t.includes(','), t);
});

// ---- Correções B3–B8 (painel IAs) ----
function janelaB(granularidade, rotulos, extra) {
  return { origens: [], modelos: [], total: {}, granularidade, serie: rotulos.map(r => ({ rotulo: r,
    total: [1, 10], por_canal: {}, por_modelo: {} })), ...extra };
}
function colunasDe(doc) {
  return doc.getElementById('gastos-colunas').children.find(e => e.tagName === 'ol').children;
}

test('B3 cartão WhatsApp rotula a hora da ponte e mantém a idade do arquivo', () => {
  const { doc } = ambiente();
  api().desenhar({ whatsapp: { ...ponte, updated_at: '2026-10-30T16:49:25+00:00', idade_s: 37 } }, 1000, doc);
  const texto = doc.getElementById('whatsapp-cartao').textContent;
  // A página mostra a hora no fuso da máquina: o esperado usa o mesmo formatador, para valer em qualquer TZ.
  const horaLocal = new Date('2026-10-30T16:49:25+00:00').toLocaleString('pt-BR');
  assert.match(horaLocal, /^\d{2}\/\d{2}\/\d{4},? \d{2}:\d{2}:\d{2}$/);
  assert.ok(texto.includes('Estado da ponte registrado em: ' + horaLocal + ' · '), texto);
  assert.match(texto, /estado do arquivo: há 37 s/);
  assert.doesNotMatch(texto, /Última atualização/);
});

test('B4 colunas do gráfico não são botões nem têm tab stop; valor em aria-label e title', () => {
  const { doc } = ambiente();
  api().desenhar({ gastos_hermes: gastosE10() }, 1000, doc);
  const colunas = doc.getElementById('gastos-colunas');
  const achar = (e, fn, acc = []) => { if (fn(e)) acc.push(e); e.children.forEach(c => achar(c, fn, acc)); return acc; };
  assert.equal(achar(colunas, e => e.tagName === 'button').length, 0);
  const rotulo = colunasDe(doc)[0].children[0];
  assert.notEqual(rotulo.tagName, 'button');
  assert.equal(rotulo.onclick, undefined);
  assert.equal(rotulo.tabIndex, undefined);
  assert.match(rotulo.ariaLabel, /US\$ 4\.0000 · 40 tokens/);
  assert.equal(rotulo.ariaLabel, rotulo.title);
  assert.equal(rotulo.role, 'img');
});

test('B5 rótulos de coluna curtos: só o dia em 30d, hora nas séries horárias, seletores com nome', () => {
  const { doc } = ambiente();
  const dias = Array.from({ length: 30 }, (_, i) => '2026-10-' + String(i + 1).padStart(2, '0'));
  const g = { '30d': janelaB('dia', dias), '7d': janelaB('dia', dias.slice(0, 7)),
    '24h': janelaB('hora', ['29/10 22:34', '29/10 23:00']) };
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  escolher(doc, 'gastos-janela', '30d');
  assert.deepEqual(colunasDe(doc).slice(0, 3).map(i => i.children[0].textContent), ['01', '02', '03']);
  assert.ok(colunasDe(doc).every(i => i.children[0].textContent.length <= 2));
  assert.match(colunasDe(doc)[23].children[0].title, /^24\/10: /);
  escolher(doc, 'gastos-janela', '7d');
  assert.equal(colunasDe(doc)[0].children[0].textContent, '01/10');
  escolher(doc, 'gastos-janela', '24h');
  assert.deepEqual(colunasDe(doc).map(i => i.children[0].textContent), ['22h', '23h']);
  assert.ok(doc.getElementById('gastos-janela').ariaLabel);
  assert.ok(doc.getElementById('gastos-agrupamento').ariaLabel);
});

test('B6 tabela usa a mesma ordem da lista (custo desc, Total por último)', () => {
  const { doc } = ambiente();
  const canais = [{ ...linha('Alfa'), cost_usd: 1 }, { ...linha('Beta'), cost_usd: 9 }, { ...linha('Gama'), cost_usd: 4 }];
  api().desenhar({ gastos_hermes: { '7d': { origens: canais, modelos: [], total: { ...linha('t'), cost_usd: 14 } } } }, 1000, doc);
  const nomes = barras(doc).map(e => e.children[0].textContent);
  assert.deepEqual(nomes, ['Beta', 'Gama', 'Alfa']);
  const tabela = linhasTabela(doc).map(tr => tr.children[0].textContent);
  assert.deepEqual(tabela, ['Beta', 'Gama', 'Alfa', 'Total']);
});

test('B7 canal e modelo com 0 tokens e custo 0 somem da lista e da tabela', () => {
  const { doc } = ambiente();
  const zero = { source: 'App desktop', model: 'm-zero', input_tokens: 0, output_tokens: 0, cache_read_tokens: 0,
    cache_write_tokens: 0, cost_usd: 0 };
  const sub = { ...linha('Subagentes'), cost_usd: 0, inclui_assinatura: true };   // tem tokens: continua
  const j = { origens: [zero, sub, { ...linha('Terminal'), cost_usd: 2 }], modelos: [zero, { ...linha('x'), model: 'm-ok' }],
    total: { ...linha('t'), cost_usd: 2 } };
  api().desenhar({ gastos_hermes: { '7d': j } }, 1000, doc);
  assert.deepEqual(barras(doc).map(e => e.children[0].textContent), ['Terminal', 'Subagentes']);
  assert.deepEqual(linhasTabela(doc).map(tr => tr.children[0].textContent), ['Terminal', 'Subagentes', 'Total']);
  escolher(doc, 'gastos-agrupamento', 'modelo');
  assert.deepEqual(barras(doc).map(e => e.children[0].textContent), ['m-ok']);
  assert.deepEqual(linhasTabela(doc).map(tr => tr.children[0].textContent), ['m-ok', 'Total']);
});

test('B8 datas das séries em pt-BR dd/mm (hora como hh:00), igual em todas as janelas', () => {
  const { doc } = ambiente();
  const g = { '24h': janelaB('hora', ['29/10 22:27', '29/10 23:00']), hoje: janelaB('hora', ['30/10 00:00']),
    '7d': janelaB('dia', ['2026-10-24']), '30d': janelaB('dia', ['2026-10-01']) };
  api().desenhar({ gastos_hermes: g }, 1000, doc);
  escolher(doc, 'gastos-janela', '24h');
  assert.deepEqual(colunasDe(doc).map(i => i.children[0].title.slice(0, 11)), ['29/10 22:00', '29/10 23:00']);
  escolher(doc, 'gastos-janela', 'hoje');
  assert.match(colunasDe(doc)[0].children[0].title, /^30\/10 00:00: /);
  escolher(doc, 'gastos-janela', '7d');
  assert.match(colunasDe(doc)[0].children[0].title, /^24\/10: /);
  escolher(doc, 'gastos-janela', '30d');
  assert.match(colunasDe(doc)[0].children[0].title, /^01\/10: /);
  for (const janela of ['24h', 'hoje', '7d', '30d']) {
    escolher(doc, 'gastos-janela', janela);
    for (const i of colunasDe(doc)) assert.doesNotMatch(i.children[0].title, /\d{4}-\d{2}-\d{2}|:[1-9]\d: /);
  }
});
