'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../../publico/index.html'), 'utf8');
const corpo = 'function iniciarTransporte(' + html.split('function iniciarTransporte(')[1]
  .split('/* ---------- botão de refresh completo ---------- */')[0];
const iniciar = vm.runInNewContext(corpo + '; iniciarTransporte;');

function ambiente(protocol, comSSE = true) {
  const a = { location: { protocol }, fontes: [], timers: new Set(), carregamentos: 0, estados: [], dados: [] };
  a.setInterval = () => { const id = {}; a.timers.add(id); return id; };
  a.clearInterval = id => a.timers.delete(id);
  if (comSSE) a.EventSource = class {
    constructor(url) { this.url = url; this.listeners = {}; a.fontes.push(this); }
    addEventListener(nome, fn) { this.listeners[nome] = fn; }
    close() { this.fechado = true; }
  };
  a.fechar = iniciar(a, d => a.dados.push(d), e => a.estados.push(e), () => a.carregamentos++, 5000);
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
