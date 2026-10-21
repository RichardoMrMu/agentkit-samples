/* global React, ReactDOM, arco, arcoicon */
// Build with esbuild: --loader:.jsx=jsx --target=es2019 --format=iife --minify.

const { Alert, Button, Checkbox, Result, Spin } = arco;
const { IconMessage } = arcoicon;
const { useEffect, useRef, useState } = React;

const token = window.location.hash.slice(1);
if (token) {
  try {
    window.history.replaceState(
      null,
      '',
      window.location.pathname + window.location.search,
    );
  } catch (_) {
    // Embedded WebViews may keep the local fragment in their private history.
  }
}

async function api(path, body) {
  if (!token) {
    throw new Error('请从对话中的服务开通入口重新打开页面。');
  }
  const headers = { 'X-Service-Onboarding-Context': token };
  const options = { headers };
  if (body !== undefined) {
    Object.assign(options, {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { ...headers, 'Content-Type': 'application/json' },
    });
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (_) {
    throw new Error('本机开通页面暂时无法连接。');
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (_) {
    payload = {};
  }
  if (!response.ok || payload.success === false) {
    const error = new Error(payload.message || '当前操作未完成。');
    error.result = payload.result;
    throw error;
  }
  return payload;
}

function notifyHost(type) {
  if (window.parent !== window) {
    window.parent.postMessage({ type }, '*');
  }
}

function App() {
  const [state, setState] = useState(null);
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const ended = useRef(false);
  const working = useRef(false);

  useEffect(() => {
    let disposed = false;
    api('/api/state')
      .then(payload => {
        if (disposed) return null;
        window.document.title = payload.state.presentation.title;
        setState(payload.state);
        return api('/api/heartbeat', { active: true });
      })
      .then(payload => {
        if (!disposed && payload) notifyHost('service:wizard-ready');
      })
      .catch(cause => {
        if (!disposed) setError(cause.message);
      });
    const timer = window.setInterval(() => {
      if (!ended.current) {
        api('/api/heartbeat', {
          active: document.visibilityState === 'visible',
        }).catch(() => {});
      }
    }, 15000);
    const detach = () => {
      if (ended.current || !token) return;
      fetch('/api/detach', {
        method: 'POST',
        keepalive: true,
        headers: {
          'X-Service-Onboarding-Context': token,
          'Content-Type': 'application/json',
        },
        body: '{}',
      }).catch(() => {});
    };
    window.addEventListener('pagehide', detach);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener('pagehide', detach);
    };
  }, []);

  function finish(value) {
    ended.current = true;
    setResult(value);
    notifyHost('service:wizard-finished');
  }

  async function submit() {
    if (!agreed || working.current || ended.current) return;
    working.current = true;
    setBusy(true);
    setError('');
    try {
      const response = await api('/api/submit', { confirmed: true });
      finish(response.result);
    } catch (cause) {
      if (cause.result) {
        finish(cause.result);
      } else {
        setError(cause.message);
      }
    } finally {
      working.current = false;
      setBusy(false);
    }
  }

  async function abandon() {
    if (working.current || ended.current) return;
    working.current = true;
    setBusy(true);
    setError('');
    try {
      const response = await api('/api/abandon', {});
      finish(response.result);
    } catch (cause) {
      setError(cause.message);
      working.current = false;
      setBusy(false);
    }
  }

  if (result) {
    const display = result.display || {};
    return <main className="service-page">
      <section className="service-result">
        <Result
          status={display.status || 'info'}
          title={display.title || '本次操作已结束'}
          subTitle={display.description || ''}
        />
      </section>
    </main>;
  }

  if (!state) {
    return <main className="service-page">
      <div className="service-loading">
        {error ? <Alert type="error" content={error} /> : <Spin dot />}
      </div>
    </main>;
  }

  return <main className="service-page">
    <header className="service-header">
      <div className="service-brand">火山引擎短信</div>
      <h1>{state.presentation.title}</h1>
      <p>{state.presentation.subtitle}</p>
    </header>

    <section className="service-panel">
      <div className="service-product">
        <div className="service-product-icon" aria-hidden="true">
          <IconMessage />
        </div>
        <div>
          <h2>{state.product.name}</h2>
          <p>{state.product.description}</p>
        </div>
      </div>

      <div className="service-content">
        <h2>{state.presentation.sectionTitle}</h2>
        <p className="service-hint">{state.presentation.hint}</p>
        <ol className="service-protocols">
          {state.protocols.map((protocol, index) => <li key={protocol.url}>
            <span className="service-protocols-index">{index + 1}</span>
            <a href={protocol.url} target="_blank" rel="noreferrer">
              {protocol.title}
            </a>
          </li>)}
        </ol>

        <label className="service-consent">
          <Checkbox
            checked={agreed}
            disabled={busy}
            onChange={setAgreed}
          />
          <span>{state.presentation.consentText}</span>
        </label>

        {error && <div className="service-error" role="alert">{error}</div>}
        <div className="service-actions">
          <Button disabled={busy} onClick={abandon}>取消</Button>
          <Button
            type="primary"
            loading={busy}
            disabled={!agreed}
            onClick={submit}
          >
            {state.presentation.submitLabel}
          </Button>
        </div>
      </div>
    </section>
  </main>;
}

ReactDOM.render(<App />, document.getElementById('service-root'));
