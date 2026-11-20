'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../../publico/classico.html'), 'utf8');
const corpo = 'function iniciarTransporte(' + html.split('function iniciarTransporte(')[1]
  .split('/* ---------- botão de refresh completo ---------- */')[0];
const iniciar = vm.runInNewContext(corpo + '; iniciarTransporte;');

function ambiente(protocol, comSSE = true) {
  const a = { location: { protocol }, fontes: [], timers: new Set(), carregamentos: 0, estados: [], dados: [], retornos: [] };
  a.setInterval = fn => { const id = { fn }; a.timers.add(id); return id; };
  a.clearInterval = id => a.timers.delete(id);
  if (comSSE) a.EventSource = class {
    constructor(url) { this.url = url; this.listeners = {}; a.fontes.push(this); }
    addEventListener(nome, fn) { this.listeners[nome] = fn; }
    close() { this.fechado = true; }
  };
  a.fechar = iniciar(a, d => a.dados.push(d), e => a.estados.push(e), callback => {
    a.carregamentos++;
    a.retornos.push(callback);
  }, 5000);
  return a;
}

test('http usa SSE e aplica evento dados sem polling conectado', () => {
  const a = ambiente('http:');
  assert.equal(a.fontes[0].url, '/eventos');
  a.fontes[0].onopen();
  a.fontes[0].listeners.dados({ data: '{"origem":"vivo"}' });
  assert.equal(a.dados[0].origem, 'vivo');
  assert.equal(a.estados.at(-1), 'ao vivo');
  assert.equal(a.timers.size, 0);
});
test('file usa fallback mesmo com EventSource disponível', () => {
  const a = ambiente('file:');
  assert.equal(a.fontes.length, 0);
  assert.equal(a.carregamentos, 1);
  assert.equal(a.timers.size, 1);
  assert.equal(a.estados.at(-1), 'atualizando a cada 5 s');
});
test('http sem EventSource usa fallback', () => {
  assert.equal(ambiente('http:', false).timers.size, 1);
});
test('erro ativa um fallback e reconexão o remove', () => {
  const a = ambiente('http:');
  a.fontes[0].onerror();
  a.fontes[0].onerror();
  assert.equal(a.timers.size, 1);
  assert.equal(a.estados.at(-1), 'desconectado');
  a.fontes[0].onopen();
  assert.equal(a.timers.size, 0);
  assert.equal(a.estados.at(-1), 'ao vivo');
  a.fechar();
  assert.equal(a.fontes[0].fechado, true);
});
test('evento inválido não substitui dados', () => {
  const a = ambiente('http:');
  a.fontes[0].listeners.dados({ data: '{' });
  assert.equal(a.dados.length, 0);
});

test('fallback carregado depois do erro atualiza indicador', () => {
  const a = ambiente('http:');
  a.fontes[0].onerror();
  assert.equal(a.estados.at(-1), 'desconectado');
  [...a.timers][0].fn();
  a.retornos.at(-1)();
  assert.equal(a.estados.at(-1), 'atualizando a cada 5 s');
});

test('fallback atrasado não troca o indicador depois da reconexão SSE', () => {
  const a = ambiente('http:');
  a.fontes[0].onerror();
  a.fontes[0].onopen();
  a.retornos.at(-1)();
  assert.equal(a.estados.at(-1), 'ao vivo');
});

test('carregar sinaliza sucesso apenas no onload', () => {
  let script, sucessos = 0;
  const codigo = 'function carregar(' + html.split('function carregar(')[1]
    .split('/* Transporte isolado')[0];
  const carregar = vm.runInNewContext(codigo + '; carregar;', { document: {
    createElement: () => (script = { remove() {} }), head: { appendChild() {} }
  }});
  carregar(() => sucessos++);
  script.onerror();
  assert.equal(sucessos, 0);
  carregar(() => sucessos++);
  script.onload();
  assert.equal(sucessos, 1);
});

test('contexto só põe número finito no style', () => {
  const codigo = 'function medidorDeContexto(' + html.split('function medidorDeContexto(')[1]
    .split('function htmlPr(')[0];
  const medidor = vm.runInNewContext(codigo + '; medidorDeContexto;', { esc: s => String(s) });
  assert.match(medidor('42', 'sem dado'), /width:42%/);
  for (const valor of [null, undefined, NaN, Infinity, '42%;background:url(x)', '<script>']) {
    const resultado = medidor(valor, 'sem dado');
    assert.doesNotMatch(resultado, /style=|<script>|background:/);
  }
});

test('reset textual Claude não vira horário numérico inventado', () => {
  const cotas = fs.readFileSync(path.join(__dirname, '../../publico/cotas.js'), 'utf8');
  const codigo = 'function reabre(' + cotas.split('function reabre(')[1].split('\nfunction ')[0];
  const reabre = vm.runInNewContext(codigo + '; reabre;', { numero: v => typeof v === 'number' && Number.isFinite(v) });
  assert.equal(reabre({ renova_texto: 'Oct 27, 1am', renova_epoch: null }, 1), 'reset Oct 27, 1am');
});

