/* global React, ReactDOM, arco */
// 构建：skills/tests/node_modules/.bin/esbuild skills/specs/skills/byted-sms-sender/assets/traffic_wizard.jsx --minify --target=es2018 --outfile=skills/specs/skills/byted-sms-sender/assets/traffic_wizard.js
const { Alert, Button, Input, Select, Spin } = arco;
const { useEffect, useRef, useState } = React;
const token = window.location.hash.slice(1);
if (token) {
  try { window.history.replaceState(null, '', window.location.pathname + window.location.search); } catch (_) { /* Host controls navigation. */ }
}

const labels = {
  useVolcLink: '使用火山短链', protocol: '文案链接协议', link: '文案链接',
  linkDomainIcp: '文案域名备案号', linkDomainIcpBody: '文案域名备案主体',
  linkDomain: '文案链接二级域名', linkPath: '文案链接 Path',
  jumpLink: '跳转链接', jumpLinkDomainIcp: '跳转域名备案号', jumpLinkDomainIcpBody: '跳转域名备案主体',
  jumpLinkDomain: '跳转链接二级域名', jumpLinkPath: '跳转链接 Path',
  numberType: '号码类型', number: '联系号码', company: '号码所属企业',
  numberPerson: '号码所属人', personType: '证件类型', personId: '证件号码',
  numberProvince: '号码归属省份', numberCity: '号码归属城市',
};
const numberTypes = [[1, '手机号码'], [2, '400 号码'], [3, '700 号码'], [4, '800 号码'], [5, '95 / 96 号码'], [6, '12 号码'], [7, '固定电话']];
const numberPatterns = {
  1: '^1[3456789]\\d{9}$', 2: '^400\\d{7}$', 3: '^700\\d+$',
  4: '^800\\d{7}$', 5: '^9[56]\\d{3}$', 6: '^12\\d{3}$', 7: '^\\d{10,12}$',
};
const numberErrors = {
  1: '请输入正确的手机号', 2: '请输入正确的 400 号码', 3: '请输入正确的 700 号码',
  4: '请输入正确的 800 号码', 5: '请输入正确的 95 或 96 号码',
  6: '请输入正确的 12 号码', 7: '请输入正确的固定电话',
};

function inferNumberType(number) {
  if (!number) return undefined;
  if (/^1[3456789]/.test(number)) return 1;
  if (number.startsWith('400')) return 2;
  if (number.startsWith('700')) return 3;
  if (number.startsWith('800')) return 4;
  if (/^9[56]/.test(number)) return 5;
  if (number.startsWith('12')) return 6;
  return 7;
}

function inferProtocol(link) {
  if (link?.startsWith('https://')) return 'https';
  if (link?.startsWith('http://')) return 'http';
  return '缺省';
}

function parseLink(link) {
  if (!link) return { domain: '', path: '' };
  try {
    const parsed = new URL(link.includes('://') ? link : `https://${link}`);
    return { domain: parsed.hostname, path: parsed.pathname + parsed.search };
  } catch (_) {
    return { domain: '', path: '' };
  }
}

function initialRow(kind, row = {}) {
  if (kind === 'phone') {
    return { ...row, numberType: row.numberType || inferNumberType(row.number) };
  }
  const useVolcLink = row.useVolcLink === true;
  return {
    useVolcLink,
    ...row,
    ...(!useVolcLink && row.link && !row.protocol ? { protocol: inferProtocol(row.link) } : {}),
  };
}

async function api(path, body) {
  if (!token) throw new Error('请从对话中的引流信息入口打开本机表单。');
  const options = { headers: { 'X-Traffic-Context': token } };
  if (body !== undefined) Object.assign(options, { method: 'POST', body: JSON.stringify(body), headers: { ...options.headers, 'Content-Type': 'application/json' } });
  let response;
  try { response = await fetch(path, options); } catch (_) { throw new Error('本机表单暂时无法连接。'); }
  const payload = await response.json();
  if (!response.ok || payload.success === false) {
    const error = new Error(payload.message || '当前操作未完成。');
    error.status = response.status;
    error.result = payload.result;
    throw error;
  }
  return payload;
}

