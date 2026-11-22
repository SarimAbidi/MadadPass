const operations = document.querySelector('#operations');

function append(parent, tag, text, className = '') {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  parent.append(element);
  return element;
}

function example(schema) {
  return Object.fromEntries(schema.required.map(name => {
    const field = schema.properties[name];
    const types = [field.type].flat();
    const known = { login: 'officer', identity_ref: 'HH-201', name: 'Demo Applicant', applicant: 'Demo Applicant' };
    return [name, field.enum?.[0] ?? known[name] ?? (types.includes('null') ? null : types.includes('boolean') ? true :
      types.includes('integer') ? field.minimum : '')];
  }));
}

function operationRow(path, method, operation) {
  const row = document.createElement('details');
  const summary = append(row, 'summary', '');
  append(summary, 'span', method.toUpperCase(), 'docs-method');
  append(summary, 'span', path);
  const form = append(row, 'form', '', 'stack');
  append(form, 'p', operation.summary);
  const parameters = new Map();
  for (const parameter of operation.parameters || []) {
    const label = append(form, 'label', parameter.name);
    const input = append(label, 'input', '');
    input.required = true;
    parameters.set(parameter.name, input);
  }
  const schema = operation.requestBody?.content['application/json'].schema;
  let body;
  if (schema) {
    body = append(append(form, 'label', 'Request JSON'), 'textarea', JSON.stringify(example(schema), null, 2));
    body.rows = 6;
    const shape = append(form, 'details', '');
    append(shape, 'summary', 'Request schema');
    append(shape, 'pre', JSON.stringify(schema, null, 2));
  }
  const button = append(form, 'button', 'Send local request', 'button secondary');
  const result = append(form, 'pre', '');
  result.setAttribute('aria-live', 'polite');
  result.hidden = true;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    result.hidden = false;
    result.textContent = 'Sending…';
    button.disabled = true;
    try {
      let url = path;
      for (const [key, input] of parameters) url = url.replace('{' + key + '}', encodeURIComponent(input.value));
      const headers = {};
      const token = document.querySelector('#bearer').value.trim();
      const mockKey = document.querySelector('#key').value.trim();
      if (token && operation.security?.some(item => 'BearerAuth' in item)) headers.Authorization = `Bearer ${token}`;
      if (mockKey && operation.security?.some(item => 'MockApiKey' in item)) headers['X-API-Key'] = mockKey;
      if (body) headers['Content-Type'] = 'application/json';
      const response = await fetch(url, { method: method.toUpperCase(), headers, body: body ? JSON.stringify(JSON.parse(body.value)) : undefined });
      const text = response.headers.get('content-type')?.includes('application/json') ? JSON.stringify(await response.json(), null, 2) : await response.text();
      result.textContent = `${response.status} ${response.statusText}\n${text}`;
    } catch (error) { result.textContent = error.message; }
    finally { button.disabled = false; }
  });
  return row;
}

try {
  const response = await fetch('/openapi.json');
  if (!response.ok) throw new Error('Unable to load the API schema.');
  const spec = await response.json();
  operations.replaceChildren();
  append(operations, 'h2', 'Endpoints');
  for (const [path, methods] of Object.entries(spec.paths)) {
    for (const [method, operation] of Object.entries(methods)) operations.append(operationRow(path, method, operation));
  }
} catch (error) { operations.textContent = error.message; }
