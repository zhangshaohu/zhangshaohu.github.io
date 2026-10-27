/* Browser-independent checks for Studio's startup and connection recovery. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../tools/editor.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

function fixture() {
  return {
    site: {
      profile: {name: 'Original name', role: 'Professor', email: 'private@example.org', secondary_email: '', bio: ['A biography paragraph.']},
      lab: {tagline: 'Lab introduction', description: ['Lab description.']},
      research: [{title: 'Research', description: 'Research description', tag: 'AI'}],
      news: [{date: '2026-10-26', text: 'An update'}],
      teaching: [{code: 'CS100', title: 'A course', terms: ['Fall 2026']}],
      team: [{name: 'A Student', role: 'Ph.D. student', focus: 'AI', since: '2026', photo: ''}],
      awards: ['An award'], service: [{title: 'Service', items: ['A contribution']}],
      collaborators: [{name: 'A Collaborator', url: 'https://example.org', affiliation: 'University'}],
      resources: [{title: 'Resource', url: 'https://example.org', description: 'Reading'}],
    },
    publications: [{id: 'paper', title: 'A paper', author: 'An Author', year: '2026'}],
    photos: [], headshots: [], warnings: [], siteText: '{}',
  };
}

function harness({online = true, protocol = 'http:'} = {}) {
  const elements = [], ids = new Map(), calls = [];
  const server = {online, state: fixture(), token: 'initial_session_token_1234567890'};

  class Element {
    constructor(tag = 'div') {
      this.tag = tag; this.children = []; this.dataset = {}; this.style = {};
      this.attributes = {}; this.hidden = false; this.disabled = false;
      this.value = ''; this.listeners = {}; this.classes = new Set();
      this.classList = {
        add: name => this.classes.add(name), remove: name => this.classes.delete(name),
        contains: name => this.classes.has(name),
        toggle: (name, enabled) => {
          const show = enabled === undefined ? !this.classes.has(name) : enabled;
          if (show) this.classes.add(name); else this.classes.delete(name);
        },
      };
      elements.push(this);
    }
    set className(value) {this._className = value; this.classes = new Set(value.split(/\s+/));}
    get className() {return this._className || '';}
    set innerHTML(value) {this._html = value; this.queries = {};}
    get innerHTML() {return this._html || '';}
    append(...items) {this.children.push(...items);}
    replaceChildren(...items) {this.children = [...items]; this._html = '';}
    setAttribute(key, value) {this.attributes[key] = value;}
    removeAttribute(key) {delete this.attributes[key];}
    addEventListener(type, handler) {this.listeners[type] = handler;}
    querySelector(selector) {
      this.queries ||= {};
      return this.queries[selector] ||= new Element(selector);
    }
    scrollIntoView() {}
    click() {return (this.onclick || this.listeners.click)?.();}
  }

  for (const match of html.split('<script>')[0].matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]);
    element.hidden = /\bhidden\b/.test(match[2]);
    ids.set(match[3], element);
  }
  const tabs = ['publications', 'photos', 'content'].map((section, index) => {
    const element = new Element('button');
    element.dataset.tab = section;
    element.className = 'tab' + (index === 0 ? ' active' : '');
    return element;
  });
  const advanced = new Element('details');
  const document = {
    getElementById: id => ids.get(id), createElement: tag => new Element(tag),
    createTextNode: value => ({textContent: value}),
    querySelectorAll: selector => selector === '.tab' ? tabs : elements.filter(element => element.tag === 'button'),
    querySelector: selector => selector === '.tab.active' ? tabs.find(tab => tab.classes.has('active')) : selector === '.advanced' ? advanced : new Element(),
  };
  const context = vm.createContext({
    document, structuredClone, console,
    window: {location: {protocol}, addEventListener() {}, confirm: () => true},
    URL: {createObjectURL: () => 'blob:fixture', revokeObjectURL() {}}, FileReader: class {},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET', body: options.body});
      if (!server.online) throw new TypeError('Network unavailable');
      if (url === '/_editor') return {ok: true, status: 200, text: async () => `let TOKEN = '${server.token}';`};
      if (options.headers?.['X-Editor-Token'] !== server.token) return {ok: false, status: 403, json: async () => ({error: 'This editor session has changed. Refresh the editor and try again.'})};
      if (url === '/_api/state') return {ok: true, status: 200, json: async () => structuredClone(server.state)};
      return {ok: true, status: 200, json: async () => ({message: 'Saved locally.'})};
    },
  });
  const started = vm.runInContext(script.replace('__EDITOR_TOKEN__', server.token), context);
  return {server, ids, calls, started, run: code => vm.runInContext(code, context)};
}

test('Studio boots and renders every current content section', async () => {
  const studio = harness();
  await studio.started;
  assert.equal(studio.ids.get('loading').hidden, true);
  assert.equal(studio.ids.get('connection-help').hidden, true);
  assert.equal(studio.run('current.publications.length'), 1);
  for (const section of Object.keys(studio.server.state.site)) studio.run(`selectedSection=${JSON.stringify(section)};renderContent()`);
  assert.equal(studio.ids.get('raw-json').value.includes('private@example.org'), false);
});

test('Rebuild refreshes clean forms from externally edited content', async () => {
  const studio = harness(); await studio.started;
  studio.server.state.site.profile.name = 'Updated on disk';
  await studio.ids.get('rebuild').click();
  assert.equal(studio.run('siteDraft.profile.name'), 'Updated on disk');
  assert.equal(studio.run('dirty'), false);
  assert.match(studio.ids.get('draft-state').textContent, /Saved locally/);
  assert.equal(studio.calls.some(call => call.url === '/_api/site'), false);
});

test('Rebuild preserves dirty forms and identifies their unsaved status', async () => {
  const studio = harness(); await studio.started;
  studio.run('siteDraft.profile.name="Unsaved edit";markDirty()');
  studio.server.state.site.profile.name = 'Changed on disk';
  await studio.ids.get('rebuild').click();
  assert.equal(studio.run('siteDraft.profile.name'), 'Unsaved edit');
  assert.equal(studio.run('dirty'), true);
  assert.match(studio.ids.get('draft-state').textContent, /Unsaved changes kept/);
});

test('Failed startup hides Loading and Retry connects to the saved content', async () => {
  const studio = harness({online: false}); await studio.started;
  assert.equal(studio.ids.get('loading').hidden, true);
  assert.equal(studio.ids.get('connection-help').hidden, false);
  assert.match(studio.ids.get('connection-message').textContent, /Update Website\.command/);
  assert.equal(studio.ids.get('retry-connection').disabled, false);
  studio.server.online = true;
  studio.server.state.site.profile.name = 'Ready after restart';
  await studio.ids.get('retry-connection').click();
  assert.equal(studio.ids.get('connection-help').hidden, true);
  assert.equal(studio.run('siteDraft.profile.name'), 'Ready after restart');
  assert.equal(studio.calls.filter(call => call.method === 'POST').length, 0);
});

test('Retry renews a restarted session and preserves unsaved edits without saving', async () => {
  const studio = harness(); await studio.started;
  studio.run('siteDraft.profile.name="Keep this edit";markDirty()');
  studio.server.online = false;
  await studio.ids.get('rebuild').click();
  assert.equal(studio.ids.get('connection-help').hidden, false);
  studio.server.online = true;
  studio.server.token = 'restarted_session_token_1234567890';
  await studio.ids.get('retry-connection').click();
  assert.equal(studio.ids.get('connection-help').hidden, true);
  assert.equal(studio.run('siteDraft.profile.name'), 'Keep this edit');
  assert.equal(studio.run('dirty'), true);
  assert.equal(studio.calls.some(call => call.url === '/_api/site'), false);
});

test('Opening editor.html as a file gives the launcher instruction', async () => {
  const studio = harness({protocol: 'file:'}); await studio.started;
  assert.equal(studio.ids.get('loading').hidden, true);
  assert.equal(studio.ids.get('connection-help').hidden, false);
  assert.equal(studio.ids.get('retry-connection').hidden, true);
  assert.match(studio.ids.get('connection-message').textContent, /double-clicking Update Website\.command/);
  assert.equal(studio.calls.length, 0);
});