// E28: executa o script local completo num DOM que recusa sinks de marcação.
async function paginaRedeE28(secao) {
  class No {
    constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.value = ''; this.textContent = ''; }
    set innerHTML(_) { throw new Error('sink HTML proibido'); }
    appendChild(n) { this.children.push(...(n.tag === 'fragment' ? n.children : [n])); return n; }
    replaceChildren(...ns) { this.children = []; ns.forEach(n => this.appendChild(n)); }
    addEventListener(e, fn) { this.events[e] = fn; }
  }
  const elementos = {}, timers = {};
  let dados = {gerado_em_epoch: 2000, rede_sessao: secao};
  class Relogio extends Date { static now() { return 2000000; } }
  const contexto = {document: {getElementById: id => elementos[id] ||= new No(id),
    createElement: tag => new No(tag), createDocumentFragment: () => new No('fragment')},
    Date: Relogio, setInterval: (fn, ms) => {timers[ms] = fn;}, setTimeout, clearTimeout,
    fetch: async (url, opts) => {
      assert.equal(url, '/local.json'); assert.equal(opts.cache, 'no-store');
      return {ok: true, json: async () => dados};
    }};
  vm.createContext(contexto);
  const pagina = fs.readFileSync(path.join(__dirname, '../../publico_local/index.html'), 'utf8');
  assert.doesNotMatch(pagina, /innerHTML|outerHTML|insertAdjacentHTML/);
  vm.runInContext(pagina.match(/<script>([\s\S]*?)<\/script>/)[1], contexto);
  await new Promise(resolve => setImmediate(resolve));
  return {elementos, timers, atualizar: async novo => {
    dados = {gerado_em_epoch: 2000, rede_sessao: novo};
    await vm.runInContext('atualizar()', contexto);
  }};
}

const secaoRedeE28 = () => ({estado: 'ativo', gerado_em_epoch: 1969, sessoes_omitidas: 2,
  fluxos_omitidos: 3, pids_omitidos: 4,
  sessoes: [{agente: '<img src=x onerror=boom>', pid_raiz: 99, inicio_epoch: 1900,
    sessao: '<script>boom()</script>', processos: 3, janela: [10,20], total: [100,200], conexoes_omitidas: 5,
    conexoes: [{proto: 'tcp', local_porta: 1234, remoto: '<svg onload=boom>', remoto_porta: 443,
      processo: '<img src=x onerror=boom>', janela: [10,20], total: [100,200]}]}],
  outros: {janela: [1,2], total: [3,4], nao_atribuidos: {total: [3,4]}}});

test('E28: sessão e conexão hostis viram somente texto; detalhe é expandível', async () => {
  const p = await paginaRedeE28(secaoRedeE28());
  const linha = p.elementos['rede-sessoes'].children[0];
  const detalhe = linha.children[0].children[0];
  assert.equal(detalhe.tag, 'details');
  assert.match(detalhe.children[0].textContent, /<img src=x onerror=boom>.*<script>boom/);
  const conexao = detalhe.children[1].children[1].children[0];
  assert.equal(conexao.children[2].textContent, '<svg onload=boom>');
  assert.equal(conexao.children[4].textContent, '<img src=x onerror=boom>');
  const todos = n => [n, ...n.children.flatMap(todos)];
  assert.equal(todos(linha).some(n => ['img','svg','script'].includes(n.tag)), false);
  assert.match(linha.children[2].textContent, /10 B \/ 20 B/);
  assert.match(linha.children[3].textContent, /100 B \/ 200 B/);
  assert.match(p.elementos['situacao-rede-sessao'].textContent, /idade: 31 s.*AVISO.*sessões omitidas: 2/);
  assert.match(detalhe.children[2].textContent, /Conexões omitidas: 5/);
  assert.match(p.elementos['rede-outros'].textContent, /sem vínculo confiável: 3 B \/ 4 B/);
  detalhe.open = true; detalhe.events.toggle();
  const nova = secaoRedeE28(); nova.sessoes[0].janela = [30,40];
  await p.atualizar(nova);
  assert.equal(p.elementos['rede-sessoes'].children[0].children[0].children[0].open, true);
});

test('E28: ausência e erro limpam sessões e instrução usa textContent', async () => {
  const p = await paginaRedeE28(secaoRedeE28());
  await p.atualizar({estado: 'sem captura detalhada', instrucao: '<img onerror=boom> --detalhe'});
  assert.equal(p.elementos['rede-sessoes'].children.length, 0);
  assert.match(p.elementos['situacao-rede-sessao'].textContent, /<img onerror=boom> --detalhe/);
  await p.atualizar({estado: 'erro'});
  assert.match(p.elementos['situacao-rede-sessao'].textContent, /erro/);
  await p.atualizar(null);
  assert.match(p.elementos['situacao-rede-sessao'].textContent, /Sem captura detalhada.*--detalhe/);
});