function notifyHost(type) { window.parent.postMessage({ type }, '*'); }

function toPayload(kind, row) {
  if (kind === 'phone') {
    const fields = ['numberType', 'number', 'numberProvince', 'numberCity', ...(row.numberType === 1 ? ['numberPerson', 'personType', 'personId'] : ['company'])];
    return Object.fromEntries(fields.filter(key => row[key] !== undefined).map(key => [key, row[key]]));
  }
  const linkParts = parseLink(row.link);
  const jumpLinkParts = parseLink(row.jumpLink);
  const complete = {
    ...row,
    ...(row.link ? { linkDomain: linkParts.domain, linkPath: linkParts.path } : {}),
    ...(row.jumpLink ? { jumpLinkDomain: jumpLinkParts.domain, jumpLinkPath: jumpLinkParts.path } : {}),
  };
  const fields = row.useVolcLink ? [
    'useVolcLink', 'jumpLink', 'jumpLinkDomainIcp', 'jumpLinkDomainIcpBody',
    'jumpLinkDomain', 'jumpLinkPath',
  ] : [
    'useVolcLink', 'protocol', 'link', 'linkDomainIcp', 'linkDomainIcpBody',
    'linkDomain', 'linkPath', 'jumpLink', 'jumpLinkDomainIcp', 'jumpLinkDomainIcpBody',
    'jumpLinkDomain', 'jumpLinkPath',
  ];
  return Object.fromEntries(fields.filter(key => complete[key] !== undefined).map(key => [key, complete[key]]));
}

function displayValue(key, value) {
  if (key === 'useVolcLink') return value ? '是' : '否';
  if (key === 'numberType') return (numberTypes.find(item => item[0] === value) || [value, value])[1];
  if (key === 'personType') return value === 1 ? '居民身份证' : '未填写';
  return String(value);
}

function validateRows(kind, rows) {
  const errors = {};
  const requireField = (index, row, key) => {
    if (row[key] === undefined || row[key] === '') errors[`${index}.${key}`] = '此项必填';
  };
  rows.forEach((row, index) => {
    if (kind === 'link') {
      requireField(index, row, 'useVolcLink');
      const required = row.useVolcLink
        ? ['jumpLink', 'jumpLinkDomainIcp', 'jumpLinkDomainIcpBody']
        : ['protocol', 'link', 'linkDomainIcp', 'linkDomainIcpBody'];
      required.forEach(key => requireField(index, row, key));
      return;
    }
    requireField(index, row, 'numberType');
    requireField(index, row, 'number');
    if (row.numberType && row.number && !new RegExp(numberPatterns[row.numberType]).test(row.number)) {
      errors[`${index}.number`] = numberErrors[row.numberType];
    }
  });
  return errors;
}

