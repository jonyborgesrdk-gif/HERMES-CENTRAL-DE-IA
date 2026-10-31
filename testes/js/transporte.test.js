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