function App() {
  const [metadata, setMetadata] = useState(null);
  const [rows, setRows] = useState([]);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState({});
  const [result, setResult] = useState(null);
  const working = useRef(false);
  const ended = useRef(false);
  const form = useRef(null);

  useEffect(() => {
    let disposed = false;
    api('/api/state').then(({ state }) => {
      if (disposed) return;
      setMetadata(state);
      setRows(state.initialData.map(row => initialRow(state.kind, row)));
      return api('/api/heartbeat', { active: true });
    }).then(() => { if (!disposed) notifyHost('traffic:wizard-ready'); }).catch(e => { if (!disposed) setError(e.message); });
    const timer = setInterval(() => {
      if (!ended.current) api('/api/heartbeat', { active: document.visibilityState === 'visible' }).catch(() => {});
    }, 15000);
    const detach = () => {
      if (ended.current || !token) return;
      const headers = new Headers({ 'Content-Type': 'application/json' });
      headers.set('X-Traffic-Context', token);
      fetch('/api/detach', {
        method: 'POST', keepalive: true, headers, body: '{}',
      }).catch(() => {});
    };
    window.addEventListener('pagehide', detach);
    return () => { disposed = true; clearInterval(timer); window.removeEventListener('pagehide', detach); };
  }, []);

  function finish(value) {
    ended.current = true;
    setRows([]); setPreview(null); setResult(value);
    notifyHost('traffic:wizard-finished');
  }
  function update(index, key, value) {
    setRows(old => old.map((row, position) => {
      if (position !== index) return row;
      if (key === 'link') return { ...row, link: value, protocol: inferProtocol(value) };
      return { ...row, [key]: value };
    }));
    setPreview(null); setError(''); setFieldErrors({});
  }
  async function act(operation) {
    if (working.current || ended.current) return;
    working.current = true; setBusy(true); setError('');
    try { await operation(); } catch (e) {
      if (e.result) finish(e.result);
      setError(e.message);
    } finally { working.current = false; setBusy(false); }
  }
  async function prepare(event) {
    event.preventDefault();
    const validation = validateRows(metadata.kind, rows);
    setFieldErrors(validation);
    if (Object.keys(validation).length > 0) return;
    await act(async () => {
      const response = await api('/api/preview', { data: rows.map(row => toPayload(metadata.kind, row)) });
      setPreview(response.preview);
    });
  }
  async function submit() {
    if (!preview) return;
    await act(async () => {
      try {
        const response = await api('/api/create', { revision: preview.revision, confirmed: true });
        finish(response.result);
      } catch (e) {
        setPreview(null);
        if (!e.status) finish({ status: 'traffic_creation_outcome_unknown', outcomeUnknown: true });
        throw e;
      }
    });
  }
  function field(index, key, { required = false, wide = false, options = null, numeric = false, boolean = false, placeholder = '' } = {}) {
    const id = `traffic-${index}-${key}`;
    const value = rows[index][key] === undefined ? '' : rows[index][key];
    const fieldError = fieldErrors[`${index}.${key}`];
    return <div
      className={`traffic-field${wide ? ' wide' : ''}`}
      key={key}
      data-field={key}
      data-value={String(value)}
    >
      <label htmlFor={id}>{required && <span className="traffic-required" aria-hidden="true">*</span>}{labels[key]}</label>
      {options ? <Select
        id={id}
        value={value === '' ? undefined : value}
        options={options.map(([optionValue, title]) => ({ value: optionValue, label: title }))}
        placeholder="请选择"
        disabled={busy}
        error={Boolean(fieldError)}
        aria-invalid={Boolean(fieldError)}
        onChange={next => update(index, key, numeric ? Number(next) : boolean ? next === true : next)}
      /> : <Input
        id={id}
        name={key}
        autoComplete="off"
        value={value}
        placeholder={placeholder}
        disabled={busy}
        error={Boolean(fieldError)}
        aria-required={required}
        aria-invalid={Boolean(fieldError)}
        onChange={next => update(index, key, next)}
      />}
      {fieldError && <div className="traffic-field-error" role="alert">{fieldError}</div>}
    </div>;
  }
  function derivedField(label, value) {
    return <div className="traffic-field"><label>{label}</label><Input value={value} readOnly /></div>;
  }
  const title = metadata?.kind === 'phone' ? '录入引流号码' : '录入引流链接';

  return <main className="traffic-page">
    <div className="traffic-eyebrow">火山引擎短信</div><h1>{title}</h1>
    {metadata?.businessName && <p>当前账号主体：{metadata.businessName}</p>}
    <p>{metadata?.kind === 'phone' ? '在本机填写号码及归属信息，确认后提交短信服务。个人材料不会进入对话。' : '核对链接及域名备案信息，确认后提交短信服务。已有查询结果可预填，缺失项在这里补充。'}</p>
    {metadata?.kind === 'link' && <Alert type="info" content="文案链接是短信正文中的链接；使用火山短链时，跳转链接是客户点击后打开的落地页。" />}
    {error && <div className="traffic-status"><Alert type="error" content={error} /></div>}
    {result ? <section className="traffic-card traffic-result" aria-live="polite">
      <h2>{result.created ? '引流信息已提交' : result.outcomeUnknown ? '录入结果待确认' : '本次录入已结束'}</h2>
      <p>{result.status === 'traffic_identity_changed' ? '登录身份已变化，本次录入没有发送。请确认所需账号后重新打开表单。' : result.status === 'traffic_created' ? '已查询到录入的资源，可回到对话继续申请模板。录入成功不代表运营商报备已生效。' : result.created ? '提交已成功，暂未完整确认查询结果。请回到对话查询，避免重复录入。' : result.outcomeUnknown ? '请回到对话核对已有资源和请求编号，不要重复提交。' : '尚未提交的材料已清除。'}</p>
      {result.resourceIds?.length > 0 && <p>资源 ID：{result.resourceIds.join('、')}</p>}
      {result.requestId && <p>请求编号：{result.requestId}</p>}
    </section> : metadata ? <>
      <form ref={form} onSubmit={prepare}>
        {rows.map((row, index) => <section className="traffic-card" key={index}>
          <div className="traffic-row-title"><h2>{metadata.kind === 'link' ? '链接' : '号码'} {index + 1}</h2>
            {rows.length > 1 && <Button type="text" disabled={busy} onClick={() => { setRows(old => old.filter((_, i) => i !== index)); setPreview(null); }}>移除</Button>}
          </div>
          <div className="traffic-fields">
            {metadata.kind === 'link' ? <>
              {field(index, 'useVolcLink', { required: true, options: [[false, '否'], [true, '是']], boolean: true })}
              {!row.useVolcLink && <>
                {field(index, 'protocol', { required: true, options: [['https', 'https'], ['http', 'http'], ['缺省', '不带协议']] })}
                {field(index, 'link', { required: true, placeholder: '请输入短信正文中的完整链接' })}
                {field(index, 'linkDomainIcp', { required: true })}
                {field(index, 'linkDomainIcpBody', { required: true })}
                {derivedField('文案链接二级域名', parseLink(row.link).domain)}
                {derivedField('文案链接 Path', parseLink(row.link).path)}
              </>}
              {field(index, 'jumpLink', { required: row.useVolcLink === true, wide: true, placeholder: '请输入点击后打开的完整落地页链接' })}
              {field(index, 'jumpLinkDomainIcp', { required: row.useVolcLink === true })}
              {field(index, 'jumpLinkDomainIcpBody', { required: row.useVolcLink === true })}
              {row.jumpLink && <>
                {derivedField('跳转链接二级域名', parseLink(row.jumpLink).domain)}
                {derivedField('跳转链接 Path', parseLink(row.jumpLink).path)}
              </>}
            </> : <>
              {field(index, 'numberType', { required: true, options: numberTypes, numeric: true })}
              {field(index, 'number', { required: true, placeholder: '不带 86、空格或分隔符' })}
              {row.numberType === 1 ? <>
                {field(index, 'numberPerson')}
                {field(index, 'personType', { options: [[1, '居民身份证']], numeric: true })}
                {field(index, 'personId', { wide: true })}
              </> : field(index, 'company', { wide: true })}
              {field(index, 'numberProvince')}{field(index, 'numberCity')}
            </>}
          </div>
        </section>)}
        <div className="traffic-actions">
          <Button disabled={busy || rows.length >= metadata.maxRecords} onClick={() => { setRows(old => [...old, initialRow(metadata.kind)]); setPreview(null); }}>添加一条</Button>
          <Button type="primary" htmlType="submit" loading={busy}>预览录入内容</Button>
        </div>
      </form>
      {preview && <section className="traffic-card" aria-live="polite">
        <h2>核对本次录入</h2>
        {preview.data.map((row, index) => <dl className="traffic-preview" key={index}>{Object.entries(row).filter(([, value]) => value !== '').map(([key, value]) => <React.Fragment key={key}><dt>{labels[key]}</dt><dd>{displayValue(key, value)}</dd></React.Fragment>)}</dl>)}
        <div className="traffic-actions"><Button type="primary" disabled={busy} loading={busy} onClick={submit}>确认录入</Button></div>
      </section>}
      <div className="traffic-actions"><Button disabled={busy} onClick={() => act(async () => finish((await api('/api/abandon', {})).result))}>结束本次录入</Button></div>
    </> : !error && <Spin />}
  </main>;
}

ReactDOM.createRoot(document.getElementById('traffic-root')).render(<App />);
